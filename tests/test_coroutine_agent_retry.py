import unittest
import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[1] / "src" / "track_1_agent_coroutine_under_test"
sys.path.insert(0, str(AGENT_DIR))

from coroutine_agent import (  # noqa: E402
    CEREBRAS_REPEATED_REPL_ERROR_LIMIT,
    CoroutineAgentWorker,
    REPEATED_REPL_ERROR_LIMIT,
    STORM_RETRY_TEMPERATURES,
)
from coroutine_repl import ExecutionResult  # noqa: E402
import provider as provider_module  # noqa: E402
from turn_metrics import NUM_LLM_CALLS, TURN_METRICS_KEY


class FakeChatCompletions:
    def __init__(self):
        self.kwargs: dict | None = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return object()


class FakeClient:
    def __init__(self):
        self.completions = FakeChatCompletions()
        self.chat = type("FakeChat", (), {"completions": self.completions})()


class RepeatedReplErrorBreakerTests(unittest.TestCase):
    def test_repeated_syntax_error_escalates_temperature_then_breaks(self):
        worker = CoroutineAgentWorker(
            context_id="retry-storm-test",
            model="test-model",
            provider="cerebras",
            tool_mode="prompt_json",
            client=object(),
        )
        steps: list[int] = []
        temps: list[float | None] = []

        def next_execute_python(step_index: int, temperature_override=None):
            steps.append(step_index)
            temps.append(temperature_override)
            worker.metrics.add_call({}, 1.0)
            return (
                {"role": "assistant", "content": '{"thought": "", "code": "bad"}'},
                "",
                "if True:\nprint('bad')",
                f"call-{step_index}",
            )

        worker._next_execute_python = next_execute_python  # type: ignore[method-assign]
        worker.executor.run = lambda code: ExecutionResult(
            stdout="",
            error={
                "type": "IndentationError",
                "message": "expected an indented block after 'if' statement on line 1 (<string>, line 2)",
            },
            response_text=None,
        )

        worker._run_until_outbound()

        outbound = worker.outbox.get_nowait()
        # First attempt at the configured temperature, then the escalation ladder
        # (slight first), then break once every step has been tried.
        n = CEREBRAS_REPEATED_REPL_ERROR_LIMIT
        self.assertEqual(steps, list(range(1, n + 1)))
        self.assertEqual(temps, [None, *STORM_RETRY_TEMPERATURES])
        self.assertEqual(temps[1], STORM_RETRY_TEMPERATURES[0])  # first retry is the slight bump
        self.assertEqual(
            outbound.response_text,
            "I hit an internal issue while deciding the next step.",
        )
        self.assertEqual(outbound.metadata[TURN_METRICS_KEY][NUM_LLM_CALLS], n)

    def test_non_cerebras_breaks_without_temperature_escalation(self):
        worker = CoroutineAgentWorker(
            context_id="retry-storm-test",
            model="test-model",
            provider="nebius",
            tool_mode="prompt_json",
            client=object(),
        )
        steps: list[int] = []
        temps: list[float | None] = []

        def next_execute_python(step_index: int, temperature_override=None):
            steps.append(step_index)
            temps.append(temperature_override)
            worker.metrics.add_call({}, 1.0)
            return (
                {"role": "assistant", "content": '{"thought": "", "code": "bad"}'},
                "",
                "if True:\nprint('bad')",
                f"call-{step_index}",
            )

        worker._next_execute_python = next_execute_python  # type: ignore[method-assign]
        worker.executor.run = lambda code: ExecutionResult(
            stdout="",
            error={
                "type": "IndentationError",
                "message": "expected an indented block after 'if' statement on line 1 (<string>, line 2)",
            },
            response_text=None,
        )

        worker._run_until_outbound()

        outbound = worker.outbox.get_nowait()
        n = REPEATED_REPL_ERROR_LIMIT
        self.assertEqual(steps, list(range(1, n + 1)))
        self.assertEqual(temps, [None] * n)
        self.assertEqual(
            outbound.response_text,
            "I hit an internal issue while deciding the next step.",
        )
        self.assertEqual(outbound.metadata[TURN_METRICS_KEY][NUM_LLM_CALLS], n)

    def test_temperature_escalation_recovers_on_different_code(self):
        worker = CoroutineAgentWorker(
            context_id="retry-recover-test",
            model="test-model",
            provider="cerebras",
            tool_mode="prompt_json",
            client=object(),
        )
        temps: list[float | None] = []
        results = [
            ExecutionResult(stdout="", error={
                "type": "SyntaxError", "message": "invalid syntax (<string>, line 2)"},
                response_text=None),
            # Second attempt (escalated temperature) produces valid code that responds.
            ExecutionResult(stdout="", error=None, response_text="Done."),
        ]

        def next_execute_python(step_index: int, temperature_override=None):
            temps.append(temperature_override)
            worker.metrics.add_call({}, 1.0)
            return ({"role": "assistant", "content": "{}"}, "", "code", f"call-{step_index}")

        worker._next_execute_python = next_execute_python  # type: ignore[method-assign]
        worker.executor.run = lambda code: results.pop(0)

        worker._run_until_outbound()

        outbound = worker.outbox.get_nowait()
        # First retry uses the slight bump, then recovered.
        self.assertEqual(temps, [None, STORM_RETRY_TEMPERATURES[0]])
        self.assertEqual(outbound.response_text, "Done.")

    def test_repl_error_signature_ignores_line_number_noise(self):
        first = CoroutineAgentWorker._retry_storm_error_signature(
            {
                "type": "SyntaxError",
                "message": "invalid syntax (<string>, line 2)",
            }
        )
        second = CoroutineAgentWorker._retry_storm_error_signature(
            {
                "type": "SyntaxError",
                "message": "invalid syntax (<string>, line 9)",
            }
        )

        self.assertEqual(first, second)

    def test_non_syntax_error_does_not_trigger_retry_storm_breaker(self):
        self.assertIsNone(
            CoroutineAgentWorker._retry_storm_error_signature(
                {
                    "type": "ValueError",
                    "message": "Unknown tool/helper name 'bad_tool'",
                }
            )
        )


class ProviderTemperatureOverrideTests(unittest.TestCase):
    def setUp(self):
        self.original_temperature = provider_module.MODEL_TEMPERATURE
        self.original_reasoning_effort = provider_module.MODEL_REASONING_EFFORT
        provider_module.MODEL_TEMPERATURE = "0"
        provider_module.MODEL_REASONING_EFFORT = None

    def tearDown(self):
        provider_module.MODEL_TEMPERATURE = self.original_temperature
        provider_module.MODEL_REASONING_EFFORT = self.original_reasoning_effort

    def test_non_cerebras_ignores_retry_temperature_override(self):
        client = FakeClient()

        provider_module._call_model(
            client=client,
            provider="nebius",
            model="test-model",
            messages=[],
            tool_mode="prompt_json",
            temperature_override=0.7,
        )

        self.assertEqual(client.completions.kwargs["temperature"], 0.0)

    def test_cerebras_honors_retry_temperature_override(self):
        client = FakeClient()

        provider_module._call_model(
            client=client,
            provider="cerebras",
            model="test-model",
            messages=[],
            tool_mode="prompt_json",
            temperature_override=0.7,
        )

        self.assertEqual(client.completions.kwargs["temperature"], 0.7)


if __name__ == "__main__":
    unittest.main()
