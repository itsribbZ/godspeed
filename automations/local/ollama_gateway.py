#!/usr/bin/env python3
"""
Toke Local — Ollama Gateway
============================
HTTP wrapper for the local Ollama server. Exposes typed responses with
per-token logprobs for the confidence monitor.

Ported from Buddy session 5 (2026-04-12). Toke conventions applied:
  - pathlib + tomllib for config
  - UTF-8 stdout reconfigure
  - keep_alive sourced from manifest (0 = unload after each request)
  - logprobs requested by default for confidence scoring
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator

import requests

# Ensure UTF-8 stdout/stderr on Windows
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


# ──────────────────────────────────────────────
# DATA
# ──────────────────────────────────────────────

@dataclass
class TokenLogprob:
    token: str
    logprob: float


@dataclass
class OllamaResponse:
    text: str
    model: str
    tokens_generated: int
    tokens_prompted: int
    logprobs: list[list[TokenLogprob]]
    latency_ms: float
    raw: dict = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.tokens_generated + self.tokens_prompted

    @property
    def has_logprobs(self) -> bool:
        return len(self.logprobs) > 0


# ──────────────────────────────────────────────
# GATEWAY
# ──────────────────────────────────────────────

class OllamaGateway:
    """
    Production wrapper for local Ollama.
    Reads defaults from local_manifest.toml [ollama] section.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5:14b-instruct-q4_K_M",
        temperature: float = 0.8,
        num_ctx: int = 4096,
        num_predict: int = 512,
        timeout: int = 300,
        keep_alive: int = 0,
        request_logprobs: bool = True,
        num_logprobs: int = 5,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.num_predict = num_predict
        self.timeout = timeout
        self.keep_alive = keep_alive
        self.request_logprobs = request_logprobs
        self.num_logprobs = num_logprobs
        self._session = requests.Session()

    # ──────────────────────────────────────────────
    # FACTORY — load from manifest
    # ──────────────────────────────────────────────

    @classmethod
    def from_manifest(cls, manifest: dict) -> "OllamaGateway":
        """Build from local_manifest.toml [ollama] section."""
        ol = manifest.get("ollama", {})
        lp = ol.get("logprobs", {})
        return cls(
            base_url=ol.get("base_url", "http://localhost:11434"),
            model=ol.get("model", "qwen2.5:14b-instruct-q4_K_M"),
            temperature=float(ol.get("temperature", 0.8)),
            num_ctx=int(ol.get("num_ctx", 4096)),
            num_predict=int(ol.get("num_predict", 512)),
            timeout=int(ol.get("timeout", 300)),
            keep_alive=int(ol.get("keep_alive", 0)),
            request_logprobs=bool(lp.get("enabled", True)),
            num_logprobs=int(lp.get("top_k", 5)),
        )

    # ──────────────────────────────────────────────
    # HEALTH
    # ──────────────────────────────────────────────

    def ping(self) -> bool:
        """Check Ollama is reachable and the model is loaded."""
        try:
            r = self._session.get(f"{self.base_url}/api/tags", timeout=5)
            if r.status_code != 200:
                return False
            models = [m.get("name", "") for m in r.json().get("models", [])]
            prefix = self.model.split(":")[0]
            return any(prefix in m for m in models)
        except requests.RequestException:
            return False

    def list_models(self) -> list[str]:
        try:
            r = self._session.get(f"{self.base_url}/api/tags", timeout=5)
            r.raise_for_status()
            return [m.get("name", "") for m in r.json().get("models", [])]
        except requests.RequestException:
            return []

    # ──────────────────────────────────────────────
    # GENERATE
    # ──────────────────────────────────────────────

    def generate(self, prompt: str, max_tokens: int | None = None) -> OllamaResponse:
        """Blocking generation with logprobs."""
        payload = self._build_payload(prompt, max_tokens or self.num_predict, stream=False)
        t0 = time.perf_counter()

        r = self._session.post(
            f"{self.base_url}/api/generate",
            json=payload,
            timeout=self.timeout,
        )
        r.raise_for_status()
        latency_ms = (time.perf_counter() - t0) * 1000
        return self._parse_response(r.json(), latency_ms)

    def generate_stream(self, prompt: str, max_tokens: int | None = None) -> Generator[str, None, None]:
        """SSE streaming. Logprobs not available in stream mode."""
        payload = self._build_payload(prompt, max_tokens or self.num_predict, stream=True)
        with self._session.post(
            f"{self.base_url}/api/generate",
            json=payload,
            stream=True,
            timeout=self.timeout,
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if chunk.get("done"):
                    break
                text = chunk.get("response", "")
                if text:
                    yield text

    def chat(self, messages: list[dict], max_tokens: int | None = None) -> OllamaResponse:
        """Chat-format generation via /api/chat (auto-applies model template)."""
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
                "num_predict": max_tokens or self.num_predict,
            },
        }
        t0 = time.perf_counter()
        r = self._session.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
        r.raise_for_status()
        latency_ms = (time.perf_counter() - t0) * 1000

        raw = r.json()
        text = raw.get("message", {}).get("content", "")
        return OllamaResponse(
            text=text,
            model=self.model,
            tokens_generated=raw.get("eval_count", 0),
            tokens_prompted=raw.get("prompt_eval_count", 0),
            logprobs=[],
            latency_ms=round(latency_ms, 1),
            raw=raw,
        )

    # ──────────────────────────────────────────────
    # INTERNALS
    # ──────────────────────────────────────────────

    def _build_payload(self, prompt: str, max_tokens: int, stream: bool) -> dict:
        options: dict = {
            "temperature": self.temperature,
            "num_ctx": self.num_ctx,
            "num_predict": max_tokens,
        }
        if self.request_logprobs:
            options["logprobs"] = True
            options["top_k"] = self.num_logprobs

        return {
            "model": self.model,
            "prompt": prompt,
            "stream": stream,
            "keep_alive": self.keep_alive,
            "options": options,
        }

    def _parse_response(self, raw: dict, latency_ms: float) -> OllamaResponse:
        text = raw.get("response", "")
        tokens_generated = raw.get("eval_count", 0)
        tokens_prompted = raw.get("prompt_eval_count", 0)

        logprobs: list[list[TokenLogprob]] = []
        raw_lp = raw.get("logprobs") or {}
        for entry in raw_lp.get("content", []):
            top = [
                TokenLogprob(token=tl.get("token", ""), logprob=float(tl.get("logprob", 0.0)))
                for tl in entry.get("top_logprobs", [])
            ]
            if top:
                logprobs.append(top)

        return OllamaResponse(
            text=text,
            model=self.model,
            tokens_generated=tokens_generated,
            tokens_prompted=tokens_prompted,
            logprobs=logprobs,
            latency_ms=round(latency_ms, 1),
            raw=raw,
        )
