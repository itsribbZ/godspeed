---
name: local
description: Toke Local LLM workbench — routes brain-classified S0/S1/S2 prompts to a local Ollama model (Qwen 2.5 14B) with softmax-entropy confidence monitoring. Critical Logic Override gate hands work back to Claude only when local confidence drops below threshold AND the user explicitly approves. Toke-native — uses Brain's classifier upstream, writes to its own local_decisions.jsonl downstream. Source: ~/Desktop/T1/Toke/automations/local/
model: sonnet
effort: medium
---

# Toke Local — Workbench

This skill is the entry point for Toke's local LLM automation. It runs alongside the existing Brain router — Brain classifies the prompt's tier, Local decides whether to handle it on-device.

## Architecture

```
prompt
  ▼
brain.classify()           ← existing Toke brain
  ▼
tier ∈ {S0,S1,S2}? ────────────────────► local Qwen via Ollama
                                              ▼
tier ∈ {S3,S4,S5} ──► Claude (default)        confidence_monitor
                                              ▼
                                    is_critical (< 12.5%) ?
                                              │
                                          yes │   no
                                              ▼   ▼
                                       claude_override   return
                                          gate (y/n)
                                              ▼
                                       approved? → Claude
                                       declined? → use local
```

## Canonical paths

- CLI:        `~/Desktop/T1/Toke/automations/local/local_cli.py`
- Manifest:   `~/Desktop/T1/Toke/automations/local/local_manifest.toml`
- Decisions:  `~/.claude/telemetry/local/local_decisions.jsonl`

## Commands

### `local query "prompt"`
Run a prompt through the full pipeline: brain classification → local-eligible check → Qwen generation → confidence monitor → optional override.

```bash
python "~/Desktop/T1/Toke/automations/local/local_cli.py" query "summarize the routing manifest"
```

### `local ping`
Health check: is Ollama serving? Is the configured model present?

```bash
python "~/Desktop/T1/Toke/automations/local/local_cli.py" ping
```

### `local stats [N]`
Show aggregate stats from the last N decisions: override rate, approval rate, avg confidence, token savings vs all-Claude.

```bash
python "~/Desktop/T1/Toke/automations/local/local_cli.py" stats 100
```

### `local config [key=value]`
Show or update local_manifest.toml settings. With no args, prints current config.

```bash
python "~/Desktop/T1/Toke/automations/local/local_cli.py" config
python "~/Desktop/T1/Toke/automations/local/local_cli.py" config threshold=0.15
```

### `local test`
Smoke-test the pipeline with 3 representative prompts (one per S0/S1/S2 tier).

```bash
python "~/Desktop/T1/Toke/automations/local/local_cli.py" test
```

## Hard constraints

1. **Claude API NEVER fires without explicit approval.** The override gate is a blocking input prompt (terminal mode) or a pending state requiring POST /approve_override (api mode). No code path bypasses this.
2. **Brain classifier is the source of truth** for tier assignment. Local does NOT re-classify — it calls Brain's `classify()`.
3. **Tier S3+ never auto-runs locally.** Brain says S3+, Local hands off immediately.
4. **`keep_alive=0` by default** for memory-pressured rigs (8GB GPU). Override via manifest.

## Configuration (local_manifest.toml)

```toml
[ollama]
base_url    = "http://localhost:11434"
model       = "qwen2.5:14b-instruct-q4_K_M"
temperature = 0.8
num_ctx     = 4096
keep_alive  = 0          # 0 = unload after each request (stable on 8GB GPU)

[confidence]
threshold       = 0.125  # 12.5% — below this triggers override gate
mode_priority   = ["logprobs", "self_consistency", "model_stated"]

[routing]
local_eligible_tiers = ["S0", "S1", "S2"]
override_mode        = "terminal"   # terminal | api
```

## Workflow

1. `local ping` — verify Ollama + model
2. `local query "your prompt"` — single-shot test
3. `local test` — full smoke test
4. `local stats` — observe routing patterns
5. Adjust `threshold` in `local_manifest.toml` based on observed override rate

## Hard cap

Routes only S0/S1/S2 tier work. Anything Brain classifies as S3+ falls through to standard Claude execution. This keeps the local model from attempting work beyond its quality range.
