# SPDX-License-Identifier: GPL-3.0-or-later
"""Errors shared by image and color validation."""


class CleanerError(Exception):
    """An input cannot be cleaned or an output cannot be verified safely."""
