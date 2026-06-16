#!/bin/sh
set -e

tailscaled --tun=userspace-networking --socks5-server=127.0.0.1:1055 &

sleep 3

tailscale up \
  --authkey="${TAILSCALE_AUTHKEY}" \
  --hostname="fly-scraper-${FLY_MACHINE_ID:-unknown}" \
  --accept-dns=false

echo "Tailscale up. Starting scraper..."
exec python scraper_node.py
