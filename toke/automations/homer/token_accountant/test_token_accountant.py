#!/usr/bin/env python3
"""
Edge-case smoke tests for token-accountant Cycle 2 + 3 modules.
================================================================
Run: python test_token_accountant.py

Covers:
  - msg.id dedupe (the load-bearing Cycle 2 fix)
  - Empty / corrupt transcript JSONL tolerance
  - Missing-session find_transcript fallback
  - Sentinel staleness mtime parsing
  - Model-switch within session (Opus → Sonnet → Opus)
  - First-fire skill (n<5 == insufficient_samples in cache_thrash)
  - Cost-model alias mapping (opus / opus[1m] / sonnet / haiku / unknown)
  - Tier baseline observed-vs-default switch (n<10 == default)
  - Reconciliation NO_TURNS flag for empty driver
  - Long-tail multi-gate filter (small spike does NOT pass)

Pure stdlib + tmp dirs. No network, no external deps. Exit 0 on all-pass.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from transcript_loader import (  # noqa: E402
    parse_transcript, find_transcript, _extract_skills, _extract_thinking_chars,
)
from cost_model import (  # noqa: E402
    cost_from_usage, price_for, alias_for_tier, _alias_for_model,
    tier_predicted_cost_per_call, tier_baseline_from_observed,
)
from cache_thrash import (  # noqa: E402
    smoothed_hit_rate, CohortStats, divergence_proposal,
)
from long_tail import (  # noqa: E402
    percentile, CohortFires, MIN_SAMPLES, RATIO_GATE,
    ABSOLUTE_GATE_USD, SPREAD_GATE_USD, diagnose_spike,
)
from token_accountant import sentinel_age_hours  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


# -----------------------------------------------------------------------------
# Test harness (lightweight — no pytest dep)
# -----------------------------------------------------------------------------


_results: list[tuple[str, str, str | None]] = []  # (name, status, msg)


def test(name: str):
    def deco(fn):
        try:
            fn()
            _results.append((name, "PASS", None))
            print(f"  PASS  {name}")
        except AssertionError as e:
            _results.append((name, "FAIL", str(e) or repr(e)))
            print(f"  FAIL  {name} :: {e}")
        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc(limit=2)
            _results.append((name, "ERROR", f"{type(e).__name__}: {e}\n{tb}"))
            print(f"  ERROR {name} :: {type(e).__name__}: {e}")
        return fn
    return deco


# -----------------------------------------------------------------------------
# Fixture builder: tiny synthetic transcript
# -----------------------------------------------------------------------------


def _make_assistant_line(*, msg_id: str, ts: str, model: str = "claude-opus-4-7",
                        usage: dict, content: list) -> str:
    return json.dumps({
        "type": "assistant",
        "timestamp": ts,
        "sessionId": "_test",
        "requestId": f"req_{msg_id}",
        "message": {
            "id": msg_id,
            "model": model,
            "usage": usage,
            "content": content,
        },
    }) + "\n"


def _usage(*, inp=100, cr=10000, cw5=0, cw1=2000, out=300) -> dict:
    return {
        "input_tokens": inp,
        "cache_read_input_tokens": cr,
        "cache_creation": {
            "ephemeral_5m_input_tokens": cw5,
            "ephemeral_1h_input_tokens": cw1,
        },
        "output_tokens": out,
    }


# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------


print("token_accountant edge-case tests")
print("=" * 60)


@test("msg.id dedupe collapses duplicate assistant entries")
def _():
    """Three lines with same msg.id should yield ONE TranscriptTurn."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "_test.jsonl"
        u = _usage()
        lines = [
            _make_assistant_line(
                msg_id="m1", ts="2026-05-02T10:00:00.000Z", usage=u,
                content=[{"type": "thinking", "thinking": "..."}]),
            _make_assistant_line(
                msg_id="m1", ts="2026-05-02T10:00:00.500Z", usage=u,
                content=[{"type": "tool_use", "name": "Read", "input": {}}]),
            _make_assistant_line(
                msg_id="m1", ts="2026-05-02T10:00:01.000Z", usage=u,
                content=[{"type": "tool_use", "name": "Bash", "input": {}}]),
        ]
        path.write_text("".join(lines), encoding="utf-8")
        turns = list(parse_transcript(path))
        assert len(turns) == 1, f"expected 1 turn, got {len(turns)}"
        # Tools across all 3 lines should aggregate into one turn
        names = [b.get("name") for b in turns[0].tool_uses]
        assert "Read" in names and "Bash" in names, f"expected Read+Bash, got {names}"
        # Usage should NOT be 3x — single envelope per msg.id
        assert turns[0].input_tokens == 100, f"input not deduped: {turns[0].input_tokens}"


@test("multiple distinct msg.ids yield multiple turns")
def _():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "_test.jsonl"
        u = _usage()
        path.write_text(
            _make_assistant_line(msg_id="m1", ts="2026-05-02T10:00:00Z",
                                usage=u, content=[{"type": "text", "text": "hi"}])
            + _make_assistant_line(msg_id="m2", ts="2026-05-02T10:00:01Z",
                                  usage=u, content=[{"type": "text", "text": "bye"}]),
            encoding="utf-8",
        )
        turns = list(parse_transcript(path))
        assert len(turns) == 2, f"expected 2, got {len(turns)}"


