#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
資料庫遷移腳本：為 bot_configs 表添加 max_invest_usdt 欄位

執行方式：
    python migrate_add_max_invest_usdt.py
"""

import sqlite3
import os
from pathlib import Path

DB_FILE = "trading_bot.db"

def migrate():
    """執行遷移：添加 max_invest_usdt 欄位"""
    if not os.path.exists(DB_FILE):
        print(f"錯誤：找不到資料庫檔案 {DB_FILE}")
        return False
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    try:
        # 檢查欄位是否已存在
        cursor.execute("PRAGMA table_info(bot_configs)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if "max_invest_usdt" in columns:
            print("✓ max_invest_usdt 欄位已存在，無需遷移")
            return True
        
        print("開始遷移：添加 max_invest_usdt 欄位...")
        
        # SQLite 3.1.3+ 支援 ADD COLUMN
        try:
            cursor.execute("ALTER TABLE bot_configs ADD COLUMN max_invest_usdt REAL NULL")
            conn.commit()
            print("✓ 成功添加 max_invest_usdt 欄位")
            return True
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                print("✓ max_invest_usdt 欄位已存在")
                return True
            else:
                print(f"❌ 遷移失敗: {e}")
                return False
                
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
    print("資料庫遷移：添加 max_invest_usdt 欄位")
    print("=" * 60)
    
    if migrate():
        print("\n✓ 遷移完成！")
    else:
        print("\n❌ 遷移失敗，請檢查錯誤訊息")
        exit(1)

