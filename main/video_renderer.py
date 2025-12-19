from __future__ import annotations

from pathlib import Path

import ffmpeg

from .utils import ensure_directory


class VideoRenderer:
    def __init__(self, fps: int = 24):
        self.fps = fps

    def render(self, frames_dir: Path, output_path: Path) -> Path:
        ensure_directory(output_path.parent)
        input_pattern = str(frames_dir / "frame_%05d.png")
        stream = (
            ffmpeg.input(input_pattern, framerate=self.fps)
            .output(str(output_path), vcodec="libx264", pix_fmt="yuv420p", r=self.fps)
            .overwrite_output()
        )
        stream.run(quiet=True)
        return output_path