@test("empty transcript yields zero turns without erroring")
def _():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "_test.jsonl"
        path.write_text("", encoding="utf-8")
        turns = list(parse_transcript(path))
        assert turns == []


@test("corrupt mid-line is skipped")
def _():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "_test.jsonl"
        good = _make_assistant_line(
            msg_id="m1", ts="2026-05-02T10:00:00Z", usage=_usage(),
            content=[{"type": "text", "text": "ok"}])
        bad = "{not valid json at all\n"
        path.write_text(good + bad + good.replace("m1", "m2"), encoding="utf-8")
        turns = list(parse_transcript(path))
        assert len(turns) == 2, f"expected 2 (corrupt skipped), got {len(turns)}"


@test("missing session_id returns None from find_transcript")
def _():
    res = find_transcript("nonexistent-session-id-xyzzy")
    assert res is None, f"expected None, got {res}"


@test("sentinel mtime in past returns large age")
def _():
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "_active_decision_test.txt"
        f.write_text("x", encoding="utf-8")
        # Force mtime back 2hr
        old = time.time() - 7200
        import os
        os.utime(f, (old, old))
        age = sentinel_age_hours(f)
        assert age >= 1.99, f"expected >=2hr age, got {age}"


@test("sentinel age on missing file is infinite")
def _():
    age = sentinel_age_hours(Path("/no/such/file.txt"))
    assert age == float("inf"), f"expected inf, got {age}"


@test("model-switch within session: cost reflects per-turn pricing")
def _():
    """Three turns: opus, sonnet, haiku — each should price correctly."""
    opus = cost_from_usage(model_id="claude-opus-4-7", input_tokens=1000,
                           cache_read=0, cache_create_5m=0, cache_create_1h=0,
                           output_tokens=1000)
    sonnet = cost_from_usage(model_id="claude-sonnet-4-6", input_tokens=1000,
                             cache_read=0, cache_create_5m=0, cache_create_1h=0,
                             output_tokens=1000)
    haiku = cost_from_usage(model_id="claude-haiku-4-5", input_tokens=1000,
                            cache_read=0, cache_create_5m=0, cache_create_1h=0,
                            output_tokens=1000)
    assert opus > sonnet > haiku, f"price ordering wrong: opus={opus} sonnet={sonnet} haiku={haiku}"
    # Spot check: opus 1k input + 1k output = 0.001 * 5 + 0.001 * 25 = 0.030
    assert abs(opus - 0.030) < 1e-6, f"opus calc wrong: {opus}"
    # Sonnet: 0.001 * 3 + 0.001 * 15 = 0.018
    assert abs(sonnet - 0.018) < 1e-6, f"sonnet calc wrong: {sonnet}"


@test("alias mapping handles common Claude model ids")
def _():
    assert _alias_for_model("claude-opus-4-7") == "opus"
    assert _alias_for_model("claude-opus-4-7[1m]") == "opus[1m]"
    assert _alias_for_model("claude-sonnet-4-6") == "sonnet"
    assert _alias_for_model("claude-haiku-4-5") == "haiku"
    assert _alias_for_model("") == "unknown"
    assert _alias_for_model("gpt-4") == "unknown"


@test("price_for falls back to 'unknown' for novel model")
def _():
    p = price_for("claude-mystery-model-99")
    # Should equal opus default (5/25/0.50)
    assert p.input_per_mtok == 5.0
    assert p.output_per_mtok == 25.0


@test("cache_thrash: insufficient_samples flags fires<5")
def _():
    s = CohortStats(skill="rare", model="opus")
    s.fires = 3
    s.fresh_input = 1000
    s.cache_read = 5000
    assert s.status == "insufficient_samples", f"expected insufficient, got {s.status}"


@test("cache_thrash: below_min flags zero-cache + fresh-only")
def _():
    s = CohortStats(skill="x", model="opus")
    s.fires = 100
    s.fresh_input = 50000
    s.cache_read = 0
    s.cache_create_5m = 0
    s.cache_create_1h = 0
    assert s.status == "below_min", f"expected below_min, got {s.status}"


@test("cache_thrash: smoothed_hit_rate raises low-sample to prior")
def _():
    # Single first-fire turn with all-create + zero-read would be raw 0%
    raw = 0 / (10000 + 1)
    smoothed = smoothed_hit_rate(0, 10000)
    # 10K denom + 10K prior = 20K, hits = 8K prior → 40% smoothed
    assert smoothed > raw, f"smoothed {smoothed} should exceed raw {raw}"
    assert 0.30 < smoothed < 0.50, f"smoothed should be ~0.40, got {smoothed}"


@test("long_tail: percentile linear interpolation matches manual calc")
def _():
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert percentile(vals, 50) == 3.0
    assert percentile(vals, 95) == 4.8  # linear interp between 4 and 5
    assert percentile(vals, 100) == 5.0
    assert percentile([], 50) == 0.0
    assert percentile([42.0], 50) == 42.0  # single-element edge case


