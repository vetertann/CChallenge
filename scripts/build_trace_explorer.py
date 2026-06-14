#!/usr/bin/env python3
"""Build a self-contained HTML explorer for CAR-bench agent traces."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CHECKPOINTS = [
    "/tmp/car_bench_eval_base_test.json",
    "/tmp/car_bench_eval_hallucination_test.json",
    "/tmp/car_bench_eval_disambiguation_test.json",
    "/tmp/car_bench_eval_base_train.json",
    "/tmp/car_bench_eval_hallucination_train.json",
    "/tmp/car_bench_eval_disambiguation_train.json",
]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    for line_no, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as exc:
            events.append(
                {
                    "event": "jsonl_parse_error",
                    "path": str(path),
                    "line_no": line_no,
                    "error": str(exc),
                    "raw": line[:2000],
                }
            )
    return events


def compact(value: Any, max_len: int = 600) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    value = " ".join(value.split())
    if len(value) > max_len:
        return value[: max_len - 12] + " ...[truncated]"
    return value


def clean_json(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean_json(v) for v in value]
    return value


def number(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def token_metrics_from_usage(usage: dict[str, Any] | None) -> dict[str, float]:
    usage = usage or {}
    prompt = number(usage.get("prompt_tokens"))
    completion = number(usage.get("completion_tokens"))
    total = number(usage.get("total_tokens")) or prompt + completion
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "thinking_tokens": number(usage.get("thinking_tokens")),
    }


def add_metrics(left: dict[str, float], right: dict[str, Any]) -> dict[str, float]:
    for key, value in right.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            left[key] = number(left.get(key)) + number(value)
    return left


def metrics_from_model_calls(model_calls: list[dict[str, Any]]) -> dict[str, float]:
    metrics: dict[str, float] = {
        "num_llm_calls": float(len(model_calls)),
        "model_latency_ms": 0.0,
        "prompt_tokens": 0.0,
        "completion_tokens": 0.0,
        "total_tokens": 0.0,
        "thinking_tokens": 0.0,
    }
    for call in model_calls:
        metrics["model_latency_ms"] += number(call.get("elapsed_ms"))
        add_metrics(metrics, token_metrics_from_usage(call.get("usage")))
    return metrics


def metrics_from_turn_metrics(turn_metrics: dict[str, Any] | None) -> dict[str, float]:
    turn_metrics = turn_metrics or {}
    num_llm_calls = number(turn_metrics.get("num_llm_calls"))
    avg_call_ms = number(turn_metrics.get("avg_llm_call_time_ms"))
    return {
        "num_llm_calls": num_llm_calls,
        "model_latency_ms": avg_call_ms * num_llm_calls,
        "prompt_tokens": number(turn_metrics.get("prompt_tokens")),
        "completion_tokens": number(turn_metrics.get("completion_tokens")),
        "total_tokens": number(turn_metrics.get("total_tokens"))
        or number(turn_metrics.get("prompt_tokens")) + number(turn_metrics.get("completion_tokens")),
        "thinking_tokens": number(turn_metrics.get("thinking_tokens")),
        "cost": number(turn_metrics.get("cost")),
        "raw_turn_time_ms": number(turn_metrics.get("raw_turn_time_ms")),
        "turn_time_ms": number(turn_metrics.get("turn_time_ms")),
        "quota_wait_time_ms": number(turn_metrics.get("quota_wait_time_ms")),
        "num_passes": number(turn_metrics.get("num_passes")),
    }


def metrics_from_trajectory(traj: list[dict[str, Any]]) -> dict[str, float]:
    metrics: dict[str, float] = {
        "a2a_turn_time_ms": 0.0,
        "a2a_effective_turn_time_ms": 0.0,
        "quota_wait_time_ms": 0.0,
        "prompt_tokens": 0.0,
        "completion_tokens": 0.0,
        "total_tokens": 0.0,
        "thinking_tokens": 0.0,
        "num_llm_calls": 0.0,
        "model_latency_ms": 0.0,
        "cost": 0.0,
    }
    for msg in traj:
        evaluator_metrics = msg.get("evaluator_metrics") or {}
        metrics["a2a_turn_time_ms"] += number(evaluator_metrics.get("a2a_turn_time_ms"))
        metrics["a2a_effective_turn_time_ms"] += number(evaluator_metrics.get("a2a_effective_turn_time_ms"))
        metrics["quota_wait_time_ms"] += number(evaluator_metrics.get("quota_wait_time_ms"))
        add_metrics(metrics, metrics_from_turn_metrics(msg.get("turn_metrics")))
    return metrics


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def elapsed_ms(start: Any, end: Any) -> float:
    start_dt = parse_ts(start)
    end_dt = parse_ts(end)
    if not start_dt or not end_dt:
        return 0.0
    return max(0.0, (end_dt - start_dt).total_seconds() * 1000.0)


def first_user_from_trace(events: list[dict[str, Any]]) -> str:
    for event in events:
        if event.get("event") != "inbound_a2a":
            continue
        text = event.get("user_text") or ""
        if text:
            return text
        for part in event.get("parts") or []:
            part_text = part.get("text") or ""
            if "\n\nUser:" in part_text:
                return part_text.rsplit("\n\nUser:", 1)[-1].strip()
            if part_text and not part_text.startswith("System:"):
                return part_text.strip()
    return ""


def first_user_from_traj(traj: list[dict[str, Any]]) -> str:
    for msg in traj:
        if msg.get("role") == "user":
            content = msg.get("content") or ""
            if content != "###STOP###":
                return content
    return ""


def load_checkpoints(paths: list[str]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists():
            continue
        try:
            rows = json.loads(path.read_text(errors="replace"))
        except json.JSONDecodeError:
            continue
        split = path.stem.replace("car_bench_eval_", "")
        for row in rows:
            traj = row.get("traj") or row.get("trajectory") or []
            info = row.get("info", {}) or {}
            task = row.get("info", {}).get("task") or row.get("task") or {}
            reward_info = row.get("info", {}).get("reward_info") or row.get("reward_info") or {}
            metrics = metrics_from_trajectory(traj)
            metrics["total_llm_induced_latency_ms"] = number(info.get("total_llm_induced_latency_ms"))
            metrics["average_llm_induced_latency_per_turn_ms"] = number(
                info.get("average_llm_induced_latency_per_turn_ms")
            )
            metrics["latest_prompt_tokens"] = number(info.get("latest_prompt_tokens"))
            metrics["total_agent_cost"] = number(info.get("total_agent_cost"))
            metrics["user_cost"] = number(info.get("user_cost"))
            tasks.append(
                {
                    "checkpoint_path": str(path),
                    "split": split,
                    "task_id": row.get("task_id"),
                    "task_index": row.get("task_index"),
                    "trial": row.get("trial", 0),
                    "pass_number": int(row.get("trial", 0)) + 1 if row.get("trial") is not None else None,
                    "reward": row.get("reward"),
                    "error": row.get("info", {}).get("error") or row.get("error"),
                    "first_user": first_user_from_traj(traj),
                    "instruction": task.get("instruction"),
                    "removed_part": task.get("removed_part"),
                    "disambiguation_internal": task.get("disambiguation_element_internal"),
                    "disambiguation_user": task.get("disambiguation_element_user"),
                    "expected_actions": task.get("actions") or [],
                    "reward_info": reward_info,
                    "metrics": metrics,
                    "trajectory": traj,
                }
            )
    return tasks


def read_run_manifest(run_dir: Path) -> dict[str, Any]:
    manifest_path = run_dir / ".run.json"
    if not manifest_path.exists():
        return {}
    try:
        data = json.loads(manifest_path.read_text(errors="replace"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def run_meta_for_trace(trace_root: Path, path: Path, events: list[dict[str, Any]]) -> dict[str, Any]:
    event_run_id = next((event.get("run_id") for event in events if event.get("run_id")), None)
    manifest = read_run_manifest(path.parent)
    if event_run_id:
        run_id = str(event_run_id)
    elif manifest.get("run_id"):
        run_id = str(manifest["run_id"])
    elif path.parent == trace_root:
        run_id = "legacy"
    else:
        run_id = path.parent.name

    label = manifest.get("label") or manifest.get("name") or run_id
    if run_id == "legacy":
        label = "legacy flat traces"

    return {
        "run_id": run_id,
        "run_label": label,
        "run_dir": str(path.parent),
        "run_created_at": manifest.get("created_at"),
        "run_manifest": manifest,
    }


def discover_trace_paths(trace_root: Path) -> list[Path]:
    if not trace_root.exists():
        return []
    return sorted(trace_root.rglob("*.jsonl"))


def summarize_inbound(event: dict[str, Any]) -> dict[str, Any]:
    tools = event.get("tool_names") or []
    tool_results = event.get("tool_results") or []
    return {
        "source": event.get("source"),
        "metadata": event.get("metadata") or {},
        "user_text": event.get("user_text") or "",
        "policy_preview": compact(event.get("policy_text"), 1200),
        "tool_count": len(tools),
        "tool_names": tools,
        "tool_results": tool_results,
        "parts": event.get("parts") or [],
    }


def summarize_step(events: list[dict[str, Any]], start_index: int, end_index: int) -> dict[str, Any]:
    chunk = events[start_index:end_index]
    inbound = chunk[0] if chunk else {}
    model_calls = [e for e in chunk if e.get("event") == "model_execute_python"]
    repl_results = [e for e in chunk if e.get("event") == "repl_result"]
    outbound = next((e for e in reversed(chunk) if e.get("event") == "outbound_a2a"), None)
    benchmark_ready = next((e for e in reversed(chunk) if e.get("event") == "benchmark_action_ready"), None)
    model_metrics = metrics_from_model_calls(model_calls)
    outbound_turn_metrics = metrics_from_turn_metrics((outbound or {}).get("metadata", {}).get("turn_metrics"))
    step_metrics = dict(model_metrics)
    if outbound_turn_metrics.get("num_llm_calls"):
        step_metrics["outbound_num_llm_calls"] = outbound_turn_metrics["num_llm_calls"]
        step_metrics["outbound_model_latency_ms"] = outbound_turn_metrics["model_latency_ms"]
        step_metrics["outbound_prompt_tokens"] = outbound_turn_metrics["prompt_tokens"]
        step_metrics["outbound_completion_tokens"] = outbound_turn_metrics["completion_tokens"]
        step_metrics["outbound_total_tokens"] = outbound_turn_metrics["total_tokens"]
        step_metrics["outbound_thinking_tokens"] = outbound_turn_metrics["thinking_tokens"]
        step_metrics["outbound_cost"] = outbound_turn_metrics["cost"]

    analyses = []
    input_summary = summarize_inbound(inbound)
    if input_summary["source"] == "user":
        analyses.append("Input is a user turn. The agent should either answer or emit tool calls needed to satisfy the request.")
    elif input_summary["source"] == "environment":
        analyses.append("Input is evaluator environment data, usually tool results from the previous agent action.")
    if input_summary["tool_results"]:
        analyses.append(f"Received {len(input_summary['tool_results'])} tool result(s).")
    if input_summary["tool_count"]:
        analyses.append(f"Available tool subset contains {input_summary['tool_count']} tool(s).")

    if model_calls:
        last_model = model_calls[-1]
        code = last_model.get("code") or ""
        thought = last_model.get("thought") or ""
        if code:
            analyses.append("Model chose to execute Python in the REPL.")
        if thought:
            analyses.append("Model thought: " + compact(thought, 500))
        analyses.append(
            f"Model call metrics in this step: {int(model_metrics['num_llm_calls'])} call(s), "
            f"{model_metrics['model_latency_ms']:.1f} ms, "
            f"{int(model_metrics['total_tokens'])} token(s)."
        )

    if repl_results:
        last_repl = repl_results[-1]
        if last_repl.get("error"):
            analyses.append("REPL error occurred: " + compact(last_repl["error"], 500))
        if last_repl.get("tool_calls"):
            analyses.append(f"REPL emitted {len(last_repl['tool_calls'])} benchmark tool call(s).")
        if last_repl.get("response_text"):
            analyses.append("REPL emitted a user-facing response.")

    if outbound:
        if outbound.get("tool_calls"):
            analyses.append("Output to evaluator is tool call data.")
        elif outbound.get("text"):
            analyses.append("Output to evaluator is text for the simulated user.")
        else:
            analyses.append("Output to evaluator has no text and no tool calls.")

    return {
        "index": len([e for e in events[:start_index] if e.get("event") == "inbound_a2a"]) + 1,
        "ts": inbound.get("ts"),
        "input": input_summary,
        "model_calls": model_calls,
        "repl_results": repl_results,
        "benchmark_ready": benchmark_ready,
        "outbound": outbound,
        "metrics": step_metrics,
        "events": chunk,
        "analysis": analyses,
    }


def summarize_trace(
    path: Path,
    trace_root: Path,
    task_match: dict[str, Any] | None = None,
) -> dict[str, Any]:
    events = read_jsonl(path)
    context_id = events[0].get("context_id") if events else path.stem
    first_user = first_user_from_trace(events)

    inbound_indexes = [i for i, event in enumerate(events) if event.get("event") == "inbound_a2a"]
    steps = []
    for pos, start_index in enumerate(inbound_indexes):
        end_index = inbound_indexes[pos + 1] if pos + 1 < len(inbound_indexes) else len(events)
        steps.append(summarize_step(events, start_index, end_index))

    session = next((e for e in events if e.get("event") == "session_start"), {})
    run_meta = run_meta_for_trace(trace_root, path, events)
    last_outbound = next((e for e in reversed(events) if e.get("event") == "outbound_a2a"), {})
    started_at = events[0].get("ts") if events else None
    ended_at = events[-1].get("ts") if events else None
    trace_metrics: dict[str, float] = {
        "task_wall_time_ms": elapsed_ms(started_at, ended_at),
        "num_llm_calls": 0.0,
        "model_latency_ms": 0.0,
        "prompt_tokens": 0.0,
        "completion_tokens": 0.0,
        "total_tokens": 0.0,
        "thinking_tokens": 0.0,
        "outbound_num_llm_calls": 0.0,
        "outbound_model_latency_ms": 0.0,
        "outbound_prompt_tokens": 0.0,
        "outbound_completion_tokens": 0.0,
        "outbound_total_tokens": 0.0,
        "outbound_thinking_tokens": 0.0,
        "outbound_cost": 0.0,
    }
    for step in steps:
        add_metrics(trace_metrics, step.get("metrics") or {})
    return {
        **run_meta,
        "context_id": context_id,
        "path": str(path),
        "file_mtime": path.stat().st_mtime if path.exists() else None,
        "event_count": len(events),
        "started_at": started_at,
        "ended_at": ended_at,
        "model": session.get("model"),
        "provider": session.get("provider"),
        "tool_mode": session.get("tool_mode"),
        "first_user": first_user,
        "matched_task_id": task_match.get("task_id") if task_match else None,
        "matched_task_index": task_match.get("task_index") if task_match else None,
        "matched_split": task_match.get("split") if task_match else None,
        "matched_trial": task_match.get("trial") if task_match else None,
        "matched_pass_number": task_match.get("pass_number") if task_match else None,
        "matched_reward": task_match.get("reward") if task_match else None,
        "final_text": last_outbound.get("text"),
        "final_tool_calls": last_outbound.get("tool_calls") or [],
        "metrics": trace_metrics,
        "steps": steps,
    }


def build_payload(trace_dir: Path, checkpoint_paths: list[str], active_window_minutes: float = 30.0) -> dict[str, Any]:
    tasks = load_checkpoints(checkpoint_paths)
    trace_root = trace_dir
    trace_paths = discover_trace_paths(trace_root)
    trace_events = {path: read_jsonl(path) for path in trace_paths}
    trace_first_users = {path: first_user_from_trace(events) for path, events in trace_events.items()}
    trace_run_ids = {
        path: run_meta_for_trace(trace_root, path, trace_events.get(path) or []).get("run_id")
        for path in trace_paths
    }
    checkpoint_match_run_id = None
    if trace_paths:
        checkpoint_match_run_id = trace_run_ids.get(
            max(trace_paths, key=lambda p: p.stat().st_mtime if p.exists() else 0)
        )

    tasks_by_first_user: dict[str, list[dict[str, Any]]] = {}
    for task in sorted(
        tasks,
        key=lambda t: (
            str(t.get("split") or ""),
            str(t.get("task_id") or ""),
            int(t.get("trial") or 0),
        ),
    ):
        tasks_by_first_user.setdefault(task.get("first_user") or "", []).append(task)

    trace_paths_by_run_and_first_user: dict[tuple[str, str], list[Path]] = {}
    for path in sorted(trace_paths, key=lambda p: p.stat().st_mtime):
        run_id = str(trace_run_ids.get(path) or "")
        first_user = trace_first_users.get(path) or ""
        trace_paths_by_run_and_first_user.setdefault((run_id, first_user), []).append(path)

    task_by_trace_path: dict[Path, dict[str, Any]] = {}
    for (run_id, first_user), paths in trace_paths_by_run_and_first_user.items():
        if checkpoint_match_run_id and run_id != checkpoint_match_run_id:
            continue
        candidates = tasks_by_first_user.get(first_user) or []
        for index, path in enumerate(paths):
            if index < len(candidates):
                task_by_trace_path[path] = candidates[index]

    traces = [summarize_trace(path, trace_root, task_by_trace_path.get(path)) for path in trace_paths]
    now_ts = datetime.now(timezone.utc).timestamp()
    active_context_by_run: dict[str, str] = {}
    for run_id in {str(trace.get("run_id") or "") for trace in traces}:
        unmatched_recent = [
            trace
            for trace in traces
            if str(trace.get("run_id") or "") == run_id
            and not trace.get("matched_task_id")
            and trace.get("file_mtime")
            and now_ts - float(trace["file_mtime"]) <= active_window_minutes * 60.0
        ]
        if unmatched_recent:
            active_context_by_run[run_id] = str(
                max(unmatched_recent, key=lambda t: float(t.get("file_mtime") or 0)).get("context_id")
            )
    for trace in traces:
        if trace.get("matched_task_id"):
            trace["status"] = "completed"
        elif trace.get("context_id") == active_context_by_run.get(str(trace.get("run_id") or "")):
            trace["status"] = "active"
        else:
            trace["status"] = "unmatched"

    runs_by_id: dict[str, dict[str, Any]] = {}
    for trace in traces:
        run_id = str(trace.get("run_id") or "unknown")
        run = runs_by_id.setdefault(
            run_id,
            {
                "run_id": run_id,
                "run_label": trace.get("run_label") or run_id,
                "run_dir": trace.get("run_dir"),
                "run_created_at": trace.get("run_created_at"),
                "trace_count": 0,
                "completed_count": 0,
                "active_count": 0,
                "unmatched_count": 0,
                "started_at": None,
                "ended_at": None,
                "models": [],
                "providers": [],
            },
        )
        run["trace_count"] += 1
        status_key = f"{trace.get('status') or 'unmatched'}_count"
        if status_key in run:
            run[status_key] += 1
        if trace.get("started_at") and (not run["started_at"] or str(trace["started_at"]) < str(run["started_at"])):
            run["started_at"] = trace["started_at"]
        if trace.get("ended_at") and (not run["ended_at"] or str(trace["ended_at"]) > str(run["ended_at"])):
            run["ended_at"] = trace["ended_at"]
        if trace.get("model") and trace["model"] not in run["models"]:
            run["models"].append(trace["model"])
        if trace.get("provider") and trace["provider"] not in run["providers"]:
            run["providers"].append(trace["provider"])
    runs = sorted(
        runs_by_id.values(),
        key=lambda run: str(run.get("started_at") or run.get("run_created_at") or ""),
        reverse=True,
    )
    return clean_json({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trace_dir": str(trace_root),
        "active_window_minutes": active_window_minutes,
        "checkpoint_match_run_id": checkpoint_match_run_id,
        "checkpoints": checkpoint_paths,
        "runs": runs,
        "tasks": tasks,
        "traces": traces,
    })


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CAR-bench Trace Explorer</title>
  <style>
    :root { --bg:#101214; --panel:#181c20; --panel2:#20262c; --text:#e8edf2; --muted:#9aa8b6; --ok:#59c36a; --bad:#ff6b6b; --warn:#f6c85f; --line:#303943; --blue:#77b7ff; }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--bg); color:var(--text); font:14px/1.45 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    header { padding:18px 22px; border-bottom:1px solid var(--line); background:linear-gradient(120deg,#18202a,#101214); position:sticky; top:0; z-index:5; }
    h1 { margin:0 0 6px; font-size:20px; }
    .meta { color:var(--muted); font-size:12px; }
    .layout { display:grid; grid-template-columns:360px 1fr; min-height:calc(100vh - 78px); }
    aside { border-right:1px solid var(--line); padding:14px; overflow:auto; max-height:calc(100vh - 78px); position:sticky; top:78px; }
    main { padding:18px; overflow:auto; }
    input, select { width:100%; padding:9px 10px; margin:0 0 10px; border:1px solid var(--line); border-radius:8px; background:#0d0f11; color:var(--text); }
    .task { border:1px solid var(--line); border-radius:10px; padding:10px; margin-bottom:10px; background:var(--panel); cursor:pointer; }
    .task:hover, .task.active { border-color:var(--blue); background:#1b2430; }
    .title { display:flex; gap:8px; align-items:center; justify-content:space-between; font-weight:650; }
    .pill { padding:2px 7px; border-radius:999px; font-size:12px; border:1px solid var(--line); color:var(--muted); white-space:nowrap; }
    .pass { color:var(--ok); border-color:rgba(89,195,106,.4); }
    .fail { color:var(--bad); border-color:rgba(255,107,107,.4); }
    .pending { color:var(--warn); border-color:rgba(246,200,95,.4); }
    .small { color:var(--muted); font-size:12px; margin-top:5px; }
    .panel { border:1px solid var(--line); background:var(--panel); border-radius:12px; padding:14px; margin-bottom:14px; }
    .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:10px; }
    .kv { background:var(--panel2); border-radius:9px; padding:10px; }
    .kv b { display:block; color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.05em; margin-bottom:4px; }
    details { border:1px solid var(--line); background:var(--panel); border-radius:10px; margin:10px 0; overflow:hidden; }
    summary { cursor:pointer; padding:11px 13px; background:var(--panel2); font-weight:650; }
    .details-body { padding:12px; }
    pre { white-space:pre-wrap; word-break:break-word; overflow:auto; background:#0c0f12; color:#d7e2ec; border:1px solid #29313a; border-radius:9px; padding:11px; max-height:420px; }
    code { color:#d7e2ec; }
    .step-head { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
    .analysis { margin:8px 0 0; padding-left:18px; color:#cbd5df; }
    .analysis li { margin:3px 0; }
    .two { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
    @media (max-width:900px) { .layout { grid-template-columns:1fr; } aside { position:static; max-height:none; border-right:0; border-bottom:1px solid var(--line); } .two { grid-template-columns:1fr; } }
  </style>
</head>
<body>
<header>
  <h1>CAR-bench Trace Explorer</h1>
  <div class="meta" id="generated"></div>
</header>
<div class="layout">
  <aside>
    <select id="runFilter"></select>
    <input id="filter" placeholder="Filter by task, context, user text, split...">
    <select id="mode">
      <option value="all">All traces and completed tasks</option>
      <option value="completed">Completed tasks only</option>
      <option value="active">Active task only</option>
      <option value="unmatched">Unmatched/stale traces only</option>
    </select>
    <div id="taskList"></div>
  </aside>
  <main id="main"></main>
</div>
<script id="payload" type="application/json">__PAYLOAD__</script>
<script>
const data = JSON.parse(document.getElementById('payload').textContent);
document.getElementById('generated').textContent = `Generated ${data.generated_at} from ${data.trace_dir}; checkpoint match run: ${data.checkpoint_match_run_id || 'none'}`;

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function pretty(x) { return esc(JSON.stringify(x, null, 2)); }
function num(x) { const n = Number(x || 0); return Number.isFinite(n) ? n : 0; }
function fmtMs(x) {
  const n = num(x);
  if (!n) return '0 ms';
  return n >= 1000 ? `${(n / 1000).toFixed(1)} s` : `${n.toFixed(1)} ms`;
}
function fmtTok(x) {
  const n = num(x);
  if (n >= 1000000) return `${(n / 1000000).toFixed(2)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return `${Math.round(n)}`;
}
function metricCards(metrics) {
  metrics = metrics || {};
  return `
    <div class="grid">
      ${metrics.task_wall_time_ms !== undefined ? `<div class="kv"><b>Task Wall Time</b>${fmtMs(metrics.task_wall_time_ms)}</div>` : ''}
      <div class="kv"><b>LLM Calls</b>${fmtTok(metrics.num_llm_calls)}</div>
      <div class="kv"><b>Model Latency</b>${fmtMs(metrics.model_latency_ms)}</div>
      <div class="kv"><b>Total Tokens</b>${fmtTok(metrics.total_tokens)}</div>
      <div class="kv"><b>Prompt Tokens</b>${fmtTok(metrics.prompt_tokens)}</div>
      <div class="kv"><b>Completion Tokens</b>${fmtTok(metrics.completion_tokens)}</div>
      <div class="kv"><b>Thinking Tokens</b>${fmtTok(metrics.thinking_tokens)}</div>
      ${metrics.a2a_turn_time_ms !== undefined ? `<div class="kv"><b>A2A Wall Time</b>${fmtMs(metrics.a2a_turn_time_ms)}</div>` : ''}
      ${metrics.a2a_effective_turn_time_ms !== undefined ? `<div class="kv"><b>A2A Effective</b>${fmtMs(metrics.a2a_effective_turn_time_ms)}</div>` : ''}
      ${metrics.latest_prompt_tokens !== undefined ? `<div class="kv"><b>Latest Prompt</b>${fmtTok(metrics.latest_prompt_tokens)}</div>` : ''}
      ${metrics.total_agent_cost !== undefined ? `<div class="kv"><b>Agent Cost</b>${num(metrics.total_agent_cost).toFixed(6)}</div>` : ''}
    </div>`;
}
function rewardPill(reward) {
  if (reward === null || reward === undefined) return '<span class="pill pending">live</span>';
  return Number(reward) > 0 ? `<span class="pill pass">${reward}</span>` : `<span class="pill fail">${reward}</span>`;
}
function statusPill(t) {
  if (t.status === 'completed') return rewardPill(t.matched_reward);
  if (t.status === 'active') return '<span class="pill pending">active</span>';
  return '<span class="pill">unmatched</span>';
}
function traceLabel(t) {
  const id = t.matched_task_id || t.context_id.slice(0, 8);
  const pass = t.matched_pass_number ? ` pass ${t.matched_pass_number}` : '';
  const split = t.matched_split ? `${t.matched_split} ` : '';
  return `${split}${id}${pass}`;
}
function taskKey(t) {
  return `${t.run_id || ''} ${t.run_label || ''} ${t.matched_task_id || 'live'} ${t.context_id} ${t.matched_split || ''} ${t.first_user || ''}`.toLowerCase();
}

const traces = data.traces.slice().sort((a,b) => String(b.started_at).localeCompare(String(a.started_at)));
const runs = data.runs || [];
let selected = traces[0]?.context_id;

function renderRunFilter() {
  const options = ['<option value="__all__">All runs</option>'].concat(runs.map(r => {
    const label = `${r.run_label || r.run_id} (${r.trace_count} traces, ${r.completed_count} completed, ${r.active_count} active)`;
    return `<option value="${esc(r.run_id)}">${esc(label)}</option>`;
  }));
  document.getElementById('runFilter').innerHTML = options.join('');
  if (runs[0]) document.getElementById('runFilter').value = runs[0].run_id;
}

function visibleTraces() {
  const q = document.getElementById('filter').value.toLowerCase();
  const mode = document.getElementById('mode').value;
  const runId = document.getElementById('runFilter').value;
  return traces.filter(t => {
    if (runId !== '__all__' && t.run_id !== runId) return false;
    if (mode === 'completed' && t.status !== 'completed') return false;
    if (mode === 'active' && t.status !== 'active') return false;
    if (mode === 'unmatched' && t.status !== 'unmatched') return false;
    return !q || taskKey(t).includes(q);
  });
}

function renderList() {
  const items = visibleTraces();
  if (items.length && !items.some(t => t.context_id === selected)) selected = items[0].context_id;
  document.getElementById('taskList').innerHTML = items.map(t => `
    <div class="task ${t.context_id === selected ? 'active' : ''}" onclick="selectTrace('${esc(t.context_id)}')">
      <div class="title"><span>${esc(traceLabel(t))}</span>${statusPill(t)}</div>
      <div class="small">Run: ${esc(t.run_label || t.run_id || '-')}</div>
      <div class="small">${esc(t.context_id)}</div>
      <div class="small">Status: ${esc(t.status || 'unknown')}</div>
      <div class="small">${esc((t.first_user || '').slice(0, 180))}</div>
      <div class="small">${t.steps.length} step(s), ${t.event_count} event(s)</div>
      <div class="small">Wall ${fmtMs(t.metrics?.task_wall_time_ms)} · Model ${fmtMs(t.metrics?.model_latency_ms)}</div>
      <div class="small">${fmtTok(t.metrics?.total_tokens)} tok · ${fmtTok(t.metrics?.num_llm_calls)} LLM calls</div>
    </div>`).join('') || '<div class="small">No traces match.</div>';
}

function renderTrajectory(task) {
  if (!task) return '<div class="small">No completed evaluator trajectory matched this trace yet.</div>';
  return `
    <details>
      <summary>Evaluator Trajectory</summary>
      <div class="details-body">
        ${(task.trajectory || []).map((m, i) => `
          <details>
            <summary>${i}. ${esc(m.role || 'message')} ${m.name ? esc(m.name) : ''}</summary>
            <div class="details-body"><pre>${pretty(m)}</pre></div>
          </details>`).join('')}
      </div>
    </details>`;
}

function renderStep(step) {
  const out = step.outbound || {};
  return `
    <details open>
      <summary>
        <div class="step-head">
          <span>Step ${step.index}</span>
          <span class="pill">${esc(step.input.source || 'unknown')}</span>
          <span class="pill">${step.input.tool_count} tools</span>
          <span class="pill">${step.input.tool_results.length} tool result(s)</span>
          <span class="pill">${fmtTok(step.metrics?.total_tokens)} tok</span>
          <span class="pill">${fmtMs(step.metrics?.model_latency_ms)}</span>
          <span class="pill">${fmtTok(step.metrics?.num_llm_calls)} calls</span>
          ${out.tool_calls?.length ? `<span class="pill">${out.tool_calls.length} outgoing call(s)</span>` : ''}
          ${out.text ? '<span class="pill">text response</span>' : ''}
        </div>
      </summary>
      <div class="details-body">
        <ul class="analysis">${(step.analysis || []).map(a => `<li>${esc(a)}</li>`).join('')}</ul>
        <h3>Step Metrics</h3>
        ${metricCards(step.metrics)}
        <details>
          <summary>Raw Step Metrics</summary>
          <div class="details-body"><pre>${pretty(step.metrics)}</pre></div>
        </details>
        <div class="two">
          <div>
            <h3>Input</h3>
            <pre>${pretty(step.input)}</pre>
          </div>
          <div>
            <h3>Output</h3>
            <pre>${pretty({benchmark_ready: step.benchmark_ready, outbound: step.outbound})}</pre>
          </div>
        </div>
        <details>
          <summary>Model Execute Python Calls (${step.model_calls.length})</summary>
          <div class="details-body">${step.model_calls.map(c => `<pre>${pretty(c)}</pre>`).join('')}</div>
        </details>
        <details>
          <summary>REPL Results (${step.repl_results.length})</summary>
          <div class="details-body">${step.repl_results.map(r => `<pre>${pretty(r)}</pre>`).join('')}</div>
        </details>
        <details>
          <summary>Raw Events (${step.events.length})</summary>
          <div class="details-body"><pre>${pretty(step.events)}</pre></div>
        </details>
      </div>
    </details>`;
}

function renderTrace(t) {
  const task = data.tasks.find(x => x.task_id === t.matched_task_id && x.trial === t.matched_trial && x.first_user === t.first_user);
  const run = runs.find(r => r.run_id === t.run_id);
  document.getElementById('main').innerHTML = `
    <section class="panel">
      <h2>${esc(traceLabel(t))}</h2>
      <div class="grid">
        <div class="kv"><b>Run</b>${esc(t.run_label || t.run_id || '-')}</div>
        <div class="kv"><b>Run Dir</b>${esc(t.run_dir || '-')}</div>
        <div class="kv"><b>Context</b>${esc(t.context_id)}</div>
        <div class="kv"><b>Task</b>${esc(t.matched_task_id || 'unmatched/live')}</div>
        <div class="kv"><b>Status</b>${esc(t.status || 'unknown')}</div>
        <div class="kv"><b>Split</b>${esc(t.matched_split || '-')}</div>
        <div class="kv"><b>Pass / Trial</b>${esc(t.matched_pass_number ?? '-')} / ${esc(t.matched_trial ?? '-')}</div>
        <div class="kv"><b>Reward</b>${esc(t.matched_reward ?? 'pending')}</div>
        <div class="kv"><b>Model</b>${esc(t.provider)} / ${esc(t.model)}</div>
        <div class="kv"><b>Trace Path</b>${esc(t.path)}</div>
        <div class="kv"><b>Started</b>${esc(t.started_at || '-')}</div>
        <div class="kv"><b>Ended</b>${esc(t.ended_at || '-')}</div>
      </div>
      ${run ? `<details><summary>Run Summary</summary><div class="details-body"><pre>${pretty(run)}</pre></div></details>` : ''}
      <h3>Trace Metrics</h3>
      <div class="small">Task Wall Time is derived from first trace event to last trace event. Model Latency is only summed LLM call latency.</div>
      ${metricCards(t.metrics)}
      <h3>First User Message</h3>
      <pre>${esc(t.first_user || '')}</pre>
      ${task ? `
        <h3>Evaluator Task Metrics</h3>${metricCards(task.metrics)}
        <details><summary>Raw Evaluator Task Metrics</summary><div class="details-body"><pre>${pretty(task.metrics)}</pre></div></details>
        <h3>Task Instruction</h3><pre>${esc(task.instruction || '')}</pre>
        <h3>Expected Actions</h3><pre>${pretty(task.expected_actions)}</pre>
        <h3>Reward Info</h3><pre>${pretty(task.reward_info)}</pre>
      ` : ''}
    </section>
    <section class="panel">
      <h2>Steps</h2>
      ${t.steps.map(renderStep).join('')}
    </section>
    <section class="panel">
      <h2>Completed Evaluator Data</h2>
      ${renderTrajectory(task)}
    </section>`;
}

window.selectTrace = function(id) {
  selected = id;
  renderList();
  const t = traces.find(x => x.context_id === id);
  if (t) renderTrace(t);
};
document.getElementById('filter').addEventListener('input', renderList);
document.getElementById('mode').addEventListener('change', renderList);
document.getElementById('runFilter').addEventListener('change', () => {
  renderList();
  const t = traces.find(x => x.context_id === selected);
  if (t) renderTrace(t);
});
renderRunFilter();
renderList();
if (traces.length) renderTrace(traces.find(t => t.context_id === selected) || traces[0]);
</script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-dir", default="run_logs/car_agent")
    parser.add_argument("--output", default="output/trace_explorer.html")
    parser.add_argument("--checkpoint", action="append", default=[])
    parser.add_argument("--active-window-minutes", type=float, default=30.0)
    args = parser.parse_args()

    checkpoints = args.checkpoint or DEFAULT_CHECKPOINTS
    payload = build_payload(Path(args.trace_dir), checkpoints, args.active_window_minutes)
    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html_text = HTML_TEMPLATE.replace("__PAYLOAD__", payload_json)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html_text)
    print(
        f"Wrote {output} with {len(payload['runs'])} run(s), "
        f"{len(payload['traces'])} trace(s), and {len(payload['tasks'])} completed task row(s)."
    )


if __name__ == "__main__":
    main()
