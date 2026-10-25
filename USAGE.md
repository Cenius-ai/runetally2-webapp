# Using runetally2

## Overview

Catalog runic inscriptions, sort by stave width, score pigment wear (standard library only).

## Quickstart

```bash
runetally --help
# or, if installed from source without symlink:
python -m runetally2 --help
```

## Commands

This CLI exposes **10** subcommands. Each was extracted from the source.

| Command | Source file |
|---------|-------------|
| `runetally add` | `runetally.py` |
| `runetally auth` | `runetally.py` |
| `runetally bands` | `runetally.py` |
| `runetally delete` | `runetally.py` |
| `runetally edit` | `runetally.py` |
| `runetally import` | `runetally.py` |
| `runetally init` | `runetally.py` |
| `runetally list` | `runetally.py` |
| `runetally show` | `runetally.py` |
| `runetally status` | `runetally.py` |

### Per-command help

```bash
runetally --help                # top-level help
runetally add --help    # help for `add`
```

## Worked example

1. Verify the CLI is on your PATH: `which runetally` (or run via `cargo run --`/`python -m`).
2. Show all subcommands: `runetally --help`.
3. Try the first detected subcommand:

```bash
runetally add --help
```

## Reference

### Entry points

| Kind | Name | Source |
|------|------|--------|
| Console script | `runetally` | `runetally:main` (pyproject.toml) |

### Surface index (auto-extracted)

| Kind | Title | Source file |
|------|-------|-------------|
| command | `add` | `runetally.py` |
| command | `auth` | `runetally.py` |
| command | `bands` | `runetally.py` |
| command | `delete` | `runetally.py` |
| command | `edit` | `runetally.py` |
| command | `import` | `runetally.py` |
| command | `init` | `runetally.py` |
| command | `list` | `runetally.py` |
| command | `show` | `runetally.py` |
| command | `status` | `runetally.py` |

## Next steps

- Read [INSTALL.md](./INSTALL.md) if anything in the Quickstart didn't work.
- Inspect `.env.example` for the full list of environment variables this project understands.
- See [README.md](./README.md) for the architecture diagram + feature list.
