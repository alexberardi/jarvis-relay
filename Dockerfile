FROM python:3.11-slim

WORKDIR /app

COPY app/ app/
COPY pyproject.toml .
RUN pip install --no-cache-dir .

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV RELAY_PORT=7735

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${RELAY_PORT:-8080}"]
