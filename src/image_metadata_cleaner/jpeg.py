# SPDX-License-Identifier: GPL-3.0-or-later
"""Encode and strictly verify our baseline, 4:4:4 JPEG output."""

from functools import lru_cache
from io import BytesIO
from pathlib import Path
import re
import struct
import tempfile
import warnings

from PIL import Image, features

from .errors import CleanerError
from .srgb import srgb_profile

DEFAULT_JPEG_QUALITY = 95
MAX_JPEG_DIMENSION = 65500  # libjpeg's supported limit, below the 16-bit field limit.


def jpeg_options(output_format, quality, background):
    """Validate explicit JPEG-only options, including during CLI dry runs."""
    if output_format != "JPEG" and (quality is not None or background is not None):
        raise CleanerError("--jpeg-quality and --background require JPEG output.")
    if quality is not None and (type(quality) is not int or not 1 <= quality <= 95):
        raise CleanerError("JPEG quality must be an integer from 1 to 95.")
    if background is not None:
        if not isinstance(background, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", background):
            raise CleanerError('JPEG background must be a quoted hex color, such as "#FFFFFF".')
        background = tuple(int(background[index:index + 2], 16) for index in (1, 3, 5))
    return DEFAULT_JPEG_QUALITY if quality is None else quality, background


def flatten_jpeg(image, background):
    """Composite in the output RGB space; never silently discard transparency."""
    if max(image.size) > MAX_JPEG_DIMENSION:
        raise CleanerError("JPEG output dimensions must not exceed 65,500 pixels; use PNG.")
    if "A" in image.mode:
        with image.getchannel("A") as alpha:
            if alpha.getextrema()[0] < 255:
                if background is None:
                    raise CleanerError('JPEG cannot retain transparency; choose --background "#RRGGBB".')
                with image.convert("RGB") as foreground:
                    result = Image.new("RGB", image.size, background)
                    result.paste(foreground, mask=alpha)
                    return result
    return image.convert("RGB")


def _encoder_options(quality, icc=None):
    return dict(format="JPEG", quality=quality, subsampling=0, optimize=False,
                progressive=False, icc_profile=icc or b"", exif=b"", xmp=b"", comment=b"")


def save_jpeg(image, stream, icc, quality):
    if not features.check("jpg"):
        raise CleanerError("JPEG output requires Pillow with JPEG support.")
    image.save(stream, **_encoder_options(quality, icc))


def _segment(stream):
    marker = stream.read(2)
    if len(marker) != 2 or marker[0] != 255 or marker[1] not in {0xE0, 0xE2, 0xDB, 0xC0, 0xC4, 0xDA}:
        raise CleanerError(f"Unexpected JPEG marker: {marker!r}.")
    length = stream.read(2)
    if len(length) != 2 or (size := int.from_bytes(length, "big")) < 2:
        raise CleanerError("Invalid JPEG segment length.")
    payload = stream.read(size - 2)
    if len(payload) != size - 2:
        raise CleanerError("Truncated JPEG segment.")
    return marker[1], payload


@lru_cache(maxsize=95)
def _headers(quality):
    # Derive the installed encoder's fixed JFIF, quantization, and Huffman tables.
    # Only dimensions and the optional standard sRGB profile may differ.
    with Image.new("RGB", (1, 1)) as sample, BytesIO() as stream:
        save_jpeg(sample, stream, None, quality)
        stream.seek(2)
        result = []
        while True:
            part = _segment(stream)
            result.append(part)
            if part[0] == 0xDA:
                return tuple(result)


def _huffman(payload):
    codes = {}
    fast = [None] * 256
    code, offset = 0, 17
    for length, count in enumerate(payload[1:17], 1):
        for symbol in payload[offset:offset + count]:
            codes[length, code] = symbol
            if length <= 8:
                start = code << (8 - length)
                fast[start:start + (1 << (8 - length))] = [(symbol, length)] * (1 << (8 - length))
            code += 1
        offset += count
        code <<= 1
    return codes, tuple(fast)


class _Scan:
    """Consume exactly the Huffman-coded blocks, without allocating pixel buffers.

    JPEG T.81 sections F.2.2 and B.1.1.3: byte stuffing, AC runs, and final
    all-one padding are checked, so decoder-ignored scan tails cannot pass.
    """

    def __init__(self, stream):
        self.stream = stream
        self.bits = self.count = 0
        self.end_marker = None

    def fill(self, count):
        while self.count < count and self.end_marker is None:
            value = self.stream.read(1)
            if not value:
                self.end_marker = b""
                break
            if value == b"\xff":
                following = self.stream.read(1)
                if following != b"\0":
                    self.end_marker = value + following
                    break
            self.bits = (self.bits << 8) | value[0]
            self.count += 8

    def read(self, count):
        if self.count < count:
            self.fill(count)
        if self.count < count:
            raise CleanerError("Truncated JPEG entropy data or unexpected marker inside scan.")
        self.count -= count
        result = self.bits >> self.count
        self.bits &= (1 << self.count) - 1
        return result

    def symbol(self, table):
        codes, fast = table
        if self.count < 8:
            self.fill(8)
        if self.count >= 8:
            result = fast[self.bits >> (self.count - 8)]
            if result is not None:
                symbol, length = result
                self.count -= length
                self.bits &= (1 << self.count) - 1
                return symbol
        code = 0
        for length in range(1, 17):
            code = (code << 1) | self.read(1)
            result = codes.get((length, code))
            if result is not None:
                return result
        raise CleanerError("Invalid JPEG Huffman code.")

    def block(self, dc, ac):
        self.read(self.symbol(dc))
        coefficient = 1
        while coefficient < 64:
            symbol = self.symbol(ac)
            if symbol == 0:  # End of block.
                break
            if symbol == 0xF0:  # Sixteen zero coefficients.
                coefficient += 16
            else:
                coefficient += symbol >> 4
                if coefficient >= 64:
                    raise CleanerError("JPEG AC run exceeds its block.")
                self.read(symbol & 15)
                coefficient += 1
            if coefficient > 64:
                raise CleanerError("JPEG AC run exceeds its block.")

    def finish(self):
        if self.count >= 8 or self.bits != (1 << self.count) - 1:
            raise CleanerError("JPEG has noncanonical entropy padding.")
        marker = self.end_marker if self.end_marker is not None else self.stream.read(2)
        if marker != b"\xff\xd9" or self.stream.read(1):
            raise CleanerError("JPEG contains excess scan data, trailing data, or a missing end marker.")


def verify_clean_jpeg(path: str | Path, *, max_pixels: int = 40_000_000,
                      strip_color: bool = False, jpeg_quality: int = DEFAULT_JPEG_QUALITY) -> None:
    """Verify this encoder's JPEG subset at the supplied quality (default 95).

    Require fixed headers, one optional standard sRGB ICC segment, baseline RGB
    with 4:4:4 sampling, exactly one complete scan, and no metadata/trailing data.
    This is not a general JPEG validator and cannot detect pixel watermarks.
    """
    jpeg_quality, _ = jpeg_options("JPEG", jpeg_quality, None)
    if max_pixels <= 0:
        raise CleanerError("The pixel limit must be positive.")
    with Path(path).open("rb") as stream:
        if stream.read(2) != b"\xff\xd8":
            raise CleanerError("Output is not a JPEG.")
        dimensions = None
        tables = {}
        for index, (kind, expected) in enumerate(_headers(jpeg_quality)):
            actual_kind, payload = _segment(stream)
            # Pillow places ICC APP2 immediately after its fixed JFIF APP0.
            if index == 1 and actual_kind == 0xE2:
                if strip_color or payload != b"ICC_PROFILE\0\x01\x01" + srgb_profile():
                    raise CleanerError("JPEG contains an unexpected or nonstandard ICC profile.")
                actual_kind, payload = _segment(stream)
            if actual_kind != kind:
                raise CleanerError("Unexpected JPEG segment order or duplicate segment.")
            if kind == 0xC0 and len(payload) == len(expected):
                height, width = struct.unpack_from(">HH", payload, 1)
                if not 0 < width <= MAX_JPEG_DIMENSION or not 0 < height <= MAX_JPEG_DIMENSION:
                    raise CleanerError("Invalid JPEG dimensions.")
                if width * height > max_pixels:
                    raise CleanerError(f"Image exceeds the {max_pixels:,}-pixel limit.")
                dimensions = width, height
                payload = payload[:1] + expected[1:5] + payload[5:]
            if payload != expected:
                raise CleanerError("JPEG contains noncanonical headers or compression settings.")
            if kind == 0xC4:
                tables[payload[0]] = _huffman(payload)
        if dimensions is None:
            raise CleanerError("JPEG has no image dimensions.")
        scan = _Scan(stream)
        for _ in range(((dimensions[0] + 7) // 8) * ((dimensions[1] + 7) // 8)):
            scan.block(tables[0], tables[0x10])
            scan.block(tables[1], tables[0x11])
            scan.block(tables[1], tables[0x11])
        scan.finish()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path, formats=["JPEG"]) as image:
                image.load()
                if image.mode != "RGB" or image.size != dimensions:
                    raise CleanerError("Decoded JPEG dimensions or channels differ from its header.")
    except (OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise CleanerError(f"JPEG cannot be decoded: {exc}") from exc


def verify_jpeg_pixels(expected, output, quality):
    """Compare decoded pixels with a reference encode, allowing only JPEG loss."""
    with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as reference:
        expected.save(reference, **_encoder_options(quality))
        reference.seek(0)
        with Image.open(reference, formats=["JPEG"]) as decoded:
            decoded.load()
            if output.mode != "RGB" or output.size != decoded.size or output.tobytes() != decoded.tobytes():
                raise CleanerError("Output pixels differ from the expected JPEG encoding.")
