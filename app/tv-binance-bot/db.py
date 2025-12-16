"""
資料庫初始化模組

負責建立資料庫連線、Session 管理，以及初始化資料庫表結構。
使用 SQLite 作為資料庫，方便本地開發和測試。
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.ext.declarative import declarative_base

# 資料庫檔案路徑（儲存在專案根目錄）
DATABASE_URL = "sqlite:///./trading_bot.db"

# 建立資料庫引擎
# connect_args={"check_same_thread": False} 是 SQLite 特有設定，允許多線程存取
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
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
    from models import Position, TradingViewSignalLog, BotConfig, TVSignalConfig  # noqa: F401
    
    # 建立所有資料表
    Base.metadata.create_all(bind=engine)
    print("資料庫初始化完成")


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

