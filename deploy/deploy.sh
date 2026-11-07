#!/usr/bin/env bash
# Construye la imagen y despliega la API y el job de sincronización en Cloud Run.
# Uso: ./deploy/deploy.sh            (lo corre GitHub Actions en cada push a main;
#                                     también sirve a mano desde tu Mac)
# En CI (variable CI=true) construye con docker en el runner en vez de Cloud Build.
set -euo pipefail
cd "$(dirname "$0")"
source ./config.env

if [ "${CI:-}" != "true" ]; then
  gcloud config set account "$GCP_ACCOUNT" >/dev/null
fi
gcloud config set project "$PROJECT_ID" >/dev/null

TAG=${IMAGE_TAG:-$(date +%Y%m%d-%H%M%S)}
IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/$SERVICE:$TAG"
CONN="$PROJECT_ID:${SQL_REGION:-$REGION}:$SQL_INSTANCE"   # SQL_REGION en config.env si la instancia está en otra región
RUN_EMAIL="$RUNTIME_SA@$PROJECT_ID.iam.gserviceaccount.com"
# "^|^" cambia el separador a "|" para que el User-Agent pueda tener comas o espacios
ENV_VARS="^|^PGSQL_HOSTNAME=/cloudsql/$CONN|PGSQL_USERNAME=$DB_USER|PGSQL_DBNAME=$DB_NAME|WIKIPEDIA_USER_AGENT=$WIKIPEDIA_USER_AGENT"
SECRETS="APP_SECRET_KEY=app-secret-key:latest,PGSQL_PASSWORD=postgres-password:latest"

if [ "${CI:-}" = "true" ]; then
  echo "▶ Construyendo $IMAGE (docker)"
  gcloud auth configure-docker "$REGION-docker.pkg.dev" --quiet
  docker build -t "$IMAGE" ..
  docker push "$IMAGE"
else
  echo "▶ Construyendo $IMAGE (Cloud Build)"
  gcloud builds submit .. --tag "$IMAGE"
fi

echo "▶ Desplegando la API ($SERVICE)"
gcloud run deploy "$SERVICE" --image="$IMAGE" --region="$REGION" \
  --service-account="$RUN_EMAIL" --allow-unauthenticated \
  --cpu=1 --memory=512Mi --min-instances=0 --max-instances=3 --concurrency=80 --timeout=60 \
  --add-cloudsql-instances="$CONN" \
  --set-env-vars="$ENV_VARS" \
  --set-secrets="$SECRETS"

echo "▶ Desplegando el job de sincronización ($SYNC_JOB)"
gcloud run jobs deploy "$SYNC_JOB" --image="$IMAGE" --region="$REGION" \
  --service-account="$RUN_EMAIL" --command=python --args=-m,app.cli,sincronizar \
  --cpu=1 --memory=512Mi --max-retries=1 --task-timeout=30m \
  --set-cloudsql-instances="$CONN" \
  --set-env-vars="$ENV_VARS" \
  --set-secrets="$SECRETS"

URL=$(gcloud run services describe "$SERVICE" --region="$REGION" --format="value(status.url)")
echo "✅ API: $URL   Docs: $URL/docs"
