# CI corpus dump design

The deterministic rule evals and clause structure tests self-skip in CI
because the corpus store is absent - rule logic only ever runs on dev
machines (recommended for fixing by two final whole-branch reviews).
Fix: restore a checked-in pg_dump of the corpus into CI's existing
postgres service and point `DATABASE_URL` at it. Test code changes not
at all - the corpus-gated fixtures already run whenever the store
exists.

Chosen over a cited-subset JSON fixture by the owner: the full dump is
the real corpus (every instrument, every version), and the existing
resolve-on-corpus tests give staleness enforcement for free.

## Dump artifact

- `pg_dump -Fc` (custom compressed format) of the corpus tables only -
  the acts and sections tables, full version history for all four
  instruments. No operational tables (audits, tenants, usage, jobs).
- Committed at `tests/fixtures/corpus.dump`. Measure the size during
  implementation and record it in the plan; if it exceeds roughly 15 MB
  revisit storage (LFS) before committing.
- `tests/fixtures/README.md` notes the source and licence: NSW and
  Victorian legislation, (c) State of New South Wales / State of
  Victoria, reproduced under CC BY 4.0, retrieved via the ingest
  pipeline from legislation.nsw.gov.au and legislation.vic.gov.au.

## CI wiring

Two additions to the existing test job (which already runs a
postgres:16 service for `TEST_DATABASE_URL`):

1. A step that creates a `lease_compliance` database on the service and
   `pg_restore`s the dump into it (ubuntu-latest ships a compatible
   postgres client; verify the client/server version pairing during
   implementation).
2. `DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/lease_compliance`
   on the pytest step (confirm the settings env name matches
   `settings.database_url` during implementation).

Result: the corpus-gated tests (NSW rules 24, VIC rules 21, clause
structure tests in both jurisdictions, roughly 55 in total) stop
skipping and run against the full corpus on every push and PR. The
`llm_eval` marker stays opt-in (API key and cost) - unchanged.

## Refresh flow and staleness enforcement

- `scripts/refresh-corpus-dump.sh`: pg_dumps the dev store's corpus
  tables over `tests/fixtures/corpus.dump`. Run manually when rules
  start citing sections the dump predates; doubles as the corpus
  restore path for a fresh dev machine (restore mode documented in the
  script header).
- Enforcement is free: the existing
  `test_every_rule_resolves_on_the_corpus` family (NSW, VIC, clause)
  runs in CI once the dump exists, so a rule citing a section missing
  from the dump turns CI red until the dump is refreshed. No scheduled
  refresh automation.

## Out of scope

- Running `llm_eval` tests in CI.
- Automated dump refresh scheduling.
- Any change to test files, fixtures in conftest, or rule code.
