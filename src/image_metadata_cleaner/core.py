# SPDX-License-Identifier: GPL-3.0-or-later
"""Clean descriptive metadata with explicit color and output format choices."""

from __future__ import annotations

import os
from pathlib import Path
import struct
import tempfile
import warnings
import zlib
from dataclasses import dataclass

from PIL import Image, ImageOps, PngImagePlugin, UnidentifiedImageError

from .color import COLOR_CHUNKS, MAX_COLOR_CHUNK_BYTES, prepare_color, unpack_icc, validate_color_chunk
from .errors import CleanerError
from .jpeg import flatten_jpeg, jpeg_options, save_jpeg, verify_clean_jpeg, verify_jpeg_pixels
from .srgb import convert_srgb, prepare_srgb, srgb_profile
from .webp import save_webp, verify_clean_webp

SUPPORTED_FORMATS = ("JPEG", "PNG", "WEBP", "GIF", "TIFF", "BMP")
SUPPORTED_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".apng", ".webp", ".gif", ".tif", ".tiff", ".bmp"}
)
DEFAULT_MAX_PIXELS = 40_000_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
OUTPUT_EXTENSIONS = {"PNG": (".png",), "WEBP": (".webp",), "JPEG": (".jpg", ".jpeg")}
_BLOCK_SIZE = 64 * 1024


@dataclass(frozen=True)
class CleanResult:
    source: Path
    destination: Path
    source_format: str
    width: int
    height: int
    output_bytes: int
    frame: int
    output_format: str = "PNG"
    mode: str = "preserve"
    notices: tuple[str, ...] = ()


def resolve_options(mode: str | None, strip_color: bool, assume_srgb: bool,
                    output_format: str | None, destination: str | Path | None, *,
                    jpeg_quality: int | None = None, background: str | None = None) -> tuple[str, str]:
    """Resolve CLI/API defaults and reject contradictory settings before writing."""
    if mode is None:
        mode = "strip" if strip_color else "preserve"
    if mode not in {"preserve", "srgb", "strip"}:
        raise CleanerError("Mode must be preserve, srgb, or strip.")
    if strip_color and mode != "strip":
        raise CleanerError("--strip-color cannot be combined with a different --mode.")
    if assume_srgb and mode != "srgb":
        raise CleanerError("--assume-srgb requires --mode srgb.")
    output_format = (output_format or (Path(destination).suffix[1:] if destination else "png")).upper()
    if output_format == "JPG":
        output_format = "JPEG"
    if output_format not in OUTPUT_EXTENSIONS:
        raise CleanerError("Output format must be PNG, WebP, or JPEG (filename .png, .webp, .jpg, or .jpeg).")
    if destination and Path(destination).suffix.lower() not in OUTPUT_EXTENSIONS[output_format]:
        raise CleanerError("The output filename extension must match the selected format.")
    if output_format == "JPEG" and mode == "preserve":
        raise CleanerError("JPEG encoding is lossy; select --mode srgb or --mode strip, or use PNG/WebP to preserve pixels.")
    jpeg_options(output_format, jpeg_quality, background)
    return mode, output_format


def _webp_colors(pixel_mode: str, chunks: dict[bytes, bytes]) -> tuple[str, dict[bytes, bytes]]:
    if chunks.keys() & {b"gAMA", b"cHRM"} or (pixel_mode in {"L", "LA"} and b"iCCP" in chunks):
        raise CleanerError("WebP cannot preserve these PNG/gray color declarations; use PNG or --mode srgb.")
    if b"sRGB" in chunks:
        chunks = {b"iCCP": b"ICC Profile\0\0" + zlib.compress(srgb_profile(chunks[b"sRGB"][0]))}
    return ("RGBA" if "A" in pixel_mode else "RGB"), chunks


def _check_pixel_limit(size: tuple[int, int], max_pixels: int) -> None:
    if size[0] * size[1] > max_pixels:
        raise CleanerError(f"Image exceeds the {max_pixels:,}-pixel limit.")


