#!/usr/bin/env python3
"""
test_cost_guard.py — Phase 3i cost / efficiency guard regression harness.

Covers:
- pure cost_guard math (budgets, breach, tier inference, cache rate)
- receipt build + write round-trip
- invoke_live mid-flight BUDGET_EXCEEDED via stubbed Anthropic client

The mid-flight test stubs `anthropic` in sys.modules BEFORE agent_runner is
imported so invoke_live picks up the fake. The stub returns a high-token
response on first iteration, which trips an S0 ($0.005) budget.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import types
from pathlib import Path

THIS = Path(__file__).resolve()
HOMER_DIR = THIS.parent
sys.path.insert(0, str(HOMER_DIR))

import cost_guard  # noqa: E402


# ----------------------------------------------------------------------------
# 1. Pure-function checks (mirror cost_guard.is_breach + budget_for_tier matrix)
# ----------------------------------------------------------------------------
def test_pure_functions() -> None:
    assert cost_guard.is_breach(0.15, 0.1) is True
    assert cost_guard.is_breach(0.10, 0.1) is False
    assert cost_guard.is_breach(0.149, 0.1) is False
    assert cost_guard.is_breach(0.0, 0.1) is False
    assert cost_guard.is_breach(1.0, 0.0) is False

    assert cost_guard.budget_for_tier("S0") == 0.005
    assert cost_guard.budget_for_tier("S5") == 5.0
    assert cost_guard.budget_for_tier(None) == 0.1
    assert cost_guard.budget_for_tier("S99") == 0.1
    assert cost_guard.budget_for_tier("s2") == 0.1

    assert cost_guard.tier_for_model("haiku")  == "S1"
    assert cost_guard.tier_for_model("sonnet") == "S2"
    assert cost_guard.tier_for_model("opus")   == "S4"
    assert cost_guard.tier_for_model(None)     == "S2"

    assert cost_guard.cache_hit_rate(0, 0, 0) is None
    assert cost_guard.cache_hit_rate(100, 900, 0) == 0.9
    assert cost_guard.cache_hit_rate(1000, 0, 0) == 0.0


# ----------------------------------------------------------------------------
# 2. Receipt round-trip (build → write → load → rollup)
# ----------------------------------------------------------------------------
def test_receipt_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        original_path = cost_guard.RECEIPT_PATH
        cost_guard.RECEIPT_PATH = Path(tmp) / "cost_efficiency.jsonl"
        try:
            ok = cost_guard.write_receipt(cost_guard.build_receipt(
                agent="alpha", tier="S2", actual_cost_usd=0.05,
                iterations=3, verdict="OK", session_id="sess1",
            ))
            assert ok, "write_receipt returned False"
            ok2 = cost_guard.write_receipt(cost_guard.build_receipt(
                agent="beta", tier="S2", actual_cost_usd=0.16,
                iterations=2, verdict="BUDGET_EXCEEDED", session_id="sess1",
            ))
            assert ok2

            rows = cost_guard.load_receipts()
            assert len(rows) == 2
            assert rows[0]["agent"] == "alpha"
            assert rows[1]["breach"] is True

            roll = cost_guard.rollup_efficiency(rows)
            assert roll["receipt_count"] == 2
            assert abs(roll["total_actual_usd"] - 0.21) < 1e-6
            assert abs(roll["total_budget_usd"] - 0.20) < 1e-6
            assert roll["breach_count"] == 1
            assert roll["breach_rate"] == 0.5
            assert "alpha" in roll["by_agent"]
            assert "beta" in roll["by_agent"]
            assert roll["by_tier"]["S2"]["fires"] == 2
        finally:
            cost_guard.RECEIPT_PATH = original_path


# ----------------------------------------------------------------------------
# 3. Mid-flight BUDGET_EXCEEDED via stubbed Anthropic client
# ----------------------------------------------------------------------------
class _StubUsage:
    """Mimics anthropic.types.Usage just enough for invoke_live to read."""
    def __init__(self, input_tokens: int, output_tokens: int):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0


class _StubBlock:
    def __init__(self, text: str):
        self.text = text


class _StubResponse:
    def __init__(self, input_tokens: int, output_tokens: int, text: str = "ok"):
        self.usage = _StubUsage(input_tokens, output_tokens)
        self.stop_reason = "end_turn"  # not tool_use → loop exits naturally
        self.content = [_StubBlock(text)]


class _StubMessages:
    def __init__(self, response: _StubResponse):
        self._response = response

    def create(self, **kwargs):
        return self._response


class _StubClient:
    def __init__(self, response: _StubResponse):
        self.messages = _StubMessages(response)


def _install_anthropic_stub(response: _StubResponse) -> None:
    fake_module = types.ModuleType("anthropic")

    class Anthropic:
        def __init__(self, api_key=None, **_):
            self._api_key = api_key

        # Allow attribute access used inside invoke_live.
        def __getattr__(self, name):
            if name == "messages":
                return _StubMessages(response)
            raise AttributeError(name)

    fake_module.Anthropic = Anthropic
    sys.modules["anthropic"] = fake_module


def test_invoke_live_breach() -> None:
    # Need an API key in env for invoke_live to enter the live path (it
    # short-circuits to dry-run otherwise). The stub never validates the key.
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-stub-key")

    # 200K input × $5/MTok opus = $1.00 (way over S0's $0.005 / breach $0.0075).
    # Sized so any S<3 tier breaches on first iteration.
    response = _StubResponse(input_tokens=200_000, output_tokens=0, text="stub-pass")
    _install_anthropic_stub(response)

    if "agent_runner" in sys.modules:
        del sys.modules["agent_runner"]
    import agent_runner  # type: ignore  # picks up stubbed anthropic

    # Hand-craft a payload that mimics build_invocation_payload output.
    payload = {
        "agent": "stub_agent",
        "division": "debug",
        "model": "opus",
        "system_prompt": "system" * 50,  # ≥ 200 chars
        "user_task": "run breach scenario",
        "tool_grants": [],
        "skill_wrappers": [],
        "max_thinking_budget": 0,
        "tool_result_truncation_chars": 8000,
        "task_hash": "stubhash",
        "tier": "S0",
        "budget_usd": 0.005,
    }
    result = agent_runner.invoke_live(payload, max_iterations=3)
    assert result["mode"] == "live", f"expected live mode, got {result['mode']!r}"
    assert result["verdict"] == "BUDGET_EXCEEDED", (
        f"expected BUDGET_EXCEEDED, got {result['verdict']!r}"
    )
    assert result["breach"] is True
    assert result["budget_usd"] == 0.005
    assert result["tier"] == "S0"
    assert result["success"] is False
    assert result["iterations"] == 1, "should break on first iteration"


# ----------------------------------------------------------------------------
# Entry
# ----------------------------------------------------------------------------
def main() -> int:
    cases = [
        ("pure_functions",     test_pure_functions),
        ("receipt_roundtrip",  test_receipt_roundtrip),
        ("invoke_live_breach", test_invoke_live_breach),
    ]
    failed: list[str] = []
    for name, fn in cases:
        try:
            fn()
            print(f"[PASS] {name}")
        except AssertionError as e:
            print(f"[FAIL] {name}: {e}")
            failed.append(name)
        except Exception as e:
            print(f"[FAIL] {name}: {type(e).__name__}: {e}")
            failed.append(name)
    print("=" * 50)
    if failed:
        print(f"{len(failed)} FAIL: {failed}")
        return 1
    print(f"ALL {len(cases)} PASS — Phase 3i cost guard verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
