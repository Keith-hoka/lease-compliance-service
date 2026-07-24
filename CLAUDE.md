# lease-compliance-service

Australian residential lease compliance audit API with a temporal legislation
store. Standalone service; the rental-management SaaS
(`~/LLMProjects/rental_management_app`) is its first client. Findings are
general information, not legal advice — product copy must say so.

## Conventions

- Python 3.12+, `uv` only: `uv run ...`, `uv add ...` — never `python3`/`pip`.
- FastAPI + async SQLAlchemy 2.0 + Alembic + PostgreSQL.
- TDD: write the failing test first, watch it fail for the right reason, then
  implement. No task is done without the full suite passing.
- Ruff sequence before every push, in this exact order from the repo root:
  `uv run ruff format .` -> `uv run ruff check --fix .` ->
  `uv run ruff check .` -> `uv run ruff format --check .`
- No emojis in code, logs, or prints.
- Short modules and functions; docstrings over inline comments; do not
  overengineer or program defensively.
- Work incrementally: small steps, validate each before moving on.

## Domain rules

- Deterministic before LLM: anything computable from structured data never
  goes through a model. LLM stages (later milestones) handle only judgments
  that require reading free text, and must cite Act + section and abstain
  when unsure.
- `jurisdiction` is a first-class dimension everywhere (corpus, rules,
  findings, evals) even while only NSW ships.
- Every finding carries a statutory citation and an as-at date; legislation
  queries are always point-in-time (`valid_from <= as_at < valid_to`).
- Eval-first: every capability ships with an eval. Deterministic rules use
  exact-assert pytest cases; later LLM stages use seeded-violation golden
  sets with per-rule precision/recall.

## Workflow

New features: brainstorm -> spec (`docs/superpowers/specs/`) -> plan
(`docs/superpowers/plans/`) -> inline TDD execution task-by-task, each task
ending with full test run, ruff sequence, commit, push, CI green, then report
and wait for approval before the next task.
