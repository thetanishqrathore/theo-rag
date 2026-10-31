#!/usr/bin/env bash
# Publish the site through a Cloudflare Tunnel as a systemd *user* service.
#
#   scripts/ops/install_tunnel.sh TUNNEL_NAME_OR_UUID [HOSTNAME]
#
# One-time prerequisites (done interactively):
#   cloudflared tunnel login                 # authorize the zone (writes ~/.cloudflared/cert.pem)
#   cloudflared tunnel create <name>         # writes ~/.cloudflared/<uuid>.json
#   cloudflared tunnel route dns <name> <hostname>
# This script writes ~/.cloudflared/config.yml from ops/cloudflared/config.bibleai.yml
# (only the endpoints the web UI needs are published), validates it, and installs
# theo-rag-tunnel.service (starts at boot with lingering, restarts on failure).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TUNNEL="${1:?usage: install_tunnel.sh TUNNEL_NAME_OR_UUID [HOSTNAME]}"
HOSTNAME_PUBLIC="${2:-bibleai.raeonlabs.com}"
CLOUDFLARED="$(command -v cloudflared || echo "$HOME/.local/bin/cloudflared")"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

UUID="$("$CLOUDFLARED" tunnel info "$TUNNEL" 2>/dev/null | sed -n 's/^ID:[[:space:]]*//p' | head -1)"
[[ -z "$UUID" ]] && UUID="$TUNNEL"
[[ -f "$HOME/.cloudflared/$UUID.json" ]] || { echo "missing credentials ~/.cloudflared/$UUID.json" >&2; exit 1; }

sed -e "s|<TUNNEL_UUID>|$UUID|g" -e "s|/home/<USER>|$HOME|" -e "s|bibleai.raeonlabs.com|$HOSTNAME_PUBLIC|g" \
  "$ROOT_DIR/ops/cloudflared/config.bibleai.yml" > "$HOME/.cloudflared/config.yml"
"$CLOUDFLARED" tunnel --config "$HOME/.cloudflared/config.yml" ingress validate

mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/theo-rag-tunnel.service" <<UNIT
[Unit]
Description=Cloudflare Tunnel for theo-rag ($HOSTNAME_PUBLIC)
After=network-online.target theo-rag.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=$CLOUDFLARED --no-autoupdate tunnel --config $HOME/.cloudflared/config.yml run $UUID
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
systemctl --user enable theo-rag-tunnel.service
systemctl --user restart theo-rag-tunnel.service
echo "tunnel service installed for https://$HOSTNAME_PUBLIC"
systemctl --user --no-pager status theo-rag-tunnel.service | head -5
