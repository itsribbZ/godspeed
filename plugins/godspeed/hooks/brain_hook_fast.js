#!/usr/bin/env node
// ============================================================================
// Toke Brain — Node.js fast-path hook
// ============================================================================
// Replaces Python subprocess calls in brain_advisor.sh / brain_tools_hook.sh.
// Node cold start ~65ms vs Python ~330ms. Same classification logic.
//
// Usage:
//   echo '{"prompt":"..."}' | node brain_hook_fast.js hook
//   echo '{"tool_name":"..."}' | node brain_hook_fast.js telemetry
//
// Reads routing_manifest.json (generated from TOML via manifest_to_json.py).
// If JSON is missing or stale, exits 2 so shell wrapper can fall back to Python.
// ============================================================================

'use strict';

const fs = require('fs');
const path = require('path');
const os = require('os');

const HOME = os.homedir();
// Portability: prefer CLAUDE_PLUGIN_ROOT (bundled engine), then TOKE_ROOT env
// override (install.sh users), finally $HOME/.toke fallback.
const TOKE_ROOT = process.env.TOKE_ROOT || process.env.CLAUDE_PLUGIN_ROOT || path.join(HOME, '.toke');
const BRAIN_DIR = path.join(TOKE_ROOT, 'automations', 'brain');
const TELEMETRY_DIR = path.join(HOME, '.claude', 'telemetry', 'brain');
const MANIFEST_JSON = path.join(BRAIN_DIR, 'routing_manifest.json');
const MANIFEST_TOML = path.join(BRAIN_DIR, 'routing_manifest.toml');
const DECISIONS_FILE = path.join(TELEMETRY_DIR, 'decisions.jsonl');
const TOOLS_FILE = path.join(TELEMETRY_DIR, 'tools.jsonl');
const HOOK_DEBUG_LOG = path.join(TELEMETRY_DIR, 'hook_debug.log');

// ---------------------------------------------------------------------------
// Manifest loading (with staleness check)
// ---------------------------------------------------------------------------

function loadManifest() {
  if (!fs.existsSync(MANIFEST_JSON)) return null;
  // Staleness: if TOML is newer than JSON, signal rebuild needed
  try {
    const jsonStat = fs.statSync(MANIFEST_JSON);
    const tomlStat = fs.statSync(MANIFEST_TOML);
    if (tomlStat.mtimeMs > jsonStat.mtimeMs) return null; // stale
  } catch { /* if TOML missing, JSON is fine */ }
  return JSON.parse(fs.readFileSync(MANIFEST_JSON, 'utf8'));
}

// ---------------------------------------------------------------------------
// Signal extractors
// ---------------------------------------------------------------------------

const CODE_BLOCK_RE = /```[^\n]*\n[\s\S]*?```/g;
const FILE_REF_RE = new RegExp(
  '(?:[A-Za-z]:[/\\\\][^\\s`\'"]+' +
  '|(?:\\./|~/|/)[^\\s`\'"]+' +
  '|@[A-Za-z0-9_./-]+' +
  '|\\b[\\w-]+\\.(?:py|ts|tsx|js|jsx|cpp|c|h|hpp|cs|go|rs|rb|java|md|json|jsonl|toml|yaml|yml|sh|bash|sql|html|css)\\b)',
  'g'
);

function estimateTokens(text) {
  return Math.max(Math.floor(text.length / 4), 0);
}

function countCodeBlocks(text) {
  return (text.match(CODE_BLOCK_RE) || []).length;
}

function countFileRefs(text) {
  return (text.match(FILE_REF_RE) || []).length;
}

