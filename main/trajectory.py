"""Trajectory reconstruction and smoothing utilities."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class TrajectoryConfig:
    """Configuration for trajectory reconstruction."""

    resample_freq: Optional[str] = None
    smoothing_window: int = 1


class TrajectoryBuilder:
    """Generate trajectories from parsed timeline data."""

    def __init__(self, df: pd.DataFrame):
        if df.empty:
            raise ValueError("Input DataFrame is empty; cannot build trajectory")
        self.df = df.copy()
        self.df = self.df.sort_values("timestamp").reset_index(drop=True)

    def resample(self, freq: str) -> "TrajectoryBuilder":
        """Resample trajectory to a fixed temporal frequency using forward fill."""

        resampled = self.df.set_index("timestamp").resample(freq).ffill().reset_index()
        self.df = resampled
        return self

    def smooth(self, window: int) -> "TrajectoryBuilder":
        """Apply a simple moving average smoothing to latitude and longitude."""

        if window <= 1:
            return self
        self.df["latitude"] = self.df["latitude"].rolling(window, min_periods=1).mean()
        self.df["longitude"] = self.df["longitude"].rolling(window, min_periods=1).mean()
        return self

    def build(self) -> pd.DataFrame:
        """Return the final trajectory DataFrame."""

        return self.df


def build_trajectory(
    df: pd.DataFrame, resample_freq: Optional[str] = None, smoothing_window: int = 1
) -> pd.DataFrame:
    """Convenience function to reconstruct trajectory from parsed points."""

    builder = TrajectoryBuilder(df)
    if resample_freq:
        builder.resample(resample_freq)
    builder.smooth(smoothing_window)
    return builder.build()
