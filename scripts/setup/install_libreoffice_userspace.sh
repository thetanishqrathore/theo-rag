#!/usr/bin/env bash
# Install a headless LibreOffice Writer without root (Ubuntu/Debian), for hosts
# where `sudo apt install libreoffice-writer-nogui` is not available.
# Prefer the apt install when you have root; this is the fallback used on the
# reference workstation. Produces a `soffice` wrapper in ~/.local/bin.
set -euo pipefail

PREFIX="${LO_PREFIX:-$HOME/.local/opt/lo-root}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Packages apt would install that are not already present on this host.
mapfile -t pkgs < <(apt-get install -s --no-install-recommends libreoffice-writer-nogui 2>/dev/null \
  | awk '/^Inst/ {print $2}')
if [ "${#pkgs[@]}" -eq 0 ]; then
  echo "No packages to fetch (already installed system-wide?)"; exit 0
fi

(cd "$WORK" && apt-get download "${pkgs[@]}")
mkdir -p "$PREFIX" "$BIN_DIR"
for deb in "$WORK"/*.deb; do dpkg -x "$deb" "$PREFIX"; done

# Re-point absolute symlinks and bootstrap paths into the prefix.
find "$PREFIX" -xtype l | while read -r link; do
  target="$(readlink "$link")"
  case "$target" in /*) ln -sfn "$PREFIX$target" "$link" ;; esac
done
PROG="$PREFIX/usr/lib/libreoffice/program"
sed -i "s|file:///usr/lib/libreoffice|file://$PREFIX/usr/lib/libreoffice|g; s|file:///etc/libreoffice|file://$PREFIX/etc/libreoffice|g" \
  "$PROG/fundamentalrc" "$PROG/sofficerc"

# The deb postinst normally links the registry layers into /etc/libreoffice.
REG="$PREFIX/etc/libreoffice/registry"
mkdir -p "$REG/res"
for f in "$PREFIX"/usr/lib/libreoffice/share/.registry/*.xcd; do ln -sfn "$f" "$REG/"; done
for f in "$PREFIX"/usr/lib/libreoffice/share/.registry/res/*; do [ -e "$f" ] && ln -sfn "$f" "$REG/res/"; done

cat > "$BIN_DIR/soffice" <<WRAP
#!/bin/sh
# User-space LibreOffice (extracted from distro debs by install_libreoffice_userspace.sh).
export LD_LIBRARY_PATH="$PROG:$PREFIX/usr/lib/x86_64-linux-gnu\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
exec "$PROG/soffice" "\$@"
WRAP
chmod +x "$BIN_DIR/soffice"
echo "Installed: $BIN_DIR/soffice"
"$BIN_DIR/soffice" --version || true
