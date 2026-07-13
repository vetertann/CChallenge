# CAR-bench IJCAI-ECAI 2026 — Submission Preparation Guide

Status: prepared 2026-07-13 (the submission Google Form opens today, Monday
2026-07-13). Sources: the official submission page
(car-bench.github.io/car-bench/submission.html), the starter-kit README, and
`docs/cerebras-harness-patterns.md`. Repo state at time of writing: `main` at
`504c0cd`, clean except untracked `trace_explorer.html`.

## What must be submitted (per track)

1. **Public GHCR Docker agent image, pinned by SHA-256 digest** (not a tag):
   `ghcr.io/<org>/<agent>@sha256:<digest>`.
2. **`scenario.toml`** referencing the *official organizer evaluator image*
   (participants never submit or self-host the evaluator), with
   `task_split = "hidden"`, `num_trials = 3`, `max_steps = 50`, and all three
   task-count fields set to `-1`.
3. **Environment-variable documentation** — names only, never values.
   Required vars as `${VAR:?message}`, optional as `${VAR:-default}`.
4. **All LLM parameters env-configurable**: model, provider route, API base,
   service tier, reasoning effort.
5. **Technical report**: 4 pages max, IJCAI author kit, must cite CAR-bench
   (arXiv 2601.22027). Track 2 additionally requires an architecture diagram
   supporting a sequential-call audit of the compute constraints.
6. **Track selection**: `track_1`, `track_2`, or `both`.

Scoring: hidden-set **Pass^3** (a task counts only if all 3 trials pass).
Track 2 additionally weighs latency and compute-aware architecture; Track 1
Innovation Award considers token efficiency, latency, and methodology.

## Track 2 compute constraints (binding for the report and the audit)

- Max **5 sequential LLM calls per baseline step** (parallel calls within a
  step are allowed). Our `CAR_AGENT_MAX_INTERNAL_STEPS=5` aligns, but schema
  repair retries (`CAR_AGENT_SCHEMA_MAX_RETRIES`, default 3) can multiply
  sequential calls within one step — the report's diagram must document the
  worst-case call graph honestly.
- Average token budget: **500k input+reasoning+output per task**. Measured on
  the 2026-07-11 3-trial full test: ~90k average per task — comfortable.
- Token usage must be aggregated into `turn_metrics`. Our accumulator attaches
  metrics to the concluding text response of each user turn;
  **tool-call-only A2A messages still carry no `turn_metrics`** (known
  limitation recorded in `docs/coroutine-agent-architecture.md`). Decide before
  submission: fix (attach a running snapshot in `ToolBridge` outbound) or
  disclose in the report.

## Step-by-step

### 0. Freeze and tag

```bash
git status                      # only trace_explorer.html untracked (ignore or delete)
git diff -- third_party/car-bench   # MUST be empty (verified 2026-07-12)
git tag submission-freeze-$(date +%Y%m%d) && git push personal --tags
```

### 1. Build the agent image for linux/amd64

`Dockerfile.agent` is already env-pure (host/port only in CMD). On Apple
Silicon the platform flag is mandatory:

```bash
docker build --platform linux/amd64 -f Dockerfile.agent \
  -t ghcr.io/<org>/car-coroutine-agent:v1 .
```

Sanity checks on the image:
- no `.env` or secrets baked in (`docker run --rm <img> sh -c 'ls -la /app; env'`),
- container starts with env-only config and binds `0.0.0.0:8080`.

### 2. Local Docker validation against the official evaluator image

Create a docker scenario (image-based `agent_under_test`, official evaluator
image) and run the compose flow:

```bash
uv run python generate_compose.py --scenario <docker-scenario>.toml
docker compose --env-file .env -f <generated>-docker-compose.yml up --abort-on-container-exit
```

Run at least the smoke selection first, then the full **test split, 3 trials**
with the exact env block that will go in the submission `scenario.toml`. This
is the last point where problems are cheap.

### 3. Push to GHCR, make public, pin the digest

