#!/usr/bin/env python3
"""
Toke Brain — CLI Workbench (v2.0)
==================================
Commands:
    brain score [TEXT]          Classify a prompt (TEXT arg or --stdin)
    brain scan                  Cost analysis + savings projection + v2 regression alerts
    brain audit-skills          Show every skill + assigned tier + unassigned skills
    brain pin SKILL [--write]   Add model: frontmatter to a skill (dry-run unless --write)
    brain apply-env             Print shell env exports to source in bashrc
    brain hook                  UserPromptSubmit hook entry (reads hook JSON from stdin)
    brain telemetry             PostToolUse telemetry entry (reads hook JSON from stdin)
    brain test                  Run classifier smoke tests

v2.0 additions (feedback loop + learning):
    brain history [N]           Show last N routing decisions with tier summary
    brain budget                Active session cost tracker with manifest thresholds
    brain tune [--write]        Propose manifest weight adjustments from telemetry
    brain good                  Mark last decision as positive (explicit feedback)
    brain bad                   Mark last decision as negative (explicit feedback)
    brain advisor-status        Check Anthropic advisor_20260301 integration status
    brain advise PROMPT [opts]  Call advisor_20260301 API (executor + Opus escalation)

v2.1 additions (periodic self-audit):
    brain godspeed-tick [N]     Increment godspeed counter; auto-run scan every N (default 33)
    brain help                  Show this help

v2.3 additions (human behavioral layer — Katanforoosh gap closure):
    Every decisions.jsonl entry now includes a human{} dict with:
    turn_index, turns_since_correction, consecutive_corrections,
    session_override_count, session_reprompt_count, prompt_token_count,
    inter_turn_gap_seconds, delegation_mode (full/supervised/checkpoint/veto).
    brain scan now includes HUMAN METRICS section via summarize_human_state().
"""

from __future__ import annotations

import datetime
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# Ensure UTF-8 stdout/stderr on Windows (cp1252 breaks on any non-ASCII in manifest)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).parent))

from severity_classifier import classify, load_manifest, DEFAULT_MANIFEST_PATH  # noqa: E402
import brain_learner  # noqa: E402

STATS_CACHE = Path.home() / ".claude" / "stats-cache.json"
TELEMETRY_DIR = Path.home() / ".claude" / "telemetry" / "brain"


# =============================================================================
# brain score
# =============================================================================


def cmd_score(args: list[str]) -> int:
    if not args:
        print("usage: brain score TEXT   OR   echo TEXT | brain score --stdin", file=sys.stderr)
        return 2

    if args[0] == "--stdin":
        prompt_text = sys.stdin.read()
    else:
        prompt_text = " ".join(args)

    result = classify(prompt_text=prompt_text)

    print(f"Tier:    {result.tier}")
    print(f"Model:   {result.model}    Effort: {result.effort}")
    print(f"Score:   {result.score}")
    print(f"Reason:  {result.reasoning}")
    print()
    print("Signals:")
    for name, value in result.signals.items():
        bar = "#" * int(value * 20)
        print(f"  {name:<15} {value:.3f}  {bar}")
    if result.guardrails_fired:
        print()
        print(f"Guardrails fired: {', '.join(result.guardrails_fired)}")
    return 0


# =============================================================================
# brain scan
# =============================================================================


