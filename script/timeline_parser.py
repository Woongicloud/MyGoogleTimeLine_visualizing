"""
Google Timeline Visualizer — Phase 1: 데이터 파싱 · 정제
파일: timeline_parser.py

지원 포맷: timelineEdits (신형)
처리 타입:
  - rawSignal.signal.position  → GPS 좌표 (핵심)
  - placeAggregates            → 장소 집계 (보조)
  - rawSignal.signal.wifiScan  → 스킵

대용량 처리: ijson 스트리밍 사용 (메모리 안전)
"""

import ijson
import sqlite3
import json
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Iterator
from math import radians, sin, cos, sqrt, atan2
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ──────────────────────────────────────────
# 상수 정의
# ──────────────────────────────────────────

class ActivityType(str, Enum):
    """속도 기반 교통수단 추론 (m/s 기준)"""
    STATIONARY  = "stationary"   # 0 ~ 0.5
    WALKING     = "walking"      # 0.5 ~ 1.5
    CYCLING     = "cycling"      # 1.5 ~ 5
    VEHICLE     = "vehicle"      # 5 ~ 20
    HIGHWAY     = "highway"      # 20 ~ 80
    FLIGHT      = "flight"       # 80+

SPEED_THRESHOLDS = [
    (0.5,  ActivityType.STATIONARY),
    (1.5,  ActivityType.WALKING),
    (5.0,  ActivityType.CYCLING),
    (20.0, ActivityType.VEHICLE),
    (80.0, ActivityType.HIGHWAY),
]

DB_PATH = Path("db/timeline.db")
ACCURACY_FILTER_MM   = 500_000  # 500m 이상 부정확한 포인트 제외
HAVERSINE_MAX_GAP_SEC = 300     # 5분 이상 이동 간격이면 속도 보간 스킵


# ──────────────────────────────────────────
# 데이터 모델
# ──────────────────────────────────────────

@dataclass
class GpsPoint:
    """파싱된 GPS 포인트 단일 레코드"""
    timestamp: str          # ISO 8601
    lat: float              # 위도 (E7 → float)
    lng: float              # 경도 (E7 → float)
    accuracy_mm: int
    altitude_m: float | None
    speed_ms: float | None  # 속도 (m/s)
    source: str             # GPS / NETWORK
    activity: str           # ActivityType 추론 결과
    year: int
    month: int


@dataclass
class PlaceAggregate:
    """장소 집계 레코드 (보조 데이터)"""
    place_id: str
    lat: float
    lng: float
    score: float
    window_start: str
    window_end: str


# ──────────────────────────────────────────
# 파싱 유틸리티
# ──────────────────────────────────────────

def e7_to_float(value: int) -> float:
    """E7 정수 좌표 → float 변환 (예: 376013151 → 37.6013151)"""
    return value / 1e7


