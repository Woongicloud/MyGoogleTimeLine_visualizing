"""Parsing and normalization of Google Timeline JSON files."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from .utils import load_json

logger = logging.getLogger(__name__)


def _safe_lat_lon(point: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    """Extract latitude and longitude from a point-like dict.

    Expects values in E7 format and converts them to float degrees.
    """

    lat_e7 = point.get("latE7") or point.get("latitudeE7")
    lon_e7 = point.get("lngE7") or point.get("longitudeE7")
    if lat_e7 is None or lon_e7 is None:
        return None
    try:
        return float(lat_e7) / 1e7, float(lon_e7) / 1e7
    except (ValueError, TypeError):
        return None


def _parse_timestamp(value: Any) -> Optional[pd.Timestamp]:
    """Parse timestamps into pandas-aware UTC objects."""

    if value is None:
        return None
    try:
        if isinstance(value, str):
            return pd.to_datetime(value, utc=True, errors="coerce")
        if isinstance(value, (int, float)):
            # timestamp in milliseconds
            return pd.to_datetime(int(value), unit="ms", utc=True, errors="coerce")
    except Exception:  # pragma: no cover - defensive parsing
        return None
    return None


def _extract_signal_points(signal: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """Yield point dictionaries from a raw signal entry."""

    position = signal.get("position") or signal
    point = position.get("point") if isinstance(position, dict) else None
    if isinstance(point, dict):
        coords = _safe_lat_lon(point)
        if coords:
            yield {
                "timestamp": signal.get("timestamp") or position.get("timestamp"),
                "latitude": coords[0],
                "longitude": coords[1],
                "accuracy": position.get("accuracy") or signal.get("accuracy"),
                "speed": position.get("speed") or signal.get("speed"),
            }


def _extract_timeline_objects(objects: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
    """Parse standard Google Location History structures."""

    for obj in objects:
        if "activitySegment" in obj:
            segment = obj["activitySegment"]
            for key in ("startLocation", "endLocation"):
                loc = segment.get(key)
                if isinstance(loc, dict):
                    coords = _safe_lat_lon(loc)
                    if coords:
                        yield {
                            "timestamp": segment.get("duration", {}).get(
                                "startTimestamp" if key == "startLocation" else "endTimestamp"
                            ),
                            "latitude": coords[0],
                            "longitude": coords[1],
                            "accuracy": loc.get("accuracyMeters"),
                            "speed": segment.get("confidence"),
                        }
        if "placeVisit" in obj:
            visit = obj["placeVisit"].get("location", {})
            coords = _safe_lat_lon(visit)
            if coords:
                yield {
                    "timestamp": obj.get("placeVisit", {})
                    .get("duration", {})
                    .get("startTimestamp"),
                    "latitude": coords[0],
                    "longitude": coords[1],
                    "accuracy": visit.get("accuracyMeters"),
                    "speed": None,
                }


def parse_timeline(json_path: Path) -> pd.DataFrame:
    """Parse a Google Timeline JSON export into a normalized DataFrame.

    Parameters
    ----------
    json_path: Path
        Path to the JSON file exported from Google Takeout.

    Returns
    -------
    pandas.DataFrame
        DataFrame with columns: timestamp, latitude, longitude, accuracy, speed.
    """

    data = load_json(json_path)
    rows: List[Dict[str, Any]] = []

    # Handle raw timelineEdits with rawSignal entries
    for edit in data.get("timelineEdits", []) or []:
        raw_signal = edit.get("rawSignal", {})
        signals = raw_signal.get("signal", [])
        if isinstance(signals, dict):
            signals = [signals]
        for signal in signals:
            rows.extend(_extract_signal_points(signal))

    # Handle standard timelineObjects entries if present
    timeline_objects = data.get("timelineObjects", [])
    rows.extend(_extract_timeline_objects(timeline_objects))

    if not rows:
        logger.warning("No usable location points found in %s", json_path)
        return pd.DataFrame(columns=["timestamp", "latitude", "longitude", "accuracy", "speed"])

    df = pd.DataFrame(rows)
    df["timestamp"] = df["timestamp"].apply(_parse_timestamp)
    df = df.dropna(subset=["timestamp", "latitude", "longitude"])

    if df.empty:
        logger.warning("Parsed DataFrame is empty after cleaning")
        return df

    df = df.sort_values("timestamp").reset_index(drop=True)

    # Deduplicate consecutive identical or near-identical points
    def _dedup_mask(frame: pd.DataFrame) -> pd.Series:
        lat_diff = frame["latitude"].diff().abs() < 1e-6
        lon_diff = frame["longitude"].diff().abs() < 1e-6
        time_dup = frame["timestamp"].diff().dt.total_seconds() == 0
        return ~(lat_diff & lon_diff & time_dup.fillna(False))

    df = df[_dedup_mask(df)].reset_index(drop=True)

    return df[["timestamp", "latitude", "longitude", "accuracy", "speed"]]
