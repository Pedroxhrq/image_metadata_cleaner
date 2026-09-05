# SPDX-License-Identifier: GPL-3.0-or-later
"""Command-line interface and deterministic batch planning."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from . import __version__
from .core import DEFAULT_MAX_PIXELS, SUPPORTED_EXTENSIONS, CleanerError, clean_image


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def _frame_index(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return number


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create PNG copies without embedded C2PA, EXIF, XMP, text or other descriptive metadata.",
        epilog="Output is always PNG. Pixel watermarks and external records are outside this tool's scope.",
    )
    parser.add_argument("input", type=Path, help="image file or folder")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("-o", "--output", type=Path, help="output .png filename (single input only)")
    output.add_argument("-d", "--output-dir", type=Path, help="destination folder; preserve relative subfolders")
    parser.add_argument("-r", "--recursive", action="store_true", help="include subfolders")
    parser.add_argument("--frame", type=_frame_index, help="extract one frame/page, indexed from zero")
    parser.add_argument("--overwrite", action="store_true", help="replace existing outputs; never the source")
    parser.add_argument("--dry-run", action="store_true", help="show planned paths without decoding or writing")
    parser.add_argument("--max-pixels", type=_positive_int, default=DEFAULT_MAX_PIXELS,
                        help=f"maximum pixels per image (default: {DEFAULT_MAX_PIXELS})")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _plan(args: argparse.Namespace) -> list[tuple[Path, Path]]:
    source = args.input.expanduser().resolve(strict=True)
    output_dir = args.output_dir.expanduser().resolve() if args.output_dir else None
    if output_dir is not None and output_dir.exists() and not output_dir.is_dir():
        raise CleanerError("--output-dir must be a directory.")
    if source.is_file():
        if args.recursive:
            raise CleanerError("--recursive requires a folder input.")
        destination = args.output or (output_dir or source.parent) / (source.name + ".clean.png")
        return [(source, destination)]
    if not source.is_dir():
        raise CleanerError("Input must be an image file or folder.")
    if args.output:
        raise CleanerError("Use --output-dir for a folder input.")
    jobs = []

    def walk_error(error: OSError) -> None:
        raise error

    for root, directories, filenames in os.walk(source, onerror=walk_error, followlinks=False):
        root_path = Path(root)
        directories[:] = sorted(
            directory for directory in directories
            if not (root_path / directory).is_symlink()
            and not (output_dir != source and (root_path / directory).resolve() == output_dir)
        ) if args.recursive else []
        for name in sorted(filenames):
            path = root_path / name
            if path.is_symlink() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            if name.lower().endswith(".clean.png"):
                continue
            relative = path.relative_to(source)
            destination = (output_dir / relative if output_dir else path).with_name(name + ".clean.png")
            jobs.append((path, destination))
    if not jobs:
        raise CleanerError("No supported images found. Generated *.clean.png files are skipped in folders.")
    return jobs


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        jobs = _plan(args)
    except (OSError, CleanerError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    failures = 0
    for source, destination in jobs:
        if args.dry_run:
            print(f"PLAN: {source} -> {destination}")
            continue
        try:
            result = clean_image(source, destination, frame=args.frame,
                                 overwrite=args.overwrite, max_pixels=args.max_pixels)
            print(f"OK: {result.source} -> {result.destination} "
                  f"({result.width}x{result.height}, {result.output_bytes:,} bytes; PNG verified)")
        except (OSError, CleanerError, RuntimeError) as exc:
            failures += 1
            print(f"ERROR: {source}: {exc}", file=sys.stderr)
    if not args.dry_run:
        print(f"Finished: {len(jobs) - failures} succeeded, {failures} failed.")
    return 1 if failures else 0
