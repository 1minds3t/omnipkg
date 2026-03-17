#!/usr/bin/env bash
# build_dispatcher.sh — compile and optionally install the C dispatcher
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$SCRIPT_DIR/omnipkg_dispatch"

echo "🔨 Compiling dispatcher.c..."
gcc -O2 -Wall -Wextra -o "$OUT" "$SCRIPT_DIR/dispatcher.c"
echo "✅ Built: $OUT  ($(du -sh "$OUT" | cut -f1))"

# ── Optional install ──────────────────────────────────────────────────────
VENV_BIN="${CONDA_PREFIX:-${VIRTUAL_ENV:-}}/bin"
if [[ -z "$VENV_BIN" || "$VENV_BIN" == "/bin" ]]; then
    echo ""
    echo "⚠️  No active venv/conda env detected. Skipping auto-install."
    echo "   Manually install with:"
    echo "     cp $OUT \$VENV/bin/8pkg"
    echo "     cp $OUT \$VENV/bin/omnipkg"
    exit 0
fi

echo ""
echo "📦 Installing into $VENV_BIN ..."

# Backup originals
for cmd in 8pkg omnipkg; do
    target="$VENV_BIN/$cmd"
    if [[ -f "$target" && ! -f "${target}.py_backup" ]]; then
        cp "$target" "${target}.py_backup"
        echo "   💾 Backed up: $target -> ${target}.py_backup"
    fi
done

# Install binary
cp "$OUT" "$VENV_BIN/8pkg"
cp "$OUT" "$VENV_BIN/omnipkg"
chmod +x "$VENV_BIN/8pkg" "$VENV_BIN/omnipkg"
echo "   ✅ Installed 8pkg and omnipkg"

# Re-create versioned symlinks (they point to 8pkg which is now the binary)
echo "   🔗 Versioned symlinks already point to 8pkg — no changes needed"

echo ""
echo "🏁 Done! Test with:"
echo "   time 8pkg install rich==14.3.2"
echo ""
echo "🔙 To rollback:"
echo "   cp \$VENV/bin/8pkg.py_backup \$VENV/bin/8pkg"
echo "   cp \$VENV/bin/omnipkg.py_backup \$VENV/bin/omnipkg"