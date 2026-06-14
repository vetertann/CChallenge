"""Provider routing for the CAR-bench A-Agent style agent."""

from __future__ import annotations

import json
import time
from typing import Any

from config import (
    CEREBRAS_API_KEY,
    CEREBRAS_BASE_URL,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    MODEL_MAX_ATTEMPTS,
    MODEL_MAX_OUTPUT_TOKENS,
    MODEL_REASONING_EFFORT,
    MODEL_TEMPERATURE,
    MODEL_TIMEOUT_SECONDS,
    NEBIUS_API_KEY,
    NEBIUS_BASE_URL,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
)


EXECUTE_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {
            "type": "string",
            "description": "One or two short sentences describing the next code step.",
        },
        "code": {
            "type": "string",
            "description": "Executable Python source only. No markdown fences.",
        },
    },
    "required": ["thought", "code"],
    "additionalProperties": False,
}

OPENAI_TOOL_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "execute_python",
            "description": "Execute Python code inside the persistent CAR-bench workspace.",
            "parameters": EXECUTE_TOOL_SCHEMA,
        },
    }
]


class ModelCallError(RuntimeError):
    """Raised when a model call fails after retries."""


def _temperature() -> float | None:
    value = (MODEL_TEMPERATURE or "").strip()
    if not value:
        return None
    return float(value)


def make_client(provider: str):
    provider = provider.lower()
    if provider == "openai":
        if not OPENAI_API_KEY:
            raise RuntimeError("Missing OPENAI_API_KEY.")
        from openai import OpenAI

        return OpenAI(
            base_url=OPENAI_BASE_URL,
            api_key=OPENAI_API_KEY,
            timeout=MODEL_TIMEOUT_SECONDS,
        )
    if provider == "nebius":
        if not NEBIUS_API_KEY:
            raise RuntimeError("Missing NEBIUS_API_KEY.")
        from openai import OpenAI

        return OpenAI(
            base_url=NEBIUS_BASE_URL,
            api_key=NEBIUS_API_KEY,
            timeout=MODEL_TIMEOUT_SECONDS,
        )
    if provider == "openrouter":
        if not OPENROUTER_API_KEY:
            raise RuntimeError("Missing OPENROUTER_API_KEY.")
        from openai import OpenAI

        return OpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=OPENROUTER_API_KEY,
            timeout=MODEL_TIMEOUT_SECONDS,
        )
    if provider == "deepseek":
        if not DEEPSEEK_API_KEY:
            raise RuntimeError("Missing DEEPSEEK_API_KEY.")
        from openai import OpenAI

        return OpenAI(
            base_url=DEEPSEEK_BASE_URL,
            api_key=DEEPSEEK_API_KEY,
            timeout=MODEL_TIMEOUT_SECONDS,
        )
    if provider == "cerebras":
        if not CEREBRAS_API_KEY:
            raise RuntimeError("Missing CEREBRAS_API_KEY.")
        from cerebras.cloud.sdk import Cerebras

        base = CEREBRAS_BASE_URL[:-3] if CEREBRAS_BASE_URL.endswith("/v1") else CEREBRAS_BASE_URL
        return Cerebras(
            api_key=CEREBRAS_API_KEY,
            base_url=base,
            timeout=MODEL_TIMEOUT_SECONDS,
        )
    raise RuntimeError(f"Unsupported provider: {provider}")


def call_model_with_retry(
    *,
    client: Any,
    provider: str,
    model: str,
    messages: list[dict[str, Any]],
    tool_mode: str,
) -> tuple[Any, float]:
    last_exc: BaseException | None = None
    for attempt in range(1, MODEL_MAX_ATTEMPTS + 1):
        started = time.perf_counter()
        try:
            response = _call_model(
                client=client,
                provider=provider,
                model=model,
                messages=messages,
                tool_mode=tool_mode,
            )
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            return response, elapsed_ms
        except Exception as exc:
            last_exc = exc
            if attempt >= MODEL_MAX_ATTEMPTS or not _is_retryable(exc):
                break
            sleep_s = min(2 ** attempt, 30)
            time.sleep(sleep_s)
    raise ModelCallError(str(last_exc)) from last_exc


