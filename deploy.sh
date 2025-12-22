#!/usr/bin/env bash
echo "=== Force remove old container if exists ==="
docker rm -f tvbot 2>/dev/null || true

set -euo pipefail

BRANCH="${1:-main}"
REPO_DIR="/srv/tvbot/repo"

# docker compose 的「service name」（不是 container name）
SERVICE="${SERVICE:-tvbot}"

# 建議你做一個 /health 回 200 的 endpoint（最乾淨）
# 若你目前沒有 health endpoint，可先用 /dashboard（HTML 也行）
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:80/dashboard}"

cd "$REPO_DIR"

echo "=== [1/7] Preflight ==="
echo "Repo:   $REPO_DIR"
echo "Branch: $BRANCH"
echo "Time:   $(date -Is)"
echo "User:   $(whoami)"
echo "PWD:    $(pwd)"

echo "=== [2/7] Git sanity ==="
git rev-parse --is-inside-work-tree >/dev/null

# 工作目錄必須乾淨，避免 deploy 把手改內容覆蓋掉
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "ERROR: Working tree is dirty. Commit/stash changes before deploy."
  git status -sb
  exit 1
fi

echo "=== [3/7] Fetch ==="
git fetch --prune origin

echo "=== [4/7] Checkout branch ==="
# 若本地不存在該分支，建立 tracking 分支
if ! git show-ref --verify --quiet "refs/heads/$BRANCH"; then
  git checkout -b "$BRANCH" "origin/$BRANCH"
else
  git checkout "$BRANCH"
fi

# 強制對齊遠端，避免 merge/rebase 互動
git reset --hard "origin/$BRANCH"

echo "=== [5/7] Build + restart (compose) ==="
# 不一定要 down；直接 up -d --build 會更安全（保留網路/volume）
docker compose up -d --build

echo "=== [6/7] Status + logs (last 120 lines) ==="
docker compose ps
docker compose logs --tail 120 "$SERVICE" || true

echo "=== [7/7] Health check ==="
for i in {1..30}; do
  code="$(curl -s -o /dev/null -w '%{http_code}' "$HEALTH_URL" || true)"
  if [[ "$code" == "200" ]]; then
    echo "OK: $HEALTH_URL (HTTP 200)"
    exit 0
  fi
  echo "Waiting... ($i/30) health=$code url=$HEALTH_URL"
  sleep 1
done

echo "ERROR: Health check failed: $HEALTH_URL"
echo "Tip: check logs:"
echo "  cd $REPO_DIR && docker compose logs --tail 200 $SERVICE"
exit 1

