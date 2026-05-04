"""
Google Timeline Visualizer — 통합 메인 엔트리
파일: main.py

3단계 파이프라인을 단일 CLI로 통합 + 각 단계 개별 실행 지원.

전체 파이프라인:
  python main.py pipeline 2026-03 --json "data/Timeline Edits.json"

개별 단계:
  python main.py parse "data/Timeline Edits.json"   # Phase 1: JSON → SQLite
  python main.py summary                              # DB 요약
  python main.py render 2026-03                      # Phase 2: PNG 시퀀스
  python main.py encode 2026-03                      # Phase 3: MP4 + GIF

상세 도움말:
  python main.py --help
  python main.py <subcommand> --help
"""

from __future__ import annotations

import sys
import argparse
import logging
import sqlite3
from pathlib import Path

# Windows 콘솔 cp949 인코딩 우회 — 한글 도움말 출력용
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")   # type: ignore[attr-defined]
    except Exception:
        pass

# script/ 디렉터리를 import 경로에 추가
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT / "script"))

# 모듈 임포트 (Phase 별)
from timeline_parser  import run as parser_run, init_db, get_summary       # Phase 1
from render_config    import (
    load_config, RenderConfig, PROVIDER_NAMES, parse_duration_str,
)                                                                           # Phase 2 설정
from frame_renderer   import render_frames                                 # Phase 2
from video_encoder    import encode as encoder_encode                      # Phase 3

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════
# 서브커맨드 핸들러
# ═════════════════════════════════════════════════════════════════

def cmd_parse(args: argparse.Namespace) -> None:
    """Phase 1 — Google Timeline JSON 파싱 후 SQLite DB 저장"""
    json_path = Path(args.json_path)
    if not json_path.exists():
        log.error("파일 없음: %s", json_path)
        sys.exit(1)
    db_path = Path(args.db) if getattr(args, "db", None) else Path("db/timeline.db")
    parser_run(json_path, db_path=db_path)


def cmd_summary(args: argparse.Namespace) -> None:
    """DB 요약 통계 출력 — 월별 분포 + 교통수단 분포"""
    db = Path(args.db) if getattr(args, "db", None) else Path("db/timeline.db")
    if not db.exists():
        log.error("DB 없음: %s", db)
        log.error("먼저 파싱을 실행하세요: python main.py parse <json_path>")
        sys.exit(1)

    conn    = sqlite3.connect(db)
    summary = get_summary(conn)

    print(f"\n총 GPS 포인트: {summary['total_points']:,}건")
    print("──────────────── 월별 분포 ────────────────")
    for m in summary["months"]:
        print(f"  {m['year']}-{m['month']:02d}: {m['count']:,}건")
    print("──────────── 교통수단 분포 ────────────────")
    for act, cnt in sorted(summary["by_activity"].items()):
        print(f"  {act:<12}: {cnt:,}건")
    conn.close()


def _parse_rgb(value: str, flag: str) -> tuple[int, int, int]:
    """
    'R,G,B' 형식 문자열을 (R, G, B) 정수 튜플로 변환.
    범위(0~255) 벗어나거나 파싱 실패 시 에러 후 종료.
    """
    try:
        parts = [int(x.strip()) for x in value.split(",")]
    except ValueError:
        log.error("%s 값이 올바르지 않습니다: %r  (형식: 'R,G,B'  예: '255,128,0')", flag, value)
        sys.exit(1)
    if len(parts) != 3:
        log.error("%s 는 세 값(R,G,B)이어야 합니다: %r", flag, value)
        sys.exit(1)
    if any(not (0 <= v <= 255) for v in parts):
        log.error("%s 값은 0~255 범위여야 합니다: %r", flag, value)
        sys.exit(1)
    return (parts[0], parts[1], parts[2])


