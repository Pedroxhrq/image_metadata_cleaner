# SPDX-License-Identifier: GPL-3.0-or-later
"""Visual-fidelity and color-container regressions using real LittleCMS transforms."""

from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from PIL import Image, ImageCms, ImageOps, PngImagePlugin, features

from image_metadata_cleaner import CleanerError, clean_image, verify_clean_png
from image_metadata_cleaner.cli import main
from image_metadata_cleaner.color import MAX_ICC_BYTES, sanitize_icc


def chunk(kind, payload):
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def tags(profile):
    count = struct.unpack_from(">I", profile, 128)[0]
    entries = [struct.unpack_from(">4sII", profile, 132 + 12 * index) for index in range(count)]
    return {tag: profile[offset:offset + size] for tag, offset, size in entries}


def rebuild(profile, entries, *, space=b"RGB ", major=4):
    header = bytearray(profile[:128])
    header[8] = major
    header[16:20] = space
    table = bytearray(struct.pack(">I", len(entries)))
    body = bytearray()
    offset = 132 + 12 * len(entries)
    for tag, payload in sorted(entries.items()):
        table.extend(struct.pack(">4sII", tag, offset + len(body), len(payload)))
        body.extend(payload)
        body.extend(b"\0" * (-len(body) % 4))
    result = header + table + body
    struct.pack_into(">I", result, 0, len(result))
    return bytes(result)


def mluc(text):
    text = text.encode("utf-16-be")
    return (b"mluc" + b"\0" * 4 + struct.pack(">II", 1, 12)
            + b"enUS" + struct.pack(">II", len(text), 28) + text)


class ColorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # CI must have LittleCMS: these fidelity checks are never silently skipped.
        cls.srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        entries = tags(cls.srgb)
        # A non-sRGB tone curve catches accidental sRGB conversion or profile loss.
        curve = b"para" + b"\0" * 8 + struct.pack(">i", round(1.8 * 65536))
        for key in (b"rTRC", b"gTRC", b"bTRC"):
            entries[key] = curve
        entries[b"desc"] = mluc("Created using AI - secret prompt")
        entries[b"cprt"] = mluc("Private author")
        cls.rgb = rebuild(cls.srgb, entries)
        gray = {key: value for key, value in entries.items() if key in {b"wtpt", b"desc", b"cprt"}}
        gray[b"kTRC"] = curve
        cls.gray = rebuild(cls.srgb, gray, space=b"GRAY")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def save_pattern(self, name, mode="RGB", profile=None, **options):
        path = self.root / name
        channels = Image.getmodebands(mode)
        data = bytes((index * 31 + index // 7) % 256 for index in range(17 * 13 * channels))
        with Image.frombytes(mode, (17, 13), data) as image:
            image.save(path, icc_profile=profile, **options)
        return path

    def assert_same_rendering(self, source, output):
        with Image.open(source) as original, ImageOps.exif_transpose(original) as oriented, Image.open(output) as cleaned:
            self.assertEqual(oriented.size, cleaned.size)
            self.assertEqual(oriented.mode, cleaned.mode)
            self.assertEqual(oriented.tobytes(), cleaned.tobytes())
            source_profile = ImageCms.ImageCmsProfile(BytesIO(oriented.info["icc_profile"]))
            target_profile = ImageCms.ImageCmsProfile(BytesIO(cleaned.info["icc_profile"]))
            srgb = ImageCms.ImageCmsProfile(BytesIO(self.srgb))
            for intent in ImageCms.Intent:
                with ImageCms.profileToProfile(oriented, source_profile, srgb, outputMode="RGB", renderingIntent=intent) as before:
                    with ImageCms.profileToProfile(cleaned, target_profile, srgb, outputMode="RGB", renderingIntent=intent) as after:
                        self.assertEqual(before.tobytes(), after.tobytes())
            self.assertEqual(dict(cleaned.getexif()), {})
            self.assertEqual(set(cleaned.info), {"icc_profile"})
            self.assertNotIn("secret prompt".encode("utf-16-be"), cleaned.info["icc_profile"])
        verify_clean_png(output)

    def test_rgb_profiles_preserve_pixels_and_managed_rendering_across_formats(self):
        suffixes = ["png", "jpg", "tiff"]
        if features.check("webp"):
            suffixes.append("webp")
        for suffix in suffixes:
            with self.subTest(suffix=suffix):
                exif = Image.Exif()
                exif[305] = "AI generator"
                source = self.save_pattern(f"profile.{suffix}", profile=self.rgb, exif=exif)
                before = source.read_bytes()
                result = clean_image(source)
                self.assert_same_rendering(source, result.destination)
                self.assertEqual(source.read_bytes(), before)

    def test_rgba_profile_orientation_and_alpha_are_preserved(self):
        exif = Image.Exif()
        exif[274] = 6
        source = self.save_pattern("alpha.png", "RGBA", self.rgb, exif=exif)
        self.assert_same_rendering(source, clean_image(source).destination)

    def test_gray_profiles_keep_grayscale_channels(self):
        for mode, suffix in (("L", "png"), ("LA", "png"), ("L", "jpg"), ("L", "tiff")):
            with self.subTest(mode=mode, suffix=suffix):
                source = self.save_pattern(f"gray-{mode}.{suffix}", mode, self.gray)
                self.assert_same_rendering(source, clean_image(source).destination)

    def test_unprofiled_grayscale_and_color_key_alpha_are_preserved(self):
        for mode in ("1", "L", "LA"):
            with self.subTest(mode=mode):
                source = self.root / f"gray-{mode}.png"
                with Image.new(mode, (5, 3), 0) as image:
                    options = {"transparency": 0} if mode != "LA" else {}
                    image.save(source, **options)
                result = clean_image(source)
                with Image.open(source) as original, Image.open(result.destination) as cleaned:
                    self.assertEqual(cleaned.mode, "LA")
                    with original.convert("RGBA") as before, cleaned.convert("RGBA") as after:
                        self.assertEqual(before.tobytes(), after.tobytes())

    def test_non_srgb_profile_actually_changes_rendered_values(self):
        source = self.save_pattern("non-srgb.png", profile=self.rgb)
        with Image.open(source) as image:
            with ImageCms.profileToProfile(image, ImageCms.ImageCmsProfile(BytesIO(self.rgb)),
                                           ImageCms.ImageCmsProfile(BytesIO(self.srgb)), outputMode="RGB") as rendered:
                self.assertNotEqual(image.tobytes(), rendered.tobytes())

    def test_v2_sampled_tone_curves_preserve_rendering(self):
        entries = tags(self.rgb)
        curve = b"curv" + b"\0" * 4 + struct.pack(">I5H", 5, 0, 5000, 22000, 43000, 65535)
        for key in (b"rTRC", b"gTRC", b"bTRC"):
            entries[key] = curve
        # Use the sanitizer's fixed valid v2 descriptive tags; then vary the color data.
        base = sanitize_icc(rebuild(self.rgb, entries, major=2), b"RGB ")
        source = self.save_pattern("v2.png", profile=base)
        self.assert_same_rendering(source, clean_image(source).destination)

    def test_profile_identifiers_text_padding_and_unreferenced_data_are_removed(self):
        profile = bytearray(self.rgb)
        profile[4:8] = b"AI!!"
        profile[40:44] = b"AI!!"
        profile[48:56] = b"AI!!AI!!"
        profile[80:84] = b"AI!!"
        profile[84:100] = b"AI!!" * 4
        profile[100:128] = b"AI!!" * 7
        profile.extend(b"unreferenced AI payload")
        struct.pack_into(">I", profile, 0, len(profile))
        source = self.save_pattern("ids.png", profile=bytes(profile))
        result = clean_image(source)
        self.assert_same_rendering(source, result.destination)
        with Image.open(result.destination) as output:
            profile = output.info["icc_profile"]
            self.assertNotIn(b"AI!!", profile)
            self.assertNotIn(b"unreferenced", profile)
            self.assertEqual(sanitize_icc(profile, b"RGB "), profile)

    def test_gamma_chromaticity_and_srgb_survive_without_text(self):
        for srgb in (False, True):
            with self.subTest(srgb=srgb):
                metadata = PngImagePlugin.PngInfo()
                metadata.add(b"gAMA", struct.pack(">I", 45455 if srgb else 55000))
                metadata.add(b"cHRM", struct.pack(">8I", 31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000))
                if srgb:
                    metadata.add(b"sRGB", b"\x01")
                metadata.add_text("parameters", "AI prompt")
                source = self.save_pattern(f"gamma-{srgb}.png", pnginfo=metadata)
                result = clean_image(source)
                with Image.open(source) as original, Image.open(result.destination) as output:
                    self.assertEqual(original.tobytes(), output.tobytes())
                    self.assertEqual(output.info, {key: value for key, value in original.info.items() if key != "parameters"})
                verify_clean_png(result.destination)
                with self.assertRaises(CleanerError):
                    verify_clean_png(result.destination, strip_color=True)

    def test_icc_and_fallback_gamma_are_both_retained(self):
        metadata = PngImagePlugin.PngInfo()
        metadata.add(b"gAMA", struct.pack(">I", 55000))
        source = self.save_pattern("icc-gamma.png", profile=self.rgb, pnginfo=metadata)
        with Image.open(clean_image(source).destination) as output:
            self.assertEqual(output.info["gamma"], 0.55)
            self.assertIn("icc_profile", output.info)

    def test_invalid_mismatched_and_unsupported_profiles_do_not_publish(self):
        invalid = [b"bad profile", self.gray]
        for tag in (b"A2B0", b"cicp", b"vcgt", b"zzzz"):
            invalid.append(rebuild(self.rgb, {**tags(self.rgb), tag: b"data" + b"\0" * 12}))
        for index, profile in enumerate(invalid):
            with self.subTest(index=index):
                source = self.save_pattern(f"invalid-{index}.png", profile=profile)
                destination = self.root / "existing.png"
                destination.write_bytes(b"keep me")
                with self.assertRaisesRegex(CleanerError, "preserve color"):
                    clean_image(source, destination, overwrite=True)
                self.assertEqual(destination.read_bytes(), b"keep me")
                self.assertEqual(list(self.root.glob(".metadata-cleaner-*")), [])

    def test_cmyk_requires_explicit_color_loss_opt_out(self):
        source = self.save_pattern("cmyk.jpg", "CMYK")
        with self.assertRaisesRegex(CleanerError, "CMYK"):
            clean_image(source)
        result = clean_image(source, strip_color=True)
        verify_clean_png(result.destination, strip_color=True)

    def test_strip_color_cli_is_explicit_and_forwarded(self):
        source = self.save_pattern("cli.png", profile=self.rgb)
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(main([str(source), "--strip-color"]), 0)
        with Image.open(source.with_name(source.name + ".clean.png")) as output:
            self.assertEqual(output.info, {})

    def test_output_pixel_mismatch_prevents_publication(self):
        source = self.save_pattern("pixels.png")
        real_save = Image.Image.save

        def wrong_pixels(image, stream, **options):
            with Image.new(image.mode, image.size, 0) as different:
                real_save(different, stream, **options)

        with patch.object(Image.Image, "save", wrong_pixels):
            with self.assertRaisesRegex(CleanerError, "pixels differ"):
                clean_image(source)
        self.assertFalse(source.with_name(source.name + ".clean.png").exists())
        self.assertEqual(list(self.root.glob(".metadata-cleaner-*")), [])

    def test_encoder_losing_profile_prevents_publication(self):
        source = self.save_pattern("colors.png", profile=self.rgb)
        real_save = Image.Image.save

        def lose_profile(image, stream, **options):
            options["icc_profile"] = None
            real_save(image, stream, **options)

        with patch.object(Image.Image, "save", lose_profile):
            with self.assertRaisesRegex(CleanerError, "color information differs"):
                clean_image(source)
        self.assertFalse(source.with_name(source.name + ".clean.png").exists())

    def test_verifier_rejects_unsafe_or_malformed_color_chunks(self):
        source = self.save_pattern("plain.png")
        original = source.read_bytes()
        canonical = sanitize_icc(self.rgb, b"RGB ")
        compressed = zlib.compress(canonical)
        invalid_chunks = [
            chunk(b"iCCP", b"ICC Profile\0\0" + zlib.compress(self.rgb)),
            chunk(b"iCCP", b"Private AI name\0\0" + compressed),
            chunk(b"iCCP", b"ICC Profile\0\0" + compressed + b"secret"),
            chunk(b"iCCP", b"ICC Profile\0\0" + compressed + zlib.compress(b"secret")),
            chunk(b"iCCP", b"ICC Profile\0\0" + compressed[:-1]),
            chunk(b"iCCP", b"ICC Profile\0\0" + zlib.compress(b"0" * (MAX_ICC_BYTES + 1))),
            chunk(b"iCCP", b"ICC Profile\0\0" + zlib.compress(sanitize_icc(self.gray, b"GRAY"))),
            chunk(b"sRGB", b"\x04"), chunk(b"sRGB", b"\x00AI"),
            chunk(b"gAMA", b"\0" * 4), chunk(b"gAMA", b"\0" * 8),
            chunk(b"cHRM", b"\0" * 31), chunk(b"cHRM", b"\xff" * 32),
            chunk(b"sRGB", b"\0") * 2,
            chunk(b"sRGB", b"\0") + chunk(b"iCCP", b"ICC Profile\0\0" + compressed),
        ]
        for index, extra in enumerate(invalid_chunks):
            with self.subTest(index=index):
                path = self.root / "verify.png"
                path.write_bytes(original[:33] + extra + original[33:])
                with self.assertRaises(CleanerError):
                    verify_clean_png(path)
        path.write_bytes(original[:-12] + chunk(b"gAMA", struct.pack(">I", 55000)) + original[-12:])
        with self.assertRaises(CleanerError):
            verify_clean_png(path)

    def test_source_malformed_and_hdr_color_data_is_not_silently_lost(self):
        source = self.save_pattern("source.png")
        original = source.read_bytes()
        variants = [
            chunk(b"iCCP", b"ICC Profile\0\0invalid"),
            chunk(b"cICP", b"\x09\x10\x00\x01"),
            chunk(b"mDCV", b"\0" * 24),
            chunk(b"cLLI", b"\0" * 8),
            chunk(b"sRGB", b"\0") * 2,
        ]
        for index, extra in enumerate(variants):
            with self.subTest(index=index):
                source.write_bytes(original[:33] + extra + original[33:])
                with self.assertRaises(CleanerError):
                    clean_image(source)


if __name__ == "__main__":
    unittest.main()
