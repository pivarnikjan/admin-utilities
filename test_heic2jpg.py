#!/usr/bin/env python3
"""
Unit tests for heic2jpg.py.   Run with:  python -m unittest -v

The test images are ordinary JPEG/PNG bitmaps saved under a .heic/.heif name.
Pillow identifies images by content rather than by extension, so the whole
pipeline runs end to end without requiring a HEIF *encoder* to be available;
one extra test covers a genuine HEIC file and skips itself when pillow-heif
cannot write one.
"""

from __future__ import annotations

import io
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from PIL import Image

import heic2jpg
from heic2jpg import (
    CONVERTED,
    FAILED,
    Result,
    SKIPPED,
    confirm,
    find_heic_files,
    main,
    plan_deletions,
    target_path,
    verify_jpeg,
)


def write_image(path: Path, fmt: str = "JPEG", size: tuple[int, int] = (60, 40)) -> Path:
    """Create a small real image at `path`, whatever its extension says."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (200, 120, 60)).save(path, fmt)
    return path


def heif_encoder_available() -> bool:
    try:
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8)).save(buffer, "HEIF")
        return True
    except Exception:  # noqa: BLE001 - no encoder in this build
        return False


def run_cli(*argv: str) -> tuple[int, str]:
    """Run the CLI, returning (exit code, captured stdout)."""
    out = io.StringIO()
    with redirect_stdout(out):
        code = main(list(argv))
    return code, out.getvalue()


def fake_tty(answer: str):
    """sys.stdin replacement that claims to be a terminal."""
    return types.SimpleNamespace(isatty=lambda: True, readline=lambda: answer)


class TempTreeTestCase(unittest.TestCase):
    """Base class providing a temporary folder tree."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def build_tree(self) -> dict[str, Path]:
        """A tree shaped like the real bookkeeping folder."""
        paths = {
            "top": write_image(self.root / "IMG_0001.heic"),
            "upper": write_image(self.root / "01_PRIJMY" / "2026-01" / "IMG_0002.HEIC"),
            "deep": write_image(
                self.root / "02_VYDAVKY" / "2026-08" / "nested" / "IMG_0003.heif",
                fmt="PNG",
            ),
            "hif": write_image(self.root / "04_OSTATNE" / "IMG_0004.hif"),
        }
        # Files that must be left alone.
        (self.root / "01_PRIJMY" / "faktura.pdf").write_bytes(b"%PDF-1.4 not an image")
        (self.root / "00_PREHLAD.csv").write_text("a;b\n1;2\n", encoding="utf-8")
        (self.root / "02_VYDAVKY" / "poznamka.docx").write_bytes(b"PK\x03\x04 stub")
        write_image(self.root / "01_PRIJMY" / "already.jpg")
        return paths

    def heic_files(self) -> list[Path]:
        return sorted(p for p in self.root.rglob("*")
                      if p.suffix.lower() in heic2jpg.HEIC_SUFFIXES)

    def jpg_files(self) -> list[Path]:
        return sorted(p for p in self.root.rglob("*.jpg"))


class FindFilesTests(TempTreeTestCase):
    def test_non_recursive_finds_only_top_level(self) -> None:
        self.build_tree()
        found = find_heic_files(self.root, recursive=False)
        self.assertEqual([p.name for p in found], ["IMG_0001.heic"])

    def test_recursive_finds_every_level(self) -> None:
        self.build_tree()
        found = find_heic_files(self.root, recursive=True)
        self.assertEqual(
            sorted(p.name for p in found),
            ["IMG_0001.heic", "IMG_0002.HEIC", "IMG_0003.heif", "IMG_0004.hif"],
        )

    def test_ignores_other_file_types(self) -> None:
        self.build_tree()
        found = find_heic_files(self.root, recursive=True)
        suffixes = {p.suffix.lower() for p in found}
        self.assertTrue(suffixes <= heic2jpg.HEIC_SUFFIXES)
        self.assertNotIn(".pdf", suffixes)
        self.assertNotIn(".csv", suffixes)
        self.assertNotIn(".docx", suffixes)
        self.assertNotIn(".jpg", suffixes)

    def test_empty_directory(self) -> None:
        self.assertEqual(find_heic_files(self.root, recursive=True), [])


