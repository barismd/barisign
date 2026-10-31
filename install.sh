#!/bin/sh
# =============================================================================
#  BariSign installer — downloads every file it needs. No git required.
#
#  In a-Shell:
#      mkdir -p ~/Documents/barisign && cd ~/Documents/barisign
#      curl -sSL "https://github.com/barismd/barisign/blob/main/install.sh?raw=true" -o install.sh
#      sh install.sh
#
#  Run it again any time to update: it overwrites the program files and leaves the
#  saved state in state/ alone, so your Apple ID, UDID and profile stay.
# =============================================================================

set -u

BASE="https://github.com/barismd/barisign/blob/main"
DIR="${BARISIGN_DIR:-$HOME/Documents/barisign}"

printf 'BariSign installer\n\n'

mkdir -p "$DIR/poc" "$DIR/bin" "$DIR/third_party/sideinstaller" \
         "$DIR/third_party/apple-roots" "$DIR/third_party/ca-bundle" || {
    printf '  ! cannot create %s (a-Shell only writes inside ~/Documents)\n' "$DIR"
    exit 1
}
cd "$DIR" || exit 1

# GitHub caches the /main/ URLs for a while after a push, and the unauthenticated
# API rate-limits, so neither can be trusted for a fresh install. Instead we read
# the newest commit id from the public atom feed (not rate-limited) and download
# every file *pinned to that commit*: those URLs are immutable, so the set can
# never be a mix of two revisions.
ATOM="https://github.com/barismd/barisign/commits/main.atom"
# a-Shell has no rev/cut/head, so the commit id is read with python3 alone.
SHA=$(curl -sSL --max-time 25 "$ATOM" 2>/dev/null | python3 -c "
import re, sys
found = re.search(r'barismd/barisign/commit/([0-9a-f]{40})', sys.stdin.read())
print(found.group(1) if found else '')
" 2>/dev/null)
if [ -n "${SHA:-}" ]; then
    RAW="https://raw.githubusercontent.com/barismd/barisign/$SHA"
    printf '  revision  %.7s\n' "$SHA"
else
    RAW="https://raw.githubusercontent.com/barismd/barisign/main"
    printf '  revision  main (not pinned)\n'
    printf '  ! the commit id could not be read; GitHub can serve an older copy\n'
fi

fetch() {   # fetch <file-in-repo> <destination>
    if curl -sSL -H 'Cache-Control: no-cache' "$RAW/$1" -o "$2" 2>/dev/null && [ -s "$2" ]; then
        return 0
    fi
    if curl -sSL -H 'Cache-Control: no-cache' \
            "https://raw.githubusercontent.com/barismd/barisign/main/$1" -o "$2" 2>/dev/null \
       && [ -s "$2" ]; then
        return 0
    fi
    FAILED="${FAILED:-} $1"
    return 1
}

download_all() {
    fail=0
    fetch poc/poc_login.py                    poc/poc_login.py                 || fail=1
    fetch poc/poc_portal.py                   poc/poc_portal.py                || fail=1
    fetch poc/barisign.py                     poc/barisign.py                  || fail=1
    fetch poc/signer.py                       poc/signer.py                    || fail=1
    fetch poc/download.py                     poc/download.py                  || fail=1
    fetch poc/pair_probe.py                   poc/pair_probe.py                || fail=1
    fetch poc/pair_install.py                 poc/pair_install.py              || fail=1
    fetch poc/ashell_bonjour.py               poc/ashell_bonjour.py            || fail=1
    fetch third_party/sideinstaller/signin.py third_party/sideinstaller/signin.py || fail=1
    fetch third_party/sideinstaller/portal.py third_party/sideinstaller/portal.py || fail=1
    fetch third_party/apple-roots/apple-roots.pem third_party/apple-roots/apple-roots.pem || fail=1
    fetch third_party/ca-bundle/cacert.pem    third_party/ca-bundle/cacert.pem || fail=1
    fetch variants.json                       variants.json                    || fail=1
    fetch bin/barisign                        bin/barisign                     || fail=1
    fetch bin/update                          bin/update                       || fail=1
    chmod +x bin/* 2>/dev/null
    return $fail
}

# Prints the files that do not compile, and fails when there is one.
check_compiles() {
    bad=""
    for f in poc/poc_login.py poc/poc_portal.py poc/barisign.py poc/signer.py \
             poc/download.py poc/pair_probe.py poc/pair_install.py \
             poc/ashell_bonjour.py third_party/sideinstaller/signin.py \
             third_party/sideinstaller/portal.py; do
        if ! python3 -c "compile(open('$f').read(), 'x', 'exec')" 2>/dev/null; then
            bad="$bad $f"
        fi
    done
    [ -z "$bad" ] && return 0
    printf '%s\n' "$bad"
    return 1
}

# Compiling is not enough: a half-stale set compiles and then fails on import.
# The wrappers are shell, so check their syntax too.
works() {
    python3 poc/barisign.py --help >/dev/null 2>&1 \
        && python3 poc/poc_portal.py --help >/dev/null 2>&1 \
        && sh -n bin/barisign && sh -n bin/update
}

attempt=1
while [ "$attempt" -le 2 ]; do
    FAILED=""
    printf '  files     ...'
    download_all
    if [ "$fail" -eq 0 ]; then
        printf '\r\033[2K  files     ok\n'
    else
        printf '\r\033[2K  files     failed\n'
        printf '  ! could not fetch:%s\n' "$FAILED"
    fi

    printf '  check     ...'
    COMPILE_BAD=""
    if [ "$fail" -eq 0 ] && COMPILE_BAD=$(check_compiles) && works; then
        printf '\r\033[2K  check     ok\n'
        break
    fi
    printf '\r\033[2K  check     failed\n'
    if [ -n "${COMPILE_BAD:-}" ]; then
        printf '  ! does not compile:%s\n' "$COMPILE_BAD"
    fi
    if [ "$attempt" -lt 2 ]; then
        printf '  ! retrying in 5 s\n'
        sleep 5
    fi
    attempt=$((attempt + 1))
done

printf '\n'
if [ "$fail" -ne 0 ] || ! works; then
    printf '  ! install failed; nothing was changed outside the program files.\n'
    printf '    check the network (Safari should open github.com) and run sh install.sh again.\n'
    exit 1
fi
printf '  ready     sh bin/barisign\n'
