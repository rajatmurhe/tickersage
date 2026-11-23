#!/usr/bin/env bash
# Deploys TickerSage to Cloud Run with Gemini on Vertex AI as the only model provider.
#
#   PROJECT=my-gcp-project ./deploy/cloudrun/deploy.sh
#
# Needs: gcloud signed in (`gcloud auth login`), a project with billing on, and permission to enable APIs,
# create a service account and deploy to Cloud Run. Optional env: REGION (default asia-southeast1),
# VERTEX_LOCATION (default global), VERTEX_PRICE_INPUT / VERTEX_PRICE_OUTPUT (USD per 1M tokens, from the
# Vertex AI pricing page; without them every call is costed at $0 and DAILY_BUDGET_USD can't trip).
#
# What it creates: two Cloud Run services (tickersage-api, tickersage-web), a service account
# tickersage-api@PROJECT that can only call Vertex AI, and an Artifact Registry repo for the images.
# Data: SQLite on the container's disk, so alerts and traces reset whenever an instance is replaced.
set -euo pipefail

: "${PROJECT:?set PROJECT to your Google Cloud project id}"
REGION="${REGION:-asia-southeast1}"
VERTEX_LOCATION="${VERTEX_LOCATION:-global}"
REPO="tickersage"
SA="tickersage-api@${PROJECT}.iam.gserviceaccount.com"
IMAGES="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TAG="$(git -C "$ROOT" rev-parse --short HEAD)"

gcloud config set project "$PROJECT" >/dev/null

echo "== enabling APIs"
gcloud services enable run.googleapis.com aiplatform.googleapis.com artifactregistry.googleapis.com \
  cloudbuild.googleapis.com

echo "== image repo and service account"
gcloud artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1 ||
  gcloud artifacts repositories create "$REPO" --repository-format docker --location "$REGION"
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 ||
  gcloud iam service-accounts create tickersage-api --display-name "TickerSage API"
gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:${SA}" \
  --role roles/aiplatform.user --condition None >/dev/null

echo "== API image"
gcloud builds submit "$ROOT" --tag "${IMAGES}/api:${TAG}"

ADMIN_TOKEN="${ADMIN_TOKEN:-$(openssl rand -hex 24)}"
ENV_VARS="PRIMARY_PROVIDER=vertex,VERTEX_PROJECT=${PROJECT},VERTEX_LOCATION=${VERTEX_LOCATION},LLM_MODE=live"
ENV_VARS+=",DATABASE_URL=sqlite:////tmp/tickersage.db,RATE_LIMIT_PER_MINUTE=5,RATE_LIMIT_PER_DAY=40"
ENV_VARS+=",TRUST_PROXY=true,DAILY_BUDGET_USD=2,ADMIN_TOKEN=${ADMIN_TOKEN}"
if [[ -n "${VERTEX_PRICE_INPUT:-}" && -n "${VERTEX_PRICE_OUTPUT:-}" ]]; then
  ENV_VARS+=",VERTEX_PRICE_INPUT=${VERTEX_PRICE_INPUT},VERTEX_PRICE_OUTPUT=${VERTEX_PRICE_OUTPUT}"
else
  echo "note: VERTEX_PRICE_INPUT/OUTPUT not set, so costs are recorded as \$0 and the daily budget can't trip"
fi

echo "== API service"
# One instance at most, scaled to zero when idle: a cold start takes a few seconds, which the UI waits out.
gcloud run deploy tickersage-api --image "${IMAGES}/api:${TAG}" --region "$REGION" \
  --service-account "$SA" --allow-unauthenticated --min-instances 0 --max-instances 1 \
  --memory 1Gi --cpu 1 --set-env-vars "$ENV_VARS"
API_URL="$(gcloud run services describe tickersage-api --region "$REGION" --format 'value(status.url)')"

echo "== web image (NEXT_PUBLIC_API_URL=${API_URL} is built into the bundle)"
BUILD_CONFIG="$(mktemp)"
cat > "$BUILD_CONFIG" <<EOF
steps:
  - name: gcr.io/cloud-builders/docker
    args: ["build", "--build-arg", "NEXT_PUBLIC_API_URL=${API_URL}", "-t", "${IMAGES}/web:${TAG}", "."]
images: ["${IMAGES}/web:${TAG}"]
EOF
gcloud builds submit "$ROOT/frontend" --config "$BUILD_CONFIG"
rm -f "$BUILD_CONFIG"

echo "== web service"
gcloud run deploy tickersage-web --image "${IMAGES}/web:${TAG}" --region "$REGION" \
  --allow-unauthenticated --min-instances 0 --max-instances 1 --memory 512Mi --port 3000
WEB_URL="$(gcloud run services describe tickersage-web --region "$REGION" --format 'value(status.url)')"

echo "== letting the web origin call the API"
gcloud run services update tickersage-api --region "$REGION" \
  --update-env-vars "^@^CORS_ORIGINS=[\"${WEB_URL}\"]"

echo
echo "API: ${API_URL}"
echo "Web: ${WEB_URL}"
echo "Admin token (for /api/traces): ${ADMIN_TOKEN}"
