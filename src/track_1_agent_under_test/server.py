"""Server entry point for the CAR-bench A-Agent style agent."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import uvicorn
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCard
from starlette.applications import Starlette

sys.path.insert(0, str(Path(__file__).parent.parent))
from logging_utils import configure_logger
sys.path.pop(0)


logger = configure_logger(role="agent_under_test", context="server")


def prepare_agent_card(url: str) -> AgentCard:
    card = AgentCard(
        name="car_bench_a_agent",
        description="A-Agent style in-car voice assistant for CAR-bench",
        version="1.0.0",
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json"],
    )

    iface = card.supported_interfaces.add()
    iface.url = url
    iface.protocol_binding = "JSONRPC"
    iface.protocol_version = "1.0"

    card.capabilities.streaming = False
    card.capabilities.push_notifications = False
    card.capabilities.extended_agent_card = False

    skill = card.skills.add()
    skill.id = "car_assistant"
    skill.name = "CAR-bench A-Agent"
    skill.description = "Uses a Python REPL planner to emit CAR-bench tool calls"
    skill.tags.extend(["benchmark", "car-bench", "python-repl"])
    return card


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the CAR-bench A-Agent style agent.")
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--card-url", type=str)
    parser.add_argument("--agent-llm", type=str, help="Model id; also CAR_AGENT_MODEL or AGENT_LLM.")
    parser.add_argument("--model-provider", type=str, help="Provider: nebius, openai, openrouter, deepseek, cerebras.")
    parser.add_argument("--base-url", type=str, help="OpenAI-compatible base URL override for Nebius/OpenAI-style providers.")
    parser.add_argument("--tool-mode", type=str, choices=["prompt_json", "native"], help="How to force execute_python.")
    args = parser.parse_args()

    if args.agent_llm:
        os.environ["CAR_AGENT_MODEL"] = args.agent_llm
    if args.model_provider:
        os.environ["CAR_AGENT_MODEL_PROVIDER"] = args.model_provider
    if args.base_url:
        os.environ["CAR_AGENT_BASE_URL"] = args.base_url
    if args.tool_mode:
        os.environ["CAR_AGENT_TOOL_MODE"] = args.tool_mode

    from car_bench_agent import CARBenchAgentExecutor
    from config import MODEL_ID, MODEL_PROVIDER, MODEL_TOOL_MODE

    agent_url = args.card_url or f"http://{args.host}:{args.port}/"
    logger.info(
        "Starting CAR-bench A-Agent",
        model=MODEL_ID,
        provider=MODEL_PROVIDER,
        tool_mode=MODEL_TOOL_MODE,
        host=args.host,
        port=args.port,
    )

    card = prepare_agent_card(agent_url)
    request_handler = DefaultRequestHandler(
        agent_executor=CARBenchAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )

    routes = create_jsonrpc_routes(request_handler, "/", enable_v0_3_compat=True)
    card_routes = create_agent_card_routes(card)
    app = Starlette(routes=routes + card_routes)

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        timeout_keep_alive=1000,
    )


if __name__ == "__main__":
    main()

