#!/usr/bin/env python3
"""
Convert every HEIC/HEIF image in a directory into a compressed JPEG.

Requires:  pip install pillow pillow-heif

Examples:
    python heic2jpg.py ~/Pictures
    python heic2jpg.py ~/Pictures -o ~/Pictures/jpg -q 80 -r
    python heic2jpg.py ./uctovnictvo-2026 -r -i -d      # whole tree, drop originals
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import NamedTuple

try:
    from PIL import Image, ImageOps
    import pillow_heif
except ImportError:
    sys.exit("Missing dependency. Install with:  pip install pillow pillow-heif")

pillow_heif.register_heif_opener()

HEIC_SUFFIXES = {".heic", ".heif", ".hif"}
ORIENTATION_TAG = 0x0112

CONVERTED = "converted"
SKIPPED = "skipped"
FAILED = "failed"


class Result(NamedTuple):
    """Outcome for one file, used later to decide what may be deleted."""

    src: Path
    dst: Path
    status: str
    message: str


def find_heic_files(root: Path, recursive: bool) -> list[Path]:
    walker = root.rglob("*") if recursive else root.glob("*")
    return sorted(p for p in walker if p.is_file() and p.suffix.lower() in HEIC_SUFFIXES)


def target_path(src: Path, root: Path, out_dir: Path) -> Path:
    """Mirror the source folder structure inside the output directory."""
    return (out_dir / src.relative_to(root)).with_suffix(".jpg")


def verify_jpeg(path: Path) -> bool:
    """True when `path` is a readable, non-empty image file."""
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return False
        with Image.open(path) as img:
            img.verify()
        return True
    except Exception:  # noqa: BLE001 - anything unreadable counts as not verified
        return False


def convert(src: Path, dst: Path, quality: int, max_size: int | None,
            overwrite: bool, keep_metadata: bool, dry_run: bool = False) -> Result:
    if dst.exists() and not overwrite:
        return Result(src, dst, SKIPPED, "skipped (already exists)")

    if dry_run:
        return Result(src, dst, CONVERTED, f"would convert -> {dst.name}")

    with Image.open(src) as img:
        # Apply the EXIF rotation to the pixels, so the JPEG looks correct
        # even in viewers that ignore the orientation flag.
        img = ImageOps.exif_transpose(img)

        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        if max_size:
            img.thumbnail((max_size, max_size), Image.LANCZOS)

        save_kwargs = {
            "quality": quality,
            "optimize": True,
            "progressive": True,
        }

        if keep_metadata:
            exif = img.getexif()
            # Orientation is already baked into the pixels above; leaving the
            # tag in would rotate the image a second time.
            exif.pop(ORIENTATION_TAG, None)
            if exif:
                save_kwargs["exif"] = exif.tobytes()
            icc = img.info.get("icc_profile")
            if icc:
                save_kwargs["icc_profile"] = icc

        dst.parent.mkdir(parents=True, exist_ok=True)
        img.save(dst, "JPEG", **save_kwargs)

    before = src.stat().st_size
    after = dst.stat().st_size
    change = (1 - after / before) * 100 if before else 0
    label = f"{abs(change):.0f}% {'smaller' if change >= 0 else 'larger'}"
    return Result(src, dst, CONVERTED,
                  f"{before / 1e6:.1f} MB -> {after / 1e6:.1f} MB ({label})")


def plan_deletions(results: list[Result], dry_run: bool = False) -> list[Path]:
    """Originals that may be removed: only those backed by a readable JPEG.

    Files skipped because their JPEG already existed count too - that JPEG is
    re-verified - so a run interrupted halfway converges on the next run.
    """
    deletable = []
    for result in results:
        if result.status == FAILED:
            continue
        if dry_run and result.status == CONVERTED:
            deletable.append(result.src)  # the JPEG would have been written
        elif verify_jpeg(result.dst):
            deletable.append(result.src)
    return deletable


def confirm(prompt: str, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    try:
        interactive = sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        interactive = False
    if not interactive:
        print("Not an interactive terminal - re-run with -y/--yes to delete.")
        return False
    try:
        answer = input(f"{prompt} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def delete_originals(paths: list[Path], dry_run: bool = False) -> tuple[int, int]:
    deleted = failed = 0
    for path in paths:
        if dry_run:
            deleted += 1
            continue
        try:
            path.unlink()
            deleted += 1
        except OSError as exc:
            failed += 1
            print(f"  could not delete {path.name}: {exc}")
    return deleted, failed


def delete_phase(results: list[Result], dry_run: bool, assume_yes: bool) -> int:
    """Remove the originals that have a verified JPEG. Returns the failure count."""
    deletable = plan_deletions(results, dry_run)
    kept = len(results) - len(deletable)

    if not deletable:
        print("\nNothing deleted - no original has a verified JPEG.")
        return 0

    total_mb = sum(p.stat().st_size for p in deletable if p.is_file()) / 1e6

    if dry_run:
        print(f"\nDry run: would delete {len(deletable)} original(s), {total_mb:.1f} MB.")
        if kept:
            print(f"{kept} original(s) would be kept (no verified JPEG).")
        return 0

    prompt = (f"\nReady to delete {len(deletable)} HEIC original(s), "
              f"{total_mb:.1f} MB. Continue?")
    if not confirm(prompt, assume_yes):
        print("Originals kept.")
        return 0

    deleted, del_failed = delete_originals(deletable)
    print(f"Deleted {deleted} original(s)."
          + (f" {del_failed} could not be removed." if del_failed else ""))
    if kept:
        print(f"{kept} original(s) kept (no verified JPEG).")
    return del_failed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert HEIC/HEIF files in a directory to compressed JPEGs."
    )
    parser.add_argument("directory", type=Path, help="Directory containing HEIC files")
    destination = parser.add_mutually_exclusive_group()
    destination.add_argument("-o", "--output", type=Path,
                             help="Output directory (default: alongside the originals)")
    destination.add_argument("-i", "--import", "--in-place", dest="in_place",
                             action="store_true",
                             help="Write each JPEG next to its HEIC, keeping the folder "
                                  "structure (required before --delete)")
    parser.add_argument("-q", "--quality", type=int, default=85,
                        help="JPEG quality 1-95 (default: 85)")
    parser.add_argument("-r", "--recursive", "--recursively", dest="recursive",
                        action="store_true", help="Also process subdirectories")
    parser.add_argument("-d", "--delete", action="store_true",
                        help="Delete each original whose JPEG was verified "
                             "(needs -i or -o; asks for confirmation)")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="Do not ask before deleting the originals")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only report what would happen; write and delete nothing")
    parser.add_argument("--max-size", type=int, metavar="PX",
                        help="Shrink so the longest side is at most PX pixels")
    parser.add_argument("--overwrite", action="store_true",
                        help="Overwrite existing .jpg files")
    parser.add_argument("--no-metadata", action="store_true",
                        help="Strip EXIF and colour profile from the output")
    parser.add_argument("-j", "--workers", type=int, default=4,
                        help="Parallel workers (default: 4)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    root: Path = args.directory.expanduser().resolve()
    if not root.is_dir():
        return print(f"Not a directory: {root}") or 1

    if not 1 <= args.quality <= 95:
        return print("Quality must be between 1 and 95.") or 1

    if args.delete and not (args.in_place or args.output):
        return print("Refusing to delete originals without an explicit destination - "
                     "add -i (convert in place) or -o DIR.") or 1

    out_dir = args.output.expanduser().resolve() if args.output else root
    files = find_heic_files(root, args.recursive)

    if not files:
        print(f"No HEIC/HEIF files found in {root}")
        return 0

    scope = "tree" if args.recursive else "folder"
    prefix = "Dry run: " if args.dry_run else ""
    print(f"{prefix}Found {len(files)} file(s) in this {scope}. "
          f"Converting at quality {args.quality}...\n")

    results: list[Result] = []
    failures = skipped = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(
                convert,
                f,
                target_path(f, root, out_dir),
                args.quality,
                args.max_size,
                args.overwrite,
                not args.no_metadata,
                args.dry_run,
            ): f
            for f in files
        }
        for future in as_completed(futures):
            src = futures[future]
            try:
                result = future.result()
                results.append(result)
                if result.status == SKIPPED:
                    skipped += 1
                print(f"  {src.name}: {result.message}")
            except Exception as exc:  # noqa: BLE001 - one bad file shouldn't stop the batch
                failures += 1
                results.append(
                    Result(src, target_path(src, root, out_dir), FAILED, str(exc))
                )
                print(f"  {src.name}: FAILED ({exc})")

    converted = len(files) - failures - skipped
    print(f"\n{prefix}Done. {converted} converted, {skipped} skipped, {failures} failed.")

    if args.delete:
        failures += delete_phase(results, args.dry_run, args.yes)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
