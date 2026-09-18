#!/usr/bin/env python3
"""
Combine every image in a directory into one PDF file (one image per page).

Reads JPEG/PNG/TIFF/BMP/WEBP and - when pillow-heif is installed - HEIC/HEIF,
so it works both on the raw scans and on the JPEGs produced by heic2jpg.py.

Requires:  pip install pillow        (optional: pillow-heif for HEIC input)

Examples:
    python img2pdf.py ./Scans-jpg
    python img2pdf.py ./Scans-jpg -o contract.pdf --preset email
    python img2pdf.py ./Scans -o contract.pdf --preset email --target-mb 5
"""

from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path

try:
    from PIL import Image, ImageOps
except ImportError:
    sys.exit("Missing dependency. Install with:  pip install pillow")

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_SUFFIXES = {".heic", ".heif", ".hif"}
except ImportError:  # HEIC input simply is not supported then
    HEIF_SUFFIXES = set()

IMAGE_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".webp",
} | HEIF_SUFFIXES

# Page sizes in millimetres, portrait orientation.
PAGE_SIZES_MM = {
    "a3": (297.0, 420.0),
    "a4": (210.0, 297.0),
    "a5": (148.0, 210.0),
    "letter": (215.9, 279.4),
    "legal": (215.9, 355.6),
}
MM_PER_INCH = 25.4

# Bundles of settings; individual command-line options always take precedence.
PRESETS = {
    "email": {"quality": 55, "dpi": 150, "max_size": 1700, "grayscale": True},
    "print": {"quality": 92, "dpi": 300, "grayscale": False},
}


def natural_key(path: Path) -> list:
    """Sort key so IMG_2 comes before IMG_10."""
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", path.name)
    ]


def find_images(root: Path, recursive: bool) -> list[Path]:
    walker = root.rglob("*") if recursive else root.glob("*")
    files = [p for p in walker if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES]
    return sorted(files, key=natural_key)


def page_pixels(name: str, dpi: int, landscape: bool) -> tuple[int, int]:
    width_mm, height_mm = PAGE_SIZES_MM[name]
    if landscape:
        width_mm, height_mm = height_mm, width_mm
    return (
        round(width_mm / MM_PER_INCH * dpi),
        round(height_mm / MM_PER_INCH * dpi),
    )


