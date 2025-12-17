# Google Timeline Trajectory Visualizer

Production-ready CLI tool for turning Google Location History / Timeline exports into animated trajectory videos. Raw Takeout JSON lives under `data/` and is never modified; outputs land in `output/`.

## Features
- Robust parser for `timelineEdits`, `timelineObjects`, and raw signals with `latE7` / `lngE7` coordinates.
- Deduplication of stationary points plus optional accuracy filtering.
- Resampling, interpolation, and rolling smoothing to reduce GPS noise.
- Basemap rendering (OpenStreetMap, CartoDB light/dark, Esri satellite) with a growing polyline and highlighted current position.
- Frame generation followed by FFmpeg stitching into MP4 videos.

## Repository layout
```
README.md
environment.yml
main/
    __init__.py
    cli.py              # Entry point
    parser.py           # Timeline JSON parsing & normalization
    trajectory.py       # Trajectory reconstruction & smoothing
    map_renderer.py     # Map rendering logic
    video_renderer.py   # Frame generation + ffmpeg integration
    utils.py
data/                   # Read-only input JSON (provided by you)
output/
    frames/
    videos/
```

## Environment setup
1. Install [Conda](https://docs.conda.io/en/latest/) or Mambaforge.
2. Create the environment:
   ```bash
   conda env create -f environment.yml
   conda activate timeline-visualizer
   ```

### External requirements
- **FFmpeg** is required at runtime. The conda environment installs `ffmpeg`, but system availability is recommended for best performance.
- Optional: set `MAPBOX_TOKEN` if you plan to use private map tiles (not required for the built-in providers).

## Usage
Ensure your exported Takeout JSON is placed under `data/` (e.g., `data/my_timeline.json`). Then run:
```bash
python -m main.cli \
    --input data/my_timeline.json \
    --output output/videos/my_route.mp4 \
    --line-color red \
    --map-style dark \
    --fps 30
```

### CLI options
- `--input`: Path to the Takeout JSON (must reside inside `data/`).
- `--output`: Target MP4 path (e.g., `output/videos/run.mp4`).
- `--frames-dir`: Optional folder for intermediate PNG frames (default: `output/frames/<output-stem>`).
- `--line-color`: Polyline color (any Matplotlib-compatible spec).
- `--line-width`: Polyline width in pixels.
- `--map-style`: `osm`, `light`, `dark`, or `satellite`.
- `--zoom`: Basemap zoom level (Web Mercator tiles).
- `--fps`: Frames per second in the output video.
- `--max-accuracy`: Drop points with accuracy worse than this many meters.
- `--resample-seconds`: Resample trajectory to a fixed timestep (seconds).
- `--smooth-window`: Rolling window size for smoothing (set to `1` to disable).
- `--no-interpolate`: Disable interpolation when resampling.

## Processing pipeline
1. **Parse & normalize** – Extract valid GPS points, convert `latE7`/`lngE7` to degrees, order by timestamp, and deduplicate stationary points.
2. **Trajectory reconstruction** – Optional resampling to uniform time steps, interpolation of gaps, and rolling smoothing to suppress noise.
3. **Map rendering** – Project to Web Mercator, draw a growing polyline with current position marker, and fetch tiles from the selected provider.
4. **Video assembly** – Save sequential PNG frames and stitch them into an H.264 MP4 via FFmpeg.

## Performance & limitations
- Rendering many points can be slow; use `--resample-seconds` or a smaller bounding box to reduce frame count.
- Basemap tiles depend on external servers; respect provider usage limits and cache policies.
- Input JSON schemas vary across Takeout versions; the parser targets common `timelineEdits` / `timelineObjects` patterns and raw signals, but rare fields may need additional handlers.

## Privacy notice
This tool operates on sensitive location data. Keep raw exports private, avoid committing them to version control, and store generated outputs securely.
