#!/bin/bash
# Copy a renewed Let's Encrypt lineage onto Caddy's origin paths and reload.
set -euo pipefail
LINEAGE="${RENEWED_LINEAGE:-/etc/letsencrypt/live/moreney.ai}"
install -o caddy -g caddy -m 644 "${LINEAGE}/fullchain.pem" /etc/caddy/certs/origin.pem
install -o caddy -g caddy -m 640 "${LINEAGE}/privkey.pem" /etc/caddy/certs/origin.key
systemctl reload caddy
