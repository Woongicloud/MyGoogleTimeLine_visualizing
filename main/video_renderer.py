"""Video rendering using ffmpeg-python."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

import ffmpeg

logger = logging.getLogger(__name__)


def render_video(frames_dir: Path, output_path: Path, fps: int = 30) -> Path:
    """Stitch PNG frames into an MP4 video using ffmpeg."""

    pattern = frames_dir / "frame_%05d.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Rendering video to %s", output_path)
    (
        ffmpeg.input(str(pattern), framerate=fps)
        .output(str(output_path), vcodec="libx264", pix_fmt="yuv420p", movflags="faststart")
        .overwrite_output()
        .run(quiet=True)
    )

    return output_path
