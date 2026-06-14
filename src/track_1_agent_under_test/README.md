# CAR-bench A-Agent

This agent follows the `A-Agent` architecture:

- the model has one action surface, `execute_python`
- Python code runs in a persistent per-task REPL
- CAR-bench tools are exposed as Python functions
- calling those functions queues evaluator-visible A2A `tool_calls`
- `respond("...")` emits user-facing text

Default provider is Nebius:

```bash
NEBIUS_API_KEY=...
AGENT_LLM=Qwen/Qwen3.5-397B-A17B
AGENT_BASE_URL=https://api.tokenfactory.us-central1.nebius.com/v1/
CAR_AGENT_MODEL_PROVIDER=nebius
CAR_AGENT_TOOL_MODE=prompt_json
```

For Track 2-style direct Cerebras routing:

```bash
CEREBRAS_API_KEY=...
CAR_AGENT_MODEL_PROVIDER=cerebras
CAR_AGENT_MODEL=gpt-oss-120b
CAR_AGENT_REASONING_EFFORT=medium
```

Run locally:

```bash
python src/track_1_agent_under_test/server.py --host 127.0.0.1 --port 8080
```

Trace logs are written per run and per A2A `context_id`:

```bash
run_logs/car_agent/<run_id>/<context_id>.jsonl
```

Each JSONL event records inbound evaluator parts, available tool names, model
`execute_python` code, REPL output, queued benchmark tool calls, user-facing
responses, and outbound A2A payloads.

Useful knobs:

```bash
CAR_AGENT_TRACE_DIR=run_logs/car_agent
CAR_AGENT_RUN_ID=smoke_001
CAR_AGENT_TRACE_FULL_MODEL_MESSAGES=false
LOGURU_LEVEL=INFO
```

If `CAR_AGENT_RUN_ID` is omitted, the agent creates one from the server start
timestamp. Each run directory includes `.run.json` with model/provider metadata.

Set `CAR_AGENT_TRACE_FULL_MODEL_MESSAGES=true` only when you need the full model
prompt history in the trace file; otherwise long prompts are stored as previews.
