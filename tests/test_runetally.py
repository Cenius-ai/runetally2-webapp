"""RuneTally2 test suite.

Runs the real CLI through subprocess against throwaway catalogs, so the tests
cover exactly what an operator (and the smoke test) exercises. Standard library
only: ``python3 -m unittest`` and ``pytest`` both collect this file.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "runetally.py"
PASSPHRASE = "test-operator-passphrase"

sys.path.insert(0, str(ROOT))


class CliTestCase(unittest.TestCase):
    """Base case: a private temp directory plus a helper that shells out to the CLI."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="runetally-test-")
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.catalog = self.dir / "catalog.json"

    def cli(
        self,
        *arguments: str,
        passphrase: str | None = PASSPHRASE,
        expect: int | None = 0,
        env_extra: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env.pop("RUNETALLY_PASSPHRASE", None)
        env.pop("RUNETALLY_FILE", None)
        env["NO_COLOR"] = "1"
        if passphrase is not None:
            env["RUNETALLY_PASSPHRASE"] = passphrase
        if env_extra:
            env.update(env_extra)
        completed = subprocess.run(
            [sys.executable, str(CLI), "--file", str(self.catalog), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            check=False,
            timeout=120,
        )
        combined = completed.stdout + completed.stderr
        self.assertNotIn("Traceback (most recent call last)", combined)
        if expect is not None:
            self.assertEqual(completed.returncode, expect, msg=f"{arguments}: {combined}")
        return completed

    def out(self, *arguments: str, **kwargs: object) -> str:
        return self.cli(*arguments, **kwargs).stdout

    def init_catalog(self) -> None:
        self.cli("auth", "init")

    def add(self, name: str, **fields: str) -> int:
        arguments = ["add", "--name", name]
        for key, value in fields.items():
            arguments += [f"--{key.replace('_', '-')}", value]
        output = self.out(*arguments)
        self.assertIn("added inscription", output)
        return int(output.split("added inscription ")[1].split(":")[0].strip())

    def listed_ids(self, *arguments: str) -> list[int]:
        ids: list[int] = []
        for line in self.out("list", *arguments).splitlines():
            head = line[:5].strip()
            if head.isdigit():
                ids.append(int(head))
        return ids

    def read_catalog(self) -> dict:
        return json.loads(self.catalog.read_text(encoding="utf-8"))


class HelpAndExitCodeTests(CliTestCase):
    def test_help_lists_every_subcommand(self) -> None:
        output = self.out("--help")
        self.assertIn("usage: runetally", output)
        for command in ("auth", "add", "list", "show", "edit", "delete", "import", "bands"):
            self.assertIn(command, output)
        self.assertEqual(self.cli("--help").returncode, 0)

    def test_help_states_the_exact_band_rubric(self) -> None:
        output = self.out("--help")
        self.assertIn("narrow   width <= 4.0", output)
        self.assertIn("medium   4.0 < width <= 8.0", output)
        self.assertIn("broad    width > 8.0", output)
        self.assertIn("unset    no width recorded (null)", output)

    def test_unknown_command_exits_two_with_one_line(self) -> None:
        completed = self.cli("wat", expect=2)
        self.assertEqual(completed.stdout, "")
        lines = [line for line in completed.stderr.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, msg=completed.stderr)
        self.assertIn("unknown command 'wat'", lines[0])

    def test_unknown_flag_exits_two(self) -> None:
        completed = self.cli("list", "--nope", expect=2)
        self.assertIn("error:", completed.stderr)

    def test_bare_invocation_prints_help_and_exits_zero(self) -> None:
        completed = self.cli(expect=0)
        self.assertIn("usage: runetally", completed.stdout)

    def test_version(self) -> None:
        self.assertIn("RuneTally2", self.out("--version"))

    def test_bands_command_prints_rubric_and_wear_scale(self) -> None:
        output = self.out("bands", passphrase=None)
        for needle in (
            "narrow   width <= 4.0",
            "medium   4.0 < width <= 8.0",
            "broad    width > 8.0",
            "unset    no width recorded (null)",
            "0  pristine",
            "5  lost",
        ):
            self.assertIn(needle, output)


class AuthTests(CliTestCase):
    def test_init_creates_schema_without_plaintext(self) -> None:
        self.cli("auth", "status", passphrase=None, expect=0)
        self.init_catalog()
        catalog = self.read_catalog()
        self.assertEqual(catalog["schema_version"], 1)
        self.assertEqual(catalog["next_seq"], 1)
        self.assertEqual(catalog["inscriptions"], [])
        self.assertRegex(catalog["operator_hash"], r"^[0-9a-f]{64}$")
        self.assertRegex(catalog["operator_salt"], r"^[0-9a-f]{32}$")
        self.assertNotIn(PASSPHRASE, self.catalog.read_text(encoding="utf-8"))

    def test_status_reports_initialized(self) -> None:
        self.init_catalog()
        output = self.out("auth", "status", passphrase=None)
        self.assertIn("initialized", output)
        self.assertIn("cenius", output)
        self.assertIn(str(self.catalog), output)

    def test_status_reports_uninitialized(self) -> None:
        output = self.out("auth", "status", passphrase=None)
        self.assertIn("uninitialized", output)
        self.assertIn("auth init", output)

    def test_guard_commands_need_an_initialized_catalog(self) -> None:
        completed = self.cli("list", expect=1)
        self.assertIn("no catalog", completed.stderr)

    def test_wrong_passphrase_exits_one_and_saves_nothing(self) -> None:
        self.init_catalog()
        completed = self.cli("add", "--name", "Denied", passphrase="nope", expect=1)
        self.assertIn("authentication failed", completed.stderr)
        self.assertEqual(self.read_catalog()["inscriptions"], [])

    def test_missing_passphrase_is_a_one_line_failure(self) -> None:
        self.init_catalog()
        completed = self.cli("list", passphrase=None, env_extra={"PATH": os.environ.get("PATH", "")},
                             expect=1)
        self.assertIn("RUNETALLY_PASSPHRASE", completed.stderr)

    def test_init_is_not_silently_repeated(self) -> None:
        self.init_catalog()
        completed = self.cli("auth", "init", expect=1)
        self.assertIn("already initialized", completed.stderr)

    def test_force_resets_the_passphrase_and_keeps_records(self) -> None:
        self.init_catalog()
        self.add("Broby stone")
        self.cli("auth", "init", "--force", passphrase="second-passphrase")
        self.assertEqual(len(self.read_catalog()["inscriptions"]), 1)
        self.cli("list", passphrase="second-passphrase")
        completed = self.cli("list", passphrase=PASSPHRASE, expect=1)
        self.assertIn("authentication failed", completed.stderr)


class AddValidationTests(CliTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_catalog()

    def test_add_assigns_ids_from_next_seq(self) -> None:
        self.assertEqual(self.add("Stone A", site="Broby", period="Viking Age",
                                  stave_width="3.2", wear="4"), 1)
        self.assertEqual(self.add("Stone B", wear="0"), 2)
        self.assertEqual(self.read_catalog()["next_seq"], 3)

    def test_empty_name_rejected(self) -> None:
        completed = self.cli("add", "--name", "   ", expect=2)
        self.assertIn("name is required", completed.stderr)
        self.assertEqual(self.read_catalog()["inscriptions"], [])

    def test_missing_name_rejected_when_not_interactive(self) -> None:
        completed = self.cli("add", "--wear", "2", expect=2)
        self.assertIn("name is required", completed.stderr)

    def test_over_long_fields_rejected(self) -> None:
        cases = (
            (("--name", "x" * 121), "name must be at most 120 characters"),
            (("--name", "ok", "--site", "s" * 81), "site must be at most 80 characters"),
            (("--name", "ok", "--period", "p" * 41), "period must be at most 40 characters"),
            (("--name", "ok", "--notes", "n" * 501), "notes must be at most 500 characters"),
        )
        for arguments, message in cases:
            with self.subTest(message=message):
                completed = self.cli("add", *arguments, expect=2)
                self.assertIn(message, completed.stderr)

    def test_wear_out_of_range_rejected(self) -> None:
        for value in ("6", "-1"):
            with self.subTest(value=value):
                completed = self.cli("add", "--name", "Bad", "--wear", value, expect=2)
                self.assertIn("pigment wear must be 0-5", completed.stderr)

    def test_non_numeric_wear_rejected(self) -> None:
        completed = self.cli("add", "--name", "Bad", "--wear", "heavy", expect=2)
        self.assertIn("pigment wear must be 0-5", completed.stderr)

    def test_stave_width_must_be_positive_decimal(self) -> None:
        for value in ("0", "-2", "abc"):
            with self.subTest(value=value):
                completed = self.cli("add", "--name", "Bad", "--stave-width", value, expect=2)
                self.assertIn("stave width must be a positive number", completed.stderr)


class ListTableTests(CliTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_catalog()

    def test_empty_catalog_message(self) -> None:
        output = self.out("list")
        self.assertIn("No inscriptions yet. Add one with: runetally add", output)

    def test_rows_never_exceed_one_hundred_columns(self) -> None:
        self.add("A" * 60, site="S" * 40, period="P" * 30)
        for line in self.out("list").splitlines():
            with self.subTest(line=line[:20]):
                self.assertLessEqual(len(line), 100)

    def test_long_values_are_truncated_with_ellipsis(self) -> None:
        self.add("A" * 60, site="S" * 40, period="P" * 30)
        row = [line for line in self.out("list").splitlines() if line[:5].strip() == "1"][0]
        self.assertIn("A" * 29 + "...", row)
        self.assertIn("S" * 17 + "...", row)
        self.assertIn("P" * 17 + "...", row)

    def test_header_matches_column_widths(self) -> None:
        self.add("Broby stone")
        header = self.out("list").splitlines()[0]
        self.assertEqual(len(header), 100)
        self.assertEqual(header, "   ID  NAME                              SITE                "
                                 "  PERIOD                   STAVE  WEAR ")

    def test_filtered_empty_invites_clearing_filters(self) -> None:
        self.add("Broby stone", stave_width="3.2")
        output = self.out("list", "--band", "broad")
        self.assertIn("No inscriptions match --band broad", output)
        self.assertIn("Clear the filters", output)

    def test_band_and_wear_filters_select_rows(self) -> None:
        self.add("Narrow stone", stave_width="3.2", wear="4")
        self.add("Broad stone", stave_width="11.5", wear="0")
        self.assertEqual(len(self.listed_ids("--band", "narrow")), 1)
        self.assertEqual(len(self.listed_ids("--band", "broad")), 1)
        self.assertEqual(self.listed_ids("--wear", "0"), [2])
        self.assertEqual(self.listed_ids("--band", "narrow", "--wear", "4"), [1])


class ShowTests(CliTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_catalog()

    def test_show_prints_full_untruncated_fields(self) -> None:
        name = "Rök runestone " + "x" * 40
        site = "Östergötland, Sweden " + "y" * 30
        self.add(name, site=site, stave_width="11.5", wear="3")
        output = self.out("show", "1")
        self.assertIn(name, output)
        self.assertIn(site, output)
        self.assertIn("11.5 mm (broad)", output)
        self.assertIn("3 (moderate)", output)
        self.assertNotIn("...", output)

    def test_show_reports_unset_width_and_wear(self) -> None:
        self.add("Einang stone")
        output = self.out("show", "1")
        self.assertIn("- (unset)", output)

    def test_unknown_id_exits_two(self) -> None:
        completed = self.cli("show", "999", expect=2)
        self.assertIn("no inscription with id 999", completed.stderr)


class EditAndDeleteTests(CliTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_catalog()
        self.add("Broby stone", site="Broby", stave_width="3.2", wear="4")

    def test_edit_changes_only_named_fields(self) -> None:
        self.cli("edit", "1", "--name", "New Name")
        output = self.out("show", "1")
        self.assertIn("New Name", output)
        self.assertIn("3.2 mm (narrow)", output)
        self.assertIn("4 (heavy)", output)

    def test_edit_can_clear_width_and_wear(self) -> None:
        self.cli("edit", "1", "--stave-width", "null", "--wear", "none")
        output = self.out("show", "1")
        self.assertIn("- (unset)", output)
        self.assertIsNone(self.read_catalog()["inscriptions"][0]["stave_width_mm"])

    def test_edit_without_fields_is_a_usage_error(self) -> None:
        completed = self.cli("edit", "1", expect=2)
        self.assertIn("no fields to update", completed.stderr)

    def test_edit_validates_values(self) -> None:
        completed = self.cli("edit", "1", "--wear", "9", expect=2)
        self.assertIn("pigment wear must be 0-5", completed.stderr)

    def test_delete_requires_yes(self) -> None:
        completed = self.cli("delete", "1", expect=2)
        self.assertIn("without --yes", completed.stderr)
        self.assertEqual(len(self.read_catalog()["inscriptions"]), 1)

    def test_delete_then_next_id_is_not_reused(self) -> None:
        self.cli("delete", "1", "--yes")
        self.assertEqual(self.read_catalog()["inscriptions"], [])
        self.assertEqual(self.add("Second stone"), 2)
        self.assertEqual([record["id"] for record in self.read_catalog()["inscriptions"]], [2])


class BandAndWearTests(CliTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_catalog()

    def test_band_boundaries_are_a_total_partition(self) -> None:
        for name, width, code in (
            ("At four", "4.0", "nar"),
            ("Just over four", "4.05", "med"),
            ("At eight", "8.0", "med"),
            ("Just over eight", "8.01", "bro"),
        ):
            self.add(name, stave_width=width)
        self.add("No width")
        rows = self.out("list").splitlines()
        for expected in ("4.0 nar", "4.05 med", "8.0 med", "8.01 bro", "- uns"):
            self.assertTrue(any(expected in row for row in rows), msg=expected)

    def test_wear_labels_cover_the_scale(self) -> None:
        labels = ("pristine", "slight", "light", "moderate", "heavy", "lost")
        for score, label in enumerate(labels):
            self.add(f"Wear {score}", wear=str(score))
            self.assertIn(f"{score} ({label})", self.out("show", str(score + 1)))

    def test_unscored_wear_shows_unset(self) -> None:
        self.add("Unscored")
        self.assertIn("- (unset)", self.out("show", "1"))


class SortingTests(CliTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_catalog()
        self.add("Broad", stave_width="8.01")
        self.add("Unset")
        self.add("Narrow", stave_width="4.0")
        self.add("Low medium", stave_width="4.05")
        self.add("High medium", stave_width="8.0")

    def test_sort_by_stave_band(self) -> None:
        self.assertEqual(self.listed_ids("--sort", "stave-band"), [3, 4, 5, 1, 2])

    def test_sort_by_stave_band_descending(self) -> None:
        # broad first, medium widest-first, then narrow, unset stays last
        self.assertEqual(self.listed_ids("--sort", "stave-band", "--order", "desc"), [1, 5, 4, 3, 2])

    def test_sort_by_stave_width_puts_nulls_last_both_ways(self) -> None:
        self.assertEqual(self.listed_ids("--sort", "stave-width"), [3, 4, 5, 1, 2])
        self.assertEqual(self.listed_ids("--sort", "stave-width", "--order", "desc"), [1, 5, 4, 3, 2])

    def test_ties_break_on_ascending_id(self) -> None:
        for index in range(3):
            self.add(f"Tie {index}", stave_width="6.0")
        # 4.0 -> 4.05 -> the three 6.0 records in id order -> 8.0 -> 8.01 -> unset
        self.assertEqual(
            self.listed_ids("--sort", "stave-width"), [3, 4, 6, 7, 8, 5, 1, 2]
        )

    def test_sort_by_wear_keeps_unset_last(self) -> None:
        self.cli("edit", "1", "--wear", "5")
        self.cli("edit", "3", "--wear", "0")
        self.assertEqual(self.listed_ids("--sort", "wear")[:2], [3, 1])

    def test_sort_by_name_and_id(self) -> None:
        self.assertEqual(self.listed_ids("--sort", "name")[:2], [1, 5])
        self.assertEqual(self.listed_ids("--order", "desc"), [5, 4, 3, 2, 1])


class ImportTests(CliTestCase):
    HEADER = "source_id\tname\tsite\tperiod\tstave_width_mm\tpigment_wear\tnotes\n"

    def setUp(self) -> None:
        super().setUp()
        self.init_catalog()

    def write_tsv(self, body: str, name: str = "rows.tsv") -> Path:
        path = self.dir / name
        path.write_text(self.HEADER + body, encoding="utf-8")
        return path

    def test_import_remints_ids_and_reports_skips(self) -> None:
        path = self.write_tsv(
            "7\tJelling stone\tJutland\tViking Age\t11.5\t3\tRaised runes\n"
            "8\tBad wear\tJutland\tViking Age\t5.0\t9\tout of range\n"
            "too\tfew\tcolumns\n"
        )
        output = self.out("import", str(path))
        self.assertIn("imported 1, skipped 2", output)
        self.assertIn("skipped line 3: pigment wear must be 0-5", output)
        self.assertIn("skipped line 4: expected 7 columns, got 3", output)
        self.assertIn("source_id 7 -> id 1", output)
        catalog = self.read_catalog()
        self.assertEqual([record["id"] for record in catalog["inscriptions"]], [1])
        self.assertEqual(catalog["next_seq"], 2)

    def test_source_id_equal_to_catalog_id_is_reminted(self) -> None:
        self.add("Existing stone")
        path = self.write_tsv("1\tImported stone\tBroby\tViking Age\t3.2\t4\t\n9\tSecond import\tBroby\tViking Age\t4.0\t1\t\n")
        output = self.out("import", str(path))
        self.assertIn("imported 2, skipped 0", output)
        self.assertIn("source_id 1 -> id 2", output)
        self.assertIn("source_id 9 -> id 3", output)
        self.assertEqual(self.add("After import"), 4)
        self.assertEqual(len(self.read_catalog()["inscriptions"]), 4)

    def test_blank_source_id_still_imports_without_mapping(self) -> None:
        path = self.write_tsv("\tAnonymous fragment\tUppsala\tMedieval\t5.0\t1\t\n")
        output = self.out("import", str(path))
        self.assertIn("imported 1, skipped 0", output)
        self.assertNotIn("source_id", output)

    def test_header_only_file_imports_nothing(self) -> None:
        path = self.write_tsv("")
        output = self.out("import", str(path))
        self.assertIn("imported 0, skipped 0", output)

    def test_invalid_header_is_a_usage_error(self) -> None:
        path = self.dir / "bad.tsv"
        path.write_text("a\tb\n1\t2\n", encoding="utf-8")
        completed = self.cli("import", str(path), expect=2)
        self.assertIn("invalid TSV header", completed.stderr)

    def test_missing_file_is_a_runtime_failure(self) -> None:
        completed = self.cli("import", str(self.dir / "absent.tsv"), expect=1)
        self.assertIn("cannot read TSV file", completed.stderr)

    def test_import_requires_the_passphrase(self) -> None:
        path = self.write_tsv("5\tStone\tSite\tPeriod\t4.0\t1\t\n")
        completed = self.cli("import", str(path), passphrase="wrong", expect=1)
        self.assertIn("authentication failed", completed.stderr)
        self.assertEqual(self.read_catalog()["inscriptions"], [])


class StorageTests(CliTestCase):
    def test_catalog_is_valid_json_and_leaves_no_temp_files(self) -> None:
        self.init_catalog()
        self.add("Broby stone")
        self.assertIsInstance(json.loads(self.catalog.read_text(encoding="utf-8")), dict)
        leftovers = [entry.name for entry in self.dir.iterdir() if entry.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_file_can_be_selected_by_environment(self) -> None:
        target = self.dir / "envfile.json"
        env = {"RUNETALLY_FILE": str(target), "RUNETALLY_PASSPHRASE": PASSPHRASE, "NO_COLOR": "1"}
        subprocess.run(
            [sys.executable, str(CLI), "auth", "init"],
            capture_output=True, text=True, env={**os.environ, **env}, check=True,
        )
        self.assertTrue(target.exists())

    def test_ten_thousand_inscriptions_list_quickly(self) -> None:
        self.init_catalog()
        catalog = self.read_catalog()
        catalog["inscriptions"] = [
            {
                "id": index,
                "name": f"Inscription {index}",
                "site": "Broby",
                "period": "Viking Age",
                "stave_width_mm": f"{(index % 200) / 10 + 0.5:.1f}",
                "pigment_wear": index % 6,
                "notes": "",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
            }
            for index in range(1, 10_001)
        ]
        catalog["next_seq"] = 10_001
        self.catalog.write_text(json.dumps(catalog), encoding="utf-8")
        started = time.monotonic()
        output = self.out("list", "--sort", "stave-width", "--order", "desc")
        elapsed = time.monotonic() - started
        self.assertIn("10000 inscriptions", output)
        self.assertLess(elapsed, 10.0)


class ModuleTests(unittest.TestCase):
    def test_module_imports_and_exposes_main(self) -> None:
        import contextlib
        import io

        import runetally

        self.assertTrue(callable(runetally.main))
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.assertEqual(runetally.main([]), 0)
        self.assertIn("usage: runetally", buffer.getvalue())

    def test_band_for_is_total(self) -> None:
        import runetally

        self.assertEqual(runetally.band_for("4.0"), "narrow")
        self.assertEqual(runetally.band_for("4.05"), "medium")
        self.assertEqual(runetally.band_for("8.0"), "medium")
        self.assertEqual(runetally.band_for("8.01"), "broad")
        self.assertEqual(runetally.band_for(None), "unset")

    def test_table_row_is_exactly_one_hundred_columns(self) -> None:
        import runetally

        record = {"id": 1, "name": "x" * 120, "site": "y" * 100, "period": "z" * 100,
                  "stave_width_mm": "8.01", "pigment_wear": 3}
        self.assertEqual(len(runetally.format_record_row(record)), 100)
        self.assertEqual(len(runetally.table_header()), 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
