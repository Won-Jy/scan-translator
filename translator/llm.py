"""Claude API 호출 공통 부분."""
import json
import os
import time
from typing import Any

import anthropic

from . import config

_client = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        # 붙여넣을 때 섞인 공백·줄바꿈·따옴표 제거
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip().strip('"').strip("'").strip()
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY 환경변수가 없습니다.")
        # SDK가 429/529(과부하)·네트워크 오류를 지수 백오프로 자동 재시도
        _client = anthropic.Anthropic(api_key=key, max_retries=8, timeout=600)
    return _client


def _strict(schema):
    """JSON 출력 형식 규칙에 맞게: 모든 object에 additionalProperties: false."""
    if isinstance(schema, dict):
        out = {k: _strict(v) for k, v in schema.items()}
        if out.get("type") == "object":
            out["additionalProperties"] = False
        return out
    if isinstance(schema, list):
        return [_strict(v) for v in schema]
    return schema


def call_tool(
    *,
    system: str,
    content: list[dict] | str,
    tool: dict,
    max_tokens: int = 8000,
    attempts: int = 3,
) -> dict[str, Any]:
    """구조화된 JSON을 받는다.

    최신 모델은 tool_choice로 도구 호출을 강제할 수 없어서, 도구의 입력 스키마를
    JSON 출력 형식(output_config.format)으로 넘겨 응답 자체를 그 스키마로 받는다.
    system 프롬프트에는 cache_control을 걸어, 같은 책의 반복 호출에서
    용어집·지침 부분이 캐시 요금으로 처리되도록 한다.
    """
    output_config = {"format": {"type": "json_schema", "schema": _strict(tool["input_schema"])}}
    note = f"\n\n[응답 형식] {tool['name']} 도구 대신, 그 입력에 해당하는 JSON 객체 하나만 응답하세요."
    last_err: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            resp = client().messages.create(
                model=config.MODEL,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system + note, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": content}],
                extra_body={"output_config": output_config},
            )
            if resp.stop_reason == "max_tokens":
                max_tokens = min(max_tokens * 2, 32000)
                raise RuntimeError("응답이 max_tokens에서 잘렸습니다 (한도를 늘려 재시도).")
            text = "".join(b.text for b in resp.content if b.type == "text").strip()
            if not text:
                raise RuntimeError(f"응답이 비어 있습니다 (stop_reason={resp.stop_reason}).")
            return json.loads(text)
        except (anthropic.BadRequestError, anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            raise  # 재시도해도 소용없는 오류
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"  [재시도 {attempt}/{attempts}] {type(e).__name__}: {e}")
            time.sleep(3 * attempt)
    raise RuntimeError(f"Claude 호출 실패: {last_err}") from last_err
