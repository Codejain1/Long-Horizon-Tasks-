# Horizon hosted server: MCP at /mcp (team API keys, metered credits) and the account page at /.
# See docs/HOSTING.md. Build: docker build -t horizon .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install ".[embeddings,decision,export,web]"

RUN useradd --create-home --uid 10001 horizon && mkdir -p /data/exports && chown -R horizon /data
USER horizon
ENV HORIZON_EXPORT_DIR=/data/exports FASTEMBED_CACHE_PATH=/home/horizon/.cache/fastembed

# Fetch the embedding model (BAAI/bge-small-en-v1.5) at build time, so the server starts without network access.
# If the build has no network, the server falls back to the offline hash embedder and logs a warning.
RUN python -c "from horizon.memrouter.embedding import make_embedder; print(make_embedder('fastembed').model_name)" \
    || echo "embedding model not prefetched: the server will fall back to the hash embedder"

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status != 200)"
CMD ["horizon", "serve", "--transport", "http", "--host", "0.0.0.0", "--port", "8000"]
