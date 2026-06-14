"""Prompt assembly for the CAR-bench A-Agent style agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config import CAR_AGENT_SKILL


SKILLS_DIR = Path(__file__).resolve().parent / "Skills"

BASE_SYSTEM_PROMPT = """You are a CAR-bench in-car assistant agent running inside a Python REPL harness.

## Runtime
- You have exactly one action surface: execute Python code.
- Persistent Python globals include `ws`, `scratchpad`, `respond`, `emit_tool_call`, `call`, and one bare function for each CAR-bench tool name.
- Variables you define persist across execute_python calls for the same CAR-bench task.
- The CAR-bench evaluator, not this Python runtime, executes vehicle/navigation/weather/productivity tools.
- Calling a CAR-bench tool wrapper only queues a benchmark tool call for the evaluator. It does not return the real tool result in the same Python execution.
- Tool results arrive on a later evaluator turn and are available as `ws.tool_results`.
- User follow-ups arrive as `ws.last_user_message`.
- The latest source tag is `ws.last_source`, usually `user` or `environment`.
- Use `print(...)` for observations you want to see after code execution.

## Output Discipline
- Every model reply must request exactly one execute_python action.
- To ask the evaluator to execute CAR-bench tools, call the corresponding Python wrapper, for example `get_weather(...)`.
- To speak to the user, call `respond("short TTS-friendly message")`.
- If tool calls are queued, the harness emits A2A data `{"tool_calls": [...]}`.
- If only `respond(...)` is called, the harness emits an A2A text response.
- Do not write custom JSON for A2A yourself.

## Tool Rules
- Use only tools listed in the current workspace function section.
- Use exact parameter names from each tool schema.
- If required information is missing or a tool is unavailable, ask a short clarification or transparently say it cannot be done.
- Respect the CAR-bench policy prompt exactly. It is benchmark policy, not user data.
- Do not invent tool results. If information requires a tool, queue the tool call and wait for evaluator results.
- Never invent IDs. Use only IDs present in context/policy or returned by evaluator tool results. Names are not IDs.
- Prefer environment/domain tools over manual reasoning when such a tool exists. Use calculator/math only for arithmetic that no domain tool covers.

## Execution Strategy
- Prefer a staged loop: first queue all independent read-only evaluator tool calls needed for the decision; wait for environment results; then decide whether to clarify, gather more facts, or perform side effects.
- Do not perform side effects in the same step as read-only calls if the side-effect arguments depend on those future tool results.
- Multiple independent read-only tool calls can be queued together. Sequential state changes should usually wait for the previous evaluator result if later arguments depend on earlier state.
- When a task has multiple requested outcomes, track them in `scratchpad["goals"]` or `scratchpad["gates"]` and do not stop until each outcome is completed, blocked by policy/tooling, or requires user clarification.

## Scratchpad
- `scratchpad` is persistent working memory.
- Use `scratchpad["gates"]` for confirmation, disambiguation, safety, and policy gates when helpful.
- Before any side effect, record the relevant gate in `scratchpad["gates"]`: required information known, policy prerequisites satisfied, ambiguity resolved, and confirmation obtained when required.
- Keep scratchpad compact.
"""

PROMPT_JSON_SUFFIX = """

## execute_python JSON Contract
Native tool calling is disabled for this run.
Every assistant reply must be exactly one JSON object:
{
  "thought": "one or two short sentences",
  "code": "valid Python source only"
}
Do not wrap the JSON in markdown fences. Do not add text before or after it.
"""


def load_skill_text() -> str:
    skill_name = (CAR_AGENT_SKILL or "").strip()
    if not skill_name:
        return ""
    skill_path = (SKILLS_DIR / skill_name).resolve()
    try:
        skill_path.relative_to(SKILLS_DIR.resolve())
    except ValueError as exc:
        raise RuntimeError(f"Skill must be inside {SKILLS_DIR}") from exc
    if not skill_path.exists():
        raise RuntimeError(f"Skill file not found: {skill_path}")
    text = skill_path.read_text().strip()
    return f"\n\n## Active Domain Skill\n{text}\n" if text else ""


def build_system_prompt(
    *,
    car_policy: str,
    tools: list[dict[str, Any]],
    tool_mode: str,
) -> str:
    prompt = BASE_SYSTEM_PROMPT + load_skill_text()
    prompt += "\n\n## CAR-bench Policy From Evaluator\n"
    prompt += car_policy.strip() if car_policy.strip() else "(No policy text was provided.)"
    prompt += "\n\n## Current Workspace Functions\n"
    prompt += render_tool_functions(tools)
    if tool_mode == "prompt_json":
        prompt += PROMPT_JSON_SUFFIX
    return prompt


def render_tool_functions(tools: list[dict[str, Any]]) -> str:
    if not tools:
        return "No CAR-bench tools are currently available. Use respond(...).\n"
    lines: list[str] = []
    for tool in tools:
        fn = tool.get("function", {})
        name = fn.get("name", "")
        if not name:
            continue
        description = (fn.get("description") or "").strip()
        parameters = fn.get("parameters") or {}
        lines.append(f"### `{name}(**kwargs)`")
        if description:
            lines.append(description)
        lines.append("Parameter schema:")
        lines.append("```json")
        lines.append(json.dumps(parameters, indent=2, ensure_ascii=True))
        lines.append("```")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def initial_user_message(user_request: str) -> str:
    return (
        "Initial user request:\n"
        f"{user_request.strip() or 'none'}\n\n"
        "Decide the next action by executing Python. Use CAR-bench tool wrappers "
        "or respond(...)."
    )


def user_followup_message(user_text: str) -> str:
    return (
        "User follow-up:\n"
        f"{user_text.strip() or 'none'}\n\n"
        "Continue from the current scratchpad and transcript."
    )


def environment_message(tool_results: list[dict[str, Any]]) -> str:
    return (
        "Environment tool results from the evaluator:\n"
        f"{json.dumps(tool_results, indent=2, ensure_ascii=True)}\n\n"
        "These are now available as ws.tool_results. Continue by executing Python."
    )
