# MyGoogleTimeLine Visualizer

Google Takeout 타임라인 JSON을 파싱해 월별 이동 동선을 지도 위에 애니메이션으로 시각화하고 MP4·GIF로 출력하는 도구입니다.

```
[Phase 1] JSON 파싱 → SQLite DB
[Phase 2] GPS 트랙 → PNG 프레임 시퀀스 (World Canvas + 동적 카메라)
[Phase 3] PNG 시퀀스 → MP4 / GIF
```

통합 진입점 [`main.py`](main.py)로 전체 파이프라인을 한 번에 실행하거나 각 단계를 개별 실행할 수 있습니다.

---

## 설치

**요구사항:** Python 3.11+

```bash
git clone <repo-url>
cd MyGoogleTimeLine_visualizing

# 전체 설치 (Phase 1 + 2 + 3)
pip install -r requirement/base.txt
```

### Phase별 최소 설치

```bash
pip install -r requirement/phase1-parse.txt    # 파싱만 (ijson)
pip install -r requirement/phase2-render.txt   # 렌더링만 (Pillow, staticmap, ...)
pip install -r requirement/phase3-encode.txt   # 인코딩만 (imageio-ffmpeg)
```

루트의 `requirements.txt` 도 동일하게 작동합니다 (`requirement/base.txt`로 위임).

---

## 빠른 시작 — 통합 main.py

### 한 번에 실행 (전체 파이프라인)

```bash
# 신규 (DB 없을 때): 파싱 → 렌더링 → 인코딩 자동 실행
python main.py pipeline 2026-03 --json "data/Timeline Edits.json"

# DB 있을 때: 렌더링 + 인코딩만
python main.py pipeline 2026-03

# PNG까지만 (인코딩 생략)
python main.py pipeline 2026-03 --no-encode
```

### 단계별 실행

```bash
# Phase 1 — JSON → SQLite
python main.py parse "data/Timeline Edits.json"

# DB 요약 통계
python main.py summary

# Phase 2 — PNG 프레임 시퀀스
python main.py render 2026-03

# 좌표 정확도 검증 (9개 기준점 십자가)
python main.py render 2026-03 --verify

# Phase 3 — MP4 + GIF 인코딩
python main.py encode 2026-03
```

### 도움말

```bash
python main.py --help
python main.py <subcommand> --help    # 각 서브커맨드 상세 옵션
```

출력: `output/<period>/frames/frame_*.png` + `<period>.mp4` + `<period>.gif`

---

## 추가 옵션 예시

```bash
# 다른 지도 공급자
python main.py render 2026-03 --map cartodb-light

# 영상 길이·fps 조정
python main.py render 2026-03 --duration 30 --fps 24

# 인코딩 품질 / GIF 크기 조정
python main.py encode 2026-03 --crf 23 --preset fast --gif-scale 640

# 특정 날짜 범위
python main.py render 2025-12-20~2025-12-31

---

## 지도 공급자 가이드

### 무료 — API 키 불필요

| 이름 | `--map` 값 | 배경 스타일 | 특징 |
|------|-----------|------------|------|
| CartoDB Voyager | `cartodb-voyager` | 컬러 (기본값) | 도로·지명 선명, GPS 점과 대비 좋음 |
| CartoDB Light | `cartodb-light` | 밝은 회색 | 깔끔, 낮 시간대 데이터에 적합 |
| CartoDB Dark | `cartodb-dark` | 어두운 검정 | 네온 스타일, 야간 데이터 강조 |
| OpenStreetMap | `osm` | 표준 컬러 | 상세 지명, 데이터 풍부 |

```bash
# 예시
python script/frame_renderer.py 2025-12 --map cartodb-light
python script/frame_renderer.py 2025-12 --map osm
```

---

### Stadia Maps (무료 — 회원가입 필요)

Stadia Maps는 무료 플랜으로 월 200,000 타일 요청을 제공합니다.

**가입 절차:**

1. [client.stadiamaps.com](https://client.stadiamaps.com/signup/) 에서 계정 생성
2. 로그인 후 **Manage Properties** → **Add Property** (도메인/앱 이름 입력)
3. Property 생성 후 **API Keys** 탭 → **Create API Key**
4. 생성된 키를 복사

**설정:**
```bash
# .env 파일에 추가
STADIA_API_KEY=your_api_key_here
```

| 이름 | `--map` 값 | 스타일 |
|------|-----------|--------|
| Stadia Dark | `stadia-dark` | 부드러운 다크 테마 |
| Stadia Light | `stadia-light` | 부드러운 라이트 테마 |

> **참고:** Stadia API 키 없이 `stadia-dark`를 사용하면 일부 지역에서 타일 로드가 제한될 수 있습니다.

---

### Mapbox (월 50,000 요청 무료)

Mapbox는 고품질 벡터 지도를 제공하며 다양한 스타일을 지원합니다.

**가입 절차:**

1. [account.mapbox.com](https://account.mapbox.com/auth/signup/) 에서 계정 생성
2. 대시보드 → **Tokens** → **Create a token**
3. Scopes에서 `styles:tiles` 체크 → **Create token**
4. 생성된 토큰(`pk.eyJ1...`)을 복사

**설정:**
```bash
# .env 파일에 추가
MAPBOX_TOKEN=pk.eyJ1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

