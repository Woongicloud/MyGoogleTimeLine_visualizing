from __future__ import annotations

import argparse
from pathlib import Path

from .map_renderer import MapRenderer
from .parser import TimelineParser
from .trajectory import TrajectoryBuilder, TrajectoryOptions
from .utils import ensure_directory
from .video_renderer import VideoRenderer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render Google Timeline exports to animated videos.")
    parser.add_argument("--input", required=True, help="Path to the timeline JSON file under data/")
    parser.add_argument("--output", required=True, help="Output video path (MP4)")
    parser.add_argument("--frames-dir", default=None, help="Directory to store generated frames")
    parser.add_argument("--line-color", default="red", help="Polyline color")
    parser.add_argument("--line-width", type=float, default=2.5, help="Polyline width")
    parser.add_argument("--map-style", default="osm", choices=["osm", "light", "dark", "satellite"], help="Basemap style")
    parser.add_argument("--zoom", type=int, default=13, help="Basemap zoom level")
    parser.add_argument("--fps", type=int, default=24, help="Frames per second for the video")
    parser.add_argument("--max-accuracy", type=float, default=None, help="Discard points with accuracy worse than this (meters)")
    parser.add_argument("--resample-seconds", type=int, default=None, help="Resample trajectory to a fixed time step in seconds")
    parser.add_argument("--smooth-window", type=int, default=5, help="Rolling window size for smoothing (set 1 to disable)")
    parser.add_argument("--no-interpolate", action="store_true", help="Disable interpolation when resampling")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    data_dir = (Path.cwd() / "data").resolve()
    resolved_input = input_path.resolve()
    if data_dir not in resolved_input.parents:
        raise ValueError("Input must be inside the data/ directory to avoid modifying raw data.")

    frames_dir = Path(args.frames_dir) if args.frames_dir else Path("output/frames") / output_path.stem
    ensure_directory(frames_dir)

    timeline_parser = TimelineParser(max_accuracy=args.max_accuracy)
    df = timeline_parser.parse(input_path)
    if df.empty:
        raise RuntimeError("No valid location points found in the input file.")

    trajectory_options = TrajectoryOptions(
        resample_seconds=args.resample_seconds,
        smooth_window=args.smooth_window,
        interpolate=not args.no_interpolate,
    )
    builder = TrajectoryBuilder(trajectory_options)
    trajectory_df = builder.build(df)

    map_renderer = MapRenderer(
        line_color=args.line_color,
        line_width=args.line_width,
        map_style=args.map_style,
        zoom=args.zoom,
    )
    map_renderer.render_frames(trajectory_df, frames_dir)

    video_renderer = VideoRenderer(fps=args.fps)
    video_renderer.render(frames_dir, output_path)
    print(f"Video saved to {output_path}")


if __name__ == "__main__":
    main()
