#!/usr/bin/env bash
# RuneTally2 end-to-end demo.
#
# Runs every primary command against a throwaway catalog in a temp directory, so
# nothing in your working tree is touched. Non-interactive: the operator
# passphrase comes from RUNETALLY_PASSPHRASE (demo value: cenius).
#
#   bash demo.sh
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/runetally-demo.XXXXXX")"
CATALOG="$WORK_DIR/runetally-demo.json"
export RUNETALLY_PASSPHRASE="${RUNETALLY_PASSPHRASE:-cenius}"
export PYTHONIOENCODING=utf-8
export NO_COLOR=1

cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

# Print the command, then run it - this is what makes the recording readable.
run() {
  printf '\n$ %s\n' "$*"
  "$@"
}

RT=("$PYTHON" runetally.py --file "$CATALOG")

printf 'RuneTally2 demo - catalog file: %s\n' "$CATALOG"

run "${RT[@]}" --help
run "${RT[@]}" auth init
run "${RT[@]}" auth status
run "${RT[@]}" bands

run "${RT[@]}" add --name "Broby stone" --site Broby --period "Viking Age" --stave-width 3.2 --wear 4
run "${RT[@]}" add --name "Noleby stone" --site "Västergötland, Sweden" --period "Migration Period (c. 600)" --stave-width 11.5 --wear 3 --notes "Bound staves; pigment survives in the deep channels"
run "${RT[@]}" add --name "Vadstena fragment" --site "Östergötland, Sweden" --period "Medieval (c. 1200)" --stave-width 4.05 --wear 2
run "${RT[@]}" add --name "Hovgården rune block" --site "Adelsö, Sweden" --period "Viking Age (c. 1050)" --stave-width 8.01 --wear 5 --notes "Pigment completely lost; width measured at mid-stave"
run "${RT[@]}" add --name "Sigtuna fragment" --site "Sigtuna, Sweden" --period "Medieval (c. 1150)" --notes "Width not recorded in the field notebook"

run "${RT[@]}" list
run "${RT[@]}" list --sort stave-band
run "${RT[@]}" list --sort stave-width --order desc
run "${RT[@]}" list --band broad
run "${RT[@]}" list --wear 5

run "${RT[@]}" show 1
run "${RT[@]}" show 5

run "${RT[@]}" edit 3 --wear 0 --stave-width 6.5
run "${RT[@]}" show 3

run "${RT[@]}" import examples/inscriptions.tsv
run "${RT[@]}" import examples/inscriptions_messy.tsv
run "${RT[@]}" list --sort wear

run "${RT[@]}" delete 5 --yes
run "${RT[@]}" list --band unset
run "${RT[@]}" add --name "After delete" --wear 1
run "${RT[@]}" auth status

printf '\nDEMO OK\n'
