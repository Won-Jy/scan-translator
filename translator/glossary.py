"""2단계: 책 전체 원문을 한 번 훑어 책 정보와 용어집을 만든다.

모든 페이지 번역이 같은 용어집·표기 기준을 공유하므로
페이지를 병렬로 번역해도 용어가 흔들리지 않는다.
"""
import re

from .llm import call_tool
from .transcribe import page_plain_text

MAX_CHARS = 350_000  # 아주 두꺼운 스캔이면 앞부분 위주로 샘플링

TOOL = {
    "name": "record_glossary",
    "description": "책의 기본 정보, 번역 지침, 용어집을 기록한다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "원제 (알 수 없으면 빈 문자열)"},
            "author": {"type": "string", "description": "저자 (알 수 없으면 빈 문자열)"},
            "source_language": {"type": "string", "description": "주 원문 언어 (예: 프랑스어)"},
            "field": {"type": "string", "description": "분야 (예: 돌봄 윤리, 현대 미학)"},
            "summary": {"type": "string", "description": "이 텍스트의 내용과 논지를 한국어 3~5문장으로"},
            "style_notes": {"type": "string",
                            "description": "이 책 번역 시 문체·어조에 관한 구체적 지침 1~3문장 (한국어)"},
            "terms": {
                "type": "array",
                "description": "핵심 개념어·반복 전문용어 (30~100개). 일상어는 제외.",
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "korean": {"type": "string"},
                        "note": {"type": "string", "description": "필요할 때만: 맥락·대안 번역 (짧게)"},
                    },
                    "required": ["source", "korean"],
                },
            },
            "names": {
                "type": "array",
                "description": "인명·기관명·작품명 등 고유명사와 한국어 표기 (국립국어원 외래어 표기법, "
                               "국내에 정착된 표기가 있으면 그것을).",
                "items": {
                    "type": "object",
                    "properties": {"source": {"type": "string"}, "korean": {"type": "string"}},
                    "required": ["source", "korean"],
                },
            },
        },
        "required": ["title", "author", "source_language", "field", "summary",
                     "style_notes", "terms", "names"],
    },
}

SYSTEM = """당신은 인문·예술 분야 학술서의 전문 번역가이자 편집자입니다. 곧 이 텍스트를 한국어로 번역할 것이며, 번역 전에 책 전체를 훑어 번역 기준서(용어집)를 만듭니다.

- terms: 저자의 핵심 개념어와 반복되는 전문용어를 고르고, 한국 학계에서 통용되는 번역어를 우선하세요. 하나의 원어에는 하나의 번역어를 정하세요. 철학자의 고유 개념(예: Heidegger의 Dasein)은 정착된 역어를 따르세요.
- 사용자가 미리 정한 고정 용어가 주어지면 반드시 그대로 포함하고 바꾸지 마세요.
- names: 텍스트에 등장하는 인명·작품명·기관명을 한국어 표기와 함께.
- 근거 없이 저자나 제목을 지어내지 마세요."""


def parse_user_glossary(text: str) -> dict[str, str]:
    """'원어 = 번역어' 형식의 줄을 읽는다. # 뒤는 주석."""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"^(.+?)\s*(?:=|→|->|:)\s*(.+)$", line)
        if m:
            out[m.group(1).strip()] = m.group(2).strip()
    return out


def build_glossary(pages: list[dict], user_terms: dict[str, str]) -> dict:
    parts = []
    total = 0
    for i, p in enumerate(pages):
        if p["page_kind"] in ("bibliography", "index", "blank"):
            continue
        t = page_plain_text(p)
        if not t:
            continue
        chunk = f"[스캔 {i + 1}쪽]\n{t}"
        if total + len(chunk) > MAX_CHARS:
            break
        parts.append(chunk)
        total += len(chunk)
    corpus = "\n\n".join(parts)

    fixed = "\n".join(f"- {k} → {v}" for k, v in user_terms.items()) or "(없음)"
    prompt = (f"[사용자 고정 용어 — 반드시 그대로 사용]\n{fixed}\n\n"
              f"[원문 전체]\n{corpus}\n\n"
              "위 텍스트를 바탕으로 record_glossary를 작성하세요.")
    data = call_tool(system=SYSTEM, content=prompt, tool=TOOL, max_tokens=16000)

    # 사용자 고정 용어가 항상 이긴다
    terms = [t for t in data.get("terms", []) if t.get("source") and t.get("korean")]
    lower_fixed = {k.lower() for k in user_terms}
    terms = [t for t in terms if t["source"].strip().lower() not in lower_fixed]
    terms = [{"source": k, "korean": v, "note": "고정 용어"} for k, v in user_terms.items()] + terms
    data["terms"] = terms
    data["names"] = [n for n in data.get("names", []) if n.get("source") and n.get("korean")]
    return data


def glossary_to_text(g: dict) -> str:
    """사람이 읽고 고칠 수 있는 형태 (Drive에 저장됨)."""
    lines = [
        f"# {g.get('title') or '(제목 미상)'} — {g.get('author') or '(저자 미상)'}",
        f"# 분야: {g.get('field', '')} / 원문: {g.get('source_language', '')}",
        "#",
        "# 이 책의 자동 생성 용어집입니다. 마음에 들지 않는 번역어가 있으면",
        "# Reading/glossary.txt 에 '원어 = 번역어' 로 추가한 뒤 다시 번역하세요.",
        "",
        "## 요약",
        g.get("summary", ""),
        "",
        "## 용어",
    ]
    for t in g.get("terms", []):
        note = f"   # {t['note']}" if t.get("note") else ""
        lines.append(f"{t['source']} = {t['korean']}{note}")
    lines += ["", "## 고유명사"]
    for n in g.get("names", []):
        lines.append(f"{n['source']} = {n['korean']}")
    return "\n".join(lines) + "\n"


def glossary_for_prompt(g: dict) -> str:
    head = [
        f"제목: {g.get('title') or '미상'} / 저자: {g.get('author') or '미상'}",
        f"분야: {g.get('field', '')} / 원문 언어: {g.get('source_language', '')}",
        f"내용 요약: {g.get('summary', '')}",
        f"문체 지침: {g.get('style_notes', '')}",
        "",
        "[용어집 — 반드시 이 번역어를 사용]",
    ]
    head += [f"- {t['source']} → {t['korean']}" + (f" ({t['note']})" if t.get("note") else "")
             for t in g.get("terms", [])]
    head += ["", "[고유명사 표기]"]
    head += [f"- {n['source']} → {n['korean']}" for n in g.get("names", [])]
    return "\n".join(head)