function countKeywords(text, keywords) {
  if (!keywords || !keywords.length || !text) return 0;
  const lower = text.toLowerCase();
  let count = 0;
  for (const kw of keywords) {
    const kwLower = kw.toLowerCase().trim();
    if (!kwLower) continue;
    if (kwLower.includes(' ') || kwLower.includes('-') || kwLower.includes('.')) {
      // Multi-word/special: substring match
      let idx = -1;
      while ((idx = lower.indexOf(kwLower, idx + 1)) !== -1) count++;
    } else {
      // Single word: word boundary match
      const re = new RegExp('\\b' + kwLower.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\b', 'gi');
      count += (text.match(re) || []).length;
    }
  }
  return count;
}

// ---------------------------------------------------------------------------
// Signal computation
// ---------------------------------------------------------------------------

function computeSignals(prompt, contextTokens, manifest) {
  const tokens = estimateTokens(prompt);
  const kw = manifest.keywords || {};
  const norms = manifest.normalization || {};

  function norm(value, capKey, def) {
    const cap = parseFloat(norms[capKey] || def);
    return cap > 0 ? Math.min(value / cap, 1.0) : 0.0;
  }

  return {
    prompt_length: norm(tokens, 'prompt_length_cap', 500),
    code_blocks: norm(countCodeBlocks(prompt), 'code_blocks_cap', 3),
    file_refs: norm(countFileRefs(prompt), 'file_refs_cap', 5),
    reasoning: norm(countKeywords(prompt, kw.reasoning || []), 'reasoning_cap', 5),
    multi_step: norm(countKeywords(prompt, kw.multi_step || []), 'multi_step_cap', 3),
    ambiguity: norm(countKeywords(prompt, kw.ambiguity || []), 'ambiguity_cap', 3),
    tool_calls: norm(countKeywords(prompt, kw.tool_calls || []), 'tool_calls_cap', 4),
    context_size: norm(contextTokens, 'context_tokens_cap', 150000),
    code_action: norm(countKeywords(prompt, kw.code_action || []), 'code_action_cap', 2),
    system_scope: norm(countKeywords(prompt, kw.system_scope || []), 'system_scope_cap', 2),
  };
}

// ---------------------------------------------------------------------------
// Guardrail evaluation
// ---------------------------------------------------------------------------

// Domain detection from CWD path (mirrors Python _detect_project_domain)
function detectProjectDomain(cwd) {
  if (!cwd) return null;
  const lower = cwd.toLowerCase().replace(/\\/g, '/');
  if (lower.includes('.uproject')) return 'ue5';
  return null;
}

function guardrailFires(prompt, contextTokens, gDef, cwdDomain) {
  // v2.4: domain-scoped guardrails — suppress when CWD doesn't match
  const domainTags = gDef.domain_tags;
  if (domainTags && cwdDomain && !domainTags.includes(cwdDomain)) return false;

  const checks = [];

  if (gDef.keywords) {
    checks.push(countKeywords(prompt, gDef.keywords) > 0);
  }
  if (gDef.min_file_refs != null) {
    checks.push(countFileRefs(prompt) >= parseInt(gDef.min_file_refs));
  }
  if (gDef.min_context_tokens != null) {
    checks.push(contextTokens >= parseInt(gDef.min_context_tokens));
  }
  if (gDef.regex) {
    checks.push(gDef.regex.some(p => {
      // Convert Python-style inline flags (?i) to JS flags
      let pattern = p.replace(/\(\?[imsx]+\)/g, '');
      try { return new RegExp(pattern, 'is').test(prompt); }
      catch { return false; }
    }));
  }

  if (!checks.length) return false;
  return gDef.require_all ? checks.every(Boolean) : checks.some(Boolean);
}

// ---------------------------------------------------------------------------
// Tier mapping + confidence
// ---------------------------------------------------------------------------

function scoreToTier(score, thresholds) {
  if (score < (thresholds.s0_max || 0.08)) return 'S0';
  if (score < (thresholds.s1_max || 0.18)) return 'S1';
  if (score < (thresholds.s2_max || 0.35)) return 'S2';
  if (score < (thresholds.s3_max || 0.55)) return 'S3';
  if (score < (thresholds.s4_max || 0.80)) return 'S4';
  return 'S5';
}

function computeConfidence(score, thresholds) {
  const boundaries = [
    thresholds.s0_max || 0.08,
    thresholds.s1_max || 0.18,
    thresholds.s2_max || 0.35,
    thresholds.s3_max || 0.55,
    thresholds.s4_max || 0.80,
  ];
  const minDist = Math.min(...boundaries.map(b => Math.abs(score - b)));
  return Math.round(Math.min(minDist / 0.10, 1.0) * 1000) / 1000;
}

const TIER_ORDER = ['S0', 'S1', 'S2', 'S3', 'S4', 'S5'];

function bumpTier(tier) {
  const idx = TIER_ORDER.indexOf(tier);
  if (idx < 0) return 'S4';
  return TIER_ORDER[Math.min(idx + 1, 5)];
}

// ---------------------------------------------------------------------------
// Session context (reads decisions.jsonl)
// ---------------------------------------------------------------------------

function getSessionContext(sessionId) {
  if (!sessionId || !fs.existsSync(DECISIONS_FILE)) return { history: [], maxTier: null };
  const lines = fs.readFileSync(DECISIONS_FILE, 'utf8').split('\n').filter(Boolean);
  const sessionDecisions = [];
  for (const line of lines) {
    try {
      const d = JSON.parse(line);
      if (d.session_id === sessionId) sessionDecisions.push(d);
    } catch { /* skip */ }
  }
  const history = sessionDecisions.slice(-3);
  let maxIdx = -1;
  for (const d of sessionDecisions) {
    const t = (d.result || {}).tier || 'S0';
    const idx = TIER_ORDER.indexOf(t);
    if (idx > maxIdx) maxIdx = idx;
  }
  return { history, maxTier: maxIdx >= 0 ? TIER_ORDER[maxIdx] : null };
}

// ---------------------------------------------------------------------------
// Main classifier
// ---------------------------------------------------------------------------

function classify(promptText, contextTokens, manifest, sessionId, cwd) {
  const signals = computeSignals(promptText, contextTokens, manifest);
  const weights = manifest.weights || {};

  let baseScore = 0;
  for (const k of Object.keys(signals)) {
    baseScore += signals[k] * parseFloat(weights[k] || 0);
  }

  // v2.4: detect CWD domain for guardrail scoping
  const cwdDomain = detectProjectDomain(cwd);

  const guardrailsFired = [];
  const guardrails = manifest.guardrails || {};
  for (const [gName, gDef] of Object.entries(guardrails)) {
    if (guardrailFires(promptText, contextTokens, gDef, cwdDomain)) {
      guardrailsFired.push(gName);
      baseScore = Math.max(baseScore, parseFloat(gDef.min_score || 0));
    }
  }

  // v2.6: ceiling guardrails — cap score when specificity patterns detected
  const ceilingGuardrails = manifest.ceiling_guardrails || {};
  for (const [gName, gDef] of Object.entries(ceilingGuardrails)) {
    if (guardrailFires(promptText, contextTokens, gDef, cwdDomain)) {
      guardrailsFired.push(gName);
      baseScore = Math.min(baseScore, parseFloat(gDef.max_score || 1));
    }
  }

  let finalScore = Math.min(Math.max(baseScore, 0), 1);
  const thresholds = manifest.thresholds || {};

  // Skill override
  const skillMap = manifest.skills || {};
  let skillOverride = null;
  let tier = scoreToTier(finalScore, thresholds);

  // (skill_name not available in hook stdin — skip skill override for hook path)

  const confidence = computeConfidence(finalScore, thresholds);
  let uncertaintyEscalated = false;

  // Uncertainty escalation
  const uncertaintyCfg = manifest.uncertainty || {};
  const learningCfg = manifest.learning || {};
  const escalateOnUncertain = uncertaintyCfg.escalate_on_low_confidence || false;
  const lowConfThreshold = parseFloat(learningCfg.confidence_low_threshold || 0.30);

  if (escalateOnUncertain && confidence < lowConfThreshold && !skillOverride &&
      !guardrailsFired.length && !['S4', 'S5'].includes(tier)) {
    tier = bumpTier(tier);
    uncertaintyEscalated = true;
  }

  // Correction detection
  const correctionKeywords = ((manifest.keywords || {}).correction) || [];
  const correctionDetected = correctionKeywords.some(kw =>
    promptText.toLowerCase().includes(kw.toLowerCase())
  );

  // Multi-turn context
  let contextTurnsSeen = 0;
  const { history, maxTier } = getSessionContext(sessionId);
  if (history.length) {
    contextTurnsSeen = history.length;
    const last = history[history.length - 1];
    if (last) {
      const lastResult = last.result || {};
      const lastOverridden = last.current_model &&
        !(last.current_model || '').toLowerCase().includes((lastResult.model || '').toLowerCase());
      const lastCorrection = lastResult.correction_detected_in_prompt || false;
      if ((lastOverridden || lastCorrection || correctionDetected) && !['S4', 'S5'].includes(tier)) {
        tier = bumpTier(tier);
        uncertaintyEscalated = true;
      }
    }
  }

  // v2.5: session_turn_depth weighting — deep sessions rarely stay at S0
  if (contextTurnsSeen >= 15 && ['S0', 'S1'].includes(tier) && !skillOverride) {
    tier = 'S2';
    uncertaintyEscalated = true;
  } else if (contextTurnsSeen >= 8 && tier === 'S0' && !skillOverride) {
    tier = bumpTier(tier); // S0 → S1
    uncertaintyEscalated = true;
  }

  // Session high-water mark for continuation prompts
  // v2.4: widened from 60 to 120 chars — catches more continuation prompts
  if (maxTier && promptText.length <= 120) {
    const maxIdx = TIER_ORDER.indexOf(maxTier);
    const curIdx = TIER_ORDER.indexOf(tier);
    const floorIdx = Math.max(maxIdx - 1, 0);
    if (maxIdx >= 3 && curIdx < floorIdx) {
      tier = TIER_ORDER[floorIdx];
      uncertaintyEscalated = true;
    }
  }

  const tierMap = manifest.tier_map || {};
  const tierCfg = tierMap[tier] || {};
  const model = tierCfg.model || 'sonnet';
  const effort = tierCfg.effort || 'high';
  const extendedThinkingBudget = parseInt(tierCfg.extended_thinking_budget || 0);

  // Build reasoning string
  const parts = [];
  parts.push(`score=${finalScore.toFixed(3)}->${tier}`);
  const topSignals = Object.entries(signals)
    .sort((a, b) => b[1] - a[1])
    .filter(([, v]) => v > 0.01)
    .slice(0, 3)
    .map(([k, v]) => `${k}=${v.toFixed(2)}`);
  if (topSignals.length) parts.push('top:' + topSignals.join(','));
  if (guardrailsFired.length) parts.push('guards:' + guardrailsFired.join('+'));
  if (uncertaintyEscalated) parts.push(`escalated:conf=${confidence.toFixed(2)}`);
  if (correctionDetected) parts.push('correction_follow');
  if (extendedThinkingBudget > 0) parts.push(`thinking:${extendedThinkingBudget}`);
  if (contextTurnsSeen > 0) parts.push(`ctx:${contextTurnsSeen}turns`);

  return {
    tier, model, effort, score: Math.round(finalScore * 1000) / 1000,
    signals, guardrails_fired: guardrailsFired, skill_override: skillOverride,
    reasoning: parts.join(' | '), confidence,
    extended_thinking_budget: extendedThinkingBudget,
    uncertainty_escalated: uncertaintyEscalated,
    context_turns_seen: contextTurnsSeen,
    correction_detected_in_prompt: correctionDetected,
  };
}

// ---------------------------------------------------------------------------
// Hook command: UserPromptSubmit
// ---------------------------------------------------------------------------

function cmdHook(payload) {
  const manifest = loadManifest();
  if (!manifest) process.exit(2); // signal fallback to Python

  const promptText = payload.prompt || payload.prompt_text || '';
  const sessionId = payload.session_id || '';
  const hookCwd = payload.cwd || '';  // v2.4: CWD for domain-scoped guardrails
  let contextTokens = 0;
  try { contextTokens = parseInt(payload.context_tokens || 0); } catch { /* 0 */ }

  const result = classify(promptText, contextTokens, manifest, sessionId, hookCwd);

  // Log decision
  fs.mkdirSync(TELEMETRY_DIR, { recursive: true });
  const ts = new Date().toISOString();
  const entry = {
    ts, session_id: sessionId, hook: 'UserPromptSubmit',
    prompt_text: promptText.substring(0, 500),  // v2.5: capture for golden_set mining
    result, current_model: payload.model || '',
  };
  fs.appendFileSync(DECISIONS_FILE, JSON.stringify(entry) + '\n');

  // Emit advisory banner to stderr
  const { tier, model } = result;
  if (tier === 'S0' || tier === 'S1') {
    process.stderr.write(`[BRAIN ${tier}/${model}] this prompt does not need Opus. /effort low saves tokens.\n`);
  } else if (tier === 'S5') {
    process.stderr.write(`[BRAIN ${tier}/${model}] full complexity -- /effort max correct.\n`);
  } else {
    process.stderr.write(`[BRAIN ${tier}/${model}] moderate -- /effort medium is sufficient.\n`);
  }
}

// ---------------------------------------------------------------------------
// Telemetry command: PostToolUse
// ---------------------------------------------------------------------------

function cmdTelemetry(payload) {
  fs.mkdirSync(TELEMETRY_DIR, { recursive: true });

  const ts = new Date().toISOString();
  const toolName = payload.tool_name || 'unknown';
  const model = payload.model || '';
  const inputSize = JSON.stringify(payload.tool_input || {}).length;
  const outputSize = JSON.stringify(payload.tool_response || '').length;
  const sessionId = payload.session_id || '';

  const entry = {
    ts, hook: 'PostToolUse', session_id: sessionId,
    tool_name: toolName, model, input_size: inputSize, output_size: outputSize,
  };
  fs.appendFileSync(TOOLS_FILE, JSON.stringify(entry) + '\n');

  // Debug capture (Sacred Rule #5: diagnostics are features)
  const debugLine = `[${ts}] CALLED | stdin_len=${JSON.stringify(payload).length}\n`;
  fs.appendFileSync(HOOK_DEBUG_LOG, debugLine);
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

const cmd = process.argv[2] || 'hook';
let raw = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => { raw += chunk; });
process.stdin.on('end', () => {
  let payload = {};
  try { payload = JSON.parse(raw); } catch { /* empty */ }

  try {
    if (cmd === 'hook') cmdHook(payload);
    else if (cmd === 'telemetry') cmdTelemetry(payload);
    else { process.stderr.write(`Unknown command: ${cmd}\n`); process.exit(1); }
  } catch (e) {
    // Log error for diagnostics, then fail silent — hooks must never block
    try {
      const errLog = path.join(TELEMETRY_DIR, 'hook_errors.log');
      fs.appendFileSync(errLog, `[${new Date().toISOString()}] ${cmd}: ${e.message}\n${e.stack}\n`);
    } catch { /* truly silent */ }
    process.exit(0);
  }
});