def haversine_distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Haversine 공식으로 두 좌표 간 거리(m) 계산"""
    R  = 6_371_000
    p1 = radians(lat1); p2 = radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lng2 - lng1)
    a  = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def infer_activity(speed_ms: float | None) -> str:
    """속도(m/s) 기반 교통수단 추론"""
    if speed_ms is None:
        return ActivityType.STATIONARY
    for threshold, activity in SPEED_THRESHOLDS:
        if speed_ms < threshold:
            return activity
    return ActivityType.FLIGHT


def parse_timestamp(ts: str) -> tuple[int, int]:
    """ISO 타임스탬프에서 (year, month) 추출"""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt.year, dt.month


# ──────────────────────────────────────────
# 스트리밍 파서 (대용량 핵심)
# ──────────────────────────────────────────

def stream_gps_points(json_path: Path) -> Iterator[GpsPoint]:
    """
    ijson으로 timelineEdits 배열을 스트리밍 파싱.
    position 타입 레코드만 추출, 정확도 필터 적용.
    speedMetersPerSecond 없는 포인트는 Haversine으로 보간.
    메모리: O(1) — 파일 크기와 무관하게 일정.
    """
    prev: dict | None = None  # {lat, lng, ts_dt} — 이전 포인트 (속도 보간용)

    with open(json_path, "rb") as f:
        for entry in ijson.items(f, "timelineEdits.item", use_float=True):
            raw = entry.get("rawSignal", {}).get("signal", {})
            pos = raw.get("position")

            if pos is None:
                continue  # wifiScan, placeAggregates 등 스킵

            accuracy = int(pos.get("accuracyMm", 999_999_999))
            if accuracy > ACCURACY_FILTER_MM:
                log.debug("정확도 필터 제외: %d mm", accuracy)
                continue

            ts = pos.get("timestamp") or entry.get("rawSignal", {}).get("additionalTimestamp")
            if not ts:
                continue

            lat_e7 = pos.get("point", {}).get("latE7")
            lng_e7 = pos.get("point", {}).get("lngE7")
            if lat_e7 is None or lng_e7 is None:
                continue

            lat   = e7_to_float(lat_e7)
            lng   = e7_to_float(lng_e7)
            speed = pos.get("speedMetersPerSecond")
            ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))

            # speedMetersPerSecond 없으면 Haversine으로 보간
            if speed is None and prev is not None:
                dt_sec = (ts_dt - prev["ts_dt"]).total_seconds()
                if 0 < dt_sec <= HAVERSINE_MAX_GAP_SEC:
                    dist_m = haversine_distance_m(prev["lat"], prev["lng"], lat, lng)
                    speed  = dist_m / dt_sec

            yield GpsPoint(
                timestamp   = ts,
                lat         = lat,
                lng         = lng,
                accuracy_mm = accuracy,
                altitude_m  = pos.get("altitudeMeters"),
                speed_ms    = speed,
                source      = pos.get("source", "UNKNOWN"),
                activity    = infer_activity(speed),
                year        = ts_dt.year,
                month       = ts_dt.month,
            )

            prev = {"lat": lat, "lng": lng, "ts_dt": ts_dt}


def stream_place_aggregates(json_path: Path) -> Iterator[PlaceAggregate]:
    """placeAggregates 레코드 스트리밍 파싱 (장소명 보정 보조용)"""
    with open(json_path, "rb") as f:
        for entry in ijson.items(f, "timelineEdits.item", use_float=True):
            agg = entry.get("placeAggregates")
            if not agg:
                continue

            window = agg.get("processWindow", {})
            for info in agg.get("placeAggregateInfo", []):
                place_id = info.get("placeId")
                point = info.get("point", {})
                lat_e7 = point.get("latE7")
                lng_e7 = point.get("lngE7")
                if not place_id or lat_e7 is None:
                    continue

                yield PlaceAggregate(
                    place_id     = place_id,
                    lat          = e7_to_float(lat_e7),
                    lng          = e7_to_float(lng_e7),
                    score        = info.get("score", 0.0),
                    window_start = window.get("startTime", ""),
                    window_end   = window.get("endTime", ""),
                )


# ──────────────────────────────────────────
# SQLite 저장소
# ──────────────────────────────────────────

def init_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """DB 초기화 — 테이블 생성 + 인덱스. db 디렉터리 자동 생성."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")   # 대용량 쓰기 최적화
    conn.execute("PRAGMA synchronous=NORMAL")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS gps_points (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            lat         REAL NOT NULL,
            lng         REAL NOT NULL,
            accuracy_mm INTEGER,
            altitude_m  REAL,
            speed_ms    REAL,
            source      TEXT,
            activity    TEXT,
            year        INTEGER,
            month       INTEGER
        );

        CREATE TABLE IF NOT EXISTS place_aggregates (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            place_id     TEXT NOT NULL,
            lat          REAL NOT NULL,
            lng          REAL NOT NULL,
            score        REAL,
            window_start TEXT,
            window_end   TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_gps_year_month ON gps_points(year, month);
        CREATE INDEX IF NOT EXISTS idx_gps_activity   ON gps_points(activity);
        CREATE INDEX IF NOT EXISTS idx_place_id       ON place_aggregates(place_id);
    """)
    # timestamp 중복 삽입 방지 — 재파싱 시 기존 데이터 보존
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_gps_timestamp ON gps_points(timestamp)"
    )
    conn.commit()
    return conn


def bulk_insert_gps(conn: sqlite3.Connection, points: Iterator[GpsPoint], batch_size: int = 5000) -> int:
    """GPS 포인트 배치 삽입 — 대용량 성능 최적화. 이미 존재하는 timestamp는 건너뜀."""
    sql = """
        INSERT OR IGNORE INTO gps_points
            (timestamp, lat, lng, accuracy_mm, altitude_m, speed_ms, source, activity, year, month)
        VALUES
            (:timestamp, :lat, :lng, :accuracy_mm, :altitude_m, :speed_ms, :source, :activity, :year, :month)
    """
    batch = []
    total = 0

    for point in points:
        batch.append(asdict(point))
        if len(batch) >= batch_size:
            conn.executemany(sql, batch)
            conn.commit()
            total += len(batch)
            log.info("삽입 완료: %d건 누적", total)
            batch.clear()

    if batch:
        conn.executemany(sql, batch)
        conn.commit()
        total += len(batch)

    return total


def bulk_insert_places(conn: sqlite3.Connection, places: Iterator[PlaceAggregate]) -> int:
    """장소 집계 삽입"""
    sql = """
        INSERT INTO place_aggregates (place_id, lat, lng, score, window_start, window_end)
        VALUES (:place_id, :lat, :lng, :score, :window_start, :window_end)
    """
    rows = [asdict(p) for p in places]
    conn.executemany(sql, rows)
    conn.commit()
    return len(rows)


# ──────────────────────────────────────────
# 쿼리 헬퍼 (Phase 2 렌더링에서 사용)
# ──────────────────────────────────────────

def get_monthly_track(conn: sqlite3.Connection, year: int, month: int) -> list[dict]:
    """
    특정 월의 GPS 포인트를 시간순으로 반환.
    Phase 2 지도 렌더링에서 이 함수로 프레임 데이터를 가져옴.
    """
    cur = conn.execute(
        """
        SELECT timestamp, lat, lng, speed_ms, activity
        FROM gps_points
        WHERE year = ? AND month = ?
        ORDER BY timestamp ASC
        """,
        (year, month),
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_summary(conn: sqlite3.Connection) -> dict:
    """전체 데이터 요약 통계"""
    total    = conn.execute("SELECT COUNT(*) FROM gps_points").fetchone()[0]
    months   = conn.execute(
        "SELECT year, month, COUNT(*) as cnt FROM gps_points GROUP BY year, month ORDER BY year, month"
    ).fetchall()
    by_activity = conn.execute(
        "SELECT activity, COUNT(*) FROM gps_points GROUP BY activity"
    ).fetchall()

    return {
        "total_points"  : total,
        "months"        : [{"year": r[0], "month": r[1], "count": r[2]} for r in months],
        "by_activity"   : dict(by_activity),
    }


# ──────────────────────────────────────────
# 메인 실행
# ──────────────────────────────────────────

def run(json_path: str | Path, db_path: str | Path = DB_PATH) -> None:
    json_path = Path(json_path)
    if not json_path.exists():
        raise FileNotFoundError(f"파일 없음: {json_path}")

    log.info("DB 초기화 중... (%s)", db_path)
    conn = init_db(Path(db_path))

    log.info("GPS 포인트 파싱 시작: %s", json_path)
    gps_total = bulk_insert_gps(conn, stream_gps_points(json_path))
    log.info("GPS 포인트 저장 완료: %d건", gps_total)

    log.info("장소 집계 파싱 시작...")
    place_total = bulk_insert_places(conn, stream_place_aggregates(json_path))
    log.info("장소 집계 저장 완료: %d건", place_total)

    summary = get_summary(conn)
    log.info("=== 파싱 완료 요약 ===")
    log.info("총 GPS 포인트: %d", summary["total_points"])
    log.info("월별 분포:")
    for m in summary["months"]:
        log.info("  %d-%02d: %d건", m["year"], m["month"], m["count"])
    log.info("교통수단 분포: %s", summary["by_activity"])

    conn.close()


if __name__ == "__main__":
    import sys
    import argparse

    ap = argparse.ArgumentParser(description="Google Timeline 파서 (Phase 1)")
    ap.add_argument("json_path", nargs="?", help="타임라인 JSON 파일 경로")
    ap.add_argument("--summary", action="store_true", help="DB 요약 통계 출력 (파싱 없이)")
    args = ap.parse_args()

    if args.summary:
        conn    = init_db()
        summary = get_summary(conn)
        print(f"\n총 GPS 포인트: {summary['total_points']:,}건")
        print("──────────────── 월별 분포 ────────────────")
        for m in summary["months"]:
            print(f"  {m['year']}-{m['month']:02d}: {m['count']:,}건")
        print("──────────── 교통수단 분포 ────────────────")
        for act, cnt in sorted(summary["by_activity"].items()):
            print(f"  {act:<12}: {cnt:,}건")
        conn.close()
    elif args.json_path:
        run(args.json_path)
    else:
        ap.print_help()
        sys.exit(1)