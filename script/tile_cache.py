"""
Google Timeline Visualizer — XYZ 타일 캐시 모듈
파일: tile_cache.py

핵심 책임:
  1. TileCache: (z, x, y) 단위 영구 디스크 캐시 + 병렬 다운로드
  2. Web Mercator 타일 좌표 ↔ 위경도 변환
  3. 임의 bbox + zoom → 타일 모자이크 합성 + 서브픽셀 정확 크롭

설계 핵심:
  · 타일은 표준 256×256 XYZ 포맷 (OSM/CartoDB/Stadia/Mapbox 모두 지원)
  · 캐시는 프로젝트 전역(provider 별 분리) — 기간/실행 간 재사용
  · 사전 수집 → 병렬 다운로드 후 렌더링 (네트워크 병목 최소화)
  · AFFINE + BICUBIC 변환으로 ±0.5px 미만 좌표 정확도
"""

from __future__ import annotations

import math
import logging
from pathlib import Path
from typing import Iterable, Optional, Set, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from PIL import Image

log = logging.getLogger(__name__)

TILE_SIZE = 256          # XYZ 표준 타일 크기
DEFAULT_UA = "GoogleTimelineVisualizer/1.0 (+https://github.com/example)"


# ──────────────────────────────────────────
# 타일 좌표 수학 (Web Mercator EPSG:3857)
# ──────────────────────────────────────────

