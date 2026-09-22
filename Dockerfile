# The agent worker. One process, always on, never sleeps.
#
# We self-host rather than use LiveKit Cloud's hosted agents because the free
# Build tier cold-starts 10-20 SECONDS after idle, and this venue takes ~3 calls
# a day — so essentially every call would pay it. A worker we run ourselves is
# always warm. See docs/HOSTING_DECISION.md.

FROM python:3.11-slim

# Build tools are needed for a few wheels; removed in the same layer so they
# don't ship. onnxruntime (turn detector) and soundfile need libgomp/libsndfile.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libgomp1 libsndfile1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so a code change doesn't re-resolve the whole tree.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir -e ".[agent]" \
    && apt-get purge -y build-essential && apt-get autoremove -y

COPY mock_libro/ ./mock_libro/
COPY scripts/ ./scripts/

# Download the turn-detector / VAD model weights at BUILD time. Without this the
# first call after every deploy pays the download — which is the cold start we
# moved off LiveKit Cloud to avoid.
RUN python -m resto_agent.agent download-files || \
    echo "WARNING: model prefetch failed; first call will be slow"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# `start` runs the worker against LiveKit and waits for dispatched calls.
CMD ["python", "-m", "resto_agent.agent", "start"]