```bash
# 사용법 (기본 스타일: mapbox/dark-v11)
python script/frame_renderer.py 2025-12 --map mapbox

# 다른 Mapbox 스타일 사용 (render_config.yml에서 custom_url에 스타일 ID 입력)
# custom_url: "mapbox/streets-v12"
```

**Mapbox 주요 스타일 ID:**

| 스타일 | ID |
|--------|-----|
| Dark (기본) | `mapbox/dark-v11` |
| Streets | `mapbox/streets-v12` |
| Outdoors | `mapbox/outdoors-v12` |
| Satellite | `mapbox/satellite-v9` |
| Light | `mapbox/light-v11` |

---

### 커스텀 XYZ 타일

표준 XYZ 포맷(`{z}/{x}/{y}`)을 지원하는 어떤 타일 서버든 사용 가능합니다:

```bash
python script/frame_renderer.py 2025-12 \
  --map "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
```

---

## 설정 파일 (`render_config.yml`)

프로젝트 루트의 `render_config.yml`에서 모든 시각화 옵션을 조정할 수 있습니다.

### 전체 옵션 표

#### `map` 섹션

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `provider` | string | `cartodb-voyager` | 타일 공급자 이름 |
| `custom_url` | string | `""` | 커스텀 XYZ URL (provider=custom 일 때) |
| `mapbox_token` | string | `""` | Mapbox 토큰 (.env 우선) |
| `stadia_api_key` | string | `""` | Stadia API 키 (.env 우선) |

#### `canvas` 섹션

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `width` | int | `1280` | 출력 가로 픽셀 |
| `height` | int | `720` | 출력 세로 픽셀 |
| `bbox_padding` | float | `0.05` | GPS 범위 여백 비율 |

#### `trail` 섹션

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `length` | int | `300` | 화면 표시 최대 이전 포인트 수 |
| `alpha_min` | int | `60` | 가장 오래된 점 투명도 (0~255) |
| `alpha_max` | int | `255` | 현재 위치 점 투명도 |
| `current_point_scale` | int | `2` | 현재 위치 점 크기 배율 |
| `outline_color` | list | `[255,255,255]` | 현재 위치 외곽선 색 (RGB) |
| `outline_alpha` | int | `200` | 외곽선 투명도 |

#### `colors` 섹션 (RGB)

| 교통수단 | 기본 색 | hex |
|---------|---------|-----|
| `stationary` | `[100, 100, 100]` | #646464 (회색) |
| `walking` | `[34, 197, 94]` | #22c55e (초록) |
| `cycling` | `[59, 130, 246]` | #3b82f6 (파랑) |
| `vehicle` | `[249, 115, 22]` | #f97316 (주황) |
| `highway` | `[239, 68, 68]` | #ef4444 (빨강) |
| `flight` | `[168, 85, 247]` | #a855f7 (보라) |

#### `radius` 섹션 (픽셀)

| 교통수단 | 기본 반경 |
|---------|---------|
| `stationary` | 2 |
| `walking` | 3 |
| `cycling` / `vehicle` | 4 |
| `highway` | 5 |
| `flight` | 6 |

#### `hud` 섹션

상단 HUD(날짜·시각 + 네비게이션 바)의 외관과 표시 내용을 제어합니다.

**기본 설정**

| 키 | 타입 | 기본값 | CLI 플래그 | 설명 |
|----|------|--------|-----------|------|
| `enabled` | bool | `true` | `--no-hud` | HUD 전체 표시 여부 |
| `banner_height` | int | `38` | — | 상단 배너 높이 (px) |
| `banner_alpha` | int | `150` | `--hud-alpha N` | 배너 배경 투명도 (0~255) |
| `banner_color` | list | `[0,0,0]` | `--hud-color R,G,B` | 배너 배경 RGB 색상 |
| `font_size` | int | `20` | — | 텍스트 크기 (pt) |
| `font_path` | string | `""` | — | 커스텀 폰트 파일 절대 경로 |

