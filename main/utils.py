"""Utility helpers for the Google Timeline visualization toolkit."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict


def configure_logging(level: int = logging.INFO) -> None:
    """Configure basic logging for the CLI.

    Parameters
    ----------
    level: int
        Logging verbosity level.
    """

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def load_json(path: Path) -> Dict[str, Any]:
    """Load a JSON file from disk.

    Parameters
    ----------
    path: Path
        File path to a JSON document.

    Returns
    -------
    dict
        Parsed JSON content.
    """
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def ensure_directory(path: Path) -> None:
    """Create a directory if it does not already exist."""

    path.mkdir(parents=True, exist_ok=True)
