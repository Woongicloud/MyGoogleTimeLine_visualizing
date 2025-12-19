from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from .utils import E7_FACTOR, haversine_distance, load_json_file, to_timestamp


class TimelineParser:
    """Parse Google Timeline / Location History exports into a clean DataFrame."""

    def __init__(self, max_accuracy: Optional[float] = None, dedup_tolerance_m: float = 5.0):
        self.max_accuracy = max_accuracy
        self.dedup_tolerance_m = dedup_tolerance_m

    def parse(self, input_path: Path | str) -> pd.DataFrame:
        path = Path(input_path)
        raw = load_json_file(path)
        points = list(self._collect_points(raw))
        df = pd.DataFrame(points)
        if df.empty:
            return pd.DataFrame(columns=["timestamp", "latitude", "longitude", "accuracy", "speed"])

        df = df.dropna(subset=["timestamp", "latitude", "longitude"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.sort_values("timestamp").reset_index(drop=True)

        if self.max_accuracy is not None:
            df = df[df["accuracy"].fillna(self.max_accuracy) <= self.max_accuracy]

        df = self._deduplicate(df)
        return df.reset_index(drop=True)

    def _collect_points(self, raw: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
        containers: List[Any] = []
        if isinstance(raw, dict):
            if "timelineEdits" in raw and isinstance(raw["timelineEdits"], list):
                containers.extend(raw["timelineEdits"])
            if "timelineObjects" in raw and isinstance(raw["timelineObjects"], list):
                containers.extend(raw["timelineObjects"])
            if "locations" in raw and isinstance(raw["locations"], list):
                containers.extend(raw["locations"])

        for entry in containers:
            yield from self._extract_from_entry(entry)

    def _extract_from_entry(self, entry: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
        timestamp = self._extract_timestamp(entry)
        if not timestamp:
            return []

        # raw signal points
        if "rawSignal" in entry:
            raw_signal = entry.get("rawSignal")
            signals = raw_signal.get("signal") if isinstance(raw_signal, dict) else raw_signal
            if isinstance(signals, list):
                for signal in signals:
                    point = self._extract_point(signal, timestamp)
                    if point:
                        yield point

        # direct point on entry (legacy locations or place visits)
        point = self._extract_point(entry, timestamp)
        if point:
            yield point

        # activity segments (timelineObjects)
        if "activitySegment" in entry:
            segment = entry["activitySegment"]
            for key in ("startLocation", "endLocation"):
                loc = segment.get(key)
                if loc:
                    seg_timestamp = to_timestamp(segment.get(f"{key}Timestamp")) or timestamp
                    point = self._extract_point(loc, seg_timestamp)
                    if point:
                        yield point

        return []

    def _extract_point(self, payload: Dict[str, Any], timestamp: pd.Timestamp) -> Optional[Dict[str, Any]]:
        position = None
        if "position" in payload and isinstance(payload["position"], dict):
            position = payload["position"].get("point") or payload["position"]
        elif "point" in payload:
            position = payload.get("point")
        elif "latitudeE7" in payload and "longitudeE7" in payload:
            position = {"latE7": payload.get("latitudeE7"), "lngE7": payload.get("longitudeE7")}
        elif "latE7" in payload and "lngE7" in payload:
            position = payload

        if not position:
            return None

        lat_e7 = position.get("latE7")
        lng_e7 = position.get("lngE7")
        if lat_e7 is None or lng_e7 is None:
            return None

        lat = float(lat_e7) / E7_FACTOR
        lon = float(lng_e7) / E7_FACTOR

        accuracy = payload.get("accuracy") or payload.get("verticalAccuracy") or payload.get("hAccuracy")
        speed = payload.get("speed")
        parsed_timestamp = to_timestamp(payload.get("timestamp")) or timestamp
        if parsed_timestamp is None:
            return None

        return {
            "timestamp": parsed_timestamp,
            "latitude": lat,
            "longitude": lon,
            "accuracy": float(accuracy) if accuracy is not None else None,
            "speed": float(speed) if speed is not None else None,
        }

    def _extract_timestamp(self, entry: Dict[str, Any]) -> Optional[pd.Timestamp]:
        ts = entry.get("timestamp") or entry.get("timestampMs") or entry.get("eventTime")
        ts_obj = to_timestamp(ts)
        if ts_obj is not None:
            return ts_obj
        # fallback to nested segment times
        for key in ("startTimestamp", "startTimestampMs"):
            if key in entry:
                ts_obj = to_timestamp(entry.get(key))
                if ts_obj is not None:
                    return ts_obj
        return None

    def _deduplicate(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df

        rows = [df.iloc[0]]
        for _, row in df.iloc[1:].iterrows():
            last = rows[-1]
            dist = haversine_distance(last["latitude"], last["longitude"], row["latitude"], row["longitude"])
            if dist < self.dedup_tolerance_m:
                continue
            rows.append(row)
        return pd.DataFrame(rows)
