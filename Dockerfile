# Image unique AuroraCast : l'API (commande par défaut) et les tâches planifiées
# (Container Apps Jobs, commande « python -m auroracast.cloud.jobs <tâche> »).
#   docker build -t auroracast .
#   docker run --rm -p 8000:8000 -v "$PWD/models:/app/models:ro" auroracast   # http://localhost:8000
# Publiée sur GitHub Container Registry (gratuit) par .github/workflows/azure-deploy.yml.
# Aucun secret dans l'image : DATABASE_URL vient de Key Vault, le stockage de l'identité gérée.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    AURORACAST_WEB_DIR=/app/web \
    AURORACAST_MODELS_DIR=/app/models \
    AURORACAST_SITE_DIR=/app/site

WORKDIR /app

# Dépendances d'abord : couche réutilisée tant que les requirements ne changent pas
COPY requirements.txt requirements-azure.txt ./
RUN pip install -r requirements-azure.txt

COPY pyproject.toml ./
COPY src ./src
COPY sql ./sql
COPY web ./web
# mode éditable : le code reste dans /app/src, donc sql/schema.sql et web/ restent trouvables
RUN pip install --no-deps -e . \
    && useradd --create-home --uid 10001 auroracast \
    && mkdir -p /app/models /app/site && chown -R auroracast /app/models /app/site

USER auroracast
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"
CMD ["uvicorn", "auroracast.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
