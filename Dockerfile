# Dossier Analyzer — Streamlit app
FROM python:3.13-slim-bookworm

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/app/.venv/bin:${PATH}"

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY dossier_analyzer ./dossier_analyzer

RUN uv sync --frozen --no-dev

COPY app.py ./
COPY .streamlit ./.streamlit

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health')" || exit 1

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
