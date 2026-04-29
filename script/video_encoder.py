"""
Google Timeline Visualizer — Phase 3: 영상 합성
파일: video_encoder.py

PNG 시퀀스 → MP4 (H.264) + GIF (팔레트 최적화 2-pass)
FFmpeg 필요: https://ffmpeg.org/download.html

사용법:
  python script/video_encoder.py 2026-03
  python script/video_encoder.py 2026-03 --mp4-only --crf 18
  python script/video_encoder.py 2026-03 --gif-only --gif-fps 15 --gif-scale 960
  python script/video_encoder.py 2025-12 --preset fast --fps 30
"""

import re
import sys
import shutil
import argparse
import logging
import subprocess
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

OUT_ROOT = Path("output")

# ──────────────────────────────────────────
# FFmpeg 설치 확인
# ──────────────────────────────────────────

_INSTALL_GUIDE = """
FFmpeg 설치 방법 (Windows):
  방법 1 — winget (권장):
    winget install Gyan.FFmpeg
    ※ 설치 후 터미널 재시작 필요

  방법 2 — 직접 다운로드:
    https://www.gyan.dev/ffmpeg/builds/ 에서
    "release essentials" 빌드 다운로드 → 압축 해제 →
    bin/ 폴더 경로를 시스템 PATH에 추가

  방법 3 — Chocolatey:
    choco install ffmpeg

  방법 4 — Scoop:
    scoop install ffmpeg
"""


