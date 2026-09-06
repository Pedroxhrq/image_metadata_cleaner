# SPDX-License-Identifier: GPL-3.0-or-later
"""Integration tests use actual encoded images with synthetic metadata payloads."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from PIL import Image, PngImagePlugin, features

from image_metadata_cleaner import CleanerError, clean_image, verify_clean_png
from image_metadata_cleaner.cli import main


def png_chunk(kind, payload):
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def jpeg_segment(marker, payload):
    return b"\xff" + bytes([marker]) + struct.pack(">H", len(payload) + 2) + payload


class CleanerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def make_image(self, name="input.png", mode="RGB", color="red", **options):
        path = self.root / name
        with Image.new(mode, (7, 5), color) as image:
            image.save(path, **options)
        return path

    def assert_clean(self, path):
        verify_clean_png(path)
        with Image.open(path) as output:
            output.load()
            self.assertEqual(output.info, {})
            self.assertEqual(dict(output.getexif()), {})
        data = path.read_bytes()
        offset, kinds = 8, []
        while offset < len(data):
            length, kind = struct.unpack(">I4s", data[offset:offset + 8])
            kinds.append(kind)
            offset += length + 12
        self.assertEqual(offset, len(data))
        self.assertEqual(set(kinds), {b"IHDR", b"IDAT", b"IEND"})

    def test_strip_color_removes_text_exif_icc_c2pa_unknown_chunks_and_trailer(self):
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("parameters", "AI prompt: synthetic test, seed: 123")
        metadata.add_text("workflow", "ComfyUI synthetic workflow", zip=True)
        metadata.add_itxt("XML:com.adobe.xmp", "synthetic XMP AI identification")
        exif = Image.Exif()
        exif[305] = "AI image generator"
        source = self.make_image(pnginfo=metadata, exif=exif, icc_profile=b"synthetic profile", dpi=(300, 300))
        original = source.read_bytes()
        # caBX is the PNG C2PA carrier. Test a private chunk unknown to Pillow too.
        source.write_bytes(original[:33] + png_chunk(b"caBX", b"synthetic C2PA manifest")
                           + png_chunk(b"prIv", b"unknown identification") + original[33:]
                           + b"appended provenance")
        original = source.read_bytes()
        result = clean_image(source, strip_color=True)
        self.assertEqual(source.read_bytes(), original)
        self.assert_clean(result.destination)
        self.assertNotIn(b"synthetic", result.destination.read_bytes())
        with Image.open(result.destination) as output:
            self.assertEqual(output.getpixel((0, 0)), (255, 0, 0))

    def test_jpeg_removes_app11_xmp_iptc_comment_and_trailer_without_further_loss(self):
        exif = Image.Exif()
        exif[305] = "AI image generator"
        source = self.make_image("input.jpg", exif=exif)
        original = source.read_bytes()
        segments = (jpeg_segment(0xEB, b"JP\x00\x01\x00\x00\x00\x01synthetic C2PA manifest")
                    + jpeg_segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00AI XMP")
                    + jpeg_segment(0xED, b"Photoshop 3.0\x00synthetic IPTC")
                    + jpeg_segment(0xFE, b"Generated with AI"))
        source.write_bytes(original[:2] + segments + original[2:] + b"C2PA trailer")
        with Image.open(source) as original_image:
            expected = original_image.convert("RGB").tobytes()
        result = clean_image(source)
        self.assert_clean(result.destination)
        with Image.open(result.destination) as output:
            self.assertEqual(output.tobytes(), expected)

    @unittest.skipUnless(features.check("webp"), "Pillow has no WebP codec")
    def test_webp_removes_riff_c2pa_exif_and_xmp(self):
        exif = Image.Exif()
        exif[305] = "AI image generator"
        source = self.make_image("input.webp", exif=exif, xmp=b"AI XMP", lossless=True)
        data = source.read_bytes()
        payload = b"synthetic C2PA manifest"
        chunk = b"C2PA" + struct.pack("<I", len(payload)) + payload
        if len(payload) % 2:
            chunk += b"\x00"
        body = data[8:] + chunk
        source.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
        result = clean_image(source)
        self.assert_clean(result.destination)
        with Image.open(result.destination) as output:
            self.assertEqual(output.getpixel((0, 0)), (255, 0, 0))

    def test_transparency_survives_rgba_palette_and_rgb_color_key(self):
        for mode in ("RGBA", "P", "RGB"):
            with self.subTest(mode=mode):
                source = self.root / f"{mode}.png"
                with Image.new(mode, (3, 2)) as image:
                    if mode == "P":
                        image.putpalette([255, 0, 0] * 256)
                        image.info["transparency"] = 0
                    elif mode == "RGB":
                        image.info["transparency"] = (0, 0, 0)
                    image.save(source)
                with Image.open(source) as image:
                    expected = image.convert("RGBA").tobytes()
                result = clean_image(source)
                self.assert_clean(result.destination)
                with Image.open(result.destination) as output:
                    self.assertEqual(output.mode, "RGBA")
                    self.assertEqual(output.tobytes(), expected)

    def test_exif_orientation_is_baked_into_pixels(self):
        exif = Image.Exif()
        exif[274] = 6
        source = self.root / "oriented.png"
        with Image.new("RGB", (2, 3)) as image:
            image.putdata([(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (0, 0, 0), (255, 255, 255)])
            expected = image.transpose(Image.Transpose.ROTATE_270)
            self.addCleanup(expected.close)
            image.save(source, exif=exif)
        result = clean_image(source)
        self.assertEqual((result.width, result.height), (3, 2))
        with Image.open(result.destination) as output:
            self.assertEqual(output.tobytes(), expected.tobytes())
        self.assert_clean(result.destination)

    def test_gif_tiff_and_bmp_still_images(self):
        for suffix in ("gif", "tiff", "bmp"):
            with self.subTest(suffix=suffix):
                source = self.make_image(f"still.{suffix}")
                result = clean_image(source)
                self.assert_clean(result.destination)

    def test_animation_and_multipage_require_explicit_frame(self):
        for suffix in ("gif", "png", "tiff"):
            with self.subTest(suffix=suffix):
                source = self.root / f"frames.{suffix}"
                with Image.new("RGB", (4, 4), "red") as first, Image.new("RGB", (4, 4), "blue") as second:
                    first.save(source, save_all=True, append_images=[second])
                with self.assertRaisesRegex(CleanerError, "frames/pages"):
                    clean_image(source)
                result = clean_image(source, frame=1)
                with Image.open(result.destination) as output:
                    self.assertEqual(output.getpixel((0, 0)), (0, 0, 255))
                self.assert_clean(result.destination)
                with self.assertRaisesRegex(CleanerError, "does not exist"):
                    clean_image(source, self.root / "other.png", frame=2)

    def test_refuses_in_place_even_with_overwrite(self):
        source = self.make_image()
        original = source.read_bytes()
        with self.assertRaisesRegex(CleanerError, "different"):
            clean_image(source, source, overwrite=True)
        self.assertEqual(source.read_bytes(), original)

    def test_existing_output_needs_overwrite(self):
        source = self.make_image()
        destination = self.root / "existing.png"
        destination.write_bytes(b"existing output")
        with self.assertRaises(FileExistsError):
            clean_image(source, destination)
        self.assertEqual(destination.read_bytes(), b"existing output")
        clean_image(source, destination, overwrite=True)
        self.assert_clean(destination)

    def test_missing_invalid_truncated_and_unsupported_input(self):
        missing = self.root / "missing.png"
        with self.assertRaises(FileNotFoundError):
            clean_image(missing)
        for name, content in (("bad.png", b"not an image"), ("truncated.png", b"\x89PNG\r\n\x1a\n")):
            with self.subTest(name=name):
                source = self.root / name
                source.write_bytes(content)
                with self.assertRaises(CleanerError):
                    clean_image(source)
                self.assertFalse(source.with_name(name + ".clean.png").exists())
        source = self.make_image("input.ppm")
        with self.assertRaises(CleanerError):
            clean_image(source)

    def test_limits_and_output_extension(self):
        source = self.make_image()
        for options in ({"max_pixels": 1}, {"max_pixels": 0}, {"frame": -1}):
            with self.subTest(options=options), self.assertRaises(CleanerError):
                clean_image(source, **options)
        with self.assertRaisesRegex(CleanerError, ".png"):
            clean_image(source, self.root / "output.avif")

    def test_rejects_high_bit_depth(self):
        for suffix in ("png", "tiff"):
            with self.subTest(suffix=suffix):
                source = self.make_image(f"high.{suffix}", mode="I;16", color=65535)
                with self.assertRaisesRegex(CleanerError, "above 8"):
                    clean_image(source)

    def test_verifier_rejects_metadata_trailer_corruption_and_missing_end(self):
        source = self.make_image()
        original = clean_image(source).destination.read_bytes()
        corrupt = bytearray(original)
        corrupt[29] ^= 1
        variants = [original[:33] + png_chunk(b"tEXt", b"Software\x00AI") + original[33:],
                    original + b"extra", bytes(corrupt), original[:-12], original[:-3]]
        path = self.root / "verify.png"
        for variant in variants:
            with self.subTest(length=len(variant)), self.assertRaises(CleanerError):
                path.write_bytes(variant)
                verify_clean_png(path)

    def test_verification_failure_does_not_publish_or_replace_output(self):
        source = self.make_image()
        destination = self.root / "existing.png"
        destination.write_bytes(b"keep me")
        with patch("image_metadata_cleaner.core.verify_clean_png", side_effect=CleanerError("failed")):
            with self.assertRaises(CleanerError):
                clean_image(source, destination, overwrite=True)
        self.assertEqual(destination.read_bytes(), b"keep me")
        self.assertEqual(list(self.root.glob(".metadata-cleaner-*")), [])

    def test_no_clobber_if_output_appears_during_processing(self):
        source = self.make_image()
        destination = self.root / "raced.png"

        def race(path, **options):
            verify_clean_png(path, **options)
            destination.write_bytes(b"another process")

        with patch("image_metadata_cleaner.core.verify_clean_png", side_effect=race):
            with self.assertRaises(FileExistsError):
                clean_image(source, destination)
        self.assertEqual(destination.read_bytes(), b"another process")
        self.assertEqual(list(self.root.glob(".metadata-cleaner-*")), [])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def image(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with Image.new("RGB", (2, 2), "red") as image:
            image.save(path)
        return path

    def run_cli(self, *args):
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main([str(arg) for arg in args])
        return status, stdout.getvalue(), stderr.getvalue()

    def test_default_filename(self):
        source = self.image("photo.jpg")
        status, stdout, stderr = self.run_cli(source)
        self.assertEqual(status, 0, stderr)
        self.assertIn("PNG verified", stdout)
        self.assertTrue((self.root / "photo.jpg.clean.png").is_file())

    def test_recursive_batch_keeps_paths_and_excludes_output_folder(self):
        self.image("input/photo.jpg")
        self.image("input/photo.png")
        self.image("input/nested/UPPER.PNG")
        self.image("input/clean/old.png")
        self.image("input/previous.jpg.clean.png")
        output = self.root / "input/clean"
        status, stdout, stderr = self.run_cli(self.root / "input", "-r", "-d", output)
        self.assertEqual(status, 0, stderr)
        self.assertIn("3 succeeded", stdout)
        for relative in ("photo.jpg.clean.png", "photo.png.clean.png", "nested/UPPER.PNG.clean.png"):
            self.assertTrue((output / relative).is_file())
        self.assertFalse((output / "clean").exists())

    def test_nonrecursive_and_dry_run_create_nothing(self):
        self.image("input/photo.jpg")
        self.image("input/nested/child.png")
        output = self.root / "output"
        status, stdout, stderr = self.run_cli(self.root / "input", "-d", output, "--dry-run")
        self.assertEqual(status, 0, stderr)
        self.assertEqual(stdout.count("PLAN:"), 1)
        self.assertFalse(output.exists())

    def test_batch_continues_after_bad_file_and_reports_failure(self):
        self.image("good.png")
        (self.root / "bad.png").write_bytes(b"invalid")
        status, stdout, stderr = self.run_cli(self.root)
        self.assertEqual(status, 1)
        self.assertIn("1 succeeded, 1 failed", stdout)
        self.assertIn("bad.png", stderr)
        self.assertTrue((self.root / "good.png.clean.png").exists())

    def test_invalid_paths_and_options(self):
        self.image("image.png")
        for args in ((self.root / "missing",), (self.root, "-o", self.root / "x.png"),
                     (self.root / "image.png", "-r")):
            with self.subTest(args=args):
                status, _, stderr = self.run_cli(*args)
                self.assertEqual(status, 1)
                self.assertIn("ERROR:", stderr)


if __name__ == "__main__":
    unittest.main()
