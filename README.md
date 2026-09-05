# Image Metadata Cleaner

A Python command-line tool that creates image copies without embedded descriptive
metadata, including AI generation labels, prompts, and Content Credentials (C2PA).

It decodes an image, applies its EXIF orientation, and builds a **new PNG from pixel
bytes only**. Every output is checked before it is published: only the essential
`IHDR`, `IDAT`, and `IEND` PNG chunks are allowed. Unknown metadata is discarded along
with recognized fields. Processing is local; the tool makes no network requests.

## Requirements and installation

- Python 3.10 or newer.
- Pillow 12.1.1 or newer, below version 13 (installed automatically).
- Windows, macOS, or Linux. ExifTool is not required.

From PowerShell:

```powershell
cd C:\Users\pedro\OneDrive\Documentos\Dev\image_metadata_cleaner
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m image_metadata_cleaner --help
```

If `py` is unavailable, use your Python installation's `python` executable for the
virtual environment command. Activating the environment is optional.

On macOS or Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m image_metadata_cleaner --help
```

The examples below use `python` from the environment. In PowerShell you can replace
`python` with `.\.venv\Scripts\python.exe`. An installed console command,
`image-metadata-cleaner`, provides the same interface.

## Usage

Clean one image; this creates `photo.jpg.clean.png` beside the original:

```sh
python -m image_metadata_cleaner "photo.jpg"
```

Choose a filename or process a folder and its subfolders:

```sh
python -m image_metadata_cleaner "photo.jpg" --output "clean/photo.png"
python -m image_metadata_cleaner "images" --recursive --output-dir "clean"
```

Preview paths without decoding or writing:

```sh
python -m image_metadata_cleaner "images" -r -d "clean" --dry-run
```

Extract one frame of an animation or one page of a TIFF as a still PNG:

```sh
python -m image_metadata_cleaner "animation.gif" --frame 0
python -m image_metadata_cleaner "scan.tiff" --frame 1
```

Frame numbers start at zero. For APNG with a separate default image, index zero is
that default image. Multiframe inputs require `--frame`; the tool does not silently
drop animation frames or document pages. In a batch, the selection applies to every
input and files without that frame report an error.

| Option | Behavior |
| --- | --- |
| `-o`, `--output FILE.png` | Choose a filename for a single input. |
| `-d`, `--output-dir DIR` | Choose a destination folder; retain relative subfolders. |
| `-r`, `--recursive` | Scan subfolders. |
| `--frame N` | Explicitly extract one frame/page. |
| `--overwrite` | Allow replacing existing output files. |
| `--dry-run` | Show planned paths only; does not validate image contents or write permissions. |
| `--max-pixels N` | Set a positive image size limit; default: 40,000,000 pixels. |
| `--version` | Show the package version. |

Existing files are protected unless `--overwrite` is supplied. The input file can
never be its own output, including a hard-link alias. Output symlinks are rejected.
The full PNG is written to a temporary file, verified, then published atomically.
Without `--overwrite`, an output created by another process during cleaning is also
protected. On POSIX, the output filesystem must support hard links for this final
publication step; Windows uses a rename that refuses existing destinations.

Folder scans match extensions without regard to case and skip symbolic links,
previous `*.clean.png` outputs, and the destination subtree when it is inside the
input folder. Filenames retain the original extension (`photo.jpg.clean.png` versus
`photo.png.clean.png`) to avoid collisions. A single explicitly named file is
identified by its contents. Batch errors do not prevent other files from running.

Exit codes: `0` for success, `1` for input/processing failures (including an empty
folder scan), and `2` for command-line syntax errors.

## Formats and image appearance

| Input | Output |
| --- | --- |
| JPEG, PNG, WebP, BMP, single-frame GIF, single-page TIFF | Still PNG |
| Animated GIF, WebP, APNG, or multipage TIFF | One explicitly selected frame/page as PNG |

WebP requires a Pillow build with WebP support; official Windows wheels include it.
HEIC/HEIF, AVIF, SVG, RAW camera formats, and other formats are not supported.

Output is 8-bit RGB or RGBA. Alpha transparency is preserved, including palette and
color-key transparency. EXIF rotation and mirroring are applied before metadata is
removed. Grayscale and palette images are expanded to RGB/RGBA. High-bit-depth PNG
and TIFF and unsupported pixel modes are rejected rather than silently truncated.

PNG compression adds no lossy compression to the decoded pixels. It cannot undo
existing JPEG/WebP loss, and PNG files can be much larger than the originals. ICC
profiles, gamma, chromaticity, HDR metadata, and DPI are removed. **Color appearance
can change in color-managed viewers.** CMYK is converted using Pillow's basic RGB
conversion, without ICC color management. This tool is not a color-proofing workflow.

## What is removed, and what remains

Removed from the new file:

- Embedded C2PA/Content Credentials containers, including JPEG APP11/JUMBF, PNG
  `caBX`, and WebP RIFF `C2PA` data.
- EXIF, GPS, XMP, IPTC, software tags, author/copyright fields, timestamps, comments,
  embedded thumbnails, and remote-manifest links stored in those metadata fields.
- PNG text, compressed text, international text, and generator-specific prompts,
  seeds, parameters, and workflow JSON stored as metadata.
- ICC/color-profile and resolution metadata, unknown ancillary data, and bytes
  appended after the source image.

The tool processes every supplied supported image. It does not attempt to classify
whether an image was made with AI. C2PA is a provenance system and is not itself an
AI classification.

Image dimensions, color representation, and compressed pixel data necessarily
remain. Visible watermarks, invisible watermarks encoded in pixels (such as
SynthID), steganography, visual AI characteristics, and content-based fingerprints
are **not removed**. External manifests, sidecar files, online provenance records,
filenames, and filesystem/cloud-provider metadata are also outside the tool's scope.
Consequently, a cleaned file is not guaranteed to be unidentifiable as AI-generated.
The original file and its credentials remain untouched.

Verification checks PNG chunk types/order, CRCs, absence of trailing data, and pixel
decodability. It does not validate C2PA signatures or certify that pixel data has no
embedded information. Pillow's decompression-bomb protections remain enabled even
when `--max-pixels` is raised. Decoding uses memory proportional to image size; the
pixel limit is not a hard process memory limit.

## Python API

```python
from image_metadata_cleaner import clean_image, verify_clean_png

