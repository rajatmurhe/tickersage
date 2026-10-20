FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt

# Docs RAG models (bge-small embeddings + MiniLM cross-encoder, ~200 MB) baked in when the image will run with
# EMBEDDING_PROVIDER=local / RERANK_PROVIDER=cross-encoder (docker-compose.yml sets this), so startup needs no download.
ARG PRELOAD_RAG_MODELS=0
ENV MODEL_CACHE_DIR=/srv/.cache/models
RUN if [ "$PRELOAD_RAG_MODELS" = "1" ]; then python -c "from fastembed import TextEmbedding; \
from fastembed.rerank.cross_encoder import TextCrossEncoder; \
TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/srv/.cache/models'); \
TextCrossEncoder('Xenova/ms-marco-MiniLM-L-6-v2', cache_dir='/srv/.cache/models')"; fi

COPY backend ./backend
COPY evals ./evals
# Bake a replay eval run into the image so /evals has results (offline LLM, recorded tools: ~5 s, no network).
RUN python -m evals.run_evals

RUN useradd --create-home app && chown -R app /srv
USER app

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT:-8000}"]
