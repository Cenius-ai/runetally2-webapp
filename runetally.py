#!/usr/bin/env python3
"""RuneTally2 - catalog runic inscriptions, sort by stave width, score pigment wear.

Single-file, standard-library-only command line tool. The catalog is one local
JSON file (``--file``, default ``./runetally.json``) guarded by an operator
passphrase. See README.md for the command reference and the band rubric.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Sequence

VERSION = "2.0.0"
PROG = "runetally"

SCHEMA_VERSION = 1
DEFAULT_FILE = "runetally.json"
FILE_ENV = "RUNETALLY_FILE"
PASSPHRASE_ENV = "RUNETALLY_PASSPHRASE"
OPERATOR_ENV = "RUNETALLY_OPERATOR"
DEFAULT_OPERATOR = "cenius"
DEMO_PASSPHRASE = "cenius"

PBKDF2_ITERATIONS = 240_000
PBKDF2_ALGORITHM = "sha256"
SALT_BYTES = 16

NAME_MAX = 120
SITE_MAX = 80
PERIOD_MAX = 40
NOTES_MAX = 500

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2

# Design tokens (calm-precise, terminal dark, one accent).
#   accent  oklch(0.58 0.12 201) == #008e97
ACCENT_RGB = (0, 142, 151)
ANSI_RESET = "\x1b[0m"
ANSI_DIM = "\x1b[2m"

# Fixed table geometry: 5 + 2 + 32 + 2 + 20 + 2 + 20 + 2 + 8 + 2 + 5 == 100 columns.
SEP = "  "
COLUMN_WIDTHS = {"id": 5, "name": 32, "site": 20, "period": 20, "stave": 8, "wear": 5}
MAX_ROW_WIDTH = 100

WEAR_LABELS = {0: "pristine", 1: "slight", 2: "light", 3: "moderate", 4: "heavy", 5: "lost"}
WEAR_CODES = {0: "pri", 1: "sli", 2: "lig", 3: "mod", 4: "hea", 5: "los"}
BAND_CODES = {"narrow": "nar", "medium": "med", "broad": "bro", "unset": "uns"}

NARROW_MAX = Decimal("4.0")
MEDIUM_MAX = Decimal("8.0")

SORT_KEYS = ("id", "name", "stave-width", "stave-band", "wear")
ORDER_KEYS = ("asc", "desc")
BAND_NAMES = ("narrow", "medium", "broad", "unset")

NULL_TOKENS = {"", "-", "null", "none", "nil", "unset"}

FIELD_LABELS = {
    "name": "name",
    "site": "site",
    "period": "period",
    "stave_width_mm": "stave width",
    "pigment_wear": "wear",
    "notes": "notes",
}

IMPORT_COLUMNS = (
    "source_id",
    "name",
    "site",
    "period",
    "stave_width_mm",
    "pigment_wear",
    "notes",
)

BAND_RUBRIC = (
    "Stave width bands (mm):",
    "  narrow   width <= 4.0",
    "  medium   4.0 < width <= 8.0",
    "  broad    width > 8.0",
    "  unset    no width recorded (null)",
)

WEAR_SCALE = (
    "Pigment wear scale (0-5):",
    "  0  pristine",
    "  1  slight",
    "  2  light",
    "  3  moderate",
    "  4  heavy",
    "  5  lost",
    "  unset  wear not scored (null)",
)

EPILOG = "\n".join(
    (
        *BAND_RUBRIC,
        "",
        "Pigment wear: 0 pristine, 1 slight, 2 light, 3 moderate, 4 heavy, 5 lost; null = unset.",
        "",
        f"Passphrase: {PASSPHRASE_ENV} for non-interactive use "
        f"(demo account: {DEFAULT_OPERATOR} / {DEMO_PASSPHRASE}).",
        "",
        "Examples:",
        f"  {PROG} auth init",
        f'  {PROG} add --name "Broby stone" --site Broby --period "Viking Age" '
        "--stave-width 3.2 --wear 4",
        f"  {PROG} list --sort stave-band --band narrow",
        f"  {PROG} import examples/inscriptions.tsv",
    )
)


# --------------------------------------------------------------------------
# Errors and output styling
# --------------------------------------------------------------------------


class CliError(Exception):
    """Base class for errors reported as one line with an exit code."""

    exit_code = EXIT_FAILURE


class UsageError(CliError):
    """Bad invocation or invalid user input (exit code 2)."""

    exit_code = EXIT_USAGE


class Failure(CliError):
    """Runtime, IO, or authentication failure (exit code 1)."""

    exit_code = EXIT_FAILURE


class Style:
    """Minimal ANSI writing: one accent, one dim, no colour-only signals."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"{code}{text}{ANSI_RESET}" if self.enabled else text

    def accent(self, text: str) -> str:
        red, green, blue = ACCENT_RGB
        return self._wrap(f"\x1b[38;2;{red};{green};{blue}m", text)

    def dim(self, text: str) -> str:
        return self._wrap(ANSI_DIM, text)


