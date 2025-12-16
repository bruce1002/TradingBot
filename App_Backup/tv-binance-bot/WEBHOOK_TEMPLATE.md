# TradingView Webhook 訊息格式範本

## 基本格式

所有 webhook 請求都必須包含以下欄位：

### 必填欄位

- `secret`: Webhook 密鑰（用於驗證，必須與環境變數 `TRADINGVIEW_SECRET` 匹配）
- `symbol`: 交易對（例如：`BTCUSDT`, `ETHUSDT`）
- `side`: 交易方向（`BUY` 或 `SELL`）
- `qty`: 交易數量（數字）

### 識別欄位（二選一）

- `signal_key`: 新格式，對應 `TVSignalConfig.signal_key`（推薦）
- `bot_key`: 舊格式，對應 `BotConfig.bot_key`（兼容）

### 選填欄位

- `position_size`: 目標倉位大小（位置導向模式）
  - `> 0`: 目標為多倉
  - `< 0`: 目標為空倉
  - `= 0`: 目標為平倉
  - `null` 或未提供: 使用舊的訂單導向模式
- `time`: TradingView 傳來的時間字串（可選）
- `extra`: 彈性欄位，任意 JSON 物件（可選）

---

## 格式範本

### 1. 舊格式（訂單導向模式）- 向後兼容

**使用 `bot_key`（舊格式）：**

```json
{
  "secret": "your_tradingview_secret",
  "bot_key": "btc_short_v1",
  "symbol": "BTCUSDT",
  "side": "BUY",
  "qty": 0.01
}
```

**行為：**
- `BUY` → 開多倉（LONG）
- `SELL` → 開空倉（SHORT）
- 每次都會建立新倉位，不會檢查現有倉位

---

### 2. 新格式（位置導向模式）- 推薦

#### 2.1 使用 `signal_key`（推薦）

```json
{
  "secret": "your_tradingview_secret",
  "signal_key": "my_strategy_v1",
  "symbol": "BTCUSDT",
  "side": "BUY",
  "qty": 0.01,
  "position_size": 50.0
}
```

**行為：**
- 系統會查找所有 `enabled=True` 且 `signal_id` 匹配的 bots
- 根據 `position_size` 調整倉位到目標大小

#### 2.2 使用 `bot_key`（兼容）

```json
{
  "secret": "your_tradingview_secret",
  "bot_key": "btc_short_v1",
  "symbol": "BTCUSDT",
  "side": "BUY",
  "qty": 0.01,
  "position_size": 50.0
}
```

**行為：**
- 直接查找對應的 bot
- 根據 `position_size` 調整倉位到目標大小

---

## 位置導向模式範例

### 範例 1: 開多倉（從無倉位開始）

```json
{
  "secret": "your_tradingview_secret",
  "signal_key": "my_strategy_v1",
  "symbol": "BTCUSDT",
  "side": "BUY",
  "qty": 0.01,
  "position_size": 50.0
}
```

**結果：** 開新多倉，數量 = 50.0

---

### 範例 2: 平倉（關閉現有倉位）

```json
{
  "secret": "your_tradingview_secret",
  "signal_key": "my_strategy_v1",
  "symbol": "BTCUSDT",
  "side": "SELL",
  "qty": 0.01,
  "position_size": 0.0
}
```

**結果：** 
- 如果當前有多倉或空倉 → 關閉倉位
- 如果當前已無倉位 → 無操作（不會開新倉）

**這解決了動態停損先關閉的問題：**
- 動態停損已經關閉了倉位
- TradingView 後來發送 `position_size=0` 的 SELL 訊號
- 系統檢測到 `current_qty=0` 且 `target=0` → 無操作 ✅

---

### 範例 3: 反轉倉位（從多倉變空倉）

```json
{
  "secret": "your_tradingview_secret",
  "signal_key": "my_strategy_v1",
  "symbol": "BTCUSDT",
  "side": "SELL",
  "qty": 0.01,
  "position_size": -30.0
}
```

