# Stop-Loss Mechanism Analysis

## Current Implementation Overview

### 1. **Two Stop-Loss Modes**

#### Base Mode
- **When**: Before profit threshold is reached OR when dynamic mode is disabled
- **Trigger**: `profit_pct_based_on_best_for_threshold < profit_threshold_pct`

#### Dynamic Mode  
- **When**: After profit threshold is reached
- **Activation**: Once activated, stays active even if price retraces (uses `best` price, not current `mark`)
- **Trigger**: `profit_pct_based_on_best_for_threshold >= profit_threshold_pct`

### 2. **Configuration Priority**

1. **Position override values** (if manually set by user)
2. **Global settings** (TRAILING_CONFIG or environment defaults)
3. All positions start with NULL values and use global settings dynamically

### 3. **Stop Price Calculations**

#### For LONG Positions:

**Base Mode Stop Price:**
```python
base_stop_price = entry * (1 - base_sl_pct / 100.0)
```
- **Current**: Price-based percentage (e.g., if base_sl_pct = 1%, stop = entry * 0.99)
- **Your Requirement**: `entry_price - (Margin*Leverage) * (Base SL%)`
  - Margin*Leverage = Entry*Qty
  - This would be: `entry - (Entry*Qty) * (Base SL% / 100)`
  - **Issue**: This depends on Qty, making stop price quantity-dependent

**Dynamic Mode Stop Price:**
```python
dynamic_stop_price = entry + (best - entry) * lock_ratio
```
- **Current**: ✅ Matches your requirement: `Entry price + ((Max Mark Price) - (Entry_price)) * lock_ratio`

**Profit Threshold Check:**
```python
profit_pct_based_on_best_for_threshold >= profit_threshold_pct
```
- Uses `unrealized_pnl_pct` (margin-based) when available
- **Current**: Margin-based PnL% = `(unrealized_pnl_amount / margin) * 100`
- **Your Requirement**: `(Maximum Mark Price - entry_price) / (Margin * Leverage) >= PnL% threshold`
  - **Issue**: `(Max Mark - entry) / (Margin * Leverage)` = `(Max Mark - entry) / (Entry * Qty)`
  - This is NOT a percentage and depends on Qty
  - The current margin-based calculation is mathematically correct

**Trigger Condition:**
```python
triggered = mark <= stop_price  # LONG
```
- ✅ Matches your requirement

#### For SHORT Positions:

**Base Mode Stop Price:**
```python
base_stop_price = entry * (1 + base_sl_pct / 100.0)
```
- **Current**: Price-based percentage
- **Your Requirement**: `entry_price + (Margin*Leverage) * (Base SL%)`
  - Same issue as LONG - depends on Qty

**Dynamic Mode Stop Price:**
```python
dynamic_stop_price = entry - (entry - best) * lock_ratio
```
- **Current**: ✅ Matches your requirement: `Entry price - (((Entry_price) - Minimum Mark Price)) * lock_ratio`

**Profit Threshold Check:**
- Uses margin-based PnL% calculation
- **Your Requirement**: `(entry_price - Minimum Mark Price) / (Margin * Leverage) >= PnL% threshold`
  - Same mathematical issue as LONG

**Trigger Condition:**
```python
triggered = mark >= stop_price  # SHORT
```
- ✅ Matches your requirement

### 4. **Dynamic Mode Persistence**

✅ **Correctly Implemented**:
- Uses `best` (highest for LONG, lowest for SHORT) instead of current `mark`
- Once `profit_pct_based_on_best_for_threshold >= profit_threshold_pct`, stays in dynamic mode
- Even if current price retraces, dynamic mode remains active

### 5. **Stop Price Recalculation**

The stop price is recalculated every time `compute_stop_state()` is called, which happens:
- On every `check_trailing_stop()` cycle
- When mark price changes
- When configuration changes (global or position override)

✅ **Correctly Implemented**

## Issues Found

### ❌ **Critical Issue: Base Stop Price Calculation**

**Current Implementation:**
- LONG: `entry * (1 - base_sl_pct / 100.0)` - Price-based percentage
- SHORT: `entry * (1 + base_sl_pct / 100.0)` - Price-based percentage

**Your Requirement:**
- LONG: `entry_price - (Margin*Leverage) * (Base SL%)`
- SHORT: `entry_price + (Margin*Leverage) * (Base SL%)`

**Problem**: Your requirement formula includes `(Margin*Leverage)` which equals `Entry*Qty`. This makes the stop price **quantity-dependent**, which is unusual. Typically, stop prices are quantity-independent.

**Example**:
- Entry = 100, Base SL% = 1%, Qty = 10
- Current: `100 * (1 - 0.01) = 99` (same regardless of Qty)
- Your requirement: `100 - (100*10) * 0.01 = 100 - 10 = 90` (depends on Qty!)

### ⚠️ **Warning: Profit Threshold Formula**

**Your Requirement**: `(Max Mark - entry) / (Margin * Leverage) >= PnL% threshold`

**Mathematical Issue**:
- `(Max Mark - entry) / (Margin * Leverage)` = `(Max Mark - entry) / (Entry * Qty)`
- This is NOT a percentage - it's a ratio that depends on quantity
- The current implementation uses margin-based PnL% which is correct: `(PnL_amount / margin) * 100`

## Recommendations

1. **Clarify Base Stop Price Formula**: 
   - If you want margin-based (quantity-dependent), we need to update the code
   - If you want price-based (quantity-independent), current implementation is correct

2. **Clarify Profit Threshold Formula**:
   - Current margin-based calculation is mathematically sound
   - Your formula seems to have a mathematical inconsistency

3. **Current Implementation Status**:
   - ✅ Dynamic mode persistence (correct)
   - ✅ Dynamic stop price calculation (correct)
   - ✅ Trigger conditions (correct)
   - ✅ Configuration priority (correct)
   - ❌ Base stop price calculation (doesn't match requirement formula)
   - ⚠️ Profit threshold formula (requirement has mathematical issue)

