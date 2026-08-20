# ── stage 1: build frontend ──
FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# ── stage 2: backend + static ──
FROM python:3.12-slim-bookworm
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# system libs needed by pillow/opencv/torch (MegaDetector) for image decoding
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY VERSION ./VERSION
COPY --from=frontend /build/dist ./static
ENV STATIC_DIR=/app/static
ENV CAMERA_IMAGE_DIR=/app/data/camera_images
RUN mkdir -p /app/data/camera_images
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
