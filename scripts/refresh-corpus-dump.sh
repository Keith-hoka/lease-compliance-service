#!/usr/bin/env bash
# Refresh tests/fixtures/corpus.dump from the dev corpus store, or
# restore the committed dump into a fresh dev store with --restore.
#
# The dev store runs in the rental_management_app-db-1 postgres
# container (user rental, database lease_compliance, host port 5433).
set -euo pipefail

CONTAINER="rental_management_app-db-1"
TABLES=(-t acts -t sections -t ingested_versions)
DUMP="$(git rev-parse --show-toplevel)/tests/fixtures/corpus.dump"

if [[ "${1:-}" == "--restore" ]]; then
    docker exec -i "$CONTAINER" pg_restore -U rental -d lease_compliance \
        --clean --if-exists --no-owner --no-privileges < "$DUMP"
    echo "restored corpus from $DUMP"
else
    docker exec "$CONTAINER" pg_dump -Fc -U rental -d lease_compliance \
        "${TABLES[@]}" > "$DUMP"
    ls -lh "$DUMP"
fi
