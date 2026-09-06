# SPDX-License-Identifier: GPL-3.0-or-later
"""Color-mode choices, lossless WebP, and output publication regressions."""

from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageCms, ImageOps, PngImagePlugin

from image_metadata_cleaner import CleanerError, clean_image, verify_clean_png, verify_clean_webp
from image_metadata_cleaner.cli import main
from image_metadata_cleaner.color import sanitize_icc
from image_metadata_cleaner.srgb import srgb_profile
import test_color as color_fixtures
from test_color import chunk, rebuild, tags


def riff_chunk(kind, payload):
    return kind + struct.pack("<I", len(payload)) + payload + b"\0" * (len(payload) % 2)


def riff(chunks):
    data = b"WEBP" + b"".join(riff_chunk(kind, payload) for kind, payload in chunks)
    return b"RIFF" + struct.pack("<I", len(data)) + data


def riff_chunks(data):
    result = []
    offset = 12
    while offset < len(data):
        kind, size = struct.unpack_from("<4sI", data, offset)
        result.append((kind, data[offset + 8:offset + 8 + size]))
        offset += 8 + size + size % 2
    return result


def lut_profile(base, channels):
    # Synthetic ICC LUT8 fixture: regular input/output tables and a tiny CLUT.
    # It exercises the real LittleCMS LUT path without external licensed assets.
    lut = b"mft1" + b"\0" * 4 + bytes([channels, 3, 2, 0])
    lut += struct.pack(">9i", 65536, 0, 0, 0, 65536, 0, 0, 0, 65536)
    lut += bytes(range(256)) * channels
    for index in range(2 ** channels):
        components = [(index >> shift) & 1 for shift in reversed(range(channels))]
        rgb = components if channels == 3 else [(1 - value) * (1 - components[3]) for value in components[:3]]
        lut += bytes(round(value * 100) for value in rgb)
    lut += bytes(range(256)) * 3
    entries = {key: value for key, value in tags(base).items() if key in {b"desc", b"cprt", b"wtpt"}}
    entries[b"A2B0"] = lut
    result = bytearray(rebuild(base, entries, space=b"CMYK" if channels == 4 else b"RGB ", major=2))
    result[12:16] = b"prtr" if channels == 4 else b"scnr"
    return bytes(result)


