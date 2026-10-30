# Third-party notices

DemandIQ is proprietary (see [LICENSE](LICENSE)). It is built on the open-source
components below. Each remains under its own licence; this file records the
attribution those licences require and is not a substitute for the licence text
that ships with each package.

Versions are whatever the resolver installed from the manifests in
`backend/pyproject.toml`, `model/pyproject.toml` and `package-lock.json`. The
notices below cover direct dependencies; transitive dependencies keep their own
notices in the installed tree.

## Backend — Python

| Component | Licence | What the licence asks of DemandIQ |
| --- | --- | --- |
| FastAPI | MIT | Keep the copyright and permission notice. |
| Uvicorn | BSD-3-Clause | Keep the notice; do not use the authors' names to endorse DemandIQ. |
| Pydantic / pydantic-settings | MIT | Keep the copyright and permission notice. |
| SQLAlchemy | MIT | Keep the copyright and permission notice. |
| Alembic | MIT | Keep the copyright and permission notice. |
| psycopg2-binary | LGPL-3.0-or-later (with linking exception) | Keep the notice; relink rather than fork if the library is modified. DemandIQ imports it unmodified. |
| redis-py | MIT | Keep the copyright and permission notice. |
| python-jose | MIT | Keep the copyright and permission notice. |
| passlib | BSD-3-Clause | Keep the notice; do not use the authors' names to endorse DemandIQ. |
| bcrypt | Apache-2.0 | Keep the licence and notices; state any changes. DemandIQ does not modify it. |
| python-multipart | Apache-2.0 | Keep the licence and notices; state any changes. DemandIQ does not modify it. |
| httpx | BSD-3-Clause | Keep the notice; do not use the authors' names to endorse DemandIQ. |
| pyotp | MIT | Keep the copyright and permission notice. |
| email-validator | Unlicense | No attribution required; the notice is retained as a courtesy. |
| openpyxl | MIT | Keep the copyright and permission notice. |
| reportlab | BSD-3-Clause | Keep the notice; do not use the authors' names to endorse DemandIQ. |

## Model pipeline and serving

| Component | Licence | What the licence asks of DemandIQ |
| --- | --- | --- |
| pandas | BSD-3-Clause | Keep the notice; do not use the authors' names to endorse DemandIQ. |
| NumPy | BSD-3-Clause | Keep the notice and the notices bundled with it. |
| python-dateutil | Apache-2.0 / BSD-3-Clause (dual) | Keep the notice for the option relied on. |
| joblib | BSD-3-Clause | Keep the notice. |
| LightGBM | MIT | Keep the copyright and permission notice. |
| CatBoost | Apache-2.0 | Keep the licence and notices; state any changes. DemandIQ does not modify it. |
| XGBoost | Apache-2.0 | Keep the licence and notices; state any changes. DemandIQ does not modify it. |
| scikit-learn | BSD-3-Clause | Keep the notice; do not use the authors' names to endorse DemandIQ. |
| SHAP | MIT | Keep the copyright and permission notice. |
| PyArrow | Apache-2.0 | Keep the licence and notices; state any changes. DemandIQ does not modify it. |

## Frontend — JavaScript / TypeScript

| Component | Licence | What the licence asks of DemandIQ |
| --- | --- | --- |
| React / React DOM | MIT | Keep the copyright and permission notice. |
| Recharts | MIT | Keep the copyright and permission notice. |
| Framer Motion | MIT | Keep the copyright and permission notice. |
| lucide-react | ISC | Keep the copyright and permission notice. |
| qrcode.react | ISC | Keep the copyright and permission notice. |
| @emotion/react | MIT | Keep the copyright and permission notice. |
| Tailwind CSS | MIT | Keep the copyright and permission notice. |
| Vite | MIT | Keep the copyright and permission notice. |
| TypeScript | Apache-2.0 | Keep the licence and notices; state any changes. DemandIQ does not modify it. |
| Vitest | MIT | Keep the copyright and permission notice. |
| Playwright | Apache-2.0 | Keep the licence and notices; state any changes. DemandIQ does not modify it. |
| Testing Library (`@testing-library/*`) | MIT | Keep the copyright and permission notice. |
| axe-core | MPL-2.0 | Keep the notice; modified files, if any, stay under the MPL. DemandIQ does not modify it. |

## Local OCR

| Component | Licence | What the licence asks of DemandIQ |
| --- | --- | --- |
| Tesseract OCR engine | Apache-2.0 | Keep the licence and notices; state any changes. DemandIQ invokes it unmodified. |
| pytesseract | Apache-2.0 | Keep the licence and notices; state any changes. DemandIQ does not modify it. |
| Pillow | MIT-CMU (HPND) | Keep the copyright and permission notice. |

## Data

| Resource | Terms | How DemandIQ uses it |
| --- | --- | --- |
| Recruit Restaurant Visitor Forecasting (Recruit Holdings, 2018) | Kaggle competition rules; no open licence. | Research use only. The source files are not redistributed in this repository or in the serving bundle; only derived model artifacts and aggregated figures are kept. |
| URBAN-WASTE | Reviewed but not used in this build. | Not redistributed. |

## Services

| Service | Terms | Use |
| --- | --- | --- |
| Google Identity Services | Google API Services User Data Policy | Optional sign-in. Used only when a Google client ID is configured. |
| Cloudflare Turnstile | Cloudflare Turnstile terms | Optional human check on sign-in. Disabled when no secret is configured. |
| PostgreSQL | PostgreSQL Licence | Database. Unmodified. |
| Redis | BSD-3-Clause (Redis 7) | Optional shared rate-limit and MFA state. Unmodified. |

The optional outside vision service is not enabled in this deployment, so no
provider terms apply. If one is configured at deploy time, its terms must be
published here before any photo is sent to it.
