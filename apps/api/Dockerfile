# Image de dev : uv pour l'installation, uvicorn --reload pour le hot reload.
# Le code est monté en volume par docker-compose, l'image ne sert qu'à porter
# l'environnement Python.
FROM python:3.13-slim

# uv est distribué comme binaire statique : on le récupère depuis son image.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Couche de dépendances séparée : elle n'est réinvalidée que si pyproject bouge.
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-install-project --group dev

# Le projet lui-même, installé en editable : le bind mount de ./src prend le relais.
COPY . .
RUN uv sync --group dev

EXPOSE 4310

CMD ["uvicorn", "agent.main:app", \
     "--host", "0.0.0.0", "--port", "4310", \
     "--reload", "--reload-dir", "/app/src"]
