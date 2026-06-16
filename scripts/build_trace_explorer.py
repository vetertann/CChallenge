#!/usr/bin/env python3
"""Build a single HTML catalog of CAR-bench eval runs.

The durable source of truth is the per-run result files written by the
evaluator to ``output/<agent>/<timestamp>__<scenario>__...json``. Each file is
one completed run (metadata + summary + per-task detailed results). This script
catalogs those runs and links each task to its REPL trace under
``run_logs/car_agent/<run_dir>/<context_id>.jsonl`` when available.

It deliberately does NOT read the transient cumulative ``/tmp`` checkpoints.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
                {"event": "jsonl_parse_error", "line_no": line_no, "error": str(exc), "raw": line[:2000]}
            )
    return events


def compact(value: Any, max_len: int = 600) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    value = " ".join(value.split())
    return value if len(value) <= max_len else value[: max_len - 12] + " ...[truncated]"


def clean_json(value: Any) -> Any:
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else None
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean_json(v) for v in value]
    return value


def slim(value: Any, max_str: int = 800) -> Any:
    """Recursively truncate long strings so embedded blobs stay small."""
    if isinstance(value, str):
        return value if len(value) <= max_str else value[: max_str - 12] + " ...[trunc]"
    if isinstance(value, dict):
        return {k: slim(v, max_str) for k, v in value.items()}
    if isinstance(value, list):
        return [slim(v, max_str) for v in value]
    return value


def number(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    return value if value == value and value not in (float("inf"), float("-inf")) else 0.0


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
    a, b = parse_ts(start), parse_ts(end)
    if not a or not b:
        return 0.0
    return max(0.0, (b - a).total_seconds() * 1000.0)


# --------------------------------------------------------------------------- #
# Trace (REPL) parsing — per context_id jsonl under run_logs                   #
# --------------------------------------------------------------------------- #


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
    for msg in traj or []:
        if msg.get("role") == "user":
            content = msg.get("content") or ""
            if content and content != "###STOP###":
                return content
    return ""


def summarize_step(events: list[dict[str, Any]], start_index: int, end_index: int) -> dict[str, Any]:
    chunk = events[start_index:end_index]
    inbound = chunk[0] if chunk else {}
    model_calls = [e for e in chunk if e.get("event") == "model_execute_python"]
    repl_results = [e for e in chunk if e.get("event") == "repl_result"]
    outbound = next((e for e in reversed(chunk) if e.get("event") == "outbound_a2a"), None)

    latency = sum(number(c.get("elapsed_ms")) for c in model_calls)
    tokens = sum(number((c.get("usage") or {}).get("total_tokens")) for c in model_calls)

    analyses: list[str] = []
    source = inbound.get("source")
    if source == "user":
        analyses.append("Input is a user turn.")
    elif source == "environment":
        analyses.append("Input is evaluator environment data (tool results).")
    if inbound.get("tool_results"):
        analyses.append(f"Received {len(inbound['tool_results'])} tool result(s).")
    if model_calls:
        last = model_calls[-1]
        if last.get("thought"):
            analyses.append("Model thought: " + compact(last["thought"], 400))
    if repl_results:
        last = repl_results[-1]
        if last.get("error"):
            analyses.append("REPL error: " + compact(last["error"], 300))
        if last.get("tool_calls"):
            names = [tc.get("tool_name") for tc in last["tool_calls"]]
            analyses.append(f"Emitted {len(names)} tool call(s): {', '.join(n for n in names if n)}")
        if last.get("response_text"):
            analyses.append("Emitted a user-facing response.")
    if outbound:
        if outbound.get("tool_calls"):
            analyses.append("Output to evaluator is tool-call data.")
        elif outbound.get("text"):
            analyses.append("Output to evaluator is text for the simulated user.")

    return {
        "index": len([e for e in events[:start_index] if e.get("event") == "inbound_a2a"]) + 1,
        "source": source,
        "tool_count": inbound.get("tool_count") or len(inbound.get("tool_names") or []),
        "tool_result_count": len(inbound.get("tool_results") or []),
        "user_text": inbound.get("user_text") or "",
        "model_calls": [
            {
                "step_index": c.get("step_index"),
                "retry": c.get("retry"),
                "elapsed_ms": number(c.get("elapsed_ms")),
                "thought": c.get("thought") or "",
                "code": c.get("code") or "",
                "usage": c.get("usage") or {},
            }
            for c in model_calls
        ],
        "repl_results": [
            {
                "stdout": compact(r.get("stdout"), 800),
                "error": r.get("error"),
                "tool_calls": r.get("tool_calls") or [],
                "response_text": r.get("response_text"),
            }
            for r in repl_results
        ],
        "outbound": {
            "text": (outbound or {}).get("text"),
            "tool_calls": (outbound or {}).get("tool_calls") or [],
        }
        if outbound
        else None,
        "metrics": {"model_latency_ms": latency, "total_tokens": tokens, "num_llm_calls": float(len(model_calls))},
        "analysis": analyses,
    }


def summarize_trace(path: Path) -> dict[str, Any]:
    events = read_jsonl(path)
    inbound_idx = [i for i, e in enumerate(events) if e.get("event") == "inbound_a2a"]
    steps = []
    for pos, start in enumerate(inbound_idx):
        end = inbound_idx[pos + 1] if pos + 1 < len(inbound_idx) else len(events)
        steps.append(summarize_step(events, start, end))
    session = next((e for e in events if e.get("event") == "session_start"), {})
    metrics = {
        "task_wall_time_ms": elapsed_ms(events[0].get("ts") if events else None, events[-1].get("ts") if events else None),
        "model_latency_ms": sum(s["metrics"]["model_latency_ms"] for s in steps),
        "total_tokens": sum(s["metrics"]["total_tokens"] for s in steps),
        "num_llm_calls": sum(s["metrics"]["num_llm_calls"] for s in steps),
    }
    return {
        "context_id": events[0].get("context_id") if events else path.stem,
        "path": str(path),
        "model": session.get("model"),
        "provider": session.get("provider"),
        "first_user": first_user_from_trace(events),
        "event_count": len(events),
        "metrics": metrics,
        "steps": steps,
    }


# --------------------------------------------------------------------------- #
# Run (output/*.json) parsing                                                  #
# --------------------------------------------------------------------------- #


def _skill_from_cmd(cmd: str) -> str:
    m = re.search(r"--skill\s+(\S+)", cmd or "")
    return m.group(1) if m else "car_domain.md"


def _agent_llm_from_cmd(cmd: str) -> str:
    m = re.search(r"--agent-llm\s+(\S+)", cmd or "")
    return m.group(1) if m else ""


def task_metrics_from_row(row: dict[str, Any]) -> dict[str, float]:
    return {
        "input_tokens": number(row.get("agent_input_tokens") or row.get("agent_prompt_tokens")),
        "output_tokens": number(row.get("agent_output_tokens") or row.get("agent_completion_tokens")),
        "total_tokens": number(row.get("agent_total_tokens")),
        "thinking_tokens": number(row.get("agent_thinking_tokens")),
        "llm_latency_ms": number(row.get("total_llm_latency_ms")),
        "a2a_time_ms": number(row.get("total_a2a_time_ms")),
        "num_a2a_turns": number(row.get("num_a2a_turns")),
        "cost": number(row.get("total_agent_cost")),
    }


def parse_run_file(path: Path) -> dict[str, Any] | None:
    data = json.loads(path.read_text(errors="replace"))
    if not isinstance(data, dict) or "metadata" not in data or "final_result" not in data:
        return None  # not an evaluator run-result file
    meta = data.get("metadata", {}) or {}
    final = data.get("final_result", {}) or {}
    cmd = (meta.get("agent_metadata", {}) or {}).get("cmd", "") or ""
    config = meta.get("config", {}) or {}

    tasks: list[dict[str, Any]] = []
    by_split = final.get("detailed_results_by_split", {}) or {}
    for split, rows in by_split.items():
        for row in rows or []:
            task = row.get("task", {}) or {}
            traj = row.get("trajectory") or row.get("traj") or []
            tasks.append(
                {
                    "split": split,
                    "task_id": row.get("task_id") or task.get("task_id"),
                    "trial": int(number(row.get("trial"))),
                    "reward": row.get("reward"),
                    "error": row.get("error"),
                    "first_user": first_user_from_traj(traj),
                    "instruction": task.get("instruction"),
                    "expected_actions": task.get("actions") or [],
                    "reward_info": row.get("reward_info") or {},
                    "metrics": task_metrics_from_row(row),
                    "trajectory": slim(traj),
                }
            )

    pass_by_split = {}
    for split, info in (final.get("pass_at_k_scores_by_split", {}) or {}).items():
        rewards = (final.get("task_rewards_by_split", {}) or {}).get(split, {})
        pass_by_split[split] = {
            "pass_at_k": info,
            "pass_power_k": (final.get("pass_power_k_scores_by_split", {}) or {}).get(split, {}),
            "task_count": len(rewards) if isinstance(rewards, (dict, list)) else None,
        }

    return {
        "run_key": path.stem,
        "file": str(path),
        "scenario_name": meta.get("scenario_name"),
        "scenario_path": meta.get("scenario_path"),
        "model": meta.get("model") or _agent_llm_from_cmd(cmd),
        "skill": _skill_from_cmd(cmd),
        "reasoning_effort": meta.get("reasoning_effort"),
        "task_split": config.get("task_split"),
        "num_trials": config.get("num_trials"),
        "task_selection": meta.get("task_selection"),
        "started_at": meta.get("started_at"),
        "completed_at": meta.get("completed_at"),
        "wall_time_seconds": number(meta.get("wall_time_seconds")),
        "score": number(final.get("score")),
        "max_score": number(final.get("max_score")),
        "pass_rate": number(final.get("pass_rate")),
        "pass_at_k": final.get("pass_at_k_scores", {}) or {},
        "pass_power_k": final.get("pass_power_k_scores", {}) or {},
        "pass_by_split": pass_by_split,
        "tasks": tasks,
    }


def _log_dir_time(d: Path) -> datetime | None:
    manifest = d / ".run.json"
    if manifest.exists():
        try:
            created = json.loads(manifest.read_text(errors="replace")).get("created_at")
            if parse_ts(created):
                return parse_ts(created)
        except json.JSONDecodeError:
            pass
    m = re.match(r"run_(\d{8}T\d{6})Z", d.name)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def match_log_dir(started_at: Any, trace_root: Path, tolerance_s: float = 300.0) -> Path | None:
    target = parse_ts(started_at)
    if not target or not trace_root.exists():
        return None
    best, best_diff = None, None
    for d in trace_root.iterdir():
        if not d.is_dir():
            continue
        created = _log_dir_time(d)
        if not created:
            continue
        diff = abs((created - target).total_seconds())
        if best_diff is None or diff < best_diff:
            best, best_diff = d, diff
    return best if best and best_diff is not None and best_diff <= tolerance_s else None


def attach_traces(run: dict[str, Any], trace_root: Path) -> None:
    log_dir = match_log_dir(run.get("started_at"), trace_root)
    run["run_log_dir"] = str(log_dir) if log_dir else None
    if not log_dir:
        return
    paths_by_first_user: dict[str, list[Path]] = {}
    for path in sorted(log_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime):
        events = read_jsonl(path)
        paths_by_first_user.setdefault(first_user_from_trace(events), []).append(path)
    used: dict[str, int] = {}
    for task in run["tasks"]:
        fu = task.get("first_user") or ""
        candidates = paths_by_first_user.get(fu) or []
        idx = used.get(fu, 0)
        if idx < len(candidates):
            trace = summarize_trace(candidates[idx])
            for step in trace["steps"]:
                step.pop("events", None)
            task["trace"] = trace
            used[fu] = idx + 1
        else:
            task["trace"] = None


def build_incomplete_runs(trace_dir: Path, matched_dirs: set[str]) -> list[dict[str, Any]]:
    """Reconstruct runs from run_logs dirs that have no evaluator result file
    (e.g. killed or in-progress runs). Rewards are unavailable; traces are shown."""
    runs: list[dict[str, Any]] = []
    if not trace_dir.exists():
        return runs
    for d in sorted(trace_dir.iterdir()):
        if not d.is_dir() or str(d) in matched_dirs:
            continue
        trace_paths = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        if not trace_paths:
            continue
        manifest = {}
        if (d / ".run.json").exists():
            try:
                manifest = json.loads((d / ".run.json").read_text(errors="replace")) or {}
            except json.JSONDecodeError:
                manifest = {}
        model = provider = None
        tasks: list[dict[str, Any]] = []
        for path in trace_paths:
            tr = summarize_trace(path)
            model = model or tr.get("model")
            provider = provider or tr.get("provider")
            tasks.append(
                {
                    "split": "live",
                    "task_id": (tr.get("context_id") or path.stem)[:8],
                    "trial": 0,
                    "reward": None,
                    "error": None,
                    "first_user": tr.get("first_user"),
                    "instruction": None,
                    "expected_actions": [],
                    "reward_info": {},
                    "metrics": {
                        "total_tokens": tr["metrics"]["total_tokens"],
                        "num_a2a_turns": float(len(tr["steps"])),
                        "model_latency_ms": tr["metrics"]["model_latency_ms"],
                    },
                    "trajectory": [],
                    "trace": tr,
                }
            )
        created = _log_dir_time(d)
        runs.append(
            {
                "run_key": d.name,
                "file": None,
                "scenario_name": manifest.get("label") or manifest.get("name") or d.name,
                "model": model or manifest.get("model"),
                "skill": manifest.get("skill") or "?",
                "reasoning_effort": None,
                "task_split": None,
                "num_trials": None,
                "task_selection": None,
                "started_at": manifest.get("created_at") or (created.isoformat() if created else None),
                "completed_at": None,
                "wall_time_seconds": 0.0,
                "score": None,
                "max_score": None,
                "pass_rate": None,
                "pass_at_k": {},
                "pass_power_k": {},
                "pass_by_split": {},
                "tasks": tasks,
                "run_log_dir": str(d),
                "detail_omitted": 0,
                "incomplete": True,
            }
        )
    return runs


def build_payload(
    output_dir: Path,
    trace_dir: Path,
    max_detail_tasks: int = 80,
    include_incomplete: bool = True,
) -> dict[str, Any]:
    run_files = sorted(output_dir.rglob("*.json"))
    runs: list[dict[str, Any]] = []
    for path in run_files:
        try:
            run = parse_run_file(path)
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
        if run is None:
            continue
        attach_traces(run, trace_dir)
        # Large full-set runs keep their scoreboard + per-task reward_info, but
        # drop embedded REPL steps / trajectories so the single HTML stays light.
        # Step-level drill-in is preserved for smaller, targeted runs.
        if len(run["tasks"]) > max_detail_tasks:
            run["detail_omitted"] = len(run["tasks"])
            for task in run["tasks"]:
                task["trace"] = None
                task["trajectory"] = []
        else:
            run["detail_omitted"] = 0
        run["incomplete"] = False
        runs.append(run)
    if include_incomplete:
        matched_dirs = {r["run_log_dir"] for r in runs if r.get("run_log_dir")}
        runs.extend(build_incomplete_runs(trace_dir, matched_dirs))
    runs.sort(key=lambda r: str(r.get("started_at") or ""), reverse=True)
    return clean_json(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "output_dir": str(output_dir),
            "trace_dir": str(trace_dir),
            "run_count": len(runs),
            "runs": runs,
        }
    )


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CAR-bench Run Catalog</title>
<style>
  :root { --bg:#101214; --panel:#181c20; --panel2:#20262c; --text:#e8edf2; --muted:#9aa8b6; --ok:#59c36a; --bad:#ff6b6b; --warn:#f6c85f; --line:#303943; --blue:#77b7ff; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font:14px/1.45 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }
  header { padding:16px 22px; border-bottom:1px solid var(--line); background:linear-gradient(120deg,#18202a,#101214); }
  h1 { margin:0 0 4px; font-size:19px; }
  h2 { font-size:16px; margin:0 0 10px; }
  h3 { font-size:13px; margin:16px 0 6px; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; }
  .meta { color:var(--muted); font-size:12px; }
  .layout { display:grid; grid-template-columns:380px 1fr; min-height:calc(100vh - 70px); }
  aside { border-right:1px solid var(--line); padding:12px; overflow:auto; max-height:calc(100vh - 70px); }
  main { padding:18px; overflow:auto; max-height:calc(100vh - 70px); }
  input { width:100%; padding:8px 10px; margin:0 0 10px; border:1px solid var(--line); border-radius:8px; background:#0d0f11; color:var(--text); }
  .run { border:1px solid var(--line); border-radius:10px; padding:10px; margin-bottom:9px; background:var(--panel); cursor:pointer; }
  .run:hover, .run.active { border-color:var(--blue); background:#1b2430; }
  .row { display:flex; gap:8px; align-items:center; justify-content:space-between; }
  .name { font-weight:650; font-size:13px; word-break:break-word; }
  .small { color:var(--muted); font-size:12px; margin-top:3px; }
  .pill { padding:2px 7px; border-radius:999px; font-size:11px; border:1px solid var(--line); color:var(--muted); white-space:nowrap; }
  .pass { color:var(--ok); border-color:rgba(89,195,106,.4); }
  .fail { color:var(--bad); border-color:rgba(255,107,107,.4); }
  .mid { color:var(--warn); border-color:rgba(246,200,95,.4); }
  .panel { border:1px solid var(--line); background:var(--panel); border-radius:12px; padding:14px; margin-bottom:14px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:9px; }
  .kv { background:var(--panel2); border-radius:9px; padding:9px; }
  .kv b { display:block; color:var(--muted); font-size:10px; text-transform:uppercase; letter-spacing:.05em; margin-bottom:3px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:7px 9px; border-bottom:1px solid var(--line); }
  th { color:var(--muted); font-size:11px; text-transform:uppercase; }
  tr.tk { cursor:pointer; }
  tr.tk:hover, tr.tk.active { background:#1b2430; }
  details { border:1px solid var(--line); background:var(--panel); border-radius:10px; margin:9px 0; overflow:hidden; }
  summary { cursor:pointer; padding:9px 12px; background:var(--panel2); font-weight:600; }
  .db { padding:11px; }
  pre { white-space:pre-wrap; word-break:break-word; overflow:auto; background:#0c0f12; color:#d7e2ec; border:1px solid #29313a; border-radius:9px; padding:10px; max-height:380px; }
  .two { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
  .step-head { display:flex; flex-wrap:wrap; gap:7px; align-items:center; }
  ul.an { margin:7px 0 0; padding-left:18px; color:#cbd5df; }
  @media (max-width:920px){ .layout{grid-template-columns:1fr;} aside{max-height:none;border-right:0;border-bottom:1px solid var(--line);} .two{grid-template-columns:1fr;} }
</style>
</head>
<body>
<header>
  <h1>CAR-bench Run Catalog</h1>
  <div class="meta" id="gen"></div>
</header>
<div class="layout">
  <aside>
    <input id="filter" placeholder="Filter runs by model, skill, scenario, split...">
    <div id="runList"></div>
  </aside>
  <main id="main"></main>
</div>
<script id="payload" type="application/json">__PAYLOAD__</script>
<script>
const data = JSON.parse(document.getElementById('payload').textContent);
document.getElementById('gen').textContent = `Generated ${data.generated_at} · ${data.run_count} run(s) from ${data.output_dir}`;
const runs = data.runs || [];
let selRun = runs[0]?.run_key, selTask = null;

function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function pretty(x){return esc(JSON.stringify(x,null,2));}
function num(x){const n=Number(x||0);return Number.isFinite(n)?n:0;}
function pct(x){return `${(num(x)*100).toFixed(1)}%`;}
function fmtMs(x){const n=num(x);return n>=1000?`${(n/1000).toFixed(1)} s`:`${n.toFixed(0)} ms`;}
function fmtTok(x){const n=num(x);return n>=1000?`${(n/1000).toFixed(1)}k`:`${Math.round(n)}`;}
function rewardCls(r){if(r===null||r===undefined)return 'mid';return num(r)>=0.99?'pass':'fail';}
function rdate(s){return s?String(s).replace('T',' ').replace(/\\..*/,'').replace('+00:00','Z'):'-';}

function runPass(r){const p1=r.pass_at_k&&r.pass_at_k['Pass@1'];return p1!==undefined?p1:r.pass_rate;}
function runFilterKey(r){return `${r.model} ${r.skill} ${r.scenario_name} ${r.task_split} ${r.task_selection} ${r.run_key}`.toLowerCase();}

function renderRunList(){
  const q=document.getElementById('filter').value.toLowerCase();
  const items=runs.filter(r=>!q||runFilterKey(r).includes(q));
  if(items.length&&!items.some(r=>r.run_key===selRun))selRun=items[0].run_key;
  document.getElementById('runList').innerHTML=items.map(r=>{
    const pr=runPass(r);
    return `<div class="run ${r.run_key===selRun?'active':''}" onclick="selectRun('${esc(r.run_key)}')">
      <div class="row"><span class="name">${esc(r.model||'?')}</span><span class="pill ${r.incomplete?'mid':rewardCls(pr)}">${r.incomplete?'incomplete':pct(pr)}</span></div>
      <div class="small">skill: ${esc(r.skill)} · ${esc(r.task_split)} · ${esc(r.task_selection||'')}</div>
      <div class="small">${esc(r.scenario_name||'')}</div>
      <div class="small">${rdate(r.started_at)} · ${num(r.wall_time_seconds).toFixed(0)}s · ${r.tasks.length} task(s)</div>
    </div>`;
  }).join('')||'<div class="small">No runs match.</div>';
}

function metricCards(m){m=m||{};return `<div class="grid">
  ${m.total_tokens!==undefined?`<div class="kv"><b>Total Tokens</b>${fmtTok(m.total_tokens)}</div>`:''}
  ${m.input_tokens!==undefined?`<div class="kv"><b>Input Tokens</b>${fmtTok(m.input_tokens)}</div>`:''}
  ${m.output_tokens!==undefined?`<div class="kv"><b>Output Tokens</b>${fmtTok(m.output_tokens)}</div>`:''}
  ${m.thinking_tokens!==undefined?`<div class="kv"><b>Thinking</b>${fmtTok(m.thinking_tokens)}</div>`:''}
  ${m.llm_latency_ms!==undefined?`<div class="kv"><b>LLM Latency</b>${fmtMs(m.llm_latency_ms)}</div>`:''}
  ${m.a2a_time_ms!==undefined?`<div class="kv"><b>A2A Time</b>${fmtMs(m.a2a_time_ms)}</div>`:''}
  ${m.num_a2a_turns!==undefined?`<div class="kv"><b>A2A Turns</b>${fmtTok(m.num_a2a_turns)}</div>`:''}
  ${m.model_latency_ms!==undefined?`<div class="kv"><b>Model Latency</b>${fmtMs(m.model_latency_ms)}</div>`:''}
</div>`;}

function renderStep(s){
  const out=s.outbound||{};
  return `<details><summary><div class="step-head">
      <span>Turn ${s.index}</span>
      <span class="pill">${esc(s.source||'?')}</span>
      <span class="pill">${s.tool_count} tools</span>
      <span class="pill">${s.tool_result_count} result(s)</span>
      <span class="pill">${fmtTok(s.metrics.total_tokens)} tok</span>
      <span class="pill">${fmtMs(s.metrics.model_latency_ms)}</span>
      ${out.tool_calls&&out.tool_calls.length?`<span class="pill">${out.tool_calls.length} call(s) out</span>`:''}
      ${out.text?'<span class="pill">text</span>':''}
    </div></summary>
    <div class="db">
      ${s.user_text?`<h3>${s.source==='environment'?'Environment input':'User input'}</h3><pre>${esc(s.user_text)}</pre>`:(s.source==='environment'?`<div class="small">Environment turn · ${s.tool_result_count} tool result(s)</div>`:'')}
      <ul class="an">${(s.analysis||[]).map(a=>`<li>${esc(a)}</li>`).join('')}</ul>
      ${(s.model_calls||[]).map(c=>`<div class="two">
          <div><h3>Thought (step ${esc(c.step_index)}, ${fmtMs(c.elapsed_ms)})</h3><pre>${esc(c.thought||'(none)')}</pre></div>
          <div><h3>Code</h3><pre>${esc(c.code||'(none)')}</pre></div>
        </div>`).join('')}
      ${(s.repl_results||[]).map(r=>`<details><summary>REPL result${r.error?' · ERROR':''}</summary><div class="db"><pre>${pretty(r)}</pre></div></details>`).join('')}
      <details><summary>Outbound to evaluator</summary><div class="db"><pre>${pretty(out)}</pre></div></details>
    </div></details>`;
}

function renderTask(run,task){
  if(!task)return '<div class="small">Select a task.</div>';
  const tr=task.trace;
  return `<section class="panel">
    <h2>${esc(task.task_id)} · ${esc(task.split)} · trial ${task.trial} ${task.reward!==null&&task.reward!==undefined?`<span class="pill ${rewardCls(task.reward)}">reward ${task.reward}</span>`:''}</h2>
    <h3>Task Metrics</h3>${metricCards(task.metrics)}
    <h3>First User Message</h3><pre>${esc(task.first_user||'(none captured)')}</pre>
    ${task.instruction?`<h3>Task Instruction (evaluator)</h3><pre>${esc(task.instruction)}</pre>`:''}
    ${Object.keys(task.reward_info||{}).length?`<h3>Reward Info</h3><pre>${pretty(task.reward_info)}</pre>`:''}
    <details><summary>Expected Actions (${(task.expected_actions||[]).length})</summary><div class="db"><pre>${pretty(task.expected_actions)}</pre></div></details>
    ${task.error?`<h3>Error</h3><pre>${esc(task.error)}</pre>`:''}
  </section>
  <section class="panel">
    <h2>REPL Trace ${tr?`· ${esc(tr.context_id)}`:''}</h2>
    ${tr?`<div class="small">${tr.steps.length} turn(s) · ${fmtTok(tr.metrics.total_tokens)} tok · ${fmtMs(tr.metrics.model_latency_ms)} model latency · ${fmtMs(tr.metrics.task_wall_time_ms)} wall</div>
      ${tr.steps.map(renderStep).join('')}`
    :'<div class="small">No matched REPL trace for this task (run_logs dir not found or first-user text did not match).</div>'}
  </section>
  <section class="panel">
    <h2>Evaluator Trajectory</h2>
    <details><summary>${(task.trajectory||[]).length} message(s)</summary><div class="db">
      ${(task.trajectory||[]).map((m,i)=>`<details><summary>${i}. ${esc(m.role||'msg')} ${esc(m.name||'')}</summary><div class="db"><pre>${pretty(m)}</pre></div></details>`).join('')}
    </div></details>
  </section>`;
}

function renderRun(){
  const run=runs.find(r=>r.run_key===selRun);
  if(!run){document.getElementById('main').innerHTML='<div class="small">No run selected.</div>';return;}
  if(!run.tasks.some(t=>taskKey(t)===selTask))selTask=run.tasks[0]?taskKey(run.tasks[0]):null;
  const task=run.tasks.find(t=>taskKey(t)===selTask);
  const splits=Object.keys(run.pass_by_split||{});
  document.getElementById('main').innerHTML=`
    <section class="panel">
      <h2>${esc(run.model||'?')} — ${esc(run.scenario_name||'')}</h2>
      <div class="grid">
        <div class="kv"><b>Skill</b>${esc(run.skill)}</div>
        <div class="kv"><b>Split</b>${esc(run.task_split)}</div>
        <div class="kv"><b>Trials</b>${esc(run.num_trials)}</div>
        <div class="kv"><b>Selection</b>${esc(run.task_selection||'-')}</div>
        <div class="kv"><b>Score</b>${num(run.score).toFixed(1)} / ${num(run.max_score).toFixed(0)}</div>
        <div class="kv"><b>Pass Rate</b>${pct(run.pass_rate)}</div>
        <div class="kv"><b>Started</b>${rdate(run.started_at)}</div>
        <div class="kv"><b>Wall</b>${num(run.wall_time_seconds).toFixed(1)}s</div>
        <div class="kv"><b>Reasoning</b>${esc(run.reasoning_effort||'-')}</div>
      </div>
      <h3>Pass@k / Pass^k</h3>
      <div class="grid">
        ${Object.entries(run.pass_at_k||{}).map(([k,v])=>`<div class="kv"><b>${esc(k)}</b>${pct(v)}</div>`).join('')}
        ${Object.entries(run.pass_power_k||{}).map(([k,v])=>`<div class="kv"><b>${esc(k)}</b>${pct(v)}</div>`).join('')}
      </div>
      ${splits.length>1?`<h3>By Split</h3><div class="grid">${splits.map(s=>`<div class="kv"><b>${esc(s)}</b>${pct((run.pass_by_split[s].pass_at_k||{})['Pass@1'])}</div>`).join('')}</div>`:''}
      ${run.incomplete?`<div class="small" style="margin-top:8px">⚠ Incomplete run reconstructed from run_logs traces only (no evaluator result file — killed or in progress). Rewards / pass rates are unavailable; the REPL traces below are shown.</div>`:''}
      ${run.detail_omitted?`<div class="small" style="margin-top:8px">⚠ Large run (${run.detail_omitted} tasks): per-task REPL traces and trajectories are omitted to keep the catalog light. Reward, reward_info, and metrics are kept. To inspect this run's traces, rebuild with <code>--output-dir ${esc((run.file||'').split('/').slice(0,-1).join('/'))}</code>.</div>`:''}
      <details><summary>Run file</summary><div class="db"><pre>${esc(run.file)}\n${esc(run.run_log_dir||'(no matched run_logs dir)')}</pre></div></details>
    </section>
    <section class="panel">
      <h2>Tasks (${run.tasks.length})</h2>
      <table><thead><tr><th>Task</th><th>Split</th><th>Trial</th><th>Reward</th><th>Tokens</th><th>A2A turns</th><th>Trace</th></tr></thead><tbody>
        ${run.tasks.map(t=>`<tr class="tk ${taskKey(t)===selTask?'active':''}" onclick="selectTask('${esc(taskKey(t))}')">
          <td>${esc(t.task_id)}</td><td>${esc(t.split)}</td><td>${t.trial}</td>
          <td><span class="pill ${rewardCls(t.reward)}">${t.reward??'-'}</span></td>
          <td>${fmtTok(t.metrics.total_tokens)}</td><td>${fmtTok(t.metrics.num_a2a_turns)}</td>
          <td>${t.trace?'✓':'—'}</td></tr>`).join('')}
      </tbody></table>
    </section>
    <div id="taskDetail">${renderTask(run,task)}</div>`;
}

function taskKey(t){return `${t.split}|${t.task_id}|${t.trial}`;}
window.selectRun=function(k){selRun=k;selTask=null;renderRunList();renderRun();};
window.selectTask=function(k){selTask=k;renderRun();document.getElementById('taskDetail')?.scrollIntoView({behavior:'smooth',block:'start'});};
document.getElementById('filter').addEventListener('input',()=>{renderRunList();renderRun();});
renderRunList();renderRun();
</script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output", help="Dir scanned recursively for per-run result JSON files.")
    parser.add_argument("--trace-dir", default="run_logs/car_agent", help="Dir of per-context REPL trace jsonl.")
    parser.add_argument("--output", default="output/trace_explorer.html")
    parser.add_argument("--max-detail-tasks", type=int, default=80, help="Runs above this task count keep only their scoreboard (no embedded REPL steps/trajectories).")
    parser.add_argument("--no-incomplete", dest="incomplete", action="store_false", help="Skip reconstructing trace-only runs that have no evaluator result file.")
    args = parser.parse_args()

    payload = build_payload(Path(args.output_dir), Path(args.trace_dir), args.max_detail_tasks, args.incomplete)
    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html_text = HTML_TEMPLATE.replace("__PAYLOAD__", payload_json)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_text)
    matched = sum(1 for r in payload["runs"] if r.get("run_log_dir"))
    print(
        f"Wrote {out} cataloging {payload['run_count']} run(s) "
        f"({matched} linked to run_logs), {sum(len(r['tasks']) for r in payload['runs'])} task row(s)."
    )


if __name__ == "__main__":
    main()
