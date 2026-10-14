"""1단계: 페이지 이미지 → 인쇄 텍스트 전사 (손글씨·밑줄 제거).

Tesseract 대신 Claude 비전이 페이지를 직접 읽는다.
각 페이지는 독립적이라 병렬 처리된다.
"""
import base64

import pymupdf as fitz

from . import config
from .llm import call_tool

PAGE_KINDS = [
    "body",             # 일반 본문
    "chapter_opening",  # 장/절이 시작되는 페이지 (큰 제목 + 본문 일부일 수 있음)
    "toc",              # 목차
    "front_matter",     # 표제지, 판권, 헌사 등
    "notes",            # 책 뒤쪽 미주 모음
    "bibliography",     # 참고문헌
    "index",            # 색인
    "figure",           # 그림·사진·도표가 주이고 글은 캡션 정도
    "blank",            # 빈 페이지 (또는 의미 있는 글자가 없음)
]

BLOCK_TYPES = ["heading", "paragraph", "blockquote", "list_item", "epigraph",
               "caption", "table_row", "footnote", "other"]

TOOL = {
    "name": "record_page",
    "description": "스캔된 책 한 페이지의 인쇄 텍스트를 구조화해서 기록한다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "page_kind": {"type": "string", "enum": PAGE_KINDS},
            "printed_page_number": {
                "type": "string",
                "description": "페이지에 인쇄된 쪽번호 (예: '147', 'xii'). 없으면 빈 문자열.",
            },
            "continues_paragraph": {
                "type": "boolean",
                "description": "페이지 첫 블록이 이전 페이지 문단의 연속인가 (들여쓰기 없이 시작 등).",
            },
            "starts_mid_sentence": {
                "type": "boolean",
                "description": "페이지가 이전 페이지 문장의 중간에서 시작하는가 (소문자로 시작 등).",
            },
            "carryover_text": {
                "type": "string",
                "description": "starts_mid_sentence일 때: 페이지 맨 앞에서부터 그 문장이 끝나는 "
                               "종결부호(. ? ! 등)까지의 텍스트를 원문 그대로. 아니면 빈 문자열.",
            },
            "ends_mid_sentence": {
                "type": "boolean",
                "description": "본문 마지막 문장이 다음 페이지로 이어지며 이 페이지에서 끝나지 않는가.",
            },
            "has_figure": {"type": "boolean", "description": "그림·사진·도표가 있는가."},
            "blocks": {
                "type": "array",
                "description": "읽는 순서대로의 텍스트 블록. 각주(footnote)는 맨 뒤에.",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": BLOCK_TYPES},
                        "level": {"type": "integer", "description": "heading일 때 1(장)~3(소절)."},
                        "marker": {"type": "string", "description": "footnote일 때 각주 번호/기호."},
                        "text": {"type": "string"},
                    },
                    "required": ["type", "text"],
                },
            },
        },
        "required": ["page_kind", "printed_page_number", "continues_paragraph",
                     "starts_mid_sentence", "carryover_text", "ends_mid_sentence",
                     "has_figure", "blocks"],
    },
}

