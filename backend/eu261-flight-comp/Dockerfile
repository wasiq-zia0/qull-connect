# Build from repository root: docker build -f backend/eu261-flight-comp/Dockerfile backend
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ENV=production HOST=0.0.0.0 \
    REST_PORT=8472 MCP_PORT=8572 DATA_DIR=/var/lib/qull \
    BILLING_LEDGER_PATH=/var/lib/qull/billing.sqlite3
WORKDIR /app
COPY eu261-flight-comp/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --system --uid 10001 --no-create-home qull \
    && mkdir -p /var/lib/qull && chown qull:qull /var/lib/qull
COPY eu261-flight-comp/ ./
COPY payment_support/ ./payment_support/
COPY api_support/ ./api_support/
USER qull
VOLUME ["/var/lib/qull"]
EXPOSE 8472 8572
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ['REST_PORT']+'/health',timeout=3)" || exit 1
CMD ["python", "run.py"]