def colour_enabled(no_color_flag: bool) -> bool:
    if no_color_flag or os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    return sys.stdout.isatty()


def emit(text: str = "") -> None:
    print(text)


# --------------------------------------------------------------------------
# Catalog file IO
# --------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def resolve_catalog_path(cli_value: str | None) -> Path:
    raw = cli_value or os.environ.get(FILE_ENV) or DEFAULT_FILE
    return Path(raw).expanduser()


def empty_catalog(passphrase: str) -> dict[str, Any]:
    salt = secrets.token_bytes(SALT_BYTES)
    return {
        "schema_version": SCHEMA_VERSION,
        "operator_name": os.environ.get(OPERATOR_ENV) or DEFAULT_OPERATOR,
        "operator_hash": hash_passphrase(passphrase, salt, PBKDF2_ITERATIONS),
        "operator_salt": salt.hex(),
        "operator_iterations": PBKDF2_ITERATIONS,
        "next_seq": 1,
        "inscriptions": [],
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }


def load_catalog(path: Path) -> dict[str, Any] | None:
    """Read the catalog, or return None when the file does not exist yet."""
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise Failure(f"cannot read catalog {path}: {exc.strerror or exc}") from exc
    except json.JSONDecodeError as exc:
        raise Failure(f"catalog {path} is not valid JSON (line {exc.lineno}: {exc.msg})") from exc
    if not isinstance(data, dict):
        raise Failure(f"catalog {path} is not a RuneTally catalog (expected a JSON object)")
    return normalise_catalog(data, path)


def normalise_catalog(data: dict[str, Any], path: Path) -> dict[str, Any]:
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise Failure(
            f"catalog {path} has schema_version {version!r}; this build supports {SCHEMA_VERSION}"
        )
    inscriptions = data.get("inscriptions", [])
    if not isinstance(inscriptions, list) or not all(isinstance(r, dict) for r in inscriptions):
        raise Failure(f"catalog {path} has a malformed inscriptions list")
    for record in inscriptions:
        record.setdefault("site", "")
        record.setdefault("period", "")
        record.setdefault("notes", "")
        record.setdefault("stave_width_mm", None)
        record.setdefault("pigment_wear", None)
        record.setdefault("created_at", "")
        record.setdefault("updated_at", "")
    next_seq = data.get("next_seq", 1)
    if not isinstance(next_seq, int) or next_seq < 1:
        raise Failure(f"catalog {path} has an invalid next_seq value")
    data["inscriptions"] = inscriptions
    data["next_seq"] = next_seq
    return data


