# Deployment-only image. Local development still uses the existing venv setup.
# Python 3.13 matches the version called out in backend/requirements.txt.
FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    PORT=10000

WORKDIR /app

# Headless OpenCV / scikit-learn runtime libraries, and gosu to safely drop root.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libglib2.0-0 gosu \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home appuser

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

# .dockerignore excludes .env, uploaded evidence, caches, and local artifacts.
COPY . .
RUN mkdir -p /app/storage/media /app/storage/models \
    && chown -R appuser:appuser /app/storage

# Train the *synthetic demo* models during image build, so they exist after
# a fresh deployment without requiring a stateful startup script or database.
# These models are not validated on real valet data.
USER appuser
RUN python utils/scripts/train_security_model.py --episodes 400 \
    && python utils/scripts/train_retrieval_model.py --episodes 1000
USER root

COPY --chmod=755 docker-entrypoint.sh /usr/local/bin/valet-start
EXPOSE 10000
ENTRYPOINT ["/usr/local/bin/valet-start"]
