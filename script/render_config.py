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
    bbox_padding:    float = 0.05
    bbox_min_pad_deg: float = 0.005

    # ── 잔상(trail) ─────────────────────────────
    trail_len:        int   = 300
    trail_alpha_min:  int   = 60
    trail_alpha_max:  int   = 255
    current_pt_scale: int   = 2
    outline_color:    tuple = (255, 255, 255)
    outline_alpha:    int   = 200
    outline_extra_r:  int   = 3

    # ── 교통수단 색상 (RGB) ─────────────────────
    activity_colors: dict = field(default_factory=lambda: {
        "stationary": (100, 100, 100),
        "walking":    ( 34, 197,  94),
        "cycling":    ( 59, 130, 246),
        "vehicle":    (249, 115,  22),
        "highway":    (239,  68,  68),
        "flight":     (168,  85, 247),
        "unknown":    (200, 200, 200),
    })

    # ── 교통수단 점 반경 (px) ───────────────────
    activity_radius: dict = field(default_factory=lambda: {
        "stationary": 2,
        "walking":    3,
        "cycling":    4,
        "vehicle":    4,
        "highway":    5,
        "flight":     6,
        "unknown":    2,
    })

    # ── HUD ─────────────────────────────────────
    hud: HudConfig = field(default_factory=HudConfig)

    # ── 렌더링 기본값 ────────────────────────────
    duration_sec: int = 60
    fps:          int = 30
    db_path:      str = "timeline.db"
    output_root:  str = "output"

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
    if "width"               in c: cfg.map_w            = int(c["width"])
    if "height"              in c: cfg.map_h             = int(c["height"])
    if "bbox_padding"        in c: cfg.bbox_padding      = float(c["bbox_padding"])
    if "bbox_min_padding_deg" in c: cfg.bbox_min_pad_deg = float(c["bbox_min_padding_deg"])

    # trail 섹션
    t = raw.get("trail", {})
    if "length"               in t: cfg.trail_len        = int(t["length"])
    if "alpha_min"            in t: cfg.trail_alpha_min  = int(t["alpha_min"])
    if "alpha_max"            in t: cfg.trail_alpha_max  = int(t["alpha_max"])
    if "current_point_scale"  in t: cfg.current_pt_scale = int(t["current_point_scale"])
    if "outline_color"        in t: cfg.outline_color    = tuple(t["outline_color"])
    if "outline_alpha"        in t: cfg.outline_alpha    = int(t["outline_alpha"])
    if "outline_extra_radius" in t: cfg.outline_extra_r  = int(t["outline_extra_radius"])

    # colors / radius 섹션
    if "colors" in raw:
        cfg.activity_colors = {k: tuple(v) for k, v in raw["colors"].items()}
    if "radius" in raw:
        cfg.activity_radius = {k: int(v) for k, v in raw["radius"].items()}

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

    cfg.resolve_token()
    return cfg
