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
import json
import math
import sqlite3
import argparse
import logging
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Optional, Iterable

# render_config는 같은 script/ 디렉터리에 있으므로 경로 추가
sys.path.insert(0, str(Path(__file__).parent))
from render_config import (
    RenderConfig, TILE_PROVIDERS, PROVIDER_NAMES, load_config, parse_duration_str,
)

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

# ──────────────────────────────────────────
# 이상치 필터 (정지 포인트 GPS 노이즈 제거)
# ──────────────────────────────────────────

def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Haversine 거리(m)"""
    R = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp/2)**2 + math.cos(p1) * math.cos(p2) * math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _linear_fit(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """최소제곱 선형회귀 y = a*x + b → (a, b)"""
    n = len(xs)
    if n < 2:
        return 0.0, ys[0] if ys else 0.0
    sx = sum(xs); sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-12:
        return 0.0, sy / n
    a = (n * sxy - sx * sy) / denom
    b = (sy - a * sx) / n
    return a, b


def filter_outliers(track: list[dict], cfg: RenderConfig) -> list[dict]:
    """
    정지(stationary)로 분류된 포인트 중 주변 N개의 선형회귀 추세에서
    크게 벗어난 것을 GPS 노이즈/이상치로 판단해 제거.

    알고리즘:
      1. 각 stationary 포인트 i에 대해 양쪽 이웃 N개 추출 (이미 outlier로 표시된 것은 제외)
      2. 이웃들의 timestamp(epoch) → lat / lng 선형회귀
      3. 회귀선으로 i 시점의 예측 (lat, lng) 산출
      4. 실제 위치와 Haversine 거리 > max_deviation_m 이면 제거

    Returns: 이상치를 제거한 새 트랙 리스트
    """
    if not cfg.outlier_filter_enabled or len(track) < 5:
        return track

    # timestamp → epoch (초 단위 float)
    times: list[float] = []
    for p in track:
        try:
            dt = datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00"))
            times.append(dt.timestamp())
        except Exception:
            times.append(0.0)

    keep = [True] * len(track)
    n_removed = 0
    W   = cfg.outlier_window_size
    THR = cfg.outlier_max_deviation_m

    for i, pt in enumerate(track):
        if (pt.get("activity") or "unknown") != "stationary":
            continue

        # 유효 이웃 인덱스 (자기 자신 제외 + 이미 제거된 것 제외)
        lo = max(0, i - W)
        hi = min(len(track), i + W + 1)
        n_idx = [j for j in range(lo, hi) if j != i and keep[j]]
        if len(n_idx) < 4:
            continue

        ts   = [times[j]          for j in n_idx]
        lats = [track[j]["lat"]   for j in n_idx]
        lngs = [track[j]["lng"]   for j in n_idx]

        # timestamp가 동일한 경우 회귀 의미 없음
        if max(ts) - min(ts) < 1.0:
            continue

        a_lat, b_lat = _linear_fit(ts, lats)
        a_lng, b_lng = _linear_fit(ts, lngs)
        pred_lat = a_lat * times[i] + b_lat
        pred_lng = a_lng * times[i] + b_lng

        if _haversine_m(pt["lat"], pt["lng"], pred_lat, pred_lng) > THR:
            keep[i] = False
            n_removed += 1

    if n_removed > 0:
        log.info(
            "이상치 필터: stationary 포인트 %d개 제거 (window=%d, max_dev=%.0fm)",
            n_removed, W, THR,
        )
    return [p for p, k in zip(track, keep) if k]


# ──────────────────────────────────────────
# 이동수단 세분화 (subway / train / car / running 등 재분류)
# ──────────────────────────────────────────

# 속도 임계값 적용 시 평가 순서 (오름차순) — config 에서 가져온 dict를 순서화
_REFINE_BANDS_ORDER = ("stationary", "walking", "running", "cycling", "car", "highway")


def _classify_by_speed(speed_ms: Optional[float], thresholds: dict) -> str:
    """속도 → 활동 라벨 (config의 refine_speed_thresholds 사용)"""
    if speed_ms is None:
        return "stationary"
    s = float(speed_ms)
    if s < thresholds.get("stationary", 0.5):
        return "stationary"
    if s < thresholds.get("walking", 2.0):
        return "walking"
    if s < thresholds.get("running", 4.5):
        return "running"
    if s < thresholds.get("cycling", 8.0):
        return "cycling"
    if s < thresholds.get("car", 25.0):
        return "car"
    if s < thresholds.get("highway", 90.0):
        return "highway"
    return "flight"


def refine_transport_modes(track: list[dict], cfg: RenderConfig) -> list[dict]:
    """
    이동수단 라벨을 세분화 — 파서의 vehicle/highway 분류를
    car / bus / subway / train / highway 등으로 재분류.

    감지 알고리즘:
      Pass 1 (시간 갭 → subway):
        이전 포인트와 시간 차이 > N초 + 거리 점프 > N미터
        → 지하철 터널에서 GPS 손실 패턴

      Pass 2 (지속 고속 → train):
        윈도우 W 내 평균속도 ≥ M m/s
        → 일관된 고속 = 기차/철도

      Pass 3 (속도 기반 기본 분류):
        cfg.refine_speed_thresholds 의 임계값으로 라벨 결정

    Returns: 새 활동 라벨이 적용된 트랙 리스트
    """
    if not cfg.refine_modes_enabled or len(track) < 2:
        return track

    # 시간(epoch) 사전 계산
    times: list[float] = []
    for p in track:
        try:
            dt = datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00"))
            times.append(dt.timestamp())
        except Exception:
            times.append(times[-1] if times else 0.0)

    n = len(track)
    SUBWAY_GAP  = cfg.refine_subway_gap_sec
    SUBWAY_DIST = cfg.refine_subway_jump_m
    TRAIN_MIN   = cfg.refine_train_min_speed
    TRAIN_WIN   = max(3, cfg.refine_train_window)

    # ── Pass 1: subway 감지 ────────────────────────
    is_subway = [False] * n
    for i in range(1, n):
        dt_sec = times[i] - times[i - 1]
        if dt_sec < SUBWAY_GAP:
            continue
        dist_m = _haversine_m(
            track[i - 1]["lat"], track[i - 1]["lng"],
            track[i]["lat"],     track[i]["lng"],
        )
        if dist_m > SUBWAY_DIST:
            is_subway[i] = True

    # ── Pass 2: train 감지 (지속 평균속도) ────────
    is_train = [False] * n
    half = TRAIN_WIN // 2
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        speeds: list[float] = []
        for j in range(lo, hi):
            if is_subway[j]:
                continue
            s = track[j].get("speed_ms")
            if s is not None and s > 0:
                speeds.append(float(s))
        if len(speeds) >= TRAIN_WIN - 1 and speeds:
            avg = sum(speeds) / len(speeds)
            if avg >= TRAIN_MIN:
                is_train[i] = True

    # ── Pass 3: 적용 ─────────────────────────────
    counts: dict[str, int] = {}
    new_track: list[dict] = []
    for i, pt in enumerate(track):
        new_pt = dict(pt)

        if is_subway[i]:
            new_act = "subway"
        else:
            base = _classify_by_speed(pt.get("speed_ms"), cfg.refine_speed_thresholds)
            # train 후처리 — 차량/고속 영역에서만 train 으로 승격
            if is_train[i] and base in ("car", "highway"):
                new_act = "train"
            else:
                new_act = base

        new_pt["activity"] = new_act
        counts[new_act] = counts.get(new_act, 0) + 1
        new_track.append(new_pt)

    # 결과 로그
    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items(), key=lambda x: -x[1]))
    log.info("이동수단 세분화: %s", summary)
    return new_track


# ──────────────────────────────────────────
# 시간 그리드 다운샘플링
# ──────────────────────────────────────────

def _epoch_seconds(p: dict) -> float:
    """포인트의 timestamp(ISO 8601) → epoch 초. 실패 시 0.0."""
    try:
        return datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def downsample_by_time(track: list[dict], step_sec: float) -> list[dict]:
    """
    시간 그리드 다운샘플링 — 정지 구간 압축.

    트랙 시작 시각으로부터 step_sec 간격의 그리드를 만들고, 각 그리드 셀 내부의
    포인트 중 셀 시작점에 가장 가까운 1개만 채택한다. 결과적으로 정지 구간의
    중복 포인트가 압축되며, 영상이 시간에 비례하도록 균질해진다.

    이상치 필터·이동수단 세분화 후에 호출해야 함 (속도/시간 갭 분석은 원본 필요).

    Args:
        track:    GPS 포인트 리스트 (timestamp 순 정렬)
        step_sec: 그리드 간격(초). 0 이하면 다운샘플링 생략.

    Returns:
        다운샘플링된 새 트랙 리스트.
    """
    if step_sec <= 0 or len(track) < 2:
        return track

    epochs = [_epoch_seconds(p) for p in track]
    t0     = epochs[0]

    # 각 포인트의 버킷 인덱스 → 셀 시작점에 가장 가까운 포인트 채택
    selected: dict[int, tuple[int, float]] = {}   # bucket → (track_idx, distance)
    for i, t in enumerate(epochs):
        bucket = int((t - t0) / step_sec)
        center = t0 + bucket * step_sec
        d      = abs(t - center)
        if bucket not in selected or d < selected[bucket][1]:
            selected[bucket] = (i, d)

    keep_indices = sorted(v[0] for v in selected.values())
    new_track    = [track[i] for i in keep_indices]

    log.info(
        "시간 다운샘플링: %d → %d 포인트 (step=%.0fs, %.1f%% 감소)",
        len(track), len(new_track), step_sec,
        (1 - len(new_track) / max(len(track), 1)) * 100,
    )
    return new_track


# ──────────────────────────────────────────
# Bearing (icon 회전 + 진행 방향)
# ──────────────────────────────────────────

def compute_bearing(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """좌표 1→2 진행 방향 (도, 0=북, 90=동)"""
    rl1 = math.radians(lat1); rl2 = math.radians(lat2)
    dlng = math.radians(lng2 - lng1)
    x = math.sin(dlng) * math.cos(rl2)
    y = math.cos(rl1) * math.sin(rl2) - math.sin(rl1) * math.cos(rl2) * math.cos(dlng)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def compute_bbox(
    track: list[dict], cfg: RenderConfig, padding_factor: float = 1.0
) -> tuple[float, float, float, float]:
    """
    GPS 트랙 bounding box 계산.

    Args:
        padding_factor: 기본 padding을 추가 배율 적용 (World Canvas는 1.5 권장)
    Returns:
        (min_lat, min_lng, max_lat, max_lng)
    """
    lats = [p["lat"] for p in track]
    lngs = [p["lng"] for p in track]
    lat_span = max(lats) - min(lats)
    lng_span = max(lngs) - min(lngs)
    pad_lat  = max(lat_span * cfg.bbox_padding * padding_factor, cfg.bbox_min_pad_deg)
    pad_lng  = max(lng_span * cfg.bbox_padding * padding_factor, cfg.bbox_min_pad_deg)
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
    w: int,
    h: int,
    clamp: bool = False,
) -> tuple[int, int]:
    """
    위경도 → Mercator 투영 픽셀 좌표.

    Args:
        bbox:  (min_lat, min_lng, max_lat, max_lng) — 픽셀 0,0이 (max_lat, min_lng)
        w, h:  대상 이미지 크기
        clamp: True 시 [0, w-1] × [0, h-1] 로 제한
    """
    min_lat, min_lng, max_lat, max_lng = bbox
    x0, x1 = _mercator_x(min_lng), _mercator_x(max_lng)
    y0, y1 = _mercator_y(max_lat), _mercator_y(min_lat)  # Y 반전 (위쪽이 작은 값)

    # round()로 서브픽셀 반올림 — int()의 절단보다 좌표 정확도 ±0.5px 향상
    px = round(((_mercator_x(lng) - x0) / (x1 - x0)) * w)
    py = round(((_mercator_y(lat) - y0) / (y1 - y0)) * h)

    if clamp:
        px = max(0, min(w - 1, px))
        py = max(0, min(h - 1, py))
    return int(px), int(py)


def _compute_zoom(
    min_lat: float, min_lng: float, max_lat: float, max_lng: float,
    w: int, h: int,
) -> int:
    """bbox + 이미지 해상도 → 최적 줌 레벨 (0~17)"""
    TILE_SIZE = 256
    lat_frac  = abs(_mercator_y(max_lat) - _mercator_y(min_lat))
    lng_frac  = abs(max_lng - min_lng) / 360
    lat_zoom  = math.floor(math.log2(h / TILE_SIZE / lat_frac)) if lat_frac > 1e-9 else 17
    lng_zoom  = math.floor(math.log2(w / TILE_SIZE / lng_frac)) if lng_frac > 1e-9 else 17
    return max(0, min(17, min(lat_zoom, lng_zoom)))


def compute_rendered_bbox(
    center_lat: float, center_lng: float,
    zoom: int, w: int, h: int,
) -> tuple[float, float, float, float]:
    """
    staticmap이 (center, zoom, w, h)로 실제 렌더링한 정확한 bbox 역산.

    Web Mercator (EPSG:3857) 기준 — 표준 XYZ 타일 좌표계와 일치.
    integer zoom으로 인해 의도한 bbox와 항상 차이가 발생하므로,
    이 함수의 결과를 모든 좌표 매핑의 기준점으로 사용해야 정확하다.

    Returns:
        (min_lat, min_lng, max_lat, max_lng) — 픽셀 정확한 실제 캔버스 bbox
    """
    TILE_SIZE = 256
    world_size = TILE_SIZE * (2 ** zoom)   # zoom Z의 전체 월드 픽셀 크기

    # 중심 좌표 → 월드 픽셀 좌표 (Web Mercator)
    cx = (center_lng + 180.0) / 360.0 * world_size
    sin_lat = math.sin(math.radians(center_lat))
    sin_lat = max(-0.99999, min(0.99999, sin_lat))   # 극지방 클램프
    cy = (0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * world_size

    # 이미지 4모서리의 월드 픽셀 좌표 (중심 기준 ±w/2, ±h/2)
    left   = cx - w / 2.0
    right  = cx + w / 2.0
    top    = cy - h / 2.0
    bottom = cy + h / 2.0

    # 역변환: 월드 픽셀 → lng
    min_lng = left  / world_size * 360.0 - 180.0
    max_lng = right / world_size * 360.0 - 180.0

    # 역변환: 월드 픽셀 → lat (역 Web Mercator)
    def y_to_lat(y_world: float) -> float:
        n = math.pi * (1.0 - 2.0 * y_world / world_size)
        return math.degrees(math.atan(math.sinh(n)))

    max_lat = y_to_lat(top)      # top = 작은 y = 높은 위도
    min_lat = y_to_lat(bottom)

    return (min_lat, min_lng, max_lat, max_lng)


# ──────────────────────────────────────────
# 배경 타일 (단일 진입점)
# ──────────────────────────────────────────

def _fetch_tile_stitched(
    bbox: tuple[float, float, float, float],
    url_template: str,
    w: int,
    h: int,
    provider_name: str,
    cache_path: Path,
) -> Image.Image:
    """staticmap으로 XYZ 타일 스티칭 (CartoDB/OSM/Stadia 모두 지원)"""
    from staticmap import StaticMap

    min_lat, min_lng, max_lat, max_lng = bbox
    center = ((min_lng + max_lng) / 2, (min_lat + max_lat) / 2)
    zoom   = _compute_zoom(min_lat, min_lng, max_lat, max_lng, w, h)
    log.info("타일 스티칭 (provider=%s, zoom=%d, %dx%d)...",
             provider_name, zoom, w, h)

    m   = StaticMap(w, h, url_template=url_template)
    img = m.render(zoom=zoom, center=center)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(cache_path)
    log.info("배경 타일 저장: %s", cache_path)
    return img.convert("RGB")


def _fetch_mapbox(
    bbox: tuple[float, float, float, float],
    cfg: RenderConfig,
    w: int,
    h: int,
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
    # Mapbox 최대 1280×1280 (@2x로 2560까지 가능)
    url = (
        f"https://api.mapbox.com/styles/v1/{style}/static/"
        f"{bbox_str}/{w}x{h}?access_token={token}&logo=false"
    )
    log.info("Mapbox Static API 요청 중 (style=%s, %dx%d)...", style, w, h)
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(resp.content)
    log.info("배경 타일 저장: %s", cache_path)
    return Image.open(cache_path).convert("RGB")


def _meta_path(cache_path: Path) -> Path:
    """캔버스 메타데이터 사이드카 경로 (.png → .json)"""
    return cache_path.with_suffix(".json")


def _save_canvas_meta(
    meta_path: Path,
    intended_bbox: tuple,
    actual_bbox:   tuple,
    center_lat:    float,
    center_lng:    float,
    zoom:          int,
    canvas_w:      int,
    canvas_h:      int,
    provider:      str,
) -> None:
    """캔버스 메타데이터 JSON 저장 — 실제 렌더링 bbox 영구 보존"""
    meta = {
        "intended_bbox": list(intended_bbox),
        "actual_bbox":   list(actual_bbox),
        "center_lat":    center_lat,
        "center_lng":    center_lng,
        "zoom":          zoom,
        "canvas_w":      canvas_w,
        "canvas_h":      canvas_h,
        "provider":      provider,
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def fetch_world_canvas(
    bbox: tuple[float, float, float, float],
    cfg: RenderConfig,
    cache_path: Path,
) -> tuple[Image.Image, tuple[float, float, float, float]]:
    """
    동적 카메라용 대형 World Canvas 다운로드 + 정확한 actual_bbox 반환.

    핵심 알고리즘:
      1. 의도한 bbox로부터 integer zoom 산출
      2. staticmap이 (center, zoom, w, h)로 렌더링 → 실제 bbox는 의도와 다름
      3. compute_rendered_bbox()로 실제 bbox 역산 → 좌표 매핑 기준점 확정
      4. .json 사이드카로 영구 캐싱 (재실행 시 재계산 불필요)

    Returns:
        (canvas_image, actual_bbox)
    """
    canvas_w = cfg.map_w * cfg.canvas_scale
    canvas_h = cfg.map_h * cfg.canvas_scale
    meta_path = _meta_path(cache_path)

    # 1차: 캐시 + 메타데이터 둘 다 있으면 즉시 반환
    if cache_path.exists() and meta_path.exists():
        cached = Image.open(cache_path).convert("RGB")
        if cached.size == (canvas_w, canvas_h):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                actual_bbox = tuple(meta["actual_bbox"])
                log.info("World Canvas 캐시 사용: %s (%dx%d, zoom=%d)",
                         cache_path, canvas_w, canvas_h, meta.get("zoom", -1))
                log.info("  실제 bbox: lat[%.6f~%.6f]  lng[%.6f~%.6f]",
                         actual_bbox[0], actual_bbox[2], actual_bbox[1], actual_bbox[3])
                return cached, actual_bbox
            except (KeyError, ValueError, json.JSONDecodeError):
                log.warning("메타데이터 손상 → 재다운로드")

    # 2차: 다운로드
    log.info("World Canvas 다운로드 중 (%dx%d, scale=%d)...",
             canvas_w, canvas_h, cfg.canvas_scale)

    center_lat = (bbox[0] + bbox[2]) / 2
    center_lng = (bbox[1] + bbox[3]) / 2
    zoom       = _compute_zoom(*bbox, canvas_w, canvas_h)

    try:
        if cfg.provider == "mapbox":
            log.warning("Mapbox는 대형 캔버스 미지원 → scale=1 사용")
            img = _fetch_mapbox(bbox, cfg, cfg.map_w, cfg.map_h, cache_path)
            actual_w, actual_h = cfg.map_w, cfg.map_h
        else:
            url = cfg.tile_url()
            img = _fetch_tile_stitched(
                bbox, url, canvas_w, canvas_h, cfg.provider, cache_path
            )
            actual_w, actual_h = canvas_w, canvas_h
    except Exception as e:
        log.warning("World Canvas 로드 실패 (%s) → 단색 배경", e)
        img = Image.new("RGB", (canvas_w, canvas_h), (30, 30, 30))
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(cache_path)
        actual_w, actual_h = canvas_w, canvas_h

    # 3차: 실제 렌더링된 bbox 역산
    actual_bbox = compute_rendered_bbox(center_lat, center_lng, zoom, actual_w, actual_h)
    _save_canvas_meta(
        meta_path, bbox, actual_bbox,
        center_lat, center_lng, zoom, actual_w, actual_h, cfg.provider,
    )

    # 의도 vs 실제 차이 로그
    d_lat = (actual_bbox[2] - actual_bbox[0]) - (bbox[2] - bbox[0])
    d_lng = (actual_bbox[3] - actual_bbox[1]) - (bbox[3] - bbox[1])
    log.info("  의도한 bbox: lat[%.6f~%.6f]  lng[%.6f~%.6f]",
             bbox[0], bbox[2], bbox[1], bbox[3])
    log.info("  실제 bbox  : lat[%.6f~%.6f]  lng[%.6f~%.6f]  (diff: lat+%.4f° lng+%.4f°)",
             actual_bbox[0], actual_bbox[2], actual_bbox[1], actual_bbox[3], d_lat, d_lng)
    log.info("  zoom=%d, %dx%d", zoom, actual_w, actual_h)

    return img, actual_bbox


# ──────────────────────────────────────────
# 프레임 렌더링
# ──────────────────────────────────────────

def compute_frame_viewport(
    visible: list[dict],
    cfg: RenderConfig,
) -> tuple[float, float, float, float]:
    """
    프레임별 동적 뷰포트 bbox 계산.

    트레일에 보이는 모든 포인트가 들어가도록 bbox 산출 → 패딩 → 최소 크기 보정 →
    출력 종횡비 맞춤.

    타일 피라미드 방식에서는 캔버스 클램프 불필요 (전 세계 어디든 다운로드 가능).

    Returns:
        (min_lat, min_lng, max_lat, max_lng)
    """
    lats = [p["lat"] for p in visible]
    lngs = [p["lng"] for p in visible]

    mn_lat, mx_lat = min(lats), max(lats)
    mn_lng, mx_lng = min(lngs), max(lngs)

    # 패딩 적용
    lat_span = mx_lat - mn_lat
    lng_span = mx_lng - mn_lng
    pad_lat  = max(lat_span * cfg.bbox_padding, cfg.bbox_min_pad_deg)
    pad_lng  = max(lng_span * cfg.bbox_padding, cfg.bbox_min_pad_deg)
    mn_lat -= pad_lat;  mx_lat += pad_lat
    mn_lng -= pad_lng;  mx_lng += pad_lng

    # 최소 뷰포트 보정 (정지 시 과도한 줌인 방지)
    cur_lat_span = mx_lat - mn_lat
    cur_lng_span = mx_lng - mn_lng
    if cur_lat_span < cfg.min_viewport_deg:
        c = (mx_lat + mn_lat) / 2
        mn_lat = c - cfg.min_viewport_deg / 2
        mx_lat = c + cfg.min_viewport_deg / 2
    if cur_lng_span < cfg.min_viewport_deg:
        c = (mx_lng + mn_lng) / 2
        mn_lng = c - cfg.min_viewport_deg / 2
        mx_lng = c + cfg.min_viewport_deg / 2

    # 출력 종횡비(map_w/map_h)에 맞춰 한쪽이 너무 좁아지면 확장
    aspect = cfg.map_w / cfg.map_h
    cur_lng_span = mx_lng - mn_lng
    cur_lat_span = mx_lat - mn_lat
    if cur_lng_span / max(cur_lat_span, 1e-9) < aspect:
        target = cur_lat_span * aspect
        c = (mx_lng + mn_lng) / 2
        mn_lng = c - target / 2
        mx_lng = c + target / 2
    elif cur_lat_span / max(cur_lng_span, 1e-9) < 1 / aspect:
        target = cur_lng_span / aspect
        c = (mx_lat + mn_lat) / 2
        mn_lat = c - target / 2
        mx_lat = c + target / 2

    return (mn_lat, mn_lng, mx_lat, mx_lng)


# crop_to_viewport는 제거됨 — 타일 피라미드 방식(tile_cache.composite_for_bbox)으로 대체


def _draw_lines(
    draw: ImageDraw.ImageDraw,
    visible: list[dict],
    frame_bbox: tuple[float, float, float, float],
    cfg: RenderConfig,
    head: Optional[dict] = None,    # {"lat","lng","activity"} — 부분 segment 끝점 (애니메이션)
) -> None:
    """
    이동 경로 선 — 연속 포인트 연결 + 마지막 부분 segment(애니메이션).

    head가 주어지면 visible[-1] → head 까지 부분 segment 추가 그림.
    이게 fractional 인덱스 기반 부드러운 그리기 애니메이션의 핵심.
    """
    n = len(visible)
    a_range = cfg.trail_alpha_max - cfg.trail_alpha_min

    # 기존 visible 점들의 segment
    for i in range(1, n):
        prev = visible[i - 1]
        curr = visible[i]
        pa = prev.get("activity") or "unknown"
        ca = curr.get("activity") or "unknown"
        if pa == "stationary" and ca == "stationary":
            continue
        line_w = cfg.line_widths.get(ca, cfg.line_widths.get("unknown", 1))
        if line_w <= 0:
            continue
        color = cfg.activity_colors.get(ca, (200, 200, 200))
        alpha = int(cfg.trail_alpha_min + a_range * (i / max(n - 1, 1)))
        x1, y1 = latlng_to_pixel(prev["lat"], prev["lng"], frame_bbox, cfg.map_w, cfg.map_h)
        x2, y2 = latlng_to_pixel(curr["lat"], curr["lng"], frame_bbox, cfg.map_w, cfg.map_h)
        draw.line([(x1, y1), (x2, y2)], fill=(*color, alpha), width=line_w)

    # head — visible[-1] → head 위치까지 부분 segment (애니메이션 핵심)
    if head is not None and n > 0:
        last = visible[-1]
        ha = head.get("activity") or "unknown"
        line_w = cfg.line_widths.get(ha, cfg.line_widths.get("unknown", 1))
        # 정지 ↔ 정지가 아니면서 line_w > 0 인 경우만
        if line_w > 0 and not (
            (last.get("activity") or "unknown") == "stationary" and ha == "stationary"
        ):
            color = cfg.activity_colors.get(ha, (200, 200, 200))
            x1, y1 = latlng_to_pixel(last["lat"], last["lng"], frame_bbox, cfg.map_w, cfg.map_h)
            x2, y2 = latlng_to_pixel(head["lat"], head["lng"], frame_bbox, cfg.map_w, cfg.map_h)
            if (x1, y1) != (x2, y2):
                draw.line(
                    [(x1, y1), (x2, y2)],
                    fill=(*color, cfg.trail_alpha_max),
                    width=line_w,
                )


def _draw_dots(
    draw: ImageDraw.ImageDraw,
    visible: list[dict],
    frame_bbox: tuple[float, float, float, float],
    cfg: RenderConfig,
    skip_outline: bool = False,    # 아이콘이 head를 차지하면 외곽선 생략
) -> None:
    """잔상 점 + (옵션) 현재 위치 외곽선"""
    n = len(visible)
    a_range = cfg.trail_alpha_max - cfg.trail_alpha_min

    for i, pt in enumerate(visible):
        alpha  = int(cfg.trail_alpha_min + a_range * (i / max(n - 1, 1)))
        act    = pt.get("activity") or "unknown"
        color  = cfg.activity_colors.get(act, (200, 200, 200))
        radius = cfg.activity_radius.get(act, 2)
        px, py = latlng_to_pixel(pt["lat"], pt["lng"], frame_bbox, cfg.map_w, cfg.map_h)
        r = radius if i < n - 1 else radius * cfg.current_pt_scale + 1
        draw.ellipse([px - r, py - r, px + r, py + r], fill=(*color, alpha))

    if not skip_outline and visible:
        curr   = visible[-1]
        px, py = latlng_to_pixel(curr["lat"], curr["lng"], frame_bbox, cfg.map_w, cfg.map_h)
        act    = curr.get("activity") or "unknown"
        base_r = cfg.activity_radius.get(act, 2)
        r_out  = base_r * cfg.current_pt_scale + cfg.outline_extra_r
        draw.ellipse(
            [px - r_out, py - r_out, px + r_out, py + r_out],
            outline=(*cfg.outline_color, cfg.outline_alpha),
            width=2,
        )


# ──────────────────────────────────────────
# 아이콘 로딩 / 합성
# ──────────────────────────────────────────

def load_icons(cfg: RenderConfig) -> dict[str, Image.Image]:
    """
    icon_dir 내 모든 PNG를 RGBA로 로드 + icon_size에 맞춰 종횡비 유지 리사이즈.
    파일명(stem)을 key로 한 dict 반환.
    """
    icon_dir = Path(cfg.icon_dir)
    if not icon_dir.exists():
        log.warning("아이콘 폴더 없음: %s — 아이콘 비활성화", icon_dir)
        return {}

    out: dict[str, Image.Image] = {}
    for f in sorted(icon_dir.glob("*.png")):
        try:
            img = Image.open(f).convert("RGBA")
            ratio = cfg.icon_size / max(img.width, img.height)
            new_size = (max(1, int(img.width * ratio)), max(1, int(img.height * ratio)))
            out[f.stem] = img.resize(new_size, Image.LANCZOS)
        except Exception as e:
            log.warning("아이콘 로드 실패 %s: %s", f, e)

    if out:
        log.info("아이콘 로드: %d개 (%s)", len(out), ", ".join(sorted(out.keys())))
    return out


def _draw_icon_at(
    img:        Image.Image,        # RGBA
    lat:        float,
    lng:        float,
    bearing:    float,
    activity:   str,
    frame_bbox: tuple[float, float, float, float],
    icons:      dict[str, Image.Image],
    cfg:        RenderConfig,
) -> None:
    """head 위치에 활동별 아이콘 합성. 이동 활동은 옵션으로 bearing 회전."""
    icon_name = cfg.activity_icons.get(activity)
    if not icon_name:
        return
    icon = icons.get(icon_name)
    if icon is None:
        return

    # 이동 활동만 회전 (회전 켰을 때)
    if cfg.icon_rotate and activity in ("vehicle", "highway", "cycling", "flight"):
        # PIL.rotate: 양수=반시계. compass bearing은 시계+북=0 → 양각으로 변환
        # 아이콘이 위쪽(북쪽)을 향한다고 가정하면 -bearing
        icon = icon.rotate(-bearing, resample=Image.BICUBIC, expand=True)

    # 투명도 조정
    if cfg.icon_alpha < 255:
        a = icon.split()[3]
        a = a.point(lambda p: int(p * cfg.icon_alpha / 255))
        icon = icon.copy()
        icon.putalpha(a)

    px, py = latlng_to_pixel(lat, lng, frame_bbox, cfg.map_w, cfg.map_h)
    iw, ih = icon.size
    img.alpha_composite(icon, (px - iw // 2, py - ih // 2))


def render_frame(
    tile_cache    : "TileCache",
    track         : list[dict],
    fractional_idx: float,                   # 정수가 아닌 부동소수점 인덱스 — 부드러운 애니메이션
    cfg           : RenderConfig,
    icons         : Optional[dict[str, Image.Image]] = None,
) -> tuple[Image.Image, tuple[float, float, float, float], int]:
    """
    단일 프레임 렌더링 (타일 피라미드 + 적응형 줌 + 보간 head + 아이콘).

    fractional_idx의 정수부 = 마지막 완전 표시 GPS 포인트
    소수부 = 다음 포인트로 향하는 진행률 (0~1)

    1. visible = track[start : floor(f)+1]  (완전 통과한 점들)
    2. head    = visible[-1] → track[floor(f)+1] 사이 보간 위치 (애니메이션 끝점)
    3. frame_bbox = visible + head 모두 포함되도록 동적 계산
    4. 타일 합성 + 선/점 + 아이콘 (head에)

    Returns:
        (rendered_image, frame_bbox, zoom_used)
    """
    from tile_cache import composite_for_bbox, ideal_zoom_for_bbox

    pt_idx       = int(fractional_idx)
    seg_progress = fractional_idx - pt_idx       # 0.0 ~ 1.0

    start   = max(0, pt_idx - cfg.trail_len)
    visible = track[start : pt_idx + 1]

    # head 위치 보간 (애니메이션 핵심)
    head_lat: float
    head_lng: float
    head_act: str
    head_bearing: float = 0.0
    head_dict: Optional[dict] = None

    if visible:
        head_lat = visible[-1]["lat"]
        head_lng = visible[-1]["lng"]
        head_act = visible[-1].get("activity") or "unknown"

    if seg_progress > 1e-6 and pt_idx + 1 < len(track) and visible:
        nxt = track[pt_idx + 1]
        last = visible[-1]
        head_lat = last["lat"] + seg_progress * (nxt["lat"] - last["lat"])
        head_lng = last["lng"] + seg_progress * (nxt["lng"] - last["lng"])
        # head 활동 = 진행 방향(다음) 활동
        head_act = nxt.get("activity") or head_act
        head_bearing = compute_bearing(last["lat"], last["lng"], nxt["lat"], nxt["lng"])
        head_dict = {"lat": head_lat, "lng": head_lng, "activity": head_act}

    # frame_bbox: visible + head 모두 포함하도록 (잘림 방지)
    bbox_pts = list(visible)
    if head_dict is not None:
        bbox_pts = bbox_pts + [head_dict]
    if not bbox_pts:
        bbox_pts = [{"lat": 0.0, "lng": 0.0, "activity": "unknown"}]

    frame_bbox = compute_frame_viewport(bbox_pts, cfg)
    zoom = ideal_zoom_for_bbox(
        frame_bbox, cfg.map_w, cfg.map_h, cfg.max_zoom, cfg.zoom_offset,
    )

    bg = composite_for_bbox(tile_cache, frame_bbox, cfg.map_w, cfg.map_h, zoom)

    img  = bg.convert("RGBA")
    draw = ImageDraw.Draw(img, "RGBA")

    # 1. 선 (visible 본체 + head로 향하는 부분 segment)
    if cfg.draw_lines:
        _draw_lines(draw, visible, frame_bbox, cfg, head=head_dict)

    # 2. 점 (head에 아이콘이 들어갈 예정이면 외곽선 생략)
    use_icon = bool(icons) and head_act in cfg.activity_icons
    _draw_dots(draw, visible, frame_bbox, cfg, skip_outline=use_icon)

    # 3. 아이콘 (head 위치에)
    if use_icon and visible:
        _draw_icon_at(img, head_lat, head_lng, head_bearing, head_act,
                      frame_bbox, icons, cfg)
    elif visible:
        # 아이콘 없으면 기존 외곽선으로 head 표시
        last = visible[-1]
        px, py = latlng_to_pixel(head_lat, head_lng, frame_bbox, cfg.map_w, cfg.map_h)
        base_r = cfg.activity_radius.get(head_act, 2)
        r_out  = base_r * cfg.current_pt_scale + cfg.outline_extra_r
        draw.ellipse(
            [px - r_out, py - r_out, px + r_out, py + r_out],
            outline=(*cfg.outline_color, cfg.outline_alpha),
            width=2,
        )

    return img.convert("RGB"), frame_bbox, zoom


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
# 기준점 검증 (--verify)
# ──────────────────────────────────────────

def render_verification_overlay(
    canvas:      Image.Image,
    canvas_bbox: tuple[float, float, float, float],
    output_path: Path,
) -> None:
    """
    캔버스 좌표 정확도 검증 — 9개 기준점에 십자가 + 라벨 표시.

    actual_bbox 기반 latlng_to_pixel()로 4 모서리 + 4 변 중점 + 중심 마킹.
    저장된 이미지를 열어 십자가가 지도의 정확한 위치(예: 모서리 라벨이
    실제 모서리)에 있는지 시각적으로 확인 가능.
    """
    img = canvas.copy()
    draw = ImageDraw.Draw(img, "RGBA")
    cw, ch = canvas.size

    min_lat, min_lng, max_lat, max_lng = canvas_bbox
    mid_lat = (min_lat + max_lat) / 2
    mid_lng = (min_lng + max_lng) / 2

    points = [
        ("TL",     max_lat, min_lng),  # 좌상
        ("T-mid",  max_lat, mid_lng),
        ("TR",     max_lat, max_lng),  # 우상
        ("L-mid",  mid_lat, min_lng),
        ("CENTER", mid_lat, mid_lng),  # 정중앙
        ("R-mid",  mid_lat, max_lng),
        ("BL",     min_lat, min_lng),  # 좌하
        ("B-mid",  min_lat, mid_lng),
        ("BR",     min_lat, max_lng),  # 우하
    ]

    cross_size = max(20, cw // 100)
    line_w     = max(3, cw // 800)

    try:
        from PIL import ImageFont
        font = ImageFont.truetype("arial.ttf", max(14, cw // 120))
    except Exception:
        font = None

    for label, lat, lng in points:
        px, py = latlng_to_pixel(lat, lng, canvas_bbox, cw, ch)

        # 빨강 십자가
        draw.line([(px - cross_size, py), (px + cross_size, py)],
                  fill=(255, 0, 0, 255), width=line_w)
        draw.line([(px, py - cross_size), (px, py + cross_size)],
                  fill=(255, 0, 0, 255), width=line_w)

        # 라벨 + 좌표 (반투명 검정 배경)
        text = f"{label}\n{lat:.5f}\n{lng:.5f}"
        text_bg = (0, 0, 0, 180)
        text_pos = (px + cross_size + 5, py + cross_size + 5)
        # 텍스트 배경 박스 (대략적)
        draw.rectangle(
            [text_pos, (text_pos[0] + cw // 8, text_pos[1] + cw // 32)],
            fill=text_bg,
        )
        draw.text(text_pos, text, fill=(255, 255, 255, 255), font=font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
    log.info("기준점 검증 이미지 저장: %s", output_path)
    log.info("  → 9개 빨강 십자가 위치가 라벨 좌표와 일치하는지 시각 확인")


# ──────────────────────────────────────────
# 메인 렌더 루프
# ──────────────────────────────────────────

def render_frames(
    period_str: str,
    output_dir: Optional[Path] = None,
    cfg:        Optional[RenderConfig] = None,
    verify:     bool = False,
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

    # 이상치 필터 — stationary 포인트의 GPS 노이즈 제거
    track = filter_outliers(track, cfg)

    # 이동수단 세분화 — vehicle/highway → car/bus/subway/train 재분류
    track = refine_transport_modes(track, cfg)

    # ── realtime_speed 적용 — 데이터 시간 범위 기준 영상 길이 재계산 ─
    # (다운샘플링 *전* 시간 범위 사용: 사용자 의도는 원본 데이터 범위)
    if cfg.realtime_speed_sec > 0:
        t_first = _epoch_seconds(track[0])
        t_last  = _epoch_seconds(track[-1])
        span    = max(t_last - t_first, 1.0)
        new_dur = span / cfg.realtime_speed_sec
        log.info(
            "realtime_speed=%.0fs/s → 데이터 시간 %.0fs → 영상 길이 %ds → %.1fs",
            cfg.realtime_speed_sec, span, cfg.duration_sec, new_dur,
        )
        cfg.duration_sec = max(int(round(new_dur)), 1)

    # ── speed_factor 적용 — 영상 길이 배율 조정 ─────────────────────
    if cfg.speed_factor and cfg.speed_factor != 1.0:
        if cfg.speed_factor <= 0:
            log.error("speed_factor는 양수여야 합니다: %s", cfg.speed_factor)
            sys.exit(1)
        new_dur = cfg.duration_sec / cfg.speed_factor
        log.info(
            "speed=%.2fx → 영상 길이 %ds → %.1fs",
            cfg.speed_factor, cfg.duration_sec, new_dur,
        )
        cfg.duration_sec = max(int(round(new_dur)), 1)

    # ── 시간 그리드 다운샘플링 — 정지 구간 압축 ─────────────────────
    if cfg.time_step_sec > 0:
        track = downsample_by_time(track, cfg.time_step_sec)

    period_slug = period_str.replace("~", "_")
    out_dir     = output_dir or Path(cfg.output_root) / period_slug / "frames"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Google Drive 동기화 락 대기 — 쓰기 가능해질 때까지 (최대 60초)
    import time
    test_file = out_dir / ".write_test"
    for attempt in range(30):
        try:
            test_file.write_bytes(b"ok")
            test_file.unlink()
            break
        except (PermissionError, OSError) as e:
            if attempt == 29:
                log.error("출력 폴더 쓰기 불가: %s (%s)", out_dir, e)
                log.error("Google Drive 동기화 완료 대기 후 재시도하세요.")
                sys.exit(1)
            if attempt == 0:
                log.info("출력 폴더 쓰기 락 — 동기화 대기 중...")
            time.sleep(2)

    # 타일 피라미드 기반 적응형 배경 — 단일 World Canvas 대신 사용
    from tile_cache import (
        TileCache, build_tile_url, ideal_zoom_for_bbox, tiles_for_bbox,
        composite_for_bbox,
    )

    tile_url = build_tile_url(
        provider       = cfg.provider,
        base_url       = cfg.tile_url(),    # custom 또는 TILE_PROVIDERS 값
        custom_url     = cfg.custom_url,
        mapbox_token   = cfg.mapbox_token,
        stadia_api_key = cfg.stadia_api_key,
    )
    tile_cache = TileCache(
        url_template = tile_url,
        cache_dir    = Path(cfg.tile_cache_dir),
        provider     = cfg.provider,
    )

    # 프레임 인덱스 — fractional(float)로 부드러운 애니메이션 지원
    total_frames = cfg.duration_sec * cfg.fps
    n_points     = len(track)
    fractional_indices: list[float] = [
        i * (n_points - 1) / max(total_frames - 1, 1)
        for i in range(total_frames)
    ]

    # 아이콘 로드 (한 번)
    icons = load_icons(cfg)

    # ── --verify: 대표 영역에 대해 검증 이미지 생성 후 종료 ────────────
    if verify:
        all_bbox = compute_bbox(track, cfg, padding_factor=1.2)
        verify_zoom = ideal_zoom_for_bbox(
            all_bbox, cfg.map_w * 2, cfg.map_h * 2, cfg.max_zoom, cfg.zoom_offset,
        )
        log.info("검증 모드: 전체 bbox + zoom=%d", verify_zoom)
        # 검증은 사전 수집 없이 즉석 다운로드
        bg = composite_for_bbox(tile_cache, all_bbox, cfg.map_w * 2, cfg.map_h * 2, verify_zoom)
        verify_path = Path(cfg.output_root) / period_slug / f"verify_{cfg.provider}.png"
        render_verification_overlay(bg, all_bbox, verify_path)
        log.info("검증 모드 — 프레임 렌더링 생략. 이미지 확인 후 정확도 판정.")
        return

    # ── 1단계: 모든 프레임의 viewport·zoom 사전 분석 + 타일 수집 ─────
    log.info("타일 사전 분석 중 (%d 프레임)...", total_frames)
    needed_tiles: set[tuple[int, int, int]] = set()
    zoom_histogram: dict[int, int] = {}

    for fp in fractional_indices:
        pt_idx       = int(fp)
        seg_progress = fp - pt_idx
        start    = max(0, pt_idx - cfg.trail_len)
        visible  = track[start : pt_idx + 1]
        if not visible:
            continue

        # head 위치 보간 — viewport에 포함시켜 잘림 방지
        bbox_pts = list(visible)
        if seg_progress > 1e-6 and pt_idx + 1 < len(track):
            nxt = track[pt_idx + 1]
            last = visible[-1]
            bbox_pts.append({
                "lat": last["lat"] + seg_progress * (nxt["lat"] - last["lat"]),
                "lng": last["lng"] + seg_progress * (nxt["lng"] - last["lng"]),
                "activity": nxt.get("activity") or "unknown",
            })

        fb   = compute_frame_viewport(bbox_pts, cfg)
        zoom = ideal_zoom_for_bbox(fb, cfg.map_w, cfg.map_h, cfg.max_zoom, cfg.zoom_offset)
        zoom_histogram[zoom] = zoom_histogram.get(zoom, 0) + 1
        for t in tiles_for_bbox(fb, zoom):
            needed_tiles.add(t)

    log.info("필요 타일: %d개  | 줌 분포: %s",
             len(needed_tiles),
             ", ".join(f"z{z}={c}" for z, c in sorted(zoom_histogram.items())))

    # ── 2단계: 병렬 다운로드 (캐시 미적중분만) ──────────────────────
    tile_cache.prefetch(needed_tiles, parallel=cfg.prefetch_parallel)

    # ── 3단계: 프레임 렌더링 ──────────────────────────────────────
    log.info(
        "렌더링 시작: %d프레임 | %d초@%dfps | GPS %d건 | trail %d | lines=%s | icons=%d",
        total_frames, cfg.duration_sec, cfg.fps,
        n_points, cfg.trail_len, cfg.draw_lines, len(icons),
    )

    for frame_idx, fp in enumerate(fractional_indices):
        img, _frame_bbox, _z = render_frame(tile_cache, track, fp, cfg, icons=icons)
        # HUD 타임스탬프는 정수부 인덱스 기준
        ts = track[int(fp)]["timestamp"]
        img = draw_hud(img, ts, frame_idx, total_frames, cfg)
        img.save(out_dir / f"frame_{frame_idx:06d}.png")

        if frame_idx % 150 == 0 or frame_idx == total_frames - 1:
            log.info(
                "  프레임 %d / %d  (%.1f%%)",
                frame_idx + 1, total_frames, (frame_idx + 1) / total_frames * 100,
            )

    log.info("렌더링 완료: %d프레임 → %s", total_frames, out_dir)
    log.info("[ 다음 단계 — Phase 3 ] python main.py encode %s", period_str)


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
    ap.add_argument("--speed",    type=float, default=None, metavar="N",
                    help="영상 속도 배율 (예: 2.0=2배 빠름, 0.5=2배 느림). --realtime-speed 와 동시 사용 불가")
    ap.add_argument("--realtime-speed", dest="realtime_speed", default=None, metavar="DUR",
                    help="영상 1초당 진행할 실제 시간 (예: '1h', '5m', '60s'). --speed 와 동시 사용 불가")
    ap.add_argument("--time-step", dest="time_step", default=None, metavar="DUR",
                    help="GPS 포인트 시간 간격 다운샘플링 (예: '60s', '5m'). 정지 구간 압축")
    ap.add_argument("--verify",   action="store_true",
                    help="기준점 검증 모드 — World Canvas + 9개 십자가 마커만 생성 후 종료")
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

    # 4. 재생 속도 / 다운샘플링
    if args.speed is not None and args.realtime_speed is not None:
        log.error("--speed 와 --realtime-speed 는 동시에 사용할 수 없습니다.")
        sys.exit(1)
    if args.speed is not None:
        cfg.speed_factor = args.speed
    if args.realtime_speed is not None:
        cfg.realtime_speed_sec = parse_duration_str(args.realtime_speed)
    if args.time_step is not None:
        cfg.time_step_sec = parse_duration_str(args.time_step)

    render_frames(
        period_str = args.period,
        output_dir = args.output,
        cfg        = cfg,
        verify     = args.verify,
    )


if __name__ == "__main__":
    main()
