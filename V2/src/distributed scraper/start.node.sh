#!/bin/sh
# Bring up Tailscale (userspace networking + SOCKS5 proxy), wait until the API
# is ready, then hand off to the node. The proxy is required: in userspace mode
# there is no kernel route to the tailnet IP, so all traffic (API + Moxfield)
# goes through 127.0.0.1:1055 via ALL_PROXY.
set -e

# Fail fast with a clear message instead of hanging on Tailscale's interactive
# login when the auth key is missing.
if [ -z "${TAILSCALE_AUTHKEY}" ]; then
  echo "FATAL: TAILSCALE_AUTHKEY is not set. Run: fly secrets set TAILSCALE_AUTHKEY=tskey-... -a deck-gen-v2-node" >&2
  exit 1
fi

tailscaled --tun=userspace-networking --socks5-server=127.0.0.1:1055 &

sleep 3

tailscale up \
  --authkey="${TAILSCALE_AUTHKEY}" \
  --hostname="fly-v2-node-${FLY_MACHINE_ID:-unknown}" \
  --accept-dns=false

# Probe /ready (not just /health): it confirms the API's in-memory card map has
# finished loading, so the node never starts claiming before the API can serve.
echo "Waiting for API readiness at ${SCRAPER_API_URL}/ready ..."
until curl -sf --max-time 5 --socks5-hostname 127.0.0.1:1055 "${SCRAPER_API_URL}/ready" > /dev/null 2>&1; do
  sleep 5
done

echo "API ready. Starting node ..."
exec python node.py