class _PngImageData:
    """Validate exactly one zlib stream using bounded decompression buffers."""

    def __init__(self, width: int, height: int, channels: int) -> None:
        self.decoder = zlib.decompressobj()
        self.stride = 1 + width * channels  # One filter byte per scanline.
        self.expected_bytes = height * self.stride
        self.decoded_bytes = 0

    def feed(self, data: bytes) -> None:
        if data and self.decoder.eof:
            raise CleanerError("PNG contains data after the zlib stream.")
        while data:
            # Allow one excess byte so an overlong stream is rejected immediately.
            limit = min(_BLOCK_SIZE, self.expected_bytes - self.decoded_bytes + 1)
            try:
                decoded = self.decoder.decompress(data, limit)
            except zlib.error as exc:
                raise CleanerError(f"Invalid PNG zlib stream: {exc}") from exc
            if self.decoder.unused_data:
                raise CleanerError("PNG contains data after the zlib stream.")
            if self.decoded_bytes + len(decoded) > self.expected_bytes:
                raise CleanerError("PNG contains excess decompressed image data.")
            # Filter positions can cross both IDAT and decompression-buffer boundaries.
            first_filter = (-self.decoded_bytes) % self.stride
            if any(decoded[index] > 4 for index in range(first_filter, len(decoded), self.stride)):
                raise CleanerError("PNG contains an invalid scanline filter.")
            self.decoded_bytes += len(decoded)
            data = self.decoder.unconsumed_tail

    def finish(self) -> None:
        if not self.decoder.eof:
            raise CleanerError("PNG zlib stream is incomplete.")
        if self.decoded_bytes != self.expected_bytes:
            raise CleanerError("PNG decompressed image size does not match its header.")


