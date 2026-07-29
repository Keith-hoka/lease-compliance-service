#!/usr/bin/env bash
# Deploy (or roll back) the service. Usage: deploy.sh [image-tag]
set -euo pipefail

TAG="${1:-latest}"
SERVER="${LEASE_DEPLOY_SERVER:?set LEASE_DEPLOY_SERVER, e.g. deploy@1.2.3.4}"
DOMAIN="${LEASE_DEPLOY_DOMAIN:?set LEASE_DEPLOY_DOMAIN, e.g. api.example.com}"

echo "deploying tag ${TAG} to ${SERVER}"
ssh "$SERVER" "cd /opt/lease-compliance \
  && API_TAG='${TAG}' docker compose pull api \
  && API_TAG='${TAG}' docker compose run --rm api uv run --no-sync alembic upgrade head \
  && API_TAG='${TAG}' docker compose up -d"

sleep 3
curl -fsS "https://${DOMAIN}/health"
echo ""
echo "deployed ${TAG}"
