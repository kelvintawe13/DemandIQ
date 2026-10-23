#!/bin/sh
# DemandIQ frontend — Railway entrypoint.
#
# Renders frontend/nginx.railway.conf.template into the nginx config the server
# actually loads, then hands PID 1 to nginx. Three values come from the
# environment, every one with a safe default so the image also runs standalone:
#
#   PORT          port to listen on. Railway injects PORT; 8080 otherwise.
#   API_UPSTREAM  host:port of the FastAPI service. Defaults to the Railway
#                 private-network name of the API service.
#   resolver      NOT an env var: read from this container's /etc/resolv.conf,
#                 which is where Railway (and Docker) publish their DNS server.
#                 Hardcoding 127.0.0.11 was the compose-only assumption this
#                 script exists to remove.
#
# Why sed rather than nginx's envsubst templates: envsubst would also expand
# nginx's own $host / $uri / $request_uri / $http_upgrade, silently corrupting
# the config. Replacing three explicit tokens cannot.
set -eu

PORT="${PORT:-8080}"
API_UPSTREAM="${API_UPSTREAM:-api.railway.internal:8000}"
TEMPLATE="/etc/nginx/railway/nginx.conf.template"
TARGET="/etc/nginx/conf.d/default.conf"

# resolv.conf lines look like `nameserver fd12::10` or `nameserver 10.0.0.2`.
# nginx wants `[v6]:53` / `v4:53`, and accepts a space-separated list it tries
# in order.
RESOLVER="$(awk '
    /^[[:space:]]*nameserver[[:space:]]+/ {
        addr = $2
        if (addr ~ /:/) printf "[%s]:53 ", addr
        else             printf "%s:53 ", addr
    }
' /etc/resolv.conf 2>/dev/null || true)"
# awk leaves a trailing space; nginx accepts it, but tidy output is easier to
# eyeball in a deployment log.
RESOLVER="$(printf '%s' "${RESOLVER}" | sed 's/[[:space:]]*$//')"

# No nameservers (unusual, but a stripped container can have none): fall back
# to Docker's embedded DNS so the failure is a 502 from an unresolvable name
# rather than a config that nginx refuses to load at all.
if [ -z "${RESOLVER}" ]; then
    RESOLVER="127.0.0.11:53"
fi

sed \
    -e "s|__PORT__|${PORT}|g" \
    -e "s|__API_UPSTREAM__|${API_UPSTREAM}|g" \
    -e "s|__RESOLVER__|${RESOLVER}|g" \
    "${TEMPLATE}" > "${TARGET}"

echo "demandiq-frontend: listening on ${PORT}, proxying /api to ${API_UPSTREAM} (resolver: ${RESOLVER})"

# Fail here, with the rendered config in the log, rather than as a bare
# "host not found" five lines later.
nginx -t

exec nginx -g 'daemon off;'
