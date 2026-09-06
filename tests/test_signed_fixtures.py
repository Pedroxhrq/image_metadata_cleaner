# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise real signed files and optionally check them with the official SDK."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageOps

from image_metadata_cleaner import clean_image, verify_clean_png

try:
    import c2pa
except ModuleNotFoundError as exc:
    if exc.name != "c2pa":
        raise
    c2pa = None

if c2pa is None and os.environ.get("REQUIRE_C2PA_TESTS") == "1":
    raise ImportError('Signed-fixture SDK checks are required. Install with: pip install ".[test]"')

FIXTURES = Path(__file__).parent / "fixtures"
RECORDS = json.loads((FIXTURES / "provenance.json").read_text(encoding="utf-8"))["files"]


class SignedFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output_dir = Path(self.temporary.name)

    def test_upstream_fixture_checksums(self):
        for record in RECORDS:
            with self.subTest(file=record["file"]):
                data = (FIXTURES / record["file"]).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), record["sha256"])
                self.assertIn(b"c2pa.signature", data)

    def test_cleaning_real_signed_files_preserves_pixels_and_originals(self):
        for record in RECORDS:
            with self.subTest(file=record["file"]):
                source = FIXTURES / record["file"]
                original_bytes = source.read_bytes()
                with Image.open(source) as original, ImageOps.exif_transpose(original) as oriented:
                    with oriented.convert("RGB") as expected:
                        expected_pixels, expected_size = expected.tobytes(), expected.size
                result = clean_image(source, self.output_dir / (source.name + ".png"))
                verify_clean_png(result.destination)
                self.assertEqual(source.read_bytes(), original_bytes)
                with Image.open(result.destination) as output:
                    self.assertEqual(output.size, expected_size)
                    self.assertEqual(output.tobytes(), expected_pixels)
                    self.assertEqual(output.info, {})

    @unittest.skipIf(c2pa is None, 'Install ".[test]" to run independent C2PA SDK checks')
    def test_sdk_validates_input_signature_and_finds_no_output_manifest(self):
        # No live trust-list downloads, remote manifests, or revocation requests.
        settings = c2pa.Settings.from_dict({"verify": {
            "remote_manifest_fetch": False,
            "ocsp_fetch": False,
        }})
        with settings, c2pa.Context(settings) as context:
            for record in RECORDS:
                with self.subTest(file=record["file"]):
                    source = FIXTURES / record["file"]
                    with c2pa.Reader(source, context=context) as reader:
                        report = json.loads(reader.json())
                    self.assertIn(report["active_manifest"], report["manifests"])
                    successes = report["validation_results"]["activeManifest"]["success"]
                    codes = {entry["code"] for entry in successes}
                    self.assertIn("claimSignature.validated", codes)
                    self.assertIn("assertion.dataHash.match", codes)
                    for mode in ("preserve", "srgb", "strip"):
                        for suffix in (("png", "webp") if mode == "preserve" else ("png", "webp", "jpg")):
                            with self.subTest(mode=mode, output=suffix):
                                result = clean_image(
                                    source, self.output_dir / (source.name + "-" + mode + "." + suffix),
                                    mode=mode, assume_srgb=mode == "srgb",
                                )
                                with self.assertRaises(c2pa.C2paError.ManifestNotFound):
                                    with c2pa.Reader(result.destination, context=context):
                                        pass


if __name__ == "__main__":
    unittest.main()
