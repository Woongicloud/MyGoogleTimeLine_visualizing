from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class TrajectoryOptions:
    resample_seconds: Optional[int] = None
    smooth_window: Optional[int] = 5
    interpolate: bool = True


class TrajectoryBuilder:
    """Reconstruct and optionally smooth a trajectory."""

    def __init__(self, options: TrajectoryOptions):
        self.options = options

    def build(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df

        df = df.sort_values("timestamp").set_index("timestamp")
        if self.options.resample_seconds:
            df = self._resample(df)

        if self.options.smooth_window and self.options.smooth_window > 1:
            df = self._smooth(df)

        return df.reset_index()

    def _resample(self, df: pd.DataFrame) -> pd.DataFrame:
        freq = f"{self.options.resample_seconds}s"
        df_resampled = df.resample(freq).first()
        if self.options.interpolate:
            df_resampled[["latitude", "longitude"]] = df_resampled[["latitude", "longitude"]].interpolate(
                method="time"
            )
            df_resampled["accuracy"] = df_resampled["accuracy"].interpolate(method="time")
            df_resampled["speed"] = df_resampled["speed"].interpolate(method="time")
        return df_resampled

    def _smooth(self, df: pd.DataFrame) -> pd.DataFrame:
        window = self.options.smooth_window
        df[["latitude", "longitude"]] = (
            df[["latitude", "longitude"]]
            .rolling(window=window, min_periods=1, center=True)
            .mean()
        )
        return df
