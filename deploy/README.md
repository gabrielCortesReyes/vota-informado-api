# Despliegue de Vota Informado en Google Cloud

Arquitectura:

```
Cloud Scheduler (05:00 diario) ──▶ Cloud Run Job "vota-informado-sync"  (python -m app.cli sincronizar)
                                              │
Usuarios ──▶ Cloud Run "vota-informado" (API) ─┴──▶ Cloud SQL PostgreSQL 16 (vota_informado)
                         │                         Secret Manager: app-secret-key, postgres-password
                         └──▶ opendata.camara.cl / senado.cl / wikipedia (en vivo)
```

La sincronización corre como **job** (no como `POST /admin/sincronizar`) porque Cloud Run apaga la CPU
de la API al terminar cada request, y una tarea en segundo plano de varios minutos puede quedar a medias.

## Costo aproximado (USD/mes)

| Recurso | Costo |
|---|---|
| Cloud SQL db-f1-micro + 10 GB SSD + respaldos | ~10-15 (se cobra siempre que la instancia exista) |
| Cloud Run (API y job, escala a cero) | ~0 con poco tráfico (capa gratuita) |
| Cloud Scheduler, Secret Manager, Artifact Registry | ~0-1 |

Para no pagar Cloud SQL mientras no se use: `gcloud sql instances patch vota-informado-db --activation-policy=NEVER`
(la detiene; el almacenamiento se sigue cobrando).

## Requisitos (en tu Mac)

1. [gcloud CLI](https://cloud.google.com/sdk/docs/install) y `gcloud auth login gabriel@softmind.cl`.
2. Proyecto `vota-informado-513922` con facturación activa (región `us-central1`).
3. Revisar `deploy/config.env` (User-Agent de Wikipedia y `GITHUB_REPO`).

## Pasos

```bash
cd vota-informado-api
./deploy/setup-gcp.sh          # 1 vez: APIs, Cloud SQL, secretos, cuentas de servicio (~10 min)
./deploy/deploy.sh             # construye la imagen y despliega la API y el job
gcloud run jobs execute vota-informado-sync --region=us-central1 --wait   # primera carga del padrón
./deploy/setup-scheduler.sh    # 1 vez: sincronización diaria
./deploy/setup-github-cicd.sh  # 1 vez: autoriza a GitHub Actions a desplegar (sin llaves)
```

`setup-github-cicd.sh` imprime dos valores que se guardan como **variables** (no secretos) del repo en
GitHub: `GCP_WIF_PROVIDER` y `GCP_DEPLOYER_SA`.

Luego registra tu usuario en `https://<url>/docs` (`POST /auth/register`) y hazte administrador:

```bash
./deploy/make-admin.sh tu-correo@ejemplo.cl
```

## CI/CD (GitHub Actions)

`.github/workflows/deploy.yml` corre los tests en cada push y pull request, y en cada push a `main`
construye la imagen y despliega la API y el job (`./deploy/deploy.sh`, etiquetando la imagen con el commit).
GitHub se autentica en GCP con Workload Identity Federation: no hay llaves JSON guardadas, y solo el
repositorio definido en `GITHUB_REPO` puede usar la cuenta `vota-informado-deployer`.

También se puede desplegar a mano desde el Mac con `./deploy/deploy.sh` (usa Cloud Build).

## Comandos útiles

```bash
gcloud run services logs read vota-informado --region=us-central1
gcloud run jobs executions list --job=vota-informado-sync --region=us-central1
gcloud sql connect vota-informado-db --user=vota_informado --database=vota_informado
```
