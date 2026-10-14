#!/bin/sh
# =============================================================================
#  BariSign installer — downloads every file it needs. No git required.
#
#  In a-Shell:
#      mkdir -p ~/Documents/barisign && cd ~/Documents/barisign
#      curl -sSL "https://github.com/barismd/barisign/blob/main/install.sh?raw=true" -o install.sh
#      sh install.sh
#
#  Run it again any time to update: it overwrites the program files and leaves
#  your certificate authority (poc/barisign-ca.*) alone, so devices stay trusted.
# =============================================================================

set -u

BASE="https://github.com/barismd/barisign/blob/main"
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

# The web (raw) URLs sit behind a CDN that can serve a stale copy for a few
# minutes after an update. The GitHub API is not cached, so try it first and keep
# the web URL only as a fallback.
API="https://api.github.com/repos/barismd/barisign/contents"

fetch() {   # fetch <file-in-repo> <destination>
    printf '  %-40s' "$2"
    tmp="$2.api.tmp"
    if curl -sSL "$API/$1" -o "$tmp" 2>/dev/null && [ -s "$tmp" ] \
       && python3 -c "import base64,json;d=json.load(open('$tmp'));open('$2','wb').write(base64.b64decode(d['content']))" 2>/dev/null \
       && [ -s "$2" ]; then
        rm -f "$tmp"
        echo "ok"
        return 0
    fi
    rm -f "$tmp"
    if curl -sSL "$BASE/$1?raw=true" -o "$2" 2>/dev/null && [ -s "$2" ]; then
        echo "ok (web fallback)"
        return 0
    fi
    echo "FAILED"
    return 1
}

fail=0
fetch poc/udid_share.py                   poc/udid_share.py                || fail=1
fetch poc/poc_login.py                    poc/poc_login.py                 || fail=1
fetch poc/poc_portal.py                   poc/poc_portal.py                || fail=1
fetch poc/barisign.py                     poc/barisign.py                  || fail=1
fetch third_party/sideinstaller/signin.py third_party/sideinstaller/signin.py || fail=1
fetch third_party/sideinstaller/portal.py third_party/sideinstaller/portal.py || fail=1
fetch third_party/apple-roots/apple-roots.pem third_party/apple-roots/apple-roots.pem || fail=1
fetch third_party/ca-bundle/cacert.pem    third_party/ca-bundle/cacert.pem || fail=1
fetch variants.json                        variants.json                    || fail=1
fetch bin/barisign                        bin/barisign                     || fail=1
fetch bin/update                          bin/update                       || fail=1
fetch bin/udid                            bin/udid                         || fail=1
fetch bin/login                           bin/login                        || fail=1
fetch bin/portal                          bin/portal                       || fail=1

chmod +x bin/* 2>/dev/null

echo
echo "Checking that the scripts work:"
for f in poc/udid_share.py poc/poc_login.py poc/poc_portal.py poc/barisign.py \
         third_party/sideinstaller/signin.py third_party/sideinstaller/portal.py; do
    if python3 -c "compile(open('$f').read(), 'x', 'exec')" 2>/dev/null; then
        echo "  $f  ok"
    else
        echo "  $f  FAILED TO COMPILE"
        fail=1
    fi
done

echo
if [ "$fail" -ne 0 ]; then
    echo "Something did not download. Check the network and run this script again."
    exit 1
fi

echo "Ready. From $DIR run:"
echo
echo "  sh bin/barisign          guided run: check -> variant -> UDID -> Apple -> portal"
echo "  sh bin/barisign check    runtime + anisette + Apple TLS only"
echo
echo "To update later, run this script again (or: sh bin/update)."
echo "Your certificate authority in poc/barisign-ca.* is never touched."
