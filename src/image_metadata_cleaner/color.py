# SPDX-License-Identifier: GPL-3.0-or-later
"""Preserve supported color transforms without copying descriptive ICC fields.

Only matrix/TRC RGB and monochrome ICC v2/v4 input/display profiles are supported.
Unsupported transforms fail explicitly instead of being silently discarded.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

from .errors import CleanerError

MAX_ICC_BYTES = 1_048_576
MAX_COLOR_CHUNK_BYTES = MAX_ICC_BYTES + 4096
COLOR_CHUNKS = frozenset({b"iCCP", b"sRGB", b"gAMA", b"cHRM"})
_UNSUPPORTED_COLOR_CHUNKS = frozenset({b"cICP", b"mDCV", b"cLLI"})
_XYZ_TAGS = {b"rXYZ", b"gXYZ", b"bXYZ", b"wtpt", b"bkpt", b"lumi"}
_TRC_TAGS = {b"rTRC", b"gTRC", b"bTRC", b"kTRC"}
_COLOR_TAGS = _XYZ_TAGS | _TRC_TAGS | {b"chad", b"chrm"}
# These tags contain descriptions, provenance, or device calibration, not the
# supported matrix/TRC image transform. Unknown tags are rejected conservatively.
_DESCRIPTIVE_TAGS = {
    b"desc", b"cprt", b"dmnd", b"dmdd", b"vued", b"targ", b"tech", b"meta",
    b"pseq", b"psid", b"calt", b"scrd", b"ciis",
}


def _error(message: str) -> CleanerError:
    return CleanerError(f"Cannot preserve color: {message}")


def _numeric_tag(tag: bytes, payload: bytes) -> bytes:
    if len(payload) < 8:
        raise _error(f"truncated ICC tag {tag!r}.")
    kind = payload[:4]
    if tag in _XYZ_TAGS:
        valid = kind == b"XYZ " and len(payload) == 20
    elif tag == b"chad":
        valid = kind == b"sf32" and len(payload) == 44
    elif tag == b"chrm":
        valid = (kind == b"chrm" and len(payload) == 36
                 and struct.unpack_from(">HH", payload, 8)[0] == 3
                 and struct.unpack_from(">HH", payload, 8)[1] <= 4)
    elif kind == b"curv" and len(payload) >= 12:
        count = struct.unpack_from(">I", payload, 8)[0]
        valid = len(payload) == 12 + count * 2
    elif kind == b"para" and len(payload) >= 12:
        function = struct.unpack_from(">H", payload, 8)[0]
        counts = {0: 1, 1: 3, 2: 4, 3: 5, 4: 7}
        valid = function in counts and len(payload) == 12 + counts[function] * 4
    else:
        valid = False
    if not valid:
        raise _error(f"unsupported or malformed ICC color tag {tag!r}.")
    # Reserved bytes are never carried over from the source.
    result = payload[:4] + b"\0" * 4 + payload[8:]
    if kind == b"para":
        result = result[:10] + b"\0\0" + result[12:]
    return result


def _description(major: int) -> bytes:
    if major == 4:
        text = "Color profile".encode("utf-16-be")
        return (b"mluc" + b"\0" * 4 + struct.pack(">II", 1, 12)
                + b"enUS" + struct.pack(">II", len(text), 28) + text)
    text = b"Color profile\0"
    return b"desc" + b"\0" * 4 + struct.pack(">I", len(text)) + text + b"\0" * 78


def sanitize_icc(data: bytes, space: bytes) -> bytes:
    """Rebuild a supported ICC profile with identical numeric color transforms.

    Text, identifiers, timestamps, padding and unreferenced bytes are replaced.
    This cannot exclude information deliberately encoded in numeric color data.
    """
    if not 132 <= len(data) <= MAX_ICC_BYTES:
        raise _error("ICC profile is missing, truncated, or exceeds 1 MiB.")
    declared = struct.unpack_from(">I", data)[0]
    major = data[8]
    if (declared != len(data) or data[36:40] != b"acsp" or major not in {2, 4}
            or data[12:16] not in {b"mntr", b"scnr"}
            or data[16:20] != space or data[20:24] != b"XYZ "
            or struct.unpack_from(">I", data, 64)[0] > 3):
        raise _error("expected a matching RGB/gray matrix/TRC ICC v2/v4 input/display profile.")
    count = struct.unpack_from(">I", data, 128)[0]
    table_end = 132 + 12 * count
    if count > 128 or table_end > len(data):
        raise _error("invalid ICC tag table.")
    tags = {}
    seen = set()
    for index in range(count):
        tag, offset, length = struct.unpack_from(">4sII", data, 132 + 12 * index)
        if tag in seen or offset < table_end or offset % 4 or length < 8 or offset + length > len(data):
            raise _error("invalid or duplicate ICC tag entry.")
        seen.add(tag)
        if tag in _COLOR_TAGS:
            tags[tag] = _numeric_tag(tag, data[offset:offset + length])
        elif tag not in _DESCRIPTIVE_TAGS:
            # In particular, do not discard LUT, HDR, vendor-specific transforms,
            # or calibration tags and accidentally change the displayed colors.
            raise _error(f"unsupported ICC tag {tag!r}; its effect on appearance is unknown.")
    required = {b"wtpt", b"kTRC"} if space == b"GRAY" else {
        b"wtpt", b"rXYZ", b"gXYZ", b"bXYZ", b"rTRC", b"gTRC", b"bTRC",
    }
    if not required <= tags.keys():
        raise _error("ICC matrix/TRC color data is incomplete.")
    if (space == b"GRAY" and tags.keys() & {b"rXYZ", b"gXYZ", b"bXYZ", b"rTRC", b"gTRC", b"bTRC", b"chrm"}
            or space == b"RGB " and b"kTRC" in tags):
        raise _error("ICC tags do not match the image color space.")
    tags[b"desc"] = _description(major)
    tags[b"cprt"] = (_description(4) if major == 4 else b"text" + b"\0" * 4 + b"\0")
    header = bytearray(128)
    header[8:10] = data[8:10]  # Preserve version semantics, clear reserved bytes.
    header[12:24] = data[12:24]
    header[24:36] = struct.pack(">6H", 2000, 1, 1, 0, 0, 0)  # Fixed valid date.
    header[36:40] = b"acsp"
    # Device attributes influence media interpretation; only standard bits exist.
    header[56:64] = struct.pack(">Q", struct.unpack_from(">Q", data, 56)[0] & 15)
    header[64:80] = data[64:80]  # Rendering intent and PCS illuminant.
    table = bytearray(struct.pack(">I", len(tags)))
    body = bytearray()
    offset = 132 + 12 * len(tags)
    for tag, payload in sorted(tags.items()):
        table.extend(struct.pack(">4sII", tag, offset + len(body), len(payload)))
        body.extend(payload)
        body.extend(b"\0" * (-len(body) % 4))
    result = header + table + body
    struct.pack_into(">I", result, 0, len(result))
    return bytes(result)


def unpack_icc(payload: bytes) -> bytes:
    """Inflate exactly one bounded iCCP profile, rejecting hidden compressed tails."""
    separator = payload.find(b"\0")
    if not 1 <= separator <= 79 or payload[separator + 1:separator + 2] != b"\0":
        raise _error("invalid PNG ICC header.")
    decoder = zlib.decompressobj()
    try:
        result = decoder.decompress(payload[separator + 2:], MAX_ICC_BYTES + 1)
    except zlib.error as exc:
        raise _error(f"invalid compressed ICC profile: {exc}") from exc
    if (len(result) > MAX_ICC_BYTES or not decoder.eof or decoder.unused_data
            or decoder.unconsumed_tail):
        raise _error("ICC stream is incomplete, oversized, or contains trailing data.")
    return result


def validate_color_chunk(kind: bytes, payload: bytes, space: bytes) -> None:
    if kind == b"iCCP":
        profile = unpack_icc(payload)
        if not payload.startswith(b"ICC Profile\0\0") or sanitize_icc(profile, space) != profile:
            raise _error("output ICC profile contains noncanonical fields.")
    elif kind == b"sRGB":
        if len(payload) != 1 or payload[0] > 3:
            raise _error("invalid sRGB rendering intent.")
    elif kind == b"gAMA":
        if len(payload) != 4 or struct.unpack(">I", payload)[0] == 0:
            raise _error("invalid PNG gamma.")
    elif kind == b"cHRM":
        if len(payload) != 32:
            raise _error("invalid PNG chromaticity length.")
        values = struct.unpack(">8I", payload)
        if any(x > 100000 or y > 100000 or x + y > 100000
               for x, y in zip(values[::2], values[1::2])):
            raise _error("invalid PNG chromaticities.")


def _png_color_chunks(source: Path) -> dict[bytes, bytes]:
    chunks = {}
    seen_data = False
    with source.open("rb") as stream:
        stream.seek(8)
        while True:
            header = stream.read(8)
            if len(header) != 8:
                raise _error("truncated PNG while reading color data.")
            length, kind = struct.unpack(">I4s", header)
            if length > 0x7FFFFFFF:
                raise _error("invalid PNG chunk length.")
            if kind in _UNSUPPORTED_COLOR_CHUNKS:
                raise _error("HDR/CICP PNG color data is not supported.")
            if kind in COLOR_CHUNKS:
                if kind in chunks or seen_data or length > MAX_COLOR_CHUNK_BYTES:
                    raise _error("duplicate, misplaced, or oversized PNG color chunk.")
                payload = stream.read(length)
                crc = stream.read(4)
                if (len(payload) != length or len(crc) != 4
                        or struct.unpack(">I", crc)[0] != zlib.crc32(kind + payload) & 0xFFFFFFFF):
                    raise _error("invalid PNG color chunk checksum.")
                chunks[kind] = payload
            else:
                stream.seek(length + 4, 1)
            if kind == b"IDAT":
                seen_data = True
            elif kind == b"IEND":
                break
    return chunks


def prepare_color(image, source: Path, *, strip_color: bool) -> tuple[str, dict[bytes, bytes]]:
    has_alpha = "A" in image.getbands() or "transparency" in image.info
    if strip_color:
        return ("RGBA" if has_alpha else "RGB"), {}
    if image.mode == "CMYK":
        raise _error("CMYK cannot be stored unchanged in PNG; no output was written.")
    gray = image.mode in {"1", "L", "LA"}
    mode = ("LA" if has_alpha else "L") if gray else ("RGBA" if has_alpha else "RGB")
    space = b"GRAY" if gray else b"RGB "
    chunks = _png_color_chunks(source) if image.format == "PNG" else {}
    if b"iCCP" in chunks:
        profile = unpack_icc(chunks[b"iCCP"])
    elif "icc_profile" in image.info:
        profile = image.info["icc_profile"]
        if not profile:
            raise _error("the embedded ICC profile cannot be read.")
    else:
        profile = None
    if profile is not None:
        if b"sRGB" in chunks:
            raise _error("conflicting ICC and sRGB declarations.")
        profile = sanitize_icc(profile, space)
        chunks[b"iCCP"] = b"ICC Profile\0\0" + zlib.compress(profile)
    for kind, payload in chunks.items():
        validate_color_chunk(kind, payload, space)
    return mode, chunks
