#!/usr/bin/env bash
# Permite que GitHub Actions despliegue en GCP sin llaves (Workload Identity Federation).
# Uso: ./deploy/setup-github-cicd.sh   (1 vez, después de setup-gcp.sh y con GITHUB_REPO en config.env)
set -euo pipefail
cd "$(dirname "$0")"
source ./config.env
[[ "$GITHUB_REPO" != CAMBIAR/* ]] || { echo "Define GITHUB_REPO en deploy/config.env"; exit 1; }

existe() { "$@" >/dev/null 2>&1; }
gcloud config set account "$GCP_ACCOUNT" >/dev/null
gcloud config set project "$PROJECT_ID" >/dev/null
gcloud services enable iamcredentials.googleapis.com sts.googleapis.com

NUM=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
DEPLOY_EMAIL="$DEPLOYER_SA@$PROJECT_ID.iam.gserviceaccount.com"
RUN_EMAIL="$RUNTIME_SA@$PROJECT_ID.iam.gserviceaccount.com"

echo "▶ Cuenta de servicio de despliegue"
existe gcloud iam service-accounts describe "$DEPLOY_EMAIL" || \
  gcloud iam service-accounts create "$DEPLOYER_SA" --display-name="Vota Informado (GitHub Actions)"
for role in roles/run.admin roles/artifactregistry.writer; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$DEPLOY_EMAIL" \
    --role="$role" --condition=None --quiet >/dev/null
done
# Para poder desplegar servicios que corren como la cuenta de runtime
gcloud iam service-accounts add-iam-policy-binding "$RUN_EMAIL" \
  --member="serviceAccount:$DEPLOY_EMAIL" --role=roles/iam.serviceAccountUser --quiet >/dev/null

echo "▶ Workload Identity Federation para $GITHUB_REPO"
existe gcloud iam workload-identity-pools describe "$WIF_POOL" --location=global || \
  gcloud iam workload-identity-pools create "$WIF_POOL" --location=global --display-name="GitHub"
existe gcloud iam workload-identity-pools providers describe "$WIF_PROVIDER" \
    --workload-identity-pool="$WIF_POOL" --location=global || \
  gcloud iam workload-identity-pools providers create-oidc "$WIF_PROVIDER" \
    --workload-identity-pool="$WIF_POOL" --location=global \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition="assertion.repository == '$GITHUB_REPO'"

POOL="projects/$NUM/locations/global/workloadIdentityPools/$WIF_POOL"
gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_EMAIL" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/$POOL/attribute.repository/$GITHUB_REPO" --quiet >/dev/null

echo "✅ Listo. En GitHub → Settings → Secrets and variables → Actions → Variables, crea:"
echo "   GCP_WIF_PROVIDER = $POOL/providers/$WIF_PROVIDER"
echo "   GCP_DEPLOYER_SA  = $DEPLOY_EMAIL"
echo "   (o con gh: gh variable set GCP_WIF_PROVIDER -R $GITHUB_REPO -b \"$POOL/providers/$WIF_PROVIDER\")"