**結果：**
- 如果當前有多倉 → 先關閉多倉，再開新空倉（數量 = 30.0）
- 如果當前是空倉 → 調整空倉數量到 30.0（如果差異 > 10%）
- 如果當前無倉位 → 開新空倉（數量 = 30.0）

---

### 範例 4: 調整多倉數量

```json
{
  "secret": "your_tradingview_secret",
  "signal_key": "my_strategy_v1",
  "symbol": "BTCUSDT",
  "side": "BUY",
  "qty": 0.01,
  "position_size": 100.0
}
```

**結果：**
- 如果當前多倉 = 50.0 → 差異 = 50.0（> 10%）→ 重新開倉到 100.0
- 如果當前多倉 = 95.0 → 差異 = 5.0（< 10%）→ 跳過調整
- 如果當前多倉 = 100.0 → 無操作

---

## TradingView Alert 設定範本

### Pine Script 範例

```pinescript
//@version=5
strategy("My Strategy", overlay=true)

// 策略邏輯...
longCondition = ta.crossover(sma(close, 10), sma(close, 20))
shortCondition = ta.crossunder(sma(close, 10), sma(close, 20))
exitCondition = ta.crossunder(close, sma(close, 50))

// 計算目標倉位大小
var float targetPosition = 0.0

if longCondition
    targetPosition := 50.0  // 開多倉 50
    strategy.entry("Long", strategy.long, qty=50.0)
    
if shortCondition
    targetPosition := -30.0  // 開空倉 30
    strategy.entry("Short", strategy.short, qty=30.0)
    
if exitCondition
    targetPosition := 0.0  // 平倉
    strategy.close_all()

// Webhook 訊息（使用位置導向模式）
if longCondition or shortCondition or exitCondition
    side = longCondition ? "BUY" : shortCondition ? "SELL" : "SELL"
    qty = math.abs(targetPosition)
    
    webhook_url = "https://your-domain.com/webhook/tradingview"
    webhook_payload = json.encode({
        "secret": "your_tradingview_secret",
        "signal_key": "my_strategy_v1",
        "symbol": "BTCUSDT",
        "side": side,
        "qty": qty,
        "position_size": targetPosition  // 關鍵：傳遞目標倉位大小
    })
    
    request.security(webhook_url, timeframe.period, webhook_payload)
```

### TradingView Alert JSON 設定（手動設定）

**開多倉：**
```json
{
  "secret": "your_tradingview_secret",
  "signal_key": "my_strategy_v1",
  "symbol": "BTCUSDT",
  "side": "BUY",
  "qty": 0.01,
  "position_size": 50.0
}
```

**平倉：**
```json
{
  "secret": "your_tradingview_secret",
  "signal_key": "my_strategy_v1",
  "symbol": "BTCUSDT",
  "side": "SELL",
  "qty": 0.01,
  "position_size": 0.0
}
```

**開空倉：**
```json
{
  "secret": "your_tradingview_secret",
  "signal_key": "my_strategy_v1",
  "symbol": "BTCUSDT",
  "side": "SELL",
  "qty": 0.01,
  "position_size": -30.0
}
```

---

## cURL 測試範例

### 測試位置導向模式（開多倉）

```bash
curl -X POST http://localhost:8000/webhook/tradingview \
  -H "Content-Type: application/json" \
  -d '{
    "secret": "your_tradingview_secret",
    "signal_key": "my_strategy_v1",
    "symbol": "BTCUSDT",
    "side": "BUY",
    "qty": 0.01,
    "position_size": 50.0
  }'
```

### 測試位置導向模式（平倉）

```bash
curl -X POST http://localhost:8000/webhook/tradingview \
  -H "Content-Type: application/json" \
  -d '{
    "secret": "your_tradingview_secret",
    "signal_key": "my_strategy_v1",
    "symbol": "BTCUSDT",
    "side": "SELL",
    "qty": 0.01,
    "position_size": 0.0
  }'
```

