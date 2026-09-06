# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit, color-managed conversion to sRGB; never guess a missing profile."""

from io import BytesIO
import struct

from .color import MAX_ICC_BYTES, _png_color_chunks, sanitize_icc, unpack_icc, validate_color_chunk
from .errors import CleanerError


def _cms():
    try:
        from PIL import ImageCms
        ImageCms.core.littlecms_version
        return ImageCms
    except (ImportError, AttributeError) as exc:
        raise CleanerError("sRGB conversion requires Pillow with LittleCMS support.") from exc


def srgb_profile(intent: int = 0) -> bytes:
    """Return standard sRGB color data with fixed, generic descriptive fields."""
    cms = _cms()
    data = bytearray(cms.ImageCmsProfile(cms.createProfile("sRGB")).tobytes())
    struct.pack_into(">I", data, 64, intent)
    return sanitize_icc(bytes(data), b"RGB ")


def prepare_srgb(image, source, *, assume_srgb: bool):
    """Validate source declarations and return an input profile and notices.

    LittleCMS can use LUT and CMYK profiles that cannot be retained in our output.
    The original profile is used only for conversion and is never copied out.
    """
    cms = _cms()
    chunks = _png_color_chunks(source) if image.format == "PNG" else {}
    for kind, payload in chunks.items():
        if kind != b"iCCP":
            validate_color_chunk(kind, payload, b"RGB ")
    if b"iCCP" in chunks:
        profile = unpack_icc(chunks[b"iCCP"])
    else:
        profile = image.info.get("icc_profile")
        if "icc_profile" in image.info and not profile:
            raise CleanerError("Cannot convert to sRGB: the embedded ICC profile cannot be read.")
    if profile is not None:
        if b"sRGB" in chunks:
            raise CleanerError("Cannot convert to sRGB: conflicting ICC and sRGB declarations.")
        space = b"CMYK" if image.mode == "CMYK" else (
            b"GRAY" if image.mode in {"1", "L", "LA"} else b"RGB "
        )
        if (not 132 <= len(profile) <= MAX_ICC_BYTES
                or struct.unpack_from(">I", profile)[0] != len(profile)
                or profile[36:40] != b"acsp" or profile[8] not in {2, 4}
                or profile[16:20] != space or profile[20:24] not in {b"XYZ ", b"Lab "}
                or profile[12:16] not in {b"scnr", b"mntr", b"prtr", b"spac"}):
            raise CleanerError("Cannot convert to sRGB: invalid, oversized, or mismatched ICC profile.")
        count = struct.unpack_from(">I", profile, 128)[0]
        table_end = 132 + 12 * count
        if not 0 < count <= 128 or table_end > len(profile):
            raise CleanerError("Cannot convert to sRGB: invalid ICC tag table.")
        seen = set()
        for index in range(count):
            tag, offset, length = struct.unpack_from(">4sII", profile, 132 + index * 12)
            if (tag in seen or offset < table_end or offset % 4 or length < 8
                    or offset + length > len(profile)):
                raise CleanerError("Cannot convert to sRGB: invalid ICC tag entry.")
            seen.add(tag)
        try:
            return cms.ImageCmsProfile(BytesIO(profile)), ()
        except (cms.PyCMSError, OSError, ValueError) as exc:
            raise CleanerError(f"Cannot read the input ICC profile: {exc}") from exc
    if image.mode == "CMYK":
        raise CleanerError("CMYK conversion requires a valid embedded ICC profile; sRGB cannot be assumed.")
    if b"sRGB" in chunks:
        return None, ()
    if chunks:
        raise CleanerError("sRGB conversion from PNG gamma/chromaticities alone is unsupported; use --mode preserve.")
    if not assume_srgb:
        raise CleanerError("No input color profile. Use --assume-srgb only if the input is sRGB, or use --mode preserve.")
    return None, ("No input color profile; interpreting pixels as sRGB because --assume-srgb was selected.",)


def convert_srgb(image, profile):
    """Convert oriented color channels with perceptual intent; keep alpha exactly."""
    has_alpha = "A" in image.getbands() or "transparency" in image.info
    if profile is None:
        return image.convert("RGBA" if has_alpha else "RGB")
    cms = _cms()
    mode = "CMYK" if image.mode == "CMYK" else (
        "L" if image.mode in {"1", "L", "LA"} else "RGB"
    )
    try:
        with image.convert(mode) as channels:
            result = cms.profileToProfile(
                channels, profile, cms.createProfile("sRGB"), outputMode="RGB",
                renderingIntent=cms.Intent.PERCEPTUAL,
            )
        if has_alpha:
            with image.convert("RGBA") as rgba, rgba.getchannel("A") as alpha:
                result.putalpha(alpha)
        return result
    except (cms.PyCMSError, OSError, ValueError) as exc:
        raise CleanerError(f"Cannot convert the embedded ICC profile to sRGB: {exc}") from exc
