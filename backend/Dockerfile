FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY data ./data
COPY eval ./eval

RUN useradd --system --uid 10001 appuser && chown -R appuser /srv
USER appuser

EXPOSE 8000

# TCP healthcheck: /api/health requires the internal key and depends on Ollama being on.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import socket; socket.create_connection(('127.0.0.1', 8000), 3)"

CMD ["uvicorn", "app.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000"]
