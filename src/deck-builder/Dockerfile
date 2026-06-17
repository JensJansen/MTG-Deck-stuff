# syntax=docker/dockerfile:1
#
# Two-stage image: a shared base with dependencies, then separate targets
# for training and inference so Fly.io can run whichever is needed.
#
# Build for training  (heavy, includes psycopg2 for DB access):
#   docker build --target train -t deck-builder-train .
#   docker run --gpus all -e DATABASE_URL=... -v $(pwd)/data:/app/data deck-builder-train
#
# Build for inference (lightweight, no DB dependency at runtime):
#   docker build --target inference -t deck-builder-inference .
#   docker run -p 8080:8080 -e MODEL_CHECKPOINT=/app/checkpoints/epoch_030.pt deck-builder-inference

FROM pytorch/pytorch:2.1.0-cuda11.8-cudnn8-runtime AS base

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config.py vocabulary.py model.py dataset.py ./


# ── Training target ────────────────────────────────────────────────────────────
FROM base AS train

COPY preprocess.py train.py ./

ENV DATA_DIR=/app/data
ENV CHECKPOINT_DIR=/app/checkpoints

# Volumes should be mounted for data/ and checkpoints/ so output persists.
CMD ["python", "train.py"]


# ── Inference target ───────────────────────────────────────────────────────────
FROM base AS inference

COPY inference.py ./

# When a serving API is added (e.g. FastAPI), install it here:
# RUN pip install --no-cache-dir fastapi uvicorn

ENV DATA_DIR=/app/data
ENV CHECKPOINT_DIR=/app/checkpoints
ENV MODEL_CHECKPOINT=/app/checkpoints/best.pt
ENV DEVICE=cpu

# Placeholder: replace with `uvicorn api:app --host 0.0.0.0 --port 8080`
# once the HTTP API wrapper is added.
CMD ["python", "-c", "from inference import DeckRecommender; print('Inference image ready.')"]