class TargetPathTests(TempTreeTestCase):
    def test_in_place_lands_next_to_source(self) -> None:
        src = self.root / "01_PRIJMY" / "2026-01" / "IMG_0002.HEIC"
        dst = target_path(src, self.root, self.root)
        self.assertEqual(dst, src.with_suffix(".jpg"))
        self.assertEqual(dst.parent, src.parent)

    def test_output_dir_mirrors_structure(self) -> None:
        src = self.root / "02_VYDAVKY" / "2026-08" / "IMG_0003.heif"
        out = self.root.parent / "elsewhere"
        dst = target_path(src, self.root, out)
        self.assertEqual(dst, out / "02_VYDAVKY" / "2026-08" / "IMG_0003.jpg")


class VerifyJpegTests(TempTreeTestCase):
    def test_accepts_real_jpeg(self) -> None:
        self.assertTrue(verify_jpeg(write_image(self.root / "ok.jpg")))

    def test_rejects_missing_empty_and_corrupt(self) -> None:
        missing = self.root / "nope.jpg"
        empty = self.root / "empty.jpg"
        empty.touch()
        corrupt = self.root / "corrupt.jpg"
        corrupt.write_bytes(b"this is not an image")
        for path in (missing, empty, corrupt, self.root):
            with self.subTest(path=path.name):
                self.assertFalse(verify_jpeg(path))


class ConvertInPlaceTests(TempTreeTestCase):
    def test_recursive_in_place_keeps_structure_and_originals(self) -> None:
        paths = self.build_tree()
        code, out = run_cli(str(self.root), "-r", "-i")

        self.assertEqual(code, 0, out)
        for src in paths.values():
            with self.subTest(src=src.name):
                self.assertTrue(src.with_suffix(".jpg").is_file())
                self.assertTrue(src.exists(), "originals must survive without -d")
        self.assertIn("4 converted", out)

    def test_non_recursive_leaves_subfolders_alone(self) -> None:
        paths = self.build_tree()
        code, _ = run_cli(str(self.root), "-i")

        self.assertEqual(code, 0)
        self.assertTrue(paths["top"].with_suffix(".jpg").is_file())
        self.assertFalse(paths["deep"].with_suffix(".jpg").exists())

    def test_output_directory_mirrors_tree(self) -> None:
        self.build_tree()
        out_dir = Path(self._tmp.name) / "converted"
        code, _ = run_cli(str(self.root), "-r", "-o", str(out_dir))

        self.assertEqual(code, 0)
        self.assertTrue((out_dir / "01_PRIJMY" / "2026-01" / "IMG_0002.jpg").is_file())
        self.assertTrue(
            (out_dir / "02_VYDAVKY" / "2026-08" / "nested" / "IMG_0003.jpg").is_file()
        )

    def test_skips_existing_jpeg_unless_overwrite(self) -> None:
        src = write_image(self.root / "IMG_9000.heic", size=(80, 60))
        dst = write_image(src.with_suffix(".jpg"), size=(10, 10))
        before = dst.stat().st_size

        code, out = run_cli(str(self.root), "-i")
        self.assertEqual(code, 0)
        self.assertIn("skipped", out)
        self.assertEqual(dst.stat().st_size, before)

        code, out = run_cli(str(self.root), "-i", "--overwrite")
        self.assertEqual(code, 0)
        self.assertNotEqual(dst.stat().st_size, before)
        with Image.open(dst) as img:
            self.assertEqual(img.size, (80, 60))


