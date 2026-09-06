# SPDX-License-Identifier: GPL-3.0-or-later
"""JPEG appearance choices, strict container/scan validation, and publication."""

from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
import random
import struct
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageCms, ImageOps

from image_metadata_cleaner import CleanerError, clean_image, verify_clean_jpeg
from image_metadata_cleaner.cli import main
from image_metadata_cleaner.jpeg import _headers
from image_metadata_cleaner.srgb import srgb_profile
import test_color as color_fixtures
from test_output_choices import lut_profile


def segment(marker, payload):
    return b"\xff" + bytes([marker]) + struct.pack(">H", len(payload) + 2) + payload


def parts(data):
    headers = []
    offset = 2
    while True:
        marker = data[offset + 1]
        length = int.from_bytes(data[offset + 2:offset + 4], "big")
        headers.append((marker, data[offset + 4:offset + 2 + length]))
        offset += length + 2
        if marker == 0xDA:
            return headers, data[offset:]


def rebuild(headers, scan):
    return b"\xff\xd8" + b"".join(segment(*part) for part in headers) + scan


class JpegTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        color_fixtures.ColorTests.setUpClass()
        cls.rgb = color_fixtures.ColorTests.rgb
        cls.gray = color_fixtures.ColorTests.gray
        cls.srgb = color_fixtures.ColorTests.srgb

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    save_pattern = color_fixtures.ColorTests.save_pattern

    def run_cli(self, *args):
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main([str(arg) for arg in args])
        return status, stdout.getvalue(), stderr.getvalue()

    def assert_encoding(self, result, expected, quality=95):
        # Independent Pillow encode of expected transformed/composited RGB pixels.
        with BytesIO() as reference:
            expected.save(reference, format="JPEG", quality=quality, subsampling=0)
            reference.seek(0)
            with Image.open(reference) as encoded, Image.open(result.destination) as output:
                self.assertEqual(output.size, expected.size)
                self.assertEqual(output.mode, "RGB")
                self.assertEqual(output.tobytes(), encoded.tobytes())
                self.assertEqual(dict(output.getexif()), {})
                self.assertEqual(output.layer, [(1, 1, 1, 0), (2, 1, 1, 1), (3, 1, 1, 1)])

    def test_extensions_aliases_defaults_and_loss_notice(self):
        source = self.save_pattern("source.png")
        for suffix in ("jpg", "jpeg", "JPG", "JPEG"):
            result = clean_image(source, self.root / ("out." + suffix), mode="strip", overwrite=True)
            self.assertEqual(result.output_format, "JPEG")
            self.assertIn("lossy", result.notices[0])
            verify_clean_jpeg(result.destination, strip_color=True)
        for alias in ("jpeg", "jpg", "JPEG", "JPG"):
            result = clean_image(source, mode="strip", output_format=alias, overwrite=True)
            self.assertEqual(result.destination.name, "source.png.clean.jpg")
        result = clean_image(source, self.root / "matched.jpeg", mode="strip", output_format="jpg")
        verify_clean_jpeg(result.destination, jpeg_quality=None)
        with Image.open(source) as expected:
            self.assert_encoding(result, expected)
        default = clean_image(source)
        self.assertEqual((default.mode, default.output_format), ("preserve", "PNG"))

    def test_quality_range_changes_encoding_and_validates_every_supported_quality(self):
        source = self.root / "noise.png"
        with Image.frombytes("RGB", (17, 11), random.Random(10).randbytes(17 * 11 * 3)) as image:
            image.save(source)
            sizes = {}
            for quality in range(1, 96):
                result = clean_image(source, self.root / "out.jpg", mode="strip", jpeg_quality=quality, overwrite=True)
                sizes[quality] = result.output_bytes
                self.assert_encoding(result, image, quality)
            self.assertLess(sizes[10], sizes[95])
            with Image.open(result.destination) as output:
                self.assertNotEqual(image.tobytes(), output.tobytes())
        with self.assertRaises(CleanerError):
            verify_clean_jpeg(result.destination, jpeg_quality=10)

    def test_srgb_conversion_orientation_and_profiles_match_littlecms(self):
        for index, (pixel_mode, profile) in enumerate((("RGB", self.rgb), ("L", self.gray),
                ("RGB", lut_profile(self.srgb, 3)), ("CMYK", lut_profile(self.srgb, 4)))):
            with self.subTest(pixel_mode=pixel_mode, index=index):
                exif = Image.Exif()
                exif[274] = 6
                exif[270] = "AI edited"
                source = self.save_pattern(f"source-{index}.tiff", pixel_mode, profile, exif=exif)
                result = clean_image(source, self.root / f"output-{index}.jpg", mode="srgb", jpeg_quality=87)
                with Image.open(source) as original, ImageOps.exif_transpose(original) as oriented:
                    with ImageCms.profileToProfile(oriented, ImageCms.ImageCmsProfile(BytesIO(profile)),
                            ImageCms.createProfile("sRGB"), outputMode="RGB",
                            renderingIntent=ImageCms.Intent.PERCEPTUAL) as expected:
                        self.assert_encoding(result, expected, 87)
                with Image.open(result.destination) as output:
                    self.assertEqual(output.info["icc_profile"], srgb_profile())
                self.assertNotIn(b"AI edited", result.destination.read_bytes())

    def test_untagged_requires_assumption_and_strip_omits_icc(self):
        source = self.save_pattern("source.png")
        with self.assertRaisesRegex(CleanerError, "assume-srgb"):
            clean_image(source, output_format="jpeg", mode="srgb")
        result = clean_image(source, output_format="jpeg", mode="srgb", assume_srgb=True)
        self.assertEqual(len(result.notices), 2)
        with self.assertRaises(CleanerError):
            verify_clean_jpeg(result.destination, strip_color=True)
        source = self.save_pattern("invalid.png", profile=b"invalid")
        with self.assertRaises(CleanerError):
            clean_image(source, output_format="jpeg", mode="srgb", assume_srgb=True)
        result = clean_image(source, output_format="jpeg", strip_color=True)
        with Image.open(result.destination) as output:
            self.assertNotIn("icc_profile", output.info)

    def test_transparency_requires_explicit_background_and_composites_correctly(self):
        for pixel_mode in ("RGBA", "LA", "P", "RGB", "L"):
            for mode in ("srgb", "strip"):
                with self.subTest(pixel_mode=pixel_mode, mode=mode):
                    source = self.root / f"alpha-{pixel_mode}-{mode}.png"
                    with Image.new(pixel_mode, (9, 7)) as image:
                        if pixel_mode == "RGBA":
                            image.paste((230, 50, 110, 128), (0, 0, 9, 7))
                            image.putpixel((0, 0), (123, 45, 67, 0))
                        elif pixel_mode == "LA":
                            image.paste((120, 128), (0, 0, 9, 7))
                        elif pixel_mode == "P":
                            image.putpalette([230, 50, 110] * 256)
                        options = {"transparency": (0, 0, 0) if pixel_mode == "RGB" else 0} if pixel_mode in {"P", "RGB", "L"} else {}
                        image.save(source, **options)
                    options = dict(output_format="jpeg", mode=mode, assume_srgb=mode == "srgb")
                    with self.assertRaisesRegex(CleanerError, "background"):
                        clean_image(source, **options)
                    self.assertFalse(source.with_name(source.name + ".clean.jpg").exists())
                    for background in ("#ffffff", "#000000", "#12AB34"):
                        result = clean_image(source, **options, background=background, overwrite=True)
                        with Image.open(source) as original, original.convert("RGBA") as rgba:
                            with Image.new("RGB", rgba.size, background) as expected:
                                expected.paste(rgba.convert("RGB"), mask=rgba.getchannel("A"))
                                self.assert_encoding(result, expected)

    def test_profiled_transparency_is_composited_after_srgb_conversion(self):
        source = self.save_pattern("alpha.png", "RGBA", self.rgb)
        result = clean_image(source, mode="srgb", output_format="jpeg", background="#80A040")
        with Image.open(source) as original, original.convert("RGB") as rgb:
            with ImageCms.profileToProfile(rgb, ImageCms.ImageCmsProfile(BytesIO(self.rgb)),
                    ImageCms.createProfile("sRGB"), outputMode="RGB", renderingIntent=0) as converted:
                with Image.new("RGB", original.size, "#80A040") as expected:
                    expected.paste(converted, mask=original.getchannel("A"))
                    self.assert_encoding(result, expected)

    def test_opaque_alpha_needs_no_background_and_frames_are_explicit(self):
        source = self.root / "opaque.png"
        with Image.new("RGBA", (3, 2), (120, 20, 30, 255)) as image:
            image.save(source)
        clean_image(source, mode="strip", output_format="jpeg")
        animation = self.root / "animation.gif"
        with Image.new("RGB", (3, 2), "red") as first, Image.new("RGB", (3, 2), "blue") as second:
            first.save(animation, save_all=True, append_images=[second])
            with self.assertRaisesRegex(CleanerError, "frames"):
                clean_image(animation, mode="strip", output_format="jpeg")
            result = clean_image(animation, mode="strip", output_format="jpeg", frame=1)
            self.assert_encoding(result, second)

    def test_bad_options_fail_before_input_decode_or_directory_creation(self):
        source = self.save_pattern("source.png")
        for options in ({"mode": "preserve"}, {"jpeg_quality": 0}, {"jpeg_quality": 96},
                {"jpeg_quality": True}, {"jpeg_quality": 80.5}, {"jpeg_quality": "80"},
                {"background": "white"}, {"background": "#FFF"}, {"background": (255, 255, 255)},
                {"background": "#FFFFFF00"}, {"background": "#FFFFFF\n"},
                {"output_format": "png", "background": "#FFFFFF"},
                {"output_format": "webp", "jpeg_quality": 95}):
            kwargs = dict(mode="strip", output_format="jpeg") | options
            with self.subTest(options=options), patch("image_metadata_cleaner.core.Image.open", side_effect=AssertionError("no decode")):
                with self.assertRaises(CleanerError):
                    clean_image(source, self.root / "new/out.jpg", **kwargs)
        for args in (("--format", "jpeg"), ("--mode", "strip", "--format", "jpeg", "--jpeg-quality", "100"),
                ("--mode", "strip", "--format", "jpeg", "--background", "white"), ("--jpeg-quality", "95"),
                ("--mode", "strip", "--format", "jpeg", "-o", self.root / "bad.png")):
            status, _, _ = self.run_cli(source, *args, "--dry-run")
            self.assertEqual(status, 1, args)
        self.assertEqual(list(self.root.iterdir()), [source])

    def test_cli_batch_paths_skip_all_generated_formats_and_report_loss(self):
        folder = self.root / "input"
        (folder / "nested").mkdir(parents=True)
        with Image.new("RGB", (3, 2)) as image:
            for name in ("photo.jpg", "nested/photo.png", "old.clean.png", "old.clean.webp", "old.clean.jpg", "old.CLEAN.JPEG"):
                image.save(folder / name)
        output = folder / "out"
        args = (folder, "-r", "-d", output, "--mode", "strip", "--format", "jpg", "--jpeg-quality", "85")
        status, stdout, stderr = self.run_cli(*args, "--dry-run")
        self.assertEqual(status, 0, stderr)
        self.assertEqual(stdout.count("PLAN:"), 2)
        self.assertIn("lossy, quality=85", stdout)
        self.assertFalse(output.exists())
        status, stdout, stderr = self.run_cli(*args)
        self.assertEqual(status, 0, stderr)
        self.assertIn("2 succeeded", stdout)
        self.assertIn("JPEG verified", stdout)
        self.assertIn("lossy", stderr)
        self.assertTrue((output / "nested/photo.png.clean.jpg").is_file())
        status, stdout, stderr = self.run_cli(*args, "--overwrite")
        self.assertEqual(status, 0, stderr)
        self.assertIn("2 succeeded", stdout)

    def test_verifier_rejects_metadata_headers_truncation_and_hidden_scan_tails(self):
        source = self.save_pattern("source.png")
        result = clean_image(source, mode="srgb", assume_srgb=True, output_format="jpeg")
        original = result.destination.read_bytes()
        headers, scan = parts(original)
        variants = [original + b"AI", original[:-1], original[:-2] + b"AI\xff\xd9",
                    rebuild(headers, b"\xff\xd9"), rebuild(headers, scan[:-2] + b"\xff\0\xff\xd9"),
                    rebuild(headers, scan[:-2] + b"\0\0\xff\xd9"), rebuild(headers[::-1], scan),
                    rebuild(headers + [headers[-1]], scan), rebuild(headers[:2] + headers[1:], scan)]
        for marker, data in ((0xE1, b"Exif\0\0AI"), (0xE1, b"http://ns.adobe.com/xap/1.0/\0AI"),
                             (0xEB, b"JP\0\1c2pa"), (0xED, b"Photoshop 3.0\0AI"), (0xFE, b"AI prompt"),
                             (0xE2, b"ICC_PROFILE\0\x01\x01" + self.rgb)):
            variants.append(original[:2] + segment(marker, data) + original[2:])
            variants.append(original[:-2] + segment(marker, data) + original[-2:])
        for index, (marker, payload) in enumerate(headers):
            if marker in {0xE0, 0xE2, 0xDB, 0xC4, 0xC0, 0xDA}:
                variants.append(rebuild(headers[:index] + [(marker, payload + b"AI")] + headers[index + 1:], scan))
                variants.append(rebuild(headers[:index] + [(marker, payload[:-1] + bytes([payload[-1] ^ 1]))] + headers[index + 1:], scan))
        variants += [original[:index] for index in (0, 1, 2, 3, 4, 10, len(original) // 2)]
        bad = self.root / "bad.jpg"
        for index, data in enumerate(variants):
            with self.subTest(index=index):
                bad.write_bytes(data)
                with self.assertRaises(CleanerError):
                    verify_clean_jpeg(bad)
        for options in ({"progressive": True}, {"subsampling": 2}, {"optimize": True}):
            with Image.open(source) as image:
                image.save(bad, quality=95, **options)
            with self.assertRaises(CleanerError):
                verify_clean_jpeg(bad)

    def test_entropy_padding_and_coefficient_overruns_are_rejected(self):
        from image_metadata_cleaner.jpeg import _huffman
        headers = _headers(95)
        tables = {payload[0]: _huffman(payload) for marker, payload in headers if marker == 0xC4}

        def code(table, symbol):
            length, value = next(key for key, result in tables[table][0].items() if result == symbol)
            return format(value, f"0{length}b")

        valid = code(0, 0) + code(0x10, 0) + (code(1, 0) + code(0x11, 0)) * 2
        overflow = code(0, 0) + code(0x10, 0xF0) * 4
        bad = self.root / "scan.jpg"
        for bits, padding, succeeds in ((valid, "1", True), (valid, "0", False), (overflow, "1", False)):
            padded = bits + padding * ((-len(bits)) % 8)
            entropy = int(padded, 2).to_bytes(len(padded) // 8, "big").replace(b"\xff", b"\xff\0")
            bad.write_bytes(rebuild(headers, entropy + b"\xff\xd9"))
            if succeeds:
                verify_clean_jpeg(bad)
            else:
                with self.assertRaises(CleanerError):
                    verify_clean_jpeg(bad)

    def test_limits_checked_before_decoding_and_missing_codec_error(self):
        source = self.save_pattern("source.png")
        result = clean_image(source, mode="strip", output_format="jpeg")
        with patch("image_metadata_cleaner.jpeg.Image.open", side_effect=AssertionError("no decode")):
            with self.assertRaisesRegex(CleanerError, "pixel limit"):
                verify_clean_jpeg(result.destination, max_pixels=1)
        for options in ({"max_pixels": 0}, {"jpeg_quality": 0}):
            with self.assertRaises(CleanerError):
                verify_clean_jpeg(result.destination, **options)
        with Image.new("RGB", (65501, 1)) as image:
            image.save(self.root / "wide.png")
        with self.assertRaisesRegex(CleanerError, "65,500"):
            clean_image(self.root / "wide.png", mode="strip", output_format="jpeg")
        with patch("image_metadata_cleaner.jpeg.features.check", return_value=False):
            with self.assertRaisesRegex(CleanerError, "JPEG support"):
                clean_image(source, self.root / "missing.jpg", mode="strip")

    def test_failed_verification_or_wrong_pixels_profiles_never_publish(self):
        source = self.save_pattern("source.png")
        original = source.read_bytes()
        destination = self.root / "existing.jpg"
        destination.write_bytes(b"keep")

        def wrong_pixels(image, stream, icc, quality):
            with Image.new("RGB", image.size) as other:
                other.save(stream, format="JPEG", quality=quality, subsampling=0, icc_profile=icc)

        def wrong_profile(image, stream, icc, quality):
            image.save(stream, format="JPEG", quality=quality, subsampling=0)

        for target, replacement in (("verify_clean_jpeg", CleanerError("bad output")),
                                    ("save_jpeg", wrong_pixels), ("save_jpeg", wrong_profile)):
            with patch("image_metadata_cleaner.core." + target, side_effect=replacement):
                with self.assertRaises(CleanerError):
                    clean_image(source, destination, mode="srgb", assume_srgb=True, overwrite=True)
            self.assertEqual(destination.read_bytes(), b"keep")
            self.assertEqual(source.read_bytes(), original)
            self.assertFalse(list(self.root.glob(".metadata-cleaner-*")))
        with self.assertRaises(FileExistsError):
            clean_image(source, destination, mode="strip")
        with self.assertRaisesRegex(CleanerError, "different"):
            clean_image(destination, destination, mode="strip", overwrite=True)


if __name__ == "__main__":
    unittest.main()
