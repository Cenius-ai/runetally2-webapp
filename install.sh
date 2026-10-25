#!/usr/bin/env bash
# RuneTally2 setup: installs, seeds, and EXITS. It never starts a server and
# never runs a system package manager - the project has no third-party
# dependencies, so there is nothing to compile or download.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

echo "RuneTally2 setup"
"$PYTHON" --version

# 1. Tooling first (pip/setuptools/wheel). Best effort: an offline machine can
#    still run RuneTally2 because there is nothing to install.
"$PYTHON" -m pip install --upgrade pip setuptools wheel >/dev/null 2>&1 ||
  echo "note: could not refresh pip/setuptools/wheel (offline?) - continuing"

# 2. Declared dependencies. Standard library only, so this is a no-op by design.
"$PYTHON" -m pip install -r requirements.txt

# 3. Optional console entry point, giving you the short `runetally` command.
"$PYTHON" -m pip install -e . --no-build-isolation --no-deps >/dev/null 2>&1 ||
  echo "note: console entry point not installed; use 'python3 runetally.py' instead"

# 4. Import self-check: fails loudly here if a module is missing at runtime.
"$PYTHON" -c 'import runetally; assert callable(runetally.main)'
"$PYTHON" runetally.py --version

# 5. Seed the demo catalog once (idempotent). Passphrase: cenius.
if [ ! -f runetally.json ]; then
  RUNETALLY_PASSPHRASE="${RUNETALLY_PASSPHRASE:-cenius}" \
    "$PYTHON" runetally.py --file runetally.json auth init
  RUNETALLY_PASSPHRASE="${RUNETALLY_PASSPHRASE:-cenius}" \
    "$PYTHON" runetally.py --file runetally.json import examples/inscriptions.tsv
  echo "seeded ./runetally.json from examples/inscriptions.tsv"
else
  echo "./runetally.json already exists - seed skipped (idempotent)"
fi

cat <<'DONE'

Setup complete.
  list the catalog : python3 runetally.py list      (passphrase: cenius)
  full walkthrough : bash demo.sh
  command reference: README.md   troubleshooting: INSTALL.md
DONE
