#!/usr/bin/env bash
# Da rol de administrador a un usuario ya registrado en la API desplegada.
# Uso: ./deploy/make-admin.sh correo@ejemplo.cl
set -euo pipefail
cd "$(dirname "$0")"
source ./config.env
[ $# -eq 1 ] || { echo "Uso: $0 correo@ejemplo.cl"; exit 1; }
gcloud run jobs execute "$SYNC_JOB" --project="$PROJECT_ID" --region="$REGION" --wait \
  --args="-m,app.cli,admin,$1"
