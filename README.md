# Scan tools

Two small command-line utilities for turning phone photos of documents into a
single PDF:

| Script | What it does |
| --- | --- |
| [`heic2jpg.py`](heic2jpg.py) | Converts every HEIC/HEIF image in a folder (optionally a whole folder tree) into a compressed JPEG, and can delete the originals afterwards. |
| [`img2pdf.py`](img2pdf.py) | Merges every image in a folder into one PDF, one image per page, with size control for e-mail attachments. |

They can be used separately or chained: `Scans/` (HEIC) → `Scans-jpg/` (JPEG) → `Scans.pdf`.
`img2pdf.py` also reads HEIC directly, so the intermediate step is optional — it is
mainly useful when you want the JPEGs themselves.

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Dependencies:

- `pillow` — required by both scripts.
- `pillow_heif` — required by `heic2jpg.py`, optional for `img2pdf.py`
  (without it, `img2pdf.py` simply ignores `.heic`/`.heif` files).

`pillow_heif` is pinned on purpose: Windows Smart App Control blocks the unsigned
native module of some releases (1.5.0 and 1.7.0 fail with "An Application Control
policy has blocked this file"; 1.6.0 and 1.8.0 load). Re-test before changing the pin.

On Windows the examples below assume the virtual environment is active. Otherwise
call the interpreter directly, e.g. `.venv\Scripts\python.exe img2pdf.py ...`.

---

## `heic2jpg.py` — HEIC/HEIF → JPEG

```bash
python heic2jpg.py DIRECTORY [options]
```

Converts every `.heic`/`.heif`/`.hif` file found in `DIRECTORY`. The EXIF rotation
is baked into the pixels, so the JPEG looks correct in every viewer.

`DIRECTORY` may also be a single HEIC/HEIF file; only that file is converted, and
the JPEG lands next to it (or directly in the `-o` folder).

### Options

| Option | Default | Description |
| --- | --- | --- |
| `DIRECTORY` | – | Folder containing the HEIC files, or a single HEIC file (required). |
| `-o`, `--output DIR` | next to the originals | Output folder; the source subfolder structure is mirrored inside it. Cannot be combined with `-i`. |
| `-i`, `--import`, `--in-place` | off | Write each JPEG next to its HEIC, keeping the folder structure. This is also the default when neither `-i` nor `-o` is given — but it must be stated explicitly before `-d` is allowed. |
| `-q`, `--quality N` | `85` | JPEG quality, 1–95. |
| `-r`, `--recursive`, `--recursively` | off | Also process subdirectories, at any depth. |
| `-d`, `--delete` | off | Delete each original whose JPEG was verified. Requires `-i` or `-o`, and asks for confirmation once. |
| `-y`, `--yes` | off | Answer that confirmation automatically. |
| `--dry-run` | off | Report what would happen; write and delete nothing. |
| `--max-size PX` | off | Shrink so the longest side is at most `PX` pixels. |
| `--overwrite` | off | Overwrite existing `.jpg` files (otherwise they are skipped). |
| `--no-metadata` | off | Strip EXIF and colour profile from the output. |
| `-j`, `--workers N` | `4` | Number of files converted in parallel. |

### Examples

```bash
python heic2jpg.py Scans -o Scans-jpg
```

```bash
python heic2jpg.py Scans\IMG_5912.HEIC
```

```bash
python heic2jpg.py Scans -o Scans-jpg -q 80 --max-size 2048 --overwrite
```

Per-file progress is printed with the size change, and a summary line reports how
many files were converted, skipped or failed. Exit code is `1` if any file failed.

### Converting a whole folder tree in place

This is the bookkeeping case: one folder with many subfolders (`01_PRIJMY\2026-03\`,
`02_VYDAVKY\2026-08\`, …) holding HEIC photos mixed with PDFs, CSVs and DOCX. The
command below walks the whole tree, writes each JPEG into the folder its HEIC came
from, and then removes the HEIC files:

```bash
python heic2jpg.py uctovnictvo-2026 -r -i -d
```

The long spelling of the same command is
`--recursively --import --delete`; `--in-place` also works instead of `--import`.
Add `-y` to skip the confirmation.

```
Found 44 file(s) in this tree. Converting at quality 85...

  IMG_0001.heic: 1.7 MB -> 1.2 MB (30% smaller)
  ...

Done. 44 converted, 0 skipped, 0 failed.

Ready to delete 44 HEIC original(s), 69.5 MB. Continue? [y/N] y
Deleted 44 original(s).
```

Only the HEIC files change — PDFs, CSVs, DOCX, XLSX and existing JPEGs are left
exactly where they are.

#### What protects you from losing a photo

- **Nothing is deleted before it is converted.** All files are converted first; only
  then does the deletion phase start.
- **Every JPEG is re-opened and verified** before its original is removed. A file
  that failed to convert keeps its original and makes the exit code `1`.
- **One confirmation.** The prompt appears once, with the file count and the total
  size. Anything other than `y`/`yes` keeps everything (`Originals kept.`).
- **`-d` alone is refused.** Without `-i` or `-o` the run stops with
  *"Refusing to delete originals without an explicit destination"* — a stray `-d`
  can never delete quietly.
- **Non-interactive runs never delete** unless `-y` is given, so a scheduled task or
  a piped run cannot silently remove files.
- **Deletion is permanent** — files do not go to the Recycle Bin. Try `--dry-run`
  first if you want to see the plan:

```bash
python heic2jpg.py uctovnictvo-2026 -r -i -d --dry-run
```

```
Dry run: Found 44 file(s) in this tree. Converting at quality 85...
  ...
Dry run: would delete 44 original(s), 69.5 MB.
```

A run that is interrupted halfway is safe to repeat: files whose JPEG already exists
are reported as `skipped`, and because that existing JPEG is verified, their originals
are deleted on the second pass. Once the tree is clean, the same command just prints
`No HEIC/HEIF files found`.

---

## `img2pdf.py` — images → single PDF

```bash
python img2pdf.py DIRECTORY [options]
```

Collects every image in `DIRECTORY` (`.jpg`, `.jpeg`, `.png`, `.tif`, `.tiff`,
`.bmp`, `.gif`, `.webp`, plus `.heic`/`.heif`/`.hif` when `pillow_heif` is
installed), sorts them by file name in natural order (`IMG_2` before `IMG_10`) and
writes one PDF with one image per page.

### Options

| Option | Default | Description |
| --- | --- | --- |
| `DIRECTORY` | – | Folder containing the images (required). |
| `-o`, `--output FILE` | `<directory-name>.pdf` next to the folder | Output PDF path. |
| `-r`, `--recursive` | off | Also include images from subdirectories. |
| `-q`, `--quality N` | `85` | JPEG quality of the embedded pages, 1–95. Biggest lever on file size. |
| `--page-size NAME` | `a4` | `a3`, `a4`, `a5`, `letter`, `legal`, or `native`. |
| `--dpi N` | `200` | Pixels per inch used to render the pages. |
| `--margin MM` | `0` | White margin around each image, in millimetres. |
| `--max-size PX` | off | Shrink images so the longest side is at most `PX` pixels. |
| `--grayscale` | off | Convert pages to grayscale — much smaller files for text scans. |
| `--color` | – | Keep colour; use it to override `--preset email`. |
| `--preset NAME` | off | `email` (small attachment) or `print` (high quality) — see below. |
| `--target-mb MB` | off | Automatically lower quality, then resolution, until the PDF fits into `MB` megabytes. |
| `--min-quality N` | `30` | Lowest JPEG quality `--target-mb` is allowed to use. |
| `--title TEXT` | output file name | PDF title metadata. |
| `--overwrite` | off | Replace the output file if it already exists. |

Page layout: with a fixed paper size each image is scaled to fit inside the
margins and centred on a white page; the page orientation follows each image
(a landscape photo gets a landscape page). Images are never upscaled. With
`--page-size native` every page takes the exact size of its image at the chosen
DPI, which avoids any white borders.

### Examples

Everything with the defaults (A4, 200 DPI, quality 85):

```bash
python img2pdf.py Scans-jpg
```

Straight from the HEIC originals, borderless pages, downscaled:

```bash
python img2pdf.py Scans -o document.pdf --page-size native --max-size 1600
```

A file that could not be read is reported and skipped; the PDF is still written
from the remaining pages and the exit code is `1`.

---

## Making the PDF small enough to e-mail

Default settings produce a print-quality file — roughly 18 MB for 20 A4 pages,
which most mailboxes reject (Gmail and Outlook cut off around 20–25 MB, many
corporate servers at 10 MB). Two options handle this.

### 1. `--preset email`

Sets quality 55, 150 DPI, grayscale and a 1700 px limit on the longest side in
one go. Text scans stay perfectly readable.

```bash
python img2pdf.py Scans-jpg -o document.pdf --preset email
```

For a set of 20 phone-scanned A4 pages that gives **5.2 MB instead of 17.7 MB**.
Any individual option still overrides the preset, e.g. `--preset email --color`
keeps the colour, `--preset email --dpi 200` renders sharper pages.

| Preset | Quality | DPI | Grayscale | Max side |
| --- | --- | --- | --- | --- |
| `email` | 55 | 150 | yes | 1700 px |
| `print` | 92 | 300 | no | – |

### 2. `--target-mb` — hit an exact size limit

Give the mailbox limit and the script finds the settings itself: it binary-searches
the JPEG quality down to `--min-quality`, and if that is not enough it also
reduces the page resolution step by step (100 %, 85 %, 70 %, 55 %, 45 %, 35 %,
25 %). The paper size of the pages never changes — only the pixels behind them.

```bash
python img2pdf.py Scans-jpg -o document.pdf --target-mb 5
```

```
Fitting into 5 MB...
  100% scale, quality 30: 3.5 MB
  100% scale, quality 43: 4.4 MB
  100% scale, quality 49: 4.9 MB
  100% scale, quality 52: 5.0 MB (too big)
  ...
Done. 20 page(s) -> document.pdf (4.9 MB).
```

Combine both when the limit is tight — the preset sets the starting point, the
target does the fine tuning:

```bash
python img2pdf.py Scans-jpg -o document.pdf --preset email --target-mb 5
```

If even the smallest combination stays above the target, the file is still
written (as small as possible), a hint is printed and the exit code is `1`.
In that case use `--min-quality 20`, `--grayscale`, or split the scans into
two PDFs.

Attachments are re-encoded to base64 by e-mail, which adds about 33 % — so aim
for a target roughly a quarter below the server limit (e.g. `--target-mb 7` for
a 10 MB mailbox).

### What actually drives the size

| Lever | Effect | Cost |
| --- | --- | --- |
| `--grayscale` | ≈ 40–50 % smaller on text scans | loses colour (signatures, stamps) |
| `-q` / `--quality` | near-linear; 85 → 55 roughly halves the file | JPEG artefacts on fine print |
| `--dpi` / `--max-size` | 200 → 150 DPI is ≈ 45 % fewer pixels | less zoom detail |
| fewer pages | linear | – |

Reference numbers for a set of 20 phone-scanned A4 pages:

| Settings | Size |
| --- | --- |
| defaults (A4, 200 DPI, q 85, colour) | 17.7 MB |
| `-q 40 --grayscale` | 6.7 MB |
| `--preset email` | 5.2 MB |
| `--target-mb 5` | 4.9 MB |
| `--preset email --target-mb 1` | 1.0 MB |

---

## Typical workflow

```bash
python heic2jpg.py Scans -o Scans-jpg -q 85
python img2pdf.py Scans-jpg -o document.pdf --preset email --target-mb 7
```

Or in one step, straight from the HEIC originals:

```bash
python img2pdf.py Scans -o document.pdf --preset email --target-mb 7
```

Check that both folders hold the same number of files before building the PDF —
`heic2jpg.py` skips files whose `.jpg` already exists, so a leftover from an
earlier run can silently stay outdated (use `--overwrite` to force a rebuild).

## Running the tests

`heic2jpg.py` is covered by unit tests (stdlib `unittest`, no extra dependency):

```bash
python -m unittest -v
```

36 tests build throwaway folder trees in a temp directory and drive the real CLI —
recursive discovery, single-file input, in-place output, the confirmation prompt, `--dry-run`, deletion
safety (a corrupt file keeps its original), and a genuine HEIC round trip. Nothing
outside the temp directory is touched.

## Notes

- **File size.** See [Making the PDF small enough to e-mail](#making-the-pdf-small-enough-to-e-mail).
- **Memory.** `img2pdf.py` keeps all rendered pages in memory before writing, and
  `--target-mb` additionally renders each trial PDF into memory. A large batch at
  high DPI can use a lot of RAM; lower `--dpi` or split the folder if that
  becomes a problem.
- **Speed.** `--target-mb` re-renders the PDF a handful of times (five or six
  renders is typical), so it takes noticeably longer than a plain run.
- **Page order** follows the file names. Rename files (e.g. `01-...`, `02-...`)
  if you need a different order.