**날짜·시각 표시 제어**

| 키 | 타입 | 기본값 | CLI 플래그 | 설명 |
|----|------|--------|-----------|------|
| `show_date` | bool | `true` | `--hud-date` / `--no-hud-date` | 날짜(YYYY-MM-DD) 표시 |
| `show_time` | bool | `true` | `--hud-time` / `--no-hud-time` | 시각(HH:MM:SS) 표시 |
| `show_seconds` | bool | `true` | `--hud-seconds` / `--no-hud-seconds` | 초(:SS) 표시 (`show_time=true`일 때) |
| `text_color` | list | `[255,255,255]` | `--hud-text-color R,G,B` | 텍스트 RGB 색상 |
| `text_alpha` | int | `255` | `--hud-text-alpha N` | 텍스트 투명도 (0~255) |

**네비게이션 바(진행 바)**

| 키 | 타입 | 기본값 | CLI 플래그 | 설명 |
|----|------|--------|-----------|------|
| `progress_bar.enabled` | bool | `true` | `--no-navbar` | 진행 바 표시 여부 |
| `progress_bar.color` | list | `[99,202,255]` | — | 진행 바 채움 색 (RGB) |
| `progress_bar.alpha` | int | `200` | — | 진행 바 투명도 |
| `progress_bar.outline_color` | list | `[180,180,180]` | — | 진행 바 테두리 색 |
| `progress_bar.width` | int | `200` | — | 진행 바 너비 (px) |
| `progress_bar.height` | int | `16` | — | 진행 바 높이 (px) |

#### `render` 섹션

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `duration_sec` | int | `60` | 기본 출력 영상 길이 (초) |
| `fps` | int | `30` | 기본 프레임 레이트 |

#### `compress_stationary` 섹션

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | `false` | 정지 포인트 공간 압축 활성화 |
| `radius_m` | float | `100.0` | 클러스터 반경(m) — 이내 연속 정지점 → 1개로 압축 |
| `keep` | string | `"median"` | 대표 포인트 선택: `first` / `median` / `last` |

#### `playback` 섹션

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `speed` | float | `1.0` | 영상 속도 배율 (2.0 = 2배 빠름) |
| `realtime_speed` | string | `"0"` | 영상 1초당 실제 시간 (예: `"1h"`) |
| `time_step` | string | `"0"` | 시간 그리드 다운샘플링 (예: `"60s"`) |

---

## CLI 레퍼런스

> **적용 우선순위:** CLI 플래그 > `render_config.yml` > 내장 기본값  
> **API 키:** `.env` 파일 > `render_config.yml`

```bash
python main.py render <period> [옵션]
python main.py pipeline <period> [--json FILE] [옵션]
```

### 기본 렌더링 옵션

| 플래그 | 형식 | 기본값 | 설명 |
|--------|------|--------|------|
| `--map` | `PROVIDER` | `cartodb-voyager` | 지도 공급자 또는 커스텀 XYZ URL |
| `--config` | `FILE` | `render_config.yml` | 설정 파일 경로 |
| `--duration` | `N` | `60` | 영상 길이(초) |
| `--fps` | `N` | `30` | 프레임 레이트 |
| `--trail` | `N` | `300` | 잔상 최대 점 개수 (개수 기준) |
| `--trail-time` | `DUR` | — | 잔상 시간 기준 (예: `5m`, `300s`) |
| `--speed` | `N` | `1.0` | 영상 속도 배율 |
| `--realtime-speed` | `DUR` | — | 영상 1초당 실제 시간 (예: `1h`) |
| `--time-step` | `DUR` | — | 시간 그리드 다운샘플링 (예: `60s`) |

### 정지 포인트 압축 옵션

| 플래그 | 형식 | 기본값 | 설명 |
|--------|------|--------|------|
| `--compress-stationary` | flag | off | 정지 포인트 공간 압축 활성화 |
| `--compress-radius` | `M` | `100` | 압축 반경(m) |

### HUD 옵션

