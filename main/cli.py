"""Command-line entry point for the visualization toolkit."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .map_renderer import MapRenderer
from .parser import parse_timeline
from .trajectory import build_trajectory
from .utils import configure_logging, ensure_directory
from .video_renderer import render_video

logger = logging.getLogger(__name__)


def positive_int(value: str) -> int:
    ivalue = int(value)
    if ivalue <= 0:
        raise argparse.ArgumentTypeError("Value must be positive")
    return ivalue


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Visualize Google Timeline exports as videos.")
    parser.add_argument("--input", required=True, help="Path to Google Takeout JSON file.")
    parser.add_argument(
        "--output", default="output/videos/trajectory.mp4", help="Destination video file path."
    )
    parser.add_argument("--line-color", default="#1f77b4", help="Polyline color.")
    parser.add_argument(
        "--line-width", type=float, default=2.0, help="Polyline width in matplotlib units."
    )
    parser.add_argument(
        "--map-style",
        choices=["light", "dark", "minimal"],
        default="light",
        help="Background map style.",
    )
    parser.add_argument("--zoom", type=float, default=1.0, help="Zoom factor (higher zooms in).")
    parser.add_argument("--fps", type=positive_int, default=30, help="Frames per second for the video.")
    parser.add_argument(
        "--resample",
        help="Optional pandas offset alias for resampling (e.g., '30S', '5T').",
    )
    parser.add_argument(
        "--smooth-window",
        type=positive_int,
        default=1,
        help="Window size for moving average smoothing on coordinates.",
    )
    parser.add_argument(
        "--frames-dir",
        default="output/frames",
        help="Directory to store intermediate frame images.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity level.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging(getattr(logging, args.log_level))

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file does not exist: {input_path}")

    output_path = Path(args.output)
    frames_dir = Path(args.frames_dir)
    ensure_directory(output_path.parent)
    ensure_directory(frames_dir)

    logger.info("Loading timeline data from %s", input_path)
    parsed = parse_timeline(input_path)
    if parsed.empty:
        logger.error("No valid location points parsed; aborting.")
        return

    logger.info("Building trajectory with resample=%s, smoothing_window=%s", args.resample, args.smooth_window)
    trajectory = build_trajectory(parsed, resample_freq=args.resample, smoothing_window=args.smooth_window)

    renderer = MapRenderer(
        trajectory,
        frames_dir,
        line_color=args.line_color,
        line_width=args.line_width,
        map_style=args.map_style,
        zoom=args.zoom,
    )
    renderer.render_frames()

    render_video(frames_dir, output_path, fps=args.fps)
    logger.info("Video created at %s", output_path)


if __name__ == "__main__":
    main()
