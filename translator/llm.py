"""Claude API 호출 공통 부분."""
import os
import time
from typing import Any

import anthropic

from . import config

_client = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY 환경변수가 없습니다.")
        # SDK가 429/529(과부하)·네트워크 오류를 지수 백오프로 자동 재시도
        _client = anthropic.Anthropic(max_retries=8, timeout=600)
    return _client


def call_tool(
    *,
    system: str,
    content: list[dict] | str,
    tool: dict,
    max_tokens: int = 8000,
    attempts: int = 3,
) -> dict[str, Any]:
    """도구 호출을 강제해서 구조화된 JSON을 받는다.

    system 프롬프트에는 cache_control을 걸어, 같은 책의 반복 호출에서
    용어집·지침 부분이 캐시 요금으로 처리되도록 한다.
    """
    last_err: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            resp = client().messages.create(
                model=config.MODEL,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
                messages=[{"role": "user", "content": content}],
            )
            if resp.stop_reason == "max_tokens":
                max_tokens = min(max_tokens * 2, 32000)
                raise RuntimeError("응답이 max_tokens에서 잘렸습니다 (한도를 늘려 재시도).")
            for block in resp.content:
                if block.type == "tool_use":
                    return block.input
            raise RuntimeError(f"도구 응답이 없습니다 (stop_reason={resp.stop_reason}).")
        except (anthropic.BadRequestError, anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            raise  # 재시도해도 소용없는 오류
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"  [재시도 {attempt}/{attempts}] {type(e).__name__}: {e}")
            time.sleep(3 * attempt)
    raise RuntimeError(f"Claude 호출 실패: {last_err}") from last_err