def _load_stats_cache() -> dict[str, Any] | None:
    if not STATS_CACHE.exists():
        return None
    try:
        return json.loads(STATS_CACHE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _price_model(usage: dict[str, Any], prices: dict[str, Any]) -> float:
    # Cache-write multiplier notes (per Anthropic pricing docs, verified 2026-04-11):
    #   5m cache write = base input x 1.25
    #   1h cache write = base input x 2.00
    # stats-cache.json reports a single aggregated `cacheCreationInputTokens` that does NOT
    # distinguish 5m vs 1h. the user's real sessions write ~100% to 1h cache (ephemeral_1h
    # dominates ephemeral_5m ~ 0 per transcript inspection). Using 2.0 is the accurate
    # conservative choice for this workload. For fine-grained 5m/1h split, use
    # `tokens/token_snapshot.py` which reads transcript-level cache_creation fields.
    _CACHE_WRITE_MULT_1H = 2.00
    input_tok = usage.get("inputTokens", 0)
    output_tok = usage.get("outputTokens", 0)
    cache_read = usage.get("cacheReadInputTokens", 0)
    cache_write = usage.get("cacheCreationInputTokens", 0)

    cost = (
        (input_tok / 1_000_000) * prices["cost_input_per_mtok"]
        + (output_tok / 1_000_000) * prices["cost_output_per_mtok"]
        + (cache_read / 1_000_000) * prices["cost_cache_read_per_mtok"]
        + (cache_write / 1_000_000) * prices["cost_input_per_mtok"] * _CACHE_WRITE_MULT_1H
    )
    return cost


def cmd_scan(args: list[str]) -> int:
    data = _load_stats_cache()
    if data is None:
        print(f"ERROR: {STATS_CACHE} not found or malformed", file=sys.stderr)
        return 1

    manifest = load_manifest(DEFAULT_MANIFEST_PATH)
    models_cfg = manifest.get("models", {})

    model_usage = data.get("modelUsage", {})
    rows: dict[str, dict[str, Any]] = {}

    for model_id, usage in model_usage.items():
        key_lower = model_id.lower()
        if "opus" in key_lower:
            prices = models_cfg.get("opus", {})
            category = "opus"
        elif "sonnet" in key_lower:
            prices = models_cfg.get("sonnet", {})
            category = "sonnet"
        elif "haiku" in key_lower:
            prices = models_cfg.get("haiku", {})
            category = "haiku"
        else:
            continue

        if not prices:
            continue

        cost = _price_model(usage, prices)
        rows[model_id] = {
            "category": category,
            "input_tokens": usage.get("inputTokens", 0),
            "output_tokens": usage.get("outputTokens", 0),
            "cache_read": usage.get("cacheReadInputTokens", 0),
            "cache_write": usage.get("cacheCreationInputTokens", 0),
            "cost_usd": round(cost, 2),
        }

    total = sum(r["cost_usd"] for r in rows.values())

    print("=======================================")
    print("  BRAIN SCAN - 30-day cost analysis")
    print("=======================================")
    print(f"Data source: {STATS_CACHE}")
    print(f"First session:   {data.get('firstSessionDate', '?')}")
    print(f"Total sessions:  {data.get('totalSessions', '?')}")
    print(f"Total messages:  {data.get('totalMessages', '?')}")
    print()
    print("Cost by model:")
    for model_id, row in sorted(rows.items(), key=lambda kv: kv[1]["cost_usd"], reverse=True):
        pct = (row["cost_usd"] / total * 100) if total > 0 else 0
        print(f"  {model_id:<38} ${row['cost_usd']:>10,.2f}  ({pct:>4.1f}%)")
    print(f"  {'TOTAL':<38} ${total:>10,.2f}")
    print()

    opus_cost = sum(r["cost_usd"] for r in rows.values() if r["category"] == "opus")

    # Theoretical ceiling (assumes routing authority over main session — NOT achievable under /effort max)
    theo_50_sonnet = opus_cost * 0.50 * 0.40
    theo_20_haiku = opus_cost * 0.20 * 0.80
    theo_total = theo_50_sonnet + theo_20_haiku
    theo_pct = (theo_total / total * 100) if total > 0 else 0

    # Achievable via Zone 2 (subagents only — Claude Code hooks cannot force main-session model)
    # Subagents are typically ~5-15% of total Opus spend; we take the conservative midpoint.
    zone2_share = 0.10  # empirical subagent share of Opus spend
    achievable = opus_cost * zone2_share * 0.50  # Sonnet replacing half of subagent Opus calls
    achievable_pct = (achievable / total * 100) if total > 0 else 0

    print("Routing savings — honest accounting:")
    print(f"  Opus 30-day baseline:                 ${opus_cost:>10,.2f}")
    print(f"  ACHIEVABLE (Zone 2 subagents only):   ${achievable:>10,.2f}  ({achievable_pct:>4.1f}% of total)")
    print(f"     ~ Sonnet replaces half of subagent Opus calls via CLAUDE_CODE_SUBAGENT_MODEL env.")
    print(f"  THEORETICAL CEILING (not reachable):  ${theo_total:>10,.2f}  ({theo_pct:>4.1f}% of total)")
    print(f"     ~ Assumes main session could be routed; blocked by /effort max + hook advisory-only limit.")
    print(f"  Pricing: verified against Anthropic docs 2026-04-11 (Opus 4.6 = $5/$25/$0.50).")
    print(f"           Cache-write uses 2.0x (1h-dominant real usage). See tokens/PRICING_NOTES.md.")
    print()

    sonnet_cost = sum(r["cost_usd"] for r in rows.values() if r["category"] == "sonnet")
    haiku_cost = sum(r["cost_usd"] for r in rows.values() if r["category"] == "haiku")
    print("Current routing split:")
    print(f"  Opus:    ${opus_cost:>10,.2f}  ({(opus_cost/total*100) if total else 0:>5.1f}%)")
    print(f"  Sonnet:  ${sonnet_cost:>10,.2f}  ({(sonnet_cost/total*100) if total else 0:>5.1f}%)")
    print(f"  Haiku:   ${haiku_cost:>10,.2f}  ({(haiku_cost/total*100) if total else 0:>5.1f}%)")
    print()

    # ========================================================================
    # v2.0: Learning state + regression alerts
    # ========================================================================
    learning_cfg = manifest.get("learning", {})
    if learning_cfg.get("enabled", False):
        print("=======================================")
        print("  v2 LEARNING STATE")
        print("=======================================")
        state = brain_learner.summarize_learning_state()
        print(f"Decisions logged:     {state['decisions_seen']}")
        print(f"Tool calls logged:    {state['tools_seen']}")
        print(f"Override events:      {state['override_events']}")
        print(f"Correction follows:   {state['correction_follows']}")
        print()

        top_skills = state.get("top_overridden_skills", [])
        if top_skills:
            print("Top overridden skills (candidates for tier bump):")
            for skill, count in top_skills:
                print(f"  {skill:<25} {count} overrides")
            print()

        drift = state.get("tier_drift_pp_7d_vs_30d", {})
        short_total = state.get("short_window_total", 0)
        long_total = state.get("long_window_total", 0)
        if long_total > 0:
            print(f"Tier drift (7d vs 30d)   [7d: {short_total} decisions, 30d: {long_total} decisions]")
            for tier in ["S0", "S1", "S2", "S3", "S4", "S5"]:
                delta = drift.get(tier, 0.0)
                marker = " WARN" if abs(delta) > learning_cfg.get("drift_threshold_pp", 10.0) else ""
                print(f"  {tier}: {delta:+6.2f} pp{marker}")
            print()

        # Regression alerts
        alerts: list[str] = []
        drift_threshold = float(learning_cfg.get("drift_threshold_pp", 10.0))
        for tier, delta in drift.items():
            if abs(delta) > drift_threshold:
                direction = "UP" if delta > 0 else "DOWN"
                alerts.append(f"Tier {tier} drifted {direction} {abs(delta):.1f}pp in 7d vs 30d baseline")

        override_rate = (state["override_events"] / max(state["decisions_seen"], 1)) * 100
        if override_rate > 20:
            alerts.append(f"Override rate {override_rate:.1f}% — classifier may be miscalibrated")

        correction_rate = (state["correction_follows"] / max(state["decisions_seen"], 1)) * 100
        if correction_rate > 15:
            alerts.append(f"Correction-follow rate {correction_rate:.1f}% — quality regression signal")

        if alerts:
            print("REGRESSION ALERTS:")
            for a in alerts:
                print(f"  [WARN] {a}")
            print()
        else:
            print("Regression alerts: none (all learning metrics within thresholds)")
            print()

    # ========================================================================
    # v2.3: Human behavioral metrics (Katanforoosh gap closure)
    # ========================================================================
    try:
        human_state = brain_learner.summarize_human_state(days=30)
        if "error" not in human_state:
            print("=======================================")
            print("  HUMAN METRICS (30-day)")
            print("=======================================")
            total_d = human_state["total_decisions"]
            total_s = human_state["total_sessions"]
            print(f"Decisions:            {total_d} across {total_s} sessions")
            print(f"Override rate:        {human_state['override_rate']*100:.1f}%")
            per_tier = human_state.get("override_rate_by_tier", {})
            if per_tier:
                tier_str = "  ".join(f"{t}: {v*100:.1f}%" for t, v in per_tier.items())
                print(f"  By tier:            {tier_str}")
            print(f"Reprompt rate:        {human_state['reprompt_rate']*100:.1f}%")
            print(f"Abandonment rate:     {human_state['abandonment_rate']*100:.1f}%")

            trust = human_state.get("trust_calibration", {})
            trust_label = "healthy" if trust.get("healthy") else "uncalibrated"
            if trust.get("avg_confidence_on_correction") is not None:
                print(
                    f"Trust calibration:    {trust_label}"
                    f" (conf on correction={trust['avg_confidence_on_correction']:.3f},"
                    f" conf on normal={trust.get('avg_confidence_on_normal', '?')})"
                )

            modes = human_state.get("delegation_modes_pct", {})
            if modes:
                mode_str = "  ".join(f"{m}={p}%" for m, p in modes.items())
                print(f"Delegation modes:     {mode_str}")

            p50 = human_state.get("inter_turn_gap_p50_s")
            p95 = human_state.get("inter_turn_gap_p95_s")
            if p50 is not None:
                print(f"Inter-turn gap:       {p50:.0f}s (p50) / {p95:.0f}s (p95)")
            print()
    except Exception:
        pass  # human metrics are additive — don't break scan on failure

    # ========================================================================
    # v2.3: Governance summary (Katanforoosh gap closure Phase 4B)
    # ========================================================================
    try:
        gov_path = Path(__file__).parent.parent / "governance" / "audit_protocol.py"
        if gov_path.exists():
            # Import dynamically to avoid hard dependency
            import importlib.util
            spec = importlib.util.spec_from_file_location("audit_protocol", gov_path)
            if spec and spec.loader:
                audit_mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(audit_mod)
                events = audit_mod.build_unified_events(days=30)
                sacred = audit_mod.audit_sacred_rules(events)
                risk_count = sum(len(e.get("risk_flags", [])) for e in events)

                print("=======================================")
                print("  GOVERNANCE (30-day)")
                print("=======================================")
                print(f"Auditable events:     {len(events)}")
                print(f"Risk flags:           {risk_count}")
                print(f"Sacred Rules:         {sacred['compliant']}/{sacred['total_rules']}"
                      f" ({sacred['compliance_rate']}%)")
                if sacred["violations"]:
                    for v in sacred["violations"][:3]:
                        print(f"  [VIOLATION] Rule #{v['rule']}: {v['description']}")
                else:
                    print("  All clear — no violations detected.")
                print()
    except Exception:
        pass  # governance is additive — don't break scan on failure

    return 0


# =============================================================================
# brain audit-skills
# =============================================================================


def cmd_audit_skills(args: list[str]) -> int:
    manifest = load_manifest(DEFAULT_MANIFEST_PATH)
    skills = manifest.get("skills", {})
    tier_map = manifest.get("tier_map", {})

    print("=======================================")
    print("  BRAIN AUDIT - Skill tier assignments")
    print("=======================================")
    print()

    by_tier: dict[str, list[str]] = {}
    for skill_name, tier in skills.items():
        by_tier.setdefault(tier, []).append(skill_name)

    for tier in ["S0", "S1", "S2", "S3", "S4", "S5"]:
        if tier not in by_tier:
            continue
        cfg = tier_map.get(tier, {})
        print(f"{tier} - {cfg.get('description', '')}")
        print(f"     -> {cfg.get('model', '?')} / {cfg.get('effort', '?')}")
        for skill in sorted(by_tier[tier]):
            print(f"     . {skill}")
        print()

    skills_dir = Path.home() / ".claude" / "skills"
    if skills_dir.exists():
        local_skills = {d.name for d in skills_dir.iterdir() if d.is_dir()}
        unassigned = local_skills - set(skills.keys())
        if unassigned:
            print("Skills present but not assigned a tier (will fall back to score-based routing):")
            for s in sorted(unassigned):
                print(f"  . {s}")
            print()
    return 0


# =============================================================================
# brain pin
# =============================================================================


def cmd_pin(args: list[str]) -> int:
    if not args:
        print("usage: brain pin SKILL [--write]", file=sys.stderr)
        return 2

    skill_name = args[0]
    write_mode = "--write" in args

    manifest = load_manifest(DEFAULT_MANIFEST_PATH)
    skills = manifest.get("skills", {})

    if skill_name not in skills:
        print(f"ERROR: skill '{skill_name}' not in routing_manifest.toml [skills]", file=sys.stderr)
        print("Add it to the manifest first, or pick an assigned skill.", file=sys.stderr)
        return 1

    tier = skills[skill_name]
    tier_cfg = manifest.get("tier_map", {}).get(tier, {})
    model = tier_cfg.get("model", "sonnet")
    effort = tier_cfg.get("effort", "high")

    skill_path = Path.home() / ".claude" / "skills" / skill_name / "SKILL.md"
    if not skill_path.exists():
        print(f"ERROR: {skill_path} not found", file=sys.stderr)
        return 1

    content = skill_path.read_text(encoding="utf-8")

    if content.startswith("---\n"):
        end = content.find("\n---\n", 4)
        if end == -1:
            print(f"ERROR: malformed frontmatter in {skill_path}", file=sys.stderr)
            return 1
        frontmatter = content[4:end]
        body = content[end + 5:]
        fm_lines = [
            line for line in frontmatter.split("\n")
            if not line.lstrip().startswith("model:") and not line.lstrip().startswith("effort:")
        ]
        fm_lines.append(f"model: {model}")
        fm_lines.append(f"effort: {effort}")
        new_content = "---\n" + "\n".join(fm_lines) + "\n---\n" + body
    else:
        new_content = f"---\nmodel: {model}\neffort: {effort}\n---\n" + content

    print(f"Target: {skill_path}")
    print(f"  tier:   {tier}")
    print(f"  model:  {model}")
    print(f"  effort: {effort}")
    print()

    if write_mode:
        skill_path.write_text(new_content, encoding="utf-8")
        print(f"OK: wrote {skill_path}")
    else:
        print("Dry-run. Add --write to apply.")
    return 0


# =============================================================================
# brain apply-env
# =============================================================================


def cmd_apply_env(args: list[str]) -> int:
    print("# Toke Brain - persistent environment exports")
    print("# Add to ~/.bashrc or ~/.bash_profile (or source directly)")
    print()
    print('export CLAUDE_CODE_SUBAGENT_MODEL="sonnet"              # Route all subagents to Sonnet by default')
    print('export CLAUDE_CODE_EFFORT_LEVEL="max"                   # Persist /effort max (otherwise manual each session)')
    print('export ANTHROPIC_DEFAULT_HAIKU_MODEL="claude-haiku-4-5" # Pin background Haiku version')
    print()
    print("# Aggressive cost mode (uncomment to enable):")
    print('# export ANTHROPIC_MODEL="opusplan"                     # Auto Opus->Sonnet on plan->execute')
    return 0


# =============================================================================
# brain hook (UserPromptSubmit)
# =============================================================================


def cmd_hook(args: list[str]) -> int:
    """UserPromptSubmit hook. Reads Claude Code hook JSON from stdin. Fails silent.

    v2.0: passes context_history to classify for multi-turn awareness.
    v2.0: correction detection runs inside classify via manifest keywords.
    """
    TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
    log_file = TELEMETRY_DIR / "decisions.jsonl"

    try:
        hook_input = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        return 0

    prompt_text = hook_input.get("prompt", hook_input.get("prompt_text", ""))
    current_model = hook_input.get("model", "")
    # Fallback: Claude Code hook JSON does not include the active session model.
    # Read it from ~/.claude/settings.json — the canonical source for /model setting.
    if not current_model:
        try:
            settings_path = Path.home() / ".claude" / "settings.json"
            _settings = json.loads(settings_path.read_text(encoding="utf-8"))
            current_model = _settings.get("model", "")
        except Exception:
            pass
    session_id = hook_input.get("session_id", "")
    try:
        context_tokens = int(hook_input.get("context_tokens", 0))
    except (TypeError, ValueError):
        context_tokens = 0

    # v2.0: fetch multi-turn context (last 3 decisions in same session)
    # v2.1: also compute session high-water mark tier (single file read)
    context_history: list[dict[str, Any]] = []
    session_max_tier: str | None = None
    if session_id:
        try:
            all_decisions = brain_learner.read_decisions(limit=500)
            session_decisions = [d for d in all_decisions if d.get("session_id") == session_id]
            context_history = session_decisions[-3:] if session_decisions else []
            # Compute session high-water mark tier
            if session_decisions:
                _tier_order = ["S0", "S1", "S2", "S3", "S4", "S5"]
                _max_idx = -1
                for _d in session_decisions:
                    _r = _d.get("result") or {}
                    _t = _r.get("tier", "S0")
                    try:
                        _idx = _tier_order.index(_t)
                        _max_idx = max(_max_idx, _idx)
                    except ValueError:
                        pass
                if _max_idx >= 0:
                    session_max_tier = _tier_order[_max_idx]
        except Exception:
            context_history = []
            session_max_tier = None

    # v2.4: extract CWD for domain-scoped guardrails
    hook_cwd = hook_input.get("cwd", "")

    # v2.5: active_skill inference — detect skill trigger in prompt → tier floor via skill_map
    # classify() has a full [skills] floor mechanism but cmd_hook never passed skill_name.
    # Simple prefix match covers all skill invocations (direct + slash command).
    _SKILL_TRIGGERS: dict[str, str] = {
        "godspeed": "godspeed", "/godspeed": "godspeed",
        "holy-trinity": "holy-trinity", "/holy-trinity": "holy-trinity",
        "devteam": "devTeam", "/devteam": "devTeam",
        "profteam": "profTeam", "/profteam": "profTeam",
        "blueprint": "blueprint", "/blueprint": "blueprint",
        "professor": "professor", "/professor": "professor",
        "bionics": "bionics", "/bionics": "bionics",
        "debug": "debug", "/debug": "debug",
        "init": "init", "/init": "init",
        "verify": "verify", "/verify": "verify",
        "sitrep": "sitrep", "/sitrep": "sitrep",
        "close-session": "close-session", "/close-session": "close-session",
        "toke init": "toke-init", "sworder init": "sworder-init",
        "brain scan": "brain", "brain audit": "brain", "brain history": "brain",
    }
    inferred_skill: str | None = None
    _prompt_lower = prompt_text.strip().lower()
    for _trigger, _skill in _SKILL_TRIGGERS.items():
        if _prompt_lower == _trigger or _prompt_lower.startswith(_trigger + " "):
            inferred_skill = _skill
            break

    try:
        result = classify(
            prompt_text=prompt_text,
            context_tokens=context_tokens,
            current_model=current_model,
            context_history=context_history,
            session_max_tier=session_max_tier,
            cwd=hook_cwd,
            skill_name=inferred_skill,
        )
    except Exception:
        return 0

    # v2.1: LLM fallback for low-confidence uncertainty-escalated classifications.
    # Uses compiled DSPy few-shot demos via Haiku for precise routing on ambiguous prompts.
    # Only fires when rule-based classifier is genuinely uncertain (confidence < 0.30).
    # Cost: ~$0.003 per call. Latency: 1-2s. Frequency: ~10-20% of prompts.
    # v2.6.3 (G7 fix, 2026-04-17): LLM override must NEVER downgrade the pre-escalation
    # tier. Before this guard, an uncertainty-escalated S2→S3 could be silently dropped
    # to S1 if the LLM disagreed — losing an entire tier of trust without telemetry.
    # Now: llm_tier is clamped to max(llm_tier, pre_escalation_tier).
    if result.uncertainty_escalated and result.confidence < 0.30:
        try:
            from brain_llm_classifier import llm_classify  # noqa: E402 — lazy import, fail silent
            llm_tier = llm_classify(prompt_text)
            if llm_tier and llm_tier in ("S0", "S1", "S2", "S3", "S4", "S5"):
                _tier_map = {
                    "S0": ("haiku", "low", 0), "S1": ("haiku", "medium", 0),
                    "S2": ("sonnet", "medium", 0), "S3": ("sonnet", "high", 16000),
                    "S4": ("opus", "high", 32000), "S5": ("opus[1m]", "max", 64000),
                }
                _tier_order = ("S0", "S1", "S2", "S3", "S4", "S5")
                # G7 floor: the rule-based `result.tier` is the pre-LLM classification.
                # If LLM says lower, clamp back up to it. If LLM says same-or-higher, use LLM.
                pre_tier = result.tier
                if _tier_order.index(llm_tier) < _tier_order.index(pre_tier):
                    clamped_tier = pre_tier
                    override_note = f" | llm_suggested:{llm_tier}_clamped_to:{pre_tier}"
                else:
                    clamped_tier = llm_tier
                    override_note = f" | llm_override:{llm_tier}"
                model, effort, thinking = _tier_map[clamped_tier]
                result.tier = clamped_tier
                result.model = model
                result.effort = effort
                result.extended_thinking_budget = thinking
                result.reasoning += override_note
        except Exception:
            pass  # fail silent — LLM fallback is additive, rule-based result preserved

    # v2.3: compute human behavioral metrics (Katanforoosh gap closure)
    human_metrics: dict[str, Any] = {}
    try:
        human_metrics = brain_learner.compute_human_metrics(
            session_id=session_id,
            prompt_text=prompt_text,
            classification_result=result.to_json(),
        )
    except Exception:
        pass  # fail silent — human metrics are additive, not critical

    # v2.4: read manual effort override if set
    effort_override: int | None = None
    effort_state_file = Path.home() / ".claude" / "telemetry" / "brain" / "effort_override.txt"
    try:
        if effort_state_file.exists():
            _val = effort_state_file.read_text().strip()
            _lvl = int(_val)
            if 1 <= _lvl <= 5:
                effort_override = _lvl
    except (ValueError, OSError):
        pass

    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "hook": "UserPromptSubmit",
        "session_id": session_id,
        "prompt_text": prompt_text[:500],  # v2.5: capture for golden_set mining + GEPA ASI
        "current_model": current_model,
        "result": result.to_json(),
        "effort_override": effort_override,
        "human": human_metrics,
    }
    try:
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass

    # v2.0: enriched advisory output (tier, confidence, thinking budget if applicable)
    recommended = result.model
    if current_model and recommended:
        cur_lower = current_model.lower()
        rec_lower = recommended.lower()
        if rec_lower not in cur_lower and cur_lower not in rec_lower:
            extras: list[str] = []
            if result.confidence < 0.5:
                extras.append(f"conf={result.confidence:.2f}")
            if result.extended_thinking_budget > 0:
                extras.append(f"thinking={result.extended_thinking_budget}")
            if result.uncertainty_escalated:
                extras.append("escalated")
            extra_str = f" ({', '.join(extras)})" if extras else ""
            print(
                f"[brain] {result.tier} task -> /model {recommended}{extra_str}",
                file=sys.stderr,
            )
    return 0


