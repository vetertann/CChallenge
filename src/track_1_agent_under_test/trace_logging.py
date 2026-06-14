"""JSONL tracing for CAR-bench agent turns."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import CAR_AGENT_RUN_ID, CAR_AGENT_TRACE_DIR


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_json(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return repr(value)


def _safe_name(value: str, default: str) -> str:
    value = value.strip() or default
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)


def preview_text(value: str | None, limit: int = 240) -> str:
    text = value or ""
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


class TraceWriter:
    """Append-only JSONL trace grouped by A2A context id."""

    def __init__(
        self,
        context_id: str,
        *,
        model: str | None = None,
        provider: str | None = None,
        tool_mode: str | None = None,
    ) -> None:
        self.context_id = context_id
        self.run_id = _safe_name(CAR_AGENT_RUN_ID, "run")
        trace_dir = Path(CAR_AGENT_TRACE_DIR) / self.run_id
        trace_dir.mkdir(parents=True, exist_ok=True)
        self.trace_dir = trace_dir
        self._ensure_run_manifest(model=model, provider=provider, tool_mode=tool_mode)
        safe_context = _safe_name(context_id, "context")
        self.path = trace_dir / f"{safe_context}.jsonl"

    def _ensure_run_manifest(
        self,
        *,
        model: str | None,
        provider: str | None,
        tool_mode: str | None,
    ) -> None:
        manifest_path = self.trace_dir / ".run.json"
        if manifest_path.exists():
            return
        manifest = {
            "run_id": self.run_id,
            "created_at": _now(),
            "trace_root": str(Path(CAR_AGENT_TRACE_DIR)),
            "trace_dir": str(self.trace_dir),
            "model": model,
            "provider": provider,
            "tool_mode": tool_mode,
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=True) + "\n")

    def write(self, event: str, **payload: Any) -> None:
        record = {
            "ts": _now(),
            "run_id": self.run_id,
            "context_id": self.context_id,
            "event": event,
            **{key: _safe_json(value) for key, value in payload.items()},
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")