### 測試舊格式（訂單導向，向後兼容）

```bash
curl -X POST http://localhost:8000/webhook/tradingview \
  -H "Content-Type: application/json" \
  -d '{
    "secret": "your_tradingview_secret",
    "bot_key": "btc_short_v1",
    "symbol": "BTCUSDT",
    "side": "BUY",
    "qty": 0.01
  }'
```

---

## 欄位說明

| 欄位 | 類型 | 必填 | 說明 |
|------|------|------|------|
| `secret` | string | ✅ | Webhook 密鑰，必須與 `TRADINGVIEW_SECRET` 環境變數匹配 |
| `signal_key` | string | ⚠️ | 新格式：對應 `TVSignalConfig.signal_key`（與 `bot_key` 二選一） |
| `bot_key` | string | ⚠️ | 舊格式：對應 `BotConfig.bot_key`（與 `signal_key` 二選一） |
| `symbol` | string | ✅ | 交易對，例如：`BTCUSDT`, `ETHUSDT` |
| `side` | string | ✅ | 交易方向：`BUY` 或 `SELL` |
| `qty` | float | ✅ | 交易數量（在位置導向模式中，此欄位主要用於記錄，實際數量由 `position_size` 決定） |
| `position_size` | float | ❌ | **位置導向模式關鍵欄位**：目標倉位大小<br>- `> 0`: 目標多倉<br>- `< 0`: 目標空倉<br>- `= 0`: 目標平倉<br>- `null` 或未提供: 使用舊的訂單導向模式 |
| `time` | string | ❌ | TradingView 傳來的時間字串（可選） |
| `extra` | object | ❌ | 彈性欄位，任意 JSON 物件（可選） |

---

## 重要提示

1. **優先使用 `signal_key`**：新格式更靈活，一個 signal 可以對應多個 bots
2. **`position_size` 是關鍵**：提供此欄位才會啟用位置導向模式
3. **向後兼容**：未提供 `position_size` 時，行為與舊版本完全相同
4. **`qty` 欄位**：在位置導向模式中，`qty` 主要用於記錄，實際操作數量由 `position_size` 決定
5. **`side` 欄位**：在位置導向模式中，`side` 主要用於記錄，實際操作由 `position_size` 的正負號決定

---

## 常見場景範例

### 場景 1: 標準進出場流程

**步驟 1 - 開多倉：**
```json
{
  "secret": "your_secret",
  "signal_key": "strategy_v1",
  "symbol": "BTCUSDT",
  "side": "BUY",
  "qty": 0.01,
  "position_size": 50.0
}
```

**步驟 2 - 平倉：**
```json
{
  "secret": "your_secret",
  "signal_key": "strategy_v1",
  "symbol": "BTCUSDT",
  "side": "SELL",
  "qty": 0.01,
  "position_size": 0.0
}
```

**結果：** 建立一個 LONG 倉位，之後平倉。如果動態停損先關閉了倉位，步驟 2 不會開新倉。

---

### 場景 2: 反轉倉位

**從多倉直接變空倉：**
```json
{
  "secret": "your_secret",
  "signal_key": "strategy_v1",
  "symbol": "BTCUSDT",
  "side": "SELL",
  "qty": 0.01,
  "position_size": -30.0
}
```

**結果：** 系統會先關閉現有的多倉，然後開新空倉。

---

### 場景 3: 調整倉位大小

**從 50 調整到 100：**
```json
{
  "secret": "your_secret",
  "signal_key": "strategy_v1",
  "symbol": "BTCUSDT",
  "side": "BUY",
  "qty": 0.01,
  "position_size": 100.0
}
```

**結果：** 如果當前多倉 = 50.0，差異 > 10%，系統會重新開倉到 100.0。