```bash
docker push ghcr.io/<org>/car-coroutine-agent:v1
# GitHub → Packages → car-coroutine-agent → Package settings → Visibility: Public
docker inspect --format='{{index .RepoDigests 0}}' ghcr.io/<org>/car-coroutine-agent:v1
```

Re-run one smoke scenario with the **digest-pinned** reference pulled fresh
(`docker rmi` the local tag first) — this reproduces exactly what organizers
will run.

### 4. Submission `scenario.toml`

Critical: `config.py` defaults are **nebius / Qwen / car_domain.md** — every
submission-relevant value must be pinned via env. Track 2 example:

```toml
[evaluator]
image = "ghcr.io/car-bench/car-bench-evaluator:latest"   # official; do not change

[evaluator.env]
GEMINI_API_KEY = "${GEMINI_API_KEY:?Set GEMINI_API_KEY}"
LOGURU_LEVEL = "${LOGURU_LEVEL:-INFO}"

[agent_under_test]
image = "ghcr.io/<org>/car-coroutine-agent@sha256:<digest>"

[agent_under_test.env]
CEREBRAS_API_KEY = "${CEREBRAS_API_KEY:?Set CEREBRAS_API_KEY}"
CAR_AGENT_MODEL_PROVIDER = "${CAR_AGENT_MODEL_PROVIDER:-cerebras}"
CAR_AGENT_MODEL = "${CAR_AGENT_MODEL:-gpt-oss-120b}"
CAR_AGENT_SKILL = "${CAR_AGENT_SKILL:-car_domain_120b.md}"
CAR_AGENT_TOOL_MODE = "${CAR_AGENT_TOOL_MODE:-prompt_json}"
CAR_AGENT_TEMPERATURE = "${CAR_AGENT_TEMPERATURE:-0}"
CAR_AGENT_REASONING_EFFORT = "${CAR_AGENT_REASONING_EFFORT:-}"
CAR_AGENT_MAX_INTERNAL_STEPS = "${CAR_AGENT_MAX_INTERNAL_STEPS:-5}"
CAR_AGENT_MAX_ATTEMPTS = "${CAR_AGENT_MAX_ATTEMPTS:-6}"
LOGURU_LEVEL = "${LOGURU_LEVEL:-INFO}"

[config]
num_trials = 3
task_split = "hidden"
tasks_base_num_tasks = -1
tasks_hallucination_num_tasks = -1
tasks_disambiguation_num_tasks = -1
max_steps = 50
```

For a Track 1 (`both`) submission, the same image is reused with a different
env block (e.g. `CAR_AGENT_MODEL_PROVIDER=openai`, `CAR_AGENT_MODEL=gpt-5.5`);
confirm with organizers whose API keys fund hidden-set agent calls before
choosing a paid frontier model.

### 5. Environment-variable documentation (form field)

Required: `CEREBRAS_API_KEY` (Track 2) / provider key for chosen route.
Optional (with defaults): `CAR_AGENT_MODEL_PROVIDER`, `CAR_AGENT_MODEL`,
`CAR_AGENT_BASE_URL`, `CAR_AGENT_SKILL`, `CAR_AGENT_TOOL_MODE`,
`CAR_AGENT_TEMPERATURE`, `CAR_AGENT_REASONING_EFFORT`,
`CAR_AGENT_MAX_OUTPUT_TOKENS`, `CAR_AGENT_MAX_INTERNAL_STEPS`,
`CAR_AGENT_SCHEMA_MAX_RETRIES`, `CAR_AGENT_MAX_ATTEMPTS`,
`CAR_AGENT_TIMEOUT_SECONDS`, `CAR_AGENT_STORM_RETRY_TEMPERATURES`,
`CAR_AGENT_TRACE_DIR`, `CAR_AGENT_RUN_ID`, `LOGURU_LEVEL`. Names only.

### 6. Technical report (4 pages, IJCAI kit)

Suggested outline for this agent:
1. Coroutine-bridge architecture (reuse the mermaid diagram from
   `docs/coroutine-agent-architecture.md` — it doubles as the Track 2
   sequential-call audit diagram once annotated with the ≤5-call loop, schema
   retries, and the parallel `batch()` path).