def save_catalog(path: Path, catalog: dict[str, Any]) -> None:
    """Write the catalog atomically: temp file in the same directory, then os.replace."""
    directory = path.parent
    temp_name = ""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        catalog["updated_at"] = now_iso()
        handle_fd, temp_name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=directory)
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            json.dump(catalog, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        temp_name = ""
    except OSError as exc:
        raise Failure(f"cannot write catalog {path}: {exc.strerror or exc}") from exc
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def require_catalog(path: Path) -> dict[str, Any]:
    catalog = load_catalog(path)
    if catalog is None:
        raise Failure(f"no catalog at {path}: run '{PROG} --file {path} auth init' first")
    return catalog


# --------------------------------------------------------------------------
# Operator passphrase
# --------------------------------------------------------------------------


def hash_passphrase(passphrase: str, salt: bytes, iterations: int) -> str:
    return hashlib.pbkdf2_hmac(PBKDF2_ALGORITHM, passphrase.encode("utf-8"), salt, iterations).hex()


def operator_name(catalog: dict[str, Any]) -> str:
    return str(catalog.get("operator_name") or DEFAULT_OPERATOR)


def read_existing_passphrase(action: str) -> str:
    """Passphrase for a guarded command: environment first, then an interactive prompt."""
    env_value = os.environ.get(PASSPHRASE_ENV)
    if env_value is not None:
        if not env_value:
            raise Failure(f"passphrase required for {action}: {PASSPHRASE_ENV} is empty")
        return env_value
    if not sys.stdin.isatty():
        raise Failure(
            f"passphrase required for {action}: set {PASSPHRASE_ENV} or run {PROG} in a terminal"
        )
    try:
        return getpass.getpass(f"Operator passphrase ({action}): ")
    except (EOFError, KeyboardInterrupt) as exc:
        raise Failure("passphrase prompt cancelled") from exc


def read_new_passphrase() -> str:
    """New passphrase for auth init: environment value, or a confirmed double prompt."""
    env_value = os.environ.get(PASSPHRASE_ENV)
    if env_value is not None:
        if not env_value:
            raise UsageError("passphrase must not be empty")
        return env_value
    if not sys.stdin.isatty():
        raise Failure(f"passphrase required: set {PASSPHRASE_ENV} or run {PROG} in a terminal")
    try:
        first = getpass.getpass("New operator passphrase: ")
        second = getpass.getpass("Repeat passphrase: ")
    except (EOFError, KeyboardInterrupt) as exc:
        raise Failure("passphrase prompt cancelled") from exc
    if not first:
        raise UsageError("passphrase must not be empty")
    if first != second:
        raise UsageError("passphrases do not match")
    return first


def authenticate(catalog: dict[str, Any], path: Path, action: str) -> None:
    """Verify the operator passphrase; the only failure message is non-enumerating."""
    stored_hash = catalog.get("operator_hash")
    stored_salt = catalog.get("operator_salt")
    if not stored_hash or not stored_salt:
        raise Failure(f"catalog {path} has no operator passphrase: run '{PROG} auth init'")
    passphrase = read_existing_passphrase(action)
    try:
        salt = bytes.fromhex(str(stored_salt))
    except ValueError as exc:
        raise Failure(f"catalog {path} has a malformed operator salt") from exc
    iterations = catalog.get("operator_iterations", PBKDF2_ITERATIONS)
    candidate = hash_passphrase(passphrase, salt, int(iterations))
    if not hmac.compare_digest(str(stored_hash), candidate):
        raise Failure("authentication failed")


# --------------------------------------------------------------------------
# Field validation
# --------------------------------------------------------------------------


def clean_text(value: str | None, field: str, max_length: int, required: bool = False) -> str:
    """Trim, flatten whitespace, and length-check one free-text field."""
    text = re.sub(r"[\t\r\n]+", " ", value or "").strip()
    if required and not text:
        raise UsageError(f"{field} is required")
    if len(text) > max_length:
        raise UsageError(f"{field} must be at most {max_length} characters")
    return text


def format_decimal(value: Decimal) -> str:
    """Canonical decimal text: no exponent, no trailing zeros ("4.0" -> "4")."""
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def parse_stave_width(raw: str | None) -> str | None:
    if raw is None:
        return None
    token = raw.strip()
    if token.lower() in NULL_TOKENS:
        return None
    try:
        value = Decimal(token)
    except InvalidOperation as exc:
        raise UsageError("stave width must be a positive number") from exc
    if not value.is_finite() or value <= 0:
        raise UsageError("stave width must be a positive number")
    return format_decimal(value)


def parse_pigment_wear(raw: str | None) -> int | None:
    if raw is None:
        return None
    token = raw.strip()
    if token.lower() in NULL_TOKENS:
        return None
    if not re.fullmatch(r"[+-]?\d+", token):
        raise UsageError("pigment wear must be 0-5")
    value = int(token)
    if value < 0 or value > 5:
        raise UsageError("pigment wear must be 0-5")
    return value


# --------------------------------------------------------------------------
# Band and wear presentation
# --------------------------------------------------------------------------


def band_for(width_text: str | None) -> str:
    """Total partition: narrow <= 4.0; medium > 4.0 and <= 8.0; broad > 8.0; unset."""
    if width_text is None:
        return "unset"
    try:
        value = Decimal(str(width_text))
    except InvalidOperation:
        return "unset"
    if value <= NARROW_MAX:
        return "narrow"
    if value <= MEDIUM_MAX:
        return "medium"
    return "broad"


def stave_display(width_text: str | None) -> str:
    if width_text is None:
        return "-"
    text = str(width_text)
    return text if "." in text else f"{text}.0"


def wear_label(value: int | None) -> str:
    return "unset" if value is None else WEAR_LABELS[int(value)]


def _truncate(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def _column(text: str, width: int, align: str = "<") -> str:
    clipped = _truncate(text, width)
    return clipped.rjust(width) if align == ">" else clipped.ljust(width)


def format_record_row(record: dict[str, Any]) -> str:
    width_text = record.get("stave_width_mm")
    band = band_for(width_text)
    stave_cell = f"{_truncate(stave_display(width_text), 4).rjust(4)} {BAND_CODES[band]}"
    wear_value = record.get("pigment_wear")
    if wear_value is None:
        wear_cell = "- uns"
    else:
        wear_cell = f"{_truncate(str(wear_value), 1)} {WEAR_CODES[int(wear_value)]}"
    cells = (
        _column(str(record.get("id", "?")), COLUMN_WIDTHS["id"], ">"),
        _column(str(record.get("name", "")), COLUMN_WIDTHS["name"]),
        _column(str(record.get("site", "")), COLUMN_WIDTHS["site"]),
        _column(str(record.get("period", "")), COLUMN_WIDTHS["period"]),
        _column(stave_cell, COLUMN_WIDTHS["stave"]),
        _column(wear_cell, COLUMN_WIDTHS["wear"]),
    )
    return SEP.join(cells)


def table_header() -> str:
    return SEP.join(
        (
            _column("ID", COLUMN_WIDTHS["id"], ">"),
            _column("NAME", COLUMN_WIDTHS["name"]),
            _column("SITE", COLUMN_WIDTHS["site"]),
            _column("PERIOD", COLUMN_WIDTHS["period"]),
            _column("STAVE", COLUMN_WIDTHS["stave"], ">"),
            _column("WEAR", COLUMN_WIDTHS["wear"]),
        )
    )


def table_legend() -> tuple[str, str]:
    band_line = (
        "bands: nar = narrow (<= 4.0)  med = medium (> 4.0, <= 8.0)  "
        "bro = broad (> 8.0)  uns = unset"
    )
    wear_line = "wear: 0 pri 1 sli 2 lig 3 mod 4 hea 5 los  (full labels: runetally show <id>)"
    return band_line, wear_line


# --------------------------------------------------------------------------
# Sorting and filtering
# --------------------------------------------------------------------------


def _by_id(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(records, key=lambda record: int(record.get("id", 0)))


def sort_inscriptions(
    records: Sequence[dict[str, Any]], sort_key: str, order: str
) -> list[dict[str, Any]]:
    """Deterministic ordering; ties always break on ascending id, nulls always last."""
    ordered = _by_id(records)
    descending = order == "desc"
    if sort_key == "id":
        return list(reversed(ordered)) if descending else ordered
    if sort_key == "name":
        return sorted(ordered, key=lambda r: str(r.get("name", "")).lower(), reverse=descending)

    field = "stave_width_mm" if sort_key in ("stave-width", "stave-band") else "pigment_wear"
    present = [record for record in ordered if record.get(field) is not None]
    missing = [record for record in ordered if record.get(field) is None]
    if sort_key == "stave-band":
        key: Callable[[dict[str, Any]], Any] = lambda r: (
            BAND_NAMES.index(band_for(r.get("stave_width_mm"))),
            Decimal(str(r.get("stave_width_mm"))),
        )
    elif sort_key == "stave-width":
        key = lambda r: Decimal(str(r.get("stave_width_mm")))
    else:
        key = lambda r: int(r.get("pigment_wear"))
    return sorted(present, key=key, reverse=descending) + missing


def filter_inscriptions(
    records: Sequence[dict[str, Any]], band: str | None, wear: str | None
) -> list[dict[str, Any]]:
    selected = list(records)
    if band is not None:
        selected = [r for r in selected if band_for(r.get("stave_width_mm")) == band]
    if wear is not None:
        if wear.strip().lower() == "unset":
            selected = [r for r in selected if r.get("pigment_wear") is None]
        else:
            wanted = parse_pigment_wear(wear)
            selected = [r for r in selected if r.get("pigment_wear") == wanted]
    return selected


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def cmd_auth_init(args: argparse.Namespace, style: Style) -> int:
    path = resolve_catalog_path(args.file)
    catalog = load_catalog(path)
    if catalog is not None and catalog.get("operator_hash") and not args.force:
        raise Failure(
            f"catalog {path} is already initialized for operator "
            f"'{operator_name(catalog)}': use --force to set a new passphrase"
        )
    passphrase = read_new_passphrase()
    if catalog is None:
        catalog = empty_catalog(passphrase)
    else:
        salt = secrets.token_bytes(SALT_BYTES)
        catalog["operator_salt"] = salt.hex()
        catalog["operator_hash"] = hash_passphrase(passphrase, salt, PBKDF2_ITERATIONS)
        catalog["operator_iterations"] = PBKDF2_ITERATIONS
        catalog["operator_name"] = os.environ.get(OPERATOR_ENV) or operator_name(catalog)
    save_catalog(path, catalog)
    emit(style.accent(f"initialized catalog {path}") + f" for operator '{operator_name(catalog)}'")
    emit(f"passphrase stored as PBKDF2-{PBKDF2_ALGORITHM.upper()} ({PBKDF2_ITERATIONS} iterations) + random salt")
    return EXIT_OK


def cmd_auth_status(args: argparse.Namespace, style: Style) -> int:
    path = resolve_catalog_path(args.file)
    catalog = load_catalog(path)
    if catalog is None:
        emit(f"uninitialized: no catalog at {path}")
        emit(f"create it with: {PROG} --file {path} auth init")
        emit(f"demo account: {DEFAULT_OPERATOR} / {DEMO_PASSPHRASE} "
             f"(export {PASSPHRASE_ENV}={DEMO_PASSPHRASE})")
        return EXIT_OK
    if not catalog.get("operator_hash"):
        emit(f"uninitialized: catalog {path} has no operator passphrase")
        emit(f"set one with: {PROG} --file {path} auth init")
        emit(f"demo account: {DEFAULT_OPERATOR} / {DEMO_PASSPHRASE} "
             f"(export {PASSPHRASE_ENV}={DEMO_PASSPHRASE})")
        return EXIT_OK
    count = len(catalog["inscriptions"])
    noun = "inscription" if count == 1 else "inscriptions"
    emit(style.accent("initialized") + f": operator '{operator_name(catalog)}', {count} {noun}")
    emit(f"file: {path}")
    return EXIT_OK


def _resolve_name_argument(args: argparse.Namespace) -> str:
    """--name, or one interactive prompt when the terminal allows it."""
    raw = getattr(args, "name", None)
    if raw is not None:
        return raw
    if not sys.stdin.isatty():
        raise UsageError("name is required")
    try:
        return input("Name: ")
    except EOFError as exc:
        raise UsageError("name is required") from exc


def _apply_field_arguments(args: argparse.Namespace, values: dict[str, Any]) -> dict[str, Any]:
    """Map only the field flags that were actually supplied (no other key can arrive)."""
    if getattr(args, "name", None) is not None:
        values["name"] = clean_text(args.name, "name", NAME_MAX, required=True)
    if getattr(args, "site", None) is not None:
        values["site"] = clean_text(args.site, "site", SITE_MAX)
    if getattr(args, "period", None) is not None:
        values["period"] = clean_text(args.period, "period", PERIOD_MAX)
    if getattr(args, "notes", None) is not None:
        values["notes"] = clean_text(args.notes, "notes", NOTES_MAX)
    if getattr(args, "stave_width", None) is not None:
        values["stave_width_mm"] = parse_stave_width(args.stave_width)
    if getattr(args, "wear", None) is not None:
        values["pigment_wear"] = parse_pigment_wear(args.wear)
    return values


def build_record(catalog: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    stamp = now_iso()
    return {
        "id": int(catalog["next_seq"]),
        "name": values["name"],
        "site": values.get("site", ""),
        "period": values.get("period", ""),
        "stave_width_mm": values.get("stave_width_mm"),
        "pigment_wear": values.get("pigment_wear"),
        "notes": values.get("notes", ""),
        "created_at": stamp,
        "updated_at": stamp,
    }


def cmd_add(args: argparse.Namespace, style: Style) -> int:
    path = resolve_catalog_path(args.file)
    catalog = require_catalog(path)
    authenticate(catalog, path, "add")
    args.name = _resolve_name_argument(args)
    values = _apply_field_arguments(args, {})
    record = build_record(catalog, values)
    catalog["inscriptions"].append(record)
    catalog["next_seq"] = record["id"] + 1
    save_catalog(path, catalog)
    emit(style.accent(f"added inscription {record['id']}") + f": {record['name']}")
    emit(
        f"  band: {band_for(record['stave_width_mm'])}"
        f"  wear: {wear_label(record['pigment_wear'])}"
    )
    return EXIT_OK


def cmd_list(args: argparse.Namespace, style: Style) -> int:
    path = resolve_catalog_path(args.file)
    catalog = require_catalog(path)
    authenticate(catalog, path, "list")
    records = catalog["inscriptions"]
    selected = filter_inscriptions(records, args.band, args.wear)
    selected = sort_inscriptions(selected, args.sort, args.order)

    if not records:
        emit("No inscriptions yet. Add one with: runetally add")
        return EXIT_OK
    if not selected:
        filters = []
        if args.band is not None:
            filters.append(f"--band {args.band}")
        if args.wear is not None:
            filters.append(f"--wear {args.wear}")
        total = len(records)
        noun = "inscription" if total == 1 else "inscriptions"
        emit(f"No inscriptions match {' '.join(filters)} - all {total} {noun} are hidden by filters.")
        emit("Clear the filters to see the whole catalog.")
        return EXIT_OK

    emit(style.accent(table_header()))
    emit(style.dim("-" * MAX_ROW_WIDTH))
    for record in selected:
        emit(format_record_row(record))
    noun = "inscription" if len(selected) == 1 else "inscriptions"
    emit(style.dim(f"{len(selected)} {noun}"))
    band_line, wear_line = table_legend()
    emit(style.dim(band_line))
    emit(style.dim(wear_line))
    return EXIT_OK


def find_record(catalog: dict[str, Any], inscription_id: int) -> dict[str, Any]:
    for record in catalog["inscriptions"]:
        if int(record.get("id", -1)) == inscription_id:
            return record
    raise UsageError(f"no inscription with id {inscription_id}")


def cmd_show(args: argparse.Namespace, style: Style) -> int:
    path = resolve_catalog_path(args.file)
    catalog = require_catalog(path)
    authenticate(catalog, path, "show")
    record = find_record(catalog, args.id)
    width_text = record.get("stave_width_mm")
    wear_value = record.get("pigment_wear")
    if width_text is not None:
        stave_row = f"{stave_display(width_text)} mm ({band_for(width_text)})"
    else:
        stave_row = "- (unset)"
    if wear_value is not None:
        wear_row = f"{wear_value} ({wear_label(wear_value)})"
    else:
        wear_row = "- (unset)"
    rows = (
        ("id", str(record["id"])),
        ("name", record.get("name") or "-"),
        ("site", record.get("site") or "-"),
        ("period", record.get("period") or "-"),
        ("stave width", stave_row),
        ("pigment wear", wear_row),
        ("notes", record.get("notes") or "-"),
        ("created at", record.get("created_at") or "-"),
        ("updated at", record.get("updated_at") or "-"),
    )
    emit(style.accent(f"inscription {record['id']}"))
    for label, value in rows:
        emit(f"  {label:<12} : {value}")
    return EXIT_OK


def cmd_edit(args: argparse.Namespace, style: Style) -> int:
    path = resolve_catalog_path(args.file)
    catalog = require_catalog(path)
    authenticate(catalog, path, "edit")
    record = find_record(catalog, args.id)
    updates = _apply_field_arguments(args, {})
    if not updates:
        raise UsageError(
            "no fields to update: pass --name, --site, --period, --stave-width, --wear or --notes"
        )
    record.update(updates)
    record["updated_at"] = now_iso()
    save_catalog(path, catalog)
    label_list = ", ".join(FIELD_LABELS[name] for name in sorted(updates))
    emit(style.accent(f"updated inscription {record['id']}") + f": {label_list}")
    return EXIT_OK


def cmd_delete(args: argparse.Namespace, style: Style) -> int:
    path = resolve_catalog_path(args.file)
    catalog = require_catalog(path)
    authenticate(catalog, path, "delete")
    record = find_record(catalog, args.id)
    if not args.yes:
        raise UsageError(
            f"refusing to delete inscription {record['id']} without --yes "
            f'(re-run with --yes to delete "{record.get("name", "")}")'
        )
    catalog["inscriptions"] = [
        stored for stored in catalog["inscriptions"] if int(stored.get("id", -1)) != args.id
    ]
    save_catalog(path, catalog)
    emit(style.accent(f"deleted inscription {record['id']}") + f": {record.get('name', '')}")
    emit(f"next id to assign: {catalog['next_seq']} (ids are never reused)")
    return EXIT_OK


def read_tsv_rows(tsv_path: Path) -> list[str]:
    try:
        with tsv_path.open("r", encoding="utf-8-sig") as handle:
            return handle.read().splitlines()
    except FileNotFoundError as exc:
        raise Failure(f"cannot read TSV file {tsv_path}: no such file") from exc
    except IsADirectoryError as exc:
        raise Failure(f"cannot read TSV file {tsv_path}: is a directory") from exc
    except UnicodeDecodeError as exc:
        raise Failure(f"cannot read TSV file {tsv_path}: not valid UTF-8") from exc
    except OSError as exc:
        raise Failure(f"cannot read TSV file {tsv_path}: {exc.strerror or exc}") from exc


def parse_tsv_header(line: str) -> list[str]:
    columns = [cell.strip().lstrip("\ufeff") for cell in line.split("\t")]
    missing = [name for name in IMPORT_COLUMNS if name not in columns]
    if missing:
        raise UsageError(
            "invalid TSV header: expected "
            + ", ".join(IMPORT_COLUMNS)
            + f" (missing {', '.join(missing)})"
        )
    return columns


def validate_import_row(cells: Sequence[str], columns: Sequence[str]) -> dict[str, Any]:
    row = dict(zip(columns, cells))
    return {
        "name": clean_text(row.get("name"), "name", NAME_MAX, required=True),
        "site": clean_text(row.get("site"), "site", SITE_MAX),
        "period": clean_text(row.get("period"), "period", PERIOD_MAX),
        "notes": clean_text(row.get("notes"), "notes", NOTES_MAX),
        "stave_width_mm": parse_stave_width(row.get("stave_width_mm")),
        "pigment_wear": parse_pigment_wear(row.get("pigment_wear")),
    }


def cmd_import(args: argparse.Namespace, style: Style) -> int:
    path = resolve_catalog_path(args.file)
    catalog = require_catalog(path)
    authenticate(catalog, path, "import")
    tsv_path = Path(args.tsv).expanduser()
    lines = read_tsv_rows(tsv_path)

    header_index = next((index for index, line in enumerate(lines) if line.strip()), None)
    if header_index is None:
        raise UsageError(
            f"TSV file {tsv_path} is empty: expected a header line of " + ", ".join(IMPORT_COLUMNS)
        )
    columns = parse_tsv_header(lines[header_index])

    imported = 0
    skipped: list[tuple[int, str]] = []
    mappings: list[tuple[str, int]] = []
    for line_number, line in enumerate(lines[header_index + 1 :], start=header_index + 2):
        if not line.strip():
            continue
        cells = line.split("\t")
        if len(cells) != len(columns):
            skipped.append((line_number, f"expected {len(columns)} columns, got {len(cells)}"))
            continue
        try:
            values = validate_import_row(cells, columns)
        except CliError as exc:
            skipped.append((line_number, str(exc)))
            continue
        record = build_record(catalog, values)
        catalog["inscriptions"].append(record)
        catalog["next_seq"] = record["id"] + 1
        imported += 1
        source_id = (dict(zip(columns, cells)).get("source_id") or "").strip()
        if source_id:
            mappings.append((source_id, record["id"]))

    if imported:
        save_catalog(path, catalog)

    emit(style.accent(f"imported {imported}, skipped {len(skipped)}"))
    for line_number, reason in skipped:
        emit(f"  skipped line {line_number}: {reason}")
    for source_id, new_id in mappings:
        emit(f"  source_id {source_id} -> id {new_id}")
    if imported == 0 and not skipped:
        emit("  nothing to import: the file has a header but no rows")
    return EXIT_OK


def cmd_bands(args: argparse.Namespace, style: Style) -> int:
    emit(style.accent(BAND_RUBRIC[0]))
    for line in BAND_RUBRIC[1:]:
        emit(line)
    emit("")
    emit(style.accent(WEAR_SCALE[0]))
    for line in WEAR_SCALE[1:]:
        emit(line)
    emit("")
    emit(f"filter with: {PROG} list --band narrow   |   {PROG} list --wear 4")
    emit(f"sort with:   {PROG} list --sort stave-band --order asc")
    return EXIT_OK


# --------------------------------------------------------------------------
# Argument parsing and entry point
# --------------------------------------------------------------------------


class CliParser(argparse.ArgumentParser):
    """Argument parser that reports usage problems as one-line errors (exit code 2)."""

    def error(self, message: str) -> None:  # type: ignore[override]
        raise UsageError(one_line_usage(message))


def one_line_usage(message: str) -> str:
    match = re.search(r"invalid choice: '([^']+)'", message)
    if match:
        return f"unknown command '{match.group(1)}' (run '{PROG} --help' for usage)"
    return f"{message} (run '{PROG} --help' for usage)"


def add_field_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--name", metavar="TEXT", help="inscription name (1-120 characters)")
    parser.add_argument("--site", metavar="TEXT", help="find site (<= 80 characters)")
    parser.add_argument("--period", metavar="TEXT", help="dating period (<= 40 characters)")
    parser.add_argument(
        "--stave-width",
        metavar="MM",
        help="stave width in mm, positive decimal; 'null'/'unset'/'none' clears it",
    )
    parser.add_argument(
        "--wear",
        metavar="N",
        help="pigment wear 0-5 (0 pristine .. 5 lost); 'null'/'unset'/'none' clears it",
    )
    parser.add_argument("--notes", metavar="TEXT", help="free-form notes (<= 500 characters)")


def build_parser() -> CliParser:
    parser = CliParser(
        prog=PROG,
        description=(
            "RuneTally2 - catalog runic inscriptions, sort by stave width, score pigment wear.\n"
            "Offline, one JSON catalog, guarded by a local operator passphrase."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--file",
        metavar="PATH",
        default=None,
        help=f"catalog JSON file (default: ./{DEFAULT_FILE}; env {FILE_ENV})",
    )
    parser.add_argument("--no-color", action="store_true", help="never emit ANSI colour")
    parser.add_argument("--version", action="version", version=f"RuneTally2 {VERSION}")

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    # The two global flags are accepted after the subcommand too. SUPPRESS keeps
    # a value given before the subcommand from being overwritten by a default.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--file", metavar="PATH", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument(
        "--no-color", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS
    )

    auth = subparsers.add_parser(
        "auth",
        parents=[common],
        help="create or inspect the operator passphrase",
        description="Local operator authentication for this catalog file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    auth_sub = auth.add_subparsers(dest="auth_command", metavar="<action>")
    auth_init = auth_sub.add_parser(
        "init",
        parents=[common],
        help="create the catalog and set the operator passphrase",
        description=(
            "Create the catalog file (if missing) and store a PBKDF2-HMAC-SHA256 hash plus a "
            f"random salt. Passphrase from {PASSPHRASE_ENV} or an interactive prompt."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            f"demo passphrase: {DEMO_PASSPHRASE}   "
            f"(example: {PASSPHRASE_ENV}={DEMO_PASSPHRASE} {PROG} auth init)"
        ),
    )
    auth_init.add_argument(
        "--force", action="store_true", help="replace an existing passphrase (keeps inscriptions)"
    )
    auth_init.set_defaults(handler=cmd_auth_init)
    auth_status = auth_sub.add_parser(
        "status",
        parents=[common],
        help="report initialized or uninitialized",
        description="Report whether the catalog file exists and has an operator passphrase.",
    )
    auth_status.set_defaults(handler=cmd_auth_status)

    add = subparsers.add_parser(
        "add",
        parents=[common],
        help="add one inscription",
        description="Add one inscription; the id comes from next_seq and is never reused.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_field_arguments(add)
    add.set_defaults(handler=cmd_add)

    list_parser = subparsers.add_parser(
        "list",
        parents=[common],
        help="print the fixed-width catalog table",
        description=(
            "Print the catalog table (NAME 32, SITE 20, PERIOD 20 characters).\n"
            "Every row stays within 100 columns; full values are available in show."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    list_parser.add_argument(
        "--sort", choices=SORT_KEYS, default="id", help="sort key (default: id)"
    )
    list_parser.add_argument(
        "--order", choices=ORDER_KEYS, default="asc", help="sort direction (default: asc)"
    )
    list_parser.add_argument(
        "--band", choices=BAND_NAMES, default=None, help="only show this stave band"
    )
    list_parser.add_argument(
        "--wear", metavar="N", default=None, help="only show this pigment wear score (0-5 or 'unset')"
    )
    list_parser.set_defaults(handler=cmd_list)

    show = subparsers.add_parser(
        "show",
        parents=[common],
        help="print one inscription with full fields",
        description="Print every stored field of one inscription, untruncated, with band and wear labels.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    show.add_argument("id", type=int, metavar="ID", help="catalog id")
    show.set_defaults(handler=cmd_show)

    edit = subparsers.add_parser(
        "edit",
        parents=[common],
        help="update named fields of one inscription",
        description=(
            "Update only the fields you pass; every value is validated exactly like add.\n"
            "Pass --stave-width null or --wear null to clear a value."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    edit.add_argument("id", type=int, metavar="ID", help="catalog id")
    add_field_arguments(edit)
    edit.set_defaults(handler=cmd_edit)

    delete = subparsers.add_parser(
        "delete",
        parents=[common],
        help="delete one inscription (requires --yes)",
        description="Delete one inscription. Ids are never reused: next_seq keeps counting.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    delete.add_argument("id", type=int, metavar="ID", help="catalog id")
    delete.add_argument("--yes", action="store_true", help="confirm the deletion")
    delete.set_defaults(handler=cmd_delete)

    import_parser = subparsers.add_parser(
        "import",
        parents=[common],
        help="import inscriptions from a TSV file",
        description=(
            "Import a TSV file. Every imported row gets a fresh id from next_seq -\n"
            "source ids are never preserved and are reported as 'source_id X -> id N'."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "TSV header (any column order):\n  "
            + "\t".join(IMPORT_COLUMNS)
            + "\n\nInvalid rows are skipped with a reason; valid rows are saved atomically."
        ),
    )
    import_parser.add_argument("tsv", metavar="TSV", help="path to the TSV file")
    import_parser.set_defaults(handler=cmd_import)

    bands = subparsers.add_parser(
        "bands",
        parents=[common],
        help="print the stave band rubric and the wear scale",
        description="Print the complete stave width band rubric and the pigment wear scale.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    bands.set_defaults(handler=cmd_bands)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if getattr(args, "command", None) is None:
            parser.print_help()
            return EXIT_OK
        if args.command == "auth" and getattr(args, "auth_command", None) is None:
            parser.parse_args(["auth", "--help"])
            return EXIT_OK
        handler = getattr(args, "handler", None)
        if handler is None:
            parser.print_help()
            return EXIT_USAGE
        style = Style(enabled=colour_enabled(bool(getattr(args, "no_color", False))))
        return int(handler(args, style))
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except OSError:
            pass
        return EXIT_OK
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return 130
    except OSError as exc:
        print(f"error: {exc.strerror or exc}", file=sys.stderr)
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
