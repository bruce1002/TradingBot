# Semi-Auto Trading Mode Implementation

## Overview

This feature adds a **semi-auto trading mode** that queues trading signals for user approval before execution. When a bot is set to `semi-auto` mode, incoming TradingView signals are queued in the `pending_orders` table and wait for user approval or rejection.

## Features

### 1. Trading Modes

Each Bot can be configured with one of three trading modes:

- **`auto`** (default): Signals are executed immediately when received (existing behavior)
- **`semi-auto`**: Signals are queued and require user approval before execution
- **`manual`**: Signals are ignored/not processed (for manual trading only)

### 2. Database Changes

#### New Field: `bot_configs.trading_mode`
- Type: `VARCHAR(20)`
- Default: `'auto'`
- Values: `'auto'`, `'semi-auto'`, `'manual'`

#### New Table: `pending_orders`
Stores queued signals waiting for approval:

- `id`: Primary key
- `bot_id`: Reference to bot_configs
- `tv_signal_log_id`: Reference to tv_signal_logs
- `symbol`: Trading symbol (e.g., BTCUSDT)
- `side`: Trade direction (BUY/SELL or LONG/SHORT)
- `qty`: Calculated quantity (if available)
- `position_size`: Target position size (for position-based mode)
- `calculated_qty`: Pre-calculated quantity for execution
- `calculated_side`: Pre-calculated side for execution
- `is_position_based`: Whether this is position-based mode
- `status`: PENDING, APPROVED, REJECTED, EXECUTED, FAILED
- `approved_at`, `rejected_at`, `executed_at`: Timestamps
- `error_message`: Error message if execution fails
- `position_id`: Created Position ID if execution succeeds

### 3. Migration

Run the migration script to add the new field and table:

```bash
python migrate_add_semi_auto_trading.py
```

### 4. API Endpoints

#### GET `/pending-orders`
List all pending orders (with optional filters).

**Query Parameters:**
- `status` (optional): Filter by status (PENDING, APPROVED, REJECTED, EXECUTED, FAILED)
- `bot_id` (optional): Filter by bot ID

**Response:**
```json
[
  {
    "id": 1,
    "bot_id": 5,
    "tv_signal_log_id": 123,
    "symbol": "BTCUSDT",
    "side": "BUY",
    "qty": 0.01,
    "position_size": null,
    "calculated_qty": 0.01,
    "calculated_side": "BUY",
    "is_position_based": false,
    "status": "PENDING",
    "approved_at": null,
    "rejected_at": null,
    "executed_at": null,
    "error_message": null,
    "position_id": null,
    "created_at": "2024-01-01T12:00:00Z"
  }
]
```

#### POST `/pending-orders/{order_id}/approve`
Approve and execute a pending order.

**Response:**
```json
{
  "success": true,
  "message": "訂單已批准並執行",
  "pending_order_id": 1,
  "position_id": 456
}
```

#### POST `/pending-orders/{order_id}/reject`
Reject a pending order (marks it as REJECTED, no execution).

**Response:**
```json
{
  "success": true,
  "message": "訂單已拒絕",
  "pending_order_id": 1
}
```

### 5. Bot Configuration

#### Creating a Bot with Semi-Auto Mode

When creating a bot via API:

```json
{
  "name": "My Bot",
  "bot_key": "my_bot_v1",
  "trading_mode": "semi-auto",
  "symbol": "BTCUSDT",
  "qty": 0.01,
  "leverage": 20,
  ...
}
```

#### Updating a Bot's Trading Mode

```json
PUT /bots/{bot_id}
{
  "trading_mode": "semi-auto"
}
```

### 6. Webhook Behavior

When a TradingView webhook is received:

1. **Auto mode**: Signal is processed immediately (existing behavior)
2. **Semi-auto mode**: Signal is queued in `pending_orders` table with status `PENDING`
3. **Manual mode**: Signal is logged but not processed

### 7. Workflow

1. TradingView sends signal → Webhook receives it
2. System checks bot's `trading_mode`:
   - If `auto`: Execute immediately
   - If `semi-auto`: Create `PendingOrder` with status `PENDING`
   - If `manual`: Skip processing
3. User views pending orders via API or dashboard
4. User approves or rejects:
   - **Approve**: Order is executed, status → `EXECUTED` (or `FAILED` if error)
   - **Reject**: Status → `REJECTED`

## Usage Example

### Step 1: Set Bot to Semi-Auto Mode

```bash
curl -X PUT http://localhost:8000/bots/1 \
  -H "Content-Type: application/json" \
  -d '{
    "trading_mode": "semi-auto"
  }'
```

### Step 2: Receive Signal (TradingView Webhook)

TradingView sends signal → System queues it in `pending_orders`

### Step 3: View Pending Orders

```bash
curl http://localhost:8000/pending-orders?status=PENDING
```

### Step 4: Approve Order

```bash
curl -X POST http://localhost:8000/pending-orders/1/approve
```

### Step 5: Order Executed

Order status changes to `EXECUTED`, and a new `Position` is created.

## Limitations

1. **Position-Based Mode**: The approve endpoint currently only fully supports order-based mode. Position-based mode approval execution is partially implemented and may need enhancement for full support.

2. **Dashboard UI**: Basic API endpoints are implemented, but dashboard UI enhancements to display and manage pending orders can be added as a follow-up.

## Backward Compatibility

- All existing bots default to `trading_mode = 'auto'`, preserving existing behavior
- The migration script sets default values for existing records
- No breaking changes to existing functionality

## Testing

1. Run migration: `python migrate_add_semi_auto_trading.py`
2. Create or update a bot with `trading_mode: "semi-auto"`
3. Send a test TradingView webhook signal
4. Verify the signal is queued in `pending_orders`
5. Approve the order via API
6. Verify the position is created and order status is `EXECUTED`