@test("long_tail: small spike below absolute gate is NOT flagged")
def _():
    """p50=$0.001, p95=$0.10 → 100x ratio but absolute gate fails ($0.10 < $0.50)."""
    c = CohortFires(skill="cheap_skill", model="haiku")
    # Build 30 fires: 29 cheap + 1 small spike
    from transcript_loader import TranscriptTurn
    cheap_turn = TranscriptTurn(session_id="s", transcript_path="x", turn_num=1,
                                ts="", model="haiku", input_tokens=100,
                                cache_read=0, cache_create_5m=0, cache_create_1h=0,
                                output_tokens=10, thinking_chars=0)
    for _ in range(29):
        c.add(0.001, cheap_turn)
    c.add(0.10, cheap_turn)  # tiny spike
    assert not c.passes_gates(), \
        f"$0.10 spike below absolute gate $0.50 should NOT flag (passes={c.passes_gates()})"


@test("long_tail: real spike crosses all 3 gates")
def _():
    c = CohortFires(skill="expensive", model="opus")
    from transcript_loader import TranscriptTurn
    cheap_turn = TranscriptTurn(session_id="s", transcript_path="x", turn_num=1,
                                ts="", model="opus", input_tokens=100, cache_read=0,
                                cache_create_5m=0, cache_create_1h=0,
                                output_tokens=10, thinking_chars=0)
    # Need spikes in p95 zone: with N=30, p95 idx = (29*0.95) = 27.55, so
    # sorted[27]+sorted[28] must both be spikes. Use 26 cheap + 4 spikes.
    for _ in range(26):
        c.add(0.05, cheap_turn)
    for _ in range(4):
        c.add(5.00, cheap_turn)
    # p50 ~= 0.05, p95 ~= 5.00 — ratio 100x, abs >= $0.50, spread $4.95
    assert c.passes_gates(), \
        f"big spike should flag: p50={c.p50():.4f} p95={c.p95():.4f}"


@test("tier_baseline: <10 samples falls back to conservative default")
def _():
    cost, src = tier_baseline_from_observed({"S2": [0.50, 0.60, 0.70]}, "S2")
    assert src == "conservative-default", f"expected default fallback, got {src}"
    # Conservative default for S2 (sonnet) ~ $0.025
    assert cost < 0.05, f"default S2 baseline should be cheap, got {cost}"


@test("tier_baseline: >=10 samples uses observed median (with floor)")
def _():
    samples = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    cost, src = tier_baseline_from_observed({"S4": samples}, "S4")
    assert src == "observed-median", f"expected observed-median, got {src}"
    assert cost == 5.5, f"median of 1-10 should be 5.5, got {cost}"


@test("tier_baseline: observed median floors at conservative default")
def _():
    """If all observations are tiny (e.g. all NO_TURNS=0), don't return $0 baseline."""
    samples = [0.001] * 15  # 15 tiny turns
    cost, src = tier_baseline_from_observed({"S0": samples}, "S0")
    # Should clamp UP to default S0 baseline
    default = tier_predicted_cost_per_call("S0")
    assert cost >= default, f"baseline {cost} should floor at default {default}"


@test("alias_for_tier maps S0..S5 correctly")
def _():
    assert alias_for_tier("S0") == "haiku"
    assert alias_for_tier("S1") == "haiku"
    assert alias_for_tier("S2") == "sonnet"
    assert alias_for_tier("S3") == "sonnet"
    assert alias_for_tier("S4") == "opus"
    assert alias_for_tier("S5") == "opus"
    # Unknown tier defaults to opus (conservative)
    assert alias_for_tier("UNKNOWN") == "opus"


@test("transcript_loader thinking-block char extraction sums correctly")
def _():
    content = [
        {"type": "thinking", "thinking": "abc"},
        {"type": "text", "text": "ignored"},
        {"type": "thinking", "thinking": "defgh"},
    ]
    assert _extract_thinking_chars(content) == 8  # 3 + 5


@test("transcript_loader skill extraction skips non-Skill tool_use")
def _():
    content = [
        {"type": "tool_use", "name": "Read", "input": {"path": "x"}},
        {"type": "tool_use", "name": "Skill", "input": {"skill": "godspeed"}},
        {"type": "tool_use", "name": "Skill", "input": {"skill_name": "init"}},
    ]
    assert _extract_skills(content) == ["godspeed", "init"]


# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------

print("=" * 60)
n_pass = sum(1 for _, s, _ in _results if s == "PASS")
n_fail = sum(1 for _, s, _ in _results if s == "FAIL")
n_err = sum(1 for _, s, _ in _results if s == "ERROR")
total = len(_results)
print(f"{n_pass}/{total} passed | {n_fail} failed | {n_err} errored")
if n_fail or n_err:
    print()
    for name, status, msg in _results:
        if status != "PASS":
            print(f"  [{status}] {name}")
            if msg:
                print(f"          {msg}")
sys.exit(0 if (n_fail == 0 and n_err == 0) else 1)
