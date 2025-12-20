"""
資料庫遷移腳本：新增半自動交易模式

新增欄位和表：
1. bot_configs.trading_mode: 交易模式（auto, semi-auto, manual）
2. pending_orders 表：用於儲存等待批准的交易訊號

預設值為 "auto"（向後兼容，保持現有行為）。
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
        changes_made = False
        
        # 1. 檢查並新增 trading_mode 欄位到 bot_configs 表
        cursor.execute("PRAGMA table_info(bot_configs)")
        columns = [col[1] for col in cursor.fetchall()]
        
        if "trading_mode" not in columns:
            print("新增 trading_mode 欄位到 bot_configs 表...")
            cursor.execute("""
                ALTER TABLE bot_configs 
                ADD COLUMN trading_mode VARCHAR(20) NOT NULL DEFAULT 'auto'
            """)
            print("✓ trading_mode 欄位已新增（預設值: 'auto'）")
            changes_made = True
        else:
            print("✓ trading_mode 欄位已存在，跳過")
        
        # 2. 檢查並建立 pending_orders 表
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='pending_orders'
        """)
        table_exists = cursor.fetchone() is not None
        
        if not table_exists:
            print("建立 pending_orders 表...")
            cursor.execute("""
                CREATE TABLE pending_orders (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    bot_id INTEGER NOT NULL,
                    tv_signal_log_id INTEGER NOT NULL,
                    symbol VARCHAR(20) NOT NULL,
                    side VARCHAR(10) NOT NULL,
                    qty REAL,
                    position_size REAL,
                    calculated_qty REAL,
                    calculated_side VARCHAR(10),
                    is_position_based BOOLEAN NOT NULL DEFAULT 0,
                    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
                    approved_at DATETIME,
                    rejected_at DATETIME,
                    executed_at DATETIME,
                    error_message TEXT,
                    position_id INTEGER,
                    created_at DATETIME NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY(bot_id) REFERENCES bot_configs (id),
                    FOREIGN KEY(tv_signal_log_id) REFERENCES tv_signal_logs (id)
                )
            """)
            
            # 建立索引
            cursor.execute("CREATE INDEX ix_pending_orders_bot_id ON pending_orders (bot_id)")
            cursor.execute("CREATE INDEX ix_pending_orders_tv_signal_log_id ON pending_orders (tv_signal_log_id)")
            cursor.execute("CREATE INDEX ix_pending_orders_symbol ON pending_orders (symbol)")
            cursor.execute("CREATE INDEX ix_pending_orders_status ON pending_orders (status)")
            
            print("✓ pending_orders 表已建立")
            changes_made = True
        else:
            print("✓ pending_orders 表已存在，跳過")
        
        # 提交變更
        conn.commit()
        
        if changes_made:
            print("\n✅ 遷移完成！")
            print("\n說明：")
            print("- 所有現有 Bot 的 trading_mode 都已設為 'auto'（自動執行）")
            print("- 這意味著現有行為不會改變（所有訊號立即執行）")
            print("- 您可以透過 API 端點更新 Bot 的 trading_mode 為 'semi-auto' 來啟用半自動模式")
            print("- 當 trading_mode 為 'semi-auto' 時，收到的訊號會存入 pending_orders 表等待批准")
        else:
            print("\n✅ 無需遷移（所有變更已存在）")
        
    except sqlite3.Error as e:
        conn.rollback()
        print(f"❌ 遷移失敗: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    print("=" * 60)
    print("資料庫遷移：新增半自動交易模式")
    print("=" * 60)
    print(f"資料庫路徑: {DB_PATH}")
    print()
    
    migrate()

