# Image Metadata Cleaner

[English](#english) | [Português brasileiro](#português-brasileiro)

## English

A Python command-line tool that creates image copies without embedded descriptive
metadata, including AI generation labels, prompts, and Content Credentials (C2PA).

Choose **preserve** to keep supported pixels and colors (the default), **srgb** to
allow color-managed conversion, or **strip** to discard all color metadata. Output
is a new lossless PNG/WebP or a lossy JPEG. JPEG requires `srgb` or `strip` mode.
Before publishing, the tool verifies the file structure, expected encoded pixels,
transparency handling, and color data. EXIF orientation is
applied before descriptive metadata is discarded. Processing is local; the tool
makes no network requests.

### Requirements and installation

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

### Usage

Clean one image while preserving supported colors; this creates
`photo.jpg.clean.png` beside the original:

```sh
python -m image_metadata_cleaner "photo.jpg"
```

Choose the appearance policy explicitly:

```sh
python -m image_metadata_cleaner "photo.jpg" --mode preserve
python -m image_metadata_cleaner "photo.jpg" --mode srgb --format webp
python -m image_metadata_cleaner "untagged.png" --mode srgb --assume-srgb --format webp
python -m image_metadata_cleaner "photo.jpg" --mode strip
```

The second command requires an embedded ICC profile or a PNG sRGB declaration.
Use `--assume-srgb` only when an untagged image should be interpreted as sRGB; the
CLI reports this assumption. It never overrides an invalid profile.

| Mode | Pixel/color behavior | Suitable when |
| --- | --- | --- |
| `preserve` (default) | Keep decoded color values and supported color transforms; fail if they cannot be retained. | Preserving appearance is the priority. |
| `srgb` | Convert through the input ICC profile to standard sRGB; replace the source profile with standard color information. | You accept possible color/gamut changes for a standard color space. |
| `strip` | Discard all color declarations; use basic RGB/RGBA conversion if needed. | You require no color metadata and accept possible appearance changes. |

Format is a separate choice: `--format png` or `--format webp` for lossless output,
or `--format jpeg` (`jpg` is an alias) for lossy output.
Without `--format`, a single `--output` filename selects its format; otherwise PNG
is used. Explicit formats and filename extensions must agree. WebP output defaults
to `photo.jpg.clean.webp`; JPEG defaults to `photo.jpg.clean.jpg` and accepts `.jpg`
or `.jpeg` filenames. There is no interactive prompt; these options also work
in scripts and batches. Preserve mode has additional WebP restrictions below.

Choose JPEG with adjustable quality, or composite transparency onto a background:

```sh
python -m image_metadata_cleaner "profiled.jpg" --mode srgb --format jpeg --jpeg-quality 90
python -m image_metadata_cleaner "untagged.png" --mode srgb --assume-srgb -o "clean/photo.jpeg"
python -m image_metadata_cleaner "transparent.png" --mode strip --format jpg --background "#FFFFFF"
```

JPEG quality ranges from 1 to 95, defaulting to 95. Every setting is lossy, so
`preserve` rejects JPEG output. Transparent images require `--background "#RRGGBB"`;
quote the value, especially in PowerShell. Fully opaque images need no background.
`--jpeg-quality` and `--background` require JPEG output. The CLI reports lossy
encoding, including in dry-run plans; the API returns that notice in `CleanResult`.

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
| `-o`, `--output FILE` | Choose a `.png`, `.webp`, `.jpg`, or `.jpeg` filename for a single input. |
| `-d`, `--output-dir DIR` | Choose a destination folder; retain relative subfolders. |
| `-r`, `--recursive` | Scan subfolders. |
| `--frame N` | Explicitly extract one frame/page. |
| `--overwrite` | Allow replacing existing output files. |
| `--mode preserve/srgb/strip` | Set the appearance policy; default: `preserve`. |
| `--format png/webp/jpeg/jpg` | Select PNG/WebP (lossless) or JPEG (lossy, `srgb`/`strip` only). |
| `--jpeg-quality N` | JPEG quality from 1 to 95; default: 95. |
| `--background "#RRGGBB"` | JPEG background color; required when pixels have transparency. |
| `--assume-srgb` | With `--mode srgb`, explicitly interpret untagged RGB/gray pixels as sRGB. |
| `--strip-color` | Compatibility alias for `--mode strip`; conflicts with other explicit modes. |
| `--dry-run` | Show planned paths only; does not validate image contents or write permissions. |
| `--max-pixels N` | Set a positive image size limit; default: 40,000,000 pixels. |
| `--version` | Show the package version. |

Existing files are protected unless `--overwrite` is supplied. The input file can
never be its own output, including a hard-link alias. Output symlinks are rejected.
The full output is written to a temporary file, verified, then published atomically.
Without `--overwrite`, an output created by another process during cleaning is also
protected. On POSIX, the output filesystem must support hard links for this final
publication step; Windows uses a rename that refuses existing destinations.

Folder scans match extensions without regard to case and skip symbolic links,
previous `*.clean.png`, `*.clean.webp`, `*.clean.jpg`, and `*.clean.jpeg` outputs,
and the destination subtree when it is inside the
input folder. Filenames retain the original extension (`photo.jpg.clean.png` versus
`photo.png.clean.png`) to avoid collisions. A single explicitly named file is
identified by its contents. Batch errors do not prevent other files from running.

Exit codes: `0` for success, `1` for input/processing failures (including an empty
folder scan), and `2` for command-line syntax errors.

### Formats and image appearance

| Input | Output |
| --- | --- |
| JPEG, PNG, WebP, BMP, single-frame GIF, single-page TIFF | Still PNG, lossless WebP, or lossy JPEG |
| Animated GIF, WebP, APNG, or multipage TIFF | One explicitly selected frame/page as PNG, lossless WebP, or lossy JPEG |

WebP requires a Pillow build with WebP support; official Windows wheels include it.
HEIC/HEIF, AVIF, SVG, RAW camera formats, and other formats are not supported.

**The default prioritizes preserving the image's appearance.** PNG output is 8-bit RGB,
RGBA, grayscale (`L`), or grayscale with alpha (`LA`). Alpha transparency is
preserved, including palette and color-key transparency. EXIF rotation and
mirroring are applied before metadata is removed. Palette images are expanded to
RGB/RGBA; grayscale images keep grayscale channels so their profiles remain valid.
High-bit-depth PNG/TIFF and unsupported pixel modes are rejected.

Supported ICC v2/v4 RGB matrix/TRC and grayscale input/display profiles are rebuilt
with their numeric color transforms intact. Original descriptions, copyright text,
device identifiers, creation dates, profile IDs, padding, and unreferenced bytes
are removed or replaced with fixed values. The replacement profile still contains
color metadata: preserving appearance requires retaining the information that
defines what the pixel values mean. PNG gamma (`gAMA`), chromaticities (`cHRM`), and
standard sRGB declarations are also retained and validated. In `preserve` mode,
pixels are **not converted to sRGB**, so a supported wider-gamut profile keeps its
original gamut.

Malformed or mismatched profiles, profiles over 1 MiB, LUT-based profiles, unknown
ICC tags, HDR/CICP PNG declarations, and CMYK inputs produce an error by default.
The tool does not silently fall back to dropping their color information. Images
without an embedded profile remain untagged; no missing profile is guessed.

**sRGB conversion** uses Pillow's LittleCMS support with perceptual rendering
intent. It accepts RGB, gray, and CMYK profiles, including LUT transforms supported
by LittleCMS, after validating their headers and tag boundaries. The original ICC
profile is used for conversion and discarded. PNG receives only a fixed `sRGB`
color marker; WebP and JPEG receive a standard sRGB ICC profile with fixed generic fields.
Color values can change, and colors outside sRGB may be clipped or mapped. The
oriented image dimensions are preserved; alpha is preserved in PNG/WebP and
composited onto an explicit background for JPEG. This mode requires a Pillow build
with LittleCMS support.

Untagged RGB/gray inputs require `--assume-srgb`; missing CMYK profiles cannot be
assumed. An existing PNG sRGB declaration needs no assumption. Invalid, mismatched,
or oversized profiles and HDR/CICP PNG declarations still fail. Conversion from
PNG gamma/chromaticities alone is unsupported; use `preserve` for those inputs.

**Lossless WebP** supports RGB/RGBA and can produce smaller files than PNG, but
size savings are not guaranteed. It keeps RGB values even under fully transparent
pixels. Its maximum output width and height are 16,383 pixels. In `preserve` mode,
supported RGB ICC profiles are retained, and a lone PNG sRGB declaration becomes
an equivalent standard ICC profile. Untagged gray images expand to RGB/RGBA.
Grayscale ICC profiles and PNG gamma/chromaticity chunks cannot be carried over
unchanged: the tool reports an error directing you to PNG or sRGB conversion.
The sRGB mode can convert a valid ICC profile even when fallback PNG gamma data
is also present.

**JPEG output** uses baseline 8-bit RGB encoding with full chroma resolution
(4:4:4), fixed Huffman tables, and the selected quality. It requires Pillow's JPEG
support and allows dimensions up to 65,500 pixels per side, subject to the pixel
limit. It always re-encodes the decoded image, including JPEG inputs, and cannot
guarantee identical pixels or appearance. Progressive output and lossless copying
of JPEG coefficients are not implemented. JPEG's fixed JFIF header contains no
thumbnail or source DPI; its density fields specify only a 1:1 pixel aspect ratio.

In `srgb`, the input is converted first, and the output carries only the standard
sRGB ICC profile. In `strip`, no ICC profile is written. Transparent pixels are
composited against the chosen background in the output RGB space using their alpha
values, then the result is JPEG-encoded. For `srgb`, the background is an sRGB hex
color; `strip` uses the same numeric RGB values without a profile. This uses Pillow's
ordinary channel blending, not linear-light compositing. Alpha is discarded only
after compositing, or when every pixel is fully opaque. Use PNG/WebP when
transparency or exact expected pixel values must survive.

PNG and WebP encoding add no compression loss to the **expected output pixels**:
oriented/expanded input pixels in `preserve`, converted pixels in `srgb`, or basic
RGB/RGBA pixels in `strip`. Neither format can undo existing JPEG/WebP loss. Exact
pixel equality and preserved color transforms support matching appearance in the
same color-managed viewer with the same settings. They do **not** guarantee
identical rendering across all decoders, viewers, displays, or print workflows.
DPI and other layout metadata are still removed; physical print dimensions can
change. Animation remains an explicitly selected still frame.

`--mode strip` (or `--strip-color`) restores the original behavior: all
color metadata is discarded and pixels are expanded/converted to RGB/RGBA. CMYK
then uses Pillow's basic RGB conversion. **This option can change colors** and
should not be used when preserving appearance is the priority.

### What is removed, and what remains

Removed from the new file:

- Embedded C2PA/Content Credentials containers, including JPEG APP11/JUMBF, PNG
  `caBX`, and WebP RIFF `C2PA` data.
- EXIF, GPS, XMP, IPTC, software tags, author/copyright fields, timestamps, comments,
  embedded thumbnails, and remote-manifest links stored in those metadata fields.
- PNG text, compressed text, international text, and generator-specific prompts,
  seeds, parameters, and workflow JSON stored as metadata.
- Original descriptive ICC fields, resolution metadata, unknown ancillary image
  data, and bytes appended after the source image. Supported numeric color data
  remains in `preserve`, is replaced with standard sRGB information in `srgb`,
  and is discarded in `strip`.

The tool processes every supplied supported image. It does not attempt to classify
whether an image was made with AI. C2PA is a provenance system and is not itself an
AI classification.

Image dimensions, color representation, and compressed pixel data necessarily
remain. Visible watermarks, invisible watermarks encoded in pixels (such as
SynthID), steganography, visual AI characteristics, and content-based fingerprints
are **not removed**. External manifests, sidecar files, online provenance records,
filenames, and filesystem/cloud-provider metadata are also outside the tool's scope.
Consequently, a cleaned file is not guaranteed to be unidentifiable as AI-generated.
The original file and its credentials remain untouched. Retained numeric color
curves/matrices can also carry deliberately encoded information; the tool cannot
certify their absence, just as it cannot certify the absence of pixel watermarks.

Verification checks PNG chunk types/order, CRCs, and exactly one complete zlib
stream across all `IDAT` chunks. It rejects unused bytes after that stream, extra
compressed streams, missing or excess decoded image bytes, invalid scanline
filters, and trailing file data. Pixel decompression uses buffers of at most 64 KiB,
and the verified image is also decoded with Pillow. In addition to `IHDR`, `IDAT`,
and `IEND`, the default permits only validated `iCCP`, `sRGB`, `gAMA`, and `cHRM`
chunks before the pixel data. ICC decompression is limited to 1 MiB and rejects
trailing streams; the profile must match the sanitizer's canonical representation.
With `strip_color=True`, PNG verification rejects every optional chunk. The cleaning
operation additionally compares the written pixels against the selected mode's
expected pixels and checks the color data before
publishing. Verification cannot certify that numeric data carries no embedded
information, and the cleaning command does not validate C2PA signatures.

WebP verification permits only one lossless `VP8L` chunk, optionally preceded by
`VP8X` and a canonical `ICCP` profile. It checks RIFF size, chunk order, flags,
padding, dimensions, profile contents, and the absence of trailing data. It then
re-encodes the decoded image with fixed lossless settings and compares every byte,
rejecting ignored payloads inside `VP8L` as well. This costs an extra encode and is
intentionally tied to the installed libwebp version/settings: it is a verifier for
this tool's current output, not a general validator for arbitrary lossless WebP.
`strip_color=True` additionally forbids the ICC profile.

JPEG verification requires the installed encoder's fixed JFIF, quantization,
Huffman, frame, and scan headers for the supplied quality. It permits only one
optional APP2 segment containing the standard sRGB profile; `strip_color=True`
forbids it. EXIF/XMP, C2PA APP11, IPTC, comments, thumbnails, duplicate segments,
and extra scans are rejected. The verifier walks every Huffman-coded block and
checks byte stuffing, coefficient runs, final padding, the end marker, and absence
of unused scan bytes or trailing data, then decodes with Pillow. This CPU work can
be noticeable for large, detailed images. It validates this tool's baseline 4:4:4
output, not arbitrary JPEG files, and is tied to the installed encoder/settings.
During cleaning, a reference JPEG encode/decode of the expected pixels provides
the pixel comparison, accounting for the selected quality's compression loss.
That adds an encode/decode pass; it does not add another generation of loss to the
published file. The standard profile is also checked before publication.

The pixel limit is checked as soon as Pillow exposes the initial dimensions,
before scanning frame counts or seeking. Frame selection advances one frame at a
time and checks dimensions before and after each step, so an oversized intermediate
GIF canvas or TIFF page is rejected before it can be decoded by a subsequent seek.
The same limit applies during PNG, WebP, and JPEG verification. Thus, selecting a small final TIFF
page does not bypass the limit on an earlier, oversized page. Header parsing and
decoder setup may still allocate memory; the pixel limit is not a hard process
memory or execution-time limit. Pillow's decompression-bomb protections remain
enabled even when `--max-pixels` is raised.

### Python API

```python
from image_metadata_cleaner import clean_image, verify_clean_png, verify_clean_webp, verify_clean_jpeg

result = clean_image("photo.jpg", "clean/photo.png", mode="preserve")
print(result.destination, result.width, result.height)
verify_clean_png(result.destination)

converted = clean_image("profiled.jpg", "clean/photo.webp", mode="srgb")
verify_clean_webp(converted.destination)

untagged = clean_image("untagged.png", mode="srgb", output_format="webp", assume_srgb=True)
print(untagged.mode, untagged.output_format, untagged.notices)

jpeg = clean_image("transparent.png", "clean/photo.jpg", mode="srgb", assume_srgb=True,
                   jpeg_quality=90, background="#FFFFFF")
verify_clean_jpeg(jpeg.destination, jpeg_quality=90)
```

`clean_image` accepts `mode` (`preserve`, `srgb`, `strip`), `output_format` (`png`,
`webp`, `jpeg`, or alias `jpg`), `assume_srgb` (default `False`), `frame`, `overwrite`,
`max_pixels`, `jpeg_quality` (1–95, default 95 for JPEG), `background` (a `#RRGGBB`
string, default `None`), and the
compatible `strip_color` alias (default `False`). An omitted mode resolves to
`preserve`, or `strip` with the alias. `CleanResult` includes `output_format`,
`mode`, and a tuple of `notices`; the library returns notices without printing.
All three `verify_clean_*` functions accept `max_pixels` with the same
40,000,000-pixel default and `strip_color=True` to forbid optional color metadata.
`verify_clean_jpeg` also accepts `jpeg_quality` (default 95); supply the same quality
used to create the file. It retains the fixed JFIF image representation header.
The API raises `CleanerError` for unsupported/invalid image content or verification
failures and `OSError` subclasses for filesystem failures. It never returns success
before verification and publication finish.

### Development and tests

After installing the project in editable mode:

```sh
python -m unittest discover -s tests -v
```

Tests cover metadata removal, decoded pixel equality, rotation, transparency,
frame selection, unsupported inputs, corruption, batch paths, overwrite races,
and failed-write cleanup. PNG regressions cover hidden data inside `IDAT`, multiple
or truncated zlib streams, scanline sizes/filters, chunk boundaries, and bounded
decompression. Frame-limit regressions check that oversized animation frames and
intermediate TIFF pages are rejected before their pixel data is decoded.

Color tests compare both raw pixels and LittleCMS-rendered pixels before and after
cleaning, across all four rendering intents, using RGB and grayscale profiles,
alpha, orientation, and multiple input formats. They also check ICC descriptive
field removal, gamma/chromaticity preservation, malformed profiles, unsupported
color transforms, and refusal to publish if an encoder changes pixels or loses a
profile. Conversion tests compare RGB, gray, alpha, and synthetic RGB/CMYK LUT
profiles against LittleCMS. WebP tests cover hidden RGB under transparent pixels,
metadata, internal bitstream tails, format/mode choices, and batch naming. These
tests require a Pillow build with LittleCMS, WebP, and JPEG support. JPEG tests
cover all 95 quality settings, reference encodes after ICC conversion and alpha
compositing, extension aliases, CLI batches, forbidden metadata, malformed headers,
entropy padding/overruns, hidden scan tails, limits, and failed publication.

The suite includes two unmodified, signed JPEG fixtures from the official C2PA
SDK repository, with pinned source URLs, checksums, and license notices in
[tests/fixtures](tests/fixtures/README.md). Synthetic fixtures also exercise PNG
and WebP metadata containers. Install the optional test dependency to validate the
JPEG signatures and asset hashes independently and confirm that the cleaned
PNG/WebP outputs in all three modes and JPEG outputs in `srgb`/`strip` have no C2PA manifest:

```sh
python -m pip install -e ".[test]"
python -m unittest discover -s tests -v
```

Without that extra, the independent SDK test is explicitly skipped; the signed
fixture checksum and cleaning tests still run. SDK tests disable remote-manifest
and OCSP fetching and do not download trust lists. The test SDK is not required by
the cleaning command.

[GitHub Actions](.github/workflows/tests.yml) is configured for Windows, macOS, and
Linux with Python 3.10 through 3.14, plus a minimum-Pillow check on Python 3.10.
CI requires the independent C2PA test to run. A packaging job builds a wheel and
source archive and runs the tests from the archive against the installed wheel,
including its bundled fixtures. These jobs run when this project is the root of a
GitHub repository with Actions enabled; adding the workflow locally does not run
the remote platform matrix.

```text
image_metadata_cleaner/
  src/image_metadata_cleaner/   # Python API and CLI
  tests/                       # Standard-library unittest suite
  .github/workflows/tests.yml   # Platform/version and packaging checks
  MANIFEST.in                  # Include test fixtures in source archives
  pyproject.toml               # Package configuration and dependency
  README.md
  LICENSE
```

### Technical references

- [C2PA technical specification](https://spec.c2pa.org/specifications/specifications/2.2/specs/C2PA_Specification.html)
  defines embedded manifest locations and external provenance mechanisms.
- [PNG specification](https://www.w3.org/TR/png/) defines essential and ancillary chunks.
- [ICC profile specification](https://archive.color.org/specification/ICC.1-2022-05.pdf)
  defines color transforms, profile headers, and descriptive tags.
- [Pillow color management](https://pillow.readthedocs.io/en/stable/reference/ImageCms.html)
  describes ICC conversion and rendering intents.
- [WebP container specification](https://developers.google.com/speed/webp/docs/riff_container)
  defines lossless image chunks, ICC profiles, and RIFF structure.
- [JPEG T.81 specification](https://www.w3.org/Graphics/JPEG/itu-t81.pdf)
  defines baseline scans, Huffman decoding, byte stuffing, and padding.
- [Pillow image formats](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html)
  and [EXIF orientation handling](https://pillow.readthedocs.io/en/stable/reference/ImageOps.html#PIL.ImageOps.exif_transpose)
  document the decoder and transformation behavior.

### License

This project is licensed under the **GNU General Public License, version 3 or (at
your option) any later version** (`GPL-3.0-or-later`). See [LICENSE](LICENSE) for the
full license text. The program is distributed without any warranty; see the license
for its terms. The canonical text is available from the
[GNU Project](https://www.gnu.org/licenses/gpl-3.0.html).

---

## Português brasileiro

Uma ferramenta de linha de comando em Python que cria cópias de imagens sem
metadados descritivos incorporados, incluindo identificações de geração por IA,
prompts e Credenciais de Conteúdo (C2PA).

Escolha **preserve** para manter os pixels e as cores compatíveis (o padrão),
**srgb** para permitir a conversão com gerenciamento de cores ou **strip** para
descartar todos os metadados de cor. A saída é um novo PNG/WebP sem perdas ou JPEG
com perdas. JPEG exige o modo `srgb` ou `strip`. Antes de disponibilizar o resultado,
a ferramenta verifica a estrutura do arquivo, os pixels codificados esperados,
o tratamento da transparência e os dados de cor. A orientação EXIF é aplicada
antes de descartar os metadados descritivos. O processamento é local; a ferramenta
não faz requisições de rede.

### Requisitos e instalação

- Python 3.10 ou mais recente.
- Pillow 12.1.1 ou mais recente, abaixo da versão 13 (instalado automaticamente).
- Windows, macOS ou Linux. Não é necessário instalar o ExifTool.

No PowerShell:

```powershell
cd C:\Users\pedro\OneDrive\Documentos\Dev\image_metadata_cleaner
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m image_metadata_cleaner --help
```

Se `py` não estiver disponível, use o executável `python` da sua instalação do
Python no comando de criação do ambiente virtual. A ativação do ambiente é opcional.

No macOS ou Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m image_metadata_cleaner --help
```

Os exemplos abaixo usam o `python` do ambiente virtual. No PowerShell, você pode
substituir `python` por `.\.venv\Scripts\python.exe`. O comando de console instalado,
`image-metadata-cleaner`, oferece a mesma interface.

### Uso

Limpe uma imagem preservando as cores compatíveis; isso cria `photo.jpg.clean.png`
ao lado do arquivo original:

```sh
python -m image_metadata_cleaner "photo.jpg"
```

Escolha explicitamente como tratar a aparência:

```sh
python -m image_metadata_cleaner "photo.jpg" --mode preserve
python -m image_metadata_cleaner "photo.jpg" --mode srgb --format webp
python -m image_metadata_cleaner "untagged.png" --mode srgb --assume-srgb --format webp
python -m image_metadata_cleaner "photo.jpg" --mode strip
```

O segundo comando exige um perfil ICC incorporado ou uma declaração sRGB de PNG.
Use `--assume-srgb` apenas quando uma imagem sem perfil deva ser interpretada como
sRGB; a interface informa essa suposição. Ela nunca substitui um perfil inválido.

| Modo | Comportamento dos pixels e das cores | Quando usar |
| --- | --- | --- |
| `preserve` (padrão) | Mantém os valores de cor decodificados e as transformações compatíveis; gera erro se não puder mantê-los. | Preservar a aparência é a prioridade. |
| `srgb` | Converte pelo perfil ICC de entrada para sRGB padrão; substitui o perfil original por informações de cor padronizadas. | Você aceita possíveis mudanças de cor ou de gama de cores para usar um espaço padronizado. |
| `strip` | Descarta todas as declarações de cor; usa conversão básica para RGB/RGBA quando necessário. | Você exige a ausência de metadados de cor e aceita possíveis mudanças de aparência. |

O formato é uma escolha separada: `--format png` ou `--format webp` para saída sem
perdas, ou `--format jpeg` (`jpg` é um alias) para saída com perdas.
Sem `--format`, o nome informado em `--output` para uma única imagem define
o formato; nos demais casos, é usado PNG. O formato explícito e a extensão precisam
coincidir. O nome padrão para WebP é `photo.jpg.clean.webp`; para JPEG, é
`photo.jpg.clean.jpg`, e nomes com `.jpg` ou `.jpeg` são aceitos. Não há perguntas
interativas; as opções também funcionam em scripts e lotes. O modo `preserve` tem
restrições adicionais para WebP, descritas abaixo.

Escolha JPEG com qualidade ajustável ou componha a transparência sobre um fundo:

```sh
python -m image_metadata_cleaner "profiled.jpg" --mode srgb --format jpeg --jpeg-quality 90
python -m image_metadata_cleaner "untagged.png" --mode srgb --assume-srgb -o "clean/photo.jpeg"
python -m image_metadata_cleaner "transparent.png" --mode strip --format jpg --background "#FFFFFF"
```

A qualidade JPEG varia de 1 a 95, com padrão 95. Todas as opções têm perdas;
por isso, `preserve` rejeita saída JPEG. Imagens transparentes exigem
`--background "#RRGGBB"`; coloque o valor entre aspas, especialmente no PowerShell.
Imagens totalmente opacas dispensam fundo. `--jpeg-quality` e `--background`
exigem saída JPEG. A interface informa a codificação com perdas, inclusive nos
planos de simulação; a API retorna esse aviso em `CleanResult`.

Escolha um nome de arquivo ou processe uma pasta e suas subpastas:

```sh
python -m image_metadata_cleaner "photo.jpg" --output "clean/photo.png"
python -m image_metadata_cleaner "images" --recursive --output-dir "clean"
```

Visualize os caminhos previstos sem decodificar nem gravar arquivos:

```sh
python -m image_metadata_cleaner "images" -r -d "clean" --dry-run
```

Extraia um quadro de uma animação ou uma página de um TIFF como PNG estático:

```sh
python -m image_metadata_cleaner "animation.gif" --frame 0
python -m image_metadata_cleaner "scan.tiff" --frame 1
```

A numeração dos quadros começa em zero. Em um APNG com uma imagem padrão separada,
o índice zero corresponde a essa imagem. Arquivos com vários quadros ou páginas
exigem `--frame`; a ferramenta não descarta silenciosamente quadros da animação ou
páginas do documento. No processamento em lote, a seleção se aplica a todos os
arquivos de entrada, e aqueles que não possuem o quadro solicitado geram um erro.

| Opção | Comportamento |
| --- | --- |
| `-o`, `--output FILE` | Escolhe um nome `.png`, `.webp`, `.jpg` ou `.jpeg` para uma única entrada. |
| `-d`, `--output-dir DIR` | Escolhe uma pasta de destino e mantém a estrutura relativa das subpastas. |
| `-r`, `--recursive` | Percorre as subpastas. |
| `--frame N` | Extrai explicitamente um quadro ou uma página. |
| `--overwrite` | Permite substituir arquivos de saída existentes. |
| `--mode preserve/srgb/strip` | Define como tratar a aparência; padrão: `preserve`. |
| `--format png/webp/jpeg/jpg` | Seleciona PNG/WebP (sem perdas) ou JPEG (com perdas, apenas `srgb`/`strip`). |
| `--jpeg-quality N` | Qualidade JPEG de 1 a 95; padrão: 95. |
| `--background "#RRGGBB"` | Cor de fundo para JPEG; obrigatória quando há pixels transparentes. |
| `--assume-srgb` | Com `--mode srgb`, interpreta explicitamente pixels RGB/cinza sem perfil como sRGB. |
| `--strip-color` | Opção compatível com versões anteriores, equivalente a `--mode strip`; conflita com outros modos explícitos. |
| `--dry-run` | Mostra apenas os caminhos previstos; não valida o conteúdo das imagens nem as permissões de gravação. |
| `--max-pixels N` | Define um limite positivo para o tamanho da imagem; padrão: 40.000.000 de pixels. |
| `--version` | Mostra a versão do pacote. |

Arquivos existentes são protegidos, a menos que `--overwrite` seja informado. O
arquivo de entrada nunca pode ser seu próprio destino, inclusive por meio de um
link físico para o mesmo arquivo. Arquivos de saída que sejam links simbólicos são
rejeitados. O arquivo completo é gravado em um arquivo temporário, verificado e
então colocado no destino de forma atômica. Sem `--overwrite`, um arquivo de saída
criado por outro processo durante a limpeza também é protegido. Em sistemas POSIX,
o sistema de arquivos de destino precisa oferecer suporte a links físicos para
essa etapa final; no Windows, é usada uma operação de renomeação que recusa destinos
existentes.

A busca em pastas identifica extensões sem diferenciar maiúsculas de minúsculas e
ignora links simbólicos, saídas anteriores `*.clean.png`, `*.clean.webp`,
`*.clean.jpg` e `*.clean.jpeg` e a árvore de
destino quando ela está dentro da pasta de entrada. Os nomes mantêm a extensão
original (`photo.jpg.clean.png` e `photo.png.clean.png`) para evitar conflitos. Um
arquivo informado individualmente é identificado pelo conteúdo. Erros em um lote
não impedem o processamento dos demais arquivos.

Códigos de saída: `0` para sucesso, `1` para falhas de entrada ou processamento
(incluindo uma busca em pasta sem arquivos encontrados) e `2` para erros de sintaxe
na linha de comando.

### Formatos e aparência das imagens

| Entrada | Saída |
| --- | --- |
| JPEG, PNG, WebP, BMP, GIF de um único quadro, TIFF de uma única página | PNG estático, WebP sem perdas ou JPEG com perdas |
| GIF animado, WebP animado, APNG ou TIFF com várias páginas | Um quadro ou uma página explicitamente selecionado, em PNG, WebP sem perdas ou JPEG com perdas |

WebP exige uma compilação do Pillow com suporte a WebP; os pacotes wheel oficiais
para Windows incluem esse suporte. HEIC/HEIF, AVIF, SVG, formatos RAW de câmeras e
outros formatos não são compatíveis.

**O comportamento padrão prioriza preservar a aparência da imagem.** A saída PNG é RGB,
RGBA, escala de cinza (`L`) ou escala de cinza com alfa (`LA`), de 8 bits. A
transparência alfa é preservada, incluindo transparência por paleta e por cor-chave.
A rotação e o espelhamento definidos no EXIF são aplicados antes da remoção dos
metadados. Imagens com paleta são expandidas para RGB/RGBA; imagens em escala de
cinza mantêm seus canais para que os perfis continuem válidos. PNGs e TIFFs com
maior profundidade de bits e modos de pixel não compatíveis são rejeitados.

Perfis ICC v2/v4 de entrada ou exibição compatíveis, RGB baseados em matrizes e
curvas tonais (TRC) ou em escala de cinza, são reconstruídos com suas transformações
numéricas de cor intactas. Descrições originais, textos de direitos autorais,
identificadores de dispositivos, datas de criação, IDs de perfil, preenchimento e
bytes não referenciados são removidos ou substituídos por valores fixos. O perfil
substituto ainda contém metadados de cor: preservar a aparência exige manter as
informações que definem o significado dos valores dos pixels. Gama (`gAMA`),
cromaticidades (`cHRM`) e declarações de sRGB padrão de PNG também são mantidas e
validadas. No modo `preserve`, os pixels **não são convertidos para sRGB**, portanto
um perfil compatível com gama de cores mais ampla mantém sua gama original.

Perfis malformados ou incompatíveis com a imagem, perfis acima de 1 MiB, perfis
baseados em tabelas de consulta (LUT), tags ICC desconhecidas, declarações HDR/CICP
de PNG e entradas CMYK geram um erro por padrão. A ferramenta não descarta essas
informações de cor silenciosamente como alternativa. Imagens sem perfil incorporado
continuam sem perfil; nenhum perfil ausente é presumido.

**A conversão para sRGB** usa o suporte a LittleCMS do Pillow com intenção de
renderização perceptual. Aceita perfis RGB, de escala de cinza e CMYK, incluindo
transformações LUT compatíveis com LittleCMS, após validar seus cabeçalhos e os
limites das tags. O perfil ICC original é usado na conversão e descartado. O PNG
recebe apenas um marcador de cor `sRGB` fixo; WebP e JPEG recebem um perfil ICC sRGB padrão
com campos genéricos fixos. Os valores de cor podem mudar, e cores fora do espaço
sRGB podem ser limitadas ou mapeadas. As dimensões da imagem orientada são
preservadas; o alfa é mantido em PNG/WebP e composto sobre um fundo explícito em
JPEG. Esse modo exige uma compilação do Pillow com suporte a LittleCMS.

Entradas RGB/cinza sem perfil exigem `--assume-srgb`; não é possível presumir um
perfil para CMYK. Uma declaração sRGB de PNG existente dispensa essa suposição.
Perfis inválidos, incompatíveis com a imagem ou grandes demais e declarações
HDR/CICP de PNG continuam gerando erro. A conversão baseada apenas em gama ou
cromaticidades de PNG não é compatível; use `preserve` para essas entradas.

**WebP sem perdas** aceita RGB/RGBA e pode gerar arquivos menores que PNG, mas não
há garantia de redução de tamanho. Mantém os valores RGB mesmo em pixels totalmente
transparentes. A largura e a altura máximas da saída são de 16.383 pixels. No modo
`preserve`, perfis ICC RGB compatíveis são mantidos, e uma declaração sRGB isolada
de PNG é substituída por um perfil ICC padrão equivalente. Imagens em escala de
cinza sem perfil são expandidas para RGB/RGBA. Perfis ICC de escala de cinza e
blocos de gama/cromaticidade de PNG não podem ser transferidos sem alterações: a
ferramenta gera um erro indicando o uso de PNG ou da conversão para sRGB. O modo
sRGB consegue converter um perfil ICC válido mesmo quando também há dados de gama
de PNG destinados a visualizadores sem suporte ao perfil.

**A saída JPEG** usa codificação baseline RGB de 8 bits, com resolução completa de
crominância (4:4:4), tabelas Huffman fixas e a qualidade selecionada. Exige suporte
a JPEG no Pillow e permite dimensões de até 65.500 pixels por lado, respeitando o
limite de pixels. Sempre recodifica a imagem decodificada, inclusive entradas JPEG,
e não garante pixels ou aparência idênticos. Saída progressiva e cópia sem perdas
dos coeficientes JPEG não foram implementadas. O cabeçalho JFIF fixo não contém
miniatura nem o DPI original; seus campos de densidade indicam apenas proporção
de pixels de 1:1.

Em `srgb`, a entrada é convertida primeiro, e a saída contém apenas o perfil ICC
sRGB padrão. Em `strip`, nenhum perfil ICC é gravado. Pixels transparentes são
compostos sobre o fundo escolhido no espaço RGB de saída, usando seus valores
alfa; depois, o resultado é codificado em JPEG. Em `srgb`, o fundo é uma cor
hexadecimal sRGB; `strip` usa os mesmos valores RGB numéricos sem perfil. É usada
a mistura comum de canais do Pillow, sem composição em luz linear. O alfa só é
descartado após a composição ou quando todos os pixels são totalmente opacos.
Use PNG/WebP quando precisar manter a transparência ou os valores exatos dos
pixels esperados.

A codificação PNG e WebP não acrescenta perdas de compressão aos **pixels de saída
esperados**: pixels de entrada orientados/expandidos em `preserve`, pixels
convertidos em `srgb` ou pixels RGB/RGBA básicos em `strip`. Nenhum dos formatos
recupera perdas já existentes em JPEG/WebP. A igualdade exata dos pixels e a preservação das
transformações de cor permitem manter a aparência no mesmo visualizador com
gerenciamento de cores e as mesmas configurações. Isso **não** garante renderização
idêntica em todos os decodificadores, visualizadores, monitores ou fluxos de
impressão. DPI e outros metadados de diagramação ainda são removidos; as dimensões
físicas de impressão podem mudar. Animações continuam sendo extraídas como um quadro
estático selecionado explicitamente.

`--mode strip` (ou `--strip-color`) restaura o comportamento original:
todos os metadados de cor são descartados e os pixels são expandidos ou convertidos
para RGB/RGBA. Nesse caso, CMYK usa a conversão básica para RGB do Pillow. **Essa
opção pode alterar as cores** e não deve ser usada quando preservar a aparência é
a prioridade.

### O que é removido e o que permanece

São removidos do novo arquivo:

- Contêineres incorporados de C2PA/Credenciais de Conteúdo, incluindo JPEG
  APP11/JUMBF, `caBX` de PNG e dados `C2PA` em RIFF de WebP.
- EXIF, GPS, XMP, IPTC, identificações de software, campos de autoria e direitos
  autorais, datas e horários, comentários, miniaturas incorporadas e links para
  manifestos remotos armazenados nesses campos de metadados.
- Texto simples, texto comprimido e texto internacional de PNG, além de prompts,
  sementes, parâmetros e fluxos de trabalho em JSON específicos de geradores,
  quando armazenados como metadados.
- Campos descritivos originais dos perfis ICC, metadados de resolução, dados
  auxiliares desconhecidos da imagem e bytes acrescentados após a imagem original.
  Dados numéricos de cor compatíveis permanecem em `preserve`, são substituídos
  por informações sRGB padronizadas em `srgb` e são descartados em `strip`.

A ferramenta processa todas as imagens fornecidas que tenham um formato compatível.
Ela não tenta classificar se uma imagem foi feita com IA. C2PA é um sistema de
proveniência e, por si só, não é uma classificação de IA.

As dimensões da imagem, a representação de cores e os dados comprimidos dos pixels
necessariamente permanecem. Marcas-d'água visíveis, marcas-d'água invisíveis
codificadas nos pixels (como o SynthID), esteganografia, características visuais de
IA e identificadores baseados no conteúdo **não são removidos**. Manifestos externos,
arquivos auxiliares separados, registros de proveniência online, nomes de arquivos
e metadados do sistema de arquivos ou do provedor de nuvem também estão fora do
escopo da ferramenta. Portanto, não há garantia de que um arquivo limpo deixe de
ser identificável como gerado por IA. O arquivo original e suas credenciais
permanecem intactos. Curvas e matrizes numéricas de cor mantidas também podem conter
informações codificadas deliberadamente; a ferramenta não pode certificar sua
ausência, assim como não pode certificar a ausência de marcas-d'água nos pixels.

A verificação confere os tipos e a ordem dos blocos PNG, os CRCs e a presença de
exatamente um fluxo zlib completo ao longo de todos os blocos `IDAT`. Ela rejeita
bytes não utilizados após esse fluxo, fluxos comprimidos adicionais, quantidade
insuficiente ou excessiva de bytes de imagem decodificados, filtros de linha
inválidos e dados após o fim do arquivo. A descompressão dos pixels usa buffers de
no máximo 64 KiB, e a imagem verificada também é decodificada com o Pillow. Além de
`IHDR`, `IDAT` e `IEND`, o padrão permite apenas blocos `iCCP`, `sRGB`, `gAMA` e
`cHRM` validados antes dos dados dos pixels. A descompressão ICC é limitada a 1 MiB
e rejeita fluxos adicionais; o perfil precisa corresponder à representação
canônica produzida pela limpeza. Com `strip_color=True`, a verificação de PNG rejeita
todos os blocos opcionais. A operação de limpeza também compara os pixels gravados
com os pixels esperados pelo modo selecionado e verifica os dados de cor antes de
disponibilizar o resultado. A verificação não pode
certificar que dados numéricos não contêm informações incorporadas, e o comando de
limpeza não valida assinaturas C2PA.

A verificação de WebP permite apenas um bloco `VP8L` sem perdas, opcionalmente
precedido por `VP8X` e um perfil `ICCP` canônico. Confere o tamanho RIFF, a ordem dos
blocos, os sinalizadores, o preenchimento, as dimensões, o conteúdo do perfil e a
ausência de dados após o fim do arquivo. Em seguida, recodifica a imagem
decodificada com configurações fixas sem perdas e compara todos os bytes,
rejeitando também conteúdo ignorado dentro de `VP8L`. Isso exige uma codificação
adicional e depende intencionalmente da versão/configuração do libwebp instalado:
é uma verificação da saída atual desta ferramenta, não um validador geral de
qualquer WebP sem perdas. `strip_color=True` também proíbe o perfil ICC.

A verificação de JPEG exige os cabeçalhos fixos de JFIF, quantização, Huffman,
quadro e varredura do codificador instalado para a qualidade informada. Permite
apenas um segmento APP2 opcional com o perfil sRGB padrão; `strip_color=True` o
proíbe. EXIF/XMP, C2PA APP11, IPTC, comentários, miniaturas, segmentos duplicados
e varreduras extras são rejeitados. O verificador percorre cada bloco codificado
com Huffman e confere os bytes de escape, as sequências de coeficientes, o
preenchimento final, o marcador de fim e a ausência de bytes não utilizados na
varredura ou após o arquivo; depois, decodifica com Pillow. Esse trabalho de CPU
pode ser perceptível em imagens grandes e detalhadas. Ele valida a saída baseline
4:4:4 desta ferramenta, não arquivos JPEG arbitrários, e depende do codificador
instalado e de suas configurações. Durante a limpeza, uma codificação e
decodificação JPEG de referência dos pixels esperados fornece a comparação de
pixels, considerando as perdas da qualidade escolhida. Isso acrescenta uma etapa
de codificação e decodificação, sem acrescentar outra geração de perdas ao arquivo
final. O perfil padrão também é conferido antes de disponibilizar a saída.

O limite de pixels é verificado assim que o Pillow disponibiliza as dimensões
iniciais, antes da contagem de quadros ou da navegação entre eles. A seleção avança
um quadro por vez e verifica as dimensões antes e depois de cada etapa. Assim, uma
área de imagem intermediária de GIF ou uma página de TIFF que exceda o limite é
rejeitada antes de poder ser decodificada durante um avanço posterior. O mesmo
limite se aplica à verificação de PNG, WebP e JPEG. Portanto, selecionar uma página final
pequena de um TIFF não contorna o limite de uma página anterior grande demais. A
leitura dos cabeçalhos e a preparação do decodificador ainda podem alocar memória;
o limite de pixels não é um limite rígido de memória do processo nem de tempo de
execução. As proteções do Pillow contra bombas de descompressão permanecem ativas
mesmo quando `--max-pixels` é aumentado.

### API Python

```python
from image_metadata_cleaner import clean_image, verify_clean_png, verify_clean_webp, verify_clean_jpeg

result = clean_image("photo.jpg", "clean/photo.png", mode="preserve")
print(result.destination, result.width, result.height)
verify_clean_png(result.destination)

converted = clean_image("profiled.jpg", "clean/photo.webp", mode="srgb")
verify_clean_webp(converted.destination)

untagged = clean_image("untagged.png", mode="srgb", output_format="webp", assume_srgb=True)
print(untagged.mode, untagged.output_format, untagged.notices)

jpeg = clean_image("transparent.png", "clean/photo.jpg", mode="srgb", assume_srgb=True,
                   jpeg_quality=90, background="#FFFFFF")
verify_clean_jpeg(jpeg.destination, jpeg_quality=90)
```

`clean_image` aceita `mode` (`preserve`, `srgb`, `strip`), `output_format` (`png`,
`webp`, `jpeg` ou alias `jpg`), `assume_srgb` (padrão `False`), `frame`, `overwrite`,
`max_pixels`, `jpeg_quality` (1–95, padrão 95 para JPEG), `background` (uma string
`#RRGGBB`, padrão `None`) e a opção
de compatibilidade `strip_color` (padrão `False`). Quando omitido, o modo é
`preserve`, ou `strip` se essa opção for usada. `CleanResult` inclui `output_format`,
`mode` e uma tupla `notices`; a biblioteca retorna os avisos sem imprimi-los.
As três funções `verify_clean_*` aceitam `max_pixels`, com o mesmo padrão
de 40.000.000 de pixels, e `strip_color=True` para proibir metadados opcionais de cor.
`verify_clean_jpeg` também aceita `jpeg_quality` (padrão 95); informe a mesma qualidade
usada para criar o arquivo. Ela mantém o cabeçalho fixo de representação de imagem JFIF.
A API lança `CleanerError` para conteúdo de imagem inválido ou
não compatível e para falhas de verificação, e subclasses de `OSError` para falhas
do sistema de arquivos. A operação de limpeza só retorna com sucesso após a
conclusão da verificação e da gravação final no destino.

### Desenvolvimento e testes

Após instalar o projeto em modo editável:

```sh
python -m unittest discover -s tests -v
```

Os testes cobrem remoção de metadados, igualdade dos pixels decodificados, rotação,
transparência, seleção de quadros, entradas não compatíveis, corrupção, caminhos em
lote, condições de corrida na sobrescrita e limpeza após falhas de gravação. Os
testes de regressão de PNG cobrem dados ocultos em `IDAT`, fluxos zlib múltiplos ou
truncados, tamanhos e filtros de linha, limites entre blocos e descompressão com
buffers limitados. Os testes de regressão do limite de quadros verificam se quadros
de animação e páginas intermediárias de TIFF que excedem o limite são rejeitados
antes da decodificação dos dados dos pixels.

Os testes de cor comparam tanto os pixels brutos quanto os pixels renderizados pelo
LittleCMS antes e depois da limpeza, nos quatro modos de renderização, usando
perfis RGB e de escala de cinza, alfa, orientação e vários formatos de entrada.
Também verificam a remoção dos campos descritivos ICC, a preservação de gama e
cromaticidade, perfis malformados, transformações de cor não compatíveis e a recusa
em disponibilizar a saída se o codificador alterar pixels ou perder um perfil.
Os testes de conversão comparam perfis RGB, de escala de cinza, alfa e perfis LUT
RGB/CMYK sintéticos com o LittleCMS. Os testes de WebP cobrem RGB oculto sob pixels
transparentes, metadados, dados adicionais dentro do fluxo de pixels, escolhas de
formato/modo e nomes em lote. Esses testes exigem uma compilação do Pillow com
suporte a LittleCMS, WebP e JPEG. Os testes de JPEG cobrem as 95 qualidades,
codificações de referência após conversão ICC e composição alfa, aliases de
extensão, lotes pela interface, metadados proibidos, cabeçalhos malformados,
preenchimento e excesso de coeficientes, dados ocultos após a varredura, limites
e falhas de disponibilização da saída.

A suíte inclui dois arquivos JPEG de teste assinados, sem modificações, do
repositório oficial do SDK C2PA, com URLs de origem fixadas em uma revisão, somas de
verificação e avisos de licença em [tests/fixtures](tests/fixtures/README.md).
Arquivos de teste sintéticos também exercitam contêineres de metadados de PNG e
WebP. Instale a dependência opcional de testes para validar de forma independente
as assinaturas e os hashes do conteúdo dos JPEGs e confirmar que as saídas PNG e
WebP nos três modos, e JPEG em `srgb`/`strip`, não possuem manifesto C2PA:

```sh
python -m pip install -e ".[test]"
python -m unittest discover -s tests -v
```

Sem essa dependência extra, o teste independente com o SDK é explicitamente pulado;
os testes de soma de verificação e de limpeza dos arquivos assinados continuam
sendo executados. Os testes com o SDK desativam a busca de manifestos remotos e
consultas OCSP e não baixam listas de confiança. O SDK de testes não é necessário
para o comando de limpeza.

O [GitHub Actions](.github/workflows/tests.yml) está configurado para Windows, macOS
e Linux com Python 3.10 a 3.14, além de uma verificação com a versão mínima do Pillow
no Python 3.10. A integração contínua exige a execução do teste independente de
C2PA. Uma tarefa de empacotamento gera um wheel e um arquivo compactado com o
código-fonte e executa os testes desse arquivo usando o wheel instalado, incluindo
os arquivos de teste distribuídos. Essas tarefas são executadas quando o projeto
está na raiz de um repositório GitHub com Actions habilitado; adicionar o fluxo de
trabalho localmente não executa a matriz de plataformas remotamente.

```text
image_metadata_cleaner/
  src/image_metadata_cleaner/   # API Python e interface de linha de comando
  tests/                       # Suíte unittest da biblioteca padrão
  .github/workflows/tests.yml   # Verificações de plataformas, versões e pacotes
  MANIFEST.in                  # Inclui arquivos de teste nos pacotes de código-fonte
  pyproject.toml               # Configuração do pacote e dependência
  README.md
  LICENSE
```

### Referências técnicas

- A [especificação técnica C2PA](https://spec.c2pa.org/specifications/specifications/2.2/specs/C2PA_Specification.html)
  define os locais dos manifestos incorporados e os mecanismos de proveniência externa.
- A [especificação PNG](https://www.w3.org/TR/png/) define os blocos essenciais e auxiliares.
- A [especificação de perfis ICC](https://archive.color.org/specification/ICC.1-2022-05.pdf)
  define transformações de cor, cabeçalhos de perfil e tags descritivas.
- A documentação de [gerenciamento de cores do Pillow](https://pillow.readthedocs.io/en/stable/reference/ImageCms.html)
  descreve a conversão ICC e as intenções de renderização.
- A [especificação do contêiner WebP](https://developers.google.com/speed/webp/docs/riff_container)
  define os blocos de imagem sem perdas, os perfis ICC e a estrutura RIFF.
- A [especificação JPEG T.81](https://www.w3.org/Graphics/JPEG/itu-t81.pdf)
  define varreduras baseline, decodificação Huffman, bytes de escape e preenchimento.
- A documentação de [formatos de imagem do Pillow](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html)
  e de [tratamento da orientação EXIF](https://pillow.readthedocs.io/en/stable/reference/ImageOps.html#PIL.ImageOps.exif_transpose)
  descreve o comportamento dos decodificadores e das transformações.

### Licença

Este projeto é licenciado sob a **Licença Pública Geral GNU, versão 3 ou (a seu
critério) qualquer versão posterior** (`GPL-3.0-or-later`). Consulte [LICENSE](LICENSE)
para ler o texto completo da licença. O programa é distribuído sem qualquer
garantia; consulte a licença para conhecer seus termos. O texto oficial está
disponível no [Projeto GNU](https://www.gnu.org/licenses/gpl-3.0.html).
