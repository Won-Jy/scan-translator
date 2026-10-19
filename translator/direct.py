"""보조 경로: 페이지 이미지 → 한국어 번역 (원문 전사 없이).

기본 경로(전사 → 번역)에서 원문 전사 출력이 API 출력 필터에 걸린 페이지,
또는 번역 출력이 걸린 페이지만 이 경로로 처리한다. 여기서는 원문을 옮겨 적지 않고
페이지 이미지를 보고 곧바로 한국어 번역만 받는다. 용어집·번역 원칙은 기본 경로와 같다.
"""
import base64

from .llm import call_tool
from .transcribe import BLOCK_TYPES, PAGE_KINDS
from .translate import STYLE

TOOL = {
    "name": "record_translated_page",
    "description": "스캔된 책 한 페이지를 한국어로 번역해 구조화해서 기록한다.",
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
                "description": "페이지 첫 블록이 이전 페이지 문단의 연속인가.",
            },
            "has_figure": {"type": "boolean", "description": "그림·사진·도표가 있는가."},
            "blocks": {
                "type": "array",
                "description": "읽는 순서대로의 블록. text는 한국어 번역. 각주(footnote)는 맨 뒤에.",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": BLOCK_TYPES},
                        "level": {"type": "integer", "description": "heading일 때 1(장)~3(소절)."},
                        "marker": {"type": "string", "description": "footnote일 때 각주 번호/기호."},
                        "text": {"type": "string", "description": "한국어 번역문"},
                    },
                    "required": ["type", "text"],
                },
            },
        },
        "required": ["page_kind", "printed_page_number", "continues_paragraph", "has_figure", "blocks"],
    },
}

TASK = """

[이 작업의 방식]
이번에는 원문 블록 대신 스캔된 책 페이지 이미지를 직접 받습니다. 페이지에 인쇄된 텍스트를 읽고, 그 한국어 번역만 record_translated_page로 기록하세요. 원문을 옮겨 적지 말고 번역문만 내세요 (서지 정보·인용된 원어 병기처럼 위 원칙이 원어를 남기라고 한 경우만 예외).

[손글씨·독자 표시]
책 소유자가 종이에 직접 필기를 했을 수 있습니다. 손글씨 메모, 손으로 쓴 번역어·화살표·기호, 밑줄·형광펜·동그라미 등은 모두 무시하고 인쇄된 텍스트만 번역하세요. 밑줄 아래의 인쇄 글자는 정상적으로 번역합니다.

[제외할 것]
러닝헤더(매 페이지 위에 반복되는 책 제목·장 제목·저자명)와 쪽번호는 blocks에 넣지 말 것 (쪽번호는 printed_page_number에만). 손가락, 스캔 배경, 옆 페이지 일부도 제외.

[블록 구성]
- 문단 하나 = 블록 하나. 제목은 heading (장 1, 절 2, 소절 3), 들여쓴 긴 인용문은 blockquote, 장 첫머리 인용구는 epigraph, 그림 캡션은 caption, 목차·참고문헌 항목은 list_item, 표는 한 행씩 table_row (셀은 " | "로 구분).
- 페이지 아래 각주는 footnote 블록으로, 번호는 marker에 넣고 text에는 넣지 말 것. 각주는 맨 뒤에.

[페이지 경계]
- 페이지가 앞 페이지 문장의 중간에서 시작하면, 그 조각을 자연스럽게 번역하되 맨 앞에 '…'를 붙이세요.
- 본문 마지막 문장이 다음 페이지로 이어지면, 이 페이지에 있는 부분까지만 번역하고 끝에 '…'를 붙이세요."""


def translate_page_image(glossary_prompt: str, jpeg: bytes, page_no: int, page_count: int,
                         prev_translation: str = "") -> dict:
    """페이지 이미지 → 번역된 페이지 dict (render가 쓰는 pages 형식, blocks의 text가 한국어)."""
    system = STYLE.replace("record_translation 도구로 블록별 번역을", "번역을") + glossary_prompt + TASK
    text = f"스캔 {page_no}/{page_count}쪽입니다. 인쇄 텍스트를 한국어로 번역해 기록하세요."
    if prev_translation:
        text = (f"[앞 페이지 번역의 끝부분 — 흐름 파악용, 다시 쓰지 말 것]\n{prev_translation}\n\n" + text)
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                     "data": base64.standard_b64encode(jpeg).decode()}},
        {"type": "text", "text": text},
    ]
    d = call_tool(system=system, content=content, tool=TOOL, max_tokens=12000)

    blocks = []
    for b in d.get("blocks") or []:
        t = (b.get("text") or "").strip()
        if not t:
            continue
        typ = b.get("type") if b.get("type") in BLOCK_TYPES else "paragraph"
        nb = {"type": typ, "text": t}
        if typ == "heading":
            nb["level"] = min(3, max(1, int(b.get("level") or 2)))
        if typ == "footnote":
            nb["marker"] = str(b.get("marker") or "").strip()
        blocks.append(nb)
    kind = d.get("page_kind") if d.get("page_kind") in PAGE_KINDS else "body"
    if not blocks and kind != "figure":
        kind = "blank"
    return {
        "page_kind": kind,
        "printed_page_number": str(d.get("printed_page_number") or "").strip(),
        "continues_paragraph": bool(d.get("continues_paragraph")),
        "starts_mid_sentence": False,
        "carryover_text": "",
        "ends_mid_sentence": False,
        "has_figure": bool(d.get("has_figure")),
        "blocks": blocks,
        "direct": True,
    }
