"""A-Agent style CAR-bench agent under test."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from a2a.helpers.proto_helpers import new_data_part, new_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import Role
from google.protobuf.json_format import MessageToDict

from config import (
    CAR_AGENT_TRACE_FULL_MODEL_MESSAGES,
    MODEL_ID,
    MODEL_MAX_INTERNAL_STEPS,
    MODEL_PROVIDER,
    MODEL_SCHEMA_MAX_RETRIES,
    MODEL_TOOL_MODE,
)
from prompts import build_system_prompt, environment_message, initial_user_message, user_followup_message
from provider import (
    build_assistant_log,
    build_repair_message,
    build_tool_log,
    call_model_with_retry,
    extract_execute_python,
    extract_usage,
    make_client,
)
from repl_workspace import CarWorkspace, ExecutionResult, PythonExecutor, format_observation
from trace_logging import TraceWriter, preview_text

sys.path.insert(0, str(Path(__file__).parent.parent))
from logging_utils import configure_logger
from turn_metrics import (
    AVG_LLM_CALL_TIME_MS,
    COMPLETION_TOKENS,
    COST,
    MODEL,
    NUM_LLM_CALLS,
    NUM_PASSES,
    PROMPT_TOKENS,
    SOURCE_ENVIRONMENT,
    SOURCE_KEY,
    SOURCE_USER,
    THINKING_TOKENS,
    TURN_METRICS_KEY,
)
sys.path.pop(0)


logger = configure_logger(role="agent_under_test", context="-")


@dataclass
class SessionMetrics:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    thinking_tokens: int = 0
    num_llm_calls: int = 0
    total_llm_time_ms: float = 0.0
    num_passes: int = 0

    def add_call(self, usage: dict[str, int], elapsed_ms: float) -> None:
        self.prompt_tokens += int(usage.get("prompt_tokens", 0))
        self.completion_tokens += int(usage.get("completion_tokens", 0))
        self.thinking_tokens += int(usage.get("reasoning_tokens", 0))
        self.num_llm_calls += 1
        self.total_llm_time_ms += elapsed_ms

    def add_pass(self) -> None:
        self.num_passes += 1

    def as_metadata(self, model: str) -> dict[str, Any]:
        avg = self.total_llm_time_ms / self.num_llm_calls if self.num_llm_calls else 0.0
        return {
            PROMPT_TOKENS: self.prompt_tokens,
            COMPLETION_TOKENS: self.completion_tokens,
            COST: 0.0,
            MODEL: model,
            THINKING_TOKENS: self.thinking_tokens,
            NUM_LLM_CALLS: self.num_llm_calls,
            AVG_LLM_CALL_TIME_MS: round(avg, 1),
            NUM_PASSES: max(1, self.num_passes),
        }


@dataclass
class AgentSession:
    workspace: CarWorkspace = field(default_factory=CarWorkspace)
    executor: PythonExecutor | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    initialized: bool = False
    metrics: SessionMetrics = field(default_factory=SessionMetrics)
    trace: TraceWriter | None = None

    def ensure_executor(self) -> PythonExecutor:
        if self.executor is None:
            self.executor = PythonExecutor(self.workspace)
        return self.executor

    def reset_metrics(self) -> None:
        self.metrics = SessionMetrics()


@dataclass
class InboundTurn:
    source: str
    policy_text: str = ""
    user_text: str = ""
    tools: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    part_summaries: list[dict[str, Any]] = field(default_factory=list)


class CARBenchAgentExecutor(AgentExecutor):
    """Agent executor that asks the model for Python, then emits CAR-bench A2A."""

    def __init__(
        self,
        *,
        model: str | None = None,
        provider: str | None = None,
        tool_mode: str | None = None,
    ) -> None:
        self.model = model or MODEL_ID
        self.provider = (provider or MODEL_PROVIDER).lower()
        self.tool_mode = (tool_mode or MODEL_TOOL_MODE).lower()
        self.client = make_client(self.provider)
        self.sessions: dict[str, AgentSession] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        session = self.sessions.setdefault(context.context_id, AgentSession())
        if session.trace is None:
            session.trace = TraceWriter(
                context.context_id,
                model=self.model,
                provider=self.provider,
                tool_mode=self.tool_mode,
            )
            session.trace.write(
                "session_start",
                model=self.model,
                provider=self.provider,
                tool_mode=self.tool_mode,
            )
            logger.info(
                f"Trace file for ctx:{context.context_id[:8]} -> {session.trace.path}"
            )
        trace = session.trace
        ctx_logger = logger.bind(role="agent_under_test", context=f"ctx:{context.context_id[:8]}")

        inbound = self._parse_inbound(context)
        trace.write(
            "inbound_a2a",
            source=inbound.source,
            metadata=inbound.metadata,
            parts=inbound.part_summaries,
            policy_text=inbound.policy_text,
            user_text=inbound.user_text,
            tools=inbound.tools,
            tool_names=self._tool_names(inbound.tools),
            tool_results=inbound.tool_results,
        )
        self._update_session(session, inbound)

        ctx_logger.info(
            "Bench -> agent "
            f"source={inbound.source} parts={len(inbound.part_summaries)} "
            f"tools={len(session.workspace.available_tools)} "
            f"tool_results={len(inbound.tool_results)} "
            f"user={preview_text(inbound.user_text, 120)!r}"
        )
        trace.write(
            "session_state_after_inbound",
            initialized=session.initialized,
            available_tool_names=session.workspace.available_tool_names(),
            last_source=session.workspace.last_source,
            scratchpad=session.workspace.scratchpad,
        )

        try:
            result = self._run_until_benchmark_action(session, trace, ctx_logger)
        except Exception as exc:
            ctx_logger.exception("Agent internal failure", error=str(exc))
            trace.write("agent_internal_failure", error_type=exc.__class__.__name__, error=str(exc))
            result = ExecutionResult(
                stdout="",
                response_text=(
                    "I hit an internal issue while deciding the next step."
                ),
            )
        parts = []
        if result.response_text:
            parts.append(new_text_part(result.response_text))
        if result.tool_calls:
            parts.append(new_data_part({"tool_calls": result.tool_calls}))
        if not parts:
            parts.append(new_text_part("I hit an internal issue and need to try again."))

        response = new_message(
            parts=parts,
            context_id=context.context_id,
            role=Role.ROLE_AGENT,
        )

        if not result.tool_calls and session.metrics.num_llm_calls:
            response.metadata.update({TURN_METRICS_KEY: session.metrics.as_metadata(self.model)})
            session.reset_metrics()

        trace.write(
            "outbound_a2a",
            text=result.response_text,
            tool_calls=result.tool_calls,
            metadata=self._metadata_dict(response.metadata),
            part_count=len(parts),
        )
        ctx_logger.info(
            "Agent -> bench "
            f"text={preview_text(result.response_text, 120)!r} "
            f"tool_calls={json.dumps(result.tool_calls, ensure_ascii=True)}"
        )
        await event_queue.enqueue_event(response)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        self.sessions.pop(context.context_id, None)

    def _parse_inbound(self, context: RequestContext) -> InboundTurn:
        metadata = self._metadata_dict(getattr(context.message, "metadata", None))
        source = str(metadata.get(SOURCE_KEY) or "")
        policy_text = ""
        user_text = ""
        tools: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        part_summaries: list[dict[str, Any]] = []

        message = context.message
        if message:
            for index, part in enumerate(message.parts):
                content_type = part.WhichOneof("content")
                if content_type == "text":
                    text = part.text or ""
                    part_summaries.append(
                        {
                            "index": index,
                            "type": "text",
                            "length": len(text),
                            "preview": preview_text(text),
                            "text": text,
                        }
                    )
                    if "System:" in text and "\n\nUser:" in text:
                        system_part, user_part = text.split("\n\nUser:", 1)
                        policy_text = system_part.replace("System:", "", 1).strip()
                        user_text = user_part.strip()
                    else:
                        user_text = text.strip()
                elif content_type == "data":
                    data = MessageToDict(part.data)
                    part_summaries.append(
                        {
                            "index": index,
                            "type": "data",
                            "keys": sorted(data.keys()),
                            "tool_names": self._tool_names(data.get("tools") or []),
                            "tool_result_count": len(
                                data.get("tool_results") or data.get("toolResults") or []
                            ),
                            "data": data,
                        }
                    )
                    if data.get("tools"):
                        tools = data["tools"]
                    raw_results = data.get("tool_results") or data.get("toolResults") or []
                    if raw_results:
                        tool_results = [self._normalize_tool_result(item) for item in raw_results]

        if not user_text and not tool_results:
            fallback = context.get_user_input()
            if fallback:
                user_text = fallback.strip()

        if tool_results:
            source = SOURCE_ENVIRONMENT
        elif source not in {SOURCE_USER, SOURCE_ENVIRONMENT}:
            source = SOURCE_USER

        return InboundTurn(
            source=source,
            policy_text=policy_text,
            user_text=user_text or "",
            tools=tools,
            tool_results=tool_results,
            metadata=metadata,
            part_summaries=part_summaries,
        )

    def _update_session(self, session: AgentSession, inbound: InboundTurn) -> None:
        ws = session.workspace
        if inbound.tools:
            ws.update_tools(inbound.tools)
        if inbound.policy_text:
            ws.policy = inbound.policy_text

        if not session.initialized:
            session.messages = [
                {
                    "role": "system",
                    "content": build_system_prompt(
                        car_policy=ws.policy,
                        tools=list(ws.available_tools.values()),
                        tool_mode=self.tool_mode,
                    ),
                }
            ]
            ws.observe_user(inbound.user_text or "none")
            session.messages.append({"role": "user", "content": initial_user_message(inbound.user_text)})
            session.initialized = True
            return

        if inbound.source == SOURCE_ENVIRONMENT:
            if inbound.tool_results:
                ws.observe_environment(inbound.tool_results)
                session.messages.append({"role": "user", "content": environment_message(inbound.tool_results)})
            else:
                ws.observe_empty(SOURCE_ENVIRONMENT)
                session.messages.append({"role": "user", "content": "Environment sent an empty message. Continue carefully."})
            return

        ws.observe_user(inbound.user_text or "none")
        session.messages.append({"role": "user", "content": user_followup_message(inbound.user_text or "none")})

    def _run_until_benchmark_action(
        self,
        session: AgentSession,
        trace: TraceWriter,
        ctx_logger: Any,
    ) -> ExecutionResult:
        executor = session.ensure_executor()

        for step_index in range(1, MODEL_MAX_INTERNAL_STEPS + 1):
            assistant_text = thought = code = call_id = ""
            assistant_log: dict[str, Any] | None = None

            for retry in range(MODEL_SCHEMA_MAX_RETRIES):
                request_messages = session.messages
                if retry:
                    request_messages = session.messages + [
                        build_repair_message("invalid execute_python action", self.tool_mode)
                    ]
                trace.write(
                    "model_request",
                    step_index=step_index,
                    retry=retry + 1,
                    provider=self.provider,
                    model=self.model,
                    tool_mode=self.tool_mode,
                    message_count=len(request_messages),
                    messages=self._messages_for_trace(request_messages),
                )
                response, elapsed_ms = call_model_with_retry(
                    client=self.client,
                    provider=self.provider,
                    model=self.model,
                    messages=request_messages,
                    tool_mode=self.tool_mode,
                )
                session.metrics.add_call(extract_usage(response), elapsed_ms)
                try:
                    assistant_text, thought, code, call_id = extract_execute_python(
                        response,
                        self.tool_mode,
                    )
                    assistant_log = build_assistant_log(
                        assistant_text=assistant_text,
                        thought=thought,
                        code=code,
                        call_id=call_id,
                        tool_mode=self.tool_mode,
                    )
                    trace.write(
                        "model_execute_python",
                        step_index=step_index,
                        retry=retry + 1,
                        elapsed_ms=round(elapsed_ms, 1),
                        call_id=call_id,
                        assistant_text=assistant_text,
                        thought=thought,
                        code=code,
                        usage=extract_usage(response),
                    )
                    ctx_logger.info(
                        "Model -> execute_python "
                        f"step={step_index} retry={retry + 1} "
                        f"thought={preview_text(thought, 120)!r} "
                        f"code={preview_text(code, 180)!r}"
                    )
                    break
                except Exception as exc:
                    trace.write(
                        "model_action_parse_error",
                        step_index=step_index,
                        retry=retry + 1,
                        error_type=exc.__class__.__name__,
                        error=str(exc),
                    )
                    if retry + 1 >= MODEL_SCHEMA_MAX_RETRIES:
                        raise
                    session.messages.append(build_repair_message(str(exc), self.tool_mode))

            if assistant_log is None:
                raise RuntimeError("Model did not produce an execute_python action.")

            session.metrics.add_pass()
            session.messages.append(assistant_log)
            result = executor.run(code)
            observation = format_observation(result, session.workspace.scratchpad)
            session.messages.append(build_tool_log(call_id, observation, self.tool_mode))
            trace.write(
                "repl_result",
                step_index=step_index,
                stdout=result.stdout,
                error=result.error,
                tool_calls=result.tool_calls,
                response_text=result.response_text,
                scratchpad=session.workspace.scratchpad,
                observation=observation,
            )
            ctx_logger.info(
                "REPL result "
                f"step={step_index} error={result.error} "
                f"text={preview_text(result.response_text, 100)!r} "
                f"tool_calls={json.dumps(result.tool_calls, ensure_ascii=True)}"
            )

            if result.tool_calls or result.response_text:
                trace.write(
                    "benchmark_action_ready",
                    step_index=step_index,
                    tool_calls=result.tool_calls,
                    response_text=result.response_text,
                )
                return result

        fallback = ExecutionResult(
            stdout="",
            response_text="I need a bit more information before I can continue.",
        )
        trace.write("max_internal_steps_reached", max_steps=MODEL_MAX_INTERNAL_STEPS)
        return fallback

    @staticmethod
    def _normalize_tool_result(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "tool_name": item.get("tool_name") or item.get("toolName") or "",
            "tool_call_id": item.get("tool_call_id") or item.get("toolCallId") or "",
            "content": item.get("content") or "",
        }

    @staticmethod
    def _metadata_dict(metadata: Any) -> dict[str, Any]:
        if metadata is None:
            return {}
        if isinstance(metadata, dict):
            return metadata
        try:
            return MessageToDict(metadata)
        except Exception:
            return {}

    @staticmethod
    def _tool_names(tools: list[dict[str, Any]]) -> list[str]:
        return [
            tool.get("function", {}).get("name", "")
            for tool in tools
            if tool.get("function", {}).get("name")
        ]

    @staticmethod
    def _messages_for_trace(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if CAR_AGENT_TRACE_FULL_MODEL_MESSAGES:
            return messages
        out: list[dict[str, Any]] = []
        for item in messages:
            content = item.get("content")
            preview = preview_text(content, 500) if isinstance(content, str) else content
            summary = {key: value for key, value in item.items() if key != "content"}
            summary["content_preview"] = preview
            if isinstance(content, str):
                summary["content_length"] = len(content)
            out.append(summary)
        return out
