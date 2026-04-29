"""
Google Timeline Visualizer — Phase 2: 프레임 렌더러
파일: frame_renderer.py

입력 : timeline.db (Phase 1 출력) + 기간 파라미터
출력 : output/<period>/frames/frame_XXXXXX.png

기간 포맷:
  2025-12               → 2025-12-01 ~ 2025-12-31
  2025-12-20~2025-12-31 → 해당 날짜 범위만

사용법:
  python script/frame_renderer.py 2025-12
  python script/frame_renderer.py 2025-12 --map cartodb-light
  python script/frame_renderer.py 2025-12 --map osm --duration 30 --fps 24
  python script/frame_renderer.py 2025-12 --config my_config.yml
"""

import os
import re
import sys
import math
import sqlite3
import argparse
import logging
from pathlib import Path
from datetime import date, timedelta
from typing import Optional

# render_config는 같은 script/ 디렉터리에 있으므로 경로 추가
sys.path.insert(0, str(Path(__file__).parent))
from render_config import RenderConfig, TILE_PROVIDERS, PROVIDER_NAMES, load_config

from dotenv import load_dotenv
import requests
from PIL import Image, ImageDraw

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ──────────────────────────────────────────
# 기간 파싱
# ──────────────────────────────────────────

def parse_period(period_str: str) -> tuple[date, date]:
    """
    기간 문자열 → (start_date, end_date).

    지원 포맷:
      "2025-12"               → 2025-12-01 ~ 2025-12-31
      "2025-12-20~2025-12-31" → 2025-12-20 ~ 2025-12-31
    """
    range_pat = r"^(\d{4}-\d{2}-\d{2})~(\d{4}-\d{2}-\d{2})$"
    month_pat = r"^(\d{4})-(\d{2})$"

    m = re.match(range_pat, period_str)
    if m:
        start = date.fromisoformat(m.group(1))
        end   = date.fromisoformat(m.group(2))
        if start > end:
            raise ValueError(f"시작일이 종료일보다 늦습니다: {period_str}")
        return start, end

    m = re.match(month_pat, period_str)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if not (1 <= month <= 12):
            raise ValueError(f"월 범위 오류 (1~12): {month}")
        start = date(year, month, 1)
        end   = (
            date(year, month + 1, 1) if month < 12 else date(year + 1, 1, 1)
        ) - timedelta(days=1)
        return start, end

    raise ValueError(
        f"잘못된 기간 포맷: '{period_str}'\n"
        "허용 포맷: 2025-12  또는  2025-12-20~2025-12-31"
    )


# ──────────────────────────────────────────
# DB 쿼리 · 검증
# ──────────────────────────────────────────

def fetch_track(conn: sqlite3.Connection, start: date, end: date) -> list[dict]:
    """
    기간 내 GPS 포인트를 시간순으로 조회.
    데이터 없으면 사용 가능한 월 목록을 출력하고 종료.
    """
    start_ts = f"{start.isoformat()}T00:00:00+00:00"
    end_ts   = f"{end.isoformat()}T23:59:59+00:00"

    cur = conn.execute(
        """
        SELECT timestamp, lat, lng, speed_ms, activity
        FROM gps_points
        WHERE timestamp >= ? AND timestamp <= ?
        ORDER BY timestamp ASC
        """,
        (start_ts, end_ts),
    )
    cols  = [d[0] for d in cur.description]
    track = [dict(zip(cols, row)) for row in cur.fetchall()]

    if not track:
        available = conn.execute(
            "SELECT DISTINCT year, month FROM gps_points ORDER BY year, month"
        ).fetchall()
        log.error("❌ 해당 기간(%s ~ %s)의 데이터가 없습니다.", start, end)
        if available:
            months_str = ", ".join(f"{y}-{mo:02d}" for y, mo in available)
            log.error("   파싱된 기간: %s", months_str)
            log.error("   사용법: python script/timeline_parser.py --summary")
        else:
            log.error("   DB가 비어 있습니다. 먼저 파서를 실행하세요:")
            log.error("   python script/timeline_parser.py <파일경로>")
        sys.exit(1)

    log.info("GPS 포인트 %d건 로드 (%s ~ %s)", len(track), start, end)
    return track


