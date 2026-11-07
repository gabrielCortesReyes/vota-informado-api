FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8080

RUN useradd -r appuser
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app ./app
USER appuser

EXPOSE 8080
# Cloud Run define PORT; en local se puede cambiar con -e PORT=8000
CMD exec uvicorn app.main:app --host 0.0.0.0 --port $PORT
