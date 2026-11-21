FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# P1 需要 yt-dlp 与 ffmpeg；先在镜像层装好系统依赖
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
# 需要 ASR/OCR 时：docker build --build-arg PIP_EXTRAS="[asr,ocr]" ...
ARG PIP_EXTRAS=""
RUN pip install ".${PIP_EXTRAS}"

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