SYSTEM = """당신은 학술서 스캔본을 전사하는 전문가입니다. 휴대폰 스캔 앱(vFlat)으로 찍은 책 페이지 이미지를 받아, 인쇄된 텍스트만 원문 그대로 정확하게 옮겨 record_page 도구로 기록합니다. 번역하지 마세요.

[손글씨·독자 표시 — 가장 중요]
이 책의 소유자가 종이에 직접 필기를 했습니다. 다음은 모두 완전히 무시하고, 전사 결과에 어떤 흔적도 남기지 마세요:
- 손글씨 메모(여백, 행간, 페이지 위아래), 손으로 쓴 번역어·화살표·물음표·느낌표
- 밑줄, 형광펜, 동그라미, 괄호 표시, 세로줄, 별표, 체크 표시
밑줄·형광펜 아래의 인쇄 글자는 평소처럼 정상 전사합니다. 손글씨가 인쇄 글자를 일부 가리면 문맥상 가장 그럴듯한 원문으로 복원하세요.
인쇄된 텍스트와 손글씨는 서체(활자 vs 필기), 색(검정 인쇄 vs 연필·볼펜·컬러펜), 정렬로 구분할 수 있습니다.

[제외할 것]
- 러닝헤더(매 페이지 위에 반복되는 책 제목·장 제목·저자명)와 쪽번호 → blocks에 넣지 말 것 (쪽번호는 printed_page_number에만)
- 손가락, 스캔 배경, 옆 페이지가 일부 찍힌 부분의 글자
- 판권 페이지의 바코드·인증 마크 등 장식

[전사 규칙]
- 문단 하나 = 블록 하나. 인쇄상의 줄바꿈은 없애고 이어 쓰기. 줄끝 하이픈으로 나뉜 단어는 붙이기 (원래 하이픈이 있는 복합어는 유지).
- 철자·악센트·구두점(« », —, … 등)은 원문 그대로. 오타를 고치지 말 것.
- 서식은 다음 태그만 사용: 이탤릭 <i>…</i>, 볼드 <b>…</b>, 본문 속 각주 번호 <sup>3</sup>. 그 밖의 HTML·마크다운 금지.
- 제목·소제목은 heading (장 제목 level 1, 절 2, 소절 3). 전부 대문자로 인쇄된 제목은 원래 대소문자 그대로 옮기기.
- 들여쓴 긴 인용문은 blockquote, 장 첫머리 인용구는 epigraph.
- 페이지 아래 각주는 footnote 블록으로, 번호는 marker에 넣고 text에는 넣지 말 것. 각주는 blocks 맨 뒤에.
- 그림 캡션은 caption, 표는 한 행씩 table_row (셀은 " | "로 구분).
- 목차는 항목마다 list_item ("제목 …… 쪽번호" 형태 유지).
- 참고문헌·색인 페이지도 항목별 list_item으로 전사.

[페이지 경계]
- starts_mid_sentence: 첫 줄이 앞 페이지 문장의 중간부터 시작하면 true. 이때 carryover_text에 페이지 맨 앞부터 그 문장의 종결부호까지를 원문 그대로 적기 (첫 블록 text의 앞부분과 정확히 일치해야 함). 그 문장이 페이지 끝까지 끝나지 않으면 carryover_text는 빈 문자열.
- continues_paragraph: 첫 블록이 앞 페이지 문단의 연속이면 true (들여쓰기 없음).
- ends_mid_sentence: 본문의 마지막 문장이 이 페이지에서 끝나지 않고 다음 페이지로 넘어가면 true (각주는 판단에서 제외)."""


def render_page_jpeg(page: fitz.Page) -> bytes:
    long_edge_pt = max(page.rect.width, page.rect.height)
    zoom = config.IMAGE_LONG_EDGE / long_edge_pt
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csRGB, alpha=False)
    return pix.tobytes("jpeg", jpg_quality=config.JPEG_QUALITY)


def transcribe_page(jpeg: bytes, page_no: int, page_count: int) -> dict:
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                     "data": base64.standard_b64encode(jpeg).decode()}},
        {"type": "text", "text": f"스캔 {page_no}/{page_count}쪽입니다. 인쇄 텍스트만 record_page로 기록하세요."},
    ]
    data = call_tool(system=SYSTEM, content=content, tool=TOOL, max_tokens=8000)
    return normalize(data)


def normalize(d: dict) -> dict:
    """모델 출력의 빈 값·이상값을 정리."""
    blocks = []
    for b in d.get("blocks") or []:
        text = (b.get("text") or "").strip()
        if not text:
            continue
        t = b.get("type") if b.get("type") in BLOCK_TYPES else "paragraph"
        nb = {"type": t, "text": text}
        if t == "heading":
            nb["level"] = min(3, max(1, int(b.get("level") or 2)))
        if t == "footnote":
            nb["marker"] = str(b.get("marker") or "").strip()
        blocks.append(nb)
    kind = d.get("page_kind") if d.get("page_kind") in PAGE_KINDS else "body"
    if not blocks and kind not in ("figure",):
        kind = "blank"
    return {
        "page_kind": kind,
        "printed_page_number": str(d.get("printed_page_number") or "").strip(),
        "continues_paragraph": bool(d.get("continues_paragraph")),
        "starts_mid_sentence": bool(d.get("starts_mid_sentence")),
        "carryover_text": (d.get("carryover_text") or "").strip(),
        "ends_mid_sentence": bool(d.get("ends_mid_sentence")),
        "has_figure": bool(d.get("has_figure")),
        "blocks": blocks,
    }


def page_plain_text(p: dict) -> str:
    return "\n\n".join(b["text"] for b in p.get("blocks", []))
