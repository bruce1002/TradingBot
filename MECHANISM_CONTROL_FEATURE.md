# Stop-Loss/Take-Profit Mechanism Control Feature

## Overview

This feature allows you to enable/disable stop-loss and take-profit mechanisms **per position**. You can now control which mechanism is active for each position independently.

## Two Mechanisms

1. **Bot Internal Stop-Loss** (`bot_stop_loss_enabled`)
   - Dynamic stop-loss (trailing stop)
   - Base stop-loss (fixed percentage)
   - Controlled by the background worker (`trailing_stop_worker`)

2. **TradingView Signal Close** (`tv_signal_close_enabled`)
   - TradingView can send `position_size=0` signals to close positions
   - Processed by the webhook handler

## Default Behavior

Both mechanisms are **enabled by default** (`True`) for all new positions. This maintains backward compatibility - existing behavior is preserved.

## Usage Examples

### Example 1: Only Use Bot Stop-Loss
Disable TradingView signal close, rely only on bot's internal stop-loss:

```bash
curl -X PATCH http://localhost:8000/positions/123/mechanism-config \
  -H "Content-Type: application/json" \
  -d '{
    "bot_stop_loss_enabled": true,
    "tv_signal_close_enabled": false
  }'
```

**Result**: Bot will close position based on stop-loss rules, but will ignore TradingView `position_size=0` signals.

### Example 2: Only Use TradingView Signals
Disable bot stop-loss, rely only on TradingView signals:

```bash
curl -X PATCH http://localhost:8000/positions/123/mechanism-config \
  -H "Content-Type: application/json" \
  -d '{
    "bot_stop_loss_enabled": false,
    "tv_signal_close_enabled": true
  }'
```

**Result**: Bot will ignore stop-loss rules, but will close position when TradingView sends `position_size=0`.

### Example 3: Disable Both (Manual Control Only)
Disable both mechanisms for manual control:

```bash
curl -X PATCH http://localhost:8000/positions/123/mechanism-config \
  -H "Content-Type: application/json" \
  -d '{
    "bot_stop_loss_enabled": false,
    "tv_signal_close_enabled": false
  }'
```

**Result**: Position will only close manually via API or dashboard.

## API Endpoint

### Update Mechanism Configuration

**Endpoint**: `PATCH /positions/{pos_id}/mechanism-config`

**Authentication**: Required (Admin user)

**Request Body**:
```json
{
  "bot_stop_loss_enabled": true,    // Optional: Enable/disable bot stop-loss
  "tv_signal_close_enabled": true   // Optional: Enable/disable TV signal close
}
```

**Response**: Updated `Position` object

**Example**:
```bash
curl -X PATCH http://localhost:8000/positions/123/mechanism-config \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "bot_stop_loss_enabled": false,
    "tv_signal_close_enabled": true
  }'
```

## Database Migration

Run the migration script to add the new columns to existing databases:

```bash
cd app/tv-binance-bot
python migrate_add_mechanism_flags.py
```

The migration script will:
- Add `bot_stop_loss_enabled` column (default: `True`)
- Add `tv_signal_close_enabled` column (default: `True`)
- Set all existing positions to `True` for both flags (backward compatible)

**Note**: If you're creating a new database, the columns will be created automatically when you start the application.

## Implementation Details

### Position Model Changes

Two new boolean fields added to `Position` model:
- `bot_stop_loss_enabled`: Default `True`
- `tv_signal_close_enabled`: Default `True`

### Trailing Stop Worker

The `trailing_stop_worker` now filters positions:
```python
positions = (
    db.query(Position)
    .filter(Position.status == "OPEN")
    .filter(Position.bot_stop_loss_enabled == True)  # Only check enabled positions
    .all()
)
```

### Webhook Handler

When processing `position_size=0` signals:
```python
if not current_position.tv_signal_close_enabled:
    # Skip closing if disabled
    logger.info("tv_signal_close_enabled=False, skipping close")
else:
    # Close position
    close_futures_position(...)
```

## Use Cases

1. **Strategy Testing**: Test different stop-loss strategies by enabling/disabling mechanisms
2. **Risk Management**: Disable bot stop-loss for certain positions that should only close via TradingView signals
3. **Manual Control**: Disable both mechanisms for positions you want to manage manually
4. **Hybrid Approach**: Use both mechanisms for redundancy (default behavior)

## Notes

- Only `OPEN` positions can have their mechanism flags updated
- Changes take effect immediately
- Flags are persisted in the database
- Both flags default to `True` for backward compatibility

