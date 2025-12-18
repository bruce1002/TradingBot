"""
資料庫初始化模組

負責建立資料庫連線、Session 管理，以及初始化資料庫表結構。
支援 SQLite（本地開發）和 PostgreSQL/MySQL（生產環境）。
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.ext.declarative import declarative_base

# 從環境變數讀取資料庫 URL，如果未設定則使用 SQLite（本地開發）
# Google Cloud 或其他雲端平台可以透過環境變數設定資料庫連線
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./trading_bot.db")

# 建立資料庫引擎
# SQLite 需要特殊設定，其他資料庫不需要
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    echo=False  # 設為 True 可以看到 SQL 語句，方便除錯
)

# 建立 Session 類別，用於建立資料庫會話
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base 類別，所有 ORM 模型都會繼承這個類別
Base = declarative_base()


def init_db():
    """
    初始化資料庫
    
    建立所有資料表。如果資料表已存在則不會覆蓋。
    建議在應用程式啟動時呼叫此函數。
    """
    # 匯入所有模型，確保 Base.metadata 包含所有表定義
    from models import Position, TradingViewSignalLog, BotConfig, TVSignalConfig, PortfolioTrailingConfig, SymbolLock  # noqa: F401
    
    # 建立所有資料表
    Base.metadata.create_all(bind=engine)
    
    # 執行遷移（新增欄位）
    try:
        migrate_add_mechanism_flags()
    except Exception as e:
        # 如果遷移失敗，記錄警告但不中斷啟動
        import logging
        logger = logging.getLogger("tvbot")
        logger.warning(f"資料庫遷移失敗（可能欄位已存在）: {e}")
    
    print("資料庫初始化完成")


def migrate_add_mechanism_flags():
    """
    遷移：新增停損/止盈機制控制欄位
    
    如果欄位已存在則跳過。
    """
    from sqlalchemy import inspect, text
    from sqlalchemy.exc import OperationalError, ProgrammingError
    
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    
    if "positions" not in tables:
        return  # 表不存在，將由 create_all 建立
    
    # 檢查欄位是否已存在
    columns = [col['name'] for col in inspector.get_columns("positions")]
    
    # 新增 bot_stop_loss_enabled 欄位
    if "bot_stop_loss_enabled" not in columns:
        try:
            with engine.connect() as conn:
                db_url = str(engine.url)
                
                if "sqlite" in db_url.lower():
                    conn.execute(text("""
                        ALTER TABLE positions 
                        ADD COLUMN bot_stop_loss_enabled BOOLEAN NOT NULL DEFAULT 1
                    """))
                else:
                    # PostgreSQL, MySQL, etc.
                    conn.execute(text("""
                        ALTER TABLE positions 
                        ADD COLUMN bot_stop_loss_enabled BOOLEAN NOT NULL DEFAULT TRUE
                    """))
                conn.commit()
        except (OperationalError, ProgrammingError):
            pass  # 欄位可能已存在或語法錯誤，忽略
    
    # 新增 tv_signal_close_enabled 欄位
    if "tv_signal_close_enabled" not in columns:
        try:
            with engine.connect() as conn:
                db_url = str(engine.url)
                
                if "sqlite" in db_url.lower():
                    conn.execute(text("""
                        ALTER TABLE positions 
                        ADD COLUMN tv_signal_close_enabled BOOLEAN NOT NULL DEFAULT 1
                    """))
                else:
                    # PostgreSQL, MySQL, etc.
                    conn.execute(text("""
                        ALTER TABLE positions 
                        ADD COLUMN tv_signal_close_enabled BOOLEAN NOT NULL DEFAULT TRUE
                    """))
                conn.commit()
        except (OperationalError, ProgrammingError):
            pass  # 欄位可能已存在或語法錯誤，忽略


def get_db():
    """
    取得資料庫 Session 的生成器函數
    
    用於 FastAPI 的依賴注入，確保每個請求都有獨立的資料庫連線，
    並在請求結束後自動關閉連線。
    
    Yields:
        Session: SQLAlchemy 資料庫會話物件
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

