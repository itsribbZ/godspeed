---
description: Classify any prompt on the Brain severity scale (S0-S5) — surfaces tier, recommended model, effort, and signal breakdown
argument-hint: "<prompt text to classify — quote multi-word prompts>"
allowed-tools: Bash
---

# /brain-score — Toke Brain Prompt Classifier

Classify the prompt in `$ARGUMENTS` against the live routing manifest.

Arguments received: **$ARGUMENTS**

## Execution

Run this exact command via the Bash tool and echo the full output verbatim — no preamble, no rewrite, no summary. Users want the raw classifier signal.

```bash
python $TOKE_ROOT/automations/brain/brain_cli.py score "$ARGUMENTS"
```

## Output contract

`brain score` prints in this shape (do not reformat):

```
Tier:    S<N>
Model:   <haiku|sonnet|opus>    Effort: <low|medium|high>
Score:   0.<NNN>
Reason:  score=... | top:<signals> | <escalation-notes>

Signals:
  prompt_length   0.NNN  #####
  code_blocks     0.NNN
  file_refs       0.NNN
  reasoning       0.NNN
  ...

Guardrails fired: <list, if any>
```

## Rules

- NEVER ask for confirmation — execute immediately.
- If `$ARGUMENTS` is empty, print: `usage: /brain-score <prompt text>` and stop.
- If the Python command errors, show stderr verbatim — never swallow the error.
- This command is read-only. No file writes, no settings edits.
- Do not add interpretation beyond the raw classifier output unless the user explicitly asks for analysis after.
