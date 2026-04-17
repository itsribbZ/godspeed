# Agent B — Anthropic Pricing & Cache Economics

Research burst for Toke Brain — 2026-04-10. Source: `platform.claude.com/docs/en/docs/about-claude/pricing` + prompt caching docs. All prices USD per million tokens (MTok).

---

## 1. Master Pricing Table

| Model | Input | Output | Cache Write (5m) | Cache Write (1h) | Cache Read |
|---|---|---|---|---|---|
| **Opus 4.6** | $5.00 | $25.00 | $6.25 | $10.00 | $0.50 |
| **Sonnet 4.6** | $3.00 | $15.00 | $3.75 | $6.00 | $0.30 |
| **Haiku 4.5** | $1.00 | $5.00 | $1.25 | $2.00 | $0.10 |
| **Opus 4.6 Fast Mode** | $30.00 | $150.00 | $37.50 | $60.00 | $3.00 |

**Uniform multipliers across all models:**
- Cache write 5m = 1.25× base input
- Cache write 1h = 2.0× base input
- Cache read = **0.1× base input (90% discount, exact)**

---

## 2. Cost Ratio Table (Opus 4.6 = 1.0× baseline)

| Metric | Opus 4.6 | Sonnet 4.6 | Haiku 4.5 |
|---|---|---|---|
| Input | 1.0× | **0.60×** | **0.20×** |
| Output | 1.0× | **0.60×** | **0.20×** |
| Cache write 5m | 1.0× | **0.60×** | **0.20×** |
| Cache read | 1.0× | **0.60×** | **0.20×** |

**Sonnet = exactly 60% of Opus. Haiku = exactly 20% of Opus.** Uniform across every category. Pricing was designed this way.

---

## 3. 1M Context Pricing Premium

**Zero premium.** Docs: "Opus 4.6 and Sonnet 4.6 include the full 1M token context window at standard pricing."

No tiered cost for long context — same $5/MTok input regardless of context length. Break-even = $0.

Only exception: data residency (US-only inference) = 1.1× multiplier. Opt-in, not context-length cost.

---

## 4. Fast Mode Pricing — THE 6× TRAP

`/fast` in Claude Code invokes Opus 4.6 with `speed: "fast"`. **Same model, 6× price.**

- Input: $5 → $30/MTok (6×)
- Output: $25 → $150/MTok (6×)
- Cache read: $0.50 → $3.00/MTok (6×)
- **Cache discipline breaks**: switching fast/standard invalidates cache. Requests at different speeds don't share cached prefixes.
- Currently beta/waitlist only

**Never use fast mode when cache is warm on standard Opus. Cache miss + 6× premium = double hit.**

---

## 5. the user's 30-Day Actual Cost Breakdown

Source stats: `~/.claude/stats-cache.json` as of 2026-04-09.

### Opus 4.6
- Direct input: 4.55M × $5.00 = **$22.75**
- Output: 30.72M × $25.00 = **$768.00**
- Cache write: 379M × $6.25 = **$2,368.75**
- Cache read: 13,740M × $0.50 = **$6,870.00**
- **Opus subtotal: ~$10,029.50**

### Sonnet 4.6
- Direct input: 0.435M × $3.00 = **$1.31**
- Output: 1.31M × $15.00 = **$19.65**
- Cache write: 23.9M × $3.75 = **$89.63**
- Cache read: 228M × $0.30 = **$68.40**
- **Sonnet subtotal: ~$178.99**

### Haiku 4.5
- Direct input: 0.253M × $1.00 = **$0.25**
- Output: 0.702M × $5.00 = **$3.51**
- Cache write: 19.1M × $1.25 = **$23.88**
- Cache read: 144M × $0.10 = **$14.40**
- **Haiku subtotal: ~$42.04**

### Grand total (30 days): **~$10,250.53**

**Opus = 97.8% of total spend. Cache reads on Opus alone = $6,870 (67% of the bill).**

---

## 6. Routing Savings Projections

### Hypothetical: 50% of Opus → Sonnet
- Output savings: 15.36M × ($25 − $15) = $153.60
- Cache read savings: 6,870M × ($0.50 − $0.30) = $1,374.00
- Input savings: 2.275M × ($5 − $3) = $4.55
- Cache write savings: 189.5M × ($6.25 − $3.75) = $473.75
- **Total: ~$2,005.90/mo savings (19.6% reduction)**

### Hypothetical: 20% of Opus → Haiku
- Output: 6.144M × ($25 − $5) = $122.88
- Cache read: 2,748M × ($0.50 − $0.10) = $1,099.20
- Input: 0.91M × ($5 − $1) = $3.64
- Cache write: 75.8M × ($6.25 − $1.25) = $379.00
- **Total: ~$1,604.72/mo savings (15.7% reduction)**

### Combined (50% → Sonnet + 20% → Haiku of remaining Opus)
- **Estimated savings: ~$3,200-3,600/mo (~30-35% reduction)**

Target: retain near-Opus quality on the remaining 30-40% of Opus workload that stays on Opus.

---

## 7. Cache Discipline Impact — With vs Without

"No cache" scenario: all 13,740M Opus cache reads as fresh input at $5/MTok.

| | With Cache | Without Cache | Savings |
|---|---|---|---|
| Opus cache read | $6,870 | $68,700 | **$61,830** |
| Opus cache write cost | $2,369 | $0 | −$2,369 (overhead) |
| **Net Opus cache savings** | | | **$59,461** |
| Sonnet cache savings | | | $615.60 |
| Haiku cache savings | | | $129.60 |
| **Total** | | | **~$60,206/mo** |

**Cache discipline is saving ~$60,200/mo. Without it, the bill would be ~$70,000/mo instead of ~$10,250.**

**85% cost reduction from caching alone.** The 3,000:1 cache-read-to-input ratio is the core economic engine.

**Every dollar on cache writes ($2,369/mo) returns ~$25 in cache read savings.**

---

## 8. Brain Router Decision Thresholds

### When Sonnet beats Opus:
- Any task where Sonnet quality is acceptable → 40% immediate savings on every token
- Output-heavy tasks compound savings (30.72M/mo on Opus output)
- Cache reads at $0.30 vs $0.50 — Sonnet saves 40% on reads too

### When Haiku beats Sonnet:
- Simple classification, formatting, routing, summarization of pre-processed content
- Haiku = 20% Opus = 33% Sonnet
- Cache read at $0.10 vs $0.30 — Haiku is 3× cheaper on reads

### Fast mode — never unless:
- Latency is primary constraint AND cache is cold
- 6× price + cache invalidation makes it almost always wrong

### Context window consideration:
- No pricing premium for 1M context on Opus/Sonnet
- Routing to Haiku (200K context) on long tasks forces compression/chunking
- **Brain MUST check estimated token count before routing to Haiku** — if context > 150K, Haiku not viable regardless of task complexity

---

## Sources
- https://platform.claude.com/docs/en/docs/about-claude/pricing
- https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- https://platform.claude.com/docs/en/build-with-claude/fast-mode
