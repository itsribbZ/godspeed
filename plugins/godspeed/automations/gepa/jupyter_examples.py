"""
GEPA + Toke — Jupyter Examples
================================
Copy-paste these cells into Jupyter while training on GEPA.
Each cell is self-contained and demonstrates one integration point.

Prerequisite: pip install gepa (already installed on this machine)
"""

# ═══════════════════════════════════════════════════════════════
# CELL 1: Import the bridge
# ═══════════════════════════════════════════════════════════════
import sys
sys.path.insert(0, "~/Desktop/T1/Toke/automations/gepa")
from gepa_bridge import (
    get_manifest_weights,
    get_skill_catalog,
    get_eval_dataset,
    get_asi_diagnostics,
    make_manifest_evaluator,
    save_proposal,
    print_summary,
)
print_summary()


# ═══════════════════════════════════════════════════════════════
# CELL 2: See what GEPA would optimize (manifest weights)
# ═══════════════════════════════════════════════════════════════
weights = get_manifest_weights()
print("Current manifest weights (seed candidate for GEPA):")
print(weights)


# ═══════════════════════════════════════════════════════════════
# CELL 3: Run the evaluator to see Brain's current accuracy
# ═══════════════════════════════════════════════════════════════
evaluator = make_manifest_evaluator()
score, diagnostics = evaluator("current")
print(f"Brain accuracy on eval prompts: {diagnostics['accuracy']}")
print(f"Misclassified prompts:")
for m in diagnostics["misclassified"][:10]:
    print(f"  [{m['id']}] expected {m['expected']}, got {m['got']} (conf={m['confidence']:.2f})")


# ═══════════════════════════════════════════════════════════════
# CELL 4: See the ASI diagnostics (human override events)
# ═══════════════════════════════════════════════════════════════
asi = get_asi_diagnostics(limit=50)
overrides = [a for a in asi if a["asi"]["event_type"] == "override"]
print(f"Override events (GEPA ASI): {len(overrides)}/{len(asi)}")
for o in overrides[:5]:
    print(f"  {o['asi']['diagnostic'][:100]}")


# ═══════════════════════════════════════════════════════════════
# CELL 5: GEPA optimize_anything — manifest weights
# (requires gepa installed: pip install gepa)
# ═══════════════════════════════════════════════════════════════
"""
import gepa

result = gepa.optimize_anything(
    seed_candidate=get_manifest_weights(),
    evaluator=make_manifest_evaluator(),
    config=gepa.GEPAConfig(
        engine=gepa.EngineConfig(max_evaluations=50),
        reflection=gepa.ReflectionConfig(model="claude-sonnet-4-6"),
    ),
)

# Save the proposal (never writes to live manifest)
path = save_proposal("manifest_weights", result.best_candidate)
print(f"Proposal saved to: {path}")
print(f"Score improvement: {score:.1%} -> {result.best_score:.1%}")
"""


# ═══════════════════════════════════════════════════════════════
# CELL 6: View skill catalog (descriptions GEPA can evolve)
# ═══════════════════════════════════════════════════════════════
catalog = get_skill_catalog()
for s in catalog[:10]:
    pin = f" [{s.model}]" if s.model else ""
    print(f"  {s.name}{pin}: {s.description[:70]}..." if s.description else f"  {s.name}: (no desc)")


# ═══════════════════════════════════════════════════════════════
# CELL 7: Eval prompt breakdown by category
# ═══════════════════════════════════════════════════════════════
from collections import Counter
evals = get_eval_dataset()
cats = Counter(e.category for e in evals)
print(f"Eval prompts: {len(evals)} total")
for cat, n in cats.most_common():
    print(f"  {cat}: {n}")
