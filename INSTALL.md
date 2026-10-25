# INSTALL — RuneTally2

RuneTally2 is a single Python file that uses only the standard library. Installation is
therefore short: check the interpreter, run the setup script, seed the demo catalog, then
use the CLI.

One package manager, matching the committed manifest: **pip** (`requirements.txt`).

## Prerequisites (installed by you, never by the scripts)

| Requirement | Notes |
| --- | --- |
| Python 3.9 or newer | `python3 --version`. The sandbox/reference image has 3.11. |
| pip | comes with Python (`python3 -m pip --version`) |
| A POSIX shell | for `install.sh` and `demo.sh` (`bash`) |

No database, no compiler, no `libsqlite3`, no system package installation. Nothing needs
root.

## Step 1 — set up (installs, seeds, exits)

```bash
bash install.sh
```

What it does, in order:

1. refreshes `pip`, `setuptools`, `wheel` (best effort — offline machines can skip this;
   the project has no third-party dependencies),
2. `pip install -r requirements.txt` (a no-op by design: standard library only),
3. installs the optional `runetally` console command via
   `pip install -e . --no-build-isolation --no-deps`,
4. runs an import self-check and prints the version,
5. seeds `./runetally.json` **once** from `examples/inscriptions.tsv` using the demo
   passphrase `cenius`.

The script exits. It never starts a background service and never blocks on a prompt.
Re-running it is safe: an existing `./runetally.json` is left untouched.

## Step 2 — seed (already done by step 1)

Seeding is its own, idempotent step so you can repeat it deliberately:

```bash
# only if ./runetally.json does not exist yet — otherwise this rewrites nothing
export RUNETALLY_PASSPHRASE=cenius
python3 runetally.py --file ./runetally.json auth init
python3 runetally.py --file ./runetally.json import examples/inscriptions.tsv
python3 runetally.py --file ./runetally.json list
```

Skip the seed entirely for a clean catalog: just run `auth init` and start adding.

## Step 3 — run in dev

There is no server and no port. Run commands directly:

```bash
export RUNETALLY_PASSPHRASE=cenius      # demo passphrase; prompts otherwise
python3 runetally.py --help
python3 runetally.py list
python3 runetally.py add --name "Broby stone" --site Broby --stave-width 3.2 --wear 4
python3 runetally.py show 1
```

With the console command installed, `runetally list` is equivalent to
`python3 runetally.py list`. Both read `--file PATH` or `RUNETALLY_FILE`.

## Step 4 — demo

```bash
bash demo.sh        # every primary command against a throwaway temp catalog
```

`demo.txt` holds a captured run of the same script.

## Step 5 — tests

```bash
python3 smoke_test.py                      # 27 stdlib checks, ends with SMOKE OK
python3 -m unittest discover -s tests -v   # full suite, standard library only
python3 -m pytest tests -q                 # same suite if pytest is installed
```

## Troubleshooting

| Message | Fix |
| --- | --- |
| `error: no catalog at ./runetally.json` | run `python3 runetally.py auth init` |
| `error: authentication failed` | wrong passphrase — the demo value is `cenius` |
| `error: passphrase required for list: set RUNETALLY_PASSPHRASE ...` | export `RUNETALLY_PASSPHRASE` when stdin is not a terminal |
| `error: catalog ... is already initialized ...` | pass `--force` to `auth init` to set a new passphrase |
| `error: invalid TSV header: expected ...` | add the header line shown in README → "TSV import format" |
| `note: console entry point not installed` | `python3 runetally.py ...` still works; the entry point is optional |

## Uninstall

Delete the repository directory. Remove `./runetally.json` (the catalog) and, if you
installed the console command, `python3 -m pip uninstall runetally2`.