# ──────────────────────────────────────────
# 지오 유틸
# ──────────────────────────────────────────

def compute_bbox(
    track: list[dict], cfg: RenderConfig
) -> tuple[float, float, float, float]:
    """
    GPS 트랙 bounding box 계산.

    Returns:
        (min_lat, min_lng, max_lat, max_lng)
    """
    lats = [p["lat"] for p in track]
    lngs = [p["lng"] for p in track]
    lat_span = max(lats) - min(lats)
    lng_span = max(lngs) - min(lngs)
    pad_lat  = max(lat_span * cfg.bbox_padding, cfg.bbox_min_pad_deg)
    pad_lng  = max(lng_span * cfg.bbox_padding, cfg.bbox_min_pad_deg)
    return (
        min(lats) - pad_lat,
        min(lngs) - pad_lng,
        max(lats) + pad_lat,
        max(lngs) + pad_lng,
    )


def _mercator_x(lng: float) -> float:
    return (lng + 180) / 360


def _mercator_y(lat: float) -> float:
    r = math.radians(lat)
    return (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2


def latlng_to_pixel(
    lat: float,
    lng: float,
    bbox: tuple[float, float, float, float],
    cfg: RenderConfig,
) -> tuple[int, int]:
    """위경도 → Mercator 투영 픽셀 좌표"""
    min_lat, min_lng, max_lat, max_lng = bbox
    x0, x1 = _mercator_x(min_lng), _mercator_x(max_lng)
    y0, y1 = _mercator_y(max_lat), _mercator_y(min_lat)  # Y 반전

    px = int(((_mercator_x(lng) - x0) / (x1 - x0)) * cfg.map_w)
    py = int(((_mercator_y(lat) - y0) / (y1 - y0)) * cfg.map_h)
    return (
        max(0, min(cfg.map_w - 1, px)),
        max(0, min(cfg.map_h - 1, py)),
    )


def _compute_zoom(
    min_lat: float, min_lng: float, max_lat: float, max_lng: float,
    cfg: RenderConfig,
) -> int:
    """bbox + 출력 해상도 → 최적 줌 레벨 (0~17)"""
    TILE_SIZE = 256
    lat_frac  = abs(_mercator_y(max_lat) - _mercator_y(min_lat))
    lng_frac  = abs(max_lng - min_lng) / 360
    lat_zoom  = math.floor(math.log2(cfg.map_h / TILE_SIZE / lat_frac)) if lat_frac > 1e-9 else 17
    lng_zoom  = math.floor(math.log2(cfg.map_w / TILE_SIZE / lng_frac)) if lng_frac > 1e-9 else 17
    return max(0, min(17, min(lat_zoom, lng_zoom)))


# ──────────────────────────────────────────
# 배경 타일 (단일 진입점)
# ──────────────────────────────────────────

def _fetch_tile_stitched(
    bbox: tuple[float, float, float, float],
    url_template: str,
    cfg: RenderConfig,
    cache_path: Path,
) -> Image.Image:
    """staticmap으로 XYZ 타일 스티칭 (CartoDB/OSM/Stadia 모두 지원)"""
    from staticmap import StaticMap

    min_lat, min_lng, max_lat, max_lng = bbox
    center = ((min_lng + max_lng) / 2, (min_lat + max_lat) / 2)
    zoom   = _compute_zoom(min_lat, min_lng, max_lat, max_lng, cfg)
    log.info("타일 스티칭 (provider=%s, zoom=%d)...", cfg.provider, zoom)

    m   = StaticMap(cfg.map_w, cfg.map_h, url_template=url_template)
    img = m.render(zoom=zoom, center=center)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(cache_path)
    log.info("배경 타일 저장: %s", cache_path)
    return img.convert("RGB")


def _fetch_mapbox(
    bbox: tuple[float, float, float, float],
    cfg: RenderConfig,
    cache_path: Path,
) -> Image.Image:
    """Mapbox Static API 타일 (MAPBOX_TOKEN 필요)"""
    token = cfg.mapbox_token
    if not token:
        raise EnvironmentError(
            "Mapbox 공급자는 MAPBOX_TOKEN이 필요합니다.\n"
            ".env 파일에 MAPBOX_TOKEN=pk.eyJ1... 를 추가하세요."
        )
    style    = cfg.custom_url or "mapbox/dark-v11"
    min_lat, min_lng, max_lat, max_lng = bbox
    bbox_str = f"[{min_lng:.6f},{min_lat:.6f},{max_lng:.6f},{max_lat:.6f}]"
    url = (
        f"https://api.mapbox.com/styles/v1/{style}/static/"
        f"{bbox_str}/{cfg.map_w}x{cfg.map_h}?access_token={token}&logo=false"
    )
    log.info("Mapbox Static API 요청 중 (style=%s)...", style)
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(resp.content)
    log.info("배경 타일 저장: %s", cache_path)
    return Image.open(cache_path).convert("RGB")


def fetch_base_tile(
    bbox: tuple[float, float, float, float],
    cfg: RenderConfig,
    cache_path: Path,
) -> Image.Image:
    """
    배경 타일 로드. 우선순위:
      1. 로컬 캐시 (provider 이름 포함 → 공급자 전환 시 자동 갱신)
      2. Mapbox Static API  (provider="mapbox")
      3. XYZ 타일 스티칭    (나머지 모든 공급자)
      4. 단색 폴백          (#1e1e1e, 타일 요청 실패 시)
    """
    if cache_path.exists():
        log.info("배경 타일 캐시 사용: %s", cache_path)
        return Image.open(cache_path).convert("RGB")

    try:
        if cfg.provider == "mapbox":
            return _fetch_mapbox(bbox, cfg, cache_path)

        url = cfg.tile_url()   # custom_url 또는 TILE_PROVIDERS 값
        return _fetch_tile_stitched(bbox, url, cfg, cache_path)

    except Exception as e:
        log.warning("타일 로드 실패 (%s) → 단색 배경 사용", e)
        img = Image.new("RGB", (cfg.map_w, cfg.map_h), (30, 30, 30))
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(cache_path)
        return img


# ──────────────────────────────────────────
# 프레임 렌더링
# ──────────────────────────────────────────

def render_frame(
    base_img : Image.Image,
    track    : list[dict],
    up_to_idx: int,
    bbox     : tuple[float, float, float, float],
    cfg      : RenderConfig,
) -> Image.Image:
    """
    단일 프레임 렌더링.

    Args:
        up_to_idx: 현재 프레임에서 표시할 마지막 GPS 포인트 인덱스
    """
    img  = base_img.copy().convert("RGBA")
    draw = ImageDraw.Draw(img, "RGBA")

    start   = max(0, up_to_idx - cfg.trail_len)
    visible = track[start : up_to_idx + 1]
    n       = len(visible)
    a_range = cfg.trail_alpha_max - cfg.trail_alpha_min

    for i, pt in enumerate(visible):
        alpha  = int(cfg.trail_alpha_min + a_range * (i / max(n - 1, 1)))
        color  = cfg.activity_colors.get(pt["activity"] or "unknown", (200, 200, 200))
        radius = cfg.activity_radius.get(pt["activity"] or "unknown", 2)
        px, py = latlng_to_pixel(pt["lat"], pt["lng"], bbox, cfg)
        r      = radius if i < n - 1 else radius * cfg.current_pt_scale + 1

        draw.ellipse(
            [px - r, py - r, px + r, py + r],
            fill=(*color, alpha),
        )

    # 현재 위치 외곽선
    if visible:
        curr   = visible[-1]
        px, py = latlng_to_pixel(curr["lat"], curr["lng"], bbox, cfg)
        base_r = cfg.activity_radius.get(curr["activity"] or "unknown", 2)
        r_out  = base_r * cfg.current_pt_scale + cfg.outline_extra_r
        draw.ellipse(
            [px - r_out, py - r_out, px + r_out, py + r_out],
            outline=(*cfg.outline_color, cfg.outline_alpha),
            width=2,
        )

    return img.convert("RGB")


def draw_hud(
    img       : Image.Image,
    timestamp : str,
    frame_idx : int,
    total     : int,
    cfg       : RenderConfig,
) -> Image.Image:
    """상단 HUD 오버레이 (날짜·시각 + 진행 바). cfg.hud.enabled=false 이면 스킵."""
    if not cfg.hud.enabled:
        return img

    h    = cfg.hud
    pb   = h.progress_bar
    draw = ImageDraw.Draw(img, "RGBA")

    # 배너 배경
    draw.rectangle([(0, 0), (cfg.map_w, h.banner_height)], fill=(0, 0, 0, h.banner_alpha))

    # 날짜·시각 텍스트
    font = None
    if h.font_path:
        try:
            from PIL import ImageFont
            font = ImageFont.truetype(h.font_path, h.font_size)
        except Exception:
            pass
    if font is None:
        try:
            from PIL import ImageFont
            font = ImageFont.truetype("arial.ttf", h.font_size)
        except Exception:
            font = None

    dt_str = timestamp[:19].replace("T", " ")
    draw.text((12, (h.banner_height - h.font_size) // 2), dt_str,
              fill=(255, 255, 255, 255), font=font)

    # 진행 바
    if pb.enabled:
        progress = frame_idx / max(total - 1, 1)
        bx0 = cfg.map_w - pb.margin_right - pb.width
        bx1 = cfg.map_w - pb.margin_right
        by0 = pb.top_offset
        by1 = pb.top_offset + pb.height
        fill_w = int(pb.width * progress)

        draw.rectangle([(bx0, by0), (bx1, by1)],
                       outline=(*pb.outline_color, pb.outline_alpha))
        if fill_w > 0:
            draw.rectangle([(bx0, by0), (bx0 + fill_w, by1)],
                           fill=(*pb.color, pb.alpha))

    return img


# ──────────────────────────────────────────
# 메인 렌더 루프
# ──────────────────────────────────────────

def render_frames(
    period_str: str,
    output_dir: Optional[Path] = None,
    cfg:        Optional[RenderConfig] = None,
) -> None:
    """
    기간 전체를 PNG 시퀀스로 렌더링.

    Args:
        period_str: 기간 ("2025-12" 또는 "2025-12-20~2025-12-31")
        output_dir: 출력 폴더 (기본: output/<period>/frames/)
        cfg:        RenderConfig 인스턴스 (None이면 기본값)
    """
    if cfg is None:
        cfg = RenderConfig()
        cfg.resolve_token()

    start, end = parse_period(period_str)
    log.info("기간: %s ~ %s", start, end)
    log.info("지도 공급자: %s", cfg.provider)

    db = Path(cfg.db_path)
    if not db.exists():
        log.error("DB 없음: %s  →  먼저 파서를 실행하세요", db)
        log.error("  python script/timeline_parser.py <파일경로>")
        sys.exit(1)

    conn  = sqlite3.connect(db)
    track = fetch_track(conn, start, end)
    conn.close()

    bbox        = compute_bbox(track, cfg)
    period_slug = period_str.replace("~", "_")
    out_dir     = output_dir or Path(cfg.output_root) / period_slug / "frames"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 공급자 이름 포함 → 공급자 변경 시 캐시 무효화
    cache_tile = Path(cfg.output_root) / period_slug / f"tile_{cfg.provider}.png"
    base_img   = fetch_base_tile(bbox, cfg, cache_tile)

    total_frames = cfg.duration_sec * cfg.fps
    n_points     = len(track)
    indices      = [
        int(i * (n_points - 1) / max(total_frames - 1, 1))
        for i in range(total_frames)
    ]

    log.info(
        "렌더링 시작: %d프레임 | %d초@%dfps | GPS %d건 | trail %d",
        total_frames, cfg.duration_sec, cfg.fps, n_points, cfg.trail_len,
    )

    for frame_idx, pt_idx in enumerate(indices):
        img = render_frame(base_img, track, pt_idx, bbox, cfg)
        img = draw_hud(img, track[pt_idx]["timestamp"], frame_idx, total_frames, cfg)
        img.save(out_dir / f"frame_{frame_idx:06d}.png")

        if frame_idx % 150 == 0 or frame_idx == total_frames - 1:
            log.info(
                "  프레임 %d / %d  (%.1f%%)",
                frame_idx + 1, total_frames, (frame_idx + 1) / total_frames * 100,
            )

    log.info("렌더링 완료: %d프레임 → %s", total_frames, out_dir)
    log.info("[ 다음 단계 — Phase 3 ] python script/video_encoder.py %s", period_str)


# ──────────────────────────────────────────
# CLI
# ──────────────────────────────────────────

def main() -> None:
    """CLI 진입점"""
    ap = argparse.ArgumentParser(
        description="Phase 2 프레임 렌더러 — GPS 트랙 → PNG 시퀀스",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
지도 공급자 목록:
  무료 (API 키 없음) : osm, cartodb-voyager, cartodb-light, cartodb-dark
  무료 (회원가입)    : stadia-dark, stadia-light   (STADIA_API_KEY 필요)
  프리미엄           : mapbox                       (MAPBOX_TOKEN 필요)
  커스텀 XYZ URL     : https://example.com/{{z}}/{{x}}/{{y}}.png

예시:
  python script/frame_renderer.py 2025-12
  python script/frame_renderer.py 2025-12 --map cartodb-light
  python script/frame_renderer.py 2025-12-20~2025-12-31 --map osm --duration 30
  python script/frame_renderer.py 2025-08 --config my_config.yml --fps 24
        """,
    )
    ap.add_argument("period",
                    help="기간 (예: 2025-12  또는  2025-12-20~2025-12-31)")
    ap.add_argument("--map",
                    metavar="PROVIDER",
                    default=None,
                    help="지도 공급자 또는 커스텀 XYZ URL (render_config.yml 덮어씀)")
    ap.add_argument("--config",
                    type=Path,
                    default=None,
                    metavar="FILE",
                    help="설정 파일 경로 (기본: ./render_config.yml)")
    ap.add_argument("--output",   type=Path, default=None,
                    help="출력 폴더 (기본: output/<period>/frames/)")
    ap.add_argument("--duration", type=int,  default=None,
                    help="목표 영상 길이 초 (기본: render_config.yml 값 또는 60)")
    ap.add_argument("--fps",      type=int,  default=None,
                    help="프레임 레이트 (기본: render_config.yml 값 또는 30)")
    ap.add_argument("--trail",    type=int,  default=None,
                    help="잔상 최대 점 개수 (기본: render_config.yml 값 또는 300)")
    args = ap.parse_args()

    # 1. YAML 로드
    cfg = load_config(args.config)

    # 2. --map 플래그 처리 (URL이면 custom, 아니면 provider 이름)
    if args.map:
        if args.map.startswith("http"):
            cfg.provider   = "custom"
            cfg.custom_url = args.map
        else:
            cfg.provider = args.map

    # 3. 나머지 CLI 플래그 (None이면 YAML 값 유지)
    if args.duration: cfg.duration_sec = args.duration
    if args.fps:      cfg.fps          = args.fps
    if args.trail:    cfg.trail_len    = args.trail

    render_frames(
        period_str = args.period,
        output_dir = args.output,
        cfg        = cfg,
    )


if __name__ == "__main__":
    main()
