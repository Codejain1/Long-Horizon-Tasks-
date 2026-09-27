FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[embeddings]"

# Hosted mode: streamable HTTP behind a static dev key (real API keys arrive in Phase 7).
ENV HORIZON_DB_URL=postgresql://horizon:horizon@postgres:5432/horizon
EXPOSE 8000
CMD ["horizon", "serve", "--transport", "http", "--host", "0.0.0.0", "--port", "8000"]
