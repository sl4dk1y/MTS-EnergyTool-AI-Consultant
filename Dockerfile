FROM node:22-alpine AS frontend

WORKDIR /app
COPY react-frontend/client/package.json react-frontend/client/package-lock.json ./
RUN npm ci
COPY react-frontend/client/ ./
RUN npm run build
ENV VITE_API_PROXY_TARGET=http://backend:8000
EXPOSE 4173
CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0"]

FROM python:3.11-slim AS backend

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data \
    DATABASE_PATH=/app/local-data/web.sqlite3

COPY backend/requirements.txt ./backend/requirements.txt
RUN python -m pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ ./backend/
COPY consultant_contracts.py consultant_engine.py kb_search.py llm_client.py leads.py rag_engine.py ./

EXPOSE 8000
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
