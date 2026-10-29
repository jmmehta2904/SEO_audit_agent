FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/audit
WORKDIR /app
RUN groupadd --gid 10001 audit \
    && useradd --uid 10001 --gid audit --create-home --shell /usr/sbin/nologin audit
COPY requirements.txt requirements.lock ./
RUN python -m pip install --no-cache-dir -r requirements.txt
# Explicit copies keep tests, local runtimes and credentials outside the image.
COPY --chown=audit:audit seo_audit/ ./seo_audit/
COPY --chown=audit:audit app/ ./app/
USER 10001:10001
EXPOSE 8080
CMD ["sh", "-c", "exec python -m uvicorn app.server:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --no-proxy-headers --no-access-log"]
