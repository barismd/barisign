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
API="https://api.github.com/repos/barismd/barisign/contents"
DIR="${BARISIGN_DIR:-$HOME/Documents/barisign}"

echo "BariSign installer"
echo "  installing into: $DIR"
echo

mkdir -p "$DIR/poc" "$DIR/bin" "$DIR/third_party/sideinstaller" \
         "$DIR/third_party/apple-roots" "$DIR/third_party/ca-bundle" || {
    echo "Could not create $DIR - in a-Shell only ~/Documents is writable."
    exit 1
}
cd "$DIR" || exit 1

# GitHub caches both the web (raw) URLs and the API for a minute or two after a
# push, and the unauthenticated API also rate-limits. Measure, not guess: the raw
# host with a no-cache header is fresh in practice, so try it first and keep the
# API as the fallback. Every request carries a cache-buster as well.
RAW="https://raw.githubusercontent.com/barismd/barisign/main"

fetch() {   # fetch <file-in-repo> <destination>
    printf '  %-40s' "$2"
    if curl -sSL -H 'Cache-Control: no-cache' -H 'Pragma: no-cache' \
            "$RAW/$1?cb=$STAMP" -o "$2" 2>/dev/null && [ -s "$2" ]; then
        echo "ok"
        return 0
    fi
    tmp="$2.api.tmp"
    if curl -sSL -H 'Cache-Control: no-cache' "$API/$1?ref=main&cb=$STAMP" -o "$tmp" 2>/dev/null \
       && [ -s "$tmp" ] \
       && python3 -c "import base64,json;d=json.load(open('$tmp'));open('$2','wb').write(base64.b64decode(d['content']))" 2>/dev/null \
       && [ -s "$2" ]; then
        rm -f "$tmp"
        echo "ok (API fallback)"
        return 0
    fi
    rm -f "$tmp"
    echo "FAILED"
    return 1
}

download_all() {
    STAMP="$(date +%s)"
    fail=0
    fetch poc/poc_login.py                    poc/poc_login.py                 || fail=1
    fetch poc/poc_portal.py                   poc/poc_portal.py                || fail=1
    fetch poc/barisign.py                     poc/barisign.py                  || fail=1
    fetch third_party/sideinstaller/signin.py third_party/sideinstaller/signin.py || fail=1
    fetch third_party/sideinstaller/portal.py third_party/sideinstaller/portal.py || fail=1
    fetch third_party/apple-roots/apple-roots.pem third_party/apple-roots/apple-roots.pem || fail=1
    fetch third_party/ca-bundle/cacert.pem    third_party/ca-bundle/cacert.pem || fail=1
    fetch variants.json                       variants.json                    || fail=1
    fetch bin/barisign                        bin/barisign                     || fail=1
    fetch bin/update                          bin/update                       || fail=1
    fetch bin/login                           bin/login                        || fail=1
    fetch bin/portal                          bin/portal                       || fail=1
    chmod +x bin/* 2>/dev/null
    return $fail
}

check_compiles() {
    echo
    echo "Checking that the scripts compile:"
    for f in poc/poc_login.py poc/poc_portal.py poc/barisign.py \
             third_party/sideinstaller/signin.py third_party/sideinstaller/portal.py; do
        if python3 -c "compile(open('$f').read(), 'x', 'exec')" 2>/dev/null; then
            echo "  $f  ok"
        else
            echo "  $f  FAILED"
            fail=1
        fi
    done
}

# Compiling is not enough: a half-stale set compiles and then fails on import.
# The wrappers are shell, so check their syntax too.
works() {
    python3 poc/barisign.py --help >/dev/null 2>&1 \
        && python3 poc/poc_portal.py --help >/dev/null 2>&1 \
        && sh -n bin/barisign && sh -n bin/login && sh -n bin/portal && sh -n bin/update
}

attempt=1
while [ "$attempt" -le 3 ]; do
    download_all
    check_compiles
    if [ "$fail" -eq 0 ] && works; then
        break
    fi
    if [ "$attempt" -lt 3 ]; then
        echo
        echo "GitHub is still handing out the previous copy (its cache lags a"
        echo "minute or two after a release). Retrying in 20 seconds..."
        sleep 20
    fi
    attempt=$((attempt + 1))
done

echo
if [ "$fail" -ne 0 ] || ! works; then
    echo "The download did not come out right. Nothing here is destructive, so"
    echo "just run this script again in a minute. If it keeps failing, check the"
    echo "network (Safari should open github.com)."
    exit 1
fi

echo "Ready. From $DIR run:"
echo
echo "  sh bin/barisign          guided run: check -> variant -> UDID -> Apple -> portal"
echo "  sh bin/barisign check    runtime + anisette + Apple TLS only"
echo
echo "To update later, run this script again (or: sh bin/update)."
