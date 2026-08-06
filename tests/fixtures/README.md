# Test fixtures

`corpus.dump` - pg_dump (custom format) of the corpus tables (acts,
sections, ingested_versions), restored by CI so the corpus-gated rule
and clause tests run there. Refresh with
`scripts/refresh-corpus-dump.sh` when rules cite sections newer than
the dump; the same script's `--restore` mode seeds a fresh dev store.

Legislation text: (c) State of New South Wales and State of Victoria,
reproduced under Creative Commons Attribution 4.0, retrieved via the
ingest pipeline from legislation.nsw.gov.au and
legislation.vic.gov.au.
