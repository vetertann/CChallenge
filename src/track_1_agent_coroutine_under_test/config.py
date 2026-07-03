"""Configuration for the CAR-bench coroutine agent."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from dotenv import dotenv_values, find_dotenv, load_dotenv


ENV_FILE = find_dotenv(filename=".env", usecwd=True)
if ENV_FILE:
    load_dotenv(ENV_FILE, override=False)
    ENV_VALUES = dotenv_values(ENV_FILE)
else:
    ENV_VALUES = {}


def _env(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    env_file_value = ENV_VALUES.get(name)
    return str(env_file_value) if env_file_value else ""


MODEL_PROVIDER = (
    _env("CAR_AGENT_MODEL_PROVIDER")
    or _env("AGENT_MODEL_PROVIDER")
    or _env("MODEL_PROVIDER")
    or "nebius"
).lower()

MODEL_ID = (
    _env("CAR_AGENT_MODEL")
    or _env("AGENT_LLM")
    or _env("MODEL_ID")
    or "Qwen/Qwen3.5-397B-A17B"
)

OPENAI_BASE_URL = _env("OPENAI_BASE_URL") or "https://api.openai.com/v1"
OPENAI_API_KEY = _env("OPENAI_API_KEY")

NEBIUS_BASE_URL = (
    _env("CAR_AGENT_BASE_URL")
    or _env("AGENT_BASE_URL")
    or _env("NEBIUS_BASE_URL")
    or "https://api.tokenfactory.us-central1.nebius.com/v1/"
)
NEBIUS_API_KEY = _env("NEBIUS_API_KEY")

OPENROUTER_BASE_URL = _env("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1"
OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY")

DEEPSEEK_BASE_URL = _env("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
DEEPSEEK_API_KEY = _env("DEEPSEEK_API_KEY")

CEREBRAS_BASE_URL = _env("CEREBRAS_BASE_URL") or "https://api.cerebras.ai"
CEREBRAS_API_KEY = _env("CEREBRAS_API_KEY")

MODEL_TIMEOUT_SECONDS = int(_env("CAR_AGENT_TIMEOUT_SECONDS") or _env("MODEL_TIMEOUT_SECONDS") or "120")
MODEL_MAX_OUTPUT_TOKENS = int(
    _env("CAR_AGENT_MAX_OUTPUT_TOKENS")
    or _env("AGENT_MAX_COMPLETION_TOKENS")
    or _env("MODEL_MAX_OUTPUT_TOKENS")
    or "2048"
)
MODEL_TEMPERATURE = _env("CAR_AGENT_TEMPERATURE") or _env("AGENT_TEMPERATURE") or "0"
MODEL_REASONING_EFFORT = (
    _env("CAR_AGENT_REASONING_EFFORT")
    or _env("TRACK2_EXECUTOR_REASONING_EFFORT")
    or _env("AGENT_REASONING_EFFORT")
    or ""
)
MODEL_TOOL_MODE = (
    _env("CAR_AGENT_TOOL_MODE")
    or _env("MODEL_TOOL_MODE")
    or "prompt_json"
).lower()
MODEL_MAX_ATTEMPTS = int(_env("CAR_AGENT_MAX_ATTEMPTS") or _env("MODEL_MAX_ATTEMPTS") or "4")
MODEL_SCHEMA_MAX_RETRIES = int(
    _env("CAR_AGENT_SCHEMA_MAX_RETRIES")
    or _env("MODEL_SCHEMA_MAX_RETRIES")
    or "3"
)
MODEL_MAX_INTERNAL_STEPS = int(_env("CAR_AGENT_MAX_INTERNAL_STEPS") or "5")
CAR_AGENT_SKILL = _env("CAR_AGENT_SKILL") or "car_domain.md"
CAR_AGENT_TRACE_DIR = _env("CAR_AGENT_TRACE_DIR") or "run_logs/car_agent"
CAR_AGENT_RUN_ID = _env("CAR_AGENT_RUN_ID") or datetime.now(timezone.utc).strftime(
    "run_%Y%m%dT%H%M%SZ"
)
CAR_AGENT_TRACE_FULL_MODEL_MESSAGES = (
    _env("CAR_AGENT_TRACE_FULL_MODEL_MESSAGES").lower() == "true"
)
