#!/usr/bin/env bash
# 自動更新 tv-binance-bot 部署腳本（適用 ~/apps/tvbot 目錄結構）
# 使用方式（在 ~/apps/tvbot 裡）：
#   ./update_bot.sh tv-binance-bot_deploy_v02.zip

set -euo pipefail

########################################
# 專案路徑設定（依檔案所在目錄自動推算）
########################################
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"                        # 專案根目錄 = 放 docker-compose.yml 的地方
BACKUP_ROOT="$HOME/apps/tvbot_backups"          # 備份根目錄
ZIP_FILE="${1:-}"                               # 第一個參數：新版本 zip 檔名或路徑（必填）

########################################
# 前置檢查
########################################
if [[ -z "$ZIP_FILE" ]]; then
  echo "錯誤：請在執行時帶入 zip 檔名稱，例如："
  echo "  ./update_bot.sh tv-binance-bot_deploy_v02.zip"
  exit 1
fi

# 若只給檔名，從 PROJECT_DIR 底下找
if [[ ! -f "$ZIP_FILE" ]]; then
  if [[ -f "$PROJECT_DIR/$ZIP_FILE" ]]; then
    ZIP_FILE="$PROJECT_DIR/$ZIP_FILE"
  fi
fi

if [[ ! -f "$ZIP_FILE" ]]; then
  echo "錯誤：找不到指定的 zip 檔案：$ZIP_FILE"
  exit 1
fi

if [[ ! -f "$PROJECT_DIR/docker-compose.yml" ]]; then
  echo "錯誤：在專案目錄中找不到 docker-compose.yml：$PROJECT_DIR"
  exit 1
fi

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="${BACKUP_ROOT}/tvbot_backup_${TIMESTAMP}"

echo "=== 開始更新 tv-binance-bot ==="
echo "專案目錄：   $PROJECT_DIR"
echo "備份目錄：   $BACKUP_DIR"
echo "新版本 ZIP： $ZIP_FILE"
echo

########################################
# 1. 停止現有 Docker 服務
########################################
cd "$PROJECT_DIR"

echo ">>> 停止現有 Docker 服務..."
if command -v docker-compose >/dev/null 2>&1; then
  docker-compose down || true
else
  docker compose down || true
fi

########################################
# 2. 備份目前專案
########################################
echo ">>> 備份目前專案到：$BACKUP_DIR"
mkdir -p "$BACKUP_ROOT"
cp -r "$PROJECT_DIR" "$BACKUP_DIR"

########################################
# 3. 解壓新版本到暫存目錄
########################################
TMP_DIR="$(mktemp -d /tmp/tvbot_deploy_XXXXXX)"
echo ">>> 解壓新版本到暫存目錄：$TMP_DIR"
unzip -q "$ZIP_FILE" -d "$TMP_DIR"

# 你的 zip 目前結構就是 app/、data/、docker-compose.yml 在最外層
NEW_DIR="$TMP_DIR"

echo ">>> 新版本內容目錄：$NEW_DIR"
echo "    (預期包含 app/、data/、docker-compose.yml 等)"

########################################
# 4. 同步新版本檔案到正式專案
#    - 覆蓋 app/ 程式碼
#    - 覆蓋 docker-compose.yml（如果 zip 內有）
#    - 保留 data/ 資料庫與 update_bot.sh、自身 zip
########################################
echo ">>> 同步新版本檔案到正式專案（保留 data/ 與 update_bot.sh）..."

rsync -av \
  --exclude "data" \
  --exclude "data/**" \
  --exclude "update_bot.sh" \
  --exclude "tv-binance-bot_deploy_v*.zip" \
  "$NEW_DIR/" "$PROJECT_DIR/"

########################################
# 5. 重建並啟動 Docker 服務
########################################
cd "$PROJECT_DIR"
echo ">>> 重建並啟動 Docker 服務..."

if command -v docker-compose >/dev/null 2>&1; then
  docker-compose up -d --build
else
  docker compose up -d --build
fi

echo
echo ">>> Docker 服務已啟動，容器狀態："
if command -v docker-compose >/dev/null 2>&1; then
  docker-compose ps
else
  docker compose ps
fi

########################################
# 6. 清理暫存資料夾
########################################
echo ">>> 清理暫存目錄：$TMP_DIR"
rm -rf "$TMP_DIR"

echo
echo "=== 更新完成 ==="
echo "備份版本位於：$BACKUP_DIR"
echo "如需回滾，可執行（請依實際路徑與時間調整）："
echo "  rm -rf \"$PROJECT_DIR\""
echo "  cp -r \"$BACKUP_DIR\" \"$PROJECT_DIR\""
echo "  cd \"$PROJECT_DIR\" && docker compose up -d --build"
echo
echo "建議："
echo "  1. 立刻用 TradingView 或 curl 發測試 webhook，確認 bot 有收到。"
echo "  2. 查看 log：cd \"$PROJECT_DIR\" && docker compose logs -f"

