# 快速啟動指南

## 🚀 如何啟動應用程式並開啟 UI

### 步驟 1: 確認環境變數設定

確保你已經建立 `.env` 檔案並填入必要的設定值：

```bash
# 幣安 API（必填）
BINANCE_API_KEY=your_testnet_api_key_here
BINANCE_API_SECRET=your_testnet_api_secret_here
USE_TESTNET=1

# TradingView Webhook（必填）
TRADINGVIEW_SECRET=your_webhook_secret_here

# Google OAuth（Dashboard 使用，必填）
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
ADMIN_GOOGLE_EMAIL=your_admin_email@gmail.com
SESSION_SECRET_KEY=your_random_secret_key_here

# Dynamic Stop 設定（選填，有預設值）
# 這些值會作為 Dashboard UI 中 "PnL% threshold", "Lock ratio", "Base SL" 的預設值
DYN_TRAILING_ENABLED=1
DYN_PROFIT_THRESHOLD_PCT=1.0      # PnL% threshold (%)
DYN_LOCK_RATIO_DEFAULT=0.666      # Lock ratio (例如 0.666 代表 2/3，範圍 0~1)
DYN_BASE_SL_PCT=0.5               # Base SL (%)
```

### 步驟 2: 啟動應用程式

**方式 1：使用 Python 直接執行（推薦）**

```bash
# 確保虛擬環境已啟動
source .venv/bin/activate  # macOS/Linux
# 或 .venv\Scripts\activate  # Windows

# 啟動應用程式
python main.py
```

**方式 2：使用 uvicorn**

```bash
source .venv/bin/activate
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 步驟 3: 訪問 Web UI Dashboard

1. **開啟瀏覽器**，訪問：
   ```
   http://localhost:8000/dashboard
   ```

2. **登入流程**：
   - 系統會自動導向 Google 登入頁面
   - 使用設定在 `ADMIN_GOOGLE_EMAIL` 的 Google 帳號登入
   - 登入成功後即可看到倉位 Dashboard

### 步驟 4: Dashboard 功能

Dashboard 提供以下功能：

- ✅ **查看倉位**：顯示最近 100 筆倉位記錄
- ✅ **關倉操作**：點擊「關倉」按鈕即可關閉 OPEN 狀態的倉位
- ✅ **設定追蹤停損**：點擊「設定追蹤停損」按鈕，輸入回調百分比

### 其他有用的端點

- **API 文件（Swagger UI）**：http://localhost:8000/docs
- **健康檢查**：http://localhost:8000/health
- **根路徑**：http://localhost:8000/

## ⚠️ 常見問題

### Q: 無法訪問 Dashboard？

**A:** 確認以下事項：
1. 應用程式是否正常啟動（查看終端機是否有錯誤訊息）
2. 是否有設定 Google OAuth（`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`）
3. Redirect URI 是否設定正確（`http://localhost:8000/auth/callback`）

### Q: 登入後顯示「未授權」？

**A:** 確認：
1. 登入的 Google 帳號 email 是否與 `.env` 中的 `ADMIN_GOOGLE_EMAIL` 一致
2. 大小寫必須完全一致

### Q: 資料庫錯誤？

**A:** 如果遇到資料庫結構錯誤，可以刪除舊的資料庫檔案：
```bash
rm trading_bot.db
```
重新啟動應用程式時會自動建立新的資料表。

## 📝 完整的啟動命令範例

```bash
# 1. 進入專案目錄
cd /Users/bruce/Documents/Invest/tv-binance-bot

# 2. 啟動虛擬環境
source .venv/bin/activate

# 3. 確認依賴已安裝
pip install -r requirements.txt

# 4. 確認 .env 檔案已設定
cat .env  # 檢查環境變數

# 5. 啟動應用程式
python main.py

# 6. 在瀏覽器開啟
# http://localhost:8000/dashboard
```

啟動成功後，終端機會顯示類似以下的訊息：
```
資料庫初始化完成
幣安客戶端初始化成功
Dynamic Stop 設定:
  DYN_TRAILING_ENABLED: True
  DYN_PROFIT_THRESHOLD_PCT: 1.0%
  DYN_LOCK_RATIO_DEFAULT: 0.5
  DYN_BASE_SL_PCT: 3.0%
追蹤停損背景任務已在啟動事件中建立
追蹤停損背景任務已啟動
INFO:     Started server process
INFO:     Uvicorn running on http://0.0.0.0:8000
```