# =============================================================================
# brain telemetry (PostToolUse)
# =============================================================================


def cmd_telemetry(args: list[str]) -> int:
    """PostToolUse hook. Logs tool call metadata. Fails silent."""
    TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
    log_file = TELEMETRY_DIR / "tools.jsonl"

    try:
        hook_input = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        return 0

    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "hook": "PostToolUse",
        "session_id": hook_input.get("session_id", ""),
        "tool_name": hook_input.get("tool_name", ""),
        "model": hook_input.get("model", ""),
        "input_size": len(json.dumps(hook_input.get("tool_input", {}))) if hook_input.get("tool_input") else 0,
        "output_size": len(str(hook_input.get("tool_result", ""))),
    }
    try:
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass
    return 0


# =============================================================================
# brain test
# =============================================================================


def cmd_test(args: list[str]) -> int:
    """Run the classifier smoke tests."""
    tests_path = Path(__file__).parent / "brain_tests.py"
    if not tests_path.exists():
        print(f"ERROR: {tests_path} not found", file=sys.stderr)
        return 1
    import subprocess
    return subprocess.call([sys.executable, str(tests_path)])


# =============================================================================
# brain help
# =============================================================================


def cmd_help(args: list[str]) -> int:
    print(__doc__)
    return 0


# =============================================================================
# v2.0 commands
# =============================================================================


