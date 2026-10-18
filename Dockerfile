FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY backend ./backend
COPY evals ./evals
# Bake a replay eval run into the image so /evals has results (offline LLM, recorded tools: ~5 s, no network).
RUN python -m evals.run_evals

RUN useradd --create-home app && chown -R app /srv
USER app

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT:-8000}"]
