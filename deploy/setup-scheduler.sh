#!/usr/bin/env bash
# Programa la sincronización diaria del padrón (Cloud Scheduler → job de Cloud Run).
# Uso: ./deploy/setup-scheduler.sh   (después del primer deploy.sh)
set -euo pipefail
cd "$(dirname "$0")"
source ./config.env

gcloud config set account "$GCP_ACCOUNT" >/dev/null
gcloud config set project "$PROJECT_ID" >/dev/null
SCHED_EMAIL="$SCHEDULER_SA@$PROJECT_ID.iam.gserviceaccount.com"

gcloud run jobs add-iam-policy-binding "$SYNC_JOB" --region="$REGION" \
  --member="serviceAccount:$SCHED_EMAIL" --role=roles/run.invoker >/dev/null

URI="https://run.googleapis.com/v2/projects/$PROJECT_ID/locations/$REGION/jobs/$SYNC_JOB:run"
if gcloud scheduler jobs describe "$SYNC_JOB-diario" --location="$REGION" >/dev/null 2>&1; then
  ACCION=update
else
  ACCION=create
fi
gcloud scheduler jobs $ACCION http "$SYNC_JOB-diario" --location="$REGION" \
  --schedule="$SYNC_SCHEDULE" --time-zone="$SYNC_TIMEZONE" \
  --uri="$URI" --http-method=POST --oauth-service-account-email="$SCHED_EMAIL"

echo "✅ Sincronización programada: '$SYNC_SCHEDULE' ($SYNC_TIMEZONE)"
echo "   Correrla ahora: gcloud run jobs execute $SYNC_JOB --region=$REGION"