def cmd_history(args: list[str]) -> int:
    """brain history [N] - show last N routing decisions with tier summary."""
    try:
        n = int(args[0]) if args else 20
    except ValueError:
        n = 20

    decisions = brain_learner.read_decisions(limit=n)
    if not decisions:
        print("No decisions in telemetry yet. Brain hooks may not be wired, or no sessions recorded.")
        return 0

    print(f"Last {len(decisions)} routing decisions (newest first):")
    print()
    print(f"  {'timestamp':<20} {'tier':<5} {'model':<10} {'conf':<5} {'reasoning':<60}")
    print(f"  {'-'*19} {'-'*4} {'-'*9} {'-'*4} {'-'*59}")

    tier_counts: dict[str, int] = defaultdict(int)
    for d in decisions:
        result = d.get("result", {}) or {}
        tier = result.get("tier", "?")
        model = result.get("model", "?")
        conf = result.get("confidence", 0)
        reasoning = (result.get("reasoning", "") or "")[:58]
        ts = (d.get("ts", "") or "")[:19]
        print(f"  {ts:<20} {tier:<5} {model:<10} {conf!s:<5} {reasoning}")
        tier_counts[tier] += 1

    print()
    print("Tier distribution:")
    total = len(decisions)
    for tier in ["S0", "S1", "S2", "S3", "S4", "S5"]:
        count = tier_counts.get(tier, 0)
        pct = (count / total * 100) if total > 0 else 0
        bar = "#" * int(pct / 2)
        print(f"  {tier}: {count:>4} ({pct:>5.1f}%) {bar}")
    return 0


