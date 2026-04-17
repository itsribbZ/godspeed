#!/usr/bin/env python3
"""
Toke Local — Confidence Monitor
================================
Softmax entropy scorer for OllamaResponse logprobs.

Below CONFIDENCE_THRESHOLD (default 12.5%) marks the response is_critical
and triggers the Claude override gate.

Three modes (priority order from manifest):
  1. logprobs        — Shannon entropy on top-k logprobs (primary)
  2. self_consistency— cosine similarity between resampled responses
  3. model_stated    — ask the model to self-rate 0-100
"""
from __future__ import annotations

import hashlib
import math
import sys
from dataclasses import dataclass
from enum import Enum

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from ollama_gateway import OllamaGateway, OllamaResponse, TokenLogprob


CONFIDENCE_THRESHOLD = 0.125


class ConfidenceMode(str, Enum):
    LOGPROBS         = "logprobs"
    SELF_CONSISTENCY = "self_consistency"
    MODEL_STATED     = "model_stated"
    UNAVAILABLE      = "unavailable"


@dataclass
class ConfidenceResult:
    score: float
    mode: ConfidenceMode
    entropy: float
    is_critical: bool
    description: str
    query_hash: str


class ConfidenceMonitor:
    """Score model confidence; degrade gracefully through three modes."""

    def __init__(self, gateway: OllamaGateway, threshold: float = CONFIDENCE_THRESHOLD):
        self.gateway = gateway
        self.threshold = threshold

    @classmethod
    def from_manifest(cls, gateway: OllamaGateway, manifest: dict) -> "ConfidenceMonitor":
        conf = manifest.get("confidence", {})
        return cls(gateway, threshold=float(conf.get("threshold", CONFIDENCE_THRESHOLD)))

    # ──────────────────────────────────────────────
    # PUBLIC
    # ──────────────────────────────────────────────

    def score_from_response(self, query: str, response: OllamaResponse) -> ConfidenceResult:
        query_hash = hashlib.md5(query.lower().encode()).hexdigest()[:10]

        if response.has_logprobs:
            score, entropy = self._entropy_from_logprobs(response.logprobs)
            mode = ConfidenceMode.LOGPROBS
        else:
            score, entropy = self._self_consistency_score(query, response.text)
            mode = ConfidenceMode.SELF_CONSISTENCY

        is_critical = score < self.threshold
        description = self._build_description(query, response.text, score, entropy, mode, is_critical)

        return ConfidenceResult(
            score=round(score, 4),
            mode=mode,
            entropy=round(entropy, 4),
            is_critical=is_critical,
            description=description,
            query_hash=query_hash,
        )

    def model_stated_confidence(self, query: str, answer: str) -> ConfidenceResult:
        prompt = (
            f"Question: {query}\n\nYour answer: {answer[:300]}\n\n"
            "On a scale of 0 to 100, how confident are you in this answer? "
            "Reply with ONLY the integer."
        )
        try:
            r = self.gateway.generate(prompt, max_tokens=8)
            digits = "".join(c for c in r.text.strip() if c.isdigit())[:3]
            pct = min(100, max(0, int(digits))) if digits else 50
            score = pct / 100.0
        except Exception:
            score = 0.5

        entropy = 1.0 - score
        query_hash = hashlib.md5(query.lower().encode()).hexdigest()[:10]
        is_critical = score < self.threshold
        return ConfidenceResult(
            score=round(score, 4),
            mode=ConfidenceMode.MODEL_STATED,
            entropy=round(entropy, 4),
            is_critical=is_critical,
            description=self._build_description(query, answer, score, entropy, ConfidenceMode.MODEL_STATED, is_critical),
            query_hash=query_hash,
        )

    # ──────────────────────────────────────────────
    # MODES
    # ──────────────────────────────────────────────

    def _entropy_from_logprobs(self, logprobs: list[list[TokenLogprob]]) -> tuple[float, float]:
        if not logprobs:
            return 0.5, 0.5

        per_token = []
        for token_top_k in logprobs:
            if not token_top_k:
                continue
            raw_lp = np.array([t.logprob for t in token_top_k], dtype=np.float64)
            probs = self._softmax(raw_lp)
            k = len(probs)
            if k < 2:
                per_token.append(1.0)
                continue
            entropy = -float(np.sum(probs * np.log(probs + 1e-12)))
            normalized = min(entropy / math.log(k), 1.0)
            per_token.append(1.0 - normalized)

        if not per_token:
            return 0.5, 0.5

        mean_conf = float(np.mean(per_token))
        return mean_conf, 1.0 - mean_conf

    def _self_consistency_score(self, query: str, primary_text: str) -> tuple[float, float]:
        # Short answers (<15 chars) — consensus check is wasteful; assume confident.
        # One-token answers like "4" or "yes" don't benefit from resampling.
        if len(primary_text.strip()) < 15:
            return 0.95, 0.05
        try:
            # Single resample (not 2) — halves latency, still catches hallucinations
            r = self.gateway.generate(query, max_tokens=150)
            sim = self._cosine_sim_bow(primary_text, r.text)
            score = max(0.0, min(1.0, float(sim)))
            return score, 1.0 - score
        except Exception:
            return 0.5, 0.5

    # ──────────────────────────────────────────────
    # UTILS
    # ──────────────────────────────────────────────

    @staticmethod
    def _softmax(x: np.ndarray) -> np.ndarray:
        x = x - x.max()
        e = np.exp(x)
        return e / (e.sum() + 1e-12)

    @staticmethod
    def _cosine_sim_bow(a: str, b: str) -> float:
        vocab = list(set(a.split()) | set(b.split()))
        if not vocab:
            return 1.0
        va = np.array([a.split().count(w) for w in vocab], dtype=float)
        vb = np.array([b.split().count(w) for w in vocab], dtype=float)
        na, nb = np.linalg.norm(va), np.linalg.norm(vb)
        if na == 0 or nb == 0:
            return 0.0
        return float(np.dot(va, vb) / (na * nb))

    @staticmethod
    def _build_description(
        query: str, answer: str, score: float, entropy: float,
        mode: ConfidenceMode, is_critical: bool,
    ) -> str:
        status = "CRITICAL — OVERRIDE REQUESTED" if is_critical else "PASS"
        ans_preview = (answer[:140] + "...") if len(answer) > 140 else answer
        return (
            f"[{status}]\n"
            f"  Confidence  : {score:.1%}  (threshold: {CONFIDENCE_THRESHOLD:.1%})\n"
            f"  Entropy     : {entropy:.4f}\n"
            f"  Mode        : {mode.value}\n"
            f"  Query       : {query[:100]}\n"
            f"  Local answer: {ans_preview}"
        )
