#!/usr/bin/env python
"""
Simple health-check script for Binance Futures connection.

It uses the same environment variables as your FastAPI app:
- BINANCE_API_KEY
- BINANCE_API_SECRET
- USE_TESTNET
- (optional) BINANCE_FUTURES_BASE_URL

Run:
    USE_TESTNET=0 python binance_health_check.py

It will:
1. Build the Binance client via your binance_client.get_client().
2. Ping the futures API.
3. Print basic USDT-M futures balance info.
"""

import os
import sys
from binance.exceptions import BinanceAPIException
from binance_client import get_client  # import from your project


def main() -> int:
    use_testnet = os.getenv("USE_TESTNET")
    env_desc = "未設定 (預設為正式網)" if not use_testnet else use_testnet.strip()
    print(f"[health-check] USE_TESTNET={env_desc}")

    try:
        client = get_client()
        print("[health-check] Client 建立成功")
    except Exception as e:
        print(f"[health-check] 建立 Client 失敗: {e}")
        return 1

    # 1) Ping futures API
    try:
        client.futures_ping()
        print("[health-check] futures_ping OK")
    except BinanceAPIException as e:
        print(f"[health-check] futures_ping 失敗: {e}")
        return 1
    except Exception as e:
        print(f"[health-check] futures_ping 發生未知錯誤: {e}")
        return 1

    # 2) Get futures account balance summary
    try:
        balances = client.futures_account_balance()
        usdt_balance = next((b for b in balances if b.get("asset") == "USDT"), None)
        if usdt_balance:
            total_wallet = float(usdt_balance.get("balance", 0.0))
            available = float(usdt_balance.get("withdrawAvailable", 0.0))
            print(f"[health-check] USDT Futures balance: total={total_wallet}, available={available}")
        else:
            print("[health-check] 找不到 USDT 期貨餘額（可能尚未開啟期貨帳戶或無餘額）")
    except BinanceAPIException as e:
        print(f"[health-check] 取得期貨帳戶餘額失敗: {e}")
        return 1
    except Exception as e:
        print(f"[health-check] 取得期貨帳戶餘額發生未知錯誤: {e}")
        return 1

    print("[health-check] 完成，Binance 期貨連線看起來正常 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
