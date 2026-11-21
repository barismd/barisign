#!/bin/sh
# BariStore installer for Open Minis (Alpine aarch64). One line in Minis's
# terminal:
#   wget -qO- https://raw.githubusercontent.com/barismd/BariStore/main/install.sh | sh
# Downloads the newest build, checks its SHA-256, installs `baristore`, starts it.
set -eu

URL="https://github.com/barismd/BariStore/releases/download/nightly"
BIN_DIR="$HOME/.baristore/bin"

case "$(uname -m)" in
    aarch64|arm64) ;;
    *) echo "BariStore runs in Open Minis on an iPhone or iPad (aarch64); this is $(uname -m)."; exit 1 ;;
esac

fetch() { # url dest
    if command -v wget >/dev/null 2>&1; then wget -qO "$2" "$1"; else curl -fsSL -o "$2" "$1"; fi
}

mkdir -p "$BIN_DIR"
NEW="$BIN_DIR/.baristore.new"
echo "Downloading BariStore…"
fetch "$URL/baristore-aarch64" "$NEW" || { echo "Download failed. Check the internet connection and try again."; exit 1; }
fetch "$URL/baristore-aarch64.sha256" "$NEW.sha256" || { echo "Could not download the checksum."; rm -f "$NEW"; exit 1; }
want="$(cut -d' ' -f1 "$NEW.sha256")"
have="$(sha256sum "$NEW" | cut -d' ' -f1)"
rm -f "$NEW.sha256"
if [ "$want" != "$have" ]; then
    rm -f "$NEW"
    echo "The download does not match its checksum; nothing was installed."
    exit 1
fi
chmod 755 "$NEW"
mv -f "$NEW" "$BIN_DIR/baristore"

LINK=""
for dir in /usr/local/bin /usr/bin; do
    if [ -d "$dir" ] && [ -w "$dir" ]; then
        ln -sf "$BIN_DIR/baristore" "$dir/baristore" && LINK="$dir/baristore" && break
    fi
done
echo "Installed $("$BIN_DIR/baristore" --version)."
if [ -z "$LINK" ]; then
    echo "Add it to your PATH:  export PATH=\"\$HOME/.baristore/bin:\$PATH\""
fi
echo "Next time, type: baristore"
exec "$BIN_DIR/baristore"
