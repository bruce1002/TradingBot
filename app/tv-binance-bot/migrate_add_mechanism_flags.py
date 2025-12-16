"""
資料庫遷移腳本：新增停損/止盈機制控制欄位

新增欄位：
- bot_stop_loss_enabled: 是否啟用 Bot 內建的停損機制
- tv_signal_close_enabled: 是否啟用 TradingView 訊號關倉機制

預設值都是 True（向後兼容，保持現有行為）。
"""

import sqlite3
import os
import sys
from pathlib import Path

# 資料庫檔案路徑
DB_PATH = Path(__file__).parent / "trading_bot.db"

def migrate():
    """執行遷移"""
    if not DB_PATH.exists():
        print(f"資料庫檔案不存在: {DB_PATH}")
        print("請先啟動應用程式以建立資料庫")
        sys.exit(1)
    
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    try:
        # 檢查欄位是否已存在
        cursor.execute("PRAGMA table_info(positions)")
        columns = [col[1] for col in cursor.fetchall()]
        
        changes_made = False
        
        # 新增 bot_stop_loss_enabled 欄位
        if "bot_stop_loss_enabled" not in columns:
            print("新增 bot_stop_loss_enabled 欄位...")
            cursor.execute("""
                ALTER TABLE positions 
                ADD COLUMN bot_stop_loss_enabled BOOLEAN NOT NULL DEFAULT 1
            """)
            print("✓ bot_stop_loss_enabled 欄位已新增（預設值: True）")
            changes_made = True
        else:
            print("✓ bot_stop_loss_enabled 欄位已存在，跳過")
        
        # 新增 tv_signal_close_enabled 欄位
        if "tv_signal_close_enabled" not in columns:
            print("新增 tv_signal_close_enabled 欄位...")
            cursor.execute("""
                ALTER TABLE positions 
                ADD COLUMN tv_signal_close_enabled BOOLEAN NOT NULL DEFAULT 1
            """)
            print("✓ tv_signal_close_enabled 欄位已新增（預設值: True）")
            changes_made = True
        else:
            print("✓ tv_signal_close_enabled 欄位已存在，跳過")
        
        # 提交變更
        conn.commit()
        
        if changes_made:
            print("\n✅ 遷移完成！")
            print("\n說明：")
            print("- 所有現有倉位的 bot_stop_loss_enabled 和 tv_signal_close_enabled 都已設為 True")
            print("- 這意味著現有行為不會改變（兩種機制都啟用）")
            print("- 您可以透過 API 端點 /positions/{pos_id}/mechanism-config 來更新這些設定")
        else:
            print("\n✅ 無需遷移（欄位已存在）")
        
    except sqlite3.Error as e:
        conn.rollback()
        print(f"❌ 遷移失敗: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    print("=" * 60)
    print("資料庫遷移：新增停損/止盈機制控制欄位")
    print("=" * 60)
    print(f"資料庫路徑: {DB_PATH}")
    print()
    
    migrate()