def check_ffmpeg() -> str:
    """
    FFmpeg 실행 파일 경로 반환.
    우선순위:
      1. 시스템 PATH의 ffmpeg
      2. imageio-ffmpeg 번들 바이너리 (pip install imageio-ffmpeg)
      3. 둘 다 없으면 설치 안내 후 종료
    """
    # 1. 시스템 FFmpeg
    ffmpeg = shutil.which("ffmpeg")

    # 2. imageio-ffmpeg 번들
    if not ffmpeg:
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            log.info("시스템 FFmpeg 없음 → imageio-ffmpeg 번들 사용")
        except ImportError:
            pass

    if not ffmpeg:
        log.error("FFmpeg를 찾을 수 없습니다.")
        log.error("imageio-ffmpeg 설치로 해결 가능: pip install imageio-ffmpeg")
        print(_INSTALL_GUIDE)
        sys.exit(1)

    result = subprocess.run(
        [ffmpeg, "-version"], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    version = result.stdout.split("\n")[0]
    log.info("FFmpeg: %s", version)
    return ffmpeg


# ──────────────────────────────────────────
# 프레임 디렉터리 검증
# ──────────────────────────────────────────

def get_frame_count(frames_dir: Path, period_str: str) -> int:
    """PNG 프레임 개수 확인. 없으면 종료."""
    if not frames_dir.exists():
        log.error("프레임 디렉터리 없음: %s", frames_dir)
        log.error("먼저 렌더러를 실행하세요:")
        log.error("  python script/frame_renderer.py %s", period_str)
        sys.exit(1)

    count = len(sorted(frames_dir.glob("frame_*.png")))
    if count == 0:
        log.error("프레임 파일 없음: %s", frames_dir)
        sys.exit(1)

    log.info("프레임 확인: %d장 ← %s", count, frames_dir)
    return count


# ──────────────────────────────────────────
# FFmpeg 실행 + 진행률 스트리밍
# ──────────────────────────────────────────

def _run_ffmpeg(
    ffmpeg: str,
    cmd: list[str],
    total_frames: int,
    label: str,
) -> None:
    """
    FFmpeg 서브프로세스 실행.
    stderr의 'frame=' 라인을 파싱해 10% 단위로 진행률 출력.
    """
    log.info("[%s] 시작", label)
    log.debug("CMD: %s", " ".join(cmd))

    proc = subprocess.Popen(
        [ffmpeg] + cmd,
        stderr=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    frame_re = re.compile(r"frame=\s*(\d+)")
    last_pct = -1

    for line in (proc.stderr or []):
        m = frame_re.search(line)
        if m and total_frames > 0:
            frame = int(m.group(1))
            pct   = int(frame / total_frames * 100)
            if pct >= last_pct + 10:
                bar   = "█" * (pct // 5) + "░" * (20 - pct // 5)
                log.info("  [%s] |%s| %3d%%  frame %d / %d",
                         label, bar, pct, frame, total_frames)
                last_pct = pct

    proc.wait()
    if proc.returncode != 0:
        log.error("[%s] FFmpeg 실패 (exit=%d). 위 로그를 확인하세요.", label, proc.returncode)
        sys.exit(1)


# ──────────────────────────────────────────
# MP4 인코딩
# ──────────────────────────────────────────

def encode_mp4(
    ffmpeg:       str,
    frames_dir:   Path,
    output_path:  Path,
    fps:          int,
    crf:          int,
    preset:       str,
    total_frames: int,
) -> None:
    """
    PNG 시퀀스 → MP4 (H.264, yuv420p).

    Args:
        crf:    품질 (0=무손실, 18=고품질, 23=기본, 28=저용량)
        preset: 인코딩 속도/압축 균형
                ultrafast < superfast < veryfast < faster < fast < medium < slow
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "-y",
        "-framerate", str(fps),
        "-i",         str(frames_dir / "frame_%06d.png"),
        "-c:v",       "libx264",
        "-pix_fmt",   "yuv420p",
        "-crf",       str(crf),
        "-preset",    preset,
        "-movflags",  "+faststart",   # 웹 스트리밍 최적화: 메타데이터를 파일 앞으로
        str(output_path),
    ]
    _run_ffmpeg(ffmpeg, cmd, total_frames, "MP4")

    size_mb = output_path.stat().st_size / 1024 / 1024
    log.info("[MP4] 저장 완료: %s  (%.1f MB)", output_path, size_mb)


# ──────────────────────────────────────────
# GIF 인코딩 (2-pass 팔레트 최적화)
# ──────────────────────────────────────────

def encode_gif(
    ffmpeg:       str,
    frames_dir:   Path,
    output_path:  Path,
    input_fps:    int,
    gif_fps:      int,
    gif_scale:    int,
    total_frames: int,
) -> None:
    """
    PNG 시퀀스 → GIF (팔레트 최적화 2-pass).

    Pass 1: 전체 프레임에서 최적 256색 팔레트 추출 (stats_mode=diff).
    Pass 2: 팔레트 기반 디더링으로 GIF 인코딩.

    Args:
        gif_fps:   GIF 출력 fps (15 권장 — 용량과 품질 균형)
        gif_scale: GIF 가로 픽셀 (세로는 비율 유지)
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    palette_path = output_path.parent / "_palette_tmp.png"

    # Pass 1 — 팔레트 생성
    vf_palette = (
        f"fps={gif_fps},"
        f"scale={gif_scale}:-1:flags=lanczos,"
        f"palettegen=stats_mode=diff"
    )
    cmd1 = [
        "-y",
        "-framerate", str(input_fps),
        "-i",         str(frames_dir / "frame_%06d.png"),
        "-vf",        vf_palette,
        str(palette_path),
    ]
    log.info("[GIF] Pass 1 — 팔레트 생성 중...")
    subprocess.run(
        [ffmpeg] + cmd1,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
    )

    # Pass 2 — GIF 인코딩
    vf_gif = (
        f"fps={gif_fps},"
        f"scale={gif_scale}:-1:flags=lanczos"
        f"[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5"
    )
    cmd2 = [
        "-y",
        "-framerate", str(input_fps),
        "-i",         str(frames_dir / "frame_%06d.png"),
        "-i",         str(palette_path),
        "-filter_complex", vf_gif,
        str(output_path),
    ]
    _run_ffmpeg(ffmpeg, cmd2, total_frames, "GIF")

    palette_path.unlink(missing_ok=True)

    size_mb = output_path.stat().st_size / 1024 / 1024
    log.info("[GIF] 저장 완료: %s  (%.1f MB)", output_path, size_mb)


# ──────────────────────────────────────────
# 메인 인코딩 로직
# ──────────────────────────────────────────

def encode(
    period_str: str,
    fps:        int   = 30,
    crf:        int   = 18,
    preset:     str   = "medium",
    gif_fps:    int   = 15,
    gif_scale:  int   = 960,
    mp4_only:   bool  = False,
    gif_only:   bool  = False,
    output_dir: Path | None = None,
) -> None:
    """
    PNG 시퀀스 → MP4 + GIF 합성.

    Args:
        period_str: 기간 문자열 ("2026-03" 등)
        fps:        PNG 시퀀스 fps (frame_renderer.py의 --fps와 일치해야 함)
        crf:        MP4 화질 (낮을수록 고품질·대용량)
        preset:     MP4 인코딩 속도 (fast 권장, medium이 기본)
        gif_fps:    GIF 출력 fps
        gif_scale:  GIF 가로 픽셀
        mp4_only:   MP4만 생성
        gif_only:   GIF만 생성
    """
    ffmpeg      = check_ffmpeg()
    period_slug = period_str.replace("~", "_")
    period_dir  = output_dir or OUT_ROOT / period_slug
    frames_dir  = period_dir / "frames"

    total_frames = get_frame_count(frames_dir, period_str)

    log.info("=== Phase 3 인코딩 시작 ===")
    log.info("기간: %s | 프레임: %d장 | 입력 fps: %d", period_str, total_frames, fps)

    if not gif_only:
        mp4_path = period_dir / f"{period_slug}.mp4"
        encode_mp4(ffmpeg, frames_dir, mp4_path, fps, crf, preset, total_frames)

    if not mp4_only:
        gif_path = period_dir / f"{period_slug}.gif"
        encode_gif(ffmpeg, frames_dir, gif_path, fps, gif_fps, gif_scale, total_frames)

    log.info("=== 인코딩 완료 ===")
    if not gif_only:
        mp4_path = period_dir / f"{period_slug}.mp4"
        log.info("  MP4 → %s  (%.1f MB)", mp4_path, mp4_path.stat().st_size / 1024 / 1024)
    if not mp4_only:
        gif_path = period_dir / f"{period_slug}.gif"
        log.info("  GIF → %s  (%.1f MB)", gif_path, gif_path.stat().st_size / 1024 / 1024)


# ──────────────────────────────────────────
# CLI
# ──────────────────────────────────────────

def main() -> None:
    """CLI 진입점"""
    ap = argparse.ArgumentParser(
        description="Phase 3 영상 합성 — PNG 시퀀스 → MP4 + GIF",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python script/video_encoder.py 2026-03
  python script/video_encoder.py 2026-03 --mp4-only --crf 18 --preset fast
  python script/video_encoder.py 2026-03 --gif-only --gif-fps 10 --gif-scale 640
  python script/video_encoder.py 2025-12 --fps 30 --crf 23

CRF 품질 가이드:
  0        무손실 (매우 큰 파일)
  18       고품질 (기본값, 권장)
  23       FFmpeg 기본값 (균형)
  28       저용량 (품질 저하)

preset 속도 (빠름 → 느림 → 압축률 증가):
  ultrafast > superfast > veryfast > faster > fast > medium > slow
        """,
    )
    ap.add_argument("period",
                    help="기간 (예: 2026-03  또는  2025-12-20~2025-12-31)")
    ap.add_argument("--fps",
                    type=int, default=30,
                    help="PNG 시퀀스 fps (렌더러 --fps와 일치, 기본 30)")
    ap.add_argument("--crf",
                    type=int, default=18,
                    help="MP4 화질 0~51 (낮을수록 고품질, 기본 18)")
    ap.add_argument("--preset",
                    default="medium",
                    choices=["ultrafast","superfast","veryfast","faster",
                             "fast","medium","slow","slower"],
                    help="인코딩 속도 (기본 medium)")
    ap.add_argument("--gif-fps",
                    type=int, default=15,
                    help="GIF 출력 fps (기본 15)")
    ap.add_argument("--gif-scale",
                    type=int, default=960,
                    help="GIF 출력 가로 픽셀 (기본 960, 세로 비율 유지)")
    ap.add_argument("--mp4-only",
                    action="store_true",
                    help="MP4만 생성")
    ap.add_argument("--gif-only",
                    action="store_true",
                    help="GIF만 생성")
    ap.add_argument("--output",
                    type=Path, default=None,
                    help="출력 폴더 (기본: output/<period>/)")
    args = ap.parse_args()

    encode(
        period_str = args.period,
        fps        = args.fps,
        crf        = args.crf,
        preset     = args.preset,
        gif_fps    = args.gif_fps,
        gif_scale  = args.gif_scale,
        mp4_only   = args.mp4_only,
        gif_only   = args.gif_only,
        output_dir = args.output,
    )


if __name__ == "__main__":
    main()
