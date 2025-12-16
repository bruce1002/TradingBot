# TradingView Binance Bot

一個使用 FastAPI + SQLAlchemy + python-binance 建立的交易機器人，可以接收 TradingView webhook 訊號並自動在幣安期貨測試網下單，同時提供完整的倉位管理和追蹤停損功能。

## 📋 目錄

- [專案概述](#專案概述)
- [功能特色](#功能特色)
- [技術架構](#技術架構)
- [專案結構](#專案結構)
- [快速開始](#快速開始)
- [程式碼說明](#程式碼說明)
- [API 端點](#api-端點)
- [Dashboard 使用](#dashboard-使用)
- [設定說明](#設定說明)
- [注意事項](#注意事項)

## 📖 專案概述

本專案是一個自動化交易機器人，主要功能包括：

1. **接收 TradingView Webhook**：自動接收來自 TradingView 的交易訊號
2. **自動下單**：在幣安期貨測試網自動執行交易
3. **倉位管理**：完整記錄所有倉位資訊，包括進場價格、平倉價格、平倉原因等
4. **追蹤停損**：自動監控倉位價格，當觸發追蹤停損條件時自動平倉
5. **Web Dashboard**：提供網頁介面查看和管理倉位
6. **風險控制**：內建交易對白名單、杠桿限制、數量限制等風控機制

## ✨ 功能特色

### 核心功能

- ✅ **接收 TradingView Webhook 訊號**：自動處理交易訊號並下單
- ✅ **自動下單到幣安期貨測試網**：支援市價單和限價單
- ✅ **完整的倉位記錄**：所有交易記錄到 SQLite 資料庫
- ✅ **追蹤停損功能**：支援 LONG 和 SHORT 倉位的自動追蹤停損
- ✅ **RESTful API**：提供完整的 API 介面查詢和管理倉位
- ✅ **Web Dashboard**：視覺化的倉位管理介面
- ✅ **Google OAuth 認證**：安全的登入機制
- ✅ **風險控制**：交易對白名單、杠桿限制、數量限制

### 追蹤停損

- **LONG 倉位**：追蹤最高價格，當價格從最高點回調超過設定百分比時觸發停損
- **SHORT 倉位**：追蹤最低價格，當價格從最低點反彈超過設定百分比時觸發停損
- **自動執行**：背景任務每 5 秒檢查一次所有開啟追蹤停損的倉位

### 資料記錄

- 進場價格、平倉價格
- 平倉原因（trailing_stop / manual_close / error）
- 倉位狀態（OPEN / CLOSING / CLOSED / ERROR）
- 完整的時間戳記（使用 UTC 時區）

## 🏗️ 技術架構

### 技術棧

- **Web Framework**: FastAPI
- **Database ORM**: SQLAlchemy
- **Database**: SQLite (開發環境)
- **Trading API**: python-binance
- **Template Engine**: Jinja2
- **Authentication**: Google OAuth 2.0 (Authlib)
- **Session Management**: FastAPI SessionMiddleware

### 架構設計

```
┌─────────────────┐
│  TradingView    │
│     Webhook     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   FastAPI       │
│   (main.py)     │
│  ┌───────────┐  │
│  │ Webhook   │  │
│  │ Handler   │  │
│  └─────┬─────┘  │
│        │        │
│  ┌─────▼─────┐  │
│  │ Binance   │  │
│  │ Client    │  │
│  └─────┬─────┘  │
│        │        │
│  ┌─────▼─────┐  │
│  │ Database  │  │
│  │ (SQLite)  │  │
│  └───────────┘  │
│                 │
│  ┌───────────┐  │
│  │ Trailing  │  │
│  │ Stop      │  │
│  │ Worker    │  │
│  └───────────┘  │
└─────────────────┘
```

## 📁 專案結構

```
tv-binance-bot/
├── main.py                  # FastAPI 應用程式入口
│   ├── Webhook 處理        # 接收 TradingView 訊號
│   ├── Dashboard 路由      # 網頁介面
│   ├── Google OAuth        # 認證機制
│   ├── 追蹤停損背景任務    # 自動檢查倉位
│   └── API 端點            # RESTful API
├── models.py                # SQLAlchemy 資料模型
│   └── Position             # 倉位資料模型
├── db.py                    # 資料庫初始化
│   ├── SQLite 連線設定
│   └── Session 管理
├── binance_client.py        # 幣安 API 客戶端封裝
│   ├── get_client()         # 取得 Client 實例
│   ├── get_mark_price()     # 取得標記價格
│   ├── open_futures_market_order()  # 開倉
│   └── close_futures_position()     # 平倉
├── templates/
│   └── dashboard.html       # Dashboard 網頁模板
├── requirements.txt         # Python 套件依賴
├── README.md               # 本檔案
└── QUICKSTART.md           # 快速啟動指南
```

## 🚀 快速開始

### 前置需求

- Python 3.8 或更高版本
- 幣安期貨測試網帳號（申請 API 金鑰）
- Google Cloud Console 帳號（申請 OAuth 2.0 Client ID）

### 步驟 1: 安裝依賴

```bash
# 建立虛擬環境（建議）
python3 -m venv venv
source venv/bin/activate  # macOS/Linux
# 或 venv\Scripts\activate  # Windows

# 安裝套件
pip install -r requirements.txt
```

### 步驟 2: 設定環境變數

建立 `.env` 檔案：

```bash
touch .env
```

編輯 `.env` 檔案，填入以下設定：

```bash
# ==================== 幣安 API（必填）====================
BINANCE_API_KEY=your_testnet_api_key_here
BINANCE_API_SECRET=your_testnet_api_secret_here
USE_TESTNET=1

# ==================== TradingView Webhook（必填）====================
TRADINGVIEW_SECRET=your_webhook_secret_here

# ==================== Google OAuth（Dashboard 使用，必填）====================
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
ADMIN_GOOGLE_EMAIL=your_admin_email@gmail.com
SESSION_SECRET_KEY=your_random_secret_key_here
```

#### 取得必要的設定值

**1. 幣安測試網 API 金鑰**
- 前往：https://testnet.binancefuture.com/
- 註冊並登入
- 前往 API Management 建立新的 API Key
- 複製 API Key 和 Secret 到 `.env`

**2. Google OAuth 設定**
- 前往：https://console.cloud.google.com/
- 建立新專案或選擇現有專案
- 啟用 **Google Identity Platform** 或 **Google+ API**
- 前往「憑證」→「建立憑證」→「OAuth 2.0 用戶端 ID」
- 應用程式類型選擇「網頁應用程式」
- 在「已授權的重新導向 URI」中新增：
  - 開發環境：`http://localhost:8000/auth/callback`
  - 生產環境：`https://your-domain.com/auth/callback`
- 複製 Client ID 和 Client Secret 到 `.env`

**3. SESSION_SECRET_KEY**
- 可以是任何隨機字串
- 可以使用：`openssl rand -hex 32` 產生
- 用於簽署 session cookie

### 步驟 3: 啟動應用程式

**方式 1：直接執行（推薦）**

```bash
python main.py
```

**方式 2：使用 uvicorn**

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 步驟 4: 訪問應用程式

啟動成功後，可以訪問：

- **Dashboard**: http://localhost:8000/dashboard
- **API 文件**: http://localhost:8000/docs
- **健康檢查**: http://localhost:8000/health
- **Webhook 端點**: http://localhost:8000/webhook/tradingview

## 💻 程式碼說明

### 主要檔案

#### 1. `main.py` - FastAPI 應用程式入口

主要功能：

- **Webhook 處理** (`/webhook/tradingview`)：接收 TradingView 訊號並下單
- **Dashboard** (`/dashboard`)：網頁介面顯示倉位
- **Google OAuth** (`/auth/login`, `/auth/callback`)：使用者認證
- **管理 API** (`/positions`, `/positions/{id}/close`, `/positions/{id}/trailing`)：倉位管理
- **追蹤停損背景任務** (`trailing_stop_worker`)：每 5 秒檢查倉位

關鍵函式：

```python
# 追蹤停損背景任務
async def trailing_stop_worker()
async def check_trailing_stop(position, db)

# 關倉價格取得
def get_exit_price_from_order(close_order, symbol)

# 認證
async def require_admin_user(request)
```

#### 2. `models.py` - 資料模型

`Position` 模型包含的欄位：

- 基本資訊：`id`, `symbol`, `side`, `qty`, `entry_price`
- 狀態：`status` (OPEN/CLOSING/CLOSED/ERROR)
- 訂單追蹤：`binance_order_id`, `client_order_id`
- 追蹤停損：`highest_price`, `trail_callback`
- 平倉資訊：`exit_price`, `exit_reason`
- 時間戳記：`created_at`, `closed_at`

#### 3. `binance_client.py` - 幣安 API 封裝

主要函式：

- `get_client()`: 取得幣安 Client 實例（支援測試網）
- `get_mark_price(symbol)`: 取得期貨標記價格
- `open_futures_market_order()`: 開倉（自動設定杠桿）
- `close_futures_position()`: 平倉（根據倉位方向自動決定下單方向）

#### 4. `db.py` - 資料庫初始化

- SQLite 資料庫連線設定
- Session 管理（使用 `SessionLocal`）
- 自動建立資料表（透過 `init_db()`）

### 追蹤停損邏輯

#### LONG 倉位

1. 記錄最高價格到 `highest_price`
2. 計算觸發價格：`trigger_price = highest_price * (1 - trail_callback)`
3. 當 `current_price <= trigger_price` 時觸發停損

範例：
- 最高價：$100
- 回調比例：2% (0.02)
- 觸發價格：$100 × (1 - 0.02) = $98
- 當價格跌到 $98 或更低時觸發停損

#### SHORT 倉位

1. 使用 `highest_price` 欄位記錄最低價格
2. 計算觸發價格：`trigger_price = lowest_price * (1 + trail_callback)`
3. 當 `current_price >= trigger_price` 時觸發停損

範例：
- 最低價：$100
- 反彈比例：2% (0.02)
- 觸發價格：$100 × (1 + 0.02) = $102
- 當價格漲到 $102 或更高時觸發停損

## 🔌 API 端點

### Webhook 端點

#### `POST /webhook/tradingview`

接收 TradingView webhook 並下單。

**請求格式：**
```json
{
  "secret": "your_webhook_secret",
  "symbol": "BTCUSDT",
  "side": "BUY",
  "qty": 0.001,
  "leverage": 10,
  "trailing_callback_percent": 2.0,
  "tag": "strategy1"
}
```

**回應格式：**
```json
{
  "success": true,
  "message": "訂單建立成功",
  "position_id": 1,
  "binance_order": {...}
}
```

### 管理 API（需要 Google OAuth 登入）

#### `GET /dashboard`

顯示倉位 Dashboard（HTML 頁面）。

#### `GET /positions`

查詢所有倉位記錄。

**查詢參數：**
- `symbol` (optional): 交易對篩選
- `status` (optional): 狀態篩選

**回應格式：**
```json
[
  {
    "id": 1,
    "symbol": "BTCUSDT",
    "side": "LONG",
    "qty": 0.001,
    "entry_price": 50000.0,
    "exit_price": 51000.0,
    "status": "CLOSED",
    "exit_reason": "trailing_stop",
    ...
  }
]
```

#### `POST /positions/{pos_id}/close`

手動關閉倉位。

**回應格式：**
```json
{
  "success": true,
  "message": "倉位已成功關閉",
  "position_id": 1,
  "closed_at": "2024-01-01T12:00:00Z",
  "exit_price": 51000.0,
  "exit_reason": "manual_close"
}
```

#### `POST /positions/{pos_id}/trailing`

設定追蹤停損。

**請求格式：**
```json
{
  "trailing_callback_percent": 2.0,
  "activation_profit_percent": 1.0
}
```

### 認證端點

#### `GET /auth/login`

導向 Google OAuth 登入頁面。

#### `GET /auth/callback`

Google OAuth 回調處理（自動處理，使用者不需要直接訪問）。

#### `GET /auth/logout`

登出並清除 session。

### 其他端點

#### `GET /`

根路徑，健康檢查。

#### `GET /health`

健康檢查，確認幣安連線狀態。

#### `GET /docs`

Swagger UI API 文件。

## 🖥️ Dashboard 使用

### 登入 Dashboard

1. 訪問 `http://localhost:8000/dashboard`
2. 系統會自動導向 Google 登入頁面
3. 使用設定在 `ADMIN_GOOGLE_EMAIL` 的 Google 帳號登入
4. 登入成功後即可看到倉位 Dashboard

### Dashboard 功能

#### 查看倉位

- 顯示最近 100 筆倉位記錄
- 包含所有欄位：ID、交易對、方向、數量、進場價、平倉價、狀態、平倉原因等
- 支援狀態標籤顏色區分（OPEN/CLOSED/CLOSING/ERROR）

#### 關倉操作

- 點擊「關倉」按鈕即可關閉 OPEN 狀態的倉位
- 系統會自動從幣安取得實際平倉價格
- 平倉後會更新 `exit_price` 和 `exit_reason = "manual_close"`

#### 設定追蹤停損

- 點擊「設定追蹤停損」按鈕
- 輸入追蹤停損回調百分比（例如：2.0 代表 2%）
- 可選輸入啟動獲利百分比（例如：1.0 代表先賺 1% 再啟動追蹤）
- 設定成功後，背景任務會自動監控倉位

## ⚙️ 設定說明

### 環境變數

| 變數名稱 | 說明 | 是否必填 | 範例 |
|---------|------|---------|------|
| `BINANCE_API_KEY` | 幣安 API Key | ✅ 是 | `abc123...` |
| `BINANCE_API_SECRET` | 幣安 API Secret | ✅ 是 | `def456...` |
| `USE_TESTNET` | 是否使用測試網 | ✅ 是 | `1` (測試網) 或 `0` (正式網) |
| `TRADINGVIEW_SECRET` | TradingView Webhook 密鑰 | ✅ 是 | `my_secret_123` |
| `GOOGLE_CLIENT_ID` | Google OAuth Client ID | ✅ 是 | `123.apps.googleusercontent.com` |
| `GOOGLE_CLIENT_SECRET` | Google OAuth Client Secret | ✅ 是 | `GOCSPX-...` |
| `ADMIN_GOOGLE_EMAIL` | 管理員 Google Email | ✅ 是 | `admin@gmail.com` |
| `SESSION_SECRET_KEY` | Session 簽署密鑰 | ✅ 是 | `random_secret_key` |

### 風控設定（在 main.py 中）

```python
# 允許交易的交易對列表
ALLOWED_SYMBOLS = {"BTCUSDT", "ETHUSDT"}

# 每個交易對的最大杠桿倍數
MAX_LEVERAGE_PER_SYMBOL = {
    "BTCUSDT": 20,
    "ETHUSDT": 10
}

# 每個交易對的最大交易數量
MAX_QTY_PER_SYMBOL = {
    "BTCUSDT": 0.05,
    "ETHUSDT": 1
}
```

## 📝 TradingView Webhook 設定

### 1. 建立 TradingView Alert

在 TradingView 圖表上建立 Alert：

1. 設定交易策略條件
2. 在「通知」中勾選「Webhook URL」
3. 設定 Webhook URL：`http://your-server-ip:8000/webhook/tradingview`
4. 在「訊息」欄位中使用 JSON 格式

### 2. Webhook 訊息格式

**基本格式：**
```json
{
  "secret": "your_webhook_secret",
  "symbol": "BTCUSDT",
  "side": "BUY",
  "qty": 0.001
}
```

**完整格式（包含追蹤停損）：**
```json
{
  "secret": "your_webhook_secret",
  "symbol": "BTCUSDT",
  "side": "BUY",
  "qty": 0.001,
  "leverage": 10,
  "trailing_callback_percent": 2.0,
  "tag": "strategy1"
}
```

**使用 TradingView 變數：**
```json
{
  "secret": "your_webhook_secret",
  "symbol": "{{ticker}}",
  "side": "{{strategy.order.action}}",
  "qty": {{strategy.order.contracts}},
  "leverage": 10,
  "trailing_callback_percent": 2.0
}
```

### 3. 欄位說明

- `secret`: Webhook 密鑰（必須等於環境變數 `TRADINGVIEW_SECRET`）
- `symbol`: 交易對（例如：BTCUSDT）
- `side`: 交易方向（BUY 或 SELL）
- `qty`: 交易數量
- `leverage`: 杠桿倍數（選填，預設 1）
- `trailing_callback_percent`: 追蹤停損回調百分比（選填，例如 2.0 代表 2%）
- `tag`: 標籤（選填，用於標記訂單來源）

## ⚠️ 注意事項

### 安全性

1. **API 金鑰保護**：請勿將 `.env` 檔案提交到版本控制系統
2. **Session Secret**：生產環境請使用強隨機字串作為 `SESSION_SECRET_KEY`
3. **HTTPS**：生產環境建議使用 HTTPS 保護 API 端點
4. **權限控制**：只有 `ADMIN_GOOGLE_EMAIL` 設定的 email 可以登入 Dashboard

### 資料庫

1. **SQLite 限制**：目前使用 SQLite 作為開發資料庫，不適合高並發環境
2. **資料庫遷移**：如果模型結構變更，可以刪除 `trading_bot.db` 讓系統重新建立
3. **生產環境**：建議使用 PostgreSQL 或其他正式資料庫系統

### 測試網 vs 正式網

1. **預設使用測試網**：`USE_TESTNET=1` 時使用幣安期貨測試網
2. **正式網風險**：切換到正式網（`USE_TESTNET=0`）前請確認所有設定正確
3. **資金安全**：正式網會使用真實資金，請務必謹慎

### 風險控制

1. **風控限制**：目前內建交易對白名單、杠桿限制、數量限制
2. **建議增強**：可以考慮實作以下風控機制：
   - 每日交易次數限制
   - 最大持倉金額限制
   - 單筆交易金額限制
   - 滑點控制

### 錯誤處理

1. **訂單失敗**：所有訂單失敗都會記錄在資料庫中，狀態設為 `ERROR`
2. **日誌記錄**：所有操作都有完整的日誌記錄，方便排查問題
3. **追蹤停損錯誤**：單一倉位錯誤不會影響其他倉位的檢查

## 🔧 常見問題

### Q: 如何重置資料庫？

A: 刪除 `trading_bot.db` 檔案，重新啟動應用程式會自動建立新的資料表。

```bash
rm trading_bot.db
python main.py
```

### Q: Dashboard 無法登入？

A: 確認以下設定：
1. `GOOGLE_CLIENT_ID` 和 `GOOGLE_CLIENT_SECRET` 是否正確
2. Redirect URI 是否設定為 `http://localhost:8000/auth/callback`
3. `ADMIN_GOOGLE_EMAIL` 是否為登入的 Google 帳號 email

### Q: 追蹤停損沒有觸發？

A: 檢查以下項目：
1. 倉位狀態是否為 `OPEN`
2. `trail_callback` 是否有設定（不為 None）
3. 背景任務是否正常運行（查看日誌）
4. 價格是否達到觸發條件

### Q: Webhook 收到 401 錯誤？

A: 確認 `TRADINGVIEW_SECRET` 在 `.env` 中的值與 TradingView Alert 中 `secret` 欄位的值一致。

### Q: 如何查看日誌？

A: 日誌會輸出到控制台。可以設定日誌級別：

```python
# 在 main.py 中
logging.basicConfig(level=logging.DEBUG)  # 改為 DEBUG 可以看到更多資訊
```

## 📚 相關資源

- [FastAPI 文件](https://fastapi.tiangolo.com/)
- [SQLAlchemy 文件](https://docs.sqlalchemy.org/)
- [python-binance 文件](https://python-binance.readthedocs.io/)
- [幣安期貨測試網](https://testnet.binancefuture.com/)
- [Google Cloud Console](https://console.cloud.google.com/)
- [TradingView Alert 設定](https://www.tradingview.com/support/solutions/43000529348-webhooks/)

## 📄 授權

MIT License

## 🤝 貢獻

歡迎提交 Issue 和 Pull Request！

## 📞 支援

如有問題或建議，請開啟 Issue 或聯繫專案維護者。
