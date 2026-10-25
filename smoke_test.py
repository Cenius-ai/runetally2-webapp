#!/usr/bin/env python3
"""RuneTally2 smoke test - standard library only.

Exercises the primary flow through the real CLI entry point in a throwaway
directory: auth init, add, list, sort, score, import, edit and delete, plus one
wrong-passphrase negative check. Prints one line per check and ends with
``SMOKE OK`` and exit code 0.

Run it as:  python3 smoke_test.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

CLI = Path(__file__).resolve().parent / "runetally.py"
# A distinctive value so "is the passphrase stored anywhere in the file?" is a real check.
PASSPHRASE = "smoke-pass-9f3a"
CHECKS = 0
FAILURES: list[str] = []


def record(label: str, problems: list[str], output: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if problems:
        FAILURES.append(f"{label}: {', '.join(problems)}\n{output}")
        print(f"FAIL {label}")
    else:
        print(f"ok   {label}")


def run(
    catalog: Path,
    *arguments: str,
    passphrase: str | None = PASSPHRASE,
    expect: int = 0,
    contains: tuple[str, ...] = (),
    absent: tuple[str, ...] = (),
    label: str = "",
) -> str:
    """Run one CLI command and assert its exit code plus output substrings."""
    command = [sys.executable, str(CLI), "--file", str(catalog), *arguments]
    env = dict(os.environ)
    env.pop("RUNETALLY_PASSPHRASE", None)
    if passphrase is not None:
        env["RUNETALLY_PASSPHRASE"] = passphrase
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", env=env, check=False
    )
    output = completed.stdout + completed.stderr
    problems: list[str] = []
    if completed.returncode != expect:
        problems.append(f"expected exit {expect}, got {completed.returncode}")
    problems.extend(f"missing {needle!r}" for needle in contains if needle not in output)
    problems.extend(f"unexpected {needle!r}" for needle in absent if needle in output)
    if "Traceback (most recent call last)" in output:
        problems.append("printed a traceback")
    record(label or " ".join(arguments[:2]), problems, output)
    return output


def check(condition: bool, label: str) -> None:
    record(label, [] if condition else ["condition not met"])


def write_tsv(path: Path) -> None:
    path.write_text(
        "source_id\tname\tsite\tperiod\tstave_width_mm\tpigment_wear\tnotes\n"
        "S-7\tJelling stone\tJutland\tViking Age (c. 965)\t11.5\t3\tRaised runes\n"
        "S-8\tStentoften stone\tBlekinge\tMigration Period\t6.2\t1\t\n"
        "S-9\tBad wear row\tJutland\tViking Age\t5.0\t9\tthis row must be skipped\n"
        "bad-row-with-two-columns\tBroken\n"
        "S-11\tKuli stone\tMøre og Romsdal\tViking Age\t3.5\t4\t\n",
        encoding="utf-8",
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="runetally-smoke-") as workdir:
        catalog = Path(workdir) / "catalog.json"
        tsv = Path(workdir) / "rows.tsv"
        write_tsv(tsv)

        run(catalog, "--help", label="help", contains=("add", "list", "bands", "import"))
        run(catalog, "auth", "status", contains=("uninitialized",), label="auth status (fresh)")
        run(catalog, "auth", "init", contains=("initialized", "PBKDF2-SHA256"), label="auth init")
        run(catalog, "auth", "status", contains=("initialized", "operator 'cenius'"), label="auth status (initialized)")
        run(catalog, "bands", contains=("narrow   width <= 4.0", "4.0 < width <= 8.0", "width > 8.0"), label="bands rubric")
        run(catalog, "list", contains=("No inscriptions yet",), label="list (empty)")

        stored = catalog.read_text(encoding="utf-8")
        check(PASSPHRASE not in stored, "catalog stores no plaintext passphrase")

        run(
            catalog,
            "add",
            "--name",
            "Broby stone",
            "--site",
            "Broby",
            "--period",
            "Viking Age",
            "--stave-width",
            "3.2",
            "--wear",
            "4",
            contains=("added inscription 1", "narrow", "heavy"),
            label="add id 1",
        )
        run(
            catalog,
            "add",
            "--name",
            "Rök runestone",
            "--stave-width",
            "8.01",
            "--wear",
            "5",
            contains=("added inscription 2", "broad", "lost"),
            label="add id 2 (score 5 = lost)",
        )
        run(
            catalog,
            "add",
            "--name",
            "Kylver stone",
            "--stave-width",
            "4.05",
            contains=("added inscription 3", "medium"),
            label="add id 3 (4.05 mm = medium)",
        )
        run(catalog, "add", "--name", "Bad", "--wear", "6", expect=2, contains=("pigment wear must be 0-5",), label="reject wear 6")
        run(catalog, "add", expect=2, contains=("name is required",), label="reject empty name")
        run(catalog, "list", contains=("Broby stone", "3.2 nar", "5 los", "3 inscriptions"), label="list (populated)")
        run(catalog, "list", "--sort", "stave-band", contains=("3.2 nar", "4.05 med", "8.01 bro"), label="sort stave-band")
        run(catalog, "list", "--sort", "stave-width", "--order", "desc", contains=("8.01 bro",), label="sort stave-width desc")
        run(catalog, "list", "--band", "broad", contains=("Rök runestone",), absent=("Kylver stone",), label="filter band broad")
        run(catalog, "list", "--wear", "5", contains=("Rök runestone", "los"), label="filter wear 5")
        run(catalog, "show", "1", contains=("stave width", "3.2 mm (narrow)", "4 (heavy)"), label="show id 1")
        run(catalog, "edit", "1", "--wear", "0", contains=("updated inscription 1",), label="edit id 1")
        run(catalog, "show", "1", contains=("0 (pristine)",), label="score 0 = pristine")
        run(
            catalog,
            "import",
            str(tsv),
            contains=("imported 3, skipped 2", "source_id S-7 -> id 4", "skipped line 5"),
            label="import TSV (reminted ids)",
        )
        run(catalog, "add", "--name", "Post import", contains=("added inscription 7",), label="add after import")
        run(catalog, "delete", "1", "--yes", contains=("deleted inscription 1",), label="delete id 1")
        run(catalog, "show", "1", expect=2, contains=("no inscription with id 1",), label="show deleted id")
        run(catalog, "show", "999", expect=2, contains=("no inscription with id 999",), label="unknown id")
        run(
            catalog,
            "add",
            "--name",
            "Denied",
            passphrase="wrong-passphrase",
            expect=1,
            contains=("authentication failed",),
            label="wrong passphrase (negative)",
        )
        run(catalog, "list", absent=("Denied",), label="wrong passphrase saved nothing")

    if FAILURES:
        print("\nSMOKE FAILED")
        for failure in FAILURES:
            print(f"- {failure}")
        return 1
    print(f"\nSMOKE OK ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
