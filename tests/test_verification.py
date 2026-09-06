# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for strict PNG stream validation and resource limits."""

from pathlib import Path
import random
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from PIL import Image, GifImagePlugin, PngImagePlugin, TiffImagePlugin, features

from image_metadata_cleaner import CleanerError, clean_image, verify_clean_png
from image_metadata_cleaner.core import PNG_SIGNATURE


def chunk(kind, payload):
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def png(width, height, parts, *, color=2):
    header = struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0)
    return (PNG_SIGNATURE + chunk(b"IHDR", header)
            + b"".join(chunk(b"IDAT", part) for part in parts) + chunk(b"IEND", b""))


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "test.png"
        self.scanlines = b"\x00\xff\x00\x00" * 2  # Two one-pixel RGB rows.
        self.compressed = zlib.compress(self.scanlines)

    def verify(self, data, **options):
        self.path.write_bytes(data)
        verify_clean_png(self.path, **options)

    def test_rejects_trailing_text_in_same_or_later_idat(self):
        for parts in ([self.compressed + b"creator=AI"],
                      [self.compressed, b"creator=AI"],
                      [self.compressed, b"", b"creator=AI"]):
            with self.subTest(parts=parts), self.assertRaisesRegex(CleanerError, "after the zlib"):
                self.verify(png(1, 2, parts))

    def test_rejects_a_second_zlib_stream(self):
        for parts in ([self.compressed + zlib.compress(b"extra")],
                      [self.compressed, zlib.compress(b"extra")]):
            with self.subTest(parts=parts), self.assertRaisesRegex(CleanerError, "after the zlib"):
                self.verify(png(1, 2, parts))

    def test_rejects_missing_or_bad_zlib_checksum_even_with_valid_chunk_crc(self):
        for compressed in (self.compressed[:-4], self.compressed[:-1],
                           self.compressed[:-1] + bytes([self.compressed[-1] ^ 1])):
            with self.subTest(compressed=compressed), self.assertRaisesRegex(CleanerError, "zlib"):
                self.verify(png(1, 2, [compressed]))

    def test_rejects_short_or_excess_decompressed_data(self):
        for raw in (self.scanlines[:-1], self.scanlines + b"extra", self.scanlines * 2):
            with self.subTest(length=len(raw)), self.assertRaisesRegex(CleanerError, "decompressed"):
                self.verify(png(1, 2, [zlib.compress(raw)]))

    def test_rejects_invalid_filter_bytes(self):
        for row in (0, 1):
            raw = bytearray(self.scanlines)
            raw[row * 4] = 5
            with self.subTest(row=row), self.assertRaisesRegex(CleanerError, "filter"):
                self.verify(png(1, 2, [zlib.compress(raw)]))

    def test_accepts_arbitrary_idat_boundaries_and_empty_chunks(self):
        for offset in range(len(self.compressed) + 1):
            with self.subTest(offset=offset):
                self.verify(png(1, 2, [b"", self.compressed[:offset], b"", self.compressed[offset:], b""]))
        self.verify(png(1, 2, [bytes([value]) for value in self.compressed]))

    def test_accepts_all_png_filters(self):
        # The encoded bytes can be arbitrary for each valid filter type.
        raw = b"".join(bytes([filter_type, 1, 2, 3]) for filter_type in range(5))
        self.verify(png(1, 5, [zlib.compress(raw)]))

    def test_large_scanlines_and_rgba_cross_decompression_buffer_boundaries(self):
        randomizer = random.Random(17)
        for width, height, channels, color in ((22000, 3, 3, 2), (301, 150, 4, 6)):
            with self.subTest(width=width, channels=channels):
                row_bytes = width * channels
                raw = b"".join(b"\x00" + randomizer.randbytes(row_bytes) for _ in range(height))
                compressed = zlib.compress(raw)
                parts = [compressed[start:start + 997] for start in range(0, len(compressed), 997)]
                self.verify(png(width, height, parts, color=color))

    def test_high_compression_ratio_stream_uses_bounded_output_buffers(self):
        raw = (b"\x00" + b"\x00" * 3000) * 300
        self.verify(png(1000, 300, [zlib.compress(raw)]))
        # A tiny declared image must also reject an expansion bomb promptly.
        with self.assertRaisesRegex(CleanerError, "excess decompressed"):
            self.verify(png(1, 1, [zlib.compress(raw)]))

    def test_pixel_limit_applies_before_decompressing(self):
        with patch("image_metadata_cleaner.core.zlib.decompressobj") as decoder:
            with self.assertRaisesRegex(CleanerError, "pixel limit"):
                self.verify(png(1, 2, [self.compressed]), max_pixels=1)
            decoder.assert_not_called()
        self.verify(png(1, 2, [self.compressed]), max_pixels=2)
        with self.assertRaisesRegex(CleanerError, "positive"):
            self.verify(png(1, 2, [self.compressed]), max_pixels=0)

    def test_cleaning_removes_payload_that_verifier_rejects(self):
        self.path.write_bytes(png(1, 2, [self.compressed + b"creator=AI"]))
        result = clean_image(self.path)
        verify_clean_png(result.destination)
        self.assertNotIn(b"creator=AI", result.destination.read_bytes())

    def test_custom_cleaner_limit_is_forwarded_to_verification(self):
        self.path.write_bytes(png(1, 2, [self.compressed]))
        with patch("image_metadata_cleaner.core.verify_clean_png", wraps=verify_clean_png) as verifier:
            clean_image(self.path, max_pixels=123)
        self.assertEqual(verifier.call_args.kwargs["max_pixels"], 123)


class FrameLimitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def animation(self, suffix):
        source = self.root / f"frames.{suffix}"
        with Image.new("RGB", (20, 20), "red") as first, Image.new("RGB", (20, 20), "blue") as second:
            first.save(source, save_all=True, append_images=[second])
        return source

    def test_oversized_initial_canvas_is_rejected_without_decode_or_seek(self):
        for suffix, plugin in (("gif", GifImagePlugin.GifImageFile),
                               ("png", PngImagePlugin.PngImageFile),
                               ("tiff", TiffImagePlugin.TiffImageFile)):
            for frame in (None, 0, 1):
                with self.subTest(suffix=suffix, frame=frame):
                    source = self.animation(suffix)
                    with patch.object(plugin, "load") as load, patch.object(plugin, "seek") as seek:
                        with self.assertRaisesRegex(CleanerError, "pixel limit"):
                            clean_image(source, frame=frame, max_pixels=10)
                    load.assert_not_called()
                    seek.assert_not_called()
                    self.assertFalse(source.with_name(source.name + ".clean.png").exists())

    def test_oversized_intermediate_tiff_page_is_never_decoded(self):
        source = self.root / "varying.tiff"
        with Image.new("RGB", (2, 2), "red") as first, Image.new("RGB", (20, 20), "blue") as second:
            first.save(source, save_all=True, append_images=[second, first])
        with patch.object(TiffImagePlugin.TiffImageFile, "load") as load:
            with self.assertRaisesRegex(CleanerError, "pixel limit"):
                clean_image(source, frame=2, max_pixels=10)
        load.assert_not_called()

    def test_growing_gif_frame_is_rejected_before_it_is_decoded(self):
        source = self.animation("gif")
        with Image.open(source) as image:
            image.seek(1)
            # Locate the second image descriptor immediately before its LZW data.
            data_start = image.tile[0].offset
        data = bytearray(source.read_bytes())
        # Pillow may have written a local palette between descriptor and LZW data.
        descriptor = data.rfind(b"\x2c\x00\x00\x00\x00\x14\x00\x14\x00", 0, data_start)
        self.assertGreater(descriptor, 0)
        data[descriptor + 1:descriptor + 3] = struct.pack("<H", 100)
        source.write_bytes(data)
        real_load = GifImagePlugin.GifImageFile.load
        decoded_sizes = []

        def observed_load(image, *args, **kwargs):
            decoded_sizes.append(image.size)
            return real_load(image, *args, **kwargs)

        with patch.object(GifImagePlugin.GifImageFile, "load", observed_load):
            with self.assertRaisesRegex(CleanerError, "pixel limit"):
                clean_image(source, frame=1, max_pixels=500)
        self.assertTrue(decoded_sizes)
        self.assertTrue(all(width * height <= 500 for width, height in decoded_sizes))

    def test_supported_animations_still_extract_correct_frame(self):
        suffixes = ["gif", "png", "tiff"]
        if features.check("webp"):
            suffixes.append("webp")
        for suffix in suffixes:
            with self.subTest(suffix=suffix):
                source = self.animation(suffix)
                with Image.open(source) as original:
                    original.seek(1)
                    expected = original.convert("RGB").tobytes()
                result = clean_image(source, frame=1, max_pixels=400)
                with Image.open(result.destination) as output:
                    self.assertEqual(output.convert("RGB").tobytes(), expected)


if __name__ == "__main__":
    unittest.main()
