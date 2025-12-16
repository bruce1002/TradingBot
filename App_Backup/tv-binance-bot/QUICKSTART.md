# 快速啟動指南

## 步驟 1: 安裝依賴

```bash
pip install -r requirements.txt
```

或使用虛擬環境（建議）：

```bash
# 建立虛擬環境
python3 -m venv venv

# 啟動虛擬環境（macOS/Linux）
source venv/bin/activate

# 啟動虛擬環境（Windows）
# venv\Scripts\activate

# 安裝依賴
pip install -r requirements.txt
```

## 步驟 2: 設定環境變數

建立 `.env` 檔案：

```bash
touch .env
```

編輯 `.env` 檔案，填入以下內容：

```bash
# 幣安 API 金鑰（必填）
BINANCE_API_KEY=your_testnet_api_key_here
BINANCE_API_SECRET=your_testnet_api_secret_here
USE_TESTNET=1

# TradingView Webhook 設定（必填）
TRADINGVIEW_SECRET=your_webhook_secret_here

# Google OAuth 設定（Dashboard 使用，必填）
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
ADMIN_GOOGLE_EMAIL=your_admin_email@gmail.com
SESSION_SECRET_KEY=your_random_secret_key_here
```

### 取得必要的設定值：

1. **幣安測試網 API 金鑰**
   - 前往：https://testnet.binancefuture.com/
   - 註冊並申請 API Key 和 Secret

2. **Google OAuth 設定**
   - 前往：https://console.cloud.google.com/
   - 建立 OAuth 2.0 Client ID
   - 設定 Redirect URI: `http://localhost:8000/auth/callback`

3. **SESSION_SECRET_KEY**
   - 可以是任何隨機字串
   - 例如：`openssl rand -hex 32`

## 步驟 3: 啟動應用程式

### 方式 1: 直接執行（推薦）

```bash
python main.py
```

### 方式 2: 使用 uvicorn

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

## 步驟 4: 訪問應用程式

應用程式啟動後，可以訪問：

- **Dashboard**: http://localhost:8000/dashboard
- **API 文件**: http://localhost:8000/docs
- **健康檢查**: http://localhost:8000/health
- **Webhook 端點**: http://localhost:8000/webhook/tradingview

## 步驟 5: 登入 Dashboard

1. 訪問 http://localhost:8000/dashboard
2. 會被導向 Google 登入頁面
3. 使用 `.env` 中設定的 `ADMIN_GOOGLE_EMAIL` 帳號登入
4. 登入成功後即可看到倉位 Dashboard

## 常見問題

### 1. 資料庫結構變更

如果資料表結構有變更，刪除舊的資料庫檔案即可：

```bash
rm trading_bot.db
```

重新啟動應用程式時會自動建立新的資料表。

### 2. 端口被占用

如果 8000 端口被占用，可以更改端口：

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8080
```

### 3. 環境變數未載入

確認 `.env` 檔案在專案根目錄，且名稱正確（不要有空格或特殊字元）。

## 開發模式

使用 `--reload` 參數可以啟用自動重新載入（當程式碼變更時自動重啟）：

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

