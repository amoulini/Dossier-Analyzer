# Dossier Analyzer — Streamlit app
# Cloud Run: attach service account (no JSON key). Platform sets PORT (usually 8080).
# OAuth: pass STREAMLIT_AUTH_* (or GOOGLE_CLIENT_*) env vars; streamlit_entry.py writes secrets.toml at boot.
# Local: docker compose may mount gcp-service-account.json and optional secrets.toml instead.

FROM python:3.13-slim-bookworm

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/app/.venv/bin:${PATH}" \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    PORT=8080

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY dossier_analyzer ./dossier_analyzer

RUN uv sync --frozen --no-dev

COPY app.py streamlit_entry.py ./
COPY .streamlit/config.toml ./.streamlit/config.toml

EXPOSE 8080

CMD ["python", "streamlit_entry.py"]
