"""
Google Timeline Visualizer — 렌더링 설정 모듈
파일: render_config.py

render_config.yml 로드 → RenderConfig 데이터클래스로 반환.
파일 없으면 내장 기본값 사용.
CLI 플래그가 항상 최우선.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# ──────────────────────────────────────────
# 타일 공급자 레지스트리
# ──────────────────────────────────────────

TILE_PROVIDERS: dict[str, Optional[str]] = {
    # 무료 · API 키 불필요
    "osm":             "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "cartodb-voyager": "https://cartodb-basemaps-a.global.ssl.fastly.net/rastertiles/voyager/{z}/{x}/{y}.png",
    "cartodb-light":   "https://cartodb-basemaps-a.global.ssl.fastly.net/light_all/{z}/{x}/{y}.png",
    "cartodb-dark":    "https://cartodb-basemaps-a.global.ssl.fastly.net/dark_all/{z}/{x}/{y}.png",
    # 무료 · 회원가입 필요 (STADIA_API_KEY)
    "stadia-dark":     "https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/{z}/{x}/{y}.png",
    "stadia-light":    "https://tiles.stadiamaps.com/tiles/alidade_smooth/{z}/{x}/{y}.png",
    # 프리미엄 · MAPBOX_TOKEN 필요 (Mapbox Static API 방식으로 별도 처리)
    "mapbox":          None,
    # 사용자 정의 XYZ URL
    "custom":          None,
}

PROVIDER_NAMES = ", ".join(TILE_PROVIDERS.keys())


# ──────────────────────────────────────────
# 시간 문자열 파서 — '60s' / '5m' / '1h' / '1d' → 초(float)
# ──────────────────────────────────────────

def parse_duration_str(value) -> float:
    """
    시간 표기 문자열을 초 단위 float로 변환.

    허용 형식:
        - 숫자(int/float): 그대로 초로 간주
        - "60s" / "5m" / "1h" / "1d": 단위 접미사
        - "60": 단위 없으면 초로 간주
        - "" / None / 0 / "0": 0.0 (비활성)

    Args:
        value: 사용자 입력값 (문자열/숫자/None)

    Returns:
        초 단위 float. 비활성/0이면 0.0.

    Raises:
        ValueError: 파싱 실패
    """
    if value is None or value == "" or value == 0:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)

    s = str(value).strip().lower()
    if not s or s == "0":
        return 0.0

    multipliers = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
    suffix = s[-1]
    if suffix in multipliers:
        try:
            return float(s[:-1]) * multipliers[suffix]
        except ValueError as e:
            raise ValueError(f"잘못된 시간 형식: {value!r} (예: '60s', '5m', '1h')") from e

    try:
        return float(s)   # 단위 없으면 초로 간주
    except ValueError as e:
        raise ValueError(f"잘못된 시간 형식: {value!r} (예: '60s', '5m', '1h')") from e


# ──────────────────────────────────────────
# 하위 설정 데이터클래스
# ──────────────────────────────────────────

@dataclass
class ProgressBarConfig:
    enabled:       bool  = True
    color:         tuple = (99, 202, 255)
    alpha:         int   = 200
    outline_color: tuple = (180, 180, 180)
    outline_alpha: int   = 180
    margin_right:  int   = 12
    width:         int   = 200
    height:        int   = 16
    top_offset:    int   = 11


@dataclass
class HudConfig:
    """상단 HUD (날짜·진행 바) 설정"""
    enabled:       bool             = True
    banner_height: int              = 38
    banner_alpha:  int              = 150
    font_size:     int              = 20
    font_path:     str              = ""
    progress_bar:  ProgressBarConfig = field(default_factory=ProgressBarConfig)


# ──────────────────────────────────────────
# 메인 설정 데이터클래스
# ──────────────────────────────────────────

@dataclass
class RenderConfig:
    """
    모든 렌더링 설정. render_config.yml → load_config()로 채워진다.
    CLI 플래그가 개별 필드를 덮어쓴다.
    """
    # ── 지도 공급자 ─────────────────────────────
    provider:       str = "cartodb-voyager"   # TILE_PROVIDERS 키 또는 "custom"
    custom_url:     str = ""                  # provider="custom" 일 때 XYZ URL
    mapbox_token:   str = ""                  # .env MAPBOX_TOKEN 우선
    stadia_api_key: str = ""                  # .env STADIA_API_KEY 우선

    # ── 캔버스 ──────────────────────────────────
    map_w:           int   = 1280
    map_h:           int   = 720
    bbox_padding:    float = 0.10        # 동적 카메라용 — 약간 큰 여백
    bbox_min_pad_deg: float = 0.005

    # ── World Canvas (동적 카메라용 대형 배경, deprecated) ──
    canvas_scale:    int   = 3           # 사용 안함 — 타일 피라미드로 대체됨
    min_viewport_deg: float = 0.008      # 프레임당 최소 뷰포트 크기 (도)

    # ── 타일 피라미드 (다중 줌 적응형 배경) ─────
    max_zoom:         int   = 17         # 다운로드 가능 최대 줌 (대부분 17~19까지 지원)
    zoom_offset:      int   = 0          # 양수=더 정밀(타일 多), 음수=빠름
    prefetch_parallel: int  = 8          # 타일 병렬 다운로드 워커 수
    tile_cache_dir:   str   = "output/_tile_cache"   # 프로젝트 전역 타일 캐시 폴더

    # ── 잔상(trail) ─────────────────────────────
    trail_len:        int   = 300        # 점 개수 기준 잔상 길이 (trail_time_sec=0 일 때 유효)
    trail_time_sec:   float = 0.0        # 시간 기준 잔상 길이(초). 0=비활성 → trail_len 사용
    trail_alpha_min:  int   = 60
    trail_alpha_max:  int   = 255
    current_pt_scale: int   = 2
    outline_color:    tuple = (255, 255, 255)
    outline_alpha:    int   = 200
    outline_extra_r:  int   = 3

    # ── 이동 경로 선 ────────────────────────────
    draw_lines:      bool  = True        # 연속 포인트 연결선 그리기
    line_widths: dict = field(default_factory=lambda: {
        "stationary": 0,    # 0 = 선 안 그림
        "walking":    2,
        "running":    2,
        "cycling":    2,
        "car":        3,
        "taxi":       3,
        "bus":        3,
        "subway":     3,
        "train":      4,
        "highway":    4,
        "flight":     2,
        "unknown":    1,
        "vehicle":    3,
    })

    # ── 정지 포인트 공간 압축 ────────────────────────────
    compress_stationary_enabled:  bool  = False
    compress_stationary_radius_m: float = 100.0   # 클러스터 반경(m)
    compress_stationary_keep:     str   = "median" # 대표 선택: first / median / last

    # ── 이상치 필터 (정지 포인트의 GPS 노이즈 제거) ─────
    outlier_filter_enabled:  bool  = True
    outlier_window_size:     int   = 10        # 선형회귀에 사용할 양쪽 이웃 수
    outlier_max_deviation_m: float = 200.0     # 예측치 대비 허용 오차 (m)

    # ── 이동수단 세분화 (subway/train/car 등 재분류) ─────
    refine_modes_enabled:    bool  = True
    refine_subway_gap_sec:   float = 60.0      # 시간 갭 N초 + 위치 점프 → subway
    refine_subway_jump_m:    float = 300.0
    refine_train_min_speed:  float = 25.0      # 윈도우 평균속도 ≥ N m/s = train
    refine_train_window:     int   = 5         # 지속성 검사 윈도우 크기
    # 속도 임계값 (m/s, 각 활동의 상한선)
    refine_speed_thresholds: dict = field(default_factory=lambda: {
        "stationary":  0.5,
        "walking":     2.0,
        "running":     4.5,
        "cycling":     8.0,
        "car":         25.0,
        "highway":     90.0,
        # > 90: flight
    })

    # ── 아이콘 (이동수단 시각화) ─────────────────
    icon_dir:        str  = "icon"         # 아이콘 PNG 폴더
    icon_size:       int  = 64             # 아이콘 최대 변 크기 (px)
    icon_alpha:      int  = 255            # 0~255 투명도
    icon_rotate:     bool = False          # 차량/비행기 bearing 회전
    activity_icons: dict = field(default_factory=lambda: {
        # 활동 타입 → 아이콘 파일명 (확장자 제외)
        "stationary": "walking",
        "walking":    "walking",
        "running":    "running",
        "cycling":    "bicycle",
        "car":        "car",
        "taxi":       "taxi",
        "bus":        "bus",
        "subway":     "subway",
        "train":      "train",
        "highway":    "car",     # 고속도로 = 차량 (필요시 train 으로 변경)
        "flight":     "airplane",
        "unknown":    "walking",
        # 하위 호환
        "vehicle":    "car",
    })

    # ── 교통수단 색상 (RGB) ─────────────────────
    activity_colors: dict = field(default_factory=lambda: {
        "stationary": (100, 100, 100),
        "walking":    ( 34, 197,  94),
        "running":    ( 16, 185, 129),    # 더 진한 초록
        "cycling":    ( 59, 130, 246),
        "car":        (249, 115,  22),    # 주황 (기본 차량)
        "taxi":       (250, 204,  21),    # 노랑
        "bus":        (139,  92, 246),    # 보라
        "subway":     (236,  72, 153),    # 핫핑크 (지하)
        "train":      (220,  38,  38),    # 진한 빨강 (기차)
        "highway":    (239,  68,  68),    # 빨강 (고속도로)
        "flight":     (168,  85, 247),    # 보라 (비행)
        "unknown":    (200, 200, 200),
        # 하위 호환 — 파서 기본 출력
        "vehicle":    (249, 115,  22),
    })

    # ── 교통수단 점 반경 (px) ───────────────────
    activity_radius: dict = field(default_factory=lambda: {
        "stationary": 2,
        "walking":    3,
        "running":    3,
        "cycling":    4,
        "car":        4,
        "taxi":       4,
        "bus":        5,
        "subway":     5,
        "train":      6,
        "highway":    5,
        "flight":     6,
        "unknown":    2,
        "vehicle":    4,
    })

    # ── HUD ─────────────────────────────────────
    hud: HudConfig = field(default_factory=HudConfig)

    # ── 렌더링 기본값 ────────────────────────────
    duration_sec: int = 60
    fps:          int = 30
    db_path:      str = "timeline.db"
    output_root:  str = "output"

    # ── 재생 속도 / 데이터 다운샘플링 ─────────────
    # speed_factor:        영상 속도 배율 (1.0=기본, 2.0=2배 빠름)
    #                      → duration = duration_sec / speed_factor
    # realtime_speed_sec:  영상 1초당 진행할 실제 시간(초). 0=비활성.
    #                      → duration = (실제 데이터 시간 범위) / realtime_speed_sec
    #                      → speed_factor 와 동시 사용 불가
    # time_step_sec:       GPS 포인트 시간 그리드 다운샘플링 간격(초). 0=비활성.
    #                      → 정지 구간 압축 효과
    speed_factor:        float = 1.0
    realtime_speed_sec:  float = 0.0
    time_step_sec:       float = 0.0

    def resolve_token(self) -> None:
        """환경변수 → 설정값 순서로 API 키 적용"""
        self.mapbox_token   = os.getenv("MAPBOX_TOKEN",   self.mapbox_token)
        self.stadia_api_key = os.getenv("STADIA_API_KEY", self.stadia_api_key)

    def tile_url(self) -> Optional[str]:
        """
        현재 provider에 해당하는 XYZ 타일 URL 반환.
        mapbox → None (Static API 별도 처리).
        custom → custom_url 반환.
        """
        if self.provider == "custom":
            if not self.custom_url:
                raise ValueError("provider='custom' 인데 custom_url이 비어 있습니다.")
            return self.custom_url
        if self.provider not in TILE_PROVIDERS:
            raise ValueError(
                f"알 수 없는 공급자: '{self.provider}'\n"
                f"사용 가능: {PROVIDER_NAMES}"
            )
        return TILE_PROVIDERS[self.provider]  # mapbox → None


# ──────────────────────────────────────────
# YAML 로더
# ──────────────────────────────────────────

def load_config(config_path: Optional[Path] = None) -> RenderConfig:
    """
    render_config.yml 로드. 파일 없으면 기본값 반환.
    개별 키만 덮어씀 — 누락된 키는 기본값 유지.
    """
    cfg  = RenderConfig()
    path = config_path or Path("render_config.yml")

    if not path.exists():
        cfg.resolve_token()
        return cfg

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    # map 섹션
    m = raw.get("map", {})
    if "provider"       in m: cfg.provider       = str(m["provider"])
    if "custom_url"     in m: cfg.custom_url      = str(m["custom_url"])
    if "mapbox_token"   in m: cfg.mapbox_token    = str(m["mapbox_token"])
    if "stadia_api_key" in m: cfg.stadia_api_key  = str(m["stadia_api_key"])

    # canvas 섹션
    c = raw.get("canvas", {})
    if "width"                in c: cfg.map_w            = int(c["width"])
    if "height"               in c: cfg.map_h             = int(c["height"])
    if "bbox_padding"         in c: cfg.bbox_padding      = float(c["bbox_padding"])
    if "bbox_min_padding_deg" in c: cfg.bbox_min_pad_deg  = float(c["bbox_min_padding_deg"])
    if "scale"                in c: cfg.canvas_scale      = int(c["scale"])
    if "min_viewport_deg"     in c: cfg.min_viewport_deg  = float(c["min_viewport_deg"])

    # tiles 섹션 (타일 피라미드)
    ts = raw.get("tiles", {})
    if "max_zoom"          in ts: cfg.max_zoom          = int(ts["max_zoom"])
    if "zoom_offset"       in ts: cfg.zoom_offset       = int(ts["zoom_offset"])
    if "prefetch_parallel" in ts: cfg.prefetch_parallel = int(ts["prefetch_parallel"])
    if "cache_dir"         in ts: cfg.tile_cache_dir    = str(ts["cache_dir"])

    # trail 섹션
    t = raw.get("trail", {})
    if "length"               in t: cfg.trail_len        = int(t["length"])
    if "time_sec"             in t: cfg.trail_time_sec   = parse_duration_str(t["time_sec"])
    if "alpha_min"            in t: cfg.trail_alpha_min  = int(t["alpha_min"])
    if "alpha_max"            in t: cfg.trail_alpha_max  = int(t["alpha_max"])
    if "current_point_scale"  in t: cfg.current_pt_scale = int(t["current_point_scale"])
    if "outline_color"        in t: cfg.outline_color    = tuple(t["outline_color"])
    if "outline_alpha"        in t: cfg.outline_alpha    = int(t["outline_alpha"])
    if "outline_extra_radius" in t: cfg.outline_extra_r  = int(t["outline_extra_radius"])
    if "draw_lines"           in t: cfg.draw_lines       = bool(t["draw_lines"])

    # colors / radius / line_width 섹션
    if "colors" in raw:
        cfg.activity_colors = {k: tuple(v) for k, v in raw["colors"].items()}
    if "radius" in raw:
        cfg.activity_radius = {k: int(v) for k, v in raw["radius"].items()}
    if "line_width" in raw:
        cfg.line_widths = {k: int(v) for k, v in raw["line_width"].items()}

    # compress_stationary 섹션
    cs = raw.get("compress_stationary", {})
    if "enabled"   in cs: cfg.compress_stationary_enabled  = bool(cs["enabled"])
    if "radius_m"  in cs: cfg.compress_stationary_radius_m = float(cs["radius_m"])
    if "keep"      in cs: cfg.compress_stationary_keep     = str(cs["keep"])

    # outlier 필터
    of = raw.get("outlier", {})
    if "enabled"          in of: cfg.outlier_filter_enabled  = bool(of["enabled"])
    if "window_size"      in of: cfg.outlier_window_size     = int(of["window_size"])
    if "max_deviation_m"  in of: cfg.outlier_max_deviation_m = float(of["max_deviation_m"])

    # refine (이동수단 세분화)
    rf = raw.get("refine", {})
    if "enabled"        in rf: cfg.refine_modes_enabled    = bool(rf["enabled"])
    if "subway_gap_sec" in rf: cfg.refine_subway_gap_sec   = float(rf["subway_gap_sec"])
    if "subway_jump_m"  in rf: cfg.refine_subway_jump_m    = float(rf["subway_jump_m"])
    if "train_min_speed" in rf: cfg.refine_train_min_speed = float(rf["train_min_speed"])
    if "train_window"   in rf: cfg.refine_train_window     = int(rf["train_window"])
    if "speed_thresholds" in rf:
        cfg.refine_speed_thresholds = {k: float(v) for k, v in rf["speed_thresholds"].items()}

    # icon 설정
    ic = raw.get("icon", {})
    if "dir"        in ic: cfg.icon_dir     = str(ic["dir"])
    if "size"       in ic: cfg.icon_size    = int(ic["size"])
    if "alpha"      in ic: cfg.icon_alpha   = int(ic["alpha"])
    if "rotate"     in ic: cfg.icon_rotate  = bool(ic["rotate"])
    if "mapping"    in ic: cfg.activity_icons = {k: str(v) for k, v in ic["mapping"].items()}

    # hud 섹션
    h = raw.get("hud", {})
    if "enabled"       in h: cfg.hud.enabled       = bool(h["enabled"])
    if "banner_height" in h: cfg.hud.banner_height  = int(h["banner_height"])
    if "banner_alpha"  in h: cfg.hud.banner_alpha   = int(h["banner_alpha"])
    if "font_size"     in h: cfg.hud.font_size       = int(h["font_size"])
    if "font_path"     in h: cfg.hud.font_path       = str(h["font_path"])
    pb = h.get("progress_bar", {})
    if pb:
        hpb = cfg.hud.progress_bar
        if "enabled"       in pb: hpb.enabled       = bool(pb["enabled"])
        if "color"         in pb: hpb.color          = tuple(pb["color"])
        if "alpha"         in pb: hpb.alpha          = int(pb["alpha"])
        if "outline_color" in pb: hpb.outline_color  = tuple(pb["outline_color"])
        if "outline_alpha" in pb: hpb.outline_alpha  = int(pb["outline_alpha"])
        if "margin_right"  in pb: hpb.margin_right   = int(pb["margin_right"])
        if "width"         in pb: hpb.width           = int(pb["width"])
        if "height"        in pb: hpb.height          = int(pb["height"])
        if "top_offset"    in pb: hpb.top_offset      = int(pb["top_offset"])

    # render 섹션
    r = raw.get("render", {})
    if "duration_sec" in r: cfg.duration_sec = int(r["duration_sec"])
    if "fps"          in r: cfg.fps           = int(r["fps"])
    if "db_path"      in r: cfg.db_path       = str(r["db_path"])
    if "output_root"  in r: cfg.output_root   = str(r["output_root"])

    # playback 섹션 (재생 속도 / 다운샘플링)
    pb = raw.get("playback", {})
    if "speed"          in pb: cfg.speed_factor       = float(pb["speed"])
    if "realtime_speed" in pb: cfg.realtime_speed_sec = parse_duration_str(pb["realtime_speed"])
    if "time_step"      in pb: cfg.time_step_sec      = parse_duration_str(pb["time_step"])

    cfg.resolve_token()
    return cfg