| 플래그 | 형식 | 기본값 | 설명 |
|--------|------|--------|------|
| `--no-hud` | flag | — | HUD 전체 숨김 |
| `--hud-date` / `--no-hud-date` | bool | 표시 | 날짜(YYYY-MM-DD) 표시 |
| `--hud-time` / `--no-hud-time` | bool | 표시 | 시각(HH:MM:SS) 표시 |
| `--hud-seconds` / `--no-hud-seconds` | bool | 표시 | 초(:SS) 표시 |
| `--no-navbar` | flag | — | 네비게이션 바(진행 바) 숨김 |
| `--hud-alpha` | `N` | `150` | 배너 배경 투명도 (0~255) |
| `--hud-color` | `R,G,B` | `0,0,0` | 배너 배경 색상 |
| `--hud-text-color` | `R,G,B` | `255,255,255` | 텍스트 색상 |
| `--hud-text-alpha` | `N` | `255` | 텍스트 투명도 (0~255) |

### HUD 사용 예시

```bash
# HUD 전체 제거
python main.py render 2026-03 --no-hud

# 날짜만 표시 (시각 숨김)
python main.py render 2026-03 --no-hud-time

# 시분만 표시 (초 숨김)
python main.py render 2026-03 --no-hud-seconds

# 네비게이션 바 없이 날짜·시각만
python main.py render 2026-03 --no-navbar

# 반투명 남색 배너 + 노란 텍스트
python main.py render 2026-03 \
  --hud-color "20,20,60" --hud-alpha 180 \
  --hud-text-color "255,220,80"

# 완전 투명 배너 (텍스트만 지도 위에 떠 있는 효과)
python main.py render 2026-03 --hud-alpha 0

# 시각 없이 날짜만 + 네비게이션 바 유지
python main.py render 2026-03 --no-hud-time
```

---

## 커스터마이징 예시

### 밝은 지도 + 빨간 동선

```yaml
# render_config.yml
map:
  provider: cartodb-light

colors:
  walking:  [220, 50,  50]
  vehicle:  [180, 30,  30]
  highway:  [120, 20,  20]
```

### 야간 강조 (짧은 잔상)

```yaml
map:
  provider: cartodb-dark

trail:
  length: 100
  alpha_min: 20
```

### HUD 비활성화

```yaml
# render_config.yml
hud:
  enabled: false
```

또는 CLI:

```bash
python main.py render 2026-03 --no-hud
```

### HUD 날짜만 표시 (시각 숨김)

```bash
python main.py render 2026-03 --no-hud-time
```

또는 `render_config.yml`:

```yaml
hud:
  show_time: false
```

### 반투명 남색 배너 + 노란 텍스트

```bash
python main.py render 2026-03 \
  --hud-color "20,20,60" --hud-alpha 180 \
  --hud-text-color "255,220,80"
```

---

## 출력 구조

```
output/
  2025-12/
    tile_cartodb-voyager.png   # 배경 타일 캐시 (공급자별 자동 구분)
    frames/
      frame_000000.png
      frame_000001.png
      ...
      frame_001799.png         # 60초 × 30fps = 1,800장
```

---

## Phase 3 예고 — FFmpeg 인코딩

Phase 3 완성 전 수동으로 인코딩하려면:

```bash
# MP4
ffmpeg -r 30 -i output/2025-12/frames/frame_%06d.png \
  -c:v libx264 -pix_fmt yuv420p -crf 18 output/2025-12.mp4

# GIF (팔레트 최적화)
ffmpeg -r 30 -i output/2025-12/frames/frame_%06d.png \
  -vf "fps=15,scale=960:-1:flags=lanczos,palettegen" palette.png
ffmpeg -r 30 -i output/2025-12/frames/frame_%06d.png \
  -i palette.png -filter_complex "fps=15,scale=960:-1:flags=lanczos[x];[x][1:v]paletteuse" \
  output/2025-12.gif
```

---

## 트러블슈팅

### "해당 기간의 데이터가 없습니다"

```bash
python script/timeline_parser.py --summary
```
파싱된 월 목록을 확인 후 해당 기간으로 렌더링하세요.

### 타일이 단색(#1e1e1e)으로 표시됨

네트워크 연결 확인 후 다른 공급자로 전환:
```bash
python script/frame_renderer.py 2025-12 --map osm
```

### Mapbox 401 오류

`.env` 파일에 `MAPBOX_TOKEN`이 올바르게 설정되었는지 확인:
```bash
python -c "import os; from dotenv import load_dotenv; load_dotenv(); print(os.getenv('MAPBOX_TOKEN', 'NOT SET'))"
```

### 지도 배경이 캐시된 이전 공급자로 표시됨

`output/<period>/tile_*.png` 파일들을 삭제하면 다음 실행 시 재다운로드됩니다.
공급자를 바꾸면 `tile_<provider이름>.png`로 자동 구분되므로 일반적으로 문제없습니다.
