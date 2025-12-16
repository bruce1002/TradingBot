FROM python:3.11-slim

WORKDIR /app

# system deps (可留空；先保守加 curl 方便 debug)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# copy app code
COPY . /app

EXPOSE 8000
