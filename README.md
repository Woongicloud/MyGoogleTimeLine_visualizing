# Google Timeline Visualizer

A production-ready template to parse Google Location History / Timeline exports (JSON from Google Takeout), reconstruct GPS trajectories, and render animated videos of your movement.

## Repository Layout

```
README.md
environment.yml
main/
    __init__.py
    cli.py
    parser.py
    trajectory.py
    map_renderer.py
    video_renderer.py
    utils.py
data/                # Read-only Google Takeout JSON exports
output/
    frames/           # Intermediate frames (auto-generated)
    videos/           # Final rendered videos
```

## Input Data

Place your `*Timeline*.json` file from Google Takeout under `data/`. The parser expects entries similar to:

- `timelineEdits[]` with `rawSignal.signal.position.point.latE7` / `lngE7`
- ISO 8601 timestamps (UTC) or `timestampMs`
- Mixed signal types (position, wifiScan, etc.) — only position points are used

The input files in `data/` are treated as **read-only** and are never modified by the tool.

## Environment Setup

This project targets Python 3.11. Create the conda environment:

```bash
conda env create -f environment.yml
conda activate google-timeline-visualizer
```

### External Dependencies

- **FFmpeg** is required on your system for video encoding. Install it via your package manager (e.g., `apt install ffmpeg`, `brew install ffmpeg`).
- Optional map tiles (e.g., Mapbox Static Maps) can be used if you extend the renderer; load API keys via environment variables as needed.

## Usage

Basic CLI invocation:

```bash
python -m main.cli \
    --input data/MyTimeLine.json \
    --output output/videos/my_route.mp4 \
    --line-color "#1f77b4" \
    --map-style dark \
    --fps 30
```

Key options:

- `--input` **(required)**: Path to the Google Takeout JSON.
- `--output`: Destination MP4 path (directories auto-created).
- `--line-color`: Polyline color (any Matplotlib-compatible spec).
- `--line-width`: Polyline width.
- `--map-style`: One of `light`, `dark`, `minimal`.
- `--zoom`: Adjusts padding around the trajectory (higher = closer).
- `--fps`: Frames per second for the video.
- `--resample`: Pandas offset alias to enforce fixed timesteps (e.g., `30S`).
- `--smooth-window`: Moving average window for noise smoothing.
- `--frames-dir`: Where to store intermediate PNG frames.
- `--log-level`: Logging verbosity.

## How It Works

1. **Parse**: `main.parser.parse_timeline` reads the JSON, extracts valid GPS points, converts `latE7/lngE7` to degrees, orders by timestamp, and removes consecutive duplicates.
2. **Reconstruct**: `main.trajectory.build_trajectory` can resample to fixed intervals and smooth coordinates.
3. **Render Frames**: `main.map_renderer.MapRenderer` draws the growing path and current position for each timestep using Matplotlib.
4. **Encode Video**: `main.video_renderer.render_video` stitches frames into an MP4 via FFmpeg (`libx264`, `yuv420p`).

## Notes and Limitations

- The renderer uses a simple Matplotlib basemap for portability; you can extend it with external tile APIs if desired.
- Large exports can produce many frames; consider `--resample` to reduce frame count and `--smooth-window` to denoise.
- Ensure sufficient disk space in `output/frames` when generating long trajectories.

## Privacy

Google Timeline exports contain sensitive location history. Keep your data private, avoid committing personal JSON files, and review outputs before sharing.

## Contributing

Issues and pull requests are welcome. Please avoid uploading private location data; use synthetic or redacted examples when reporting bugs.