def cmd_budget(args: list[str]) -> int:
    """brain budget - active session cost tracker against manifest thresholds."""
    manifest = load_manifest(DEFAULT_MANIFEST_PATH)
    budget_cfg = manifest.get("budget", {})
    if not budget_cfg.get("enabled", False):
        print("Budget tracking disabled. Set [budget].enabled = true in routing_manifest.toml.")
        return 0

    pricing = manifest.get("models", {})
    tools = brain_learner.read_tools(limit=2000)

    active = brain_learner.compute_active_session_cost(tools, pricing)
    cost = active.get("total_cost_usd", 0.0)
    session_id = active.get("session_id", "?") or "?"
    tool_count = active.get("tool_count", 0)

    session_warn = float(budget_cfg.get("session_warn_usd", 2.00))
    session_alert = float(budget_cfg.get("session_alert_usd", 10.00))
    daily_warn = float(budget_cfg.get("daily_warn_usd", 50.00))
    daily_alert = float(budget_cfg.get("daily_alert_usd", 150.00))

    status = "OK"
    if cost >= session_alert:
        status = "ALERT"
    elif cost >= session_warn:
        status = "WARN"

    print("=======================================")
    print("  BRAIN BUDGET - Session Cost Tracker")
    print("=======================================")
    print(f"Active session:    {session_id}")
    print(f"Tool calls logged: {tool_count}")
    print(f"Estimated cost:    ${cost:.4f}")
    print(f"Status:            {status}")
    print()
    print(f"Session thresholds:  warn=${session_warn:.2f}  alert=${session_alert:.2f}")
    print(f"Daily thresholds:    warn=${daily_warn:.2f}  alert=${daily_alert:.2f}")
    print()

    by_model = active.get("by_model", {}) or {}
    if by_model:
        print("Session cost by model:")
        for model, c in sorted(by_model.items(), key=lambda kv: kv[1], reverse=True):
            print(f"  {model:<45} ${c:.4f}")
        print()

    print("Note: costs are estimates from tool telemetry (4-chars-per-token heuristic).")
    print("      For actual API costs, cross-reference with stats-cache.json via `brain scan`.")
    return 0