def _apply_render_overrides(cfg: RenderConfig, args: argparse.Namespace) -> RenderConfig:
    """CLI 인자로 RenderConfig 필드 덮어씀 (None이 아닐 때만)"""
    if getattr(args, "map", None):
        if args.map.startswith("http"):
            cfg.provider   = "custom"
            cfg.custom_url = args.map
        else:
            cfg.provider = args.map
    if getattr(args, "duration", None) is not None: cfg.duration_sec = args.duration
    if getattr(args, "fps", None)      is not None: cfg.fps          = args.fps
    if getattr(args, "trail", None)    is not None: cfg.trail_len    = args.trail

    # 시간 기준 trail
    tt = getattr(args, "trail_time", None)
    if tt is not None:
        try:
            cfg.trail_time_sec = parse_duration_str(tt)
        except ValueError as e:
            log.error("--trail-time 파싱 실패: %s", e)
            sys.exit(1)

    # 정지 포인트 압축
    if getattr(args, "compress_stationary", False):
        cfg.compress_stationary_enabled = True
    cr = getattr(args, "compress_radius", None)
    if cr is not None:
        cfg.compress_stationary_radius_m = cr

    # 재생 속도 — --speed / --realtime-speed 동시 지정 불가
    speed = getattr(args, "speed", None)
    rt    = getattr(args, "realtime_speed", None)
    if speed is not None and rt is not None:
        log.error("--speed 와 --realtime-speed 는 동시에 사용할 수 없습니다.")
        sys.exit(1)
    if speed is not None:
        if speed <= 0:
            log.error("--speed 는 양수여야 합니다: %s", speed)
            sys.exit(1)
        cfg.speed_factor = speed
    if rt is not None:
        try:
            cfg.realtime_speed_sec = parse_duration_str(rt)
        except ValueError as e:
            log.error("--realtime-speed 파싱 실패: %s", e)
            sys.exit(1)

    # 시간 그리드 다운샘플링
    ts = getattr(args, "time_step", None)
    if ts is not None:
        try:
            cfg.time_step_sec = parse_duration_str(ts)
        except ValueError as e:
            log.error("--time-step 파싱 실패: %s", e)
            sys.exit(1)

    # ── HUD 옵션 오버라이드 ──────────────────────────────────────────────────
    # HUD 전체 on/off
    if getattr(args, "no_hud", False):
        cfg.hud.enabled = False

    # 날짜·시각·초 표시
    hud_date = getattr(args, "hud_date", None)
    if hud_date is not None:
        cfg.hud.show_date = hud_date
    hud_time = getattr(args, "hud_time", None)
    if hud_time is not None:
        cfg.hud.show_time = hud_time
    hud_seconds = getattr(args, "hud_seconds", None)
    if hud_seconds is not None:
        cfg.hud.show_seconds = hud_seconds

    # 네비게이션 바(진행 바)
    if getattr(args, "no_navbar", False):
        cfg.hud.progress_bar.enabled = False

    # 배너 배경 투명도·색상
    hud_alpha = getattr(args, "hud_alpha", None)
    if hud_alpha is not None:
        if not (0 <= hud_alpha <= 255):
            log.error("--hud-alpha 는 0~255 범위여야 합니다: %s", hud_alpha)
            sys.exit(1)
        cfg.hud.banner_alpha = hud_alpha
    hud_color = getattr(args, "hud_color", None)
    if hud_color is not None:
        cfg.hud.banner_color = _parse_rgb(hud_color, "--hud-color")

    # 텍스트 색상·투명도
    hud_text_color = getattr(args, "hud_text_color", None)
    if hud_text_color is not None:
        cfg.hud.text_color = _parse_rgb(hud_text_color, "--hud-text-color")
    hud_text_alpha = getattr(args, "hud_text_alpha", None)
    if hud_text_alpha is not None:
        if not (0 <= hud_text_alpha <= 255):
            log.error("--hud-text-alpha 는 0~255 범위여야 합니다: %s", hud_text_alpha)
            sys.exit(1)
        cfg.hud.text_alpha = hud_text_alpha

    return cfg


def cmd_render(args: argparse.Namespace) -> None:
    """Phase 2 — GPS 트랙 → PNG 프레임 시퀀스"""
    cfg = load_config(args.config)
    cfg = _apply_render_overrides(cfg, args)
    render_frames(
        period_str = args.period,
        output_dir = args.output,
        cfg        = cfg,
        verify     = args.verify,
    )