class OutputChoiceTests(unittest.TestCase):
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

    def test_srgb_conversion_matches_littlecms_in_png_and_webp(self):
        for mode, profile in (("RGB", self.rgb), ("RGBA", self.rgb), ("L", self.gray), ("LA", self.gray),
                              ("RGB", lut_profile(self.srgb, 3)), ("CMYK", lut_profile(self.srgb, 4))):
            for output_format in ("png", "webp"):
                with self.subTest(mode=mode, output_format=output_format, lut=b"A2B0" in profile):
                    exif = Image.Exif()
                    exif[274] = 6
                    name = f"input-{mode}-{output_format}-{b'A2B0' in profile}.tiff"
                    source = self.save_pattern(name, mode, profile, exif=exif)
                    result = clean_image(source, self.root / ("result." + output_format),
                                         mode="srgb", overwrite=True)
                    with Image.open(source) as original, ImageOps.exif_transpose(original) as oriented:
                        input_mode = "L" if mode in {"L", "LA"} else "CMYK" if mode == "CMYK" else "RGB"
                        with oriented.convert(input_mode) as channels:
                            with ImageCms.profileToProfile(channels, ImageCms.ImageCmsProfile(BytesIO(profile)),
                                                           ImageCms.createProfile("sRGB"), outputMode="RGB",
                                                           renderingIntent=ImageCms.Intent.PERCEPTUAL) as expected:
                                with Image.open(result.destination) as output, output.convert("RGB") as rgb:
                                    self.assertEqual(output.size, oriented.size)
                                    self.assertEqual(rgb.tobytes(), expected.tobytes())
                                    if mode == "RGB" and profile == self.rgb:
                                        self.assertNotEqual(rgb.tobytes(), channels.tobytes())
                                    if "A" in mode:
                                        self.assertEqual(output.getchannel("A").tobytes(), oriented.getchannel("A").tobytes())
                                    if output_format == "png":
                                        self.assertEqual(output.info, {"srgb": 0})
                                    else:
                                        self.assertEqual(output.info["icc_profile"], srgb_profile())
                                    self.assertEqual(dict(output.getexif()), {})
                    self.assertEqual(result.mode, "srgb")
                    self.assertEqual(result.output_format, output_format.upper())

    def test_explicit_preserve_matches_default_and_retains_wide_color_values(self):
        source = self.save_pattern("source.png", profile=self.rgb)
        default = clean_image(source)
        explicit = clean_image(source, self.root / "explicit.png", mode="preserve")
        self.assertEqual(default.destination.read_bytes(), explicit.destination.read_bytes())
        with Image.open(source) as original, Image.open(explicit.destination) as output:
            self.assertEqual(original.tobytes(), output.tobytes())
            self.assertEqual(output.info["icc_profile"], sanitize_icc(self.rgb, b"RGB "))

    def test_untagged_srgb_requires_explicit_assumption_and_reports_it(self):
        for mode in ("RGB", "RGBA", "L", "LA", "P", "1"):
            with self.subTest(mode=mode):
                source = self.root / f"source-{mode}.png"
                with Image.new(mode, (3, 2)) as image:
                    image.save(source, **({"transparency": 0} if mode in {"L", "P", "1"} else {}))
                with self.assertRaisesRegex(CleanerError, "assume-srgb"):
                    clean_image(source, mode="srgb")
                result = clean_image(source, mode="srgb", assume_srgb=True, overwrite=True)
                self.assertIn("interpreting pixels as sRGB", result.notices[0])
                with Image.open(source) as original, original.convert("RGBA") as before:
                    with Image.open(result.destination) as output, output.convert("RGBA") as after:
                        self.assertEqual(before.tobytes(), after.tobytes())
        status, stdout, stderr = self.run_cli(source, "--mode", "srgb", "--assume-srgb", "--overwrite")
        self.assertEqual(status, 0, stderr)
        self.assertIn("NOTICE:", stderr)
        self.assertIn("mode=srgb", stdout)

    def test_existing_srgb_marker_needs_no_assumption(self):
        metadata = PngImagePlugin.PngInfo()
        metadata.add(b"sRGB", b"\x02")
        source = self.save_pattern("source.png", pnginfo=metadata)
        for mode in ("preserve", "srgb"):
            result = clean_image(source, self.root / (mode + ".webp"), mode=mode)
            self.assertEqual(result.notices, ())
            with Image.open(source) as original, Image.open(result.destination) as output:
                self.assertEqual(original.tobytes(), output.tobytes())
                self.assertEqual(output.info["icc_profile"], srgb_profile(2 if mode == "preserve" else 0))

    def test_srgb_never_uses_assumption_to_hide_invalid_color_data(self):
        for profile in (b"bad", self.gray, b"x" * (1048576 + 1)):
            source = self.save_pattern("source.tiff", profile=profile)
            with self.assertRaises(CleanerError):
                clean_image(source, mode="srgb", assume_srgb=True)
        source = self.save_pattern("cmyk.jpg", "CMYK")
        with self.assertRaisesRegex(CleanerError, "CMYK"):
            clean_image(source, mode="srgb", assume_srgb=True)
        source = self.save_pattern("source.png")
        original = source.read_bytes()
        for color in (chunk(b"gAMA", struct.pack(">I", 55000)), chunk(b"cICP", b"\x09\x10\0\x01"),
                      chunk(b"iCCP", b"ICC Profile\0\0bad")):
            source.write_bytes(original[:33] + color + original[33:])
            with self.assertRaises(CleanerError):
                clean_image(source, mode="srgb", assume_srgb=True)
        self.assertFalse(list(self.root.glob("*.clean.*")))

    def test_preserve_webp_keeps_profiles_transparency_and_hidden_rgb(self):
        for mode, profile in (("RGB", self.rgb), ("RGBA", self.rgb), ("L", None), ("LA", None),
                              ("RGB", None), ("RGBA", None)):
            with self.subTest(mode=mode, profile=profile is not None):
                source = self.save_pattern("source.png", mode, profile)
                if mode == "RGBA":
                    with Image.open(source) as image:
                        image.putpixel((0, 0), (123, 45, 67, 0))
                        image.save(source, icc_profile=profile)
                result = clean_image(source, self.root / "out.webp", overwrite=True)
                verify_clean_webp(result.destination)
                with Image.open(source) as original, original.convert("RGBA") as before:
                    with Image.open(result.destination) as output, output.convert("RGBA") as after:
                        self.assertEqual(before.tobytes(), after.tobytes())
                        if profile:
                            self.assertEqual(output.info["icc_profile"], sanitize_icc(profile, b"RGB "))

    def test_webp_opaque_alpha_and_frame_selection(self):
        source = self.root / "opaque.png"
        with Image.new("RGBA", (4, 3), (120, 40, 80, 255)) as image:
            image.save(source)
        clean_image(source, output_format="webp")
        source = self.root / "animation.gif"
        with Image.new("RGB", (3, 3), "red") as first, Image.new("RGB", (3, 3), "blue") as second:
            first.save(source, save_all=True, append_images=[second])
        with self.assertRaisesRegex(CleanerError, "frames"):
            clean_image(source, output_format="webp")
        result = clean_image(source, output_format="webp", frame=1)
        with Image.open(result.destination) as output:
            self.assertEqual(output.getpixel((0, 0)), (0, 0, 255))

    def test_preserve_webp_rejects_unrepresentable_color_information(self):
        metadata = PngImagePlugin.PngInfo()
        metadata.add(b"gAMA", struct.pack(">I", 55000))
        for mode, profile, options in (("L", self.gray, {}), ("RGB", None, {"pnginfo": metadata}),
                                       ("RGB", self.rgb, {"pnginfo": metadata})):
            source = self.save_pattern("source.png", mode, profile, **options)
            with self.assertRaisesRegex(CleanerError, "use PNG"):
                clean_image(source, output_format="webp")
        self.assertFalse(list(self.root.glob("*.webp")))

    def test_strip_mode_and_legacy_alias_have_identical_results(self):
        source = self.save_pattern("source.png", profile=b"invalid profile")
        for output_format in ("png", "webp"):
            first = clean_image(source, self.root / ("alias." + output_format), strip_color=True)
            second = clean_image(source, self.root / ("mode." + output_format), mode="strip")
            self.assertEqual(first.destination.read_bytes(), second.destination.read_bytes())
            (verify_clean_png if output_format == "png" else verify_clean_webp)(second.destination, strip_color=True)

    def test_conflicting_options_and_formats_fail_before_writing_even_in_dry_run(self):
        source = self.save_pattern("source.png")
        for options in ({"mode": "nope"}, {"mode": "srgb", "strip_color": True},
                        {"mode": "preserve", "strip_color": True}, {"assume_srgb": True},
                        {"output_format": "avif"}, {"output_format": "png", "destination": self.root / "bad.webp"}):
            with self.subTest(options=options), self.assertRaises(CleanerError):
                clean_image(source, **options)
        status, _, stderr = self.run_cli(source, "--mode", "srgb", "--strip-color", "--dry-run")
        self.assertEqual(status, 1)
        self.assertIn("cannot be combined", stderr)
        status, _, _ = self.run_cli(source, "--format", "png", "-o", self.root / "bad.webp", "--dry-run")
        self.assertEqual(status, 1)
        self.assertEqual(list(self.root.iterdir()), [source])

    def test_batch_webp_naming_skips_both_generated_formats_and_preserves_subfolders(self):
        folder = self.root / "input"
        (folder / "nested").mkdir(parents=True)
        with Image.new("RGB", (2, 2)) as image:
            for name in ("a.png", "nested/a.png", "old.clean.png", "old.clean.webp"):
                image.save(folder / name)
        output = folder / "output"
        status, stdout, stderr = self.run_cli(folder, "-r", "-d", output, "--format", "webp", "--dry-run")
        self.assertEqual(status, 0, stderr)
        self.assertEqual(stdout.count("PLAN:"), 2)
        self.assertIn("format=WEBP", stdout)
        self.assertFalse(output.exists())
        status, stdout, stderr = self.run_cli(folder, "-r", "-d", output, "--format", "webp")
        self.assertEqual(status, 0, stderr)
        self.assertIn("2 succeeded", stdout)
        self.assertTrue((output / "nested/a.png.clean.webp").is_file())
        status, _, _ = self.run_cli(folder, "-r", "-d", output, "--format", "webp", "--overwrite")
        self.assertEqual(status, 0)

    def test_webp_metadata_and_hidden_bitstream_tails_are_rejected(self):
        source = self.save_pattern("source.png", "RGBA", self.rgb)
        result = clean_image(source, output_format="webp")
        original = result.destination.read_bytes()
        parts = riff_chunks(original)
        variants = [original + b"AI", original[:-1], riff(parts + [(b"C2PA", b"AI")]),
                    riff(parts + [(b"EXIF", b"AI")]), riff(parts + [(b"XMP ", b"AI")]),
                    riff(parts + [parts[-1]]), riff(parts[::-1]),
                    riff(parts[:-1] + [(b"VP8L", parts[-1][1] + b"secret AI provenance")]),
                    riff(parts[:-1] + [(b"VP8L", parts[-1][1] + b"\0\0")]),
                    riff([parts[0], (b"ICCP", self.rgb), parts[-1]]),
                    riff([(b"VP8X", b"\xff" + parts[0][1][1:])] + parts[1:]),
                    riff(parts[:-1]), riff(parts[:1] + parts[2:])]
        for index, data in enumerate(variants):
            with self.subTest(index=index):
                path = self.root / "bad.webp"
                path.write_bytes(data)
                with self.assertRaises(CleanerError):
                    verify_clean_webp(path)
        with self.assertRaises(CleanerError):
            verify_clean_webp(result.destination, strip_color=True)
        lossy = self.root / "lossy.webp"
        with Image.open(source) as image:
            image.save(lossy, lossless=False)
        with self.assertRaises(CleanerError):
            verify_clean_webp(lossy)

    def test_webp_limit_is_checked_before_decoding(self):
        source = self.save_pattern("source.png")
        result = clean_image(source, output_format="webp")
        with patch("image_metadata_cleaner.webp.Image.open", side_effect=AssertionError("must not decode")):
            with self.assertRaisesRegex(CleanerError, "pixel limit"):
                verify_clean_webp(result.destination, max_pixels=10)
        with self.assertRaises(CleanerError):
            verify_clean_webp(result.destination, max_pixels=0)
        with Image.new("RGB", (16384, 1)) as image:
            image.save(source)
        with self.assertRaisesRegex(CleanerError, "16,383"):
            clean_image(source, self.root / "wide.webp")

    def test_failed_webp_verification_keeps_existing_destination_and_cleans_temporary(self):
        source = self.save_pattern("source.png")
        destination = self.root / "existing.webp"
        destination.write_bytes(b"keep")
        with patch("image_metadata_cleaner.core.verify_clean_webp", side_effect=CleanerError("bad output")):
            with self.assertRaises(CleanerError):
                clean_image(source, destination, overwrite=True)
        self.assertEqual(destination.read_bytes(), b"keep")
        self.assertFalse(list(self.root.glob(".metadata-cleaner-*")))

    def test_webp_encoder_pixel_or_color_loss_is_never_published(self):
        source = self.save_pattern("source.png", "RGBA", self.rgb)
        real_save = Image.Image.save

        def lose_pixels(image, stream, **options):
            with Image.new(image.mode, image.size, (0, 0, 0, 0)) as different:
                real_save(different, stream, **options)

        def lose_profile(image, stream, **options):
            options["icc_profile"] = b""
            real_save(image, stream, **options)

        for save in (lose_pixels, lose_profile):
            with patch.object(Image.Image, "save", save), self.assertRaises(CleanerError):
                clean_image(source, output_format="webp")
        self.assertFalse(list(self.root.glob("*.webp")))

    def test_missing_optional_codec_capabilities_report_clear_errors(self):
        source = self.save_pattern("source.png")
        with patch("image_metadata_cleaner.webp.features.check", return_value=False):
            with self.assertRaisesRegex(CleanerError, "WebP support"):
                clean_image(source, output_format="webp")
        with patch("PIL.ImageCms.core", new=None):
            with self.assertRaisesRegex(CleanerError, "LittleCMS"):
                clean_image(source, mode="srgb", assume_srgb=True)


if __name__ == "__main__":
    unittest.main()
