# Clause audit model evals

The golden-set harness (`uv run pytest -m llm_eval`, needs the dev corpus
and `ANTHROPIC_API_KEY`) is the gate for every model change:

```bash
CLAUSE_AUDIT_MODEL=<candidate> uv run pytest -m llm_eval -v -s
```

Thresholds: per-rule precision >= 0.9, recall >= 0.8 (yellow on a red case
counts as a recall miss). 54 cases across the three families plus two PDF
smokes.

## Results

| Date | Model | Prohibited (8 rules) | Mandatory (6 rules) | Fields | PDF smokes | Wall clock | Verdict |
|---|---|---|---|---|---|---|---|
| 2026-07-28 | claude-opus-4-8 (baseline) | all P=1.00 R=1.00 (2 yellow abstentions on green cross-cases) | all P=1.00 R=1.00 | pass | pass | ~7-9 min | baseline |
| 2026-07-29 | claude-sonnet-5 | all P=1.00 R=1.00 (same 2 yellow abstentions) | all P=1.00 R=1.00 | pass | pass | 5:48 | **switched: default model since engine 1.2.0** |

Pricing at the switch: Opus 4.8 $5/$25 per MTok vs Sonnet 5 $3/$15
($2/$10 introductory through 2026-08-31) — roughly half the per-audit
cost at equal measured quality on this suite, and faster.

## Hardened-client regression (2026-08-12/13, engine 1.6.0)

The provider-failover milestone rewrote the Anthropic path to
messages.create + own validation (all-required strict schemas). Any
request-shape change is eval-gated, so the full suite re-ran on
claude-sonnet-5. Gate: pass — prohibited NSW+VIC pooled, standard-form
NSW/F1/F2, fields, both PDF smokes all green under the final
configuration (~80 min full run).

What the regression surfaced, all diagnosed per-case before fixing:

- Four golden-data defects the judge flagged correctly: NSW t3 and t19
  paraphrases narrower than their prescribed sub-clauses (19.5's
  service-disconnection limb; 3.2's lost rent-card reimbursement), F2
  t15's percentage/dollar slots falling through to the name filler, and
  t15 rendering all four rent-adjustment alternatives where a completed
  lease keeps one. A sweep probe of all 11 paraphrases confirmed no
  further incomplete-coverage defects.
- One real client-side effect: the all-required schema inflates per-item
  output and adaptive thinking spends from the same budget, so 8-term
  batches truncated at max_tokens=8000 (probe-verified) or silently
  dropped items (scored yellow via the did-not-report path). Owner
  decision 2026-08-12: max_tokens 16000, matching the OpenAI adapter's
  reasoning-headroom rationale.
- Gate amendment (owner, 2026-08-13): standard-form families now gate on
  per-term recall (n=6 unchanged) plus family-pooled precision >= 0.9 —
  per-term precision denominators (~7-12 greens) carry zero noise budget
  and produced single-FP failures hopping between terms across runs,
  the same churn shape that moved the prohibited families to pooled
  gates. Thresholds themselves unchanged.
- The NSW standard-form debt (gates unmeasured under the shipped 13ebabb
  prompt) is now measured and green.

## Backup provider decision (2026-08-13)

Failover backup: **openai:gpt-5.6-terra** ($2/$12 per MTok), eval-gated on
the identical suite, goldens, and gates as the primary. Candidate sweep,
owner-directed at each step:

| Model | Result | Failure shape |
|---|---|---|
| gpt-5-mini ($0.25/$2) | FAIL | all 3 standard-form families on recall; probes showed document-wide substance crediting (a deleted term judged covered because other clauses carry its effect) |
| gpt-5.6-luna ($0.20/$1.20) | FAIL | standard-form recall, 18 terms across the families, diffuse |
| gpt-5.6-terra ($2/$12) | **PASS** | all 8 green (full run + F1/F2 focused on final goldens) |

The standard-form family's 359 per-term judgments are the discriminating
workload: both sub-Sonnet tiers passed prohibited/fields/PDF and failed
only there. Terra's three residual pre-fix failures were golden
content-overlap defects, not model errors - VIC F2 t5 restates the
extension pair's 5-years-and-a-day rule, and both forms' date-of-agreement
term is substantively supplied by the Signatures term's Dated lines - fixed
by extending the sibling co-deletion clusters, after which both terra and
the primary (freshness rerun) passed F1/F2 on the same documents.

## Excluded candidates

- **claude-haiku-4-5** — no adaptive-thinking support (Models API
  capability check, 2026-07-29): our call shape would 400, so evaluating
  it requires a code change to drop `thinking`, and legal clause judgment
  without thinking is the weakest configuration. Revisit only under real
  cost pressure.
- **Non-Anthropic candidates** (OpenAI mini-class, DeepSeek) — no longer
  need a different client implementation; the harness itself is
  model-agnostic. **Provider failover: DONE, shipped 2026-08-13** (see
  "Backup provider decision" above): a provider adapter behind the judge
  interface, eval-gated like any model change, gives true provider-level
  redundancy (an Anthropic-wide outage no longer takes out Opus and
  Sonnet alike) plus the wider cost sweep recorded above. Retained as
  history, still accurate — Decision 2026-07-29: no intra-Anthropic
  auto-fallback in the meantime — a Sonnet incident is handled by the
  existing failure semantics (job fails fast, manager notified, retry
  button) or the one-line `CLAUSE_AUDIT_MODEL` switch to the
  already-eval-passed Opus.
