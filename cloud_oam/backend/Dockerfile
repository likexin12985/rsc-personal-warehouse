FROM python:3.12-slim

ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=5

WORKDIR /app
COPY requirements.txt requirements-linux-amd64.lock requirements-build.in requirements-build.lock ./
RUN pip install --no-cache-dir --require-hashes --index-url "$PIP_INDEX_URL" -r requirements-build.lock
RUN pip install --no-cache-dir --no-build-isolation --require-hashes --index-url "$PIP_INDEX_URL" -r requirements-linux-amd64.lock
RUN pip uninstall -y setuptools wheel
COPY app ./app
COPY formal_file_integrity.py ./formal_file_integrity.py
RUN mkdir -p /data/uploads

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