def latlng_to_tile(lat: float, lng: float, zoom: int) -> tuple[float, float]:
    """
    위경도 → fractional XYZ 타일 좌표.
    반환은 float이라 서브픽셀 정확도 유지에 활용.
    """
    n = 2 ** zoom
    x = (lng + 180.0) / 360.0 * n
    sin_lat = math.sin(math.radians(lat))
    sin_lat = max(-0.99999, min(0.99999, sin_lat))   # 극지방 클램프
    y = (0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * n
    return x, y


def tile_to_latlng(x: float, y: float, zoom: int) -> tuple[float, float]:
    """fractional 타일 좌표 → 위경도 (역변환, 디버깅용)"""
    n = 2 ** zoom
    lng = x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    return math.degrees(lat_rad), lng


def tiles_for_bbox(
    bbox: tuple[float, float, float, float], zoom: int
) -> list[tuple[int, int, int]]:
    """
    bbox를 덮는 모든 (z, x, y) 정수 타일 좌표 목록.
    bbox = (min_lat, min_lng, max_lat, max_lng)
    """
    min_lat, min_lng, max_lat, max_lng = bbox
    tl_x, tl_y = latlng_to_tile(max_lat, min_lng, zoom)   # 좌상
    br_x, br_y = latlng_to_tile(min_lat, max_lng, zoom)   # 우하

    n = 2 ** zoom
    x0 = max(0, int(math.floor(tl_x)))
    y0 = max(0, int(math.floor(tl_y)))
    x1 = min(n - 1, int(math.floor(br_x)))
    y1 = min(n - 1, int(math.floor(br_y)))

    return [(zoom, x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


def ideal_zoom_for_bbox(
    bbox:    tuple[float, float, float, float],
    target_w: int,
    target_h: int,
    max_zoom: int = 17,
    zoom_offset: int = 0,
) -> int:
    """
    bbox를 (target_w, target_h) 출력 크기에 ≈1:1 픽셀비로 담기 위한 줌 레벨.

    수식: target_w = lng_frac × 256 × 2^Z  →  Z = log2(target_w / lng_frac / 256)

    Args:
        zoom_offset: 양수 = 더 정밀(타일 多, 디테일↑), 음수 = 덜 정밀(빠름)
    """
    min_lat, min_lng, max_lat, max_lng = bbox

    # Mercator y 비율 (위도)
    sin_top = math.sin(math.radians(max_lat)); sin_top = max(-0.99999, min(0.99999, sin_top))
    sin_bot = math.sin(math.radians(min_lat)); sin_bot = max(-0.99999, min(0.99999, sin_bot))
    y_top = 0.5 - math.log((1 + sin_top) / (1 - sin_top)) / (4 * math.pi)
    y_bot = 0.5 - math.log((1 + sin_bot) / (1 - sin_bot)) / (4 * math.pi)

    lat_frac = abs(y_bot - y_top)
    lng_frac = abs(max_lng - min_lng) / 360.0

    if lng_frac < 1e-12 or lat_frac < 1e-12:
        return max_zoom

    z_lng = math.log2(target_w / lng_frac / TILE_SIZE)
    z_lat = math.log2(target_h / lat_frac / TILE_SIZE)
    ideal = min(z_lng, z_lat) + zoom_offset

    # ceil로 1픽셀 여유 확보 (업스케일이 아닌 다운샘플링 쪽 선택)
    return max(0, min(max_zoom, math.ceil(ideal)))


# ──────────────────────────────────────────
# TileCache — 영구 디스크 캐시 + 병렬 다운로드
# ──────────────────────────────────────────

class TileCache:
    """
    XYZ 타일 영구 캐시.

    파일 구조:
        <cache_dir>/<provider>/<z>/<x>/<y>.png

    스레드 안전성: 동일 (z,x,y)에 대해 멱등 처리 (중복 다운로드는 마지막 쓰기가 승리).
    """

    def __init__(
        self,
        url_template: str,
        cache_dir:    Path,
        provider:     str,
        user_agent:   str = DEFAULT_UA,
        timeout:      int = 15,
    ):
        self.url_template = url_template
        self.provider     = provider
        self.cache_root   = Path(cache_dir) / provider
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.headers      = {"User-Agent": user_agent}
        self.timeout      = timeout
        self._session     = requests.Session()
        self._session.headers.update(self.headers)

    def _path(self, z: int, x: int, y: int) -> Path:
        return self.cache_root / str(z) / str(x) / f"{y}.png"

    def has(self, z: int, x: int, y: int) -> bool:
        """캐시 존재 여부만 확인 (다운로드 X)"""
        return self._path(z, x, y).exists()

    def get(self, z: int, x: int, y: int) -> Optional[Image.Image]:
        """
        (z,x,y) 타일을 PIL Image로 반환.
        캐시에 있으면 즉시 로드, 없으면 다운로드 후 저장.
        실패 시 None 반환 (호출자가 placeholder 처리).
        """
        path = self._path(z, x, y)
        if path.exists():
            try:
                return Image.open(path).convert("RGB")
            except Exception as e:
                log.warning("타일 캐시 손상, 재다운로드: %s (%s)", path, e)
                path.unlink(missing_ok=True)

        url = self.url_template.format(z=z, x=x, y=y)
        try:
            resp = self._session.get(url, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as e:
            log.warning("타일 다운로드 실패 z%d/%d/%d: %s", z, x, y, e)
            return None

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
        try:
            return Image.open(path).convert("RGB")
        except Exception as e:
            log.warning("타일 디코드 실패 z%d/%d/%d: %s", z, x, y, e)
            path.unlink(missing_ok=True)
            return None

    def prefetch(
        self,
        tiles:       Iterable[tuple[int, int, int]],
        parallel:    int = 8,
        report_every: int = 50,
    ) -> tuple[int, int, int]:
        """
        타일 목록을 병렬 다운로드.
        이미 캐시된 타일은 스킵.

        Returns:
            (cache_hit, downloaded, failed)
        """
        # 중복 제거 + 미캐시만 추출
        unique = set(tiles)
        to_fetch = [t for t in unique if not self.has(*t)]
        cache_hit = len(unique) - len(to_fetch)

        if not to_fetch:
            log.info("타일 사전 수집: %d개 모두 캐시 적중", len(unique))
            return cache_hit, 0, 0

        log.info("타일 사전 수집: 캐시 적중 %d개 / 다운로드 필요 %d개 (parallel=%d)",
                 cache_hit, len(to_fetch), parallel)

        ok, fail = 0, 0
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = {pool.submit(self.get, z, x, y): (z, x, y) for z, x, y in to_fetch}
            for i, fut in enumerate(as_completed(futures), 1):
                try:
                    result = fut.result()
                    if result is not None:
                        ok += 1
                    else:
                        fail += 1
                except Exception:
                    fail += 1
                if i % report_every == 0 or i == len(to_fetch):
                    pct = i / len(to_fetch) * 100
                    log.info("  [%4d/%4d] %.1f%%  (✓%d ✗%d)", i, len(to_fetch), pct, ok, fail)

        log.info("사전 수집 완료: 신규 %d개, 실패 %d개", ok, fail)
        return cache_hit, ok, fail


# ──────────────────────────────────────────
# 합성 — bbox + zoom → target 크기 캔버스
# ──────────────────────────────────────────

def composite_for_bbox(
    cache:    TileCache,
    bbox:     tuple[float, float, float, float],
    target_w: int,
    target_h: int,
    zoom:     int,
    fallback_color: tuple[int, int, int] = (200, 200, 200),
) -> Image.Image:
    """
    bbox + zoom → target 크기 합성 이미지 (서브픽셀 정확도).

    절차:
      1. bbox 코너의 fractional 타일 좌표 산출
      2. 정수 타일 범위 → 모자이크 캔버스(타일 갯수 × 256)에 paste
      3. AFFINE 변환으로 fractional 영역만 추출 + target 크기로 리샘플
         · BICUBIC: 다운샘플링 시 고품질, AFFINE 호환
         · 변환 매트릭스로 서브픽셀 정확도 유지
    """
    min_lat, min_lng, max_lat, max_lng = bbox
    tl_x_f, tl_y_f = latlng_to_tile(max_lat, min_lng, zoom)
    br_x_f, br_y_f = latlng_to_tile(min_lat, max_lng, zoom)

    # 음수 또는 zero-span 방어
    if br_x_f <= tl_x_f or br_y_f <= tl_y_f:
        return Image.new("RGB", (target_w, target_h), fallback_color)

    n = 2 ** zoom
    tl_xi = max(0,     int(math.floor(tl_x_f)))
    tl_yi = max(0,     int(math.floor(tl_y_f)))
    br_xi = min(n - 1, int(math.floor(br_x_f)))
    br_yi = min(n - 1, int(math.floor(br_y_f)))

    tiles_w = br_xi - tl_xi + 1
    tiles_h = br_yi - tl_yi + 1

    mosaic = Image.new("RGB", (tiles_w * TILE_SIZE, tiles_h * TILE_SIZE), fallback_color)

    for tx in range(tl_xi, br_xi + 1):
        for ty in range(tl_yi, br_yi + 1):
            tile = cache.get(zoom, tx, ty)
            if tile is None:
                continue
            mosaic.paste(tile, ((tx - tl_xi) * TILE_SIZE, (ty - tl_yi) * TILE_SIZE))

    # bbox에 해당하는 모자이크 내 fractional 영역
    src_x0 = (tl_x_f - tl_xi) * TILE_SIZE
    src_y0 = (tl_y_f - tl_yi) * TILE_SIZE
    src_x1 = (br_x_f - tl_xi) * TILE_SIZE
    src_y1 = (br_y_f - tl_yi) * TILE_SIZE

    sw = src_x1 - src_x0
    sh = src_y1 - src_y0
    if sw <= 0 or sh <= 0:
        return Image.new("RGB", (target_w, target_h), fallback_color)

    # AFFINE: dst 픽셀 (u, v) → src 픽셀 (a*u + 0*v + c, 0*u + e*v + f)
    a = sw / target_w
    e = sh / target_h
    return mosaic.transform(
        (target_w, target_h),
        Image.AFFINE,
        (a, 0, src_x0, 0, e, src_y0),
        Image.BICUBIC,
    )


# ──────────────────────────────────────────
# Provider URL 빌더
# ──────────────────────────────────────────

def build_tile_url(provider: str, base_url: Optional[str],
                   custom_url: str = "",
                   mapbox_token: str = "",
                   stadia_api_key: str = "") -> str:
    """
    Provider 식별자 + 인증 정보 → 최종 XYZ URL 템플릿.
    {z}, {x}, {y} 플레이스홀더는 그대로 보존.
    """
    if provider == "custom":
        if not custom_url:
            raise ValueError("provider='custom' 인데 custom_url이 비어 있습니다.")
        return custom_url

    if provider == "mapbox":
        if not mapbox_token:
            raise EnvironmentError(
                "Mapbox provider는 MAPBOX_TOKEN이 필요합니다.\n"
                ".env 파일에 MAPBOX_TOKEN=pk.eyJ1... 추가 후 재실행하세요."
            )
        # custom_url을 Mapbox style ID로 재해석 (예: 'mapbox/dark-v11')
        style = custom_url or "mapbox/dark-v11"
        return (
            f"https://api.mapbox.com/styles/v1/{style}/tiles/{{z}}/{{x}}/{{y}}"
            f"?access_token={mapbox_token}"
        )

    if base_url is None:
        raise ValueError(f"알 수 없는 provider: {provider}")

    # Stadia: api_key가 있으면 query로 첨부
    if provider.startswith("stadia") and stadia_api_key:
        sep = "&" if "?" in base_url else "?"
        return f"{base_url}{sep}api_key={stadia_api_key}"

    return base_url
