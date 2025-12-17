from __future__ import annotations

import json
import math
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd


E7_FACTOR = 1e7


def load_json_file(path: Path) -> Any:
    """Load a JSON file with basic validation."""
    if not path.exists():
        raise FileNotFoundError(f"Input file does not exist: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def ensure_clean_directory(path: Path) -> None:
    """Create an empty directory, removing existing contents if present."""
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def to_timestamp(value: Any) -> Optional[pd.Timestamp]:
    """Convert various timestamp representations to pandas Timestamp."""
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            # Assume milliseconds since epoch
            return pd.to_datetime(value, unit="ms", utc=True)
        if isinstance(value, str):
            value = value.strip()
            if value.isdigit():
                return pd.to_datetime(int(value), unit="ms", utc=True)
            return pd.to_datetime(value, utc=True, errors="coerce")
        if isinstance(value, datetime):
            return pd.to_datetime(value, utc=True)
    except Exception:
        return None
    return None


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute haversine distance in meters between two lat/lon points."""
    radius = 6371000  # meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius * c


def sliding_window(iterable: Iterable[Any], size: int):
    """Yield a simple sliding window over an iterable."""
    window: list[Any] = []
    for item in iterable:
        window.append(item)
        if len(window) > size:
            window.pop(0)
        if len(window) == size:
            yield list(window)
