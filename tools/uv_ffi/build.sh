#!/bin/bash
# Build the uv-ffi PyO3 extension in-place (dev) or as a wheel (release)
# Usage: ./build.sh [--wheel]
set -e
CRATE="$(dirname "$0")/../../src/omnipkg/_vendor/uv/crates/uv-ffi"
if [ "$1" = "--wheel" ]; then
    maturin build --release --manifest-path "$CRATE/Cargo.toml"
else
    maturin develop --release --manifest-path "$CRATE/Cargo.toml"
fi
