# SPDX-License-Identifier: GPL-3.0-or-later
"""Decode pixels, encode a fresh PNG, and enforce a strict output structure."""

from __future__ import annotations

import os
from pathlib import Path
import struct
import tempfile
import warnings
import zlib
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

SUPPORTED_FORMATS = ("JPEG", "PNG", "WEBP", "GIF", "TIFF", "BMP")
SUPPORTED_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".apng", ".webp", ".gif", ".tif", ".tiff", ".bmp"}
)
DEFAULT_MAX_PIXELS = 40_000_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class CleanerError(Exception):
    """An input cannot be cleaned or an output cannot be verified safely."""


@dataclass(frozen=True)
class CleanResult:
    source: Path
    destination: Path
    source_format: str
    width: int
    height: int
    output_bytes: int
    frame: int


def verify_clean_png(path: str | Path) -> None:
    """Require a decodable, 8-bit RGB(A) PNG with only IHDR, IDAT and IEND.

    Check CRCs, chunk order, image data, and the absence of trailing bytes.
    This checks the container; it cannot detect information encoded in pixels.
    """
    with Path(path).open("rb") as stream:
        if stream.read(8) != PNG_SIGNATURE:
            raise CleanerError("Output is not a PNG.")
        seen_header = seen_data = False
        while True:
            header = stream.read(8)
            if len(header) != 8:
                raise CleanerError("PNG is truncated or has no IEND chunk.")
            length, kind = struct.unpack(">I4s", header)
            if kind not in {b"IHDR", b"IDAT", b"IEND"}:
                raise CleanerError(f"Unexpected PNG chunk: {kind!r}.")
            if not seen_header and kind != b"IHDR":
                raise CleanerError("PNG must start with IHDR.")
            if kind == b"IHDR" and (seen_header or length != 13):
                raise CleanerError("Invalid or duplicate PNG header.")
            if kind == b"IEND" and (not seen_data or length != 0):
                raise CleanerError("Invalid PNG end chunk.")
            crc = zlib.crc32(kind)
            remaining = length
            payload_header = b""
            while remaining:
                block = stream.read(min(remaining, 64 * 1024))
                if not block:
                    raise CleanerError("Truncated PNG chunk.")
                crc = zlib.crc32(block, crc)
                remaining -= len(block)
                if kind == b"IHDR":
                    payload_header += block
            checksum = stream.read(4)
            if len(checksum) != 4 or struct.unpack(">I", checksum)[0] != crc & 0xFFFFFFFF:
                raise CleanerError("PNG chunk checksum does not match.")
            if kind == b"IHDR":
                width, height, depth, color, compression, filtering, interlace = struct.unpack(
                    ">IIBBBBB", payload_header
                )
                if (not width or not height or depth != 8 or color not in {2, 6}
                        or compression != 0 or filtering != 0 or interlace != 0):
                    raise CleanerError("PNG is not a supported 8-bit RGB/RGBA output.")
                seen_header = True
            elif kind == b"IDAT":
                seen_data = True
            else:
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
) -> CleanResult:
    """Write a metadata-free PNG copy, applying EXIF orientation to the pixels.

    A multiframe input requires an explicit zero-based frame selection. Existing
    outputs require overwrite=True; the source is never a permitted destination.
    """
    source = Path(source).expanduser().resolve(strict=True)
    destination = Path(destination).expanduser() if destination is not None else source.with_name(
        source.name + ".clean.png"
    )
    if destination.is_symlink():
        raise CleanerError("The output must not be a symbolic link.")
    destination = destination.resolve()
    if not source.is_file():
        raise CleanerError("The input must be a regular file.")
    if destination == source or (destination.exists() and destination.samefile(source)):
        raise CleanerError("The output must be different from the original input.")
    if destination.suffix.lower() != ".png":
        raise CleanerError("The output filename must end in .png.")
    if destination.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {destination}")
    if frame is not None and frame < 0:
        raise CleanerError("Frame index must be zero or greater.")
    if max_pixels <= 0:
        raise CleanerError("The pixel limit must be positive.")

    fresh = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source, formats=list(SUPPORTED_FORMATS)) as image:
                source_format = image.format
                frame_count = getattr(image, "n_frames", 1)
                if frame_count > 1 and frame is None:
                    raise CleanerError(
                        f"Input has {frame_count} frames/pages. Select one explicitly with --frame N."
                    )
                selected_frame = 0 if frame is None else frame
                if selected_frame >= frame_count:
                    raise CleanerError(f"Frame {selected_frame} does not exist (count: {frame_count}).")
                image.seek(selected_frame)
                if image.width * image.height > max_pixels:
                    raise CleanerError(f"Image exceeds the {max_pixels:,}-pixel limit.")
                _check_precision(image, source)
                image.load()
                with ImageOps.exif_transpose(image) as oriented:
                    has_alpha = "A" in oriented.getbands() or "transparency" in oriented.info
                    with oriented.convert("RGBA" if has_alpha else "RGB") as pixels:
                        # copy()/info.clear() alone can retain format-specific state.
                        fresh = Image.frombytes(pixels.mode, pixels.size, pixels.tobytes())
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, EOFError,
            Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise CleanerError(f"Cannot decode input: {exc}") from exc

    temporary = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=".metadata-cleaner-", suffix=".tmp", dir=destination.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            fresh.save(stream, format="PNG", compress_level=6)
            stream.flush()
            os.fsync(stream.fileno())
        verify_clean_png(temporary)
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
            output_bytes, selected_frame,
        )
    finally:
        fresh.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)
