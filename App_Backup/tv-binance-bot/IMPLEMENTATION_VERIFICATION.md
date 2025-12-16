# Implementation vs Requirements Verification Table

## Overview
This document verifies the current implementation against the requirements, with special attention to the dynamic stop issue reported.

---

## Requirements vs Implementation Comparison

| # | Requirement | Implementation Status | Code Location | Notes |
|---|------------|----------------------|---------------|-------|
| 1 | **Two Stop-Loss Modes (Base/Dynamic)** | ✅ Implemented | `compute_stop_state()` | Both modes are implemented |
| 2 | **Dynamic Mode Persistence** | ✅ Implemented | Lines 656-658, 824-826 | Uses `best` price (highest for LONG, lowest for SHORT), persists even if price retraces |
| 3 | **Global Settings Initially** | ✅ Implemented | Lines 585-640 | Positions start with NULL, use global settings dynamically |
| 4 | **Manual Settings Override** | ✅ Implemented | Lines 585-640 | Position override values take priority |
| 5 | **Dynamic Stop Price (LONG)** | ✅ Implemented | Line 750 | `entry + (best - entry) * lock_ratio` |
| 6 | **Dynamic Stop Price (SHORT)** | ✅ Implemented | Line 877 | `entry - (entry - best) * lock_ratio` |
| 7 | **Dynamic Trigger (LONG)** | ✅ Implemented | Line 1141 | `mark <= dyn_stop` |
| 8 | **Dynamic Trigger (SHORT)** | ✅ Implemented | Line 1278 | `mark >= dyn_stop` |
| 9 | **Base Stop Price (LONG)** | ✅ Implemented | Lines 668-675 | `entry - (margin * base_sl_pct / 100) / qty` |
| 10 | **Base Stop Price (SHORT)** | ✅ Implemented | Lines 797-804 | `entry + (margin * base_sl_pct / 100) / qty` |
| 11 | **Base Trigger (LONG)** | ✅ Implemented | Line 1148 | `mark <= base_stop_price` |
| 12 | **Base Trigger (SHORT)** | ✅ Implemented | Line 1285 | `mark >= base_stop_price` |
| 13 | **Non-Bot Positions Record Creation** | ✅ Implemented | Lines 429-450, 3949-3974 | Creates DB records when closed (manual/base/dynamic) |
| 14 | **Statistics Include Non-Bot Positions** | ✅ Implemented | `/bot-positions/stats` | Queries all CLOSED positions regardless of bot_id |

---

## Dynamic Stop Issue Analysis

### Current Implementation Flow

#### For Bot-Created Positions (`check_trailing_stop`):
1. **Trigger Detection** (Lines 1136-1142 for LONG, 1274-1279 for SHORT):
   ```python
   if stop_state.stop_mode == "dynamic":
       dyn_stop = stop_state.dynamic_stop_price
       if dyn_stop is not None:
           triggered = mark <= dyn_stop  # LONG
           # or
           triggered = mark >= dyn_stop  # SHORT
   ```

2. **Auto-Close Check** (Lines 1183-1188):
   ```python
   auto_close = TRAILING_CONFIG.auto_close_enabled if TRAILING_CONFIG.auto_close_enabled is not None else True
   if not auto_close:
       logger.info("...但 auto_close_enabled=False，不執行自動關倉")
       return  # ⚠️ EXITS WITHOUT CLOSING
   ```

3. **Close Execution** (Lines 1192-1213):
   - Calls `close_futures_position()`
   - Updates position status to CLOSED
   - Sets exit_reason to "dynamic_stop"

#### For Non-Bot Positions (`check_binance_non_bot_positions`):
1. **Trigger Detection** (Lines 363-370):
   ```python
   if stop_state.stop_mode == "dynamic":
       dyn_stop = stop_state.dynamic_stop_price
       if dyn_stop is not None:
           triggered = mark_price <= dyn_stop  # LONG
           # or
           triggered = mark_price >= dyn_stop  # SHORT
   ```

2. **Auto-Close Check** (Lines 405-410):
   ```python
   auto_close = TRAILING_CONFIG.auto_close_enabled if TRAILING_CONFIG.auto_close_enabled is not None else True
   if not auto_close:
       logger.info("...但 auto_close_enabled=False，不執行自動關倉")
       continue  # ⚠️ SKIPS CLOSING
   ```

3. **Close Execution** (Lines 414-450):
   - Calls `close_futures_position()`
   - Creates Position record in DB

---

## Potential Issues Causing Dynamic Stop Not Working

### Issue 1: `auto_close_enabled` Setting
**Location**: Lines 1183, 1313, 405  
**Problem**: If `TRAILING_CONFIG.auto_close_enabled = False`, dynamic stop will trigger but NOT close the position.  
**Check**: Verify `/settings/trailing` endpoint response - `auto_close_enabled` should be `true`.

