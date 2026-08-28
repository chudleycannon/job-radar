FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md requirements.txt ./
COPY jobradar ./jobradar
COPY sources ./sources
COPY skills ./skills

RUN pip install --upgrade pip \
    && pip install -e ".[pdf]"

WORKDIR /data

EXPOSE 8765

ENTRYPOINT ["job-radar"]
CMD ["--help"]
