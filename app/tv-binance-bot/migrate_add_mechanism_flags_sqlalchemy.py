"""
資料庫遷移腳本：新增停損/止盈機制控制欄位（使用 SQLAlchemy，支援多種資料庫）

新增欄位：
- bot_stop_loss_enabled: 是否啟用 Bot 內建的停損機制
- tv_signal_close_enabled: 是否啟用 TradingView 訊號關倉機制

預設值都是 True（向後兼容，保持現有行為）。

此腳本使用 SQLAlchemy，支援 SQLite、PostgreSQL、MySQL 等多種資料庫。
"""

import sys
from pathlib import Path
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError, ProgrammingError

# 添加專案路徑
sys.path.insert(0, str(Path(__file__).parent))

from db import engine, Base
from models import Position


def column_exists(table_name: str, column_name: str) -> bool:
    """檢查欄位是否已存在"""
    inspector = inspect(engine)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def migrate():
    """執行遷移"""
    try:
        # 檢查資料庫連線
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("✓ 資料庫連線成功")
    except Exception as e:
        print(f"❌ 資料庫連線失敗: {e}")
        print("請確認資料庫設定正確")
        sys.exit(1)
    
    # 檢查 positions 表是否存在
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    
    if "positions" not in tables:
        print("⚠️  positions 表不存在，將在應用程式啟動時自動建立")
        print("   請先啟動應用程式以建立資料表")
        return
    
    changes_made = False
    
    # 新增 bot_stop_loss_enabled 欄位
    if not column_exists("positions", "bot_stop_loss_enabled"):
        print("新增 bot_stop_loss_enabled 欄位...")
        try:
            with engine.connect() as conn:
                # 使用 ALTER TABLE 語句（SQLAlchemy 不直接支援 ALTER TABLE，使用原生 SQL）
                # 根據資料庫類型選擇適當的語法
                db_url = str(engine.url)
                
                if "sqlite" in db_url.lower():
                    # SQLite
                    conn.execute(text("""
                        ALTER TABLE positions 
                        ADD COLUMN bot_stop_loss_enabled BOOLEAN NOT NULL DEFAULT 1
                    """))
                elif "postgresql" in db_url.lower():
                    # PostgreSQL
                    conn.execute(text("""
                        ALTER TABLE positions 
                        ADD COLUMN bot_stop_loss_enabled BOOLEAN NOT NULL DEFAULT TRUE
                    """))
                elif "mysql" in db_url.lower():
                    # MySQL
                    conn.execute(text("""
                        ALTER TABLE positions 
                        ADD COLUMN bot_stop_loss_enabled BOOLEAN NOT NULL DEFAULT TRUE
                    """))
                else:
                    # 通用語法（嘗試）
                    conn.execute(text("""
                        ALTER TABLE positions 
                        ADD COLUMN bot_stop_loss_enabled BOOLEAN NOT NULL DEFAULT TRUE
                    """))
                
                conn.commit()
            print("✓ bot_stop_loss_enabled 欄位已新增（預設值: True）")
            changes_made = True
        except (OperationalError, ProgrammingError) as e:
            print(f"❌ 新增 bot_stop_loss_enabled 欄位失敗: {e}")
            print("   請手動執行 SQL: ALTER TABLE positions ADD COLUMN bot_stop_loss_enabled BOOLEAN NOT NULL DEFAULT TRUE")
    else:
        print("✓ bot_stop_loss_enabled 欄位已存在，跳過")
    
    # 新增 tv_signal_close_enabled 欄位
    if not column_exists("positions", "tv_signal_close_enabled"):
        print("新增 tv_signal_close_enabled 欄位...")
        try:
            with engine.connect() as conn:
                db_url = str(engine.url)
                
                if "sqlite" in db_url.lower():
                    # SQLite
                    conn.execute(text("""
                        ALTER TABLE positions 
                        ADD COLUMN tv_signal_close_enabled BOOLEAN NOT NULL DEFAULT 1
                    """))
                elif "postgresql" in db_url.lower():
                    # PostgreSQL
                    conn.execute(text("""
                        ALTER TABLE positions 
                        ADD COLUMN tv_signal_close_enabled BOOLEAN NOT NULL DEFAULT TRUE
                    """))
                elif "mysql" in db_url.lower():
                    # MySQL
                    conn.execute(text("""
                        ALTER TABLE positions 
                        ADD COLUMN tv_signal_close_enabled BOOLEAN NOT NULL DEFAULT TRUE
                    """))
                else:
                    # 通用語法
                    conn.execute(text("""
                        ALTER TABLE positions 
                        ADD COLUMN tv_signal_close_enabled BOOLEAN NOT NULL DEFAULT TRUE
                    """))
                
                conn.commit()
            print("✓ tv_signal_close_enabled 欄位已新增（預設值: True）")
            changes_made = True
        except (OperationalError, ProgrammingError) as e:
            print(f"❌ 新增 tv_signal_close_enabled 欄位失敗: {e}")
            print("   請手動執行 SQL: ALTER TABLE positions ADD COLUMN tv_signal_close_enabled BOOLEAN NOT NULL DEFAULT TRUE")
    else:
        print("✓ tv_signal_close_enabled 欄位已存在，跳過")
    
    if changes_made:
        print("\n✅ 遷移完成！")
        print("\n說明：")
        print("- 所有現有倉位的 bot_stop_loss_enabled 和 tv_signal_close_enabled 都已設為 True")
        print("- 這意味著現有行為不會改變（兩種機制都啟用）")
        print("- 您可以透過 API 端點 /positions/{pos_id}/mechanism-config 來更新這些設定")
    else:
        print("\n✅ 無需遷移（欄位已存在）")


if __name__ == "__main__":
    print("=" * 60)
    print("資料庫遷移：新增停損/止盈機制控制欄位")
    print("=" * 60)
    print(f"資料庫 URL: {engine.url}")
    print()
    
    migrate()

