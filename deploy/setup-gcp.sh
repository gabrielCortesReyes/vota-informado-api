#!/usr/bin/env bash
# Crea (una sola vez) la infraestructura de Vota Informado en GCP.
# Uso: ./deploy/setup-gcp.sh
# Se puede volver a correr: lo que ya existe se salta.
#
# ⚠️ Crea recursos con costo: la instancia de Cloud SQL (~USD 10-15/mes) se cobra mientras exista.
set -euo pipefail
cd "$(dirname "$0")"
source ./config.env

existe() { "$@" >/dev/null 2>&1; }

echo "▶ Cuenta y proyecto: $GCP_ACCOUNT / $PROJECT_ID ($REGION)"
gcloud config set account "$GCP_ACCOUNT"
gcloud config set project "$PROJECT_ID"
gcloud config set run/region "$REGION"

echo "▶ Habilitando APIs"
gcloud services enable \
  run.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com \
  artifactregistry.googleapis.com cloudbuild.googleapis.com cloudscheduler.googleapis.com

echo "▶ Artifact Registry"
existe gcloud artifacts repositories describe "$REPO" --location="$REGION" || \
  gcloud artifacts repositories create "$REPO" --repository-format=docker --location="$REGION" \
    --description="Imágenes de Vota Informado"

echo "▶ Cloud SQL (PostgreSQL 16, $SQL_TIER). Tarda 5-10 minutos la primera vez"
existe gcloud sql instances describe "$SQL_INSTANCE" || \
  gcloud sql instances create "$SQL_INSTANCE" \
    --database-version=POSTGRES_16 --edition=enterprise --tier="$SQL_TIER" --region="$REGION" \
    --storage-type=SSD --storage-size=10 --storage-auto-increase \
    --backup-start-time=07:00 --retained-backups-count=7 \
    --maintenance-window-day=SUN --maintenance-window-hour=8

REGION_SQL=$(gcloud sql instances describe "$SQL_INSTANCE" --format="value(region)")
[ "$REGION_SQL" = "$REGION" ] || echo "⚠️ $SQL_INSTANCE está en $REGION_SQL y Cloud Run en $REGION: funciona, pero con más latencia. Ajusta REGION o deploy.sh"

existe gcloud sql databases describe "$DB_NAME" --instance="$SQL_INSTANCE" || \
  gcloud sql databases create "$DB_NAME" --instance="$SQL_INSTANCE"

echo "▶ Secretos (Secret Manager)"
# La instancia puede exigir mayúscula, minúscula, número y símbolo en las contraseñas
existe gcloud secrets describe postgres-password || \
  printf %s "Vi-$(openssl rand -hex 24)9" | gcloud secrets create postgres-password --data-file=-
# Si la contraseña guardada no cumple esa política (versión antigua de este script), la reemplaza
DB_PASSWORD=$(gcloud secrets versions access latest --secret=postgres-password)
if ! [[ "$DB_PASSWORD" =~ [A-Z] && "$DB_PASSWORD" =~ [^A-Za-z0-9] ]]; then
  DB_PASSWORD="Vi-$(openssl rand -hex 24)9"
  printf %s "$DB_PASSWORD" | gcloud secrets versions add postgres-password --data-file=-
fi
# Deja el usuario con la contraseña del secreto (crea o actualiza; se puede repetir)
if gcloud sql users list --instance="$SQL_INSTANCE" --format="value(name)" | grep -qx "$DB_USER"; then
  gcloud sql users set-password "$DB_USER" --instance="$SQL_INSTANCE" --password="$DB_PASSWORD"
else
  gcloud sql users create "$DB_USER" --instance="$SQL_INSTANCE" --password="$DB_PASSWORD"
fi
existe gcloud secrets describe app-secret-key || \
  printf %s "$(openssl rand -hex 32)" | gcloud secrets create app-secret-key --data-file=-

echo "▶ Cuentas de servicio y permisos"
RUN_EMAIL="$RUNTIME_SA@$PROJECT_ID.iam.gserviceaccount.com"
SCHED_EMAIL="$SCHEDULER_SA@$PROJECT_ID.iam.gserviceaccount.com"
existe gcloud iam service-accounts describe "$RUN_EMAIL" || \
  gcloud iam service-accounts create "$RUNTIME_SA" --display-name="Vota Informado (Cloud Run)"
existe gcloud iam service-accounts describe "$SCHED_EMAIL" || \
  gcloud iam service-accounts create "$SCHEDULER_SA" --display-name="Vota Informado (Scheduler)"
for role in roles/cloudsql.client roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$RUN_EMAIL" \
    --role="$role" --condition=None --quiet >/dev/null
done

# Cloud Build usa la cuenta de servicio por defecto de Compute; en organizaciones nuevas
# ya no recibe permisos automáticamente, así que se los damos explícitamente.
NUM=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
BUILD_EMAIL="$NUM-compute@developer.gserviceaccount.com"
for role in roles/artifactregistry.writer roles/logging.logWriter roles/storage.objectViewer; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$BUILD_EMAIL" \
    --role="$role" --condition=None --quiet >/dev/null
done

echo "✅ Infraestructura lista. Siguiente paso: ./deploy/deploy.sh"