def cmd_tune(args: list[str]) -> int:
    """brain tune [--write] - propose manifest weight adjustments from telemetry."""
    manifest = load_manifest(DEFAULT_MANIFEST_PATH)
    learning_cfg = manifest.get("learning", {})
    if not learning_cfg.get("enabled", False):
        print("Learning disabled in manifest. Set [learning].enabled = true to enable.")
        return 0

    decisions = brain_learner.read_decisions(limit=2000)
    if not decisions:
        print("No telemetry data yet. Run a few Claude Code sessions first, then retry.")
        return 0

    current_weights = manifest.get("weights", {})
    overrides = brain_learner.detect_overrides(decisions)

    print("=======================================")
    print("  BRAIN TUNE - Weight Adjustment Proposal")
    print("=======================================")
    print(f"Decisions analyzed:  {len(decisions)}")
    print(f"Override events:     {len(overrides)}")
    print()

    if not overrides:
        print("No override events detected. Classifier appears well-calibrated.")
        print("Re-run after more telemetry accumulates (>=50 overrides recommended for meaningful tuning).")
        return 0

    alpha = float(learning_cfg.get("alpha", 0.005))
    proposed = brain_learner.propose_weight_adjustments(current_weights, overrides, alpha=alpha)

    print(f"Proposed adjustments (alpha={alpha}):")
    print()
    print(f"  {'signal':<15} {'current':<10} {'proposed':<10} {'delta':<10}")
    print(f"  {'-'*14} {'-'*9} {'-'*9} {'-'*9}")
    any_changed = False
    for key in sorted(current_weights.keys()):
        old_val = float(current_weights.get(key, 0.0))
        new_val = float(proposed.get(key, old_val))
        delta = new_val - old_val
        marker = " *" if abs(delta) > 1e-6 else ""
        if abs(delta) > 1e-6:
            any_changed = True
        print(f"  {key:<15} {old_val:<10.4f} {new_val:<10.4f} {delta:+.4f}{marker}")
    print()

    if not any_changed:
        print("No adjustments needed. Current weights are stable under current telemetry.")
        return 0

    if "--write" in args:
        print("NOTE: Automatic manifest rewrite is deferred to v3 for safety.")
        print("To apply these adjustments, manually edit routing_manifest.toml [weights] section")
        print("with the 'proposed' column values above.")
    else:
        print("Dry-run. Use --write to see application instructions.")
    return 0


def cmd_feedback(positive: bool) -> int:
    """Record explicit positive/negative feedback on last decision. Internal helper."""
    decisions = brain_learner.read_decisions(limit=1)
    if not decisions:
        print("No decisions in telemetry to mark. Wire the hook first.", file=sys.stderr)
        return 1

    last = decisions[0]
    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "type": "feedback",
        "positive": positive,
        "decision_ts": last.get("ts"),
        "session_id": last.get("session_id"),
        "last_tier": (last.get("result") or {}).get("tier"),
        "last_model": (last.get("result") or {}).get("model"),
        "weight_multiplier": 10,
    }

    TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
    feedback_log = TELEMETRY_DIR / "feedback.jsonl"
    try:
        with feedback_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print(f"ERROR writing feedback: {e}", file=sys.stderr)
        return 1

    label = "positive" if positive else "negative"
    tier = entry["last_tier"]
    model = entry["last_model"]
    print(f"Recorded {label} feedback for last decision: {tier} ({model})")
    return 0


def cmd_good(args: list[str]) -> int:
    """brain good - mark last decision as positive (10x implicit signal weight)."""
    return cmd_feedback(positive=True)


def cmd_bad(args: list[str]) -> int:
    """brain bad - mark last decision as negative (10x implicit signal weight)."""
    return cmd_feedback(positive=False)