### Issue 2: `dynamic_stop_price` is None
**Location**: Lines 1138, 1275, 364  
**Problem**: If `dynamic_stop_price` is `None`, trigger condition is never checked.  
**Possible Causes**:
- `lock_ratio` is `None` or `0`
- `best` price is `None` (for LONG: `highest_price` not initialized)
- Entry price is invalid

**Check**: Look for log messages:
- `"✓ 進入 Dynamic 模式: dynamic_stop_price=..."` (should appear)
- `"沒有停損價格！"` (indicates problem)

### Issue 3: Trigger Condition Not Met
**Location**: Lines 1141, 1278, 367, 369  
**Problem**: Price hasn't reached the stop level yet.  
**Check**: Log messages should show:
- `"觸發條件: mark <= dyn_stop (... = True/False)"`
- `"triggered=True"` should appear when condition is met

### Issue 4: Mode Not Set to "dynamic"
**Location**: `compute_stop_state()` Lines 740-760, 863-880  
**Problem**: Position might still be in "base" mode instead of "dynamic" mode.  
**Check**: Log messages should show:
- `"✓ 進入 Dynamic 模式"` (confirms dynamic mode activation)
- `"stop_mode=dynamic"` in position check logs

**Conditions for Dynamic Mode**:
- `profit_pct_based_on_best_for_threshold >= profit_threshold_pct`
- `lock_ratio` is not `None` and `> 0`
- `trailing_enabled` is `True`

### Issue 5: Exception During Close
**Location**: Lines 1215-1219, 431-434  
**Problem**: Close order fails, position status set to ERROR instead of CLOSED.  
**Check**: Look for error logs:
- `"關閉倉位 ... 失敗: ..."`
- Position status becomes "ERROR"

---

## Diagnostic Checklist

When dynamic stop is not working, check:

- [ ] **Auto-Close Enabled**: `GET /settings/trailing` → `auto_close_enabled` should be `true`
- [ ] **Dynamic Mode Activated**: Logs should show `"✓ 進入 Dynamic 模式"`
- [ ] **Stop Price Calculated**: Logs should show `dynamic_stop_price=...` (not `None`)
- [ ] **Trigger Condition Met**: Logs should show `triggered=True`
- [ ] **No Exceptions**: Check for error logs during close execution
- [ ] **Position Status**: After trigger, position should be `CLOSED` (not `OPEN` or `ERROR`)
- [ ] **Exit Reason**: Should be `"dynamic_stop"` (not `"base_stop"` or `null`)

---

## Code Flow Summary

### Bot-Created Position Close Flow:
```
check_trailing_stop()
  → compute_stop_state() → StopState
  → Check trigger condition (mark <=/>= stop_price)
  → If triggered:
     → Check auto_close_enabled
     → If enabled: close_futures_position()
     → Update DB: status=CLOSED, exit_reason="dynamic_stop"
```

### Non-Bot Position Close Flow:
```
check_binance_non_bot_positions()
  → compute_stop_state() → StopState
  → Check trigger condition (mark <=/>= stop_price)
  → If triggered:
     → Check auto_close_enabled
     → If enabled: close_futures_position()
     → Create Position record: status=CLOSED, exit_reason="dynamic_trailing"
```

---

## Recommendations for Debugging

1. **Enable Detailed Logging**: Check logs for:
   - `"倉位 ... LONG/SHORT 模式: dynamic"`
   - `"觸發條件: mark <= dyn_stop (... = True)"`
   - `"triggered=True"`
   - `"已成功關倉"`

2. **Verify Configuration**:
   ```bash
   curl http://localhost:8000/settings/trailing
   ```
   Should return:
   ```json
   {
     "trailing_enabled": true,
     "auto_close_enabled": true,
     "profit_threshold_pct": 1.0,
     "lock_ratio": 0.5,
     "base_sl_pct": 3.0
   }
   ```

3. **Check Position State**:
   - Query position from DB
   - Verify `highest_price` is set (for LONG) or updated (for SHORT)
   - Verify `trail_callback` is not `None`
   - Verify `dyn_profit_threshold_pct` or global threshold is set

4. **Monitor Real-Time Logs**:
   - Watch for `"✓ 進入 Dynamic 模式"` message
   - Watch for trigger condition evaluation
   - Watch for `"已成功關倉"` or error messages

---

## Summary

| Component | Status | Notes |
|-----------|--------|-------|
| **Requirements Match** | ✅ 14/14 | All requirements implemented |
| **Dynamic Stop Logic** | ✅ Correct | Trigger conditions match requirements |
| **Auto-Close Check** | ⚠️ Potential Issue | May be disabled via config |
| **Error Handling** | ✅ Implemented | Catches exceptions, sets ERROR status |
| **Non-Bot Position Tracking** | ✅ Implemented | Creates DB records on close |
| **Statistics Integration** | ✅ Implemented | Includes all CLOSED positions |

**Most Likely Cause**: `auto_close_enabled` is set to `False` in the configuration, preventing automatic closure even when dynamic stop triggers.

