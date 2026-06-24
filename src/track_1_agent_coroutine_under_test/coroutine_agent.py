"""Coroutine-bridge CAR-bench agent under test.

This variant keeps model-written Python running in a per-context worker thread.
When Python calls a CAR-bench tool wrapper, the wrapper emits an official A2A
tool call, blocks, and resumes when the evaluator sends tool results.
"""

from __future__ import annotations

import json
import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from a2a.helpers.proto_helpers import new_data_part, new_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import Role
from google.protobuf.json_format import MessageToDict

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import (  # noqa: E402
    CAR_AGENT_TRACE_FULL_MODEL_MESSAGES,
    MODEL_ID,
    MODEL_MAX_INTERNAL_STEPS,
    MODEL_PROVIDER,
    MODEL_SCHEMA_MAX_RETRIES,
    MODEL_TIMEOUT_SECONDS,
    MODEL_TOOL_MODE,
)
from coroutine_prompts import (  # noqa: E402
    NAVIGATION_STATE_POLICY_REMINDER,
    PREFERENCE_POLICY_REMINDER,
    PREFLIGHT_ATTENTION_REMINDER,
    build_system_prompt,
    environment_message,
    initial_user_message,
    user_followup_message,
)
from coroutine_repl import (  # noqa: E402
    BlockingPythonExecutor,
    CoroutineWorkspace,
    ExecutionResult,
    OutboundAction,
    ToolBridge,
    format_observation,
    json_dumps_safe,
)
from logging_utils import configure_logger  # noqa: E402
from provider import (  # noqa: E402
    build_assistant_log,
    build_repair_message,
    build_tool_log,
    call_model_with_retry,
    extract_execute_python,
    extract_usage,
    make_client,
)
from trace_logging import TraceWriter, preview_text  # noqa: E402
from turn_metrics import (  # noqa: E402
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

try:
    sys.path.remove(str(Path(__file__).resolve().parent.parent))
except ValueError:
    pass


logger = configure_logger(role="agent_under_test", context="coroutine")


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

    def reset(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.thinking_tokens = 0
        self.num_llm_calls = 0
        self.total_llm_time_ms = 0.0
        self.num_passes = 0


@dataclass
class InboundTurn:
    source: str
    policy_text: str = ""
    user_text: str = ""
    tools: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    part_summaries: list[dict[str, Any]] = field(default_factory=list)


class CoroutineAgentWorker:
    """Long-lived per-context agent worker."""

    def __init__(
        self,
        *,
        context_id: str,
        model: str,
        provider: str,
        tool_mode: str,
        client: Any,
    ) -> None:
        self.context_id = context_id
        self.model = model
        self.provider = provider
        self.tool_mode = tool_mode
        self.client = client
        self.outbox: queue.Queue[OutboundAction] = queue.Queue()
        self.inbox: queue.Queue[InboundTurn | None] = queue.Queue()
        self.bridge = ToolBridge(self.outbox)
        self.workspace = CoroutineWorkspace(self.bridge)
        self.executor = BlockingPythonExecutor(self.workspace)
        self.messages: list[dict[str, Any]] = []
        self.stable_messages: list[dict[str, Any]] = []
        self.system_prompt = ""
        self.initial_user_request = ""
        self.initialized = False
        self.metrics = SessionMetrics()
        self.trace = TraceWriter(
            context_id,
            model=model,
            provider=provider,
            tool_mode=f"{tool_mode}:coroutine",
        )
        self.ctx_logger = logger.bind(role="agent_under_test", context=f"ctx:{context_id[:8]}")
        self.thread = threading.Thread(target=self._loop, name=f"car-coroutine-{context_id[:8]}", daemon=True)
        self._stopped = threading.Event()

    def start(self) -> None:
        if not self.thread.is_alive():
            self.trace.write(
                "session_start",
                model=self.model,
                provider=self.provider,
                tool_mode=self.tool_mode,
                runtime="coroutine_bridge",
            )
            self.ctx_logger.info(f"Trace file for ctx:{self.context_id[:8]} -> {self.trace.path}")
            self.thread.start()

    def stop(self) -> None:
        self._stopped.set()
        if self.bridge.waiting:
            self.bridge.interrupt("Agent worker cancelled.")
        self.inbox.put(None)

    def submit_inbound(self, inbound: InboundTurn) -> None:
        if inbound.tools:
            self.workspace.update_tools(inbound.tools)
        if inbound.policy_text:
            self.workspace.policy = inbound.policy_text
        self.trace.write(
            "inbound_a2a",
            source=inbound.source,
            metadata=inbound.metadata,
            parts=inbound.part_summaries,
            policy_text=inbound.policy_text,
            user_text=inbound.user_text,
            tools=inbound.tools,
            tool_names=self._tool_names(inbound.tools),
            tool_results=inbound.tool_results,
            bridge_waiting=self.bridge.waiting,
        )

        if self.bridge.waiting:
            if inbound.source == SOURCE_ENVIRONMENT:
                self.trace.write(
                    "coroutine_resume_tool_results",
                    tool_results=inbound.tool_results,
                )
                self.bridge.deliver_results(inbound.tool_results)
                return
            self.bridge.interrupt(
                f"Received {inbound.source!r} while waiting for evaluator tool results."
            )
        self.inbox.put(inbound)

    def next_outbound(self, timeout: float | None = None) -> OutboundAction:
        return self.outbox.get(timeout=timeout)

    def _loop(self) -> None:
        while not self._stopped.is_set():
            inbound = self.inbox.get()
            if inbound is None:
                return
            try:
                self._handle_inbound(inbound)
                self._preflight_state(inbound)
                self.messages = self._rebuild_messages()
                self._run_until_outbound()
            except Exception as exc:
                self.ctx_logger.exception("Coroutine worker failure", error=str(exc))
                self.trace.write(
                    "agent_internal_failure",
                    error_type=exc.__class__.__name__,
                    error=str(exc),
                )
                self.outbox.put(
                    OutboundAction(
                        response_text="I hit an internal issue while deciding the next step.",
                        error=str(exc),
                    )
                )

    def _preflight_state(self, inbound: InboundTurn) -> None:
        """Ground stable system state before the model starts this user turn."""

        if inbound.source != SOURCE_USER:
            return
        result = self.workspace.preflight_navigation_state()
        self.trace.write(
            "navigation_state_preflight",
            status=result.get("status"),
            navigation_state=result.get("navigation_state"),
        )
        preference_result = self.workspace.preflight_user_preferences()
        self.trace.write(
            "user_preferences_preflight",
            status=preference_result.get("status"),
            summary=preference_result.get("summary"),
            requested_categories=preference_result.get("requested_categories"),
        )

    def _handle_inbound(self, inbound: InboundTurn) -> None:
        ws = self.workspace
        self.ctx_logger.info(
            "Bench -> coroutine agent "
            f"source={inbound.source} tools={len(ws.available_tools)} "
            f"tool_results={len(inbound.tool_results)} "
            f"user={preview_text(inbound.user_text, 120)!r}"
        )

        if not self.initialized:
            self.system_prompt = build_system_prompt(
                car_policy=ws.policy,
                tools=list(ws.available_tools.values()),
                tool_mode=self.tool_mode,
            )
            self.initial_user_request = inbound.user_text or "none"
            self.stable_messages = []
            self.messages = self._rebuild_messages()
            ws.observe_user(inbound.user_text or "none")
            self.initialized = True
            return

        self.messages = self._rebuild_messages()
        if inbound.source == SOURCE_ENVIRONMENT:
            if inbound.tool_results:
                ws.observe_environment(inbound.tool_results)
                message = environment_message(inbound.tool_results)
                self.stable_messages.append({"role": "user", "content": message})
                self.messages.append({"role": "user", "content": message})
            else:
                ws.observe_empty(SOURCE_ENVIRONMENT)
                self.messages.append(
                    {"role": "user", "content": "Environment sent an empty message. Continue carefully."}
                )
            return

        ws.observe_user(inbound.user_text or "none")
        followup = {
            "role": "user",
            "content": user_followup_message(inbound.user_text or "none"),
        }
        self.stable_messages.append(followup)
        self.messages.append(followup)

    def _rebuild_messages(self) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        if self.initial_user_request:
            messages.append({"role": "user", "content": initial_user_message(self.initial_user_request)})
        messages.extend(self.stable_messages)
        messages.append(
            {
                "role": "user",
                "content": (
                    "Current persistent scratchpad state:\n"
                    f"{json_dumps_safe(self.workspace.scratchpad, indent=2)}\n\n"
                    "Use this as your compact carry-forward memory.\n\n"
                    "Navigation policy reminder:\n"
                    f"{NAVIGATION_STATE_POLICY_REMINDER}\n\n"
                    "Preference policy reminder:\n"
                    f"{PREFERENCE_POLICY_REMINDER}\n\n"
                    f"{PREFLIGHT_ATTENTION_REMINDER}"
                ),
            }
        )
        return messages

    def _run_until_outbound(self) -> None:
        for step_index in range(1, MODEL_MAX_INTERNAL_STEPS + 1):
            assistant_log, thought, code, call_id = self._next_execute_python(step_index)
            self.metrics.add_pass()
            self.messages.append(assistant_log)
            result = self.executor.run(code)
            observation = format_observation(result, self.workspace.scratchpad)
            self.messages.append(build_tool_log(call_id, observation, self.tool_mode))
            self.trace.write(
                "repl_result",
                step_index=step_index,
                stdout=result.stdout,
                error=result.error,
                response_text=result.response_text,
                scratchpad=self.workspace.scratchpad,
                observation=observation,
            )
            self.ctx_logger.info(
                "Coroutine REPL result "
                f"step={step_index} error={result.error} "
                f"text={preview_text(result.response_text, 100)!r}"
            )

            if result.response_text:
                self.stable_messages.append(
                    {"role": "assistant", "content": result.response_text}
                )
                metadata = {TURN_METRICS_KEY: self.metrics.as_metadata(self.model)}
                self.metrics.reset()
                self.trace.write(
                    "benchmark_text_ready",
                    step_index=step_index,
                    response_text=result.response_text,
                    metadata=metadata,
                )
                self.outbox.put(OutboundAction(response_text=result.response_text, metadata=metadata))
                return

        self.trace.write("max_internal_steps_reached", max_steps=MODEL_MAX_INTERNAL_STEPS)
        self.outbox.put(
            OutboundAction(response_text="I need a bit more information before I can continue.")
        )

    def _next_execute_python(self, step_index: int) -> tuple[dict[str, Any], str, str, str]:
        for retry in range(MODEL_SCHEMA_MAX_RETRIES):
            request_messages = list(self.messages)
            if retry:
                request_messages = request_messages + [
                    build_repair_message("invalid execute_python action", self.tool_mode)
                ]
            self.trace.write(
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
            self.metrics.add_call(extract_usage(response), elapsed_ms)
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
                self.trace.write(
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
                self.ctx_logger.info(
                    "Model -> coroutine execute_python "
                    f"step={step_index} retry={retry + 1} "
                    f"thought={preview_text(thought, 120)!r} "
                    f"code={preview_text(code, 180)!r}"
                )
                return assistant_log, thought, code, call_id
            except Exception as exc:
                self.trace.write(
                    "model_action_parse_error",
                    step_index=step_index,
                    retry=retry + 1,
                    error_type=exc.__class__.__name__,
                    error=str(exc),
                )
                if retry + 1 >= MODEL_SCHEMA_MAX_RETRIES:
                    raise
                self.messages.append(build_repair_message(str(exc), self.tool_mode))
        raise RuntimeError("Model did not produce an execute_python action.")

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


class CoroutineCARBenchAgentExecutor(AgentExecutor):
    """A2A executor that bridges evaluator turns into blocking Python calls."""

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
        self.workers: dict[str, CoroutineAgentWorker] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        worker = self.workers.get(context.context_id)
        if worker is None:
            worker = CoroutineAgentWorker(
                context_id=context.context_id,
                model=self.model,
                provider=self.provider,
                tool_mode=self.tool_mode,
                client=self.client,
            )
            self.workers[context.context_id] = worker
            worker.start()

        inbound = self._parse_inbound(context)
        worker.submit_inbound(inbound)
        try:
            outbound = worker.next_outbound(timeout=max(30, MODEL_TIMEOUT_SECONDS + 30))
        except queue.Empty:
            outbound = OutboundAction(
                response_text="I hit an internal timeout while deciding the next step.",
                error="worker outbox timeout",
            )

        parts = []
        if outbound.response_text:
            parts.append(new_text_part(outbound.response_text))
        if outbound.tool_calls:
            parts.append(new_data_part({"tool_calls": outbound.tool_calls}))
        if not parts:
            parts.append(new_text_part("I hit an internal issue and need to try again."))

        response = new_message(
            parts=parts,
            context_id=context.context_id,
            role=Role.ROLE_AGENT,
        )
        response.metadata.update(outbound.metadata)
        worker.trace.write(
            "outbound_a2a",
            text=outbound.response_text,
            tool_calls=outbound.tool_calls,
            metadata=self._metadata_dict(response.metadata),
            part_count=len(parts),
            error=outbound.error,
        )
        worker.ctx_logger.info(
            "Coroutine agent -> bench "
            f"text={preview_text(outbound.response_text, 120)!r} "
            f"tool_calls={json.dumps(outbound.tool_calls, ensure_ascii=True)}"
        )
        await event_queue.enqueue_event(response)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        worker = self.workers.pop(context.context_id, None)
        if worker:
            worker.stop()

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
                    raw_results = data.get("tool_results") or data.get("toolResults") or []
                    part_summaries.append(
                        {
                            "index": index,
                            "type": "data",
                            "keys": sorted(data.keys()),
                            "tool_names": self._tool_names(data.get("tools") or []),
                            "tool_result_count": len(raw_results),
                            "data": data,
                        }
                    )
                    if data.get("tools"):
                        tools = data["tools"]
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
