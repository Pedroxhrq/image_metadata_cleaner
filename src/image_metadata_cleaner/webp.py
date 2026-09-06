# SPDX-License-Identifier: GPL-3.0-or-later
"""Encode and verify our canonical, lossless, single-frame WebP output."""

from pathlib import Path
import struct
import tempfile
import warnings

from PIL import Image, features

from .color import MAX_ICC_BYTES, sanitize_icc
from .errors import CleanerError


def save_webp(image, stream, icc: bytes | None) -> None:
    if not features.check("webp"):
        raise CleanerError("WebP output requires Pillow with WebP support.")
    if max(image.size) > 16383:
        raise CleanerError("WebP output dimensions must not exceed 16,383 pixels; use PNG.")
    image.save(stream, format="WEBP", lossless=True, exact=True, method=6,
               icc_profile=icc or b"", exif=b"", xmp=b"")


def verify_clean_webp(path: str | Path, *, max_pixels: int = 40_000_000,
                      strip_color: bool = False) -> None:
    """Require the exact bytes produced by our installed lossless WebP encoder.

    Check RIFF structure before decoding, then re-encode with fixed options and
    compare bytes. This also rejects ignored data inside VP8L. This verifier is
    intentionally tied to the installed libwebp version and encoder settings.
    It cannot identify information encoded in pixels or numeric ICC transforms.
    """
    if max_pixels <= 0:
        raise CleanerError("The pixel limit must be positive.")
    path = Path(path)
    size = path.stat().st_size
    seen = []
    icc = None
    canvas = dimensions = None
    flags = 0
    with path.open("rb") as stream:
        header = stream.read(12)
        if (len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WEBP"
                or struct.unpack_from("<I", header, 4)[0] + 8 != size):
            raise CleanerError("Invalid WebP RIFF header, size, or trailing data.")
        while stream.tell() < size:
            header = stream.read(8)
            if len(header) != 8:
                raise CleanerError("Truncated WebP chunk header.")
            kind, length = struct.unpack("<4sI", header)
            if kind not in {b"VP8X", b"ICCP", b"VP8L"} or kind in seen:
                raise CleanerError(f"Unexpected or duplicate WebP chunk: {kind!r}.")
            end = stream.tell() + length
            if end + length % 2 > size:
                raise CleanerError("Truncated WebP chunk.")
            if kind == b"VP8X":
                if seen or length != 10:
                    raise CleanerError("Invalid WebP extended header.")
                payload = stream.read(10)
                flags = payload[0]
                if flags & ~0x30 or payload[1:4] != b"\0" * 3:
                    raise CleanerError("Unsupported WebP flags or nonzero reserved bytes.")
                canvas = (int.from_bytes(payload[4:7], "little") + 1,
                          int.from_bytes(payload[7:10], "little") + 1)
            elif kind == b"ICCP":
                if strip_color or seen != [b"VP8X"] or not flags & 0x20 or length > MAX_ICC_BYTES:
                    raise CleanerError("Unexpected, misplaced, or oversized WebP ICC profile.")
                icc = stream.read(length)
                if sanitize_icc(icc, b"RGB ") != icc:
                    raise CleanerError("WebP ICC profile contains noncanonical fields.")
            else:
                if length < 5:
                    raise CleanerError("Truncated WebP lossless header.")
                payload = stream.read(5)
                bits = int.from_bytes(payload[1:], "little")
                if payload[0] != 0x2F or bits >> 29:
                    raise CleanerError("Invalid WebP lossless header.")
                dimensions = ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
                if canvas is not None and canvas != dimensions:
                    raise CleanerError("WebP canvas and image dimensions differ.")
                if canvas is not None and bool(flags & 0x10) != bool(bits & (1 << 28)):
                    raise CleanerError("WebP alpha flags differ.")
            for current in (canvas, dimensions):
                if current and current[0] * current[1] > max_pixels:
                    raise CleanerError(f"Image exceeds the {max_pixels:,}-pixel limit.")
            stream.seek(end)
            if length % 2 and stream.read(1) != b"\0":
                raise CleanerError("WebP has nonzero chunk padding.")
            seen.append(kind)
        expected = [b"VP8X", b"ICCP", b"VP8L"] if icc is not None else [b"VP8L"]
        if seen != expected or bool(flags & 0x20) != (icc is not None):
            raise CleanerError("Invalid WebP chunk order or missing image/profile.")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(path, formats=["WEBP"]) as decoded:
                    decoded.load()
                    if decoded.size != dimensions:
                        raise CleanerError("Decoded WebP dimensions differ from the header.")
                    with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as canonical:
                        save_webp(decoded, canonical, icc)
                        canonical.seek(0)
                        stream.seek(0)
                        while True:
                            block = stream.read(65536)
                            if block != canonical.read(65536):
                                raise CleanerError("WebP differs from canonical lossless encoding (extra or altered data).")
                            if not block:
                                break
        except (OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
            raise CleanerError(f"WebP cannot be decoded or verified: {exc}") from exc
