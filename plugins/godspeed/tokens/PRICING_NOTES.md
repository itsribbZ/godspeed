# Pricing Notes — Toke

> **Status:** VERIFIED 2026-04-11 against `platform.claude.com/docs/en/docs/about-claude/pricing`.
> Manifest is **CORRECT** for Claude Opus/Sonnet/Haiku 4.6 generation.

## Verified pricing (Anthropic docs, 2026-04-11)

| Model             | Input $/MTok | Cache-5m $/MTok | Cache-1h $/MTok | Cache-read $/MTok | Output $/MTok |
|-------------------|--------------|------------------|-----------------|-------------------|---------------|
| Claude Opus 4.6   | **$5.00**    | $6.25            | $10.00          | **$0.50**         | **$25.00**    |
| Claude Opus 4.5   | $5.00        | $6.25            | $10.00          | $0.50             | $25.00        |
| Claude Opus 4.1   | $15.00       | $18.75           | $30.00          | $1.50             | $75.00        |
| Claude Opus 4     | $15.00       | $18.75           | $30.00          | $1.50             | $75.00        |
| Claude Sonnet 4.6 | **$3.00**    | $3.75            | $6.00           | **$0.30**         | **$15.00**    |
| Claude Haiku 4.5  | **$1.00**    | $1.25            | $2.00           | **$0.10**         | **$5.00**     |

**Opus 4.5/4.6 got a price drop from Opus 4/4.1 (down 3x).** Earlier notes in this file flagged a "3x under-report" concern — that was wrong. I was confusing Opus 4.0/4.1 pricing with Opus 4.6 pricing.

## Single source of truth
`automations/brain/routing_manifest.toml` `[models.*]` sections.
Both `brain_cli.py scan` and `tokens/token_snapshot.py` read from this file.
Editing the manifest updates every Toke tool that touches cost.

## Current manifest values (verified CORRECT for Opus 4.6)

| Manifest alias | Input | Output | Cache-read | Verdict |
|----------------|-------|--------|------------|---------|
| haiku          | $1.00 | $5.00  | $0.10      | ✓ matches Haiku 4.5 |
| sonnet         | $3.00 | $15.00 | $0.30      | ✓ matches Sonnet 4.6 |
| opus           | $5.00 | $25.00 | $0.50      | ✓ matches Opus 4.6 |
| opus[1m]       | $5.00 | $25.00 | $0.50      | ✓ 1M context is at standard rate per long-context pricing |

## Long-context pricing clarification
From Anthropic docs: *"Claude Mythos Preview, Opus 4.6 and Sonnet 4.6 include the full 1M token context window at standard pricing. (A 900k-token request is billed at the same per-token rate as a 9k-token request.)"*
So `opus[1m]` is NOT 2x — it's the **same rate** as 200K Opus 4.6. Manifest correct.

## Cache write formula — one known bug in `brain_cli.py`

`brain_cli.py _price_model` uses `input × 1.25` uniformly for all cache writes, but the two tiers are:
- **5m cache write = base × 1.25** (correct)
- **1h cache write = base × 2.00** (undercounted by 37.5%)

Real usage (from session transcripts): the user's sessions write almost **100% to the 1h cache** (`cache_creation.ephemeral_1h_input_tokens` dominates, `ephemeral_5m_input_tokens` ≈ 0). So the undercount is ~$1,700/mo systematic across the 30-day Opus cache write volume of 455M tokens.

### Fix plan
1. Update `_price_model` to use `× 2.0` instead of `× 1.25` for the cache-write term. This is the conservative choice because stats-cache.json does NOT split 5m/1h — we can't tell the difference at the aggregate level, and the user's real usage is 1h-heavy.
2. For `token_snapshot.py`, we CAN split 5m/1h because transcripts have `cache_creation.ephemeral_*_input_tokens`. That tool is already correct.

## Fast mode warning
`/fast` mode charges **6x standard** ($30 input / $150 output on Opus 4.6) AND invalidates cache. Per SL-072, **never use /fast**.

## Batch API opportunity (noted, not claimed)
Batch API = 50% off input and output. For non-time-sensitive Toke research runs, this could halve cost. Not currently used. Worth exploring when Oracle (L7) needs nightly regression runs.

## Footer in tool output
`token_snapshot.py` prints `manifest pricing (verified 2026-04-11)` so the trust level is visible. Update this footer whenever re-verifying.
