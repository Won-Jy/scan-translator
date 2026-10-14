"""3단계: 페이지 단위 번역 (1:1 페이지 대응).

페이지 경계에서 잘린 문장은 '시작된 페이지'에서 완전한 문장으로 번역하고,
다음 페이지에서는 그 부분을 건너뛴다. 덕분에 페이지끼리 독립적이 되어
병렬 번역이 가능하고, 한국어 어순 때문에 문장이 어색하게 쪼개지지 않는다.
"""
import json

from .llm import call_tool

TOOL = {
    "name": "record_translation",
    "description": "페이지 블록별 한국어 번역을 기록한다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "blocks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "text": {"type": "string"},
                    },
                    "required": ["id", "text"],
                },
            }
        },
        "required": ["blocks"],
    },
}

STYLE = """당신은 인문·예술 학술서를 한국어로 옮기는 숙련된 번역가입니다. 스캔본에서 전사한 원문을 한 페이지씩 받아 record_translation 도구로 블록별 번역을 기록합니다. 독자는 원서를 옆에 펼쳐 놓고 번갈아 읽는 연구자입니다.

[번역 원칙]
1. 요약·생략·의역으로 내용을 줄이지 말 것. 원문의 모든 문장과 논리 관계를 빠짐없이 옮길 것.
2. 번역투를 피한 자연스럽고 정확한 학술 한국어 문어체(~다). 긴 문장은 의미를 해치지 않는 선에서 한국어 호흡에 맞게 나눠도 됨.
3. 아래 용어집과 고유명사 표기를 반드시 따를 것. 용어집에 없는 개념어는 정착된 역어를 쓰고, 이 페이지 안에서 일관되게.
4. 고유명사·작품명은 이 페이지에서 처음 나올 때만 '한국어 표기(원어)'로 병기하고, 이후엔 한국어 표기만.
5. 핵심 개념어를 처음 번역할 때 원어를 밝히는 것이 이해에 중요하면 '돌봄(soin)'처럼 병기 가능. 남용 금지.
6. 인용부호: « » 와 " " → “ ”, 내부 인용 ‘ ’. 책·잡지 제목은 『 』, 논문·시·작품 제목은 「 」.
7. 서식 태그 유지: 강조 이탤릭은 <i>…</i>, 볼드 <b>…</b>, 각주 번호 <sup>3</sup> (번역문에서 같은 자리 근처에). 이탤릭이 책 제목이면 태그 대신 『 』. 그 밖의 HTML·마크다운 금지.
8. 원문에 섞인 라틴어·그리스어·다른 언어의 짧은 인용이나 개념어는 번역하고 괄호로 원어 병기.
9. 각주(footnote)도 본문과 같은 원칙으로 번역. 서지 정보(저자, 제목, 출판사, 연도, 쪽)는 원어 그대로 두고 'cf.', 'ibid.', 'op. cit.', 'p.' 같은 약어도 그대로.
10. heading은 제목답게 간결하게. 목차 항목은 '제목 …… 쪽번호' 형태 유지.
11. 번역문 외의 설명·사과·주석을 절대 덧붙이지 말 것. 원문이 이상해 보여도 최선의 번역만 기록.
12. 입력의 모든 블록 id에 대해 정확히 하나씩, 같은 순서로 결과를 낼 것.

[참고 정보]
"""


def build_system(glossary_prompt: str) -> str:
    return STYLE + glossary_prompt


def translate_page(
    system: str,
    page: dict,
    *,
    skip_prefix: str = "",
    append_suffix: str = "",
    prev_context: str = "",
) -> list[str]:
    blocks = page["blocks"]
    payload = [{"id": i, "type": b["type"], "text": b["text"]} for i, b in enumerate(blocks)]

    notes = []
    if prev_context:
        notes.append(f"[앞 페이지 끝부분 원문 — 흐름 파악용, 번역하지 말 것]\n{prev_context}")
    if skip_prefix:
        notes.append(
            "[페이지 시작 처리] 블록 0의 맨 앞부분\n"
            f"«{skip_prefix}»\n"
            "은 앞 페이지 문장의 끝부분이며 앞 페이지 번역에 이미 포함되었습니다. "
            "이 부분은 번역하지 말고 그 다음 문장부터 번역하세요. "
            "블록 0이 이 부분뿐이라면 빈 문자열로 두세요."
        )
    if append_suffix:
        notes.append(
            "[페이지 끝 처리] 이 페이지 본문의 마지막 문장은 다음 페이지에서\n"
            f"«{append_suffix}»\n"
            "로 끝납니다. 마지막 본문 블록에서 이 문장을 위 부분까지 합쳐 하나의 완전한 문장으로 번역하세요."
        )
    elif page.get("ends_mid_sentence"):
        notes.append("[페이지 끝 처리] 마지막 본문 문장은 다음 페이지로 이어집니다. "
                     "자연스럽게 끊기도록 번역하고 끝에 '…'를 붙이세요.")

    prompt = ""
    if notes:
        prompt += "\n\n".join(notes) + "\n\n"
    prompt += ("[이 페이지의 원문 블록]\n"
               + json.dumps(payload, ensure_ascii=False, indent=1)
               + "\n\nrecord_translation으로 모든 블록을 번역해 기록하세요.")

    for attempt in range(2):
        data = call_tool(system=system, content=prompt, tool=TOOL, max_tokens=12000)
        by_id = {}
        for b in data.get("blocks", []):
            try:
                by_id[int(b["id"])] = (b.get("text") or "").strip()
            except (KeyError, TypeError, ValueError):
                continue
        missing = [i for i in range(len(blocks)) if i not in by_id]
        # 첫 블록이 skip으로 비는 것은 정상
        missing = [i for i in missing if not (i == 0 and skip_prefix)]
        if not missing:
            return [by_id.get(i, "") for i in range(len(blocks))]
        print(f"  [경고] 번역 누락 블록 {missing} — 재시도")
        prompt += f"\n\n(주의: 지난 응답에서 id {missing} 블록이 빠졌습니다. 모든 id를 포함하세요.)"
    # 그래도 누락되면 원문을 남겨 두어 내용이 사라지지 않게 함
    return [by_id.get(i, blocks[i]["text"]) for i in range(len(blocks))]
