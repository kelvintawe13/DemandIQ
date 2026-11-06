import {
  defineRailway,
  github,
  postgres,
  preserve,
  project,
  redis,
  service,
  volume,
} from "railway/iac";

// Railway Infrastructure as Code: one file for the whole DemandIQ project.
// The Railway CLI reads it for `railway config plan` and `railway config apply`.
// It is never read during a deploy, so committing it changes nothing until applied.
//
// `railway.toml` / `railway.json` are the retired per-service format: Railway stops
// reading them on 2026-12-01 and new services cannot opt into them. Do not add one.
const REPO = "kelvintawe13/DemandIQ";
const BRANCH = "main";

export default defineRailway(() => {
  const db = postgres("postgres");
  const cache = redis("redis");
  // Bundles live on a volume because a deploy replaces the container filesystem.
  // A volume belongs to one service, so the service that owns it is the one the
  // API fetches from.
  const bundles = volume("bundles");

  // API. Root directory `backend` gives the build context backend/Dockerfile
  // expects. The start command replaces the image CMD because Railway terminates
  // TLS at its edge, so the forwarded-IP headers are trustworthy here and the
  // proxy flags are safe.
  const api = service("api", {
    source: github(REPO, { branch: BRANCH, rootDirectory: "backend" }),
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    start: `sh -c 'exec uvicorn app.main:app --host 0.0.0.0 --port "\${PORT:-8000}" --proxy-headers --forwarded-allow-ips="*"'`,
    preDeploy: "alembic upgrade head",
    healthcheck: "/api/v1/health",
    healthcheckTimeout: 300,
    env: {
      APP_ENV: "production",
      DATABASE_URL: db.env.DATABASE_URL,
      REDIS_URL: cache.env.REDIS_URL,
      // Set before the first apply: with APP_ENV=production a weak or missing
      // secret fails the deploy instead of serving with a development one.
      // `preserve()` keeps whatever value is already on the service.
      JWT_SECRET: preserve(),
      PORT: "8000",
      INSTALL_EXTRAS: "model ocr",
      MODEL_BUNDLE_PATH: "/app/artifacts/serving_bundle.joblib",
      // The model service's volume is preferred when it is reachable; the export
      // at MODEL_BUNDLE_PATH above stays the fallback, so a restart of either
      // service cannot leave the API on stub forecasts. Model inference itself
      // stays in this process — one bundle in, no extra hop per forecast.
      MODEL_BUNDLE_URL: "http://model.railway.internal:9000/latest/serving_bundle.joblib",
      TRUSTED_PROXY_HEADERS: "true",
      LOG_LEVEL: "INFO",
      JOBS_ENABLED: "false",
    },
  });

  // Web. The build context is the repository root, not frontend/: dependencies are
  // one npm workspace hoisted to the root package-lock.json, and the root
  // .dockerignore is what keeps that context small. The Dockerfile itself lives in
  // frontend/. VITE_API_URL is deliberately unset, so the bundle calls relative
  // /api/ paths and the container's nginx proxy forwards them to the API.
  const web = service("web", {
    source: github(REPO, { branch: BRANCH }),
    build: {
      builder: "DOCKERFILE",
      dockerfilePath: "frontend/Dockerfile.railway",
    },
    healthcheck: "/healthz",
    env: {
      API_UPSTREAM: "api.railway.internal:8000",
      PORT: "8080",
    },
  });

  // Model service. Serves the published export to the API over the private
  // network and accepts new exports, so a retrain can be published without
  // rebuilding the API image. INSTALL_TRAINING=1 switches its image from
  // "serve bundles" to "serve bundles and run the pipeline here" (~1 GB more).
  const model = service("model", {
    source: github(REPO, { branch: BRANCH, rootDirectory: "model" }),
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile.railway" },
    healthcheck: "/healthz",
    env: {
      PORT: "9000",
      BUNDLES_DIR: "/data/bundles",
      INSTALL_TRAINING: "0",
      // Needed to publish a bundle; until it is set, uploads are refused. The
      // token itself is set in the dashboard, never committed.
      BUNDLE_ADMIN_TOKEN: preserve(),
      LOG_LEVEL: "INFO",
    },
    // Key is the mount path; the value attaches the volume declared above.
    volumeMounts: { "/data": bundles },
  });

  return project("demandiq", {
    resources: [api, web, model, db, cache],
  });
});