2. Facts-vs-intention runtime boundary; raw-wrapper delegation; live-surface
   membership (organizer-confirmed AX pattern) for hallucination tasks.
3. Reliability mechanisms: confirmation state machine, response obligations,
   result normalization, unknown-value sentinels.
4. Results: public-set Pass^1/Pass^3 (3-trial full test 2026-07-11: 93.3% /
   87.3%), token/latency profile (median ~1.1 s per call, ~90k tokens/task).
5. CAR-bench citation (bibtex in starter README).

### 7. Submit the Google Form

Image digest reference, `scenario.toml`, env-var docs, track selection,
report PDF.

## Dual-track plan (decided 2026-07-13)

One image, two submissions. Both scenario files live in `submission/`
(`track2_scenario.toml`, `track1_scenario.toml`) with digest placeholders.

**Track 2**: Cerebras `gpt-oss-120b`, skill `car_domain_120b.md`,
temperature 0, storm-retry ladder enabled. Fully validated: 3-trial full test
2026-07-11 (Pass^1 93.3% / Pass^3 87.3%) plus targeted regression runs.

**Track 1**: OpenAI `gpt-5.5` through the same image
(`CAR_AGENT_MODEL_PROVIDER=openai`, `CAR_AGENT_MODEL=gpt-5.5`,
skill `car_domain_120b.md` — the configuration used in all local gpt-5.5
runs). Provider-path facts to keep in mind:

- The gpt-5* path deliberately sends **no temperature** and uses
  `max_completion_tokens` (2048 default). For a reasoning model that cap
  includes reasoning tokens; local runs passed with it, but it is the first
  knob to raise if hidden-set tasks truncate.
- `CAR_AGENT_REASONING_EFFORT` is read but **only applied on the Cerebras
  path** (`provider.py`); on openai/gpt-5.5 the model default effort is used.
  Either wire it through for gpt-5* (one-line, default-unset behavior
  unchanged) or disclose in the form that the selector is a no-op for this
  route.
- **Validation gap**: gpt-5.5 has only been run on ~69 targeted task
  attempts (100% pass on the failure-cluster probes), never on a full split.
  Before submitting Track 1, run at least one full test-split trial with the
  digest-pinned image. Measured cost basis: ~80k input + ~2.2k
  output/thinking tokens per task ⇒ ~10M input / ~0.3M output tokens per
  full-test trial (×3 for a Pass^3-grade validation).

Form entries differ per track: env docs for Track 2 list `CEREBRAS_API_KEY`
as required; Track 1 lists `OPENAI_API_KEY`. The technical report can be
shared or split; the Track 2 copy must carry the architecture diagram and
sequential-call audit.

## Known risks / open decisions before freeze

1. **`turn_metrics` on tool-call-only messages** (Track 2): fix or disclose.
2. **429 backoff**: `provider.py` retries 429 with exponential backoff capped
   at 30 s and `CAR_AGENT_MAX_ATTEMPTS=4` (~62 s worst-case wait). The Cerebras
   harness guidance recommends 60 s → 300 s ladders under queue saturation.
   Under organizer-side concurrency this could turn rate limiting into task
   failures. Cheapest mitigation without code change: raise
   `CAR_AGENT_MAX_ATTEMPTS` in the submission env; better: honor
   `retry-after` and raise the sleep cap.
3. **Outbox timeout desync**: the A2A executor waits
   `max(30, CAR_AGENT_TIMEOUT_SECONDS + 30)` for the worker; stacked provider
   retries can exceed that, producing an "internal timeout" reply while the
   worker continues. Only reachable under sustained provider failures, but a
   429 storm is exactly that scenario.
4. **Config defaults** point at nebius/Qwen/`car_domain.md`; the scenario env
   block above is the single source of truth — never rely on image defaults.
5. Hidden split is organizer-run only; the last self-validation is the test
   split with the digest-pinned GHCR image and the exact submission env.
