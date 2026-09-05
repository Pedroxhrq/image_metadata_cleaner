# SPDX-License-Identifier: GPL-3.0-or-later
"""Create image copies without embedded descriptive metadata."""

from .core import CleanResult, CleanerError, clean_image, verify_clean_png

__all__ = ["CleanResult", "CleanerError", "clean_image", "verify_clean_png"]
__version__ = "0.1.0"