def _call_model(
    *,
    client: Any,
    provider: str,
    model: str,
    messages: list[dict[str, Any]],
    tool_mode: str,
) -> Any:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    temp = _temperature()
    if temp is not None:
        kwargs["temperature"] = temp

    if provider == "cerebras":
        kwargs["max_completion_tokens"] = MODEL_MAX_OUTPUT_TOKENS
        if MODEL_REASONING_EFFORT:
            kwargs["reasoning_effort"] = MODEL_REASONING_EFFORT
    else:
        kwargs["max_tokens"] = MODEL_MAX_OUTPUT_TOKENS

    if tool_mode == "native":
        kwargs["tools"] = OPENAI_TOOL_SPEC
        kwargs["tool_choice"] = {
            "type": "function",
            "function": {"name": "execute_python"},
        }

    if provider == "openrouter":
        kwargs["extra_body"] = {"reasoning": {"enabled": True}}
    elif provider == "deepseek":
        kwargs["extra_body"] = {"thinking": {"type": "enabled"}}

    return client.chat.completions.create(**kwargs)


def extract_execute_python(response: Any, tool_mode: str) -> tuple[str, str, str, str]:
    """Return assistant_text, thought, code, call_id."""

    msg = response.choices[0].message
    assistant_text = (msg.content or "").strip() if isinstance(msg.content, str) else ""

    if tool_mode == "native":
        for tool_call in msg.tool_calls or []:
            fn = getattr(tool_call, "function", None)
            if getattr(fn, "name", None) == "execute_python":
                payload = json.loads(getattr(fn, "arguments", "") or "{}")
                thought = payload.get("thought") or ""
                code = payload.get("code") or ""
                if not isinstance(code, str) or not code.strip():
                    raise ValueError("execute_python call must include non-empty code")
                return (
                    assistant_text,
                    thought if isinstance(thought, str) else "",
                    code,
                    getattr(tool_call, "id", None) or "call_execute_python",
                )
        raise ValueError("model did not call execute_python")

    payload = extract_json_payload(assistant_text)
    thought = payload.get("thought") or ""
    code = payload.get("code") or ""
    if not isinstance(code, str) or not code.strip():
        raise ValueError("execute_python JSON must include non-empty code")
    return assistant_text, thought if isinstance(thought, str) else "", code, "pseudo_execute_python"


def extract_json_payload(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        if len(parts) >= 3:
            raw = parts[1]
            raw = raw[4:] if raw.startswith("json") else raw
            raw = raw.strip()
    decoder = json.JSONDecoder()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        if start < 0:
            raise ValueError("model did not return JSON")
        payload, _ = decoder.raw_decode(raw[start:])
    if not isinstance(payload, dict):
        raise ValueError("model JSON must be an object")
    return payload


def build_assistant_log(
    *,
    assistant_text: str,
    thought: str,
    code: str,
    call_id: str,
    tool_mode: str,
) -> dict[str, Any]:
    payload = {"thought": thought, "code": code}
    if tool_mode == "native":
        return {
            "role": "assistant",
            "content": assistant_text or "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": "execute_python",
                        "arguments": json.dumps(payload, ensure_ascii=True),
                    },
                }
            ],
        }
    return {"role": "assistant", "content": assistant_text or json.dumps(payload)}


def build_tool_log(call_id: str, observation: str, tool_mode: str) -> dict[str, Any]:
    if tool_mode == "native":
        return {
            "role": "tool",
            "tool_call_id": call_id,
            "name": "execute_python",
            "content": observation,
        }
    return {
        "role": "user",
        "content": (
            "Observation from execute_python:\n"
            f"{observation}\n\n"
            "Reply with the next action as exactly one JSON object with keys thought and code."
        ),
    }


def build_repair_message(error: str, tool_mode: str) -> dict[str, str]:
    if tool_mode == "native":
        return {
            "role": "user",
            "content": (
                "Your previous response was invalid. Retry with exactly one "
                f"execute_python tool call. Error: {error}"
            ),
        }
    return {
        "role": "user",
        "content": (
            "Your previous response was invalid. Retry with exactly one JSON "
            f"object with keys thought and code. Error: {error}"
        ),
    }


def extract_usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}

    def get(name: str) -> int:
        value = getattr(usage, name, None)
        if value is None and isinstance(usage, dict):
            value = usage.get(name)
        return int(value or 0)

    out = {
        "prompt_tokens": get("prompt_tokens"),
        "completion_tokens": get("completion_tokens"),
        "total_tokens": get("total_tokens"),
    }
    details = getattr(usage, "completion_tokens_details", None)
    if details is not None:
        reasoning = getattr(details, "reasoning_tokens", None)
        if reasoning is not None:
            out["reasoning_tokens"] = int(reasoning or 0)
    return out


def _is_retryable(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    return status in {429, 500, 502, 503, 504} or exc.__class__.__name__ in {
        "APITimeoutError",
        "APIConnectionError",
    }

