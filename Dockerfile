FROM node:24-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_DB=/app/data/app.db APP_STORAGE=/app/data/uploads
WORKDIR /app
COPY requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt && useradd --uid 10001 --create-home appuser
COPY backend/ ./backend/
COPY --from=frontend /build/dist ./frontend/dist
RUN mkdir -p /app/data && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import urllib.request,os,base64; t=base64.b64encode((os.getenv('APP_ACCESS_USER','')+':'+os.getenv('APP_ACCESS_PASSWORD','')).encode()).decode(); r=urllib.request.Request('http://127.0.0.1:8000/api/health',headers={'Authorization':'Basic '+t}); urllib.request.urlopen(r,timeout=3)"
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
