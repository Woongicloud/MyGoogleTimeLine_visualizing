from __future__ import annotations

from pathlib import Path
from typing import Iterable, Tuple, TYPE_CHECKING

from .utils import ensure_clean_directory

if TYPE_CHECKING:
    from matplotlib.axes import Axes
    from matplotlib.figure import Figure


class MapRenderer:
    def __init__(
        self,
        line_color: str = "red",
        line_width: float = 2.5,
        map_style: str = "osm",
        zoom: int = 13,
    ) -> None:
        import contextily as ctx
        import matplotlib.pyplot as plt
        from pyproj import Transformer

        self.ctx = ctx
        self.plt = plt
        self.map_styles = {
            "osm": ctx.providers.OpenStreetMap.Mapnik,
            "light": ctx.providers.CartoDB.Positron,
            "dark": ctx.providers.CartoDB.DarkMatter,
            "satellite": ctx.providers.Esri.WorldImagery,
        }

        self.line_color = line_color
        self.line_width = line_width
        self.map_style = map_style if map_style in self.map_styles else "osm"
        self.zoom = zoom
        self.transformer = Transformer.from_crs("epsg:4326", "epsg:3857", always_xy=True)

    def render_frames(self, df, frames_dir: Path) -> Iterable[Path]:
        ensure_clean_directory(frames_dir)
        xs, ys = self.transformer.transform(df["longitude"].values, df["latitude"].values)
        extent = self._extent_with_padding(xs, ys)

        frame_paths = []
        from tqdm import tqdm

        for idx in tqdm(range(len(df)), desc="Rendering frames"):
            fig, ax = self._setup_map(extent)
            ax.plot(xs[: idx + 1], ys[: idx + 1], color=self.line_color, linewidth=self.line_width)
            ax.scatter(xs[idx], ys[idx], color="blue", s=40, zorder=5)
            ax.set_axis_off()

            frame_path = frames_dir / f"frame_{idx:05d}.png"
            fig.savefig(frame_path, dpi=150, bbox_inches="tight", pad_inches=0)
            self.plt.close(fig)
            frame_paths.append(frame_path)
        return frame_paths

    def _setup_map(self, extent: Tuple[float, float, float, float]):
        fig, ax = self.plt.subplots(figsize=(8, 8))
        ax.set_xlim(extent[0], extent[1])
        ax.set_ylim(extent[2], extent[3])
        self.ctx.add_basemap(ax, source=self.map_styles[self.map_style], zoom=self.zoom)
        return fig, ax

    def _extent_with_padding(self, xs, ys, padding_ratio: float = 0.05) -> Tuple[float, float, float, float]:
        min_x, max_x = xs.min(), xs.max()
        min_y, max_y = ys.min(), ys.max()
        dx = (max_x - min_x) or 1
        dy = (max_y - min_y) or 1
        pad_x = dx * padding_ratio
        pad_y = dy * padding_ratio
        return min_x - pad_x, max_x + pad_x, min_y - pad_y, max_y + pad_y