def build_page(src: Path, page_size: str, dpi: int, margin_mm: float,
               max_size: int | None, grayscale: bool) -> Image.Image:
    """Load one image and return the page bitmap that goes into the PDF."""
    with Image.open(src) as opened:
        # Bake the EXIF rotation into the pixels; PDF has no orientation flag.
        img = ImageOps.exif_transpose(opened)
        img.load()

    img = img.convert("L" if grayscale else "RGB")

    if max_size:
        img.thumbnail((max_size, max_size), Image.LANCZOS)

    if page_size == "native":
        return img

    # Fixed page: fit the image inside the margins, centred on white paper.
    landscape = img.width > img.height
    page_w, page_h = page_pixels(page_size, dpi, landscape)
    margin_px = round(margin_mm / MM_PER_INCH * dpi)
    box_w = max(1, page_w - 2 * margin_px)
    box_h = max(1, page_h - 2 * margin_px)

    scale = min(box_w / img.width, box_h / img.height)
    if scale < 1:  # never upscale, only shrink to fit
        img = img.resize(
            (max(1, round(img.width * scale)), max(1, round(img.height * scale))),
            Image.LANCZOS,
        )

    page = Image.new(img.mode, (page_w, page_h), 255 if grayscale else (255, 255, 255))
    page.paste(img, ((page_w - img.width) // 2, (page_h - img.height) // 2))
    return page


def scale_pages(pages: list[Image.Image], factor: float) -> list[Image.Image]:
    if factor >= 1.0:
        return pages
    return [
        p.resize(
            (max(1, round(p.width * factor)), max(1, round(p.height * factor))),
            Image.LANCZOS,
        )
        for p in pages
    ]


def render_pdf(pages: list[Image.Image], quality: int, resolution: float,
               title: str) -> bytes:
    """Render the whole PDF into memory so its size can be measured."""
    buffer = io.BytesIO()
    pages[0].save(
        buffer,
        "PDF",
        save_all=True,
        append_images=pages[1:],
        resolution=resolution,
        quality=quality,
        optimize=True,
        title=title,
    )
    return buffer.getvalue()


def fit_to_target(pages: list[Image.Image], target_bytes: int, max_quality: int,
                  min_quality: int, dpi: int, title: str) -> tuple[bytes, bool]:
    """Lower the JPEG quality, then the resolution, until the PDF fits the target.

    Returns the PDF bytes and whether the target was actually met. Page scaling
    is compensated in the PDF resolution, so the pages keep their paper size.
    """
    for factor in (1.0, 0.85, 0.7, 0.55, 0.45, 0.35, 0.25):
        scaled = scale_pages(pages, factor)
        resolution = dpi * factor

        # Cheapest possible result for this resolution; if even that is too big,
        # there is no point in searching qualities here.
        floor_pdf = render_pdf(scaled, min_quality, resolution, title)
        print(f"  {factor * 100:>3.0f}% scale, quality {min_quality:>2}: "
              f"{len(floor_pdf) / 1e6:.1f} MB")
        if len(floor_pdf) > target_bytes:
            continue

        # Binary search for the highest quality that still fits.
        best = floor_pdf
        lo, hi = min_quality + 1, max_quality
        while lo <= hi:
            mid = (lo + hi) // 2
            candidate = render_pdf(scaled, mid, resolution, title)
            fits = len(candidate) <= target_bytes
            print(f"  {factor * 100:>3.0f}% scale, quality {mid:>2}: "
                  f"{len(candidate) / 1e6:.1f} MB{'' if fits else ' (too big)'}")
            if fits:
                best = candidate
                lo = mid + 1
            else:
                hi = mid - 1
        return best, True

    # Nothing fit - fall back to the smallest thing we can produce.
    smallest = scale_pages(pages, 0.25)
    return render_pdf(smallest, min_quality, dpi * 0.25, title), False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Combine all images in a directory into a single PDF."
    )
    parser.add_argument("directory", type=Path, help="Directory containing the images")
    parser.add_argument("-o", "--output", type=Path,
                        help="Output PDF (default: <directory-name>.pdf next to the folder)")
    parser.add_argument("-r", "--recursive", action="store_true",
                        help="Also include images from subdirectories")
    parser.add_argument("-q", "--quality", type=int,
                        help="JPEG quality of the embedded pages, 1-95 (default: 85)")
    parser.add_argument("--page-size", default="a4",
                        choices=sorted(PAGE_SIZES_MM) + ["native"],
                        help="Paper size, or 'native' to let each page match its image "
                             "(default: a4)")
    parser.add_argument("--dpi", type=int,
                        help="Pixels per inch used to render the pages (default: 200)")
    parser.add_argument("--margin", type=float, default=0.0, metavar="MM",
                        help="White margin around each image in mm (default: 0)")
    parser.add_argument("--max-size", type=int, metavar="PX",
                        help="Shrink images so the longest side is at most PX pixels")
    parser.add_argument("--grayscale", action="store_true", default=None,
                        help="Convert pages to grayscale (much smaller for text scans)")
    parser.add_argument("--color", dest="grayscale", action="store_false",
                        help="Keep colour (use to override --preset email)")
    parser.add_argument("--preset", choices=sorted(PRESETS),
                        help="Bundle of settings: 'email' = small attachment, "
                             "'print' = high quality. Individual options win over it.")
    parser.add_argument("--target-mb", type=float, metavar="MB",
                        help="Shrink quality and then resolution until the PDF fits "
                             "into MB megabytes")
    parser.add_argument("--min-quality", type=int, default=30,
                        help="Lowest JPEG quality --target-mb may use (default: 30)")
    parser.add_argument("--title", help="PDF title metadata (default: output file name)")
    parser.add_argument("--overwrite", action="store_true",
                        help="Overwrite the output file if it already exists")
    args = parser.parse_args()

    # A preset only fills in what was not given explicitly on the command line.
    preset = PRESETS.get(args.preset, {})
    for key, value in preset.items():
        if getattr(args, key) is None:
            setattr(args, key, value)
    quality = args.quality if args.quality is not None else 85
    dpi = args.dpi if args.dpi is not None else 200
    grayscale = bool(args.grayscale)

    root: Path = args.directory.expanduser().resolve()
    if not root.is_dir():
        return print(f"Not a directory: {root}") or 1

    if not 1 <= quality <= 95:
        return print("Quality must be between 1 and 95.") or 1

    if not 1 <= args.min_quality <= quality:
        return print(f"--min-quality must be between 1 and {quality}.") or 1

    if dpi < 1:
        return print("DPI must be a positive number.") or 1

    if args.target_mb is not None and args.target_mb <= 0:
        return print("--target-mb must be a positive number.") or 1

    out_pdf: Path = (args.output.expanduser().resolve() if args.output
                     else root.parent / f"{root.name}.pdf")
    if out_pdf.exists() and not args.overwrite:
        return print(f"{out_pdf} already exists. Use --overwrite to replace it.") or 1

    files = find_images(root, args.recursive)
    if not files:
        return print(f"No images found in {root}") or 1

    print(f"Found {len(files)} image(s). Building {out_pdf.name}...\n")

    pages: list[Image.Image] = []
    failures = 0
    for src in files:
        try:
            pages.append(
                build_page(src, args.page_size, dpi, args.margin,
                           args.max_size, grayscale)
            )
            print(f"  page {len(pages):>3}: {src.name}")
        except Exception as exc:  # noqa: BLE001 - one bad file shouldn't stop the batch
            failures += 1
            print(f"  {src.name}: FAILED ({exc})")

    if not pages:
        print("\nNothing could be read - no PDF written.")
        return 1

    title = args.title or out_pdf.stem
    missed_target = False

    if args.target_mb:
        target_bytes = int(args.target_mb * 1_000_000)
        print(f"\nFitting into {args.target_mb:g} MB...")
        data, fitted = fit_to_target(pages, target_bytes, quality,
                                     args.min_quality, dpi, title)
        missed_target = not fitted
    else:
        data = render_pdf(pages, quality, float(dpi), title)

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    out_pdf.write_bytes(data)

    size_mb = len(data) / 1e6
    print(f"\nDone. {len(pages)} page(s) -> {out_pdf} ({size_mb:.1f} MB)"
          + (f", {failures} failed." if failures else "."))

    if missed_target:
        hints = ["a lower --min-quality", "splitting the scans into several PDFs"]
        if not grayscale:
            hints.insert(0, "--grayscale")
        print(f"Could not reach {args.target_mb:g} MB. Try {', '.join(hints)}.")

    return 1 if failures or missed_target else 0


if __name__ == "__main__":
    sys.exit(main())