class DeleteTests(TempTreeTestCase):
    def test_recursive_convert_and_delete(self) -> None:
        paths = self.build_tree()
        code, out = run_cli(str(self.root), "-r", "-i", "-d", "-y")

        self.assertEqual(code, 0, out)
        self.assertEqual(self.heic_files(), [], "every original should be gone")
        for src in paths.values():
            self.assertTrue(src.with_suffix(".jpg").is_file())
        self.assertIn("Deleted 4 original(s).", out)
        # Unrelated files survive.
        self.assertTrue((self.root / "01_PRIJMY" / "faktura.pdf").is_file())
        self.assertTrue((self.root / "00_PREHLAD.csv").is_file())

    def test_delete_requires_explicit_destination(self) -> None:
        paths = self.build_tree()
        code, out = run_cli(str(self.root), "-r", "-d", "-y")

        self.assertEqual(code, 1)
        self.assertIn("Refusing to delete", out)
        for src in paths.values():
            self.assertTrue(src.exists())
        self.assertEqual(self.jpg_files(), [self.root / "01_PRIJMY" / "already.jpg"])

    def test_declined_prompt_keeps_originals(self) -> None:
        paths = self.build_tree()
        with mock.patch.object(sys, "stdin", fake_tty("n")), \
                mock.patch("builtins.input", return_value="n") as ask:
            code, out = run_cli(str(self.root), "-r", "-i", "-d")

        ask.assert_called_once()
        self.assertEqual(code, 0)
        self.assertIn("Originals kept.", out)
        for src in paths.values():
            self.assertTrue(src.exists())
            self.assertTrue(src.with_suffix(".jpg").is_file())

    def test_accepted_prompt_deletes(self) -> None:
        self.build_tree()
        with mock.patch.object(sys, "stdin", fake_tty("y")), \
                mock.patch("builtins.input", return_value="y"):
            code, out = run_cli(str(self.root), "-r", "-i", "-d")

        self.assertEqual(code, 0, out)
        self.assertEqual(self.heic_files(), [])

    def test_yes_flag_does_not_prompt(self) -> None:
        self.build_tree()
        with mock.patch("builtins.input", side_effect=AssertionError("must not ask")):
            code, _ = run_cli(str(self.root), "-r", "-i", "-d", "-y")
        self.assertEqual(code, 0)
        self.assertEqual(self.heic_files(), [])

    def test_non_interactive_without_yes_keeps_originals(self) -> None:
        paths = self.build_tree()
        not_a_tty = types.SimpleNamespace(isatty=lambda: False)
        with mock.patch.object(sys, "stdin", not_a_tty):
            code, out = run_cli(str(self.root), "-r", "-i", "-d")

        self.assertEqual(code, 0)
        self.assertIn("Not an interactive terminal", out)
        for src in paths.values():
            self.assertTrue(src.exists())

    def test_failed_file_is_never_deleted(self) -> None:
        good = write_image(self.root / "good.heic")
        broken = self.root / "broken.heic"
        broken.write_bytes(b"definitely not an image")

        code, out = run_cli(str(self.root), "-r", "-i", "-d", "-y")

        self.assertEqual(code, 1, out)
        self.assertTrue(broken.exists(), "unconvertible original must be kept")
        self.assertFalse(good.exists())
        self.assertTrue(good.with_suffix(".jpg").is_file())
        self.assertIn("FAILED", out)
        self.assertIn("1 original(s) kept", out)

    def test_rerun_deletes_originals_left_behind(self) -> None:
        """A previous run wrote the JPEG but did not delete; the next run finishes."""
        src = write_image(self.root / "IMG_1234.heic")
        write_image(src.with_suffix(".jpg"))

        code, out = run_cli(str(self.root), "-r", "-i", "-d", "-y")

        self.assertEqual(code, 0, out)
        self.assertIn("skipped", out)
        self.assertFalse(src.exists())
        self.assertTrue(src.with_suffix(".jpg").is_file())

    def test_second_full_run_is_a_no_op(self) -> None:
        self.build_tree()
        run_cli(str(self.root), "-r", "-i", "-d", "-y")
        code, out = run_cli(str(self.root), "-r", "-i", "-d", "-y")

        self.assertEqual(code, 0)
        self.assertIn("No HEIC/HEIF files found", out)


class DryRunTests(TempTreeTestCase):
    def test_dry_run_changes_nothing(self) -> None:
        paths = self.build_tree()
        before_jpgs = self.jpg_files()

        code, out = run_cli(str(self.root), "-r", "-i", "-d", "-y", "--dry-run")

        self.assertEqual(code, 0, out)
        self.assertIn("would delete 4 original(s)", out)
        self.assertEqual(self.jpg_files(), before_jpgs, "no JPEG may be written")
        for src in paths.values():
            self.assertTrue(src.exists())


