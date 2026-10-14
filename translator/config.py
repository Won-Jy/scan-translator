"""전체 설정. 환경변수로 덮어쓸 수 있습니다."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── 모델 ─────────────────────────────────────────────
MODEL = os.environ.get("SCANTR_MODEL", "claude-sonnet-5-5")

# 동시에 처리할 페이지 수 (API 속도 제한에 걸리면 2~3으로 낮추세요)
WORKERS = int(os.environ.get("SCANTR_WORKERS", "4"))

# 페이지 이미지 — Claude 비전에 최적인 긴 변 픽셀 수
IMAGE_LONG_EDGE = 1568
JPEG_QUALITY = 85

# ── 기본 용어집 ──────────────────────────────────────
# Drive의 Reading/glossary.txt 가 있으면 그 내용이 이 값보다 우선합니다.
DEFAULT_GLOSSARY = {
    "soin": "돌봄",
    "humanisme": "인간주의",
    "irremplaçabilité": "대체불가능성",
    "capacitaire": "역량적",
    "chaire": "석좌",
}

# ── PDF 조판 ─────────────────────────────────────────
FONT_DIR = Path(os.environ.get("SCANTR_FONT_DIR", ROOT / "fonts"))
FONT_REGULAR = "NanumMyeongjo-Regular.ttf"
FONT_BOLD = "NanumMyeongjo-Bold.ttf"
FONT_URLS = {
    FONT_REGULAR: "https://raw.githubusercontent.com/google/fonts/main/ofl/nanummyeongjo/NanumMyeongjo-Regular.ttf",
    FONT_BOLD: "https://raw.githubusercontent.com/google/fonts/main/ofl/nanummyeongjo/NanumMyeongjo-Bold.ttf",
}

PAGE_WIDTH = 420           # pt (A5 폭과 비슷). 높이는 원문 비율을 따름
MARGIN_X = 34
MARGIN_TOP = 38
MARGIN_BOTTOM = 40
BASE_FONT_PT = 10.5
LINE_HEIGHT = 1.68
MIN_SCALE = 0.62           # 글이 넘칠 때 이 배율까지 글자를 줄임 (그래도 넘치면 더 줄임)
