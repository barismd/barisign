#!/bin/sh
# Release build for Open Minis (aarch64 musl, static). ring's C/asm parts need
# an aarch64 C compiler: zig's (pip install ziglang) or CC_aarch64... if set.
set -eu
cd "$(dirname "$0")/.."
TARGET=aarch64-unknown-linux-musl
if [ -z "${CC_aarch64_unknown_linux_musl:-}" ]; then
    ZIG="$(python3 -c 'import ziglang, os; print(os.path.join(os.path.dirname(ziglang.__file__), "zig"))' 2>/dev/null || command -v zig)"
    WRAP="$PWD/target/zigcc-aarch64"
    mkdir -p target
    # cc-rs passes --target=<rust triple>, which zig does not parse: drop it.
    cat > "$WRAP" <<WRAPPER
#!/bin/sh
for a in "\$@"; do shift; case "\$a" in --target=*) ;; *) set -- "\$@" "\$a" ;; esac; done
exec "$ZIG" cc -target aarch64-linux-musl "\$@"
WRAPPER
    chmod +x "$WRAP"
    export CC_aarch64_unknown_linux_musl="$WRAP"
    printf '#!/bin/sh\nexec "%s" ar "$@"\n' "$ZIG" > "$WRAP-ar"
    chmod +x "$WRAP-ar"
    export AR_aarch64_unknown_linux_musl="$WRAP-ar"
fi
cargo build --release --locked --target "$TARGET"
ls -l "target/$TARGET/release/baristore"
