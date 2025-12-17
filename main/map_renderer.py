"""Map rendering and frame generation using matplotlib."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

import matplotlib.pyplot as plt
import pandas as pd

from .utils import ensure_directory

logger = logging.getLogger(__name__)


STYLE_THEMES = {
    "light": {
        "bg": "#f8f9fa",
        "grid": "#e9ecef",
        "point": "#d62728",
        "text": "#212529",
    },
    "dark": {
        "bg": "#1b1b1b",
        "grid": "#2b2b2b",
        "point": "#ffbf00",
        "text": "#f8f9fa",
    },
    "minimal": {
        "bg": "white",
        "grid": "#f2f2f2",
        "point": "#2ca02c",
        "text": "#222",
    },
}


class MapRenderer:
    """Render trajectory frames to disk."""

    def __init__(
        self,
        df: pd.DataFrame,
        frames_dir: Path,
        line_color: str = "#1f77b4",
        line_width: float = 2.0,
        map_style: str = "light",
        zoom: float = 1.0,
    ) -> None:
        if df.empty:
            raise ValueError("No trajectory points available for rendering")
        self.df = df
        self.frames_dir = frames_dir
        self.line_color = line_color
        self.line_width = line_width
        self.map_style = map_style if map_style in STYLE_THEMES else "light"
        self.zoom = max(zoom, 0.1)
        ensure_directory(self.frames_dir)

    def _compute_bounds(self) -> tuple[float, float, float, float]:
        lat_min, lat_max = self.df["latitude"].min(), self.df["latitude"].max()
        lon_min, lon_max = self.df["longitude"].min(), self.df["longitude"].max()
        lat_range = (lat_max - lat_min) or 0.001
        lon_range = (lon_max - lon_min) or 0.001
        margin_lat = lat_range * 0.1 / self.zoom
        margin_lon = lon_range * 0.1 / self.zoom
        return (
            lat_min - margin_lat,
            lat_max + margin_lat,
            lon_min - margin_lon,
            lon_max + margin_lon,
        )

    def _theme(self) -> dict:
        return STYLE_THEMES.get(self.map_style, STYLE_THEMES["light"])

    def render_frames(self) -> Iterable[Path]:
        """Render frames showing the trajectory progression."""

        lat_min, lat_max, lon_min, lon_max = self._compute_bounds()
        theme = self._theme()
        frame_paths = []

        for idx in range(1, len(self.df) + 1):
            segment = self.df.iloc[:idx]
            current = segment.iloc[-1]

            fig, ax = plt.subplots(figsize=(8, 8))
            fig.patch.set_facecolor(theme["bg"])
            ax.set_facecolor(theme["bg"])
            ax.grid(True, color=theme["grid"], linewidth=0.8)
            ax.set_xlabel("Longitude", color=theme["text"])
            ax.set_ylabel("Latitude", color=theme["text"])
            ax.tick_params(colors=theme["text"])

            ax.set_xlim(lon_min, lon_max)
            ax.set_ylim(lat_min, lat_max)

            ax.plot(
                segment["longitude"],
                segment["latitude"],
                color=self.line_color,
                linewidth=self.line_width,
                alpha=0.9,
            )
            ax.scatter(
                current["longitude"],
                current["latitude"],
                color=theme["point"],
                s=40,
                zorder=5,
                label="Current position",
            )
            ax.legend(loc="lower right")
            ax.set_title(
                f"Trajectory @ {current['timestamp'].strftime('%Y-%m-%d %H:%M:%S %Z')}",
                color=theme["text"],
            )

            frame_name = self.frames_dir / f"frame_{idx:05d}.png"
            fig.tight_layout()
            fig.savefig(frame_name)
            plt.close(fig)
            frame_paths.append(frame_name)
        logger.info("Generated %s frames in %s", len(frame_paths), self.frames_dir)
        return frame_paths
