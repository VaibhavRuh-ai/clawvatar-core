FROM python:3.11-slim

# System deps for engine (EGL headless rendering) + audio
RUN apt-get update && apt-get install -y --no-install-recommends \
    libegl1 libgl1-mesa-glx libglib2.0-0 libsm6 libxrender1 libxext6 \
    libportaudio2 ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY pyproject.toml README.md ./
COPY clawvatar_core/ clawvatar_core/
RUN pip install --no-cache-dir ".[google]"

# Create data directories
RUN mkdir -p /root/.clawvatar/avatars

# Expose ports: server (8766) + agent health (8081)
EXPOSE 8766 8081

# Default: run the server
CMD ["clawvatar-core", "serve", "--port", "8766"]
