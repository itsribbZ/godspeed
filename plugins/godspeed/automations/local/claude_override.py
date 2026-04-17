#!/usr/bin/env python3
"""
Toke Local — Claude Override Gate
==================================
Human-in-the-loop critical logic escalation.

When ConfidenceMonitor flags a result as is_critical, this gate:
  1. Formats the situation for the user
  2. PRESENTS IT — terminal block or pending API state
  3. WAITS for explicit y/n approval
  4. Only THEN calls Claude (claude-haiku-4-5-20251001)
  5. Returns the result for logging

HARD CONSTRAINT: There is no code path that calls Anthropic without
a confirmed approval. The gate is always blocking.
"""
from __future__ import annotations

import sys
import time
import uuid
from dataclasses import dataclass
from threading import Lock

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


@dataclass
class OverrideRequest:
    override_id: str
    query: str
    local_response: str
    confidence_score: float
    confidence_description: str
    created_at: float = 0.0

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()


@dataclass
class OverrideResult:
    override_id: str
    approved: bool
    claude_response: str
    rationale: str
    tokens_used: int
    latency_ms: float


_pending: dict[str, OverrideRequest] = {}
_pending_lock = Lock()


class ClaudeOverride:
    """
    Gate for routing critical-logic escalation to Claude.

    mode = "terminal" → blocks with input() prompt
    mode = "api"      → stores request, resolved via approve_override(id, bool)
    """

    DEFAULT_MODEL = "claude-haiku-4-5-20251001"
    DEFAULT_MAX_TOKENS = 600

    def __init__(self, mode: str = "terminal", model: str = DEFAULT_MODEL, max_tokens: int = DEFAULT_MAX_TOKENS):
        if mode not in ("terminal", "api"):
            raise ValueError("mode must be 'terminal' or 'api'")
        self.mode = mode
        self.model = model
        self.max_tokens = max_tokens
        self._client = None

    @classmethod
    def from_manifest(cls, manifest: dict) -> "ClaudeOverride":
        ov = manifest.get("override", {})
        rt = manifest.get("routing", {})
        return cls(
            mode=rt.get("override_mode", "terminal"),
            model=ov.get("model", cls.DEFAULT_MODEL),
            max_tokens=int(ov.get("max_tokens", cls.DEFAULT_MAX_TOKENS)),
        )

    # ──────────────────────────────────────────────
    # PUBLIC
    # ──────────────────────────────────────────────

    def request_override(self, query: str, local_response: str, confidence_result) -> dict:
        """Stage 1 — present override request to user."""
        override_id = str(uuid.uuid4())[:8]
        req = OverrideRequest(
            override_id=override_id,
            query=query,
            local_response=local_response,
            confidence_score=confidence_result.score,
            confidence_description=confidence_result.description,
        )
        return self._terminal_flow(req) if self.mode == "terminal" else self._api_stage(req)

    def approve_override(self, override_id: str, approved: bool):
        """Stage 2 (api mode) — resolve a pending request."""
        with _pending_lock:
            req = _pending.pop(override_id, None)
        if req is None:
            return None

        if not approved:
            return OverrideResult(
                override_id=override_id, approved=False,
                claude_response="", rationale="User rejected override.",
                tokens_used=0, latency_ms=0.0,
            )
        return self._call_claude(req)

    # ──────────────────────────────────────────────
    # TERMINAL FLOW
    # ──────────────────────────────────────────────

    def _terminal_flow(self, req: OverrideRequest) -> dict:
        self._print_alert(req)
        try:
            choice = input("\n  Approve Claude API override? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "n"

        if choice in ("y", "yes"):
            result = self._call_claude(req)
            print(f"\n  [Override complete — {result.tokens_used} tokens, {result.latency_ms:.0f}ms]\n")
            return {
                "status": "override_complete",
                "override_id": req.override_id,
                "approved": True,
                "claude_response": result.claude_response,
                "tokens_used": result.tokens_used,
                "latency_ms": result.latency_ms,
            }

        print("  Override declined — using local response.\n")
        return {
            "status": "override_declined",
            "override_id": req.override_id,
            "approved": False,
        }

    @staticmethod
    def _print_alert(req: OverrideRequest):
        bar = "=" * 62
        print(f"\n{bar}")
        print("  ⚠  CRITICAL LOGIC OVERRIDE TRIGGERED")
        print(bar)
        print(req.confidence_description)
        print(f"\n  Local model answer:")
        print(f"  {req.local_response[:240]}")
        print(bar)

    # ──────────────────────────────────────────────
    # API FLOW
    # ──────────────────────────────────────────────

    def _api_stage(self, req: OverrideRequest) -> dict:
        with _pending_lock:
            _pending[req.override_id] = req
        return {
            "status": "pending_override",
            "override_id": req.override_id,
            "description": req.confidence_description,
            "local_response": req.local_response,
            "confidence_score": req.confidence_score,
            "query": req.query,
        }

    # ──────────────────────────────────────────────
    # CLAUDE CALL — only after approval
    # ──────────────────────────────────────────────

    def _call_claude(self, req: OverrideRequest) -> OverrideResult:
        client = self._get_client()
        t0 = time.perf_counter()

        system_prompt = (
            "You are a critical logic validator for a local LLM system. "
            "The local model gave a low-confidence response. "
            "Provide a concise, accurate, well-reasoned answer."
        )
        user_prompt = (
            f"Original query:\n{req.query}\n\n"
            f"Local response:\n{req.local_response}\n\n"
            f"Confidence:\n{req.confidence_description}\n\n"
            "Please provide the correct answer."
        )
        response = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        latency_ms = (time.perf_counter() - t0) * 1000

        claude_text = next((b.text for b in response.content if b.type == "text"), "")
        tokens_used = response.usage.input_tokens + response.usage.output_tokens

        return OverrideResult(
            override_id=req.override_id,
            approved=True,
            claude_response=claude_text,
            rationale=f"Override approved at confidence={req.confidence_score:.1%}",
            tokens_used=tokens_used,
            latency_ms=round(latency_ms, 1),
        )

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()
        return self._client


def get_pending_overrides() -> dict:
    with _pending_lock:
        return {
            oid: {
                "query": req.query[:80],
                "confidence_score": req.confidence_score,
                "age_seconds": round(time.time() - req.created_at, 1),
            }
            for oid, req in _pending.items()
        }
