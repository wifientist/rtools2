#!/usr/bin/env bash
set -euo pipefail

# Change to deployment directory
DEPLOY_DIR="/opt/rtools2"
cd "$DEPLOY_DIR"

echo "🚀 Deploying Ruckus Tools from ${DEPLOY_DIR}..."

# Prevent concurrent runs
exec 9>/tmp/rtools2-deploy.lock
flock -n 9 || { echo "Another deploy is running."; exit 0; }

# Source env (supports export-less lines)
set -a
source ./.env.production
set +a

# Before git reset, save current state
PREVIOUS_COMMIT=$(git rev-parse HEAD)
echo "Previous commit: $PREVIOUS_COMMIT (for rollback)"

# Pull latest code
git fetch --all --prune
git reset --hard origin/main

# Function to build with fallback Dockerfiles
build_with_fallback() {
    local service=$1
    local context_dir=$2
    local image_name=$3

    echo "🔧 Building $service with fallback strategy..."

    # Try each Dockerfile in priority order
    for dockerfile in "Dockerfile.prod.ecr" "Dockerfile.prod" "Dockerfile"; do
        if [ -f "$context_dir/$dockerfile" ]; then
            echo "  Trying $dockerfile..."
            if docker build -t "$image_name" -f "$context_dir/$dockerfile" "$context_dir"; then
                echo "  ✅ Success with $dockerfile"
                return 0
            else
                echo "  ❌ Failed with $dockerfile"
            fi
        fi
    done

    echo "  🚨 All Dockerfiles failed for $service!"
    return 1
}

# Build images with docker compose (which will use fallback Dockerfiles automatically)
echo "🔧 Building images..."
docker compose --env-file .env.production build --no-cache --pull

echo "📦 Bringing up services..."
docker compose --env-file .env.production up -d --force-recreate

# Clean up old images and containers to prevent disk bloat
echo "🧹 Cleaning up old Docker resources..."
docker image prune -af --filter "until=24h"
docker container prune -f
docker volume prune -f --filter "label!=keep"
echo "✅ Cleanup complete"

# Wait for services
echo "⏳ Waiting for services to start..."
sleep 15

# Check service health
echo "🔍 Checking service health..."
if docker compose --env-file .env.production ps | grep -q "Up"; then
    echo "✅ Services are running"
else
    echo "❌ Some services may not be healthy. Check with: docker compose --env-file .env.production ps"
fi

# Migrations with enhanced diagnostics
echo "🗃️ Running Alembic migrations..."

# Check migration files in container
echo "📂 Checking migration files in container:"
docker compose --env-file .env.production exec -T backend ls -la /app/alembic/versions/ || echo "⚠️ Could not list migration files"

# Show current state
echo "📊 Current migration state:"
docker compose --env-file .env.production exec -T backend alembic current -v

# Show migration history
echo "📜 Migration history:"
docker compose --env-file .env.production exec -T backend alembic history

# Show what migrations are pending
echo "⏳ Pending migrations:"
docker compose --env-file .env.production exec -T backend alembic heads

# Run migrations with retry logic
echo "🚀 Applying migrations..."
MIGRATION_ATTEMPTS=3
for attempt in $(seq 1 $MIGRATION_ATTEMPTS); do
    echo "Migration attempt $attempt/$MIGRATION_ATTEMPTS"
    if docker compose --env-file .env.production exec -T backend alembic upgrade head; then
        echo "✅ Migrations completed successfully"
        break
    else
        echo "❌ Migration attempt $attempt failed"
        if [ $attempt -eq $MIGRATION_ATTEMPTS ]; then
            echo "🚨 All migration attempts failed! Check logs above."
            echo "Manual fix: docker compose --env-file .env.production exec backend alembic upgrade head"
            exit 1
        fi
        echo "⏳ Waiting 5 seconds before retry..."
        sleep 5
    fi
done

# Verify final state
echo "🔍 Final migration state:"
docker compose --env-file .env.production exec -T backend alembic current

# Health check
if [[ -n "${API_HOST:-}" ]]; then
  echo "🔍 Checking API health..."
  if curl -fsS "https://${API_HOST}/api/status" >/dev/null 2>&1; then
    echo "✅ API Health OK"
  else
    echo "❌ API health check failed (continuing; check logs)."
  fi
fi

echo "✅ Deployment complete!"

# Send Slack notification on successful deployment
if [[ -n "${SLACK_WEBHOOK_URL:-}" ]]; then
  echo "📱 Sending Slack notification..."
  COMMIT_SHORT=$(git rev-parse --short HEAD)
  COMMIT_FULL=$(git rev-parse HEAD)
  COMMIT_MSG=$(git log -1 --pretty=%B | head -n 1)
  DEPLOY_TIME=$(date '+%Y-%m-%d %H:%M:%S %Z')
  COMMITTER=$(git log -1 --pretty=%an)

  # Build commit URL if GITHUB_REPO_URL is set
  if [[ -n "${GITHUB_REPO_URL:-}" ]]; then
    COMMIT_URL="${GITHUB_REPO_URL}/commit/${COMMIT_FULL}"
    COMMIT_LINK="<${COMMIT_URL}|${COMMIT_SHORT}>"
  else
    COMMIT_LINK="${COMMIT_SHORT}"
  fi

  # Slack webhook payload with rich formatting
  PAYLOAD=$(cat <<EOF
{
  "text": "🚀 Ruckus Tools Deployment Complete",
  "blocks": [
    {
      "type": "header",
      "text": {
        "type": "plain_text",
        "text": "🚀 Ruckus Tools Deployed",
        "emoji": true
      }
    },
    {
      "type": "section",
      "fields": [
        {
          "type": "mrkdwn",
          "text": "*Status:*\n✅ Successfully deployed to production"
        },
        {
          "type": "mrkdwn",
          "text": "*Environment:*\nProduction"
        }
      ]
    },
    {
      "type": "section",
      "fields": [
        {
          "type": "mrkdwn",
          "text": "*Commit:*\n${COMMIT_LINK}"
        },
        {
          "type": "mrkdwn",
          "text": "*Committer:*\n${COMMITTER}"
        }
      ]
    },
    {
      "type": "section",
      "text": {
        "type": "mrkdwn",
        "text": "*Message:*\n\`${COMMIT_MSG}\`"
      }
    },
    {
      "type": "context",
      "elements": [
        {
          "type": "mrkdwn",
          "text": "🕒 ${DEPLOY_TIME}"
        }
      ]
    }
  ]
}
EOF
)

  curl -s -X POST "${SLACK_WEBHOOK_URL}" \
    -H 'Content-Type: application/json' \
    -d "${PAYLOAD}" \
    > /dev/null 2>&1

  if [ $? -eq 0 ]; then
    echo "✅ Slack notification sent"
  else
    echo "⚠️  Failed to send Slack notification (non-critical)"
  fi
else
  echo "ℹ️  Slack notification skipped (SLACK_WEBHOOK_URL not set)"
fi

# Show final service status
echo ""
echo "📊 Final service status:"
docker compose --env-file .env.production ps

echo ""
echo "🎉 Deployment finished successfully!"
echo "💡 View logs: docker compose --env-file .env.production logs -f"
echo "💡 Rollback: git reset --hard ${PREVIOUS_COMMIT} && ./deploy.sh"