def verify_clean_png(
    path: str | Path, *, max_pixels: int = DEFAULT_MAX_PIXELS, strip_color: bool = False,
) -> None:
    """Require a decodable 8-bit PNG with only pixels and supported color data.

    Check CRCs, chunk order, exact zlib/scanline data, and no trailing bytes.
    strip_color=True additionally forbids all optional color chunks.
    This cannot detect information encoded in pixels or numeric color transforms.
    """
    if max_pixels <= 0:
        raise CleanerError("The pixel limit must be positive.")
    with Path(path).open("rb") as stream:
        if stream.read(8) != PNG_SIGNATURE:
            raise CleanerError("Output is not a PNG.")
        seen_header = seen_data = False
        seen_colors = set()
        space = b"RGB "
        image_data = None
        while True:
            header = stream.read(8)
            if len(header) != 8:
                raise CleanerError("PNG is truncated or has no IEND chunk.")
            length, kind = struct.unpack(">I4s", header)
            if length > 0x7FFFFFFF:
                raise CleanerError("PNG chunk length exceeds the format limit.")
            if kind not in {b"IHDR", b"IDAT", b"IEND"} | (set() if strip_color else COLOR_CHUNKS):
                raise CleanerError(f"Unexpected PNG chunk: {kind!r}.")
            if not seen_header and kind != b"IHDR":
                raise CleanerError("PNG must start with IHDR.")
            if kind == b"IHDR" and (seen_header or length != 13):
                raise CleanerError("Invalid or duplicate PNG header.")
            if kind == b"IEND" and (not seen_data or length != 0):
                raise CleanerError("Invalid PNG end chunk.")
            if kind in COLOR_CHUNKS:
                if kind in seen_colors or seen_data or length > MAX_COLOR_CHUNK_BYTES:
                    raise CleanerError("Duplicate, misplaced, or oversized PNG color chunk.")
                if kind in {b"iCCP", b"sRGB"} and seen_colors & {b"iCCP", b"sRGB"}:
                    raise CleanerError("Conflicting PNG color profiles.")
            crc = zlib.crc32(kind)
            remaining = length
            payload_header = b""
            while remaining:
                block = stream.read(min(remaining, _BLOCK_SIZE))
                if not block:
                    raise CleanerError("Truncated PNG chunk.")
                crc = zlib.crc32(block, crc)
                remaining -= len(block)
                if kind == b"IHDR" or kind in COLOR_CHUNKS:
                    payload_header += block
                elif kind == b"IDAT":
                    image_data.feed(block)
            checksum = stream.read(4)
            if len(checksum) != 4 or struct.unpack(">I", checksum)[0] != crc & 0xFFFFFFFF:
                raise CleanerError("PNG chunk checksum does not match.")
            if kind == b"IHDR":
                width, height, depth, color, compression, filtering, interlace = struct.unpack(
                    ">IIBBBBB", payload_header
                )
                if (not 0 < width <= 0x7FFFFFFF or not 0 < height <= 0x7FFFFFFF
                        or depth != 8 or color not in {0, 2, 4, 6}
                        or compression != 0 or filtering != 0 or interlace != 0):
                    raise CleanerError("PNG is not a supported 8-bit gray/RGB output.")
                _check_pixel_limit((width, height), max_pixels)
                image_data = _PngImageData(width, height, {0: 1, 2: 3, 4: 2, 6: 4}[color])
                space = b"GRAY" if color in {0, 4} else b"RGB "
                seen_header = True
            elif kind in COLOR_CHUNKS:
                validate_color_chunk(kind, payload_header, space)
                seen_colors.add(kind)
            elif kind == b"IDAT":
                seen_data = True
            else:
                image_data.finish()
                if stream.read(1):
                    raise CleanerError("PNG contains trailing data.")
                break
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path, formats=["PNG"]) as image:
                image.load()
    except (OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise CleanerError(f"PNG cannot be decoded: {exc}") from exc


def _check_precision(image: Image.Image, source: Path) -> None:
    # Some decoders silently reduce 16-bit RGB to 8-bit; check the file header too.
    if image.format == "PNG":
        with source.open("rb") as stream:
            header = stream.read(25)
        if len(header) != 25 or header[24] > 8:
            raise CleanerError("PNG bit depths above 8 are not supported.")
    if image.format == "TIFF":
        bits = image.tag_v2.get(258, (1,))
        if isinstance(bits, int):
            bits = (bits,)
        if any(bit > 8 for bit in bits):
            raise CleanerError("TIFF bit depths above 8 are not supported.")
    if image.mode not in {"1", "L", "LA", "P", "PA", "RGB", "RGBA", "CMYK", "YCbCr"}:
        raise CleanerError(f"Unsupported pixel mode {image.mode!r}; an 8-bit image is required.")


def clean_image(
    source: str | Path,
    destination: str | Path | None = None,
    *,
    frame: int | None = None,
    overwrite: bool = False,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    strip_color: bool = False,
    mode: str | None = None,
    output_format: str | None = None,
    assume_srgb: bool = False,
    jpeg_quality: int | None = None,
    background: str | None = None,
) -> CleanResult:
    """Remove descriptive metadata with preserve (default), srgb, or strip mode.

    A multiframe input requires an explicit zero-based frame selection. Existing
    outputs require overwrite=True; the source is never a permitted destination.
    PNG is the default; .webp selects lossless WebP, .jpg/.jpeg selects lossy JPEG.
    JPEG requires srgb/strip mode; quality defaults to 95 (range 1..95).
    Transparent JPEG inputs require an explicit background="#RRGGBB".
    srgb mode converts profiled pixels; untagged inputs require assume_srgb=True.
    strip_color=True is a compatibility alias for mode="strip".
    """
    mode, output_format = resolve_options(mode, strip_color, assume_srgb, output_format, destination,
                                         jpeg_quality=jpeg_quality, background=background)
    jpeg_quality, background_rgb = jpeg_options(output_format, jpeg_quality, background)
    strip_color = mode == "strip"
    source = Path(source).expanduser().resolve(strict=True)
    destination = Path(destination).expanduser() if destination is not None else source.with_name(
        source.name + ".clean" + OUTPUT_EXTENSIONS[output_format][0]
    )
    if destination.is_symlink():
        raise CleanerError("The output must not be a symbolic link.")
    destination = destination.resolve()
    if not source.is_file():
        raise CleanerError("The input must be a regular file.")
    if destination == source or (destination.exists() and destination.samefile(source)):
        raise CleanerError("The output must be different from the original input.")
    if destination.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {destination}")
    if frame is not None and frame < 0:
        raise CleanerError("Frame index must be zero or greater.")
    if max_pixels <= 0:
        raise CleanerError("The pixel limit must be positive.")

    fresh = None
    notices = ()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source, formats=list(SUPPORTED_FORMATS)) as image:
                source_format = image.format
                # Seeking GIF/APNG frames may decode the preceding frame. Check
                # the initial canvas before even querying/scanning frame headers.
                _check_pixel_limit(image.size, max_pixels)
                frame_count = getattr(image, "n_frames", 1)
                if frame_count > 1 and frame is None:
                    raise CleanerError(
                        f"Input has {frame_count} frames/pages. Select one explicitly with --frame N."
                    )
                selected_frame = 0 if frame is None else frame
                if selected_frame >= frame_count:
                    raise CleanerError(f"Frame {selected_frame} does not exist (count: {frame_count}).")
                # Advance one frame at a time: a later GIF canvas or TIFF page can
                # grow. Reject it before any subsequent seek can decode that frame.
                for next_frame in range(1, selected_frame + 1):
                    _check_pixel_limit(image.size, max_pixels)
                    image.seek(next_frame)
                    _check_pixel_limit(image.size, max_pixels)
                _check_precision(image, source)
                image.load()
                if mode == "srgb":
                    profile, notices = prepare_srgb(image, source, assume_srgb=assume_srgb)
                    pixel_mode, color_chunks = "RGB", {b"sRGB": b"\0"}
                else:
                    pixel_mode, color_chunks = prepare_color(image, source, strip_color=strip_color)
                if output_format == "WEBP":
                    pixel_mode, color_chunks = _webp_colors(pixel_mode, color_chunks)
                elif output_format == "JPEG" and mode == "srgb":
                    color_chunks = {b"iCCP": b"ICC Profile\0\0" + zlib.compress(srgb_profile())}
                with ImageOps.exif_transpose(image) as oriented:
                    pixels = convert_srgb(oriented, profile) if mode == "srgb" else oriented.convert(pixel_mode)
                    with pixels:
                        # copy()/info.clear() alone can retain format-specific state.
                        if output_format == "JPEG":
                            with flatten_jpeg(pixels, background_rgb) as flattened:
                                fresh = Image.frombytes("RGB", flattened.size, flattened.tobytes())
                        else:
                            fresh = Image.frombytes(pixels.mode, pixels.size, pixels.tobytes())
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, EOFError,
            Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise CleanerError(f"Cannot decode input: {exc}") from exc

    temporary = None
    if output_format == "JPEG":
        notices += (f"JPEG output uses lossy encoding (quality {jpeg_quality}, 4:4:4); pixel values can change.",)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=".metadata-cleaner-", suffix=".tmp", dir=destination.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            icc = unpack_icc(color_chunks[b"iCCP"]) if b"iCCP" in color_chunks else None
            if output_format == "PNG":
                pnginfo = PngImagePlugin.PngInfo()
                for kind, payload in color_chunks.items():
                    if kind != b"iCCP":
                        pnginfo.add(kind, payload)
                fresh.save(stream, format="PNG", compress_level=6, pnginfo=pnginfo, icc_profile=icc)
            elif output_format == "WEBP":
                save_webp(fresh, stream, icc)
            else:
                save_jpeg(fresh, stream, icc, jpeg_quality)
            stream.flush()
            os.fsync(stream.fileno())
        verifier = {"PNG": verify_clean_png, "WEBP": verify_clean_webp, "JPEG": verify_clean_jpeg}[output_format]
        verifier(temporary, max_pixels=max_pixels, strip_color=strip_color,
                 **({"jpeg_quality": jpeg_quality} if output_format == "JPEG" else {}))
        # Compare the actual written pixels and color declarations before publishing.
        with Image.open(temporary, formats=[output_format]) as output:
            output.load()
            if output_format == "JPEG":
                verify_jpeg_pixels(fresh, output, jpeg_quality)
            else:
                # WebP may omit an all-opaque alpha channel; compare its expanded values.
                with output.convert(fresh.mode) as written_pixels:
                    if (output.size != fresh.size or written_pixels.tobytes() != fresh.tobytes()
                            or output_format == "PNG" and output.mode != fresh.mode):
                        raise CleanerError("Output pixels differ from the expected cleaned pixels.")
            _, written_colors = prepare_color(output, temporary, strip_color=strip_color)
            if written_colors != color_chunks:
                raise CleanerError("Output color information differs from the cleaned input.")
        output_bytes = temporary.stat().st_size
        if overwrite:
            os.replace(temporary, destination)
        elif os.name == "nt":
            # Windows rename refuses an existing destination, including a race.
            os.rename(temporary, destination)
        else:
            # POSIX rename overwrites; an exclusive hard link publishes atomically.
            os.link(temporary, destination)
        return CleanResult(
            source, destination, source_format, fresh.width, fresh.height,
            output_bytes, selected_frame, output_format, mode, notices,
        )
    finally:
        fresh.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)
