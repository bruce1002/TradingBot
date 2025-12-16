#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
資料庫遷移腳本：為 tv_signal_logs 表添加 position_size 欄位

執行方式：
    python migrate_add_position_size.py
"""

import sqlite3
import os
from pathlib import Path

DB_FILE = "trading_bot.db"

def migrate():
    """執行遷移：添加 position_size 欄位"""
    if not os.path.exists(DB_FILE):
        print(f"錯誤：找不到資料庫檔案 {DB_FILE}")
        return False
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    try:
        # 檢查欄位是否已存在
        cursor.execute("PRAGMA table_info(tv_signal_logs)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if "position_size" in columns:
            print("✓ position_size 欄位已存在，無需遷移")
            return True
        
        print("開始遷移：添加 position_size 欄位...")
        
        # SQLite 不支援直接 ADD COLUMN（舊版本），但 SQLite 3.1.3+ 支援
        # 為了兼容性，我們先嘗試直接添加
        try:
            cursor.execute("ALTER TABLE tv_signal_logs ADD COLUMN position_size REAL NULL")
            conn.commit()
            print("✓ 成功添加 position_size 欄位")
            return True
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                print("✓ position_size 欄位已存在")
                return True
            else:
                print(f"⚠ 直接添加欄位失敗: {e}")
                print("嘗試使用表重建方式...")
                
                # 備份現有資料
                cursor.execute("SELECT * FROM tv_signal_logs")
                rows = cursor.fetchall()
                column_names = [desc[0] for desc in cursor.description]
                
                # 建立新表
                cursor.execute("""
                    CREATE TABLE tv_signal_logs_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        bot_key VARCHAR(100),
                        signal_id INTEGER,
                        symbol VARCHAR(50) NOT NULL,
                        side VARCHAR(10) NOT NULL,
                        qty REAL NOT NULL,
                        position_size REAL NULL,
                        raw_body TEXT,
                        raw_payload TEXT,
                        received_at TIMESTAMP NOT NULL,
                        processed BOOLEAN NOT NULL DEFAULT 0,
                        process_result VARCHAR(255),
                        FOREIGN KEY(signal_id) REFERENCES tv_signal_configs(id)
                    )
                """)
                
                # 複製資料（position_size 設為 NULL）
                if rows:
                    placeholders = ",".join(["?" for _ in column_names])
                    cursor.execute(f"INSERT INTO tv_signal_logs_new ({','.join(column_names)}, position_size) SELECT {','.join(column_names)}, NULL FROM tv_signal_logs")
                
                # 刪除舊表並重新命名
                cursor.execute("DROP TABLE tv_signal_logs")
                cursor.execute("ALTER TABLE tv_signal_logs_new RENAME TO tv_signal_logs")
                
                # 重建索引
                cursor.execute("CREATE INDEX IF NOT EXISTS ix_tv_signal_logs_bot_key ON tv_signal_logs(bot_key)")
                cursor.execute("CREATE INDEX IF NOT EXISTS ix_tv_signal_logs_signal_id ON tv_signal_logs(signal_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS ix_tv_signal_logs_symbol ON tv_signal_logs(symbol)")
                cursor.execute("CREATE INDEX IF NOT EXISTS ix_tv_signal_logs_processed ON tv_signal_logs(processed)")
                
                conn.commit()
                print("✓ 使用表重建方式成功添加 position_size 欄位")
                return True
                
    except Exception as e:
        print(f"❌ 遷移失敗: {e}")
        import traceback
        traceback.print_exc()
        conn.rollback()
        return False
    finally:
        conn.close()

if __name__ == "__main__":
    print("=" * 60)
    print("資料庫遷移：添加 position_size 欄位")
    print("=" * 60)
    
    if migrate():
        print("\n✓ 遷移完成！")
    else:
        print("\n❌ 遷移失敗，請檢查錯誤訊息")
        exit(1)

