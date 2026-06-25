# Build the frontend.
FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm ci || npm install
COPY web/ ./
RUN npm run build

# Python runtime that serves the API and the built frontend from one origin.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY api.py whitespace_tracker.py whitespace.db ./
COPY data/ ./data/
COPY --from=web /web/dist ./web/dist
ENV PORT=8000
EXPOSE 8000
# No ANTHROPIC_API_KEY here: the thesis briefs are pre-baked into data/briefs.json,
# so the public service holds no secrets and spends nothing.
CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port ${PORT:-8000}"]
