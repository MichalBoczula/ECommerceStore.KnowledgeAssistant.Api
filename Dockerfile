FROM python:3.12-slim-trixie
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade zlib1g \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN pip install --no-cache-dir uv==0.13.4
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev
ARG CORPUS_FILE=examples/demo-corpus.json
COPY ${CORPUS_FILE} /app/data/corpus.json
ENV KA_CORPUS_PATH=/app/data/corpus.json
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
RUN useradd --uid 10001 --create-home appuser
USER appuser
EXPOSE 8080
CMD [".venv/bin/uvicorn", "knowledge_assistant.main:app", "--host", "0.0.0.0", "--port", "8080"]
