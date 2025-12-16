#!/bin/bash
set -e

echo ">>> 停止舊容器..."
docker compose down

echo ">>> 重新建置並啟動容器（使用 docker-compose.yml）"
docker compose build --no-cache
docker compose up -d

echo ">>> 目前狀態："
docker compose ps