def cmd_encode(args: argparse.Namespace) -> None:
    """Phase 3 — PNG 시퀀스 → MP4 + GIF"""
    encoder_encode(
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


def cmd_pipeline(args: argparse.Namespace) -> None:
    """전체 파이프라인 — Phase 1 → Phase 2 → Phase 3 순차 실행"""
    log.info("══════════════════════════════════════════════")
    log.info("  Google Timeline Visualizer — 전체 파이프라인")
    log.info("══════════════════════════════════════════════")

    # ── Phase 1: Parse (JSON 제공 + DB 없을 때만) ───────────
    db_path = Path("db/timeline.db")
    if args.json_path:
        if db_path.exists() and not args.force_parse:
            log.info("[Phase 1] DB가 이미 존재 → 파싱 생략 (--force-parse 로 재실행)")
        else:
            log.info("[Phase 1] 파싱 시작: %s", args.json_path)
            parser_run(Path(args.json_path), db_path=db_path)
    elif not db_path.exists():
        log.error("DB가 없습니다. --json 으로 JSON 경로를 지정하거나 먼저 parse를 실행하세요")
        sys.exit(1)

    # ── Phase 2: Render ──────────────────────────────────────
    log.info("[Phase 2] 프레임 렌더링: %s", args.period)
    cfg = load_config(args.config)
    cfg = _apply_render_overrides(cfg, args)
    render_frames(period_str=args.period, output_dir=None, cfg=cfg, verify=False)

    # ── Phase 3: Encode (--no-encode 시 생략) ────────────────
    if args.no_encode:
        log.info("[Phase 3] 인코딩 생략 (--no-encode)")
        return

    log.info("[Phase 3] MP4/GIF 인코딩")
    encoder_encode(
        period_str = args.period,
        fps        = cfg.fps,
        crf        = args.crf,
        preset     = args.preset,
        gif_fps    = args.gif_fps,
        gif_scale  = args.gif_scale,
        mp4_only   = args.mp4_only,
        gif_only   = args.gif_only,
        output_dir = None,
    )

    log.info("══════════════════════════════════════════════")
    log.info("  파이프라인 완료 → output/%s/", args.period.replace("~", "_"))
    log.info("══════════════════════════════════════════════")


# ═════════════════════════════════════════════════════════════════
# 인자 파서 빌더 (서브커맨드별 상세 --help)
# ═════════════════════════════════════════════════════════════════

def _add_render_args(p: argparse.ArgumentParser, include_period: bool = True) -> None:
    """render / pipeline 양쪽에서 공유하는 렌더링 옵션"""
    if include_period:
        p.add_argument(
            "period",
            help=(
                "기간 문자열\n"
                "  · 월 단위: '2026-03'  → 2026-03-01 ~ 2026-03-31\n"
                "  · 범위:    '2025-12-20~2025-12-31'"
            ),
        )
    p.add_argument(
        "--map", metavar="PROVIDER", default=None,
        help=(
            "지도 타일 공급자 (render_config.yml 덮어씀)\n"
            f"  · 무료: {PROVIDER_NAMES}\n"
            "  · 커스텀 XYZ URL 직접 전달도 가능 ('https://.../{z}/{x}/{y}.png')"
        ),
    )
    p.add_argument(
        "--config", type=Path, default=None, metavar="FILE",
        help="설정 YAML 파일 경로 (기본: ./render_config.yml)",
    )
    p.add_argument(
        "--duration", type=int, default=None, metavar="N",
        help="목표 영상 길이(초). 총 프레임 수 = duration × fps (기본: 60)",
    )
    p.add_argument(
        "--fps", type=int, default=None, metavar="N",
        help="프레임 레이트 (기본: 30)",
    )
    p.add_argument(
        "--trail", type=int, default=None, metavar="N",
        help="잔상으로 표시할 최대 이전 포인트 수 (기본: 300)",
    )
    p.add_argument(
        "--speed", type=float, default=None, metavar="N",
        help=(
            "영상 속도 배율 (기본: 1.0)\n"
            "  · 2.0 = 2배 빠름 (duration 절반)\n"
            "  · 0.5 = 2배 느림 (duration 두 배)\n"
            "  · --realtime-speed 와 동시 사용 불가"
        ),
    )
    p.add_argument(
        "--realtime-speed", dest="realtime_speed", default=None, metavar="DUR",
        help=(
            "영상 1초당 진행할 실제 시간 (예: '1h' = 1초당 1시간)\n"
            "  · 단위: s/m/h/d\n"
            "  · 데이터 시간 범위와 무관하게 일정한 체감 속도 보장\n"
            "  · --speed 와 동시 사용 불가"
        ),
    )
    p.add_argument(
        "--time-step", dest="time_step", default=None, metavar="DUR",
        help=(
            "GPS 포인트 시간 간격 다운샘플링 (예: '60s', '5m', '1h')\n"
            "  · 단위: s/m/h/d\n"
            "  · 정지 구간 압축 — 이동 구간 강조\n"
            "  · 미설정 시 모든 포인트 사용"
        ),
    )
    p.add_argument(
        "--trail-time", dest="trail_time", default=None, metavar="DUR",
        help=(
            "시간 기준 잔상 길이 (예: '5m', '300s')\n"
            "  · 단위: s/m/h/d\n"
            "  · 현재 위치에서 N초 이내 포인트만 잔상으로 표시\n"
            "  · GPS 샘플링 밀도와 무관하게 체감 잔상 길이 일정\n"
            "  · 미설정 시 --trail (점 개수 기준) 사용"
        ),
    )
    p.add_argument(
        "--compress-stationary", dest="compress_stationary",
        action="store_true", default=False,
        help=(
            "정지 포인트 공간 압축 활성화\n"
            "  · 집·회사 등 체류 중 반경 내 중복 포인트 → 1개로 축소\n"
            "  · 반경은 --compress-radius 로 조정 (기본: 100m)\n"
            "  · render_config.yml compress_stationary.enabled=true 와 동일"
        ),
    )
    p.add_argument(
        "--compress-radius", dest="compress_radius",
        type=float, default=None, metavar="M",
        help=(
            "정지 포인트 압축 반경(m) (기본: 100)\n"
            "  · --compress-stationary 와 함께 사용\n"
            "  · 예: --compress-radius 50  (더 작은 클러스터로 압축)"
        ),
    )

    # ── HUD 옵션 ─────────────────────────────────────────────────────────────
    p.add_argument(
        "--no-hud", dest="no_hud",
        action="store_true", default=False,
        help="HUD 전체 숨김 (날짜·시각 + 네비게이션 바 모두 제거)",
    )
    p.add_argument(
        "--hud-date", dest="hud_date",
        action=argparse.BooleanOptionalAction, default=None,
        help=(
            "날짜(YYYY-MM-DD) 표시 여부 (기본: 표시)\n"
            "  · --hud-date     → 표시 (기본)\n"
            "  · --no-hud-date  → 숨김"
        ),
    )
    p.add_argument(
        "--hud-time", dest="hud_time",
        action=argparse.BooleanOptionalAction, default=None,
        help=(
            "시각(HH:MM 또는 HH:MM:SS) 표시 여부 (기본: 표시)\n"
            "  · --hud-time     → 표시 (기본)\n"
            "  · --no-hud-time  → 숨김"
        ),
    )
    p.add_argument(
        "--hud-seconds", dest="hud_seconds",
        action=argparse.BooleanOptionalAction, default=None,
        help=(
            "시각에서 초(:SS) 표시 여부 (기본: 표시)\n"
            "  · --hud-seconds     → HH:MM:SS (기본)\n"
            "  · --no-hud-seconds  → HH:MM 만 표시\n"
            "  · --hud-time 이 켜진 경우에만 유효"
        ),
    )
    p.add_argument(
        "--no-navbar", dest="no_navbar",
        action="store_true", default=False,
        help="우측 상단 네비게이션 바(진행 바) 숨김",
    )
    p.add_argument(
        "--hud-alpha", dest="hud_alpha",
        type=int, default=None, metavar="N",
        help=(
            "배너 배경 투명도 0~255 (기본: 150)\n"
            "  · 0   = 완전 투명 (배너 안 보임)\n"
            "  · 150 = 반투명 (기본)\n"
            "  · 255 = 불투명"
        ),
    )
    p.add_argument(
        "--hud-color", dest="hud_color",
        default=None, metavar="R,G,B",
        help=(
            "배너 배경 RGB 색상 (기본: '0,0,0')\n"
            "  · 예: --hud-color '0,0,0'      (검정, 기본)\n"
            "  · 예: --hud-color '30,30,60'   (남색 계열)\n"
            "  · 예: --hud-color '255,255,255' (흰색)"
        ),
    )
    p.add_argument(
        "--hud-text-color", dest="hud_text_color",
        default=None, metavar="R,G,B",
        help=(
            "날짜·시각 텍스트 RGB 색상 (기본: '255,255,255')\n"
            "  · 예: --hud-text-color '255,255,255' (흰색, 기본)\n"
            "  · 예: --hud-text-color '255,220,100' (노란색 계열)"
        ),
    )
    p.add_argument(
        "--hud-text-alpha", dest="hud_text_alpha",
        type=int, default=None, metavar="N",
        help=(
            "날짜·시각 텍스트 투명도 0~255 (기본: 255)\n"
            "  · 0   = 완전 투명\n"
            "  · 255 = 불투명 (기본)"
        ),
    )


def _add_encode_args(p: argparse.ArgumentParser, include_period: bool = True) -> None:
    """encode / pipeline 양쪽에서 공유하는 인코딩 옵션"""
    if include_period:
        p.add_argument(
            "period",
            help="기간 (render에서 사용한 값과 동일)",
        )
    p.add_argument(
        "--crf", type=int, default=18, metavar="N",
        help=(
            "MP4 품질 0~51 (기본: 18)\n"
            "  · 0  무손실 (매우 큼)\n"
            "  · 18 고품질 (권장)\n"
            "  · 23 균형\n"
            "  · 28 저용량 (품질 저하)"
        ),
    )
    p.add_argument(
        "--preset", default="medium",
        choices=["ultrafast","superfast","veryfast","faster","fast","medium","slow","slower"],
        help="MP4 인코딩 속도/압축률 균형 (기본: medium)",
    )
    p.add_argument(
        "--gif-fps", type=int, default=15, metavar="N",
        help="GIF 출력 fps (기본: 15)",
    )
    p.add_argument(
        "--gif-scale", type=int, default=960, metavar="W",
        help="GIF 가로 픽셀 (세로 비율 유지, 기본: 960)",
    )
    p.add_argument("--mp4-only", action="store_true", help="MP4만 생성")
    p.add_argument("--gif-only", action="store_true", help="GIF만 생성")


def build_parser() -> argparse.ArgumentParser:
    """전체 argparse 트리 구성"""
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Google Timeline Visualizer — 통합 파이프라인 진입점",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
서브커맨드:
  parse      Phase 1 — JSON → SQLite DB
  summary    DB 요약 통계 (월별/교통수단 분포)
  render     Phase 2 — GPS 트랙 → PNG 프레임 시퀀스
  encode     Phase 3 — PNG 시퀀스 → MP4 + GIF
  pipeline   전체 파이프라인 (parse → render → encode 순차 실행)

각 서브커맨드의 상세 도움말:
  python main.py parse    --help
  python main.py render   --help
  python main.py encode   --help
  python main.py pipeline --help
        """,
    )
    sub = p.add_subparsers(dest="command", required=True, metavar="<command>")

    # ── parse ─────────────────────────────────────────────────────
    pp = sub.add_parser(
        "parse",
        help="Phase 1: Google Takeout JSON 파싱 → SQLite DB 저장",
        description=(
            "Phase 1 — Google Timeline JSON 스트리밍 파싱\n\n"
            "ijson으로 대용량 JSON을 메모리 안전하게 처리하며,\n"
            "GPS 포인트 + 장소 집계를 추출해 timeline.db (SQLite)에 저장한다.\n"
            "speedMetersPerSecond 미존재 포인트는 Haversine 공식으로 보간한다.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python main.py parse "data/Timeline Edits.json"
        """,
    )
    pp.add_argument(
        "json_path",
        help="Google Takeout 타임라인 JSON 파일 경로 (timelineEdits 형식 지원)",
    )
    pp.add_argument(
        "--db", default=None, metavar="FILE",
        help="SQLite DB 경로 (기본: db/timeline.db)",
    )
    pp.set_defaults(func=cmd_parse)

    # ── summary ───────────────────────────────────────────────────
    ps = sub.add_parser(
        "summary",
        help="DB 요약 통계 출력",
        description="현재 DB에 들어있는 GPS 포인트의 월별/교통수단별 분포 출력.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python main.py summary
  python main.py summary --db custom.db
        """,
    )
    ps.add_argument(
        "--db", default=None, metavar="FILE",
        help="SQLite DB 경로 (기본: db/timeline.db)",
    )
    ps.set_defaults(func=cmd_summary)

    # ── render ────────────────────────────────────────────────────
    pr = sub.add_parser(
        "render",
        help="Phase 2: GPS 트랙 → PNG 프레임 시퀀스",
        description=(
            "Phase 2 — 동적 카메라 + World Canvas 기반 PNG 시퀀스 렌더링\n\n"
            "1. 전체 GPS 범위로 대형 World Canvas 다운로드 (1회)\n"
            "2. 매 프레임마다 트레일 bbox 기준으로 크롭 → 가상 카메라 효과\n"
            "3. 이동 경로 선 + 잔상 점 + 현재 위치 외곽선 + HUD 오버레이\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python main.py render 2026-03
  python main.py render 2026-03 --map cartodb-light --duration 30 --fps 24
  python main.py render 2026-03 --verify          # 기준점 검증 이미지만 생성
  python main.py render 2025-12-20~2025-12-31 --trail 500
        """,
    )
    _add_render_args(pr, include_period=True)
    pr.add_argument(
        "--output", type=Path, default=None, metavar="DIR",
        help="PNG 출력 폴더 (기본: output/<period>/frames/)",
    )
    pr.add_argument(
        "--verify", action="store_true",
        help=(
            "기준점 검증 모드 — World Canvas + 9개 빨강 십자가 마커만 생성 후 종료.\n"
            "캔버스 좌표계의 정확도를 시각적으로 확인할 때 사용."
        ),
    )
    pr.set_defaults(func=cmd_render)

    # ── encode ────────────────────────────────────────────────────
    pe = sub.add_parser(
        "encode",
        help="Phase 3: PNG 시퀀스 → MP4 + GIF",
        description=(
            "Phase 3 — FFmpeg 기반 영상 인코딩\n\n"
            "MP4: H.264 (libx264) + yuv420p + +faststart (웹 스트리밍 최적화)\n"
            "GIF: 2-pass 팔레트 최적화 (palettegen + paletteuse, bayer 디더링)\n\n"
            "FFmpeg는 imageio-ffmpeg 번들 또는 시스템 PATH에서 자동 탐색.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python main.py encode 2026-03
  python main.py encode 2026-03 --crf 23 --preset fast
  python main.py encode 2026-03 --gif-only --gif-fps 10 --gif-scale 640
  python main.py encode 2025-12 --mp4-only --crf 18
        """,
    )
    pe.add_argument(
        "--fps", type=int, default=30, metavar="N",
        help="입력 PNG 시퀀스 fps (render 단계와 일치, 기본: 30)",
    )
    _add_encode_args(pe, include_period=True)
    pe.add_argument(
        "--output", type=Path, default=None, metavar="DIR",
        help="출력 폴더 (기본: output/<period>/)",
    )
    pe.set_defaults(func=cmd_encode)

    # ── pipeline ──────────────────────────────────────────────────
    pl = sub.add_parser(
        "pipeline",
        help="전체 파이프라인 (parse → render → encode)",
        description=(
            "통합 파이프라인 — 한 번의 명령으로 전체 처리\n\n"
            "  Phase 1: JSON 파싱 (--json 제공 + DB 미존재 시 자동 실행)\n"
            "  Phase 2: PNG 프레임 렌더링\n"
            "  Phase 3: MP4 + GIF 인코딩 (--no-encode 시 생략)\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python main.py pipeline 2026-03                                       # DB 있을 때
  python main.py pipeline 2026-03 --json "data/Timeline Edits.json"    # 신규 파싱
  python main.py pipeline 2026-03 --map osm --crf 23                   # 옵션 조합
  python main.py pipeline 2026-03 --no-encode                          # PNG까지만
  python main.py pipeline 2026-03 --json X.json --force-parse           # 강제 재파싱
        """,
    )
    pl.add_argument(
        "period",
        help="기간 문자열 (예: 2026-03, 2025-12-20~2025-12-31)",
    )
    pl.add_argument(
        "--json", dest="json_path", default=None, metavar="PATH",
        help=(
            "Google Takeout JSON 경로\n"
            "  · 제공 + DB 없음 → Phase 1 자동 실행\n"
            "  · 제공 + DB 존재 → 생략 (--force-parse 로 강제 실행)\n"
            "  · 미제공 + DB 없음 → 오류 종료"
        ),
    )
    pl.add_argument(
        "--force-parse", action="store_true",
        help="DB가 있어도 강제 재파싱",
    )
    pl.add_argument(
        "--no-encode", action="store_true",
        help="Phase 3 (인코딩) 생략 — PNG 시퀀스까지만 생성",
    )
    _add_render_args(pl, include_period=False)
    _add_encode_args(pl, include_period=False)
    pl.set_defaults(func=cmd_pipeline)

    return p


# ═════════════════════════════════════════════════════════════════
# 진입점
# ═════════════════════════════════════════════════════════════════

def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