def cmd_advise(args: list[str]) -> int:
    """brain advise PROMPT [--executor MODEL] [--advisor MODEL] [--max-uses N] [--max-tokens N]

    Call Anthropic's advisor_20260301 API tool directly. Executor drives, escalates to
    Opus via the advisor tool only when stuck. Runs OUTSIDE Claude Code as a separate
    API call using ANTHROPIC_API_KEY from env.

    Example:
        brain advise "refactor this EXO subsystem pattern across 4 files"
        brain advise "prove this is O(n log n)" --executor sonnet --advisor opus --max-uses 3
    """
    if not args:
        print("usage: brain advise PROMPT [--executor MODEL] [--advisor MODEL] [--max-uses N] [--max-tokens N]", file=sys.stderr)
        return 2

    # Parse args
    prompt_parts: list[str] = []
    executor = "claude-sonnet-4-6"
    advisor_model = "claude-opus-4-6"
    max_uses = 3
    max_tokens = 4096

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--executor" and i + 1 < len(args):
            executor = args[i + 1]
            i += 2
        elif a == "--advisor" and i + 1 < len(args):
            advisor_model = args[i + 1]
            i += 2
        elif a == "--max-uses" and i + 1 < len(args):
            try:
                max_uses = int(args[i + 1])
            except ValueError:
                print(f"ERROR: --max-uses must be an integer, got {args[i + 1]}", file=sys.stderr)
                return 2
            i += 2
        elif a == "--max-tokens" and i + 1 < len(args):
            try:
                max_tokens = int(args[i + 1])
            except ValueError:
                print(f"ERROR: --max-tokens must be an integer, got {args[i + 1]}", file=sys.stderr)
                return 2
            i += 2
        elif a == "--stdin":
            prompt_parts.append(sys.stdin.read())
            i += 1
        else:
            prompt_parts.append(a)
            i += 1

    prompt = " ".join(p for p in prompt_parts if p).strip()
    if not prompt:
        print("ERROR: empty prompt", file=sys.stderr)
        return 2

    # Check prerequisites
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set. Cannot call advisor API.", file=sys.stderr)
        print("Set via: export ANTHROPIC_API_KEY='sk-ant-...'", file=sys.stderr)
        return 1

    try:
        import anthropic  # type: ignore
    except ImportError:
        print("ERROR: anthropic SDK not installed.", file=sys.stderr)
        print("Install via: pip install anthropic", file=sys.stderr)
        return 1

    client = anthropic.Anthropic()

    print(f"Calling advisor_20260301 API:")
    print(f"  executor: {executor}")
    print(f"  advisor:  {advisor_model}")
    print(f"  max_uses: {max_uses}")
    print(f"  prompt:   {prompt[:120]}{'...' if len(prompt) > 120 else ''}")
    print()
    print("Waiting for response...")
    print()

    try:
        response = client.messages.create(
            model=executor,
            max_tokens=max_tokens,
            tools=[{
                "type": "advisor_20260301",
                "name": "advisor",
                "model": advisor_model,
                "max_uses": max_uses,
            }],
            messages=[{"role": "user", "content": prompt}],
            extra_headers={"anthropic-beta": "advisor-tool-2026-03-01"},
        )
    except Exception as e:
        err_name = type(e).__name__
        print(f"ERROR: {err_name}: {e}", file=sys.stderr)
        print(file=sys.stderr)
        print("Possible causes:", file=sys.stderr)
        print("  - advisor_20260301 requires beta access to your plan", file=sys.stderr)
        print("  - Beta header 'advisor-tool-2026-03-01' may have changed or expired", file=sys.stderr)
        print("  - Your API key lacks the advisor tool permission", file=sys.stderr)
        print("  - Verify at: https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool", file=sys.stderr)
        return 1

    # Parse response content blocks
    executor_text_parts: list[str] = []
    advisor_advice_parts: list[str] = []
    advisor_calls = 0

    for block in getattr(response, "content", []) or []:
        block_type = getattr(block, "type", "")
        if block_type == "text":
            executor_text_parts.append(getattr(block, "text", ""))
        elif block_type == "server_tool_use":
            if getattr(block, "name", "") == "advisor":
                advisor_calls += 1
        elif block_type in ("advisor_tool_result", "advisor_result"):
            # Try multiple content shapes
            content = getattr(block, "content", None)
            if content is None:
                text = getattr(block, "text", "")
                if text:
                    advisor_advice_parts.append(text)
            elif isinstance(content, str):
                advisor_advice_parts.append(content)
            elif isinstance(content, list):
                for item in content:
                    if getattr(item, "type", "") == "text":
                        advisor_advice_parts.append(getattr(item, "text", ""))

    print("=" * 60)
    print("  EXECUTOR OUTPUT")
    print("=" * 60)
    if executor_text_parts:
        print("\n".join(executor_text_parts).strip())
    else:
        print("(no text content from executor)")
    print()

    if advisor_calls > 0:
        print("=" * 60)
        print(f"  ADVISOR CONSULTATIONS ({advisor_calls} calls)")
        print("=" * 60)
        if advisor_advice_parts:
            for idx, advice in enumerate(advisor_advice_parts, 1):
                print(f"--- advice #{idx} ---")
                print(advice.strip())
                print()
        else:
            print("(advisor was invoked but no advice text extracted from response blocks)")
        print()
    else:
        print("No advisor consultations - executor handled the task solo.")
        print()

    # Usage breakdown
    usage = getattr(response, "usage", None)
    if usage:
        print("=" * 60)
        print("  USAGE + COST")
        print("=" * 60)
        in_tok = getattr(usage, "input_tokens", 0)
        out_tok = getattr(usage, "output_tokens", 0)
        cache_read = getattr(usage, "cache_read_input_tokens", 0)
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0)
        print(f"Total input tokens:    {in_tok}")
        print(f"Total output tokens:   {out_tok}")
        print(f"Cache read tokens:     {cache_read}")
        print(f"Cache creation tokens: {cache_creation}")

        iterations = getattr(usage, "iterations", None)
        if iterations:
            print()
            print("Iterations (executor vs advisor):")
            for idx, it in enumerate(iterations, 1):
                it_type = getattr(it, "type", "?")
                it_model = getattr(it, "model", "?")
                it_in = getattr(it, "input_tokens", 0)
                it_out = getattr(it, "output_tokens", 0)
                print(f"  [{idx}] {it_type:<20} {it_model:<24} in={it_in:<8} out={it_out}")

        # Rough cost estimate using manifest pricing
        try:
            manifest = load_manifest(DEFAULT_MANIFEST_PATH)
            pricing = manifest.get("models", {})
            if "sonnet" in executor.lower():
                prices = pricing.get("sonnet", {})
            elif "haiku" in executor.lower():
                prices = pricing.get("haiku", {})
            else:
                prices = pricing.get("opus", {})
            if prices:
                cost = (
                    (in_tok / 1_000_000) * prices.get("cost_input_per_mtok", 0)
                    + (out_tok / 1_000_000) * prices.get("cost_output_per_mtok", 0)
                    + (cache_read / 1_000_000) * prices.get("cost_cache_read_per_mtok", 0)
                )
                print()
                print(f"Estimated cost (executor only, excludes advisor tokens): ${cost:.4f}")
        except Exception:
            pass
        print()

    # Telemetry log
    try:
        TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
        log_file = TELEMETRY_DIR / "advisor_calls.jsonl"
        log_entry = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "type": "advisor_call",
            "executor": executor,
            "advisor_model": advisor_model,
            "max_uses": max_uses,
            "advisor_calls_used": advisor_calls,
            "prompt_preview": prompt[:200],
            "executor_output_chars": sum(len(p) for p in executor_text_parts),
            "advisor_advice_chars": sum(len(p) for p in advisor_advice_parts),
        }
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")
    except Exception:
        pass

    return 0