class PlanDeletionsTests(TempTreeTestCase):
    def test_only_verified_jpegs_are_deletable(self) -> None:
        converted = write_image(self.root / "a.heic")
        skipped = write_image(self.root / "b.heic")
        failed = write_image(self.root / "c.heic")
        write_image(converted.with_suffix(".jpg"))
        write_image(skipped.with_suffix(".jpg"))
        # No JPEG written for the failed one.

        results = [
            Result(converted, converted.with_suffix(".jpg"), CONVERTED, ""),
            Result(skipped, skipped.with_suffix(".jpg"), SKIPPED, ""),
            Result(failed, failed.with_suffix(".jpg"), FAILED, ""),
        ]
        self.assertEqual(plan_deletions(results), [converted, skipped])

    def test_missing_output_is_not_deletable(self) -> None:
        src = write_image(self.root / "a.heic")
        results = [Result(src, self.root / "a.jpg", CONVERTED, "")]
        self.assertEqual(plan_deletions(results), [])

    def test_dry_run_counts_would_be_conversions(self) -> None:
        src = write_image(self.root / "a.heic")
        results = [Result(src, self.root / "a.jpg", CONVERTED, "")]
        self.assertEqual(plan_deletions(results, dry_run=True), [src])


class ConfirmTests(unittest.TestCase):
    def test_assume_yes_short_circuits(self) -> None:
        with mock.patch("builtins.input", side_effect=AssertionError("must not ask")):
            self.assertTrue(confirm("really?", assume_yes=True))

    def test_answers(self) -> None:
        for answer, expected in [("y", True), ("Y", True), ("yes", True),
                                 ("", False), ("n", False), ("nope", False)]:
            with self.subTest(answer=answer):
                with mock.patch.object(sys, "stdin", fake_tty(answer)), \
                        mock.patch("builtins.input", return_value=answer), \
                        redirect_stdout(io.StringIO()):
                    self.assertIs(confirm("really?"), expected)


class CliValidationTests(TempTreeTestCase):
    def test_missing_directory(self) -> None:
        code, out = run_cli(str(self.root / "does-not-exist"))
        self.assertEqual(code, 1)
        self.assertIn("Not a directory", out)

    def test_invalid_quality(self) -> None:
        self.build_tree()
        for bad in ("0", "96"):
            with self.subTest(quality=bad):
                code, out = run_cli(str(self.root), "-i", "-q", bad)
                self.assertEqual(code, 1)
                self.assertIn("Quality must be", out)

    def test_in_place_and_output_are_mutually_exclusive(self) -> None:
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()):
            with mock.patch.object(sys, "stderr", io.StringIO()):
                main([str(self.root), "-i", "-o", str(self.root / "out")])

    def test_long_flag_spellings(self) -> None:
        parser = heic2jpg.build_parser()
        args = parser.parse_args(["folder", "--recursively", "--import", "--delete",
                                  "--yes", "--dry-run"])
        self.assertTrue(args.recursive)
        self.assertTrue(args.in_place)
        self.assertTrue(args.delete)
        self.assertTrue(args.yes)
        self.assertTrue(args.dry_run)

        args = parser.parse_args(["folder", "-r", "-i", "-d", "-y"])
        self.assertTrue(args.recursive and args.in_place and args.delete and args.yes)

        args = parser.parse_args(["folder", "--in-place"])
        self.assertTrue(args.in_place)

    def test_no_files_found(self) -> None:
        code, out = run_cli(str(self.root), "-r", "-i")
        self.assertEqual(code, 0)
        self.assertIn("No HEIC/HEIF files found", out)


@unittest.skipUnless(heif_encoder_available(), "pillow-heif cannot write HEIF here")
class RealHeicTests(TempTreeTestCase):
    def test_genuine_heic_round_trip(self) -> None:
        src = self.root / "sub" / "real.heic"
        src.parent.mkdir(parents=True)
        Image.new("RGB", (120, 90), (10, 120, 200)).save(src, "HEIF")

        code, out = run_cli(str(self.root), "-r", "-i", "-d", "-y", "-q", "70")

        self.assertEqual(code, 0, out)
        self.assertFalse(src.exists())
        jpg = src.with_suffix(".jpg")
        self.assertTrue(jpg.is_file())
        with Image.open(jpg) as img:
            self.assertEqual(img.format, "JPEG")
            self.assertEqual(img.size, (120, 90))


if __name__ == "__main__":
    unittest.main(verbosity=2)
