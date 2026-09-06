# Signed C2PA fixtures

`C.jpg` and `CA.jpg` are unmodified signed JPEG test assets from
[contentauth/c2pa-rs](https://github.com/contentauth/c2pa-rs), revision
`8641fbac3971dc7163c4ba67d8c588f62c99761b`, under `sdk/tests/fixtures/`.
`CA.jpg` also contains an ingredient assertion.

[provenance.json](provenance.json) records each immutable download URL and SHA-256
checksum. The upstream assets are included under the MIT option of that project's
dual license; its copyright and permission notice is preserved in
[LICENSE-MIT](LICENSE-MIT). The cleaner itself remains GPL-3.0-or-later.

These fixtures contain actual signed C2PA manifests, rather than placeholder
strings in carrier chunks. With the optional `test` dependencies installed, the
suite uses the official `c2pa-python` SDK to require successful claim-signature and
asset-hash validation before cleaning. It then requires `ManifestNotFound` on each
output. The input signatures are not asserted to belong to a trusted production
signer: the SDK reports `signingCredential.untrusted` with its default trust setup.

Network fetching of remote manifests and OCSP responses is disabled in these tests.
No trust lists are downloaded. Files are checked locally against their pinned
checksums; tests do not refresh or overwrite these fixtures.

PNG and WebP carrier handling is additionally covered by the synthetic metadata
tests in `test_cleaner.py`. Signed PNG/WebP fixtures are not included here.