result = clean_image("photo.jpg", "clean/photo.png")
print(result.destination, result.width, result.height)
verify_clean_png(result.destination)
```

`clean_image` also accepts keyword arguments `frame`, `overwrite`, and `max_pixels`.
It raises `CleanerError` for unsupported/invalid image content or verification
failures and `OSError` subclasses for filesystem failures. It never returns success
before verification and publication finish.

## Development and tests

After installing the project in editable mode:

```sh
python -m unittest discover -s tests -v
```

Tests generate their own small images. They cover synthetic C2PA carrier payloads,
other metadata, decoded pixel equality, rotation, transparency, frame selection,
unsupported inputs, corruption, batch paths, overwrite races, and failed-write
cleanup. The C2PA test payloads are container fixtures, not signed credentials.

```text
image_metadata_cleaner/
  src/image_metadata_cleaner/   # Python API and CLI
  tests/                       # Standard-library unittest suite
  pyproject.toml               # Package configuration and dependency
  README.md
  LICENSE
```

## Technical references

- [C2PA technical specification](https://spec.c2pa.org/specifications/specifications/2.2/specs/C2PA_Specification.html)
  defines embedded manifest locations and external provenance mechanisms.
- [PNG specification](https://www.w3.org/TR/png/) defines essential and ancillary chunks.
- [Pillow image formats](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html)
  and [EXIF orientation handling](https://pillow.readthedocs.io/en/stable/reference/ImageOps.html#PIL.ImageOps.exif_transpose)
  document the decoder and transformation behavior.

## License

This project is licensed under the **GNU General Public License, version 3 or (at
your option) any later version** (`GPL-3.0-or-later`). See [LICENSE](LICENSE) for the
full license text. The program is distributed without any warranty; see the license
for its terms. The canonical text is available from the
[GNU Project](https://www.gnu.org/licenses/gpl-3.0.html).
