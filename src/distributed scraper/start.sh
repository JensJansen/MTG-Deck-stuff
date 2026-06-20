#!/bin/sh
set -e

tailscaled --tun=userspace-networking --socks5-server=127.0.0.1:1055 &

sleep 3

tailscale up \
  --authkey="${TAILSCALE_AUTHKEY}" \
  --hostname="fly-scraper-${FLY_MACHINE_ID:-unknown}" \
  --accept-dns=false

echo "Waiting for API to be reachable via Tailscale..."
until curl -sf --max-time 5 --socks5-hostname 127.0.0.1:1055 http://100.113.155.84:8000/health > /dev/null 2>&1; do
  sleep 5
done

echo "Tailscale up. Starting scraper..."
exec python scraper_node.py
