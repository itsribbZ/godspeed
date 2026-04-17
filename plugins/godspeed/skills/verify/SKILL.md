---
name: verify
description: Build and deployment verification. Auto-detects project type from the current working directory and runs the matching checks — Python imports, pytest, npm build, CMake build, Go test, Rust cargo, or a custom health script. Returns pass/fail with a terse error summary. Use after any batch of code changes or when answering "does it work?".
model: sonnet
---

# Verify — Build & Test Health Check

Triggers: "verify", "check build", "does it work", "run tests", "health check", "is this green"

---

## What it does

Inspects the current working directory, matches it against a project-type signature, and runs the corresponding verification commands. One pass/fail verdict with an error summary if anything breaks.

Defaults to fail-fast — the first failing command stops the sequence unless the user explicitly asks for a full sweep.

---

## Project-Type Detection

| Signature file | Detected type | Verification commands |
|----------------|---------------|----------------------|
| `pyproject.toml` OR `setup.py` OR `requirements.txt` OR `*.py` at root | Python | `python -m py_compile $(git ls-files '*.py')` → `pytest -x` if a `tests/` dir exists |
| `package.json` | Node / JS | `npm run build` (if defined) → `npm test` (if defined) |
| `CMakeLists.txt` | C / C++ (CMake) | `cmake --build build` → optional `ctest` |
| `Cargo.toml` | Rust | `cargo check` → `cargo test` |
| `go.mod` | Go | `go build ./...` → `go test ./...` |
| `Makefile` | make | `make -n` (dry run) then `make test` if defined |
| `.uproject` | Unreal Engine 5 | SKIP — requires the Editor; report "UE5 project detected, manual verify required" |
| A custom `scripts/verify.sh` or `.verify` file | User-defined | Run it |
| No matches | Unknown | Report the directory contents and ask |

If multiple signatures match (monorepo), run them in parallel and aggregate results.

---

## Output

```
═══════════════════════════════════════
  VERIFY — <date>
═══════════════════════════════════════

PROJECT TYPE: <detected>
CWD: <absolute path>

CHECKS
  [PASS] <command>                   <duration>
  [FAIL] <command>                   <duration>
         <first few lines of error>
  [SKIP] <command>                   <reason>

VERDICT: GREEN | YELLOW | RED

NEXT ACTIONS (if RED):
  1. <specific file:line or error type pointing to the likely cause>
```

- **GREEN** = all PASS
- **YELLOW** = some SKIP, no FAIL
- **RED** = any FAIL

---

## Rules

1. **Never stop/kill/restart long-running processes** without asking first. Verify READS process state; it doesn't manage running services.
2. **Never modify code in this skill.** Verify is a lint, not a fixer. If something fails, report the failure — don't auto-patch.
3. **Fail-open on unknown types.** If no signature matches, report the directory contents and ask the user what to run — don't guess.
4. **Time-box each command to ~60 seconds by default.** If a build reliably takes longer, the user can pass a higher timeout. Never let a verify sweep run for 10+ minutes without explicit user ack.
5. **Respect `.verify-skip` files.** If the project root contains a `.verify-skip` file listing paths, don't traverse them.

---

## Composition with other Toke skills

- **`verify` + `brain-score`** — after a big refactor, run verify, then `brain-score` the next user prompt so the classifier sees the fresh state.
- **`verify` + `close-session`** — close-session runs verify as part of Phase 3 so the session summary reports build health.
- **`verify` + `zeus`** — if Zeus is orchestrating a multi-file change, verify is the final phase before `zeus gate-write` lands the Mnemos entry.