def cmd_advisor_status(args: list[str]) -> int:
    """brain advisor-status - check Anthropic advisor_20260301 integration status."""
    manifest = load_manifest(DEFAULT_MANIFEST_PATH)
    advisor_cfg = manifest.get("advisor", {})

    print("=======================================")
    print("  BRAIN ADVISOR STATUS")
    print("=======================================")
    print(f"Manifest version:            {manifest.get('version', '?')}")
    print(f"Advisor enabled in manifest: {advisor_cfg.get('enabled_when_available', False)}")
    print(f"Beta header:                 {advisor_cfg.get('beta_header', 'N/A')}")
    print()
    print("Documentation:")
    print(f"  Docs: {advisor_cfg.get('docs_url', 'N/A')}")
    print(f"  Blog: {advisor_cfg.get('blog_url', 'N/A')}")
    print()

    pairs = advisor_cfg.get("pairs", []) or []
    if pairs:
        print("Recommended executor -> advisor pairs:")
        for pair in pairs:
            executor = pair.get("executor", "?")
            advisor = pair.get("advisor", "?")
            best_for = pair.get("best_for", "?")
            print(f"  {executor:<8} -> {advisor:<8}  best for: {best_for}")
        print()

    config = advisor_cfg.get("config", {}) or {}
    print(f"max_uses default:  {config.get('max_uses_default', 3)}")
    print(f"cache TTL:         {config.get('cache_ttl', '5m')}")
    print()
    print("Native Claude Code /advisor slash command:  NOT DETECTED (as of 2026-04-10)")
    print()
    print("Status: The Brain manifest is v3-ready. When Claude Code adds a native")
    print("/advisor slash command (or when the API tool is wired into subagent hooks),")
    print("Brain will auto-recommend advisor mode for S3/S4 tasks with multi-step flags.")
    print("See research/brain_v2_sota_synthesis_2026-04-10.md Part 1 for full integration plan.")
    return 0


# =============================================================================
# Dispatch
# =============================================================================


# =============================================================================
# brain godspeed-tick (v2.1 — periodic self-audit counter)
# =============================================================================


def cmd_godspeed_tick(args: list[str]) -> int:
    """brain godspeed-tick [THRESHOLD]

    Increment the persistent godspeed invocation counter. Every THRESHOLD
    fires (default 33), auto-run `brain scan` inline for periodic self-audit
    and learning compilation.

    Counter file: ~/.claude/telemetry/brain/godspeed_count.txt
    Called by godspeed SKILL.md Phase -1 at every invocation.

    Exit codes:
        0 - tick recorded (scan triggered OR silent pass)
        1 - counter write failed (non-fatal; godspeed continues)

    Example:
        brain godspeed-tick          # defaults to 33
        brain godspeed-tick 50       # custom threshold
    """
    threshold = 33
    if args and args[0].isdigit():
        threshold = max(int(args[0]), 1)

    TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
    counter_file = TELEMETRY_DIR / "godspeed_count.txt"

    current = 0
    if counter_file.exists():
        try:
            current = int(counter_file.read_text(encoding="utf-8").strip() or "0")
        except (ValueError, OSError):
            current = 0  # corrupt or unreadable, reset

    new_count = current + 1

    try:
        counter_file.write_text(str(new_count), encoding="utf-8")
    except OSError as e:
        print(f"ERROR: failed to write godspeed counter: {e}", file=sys.stderr)
        return 1

    scan_triggered = (new_count % threshold == 0)

    if scan_triggered:
        print(f"=== GODSPEED TICK {new_count} - THRESHOLD HIT (every {threshold}) ===")
        print(f"Periodic self-audit: auto-running `brain scan` to compile learnings.")
        print()
        return cmd_scan([])

    remaining = threshold - (new_count % threshold)
    next_scan_at = new_count + remaining
    print(f"Godspeed tick: {new_count} | next auto-scan at {next_scan_at} ({remaining} runs away)")
    return 0


COMMANDS = {
    "score": cmd_score,
    "scan": cmd_scan,
    "audit-skills": cmd_audit_skills,
    "pin": cmd_pin,
    "apply-env": cmd_apply_env,
    "hook": cmd_hook,
    "telemetry": cmd_telemetry,
    "test": cmd_test,
    # v2.0
    "history": cmd_history,
    "budget": cmd_budget,
    "tune": cmd_tune,
    "good": cmd_good,
    "bad": cmd_bad,
    "advisor-status": cmd_advisor_status,
    "advise": cmd_advise,
    # v2.1
    "godspeed-tick": cmd_godspeed_tick,
    "help": cmd_help,
    "-h": cmd_help,
    "--help": cmd_help,
}


def main() -> int:
    if len(sys.argv) < 2:
        return cmd_help([])
    cmd = sys.argv[1]
    args = sys.argv[2:]
    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print(file=sys.stderr)
        return cmd_help([])
    return COMMANDS[cmd](args)


if __name__ == "__main__":
    sys.exit(main())
