"""
資料模型定義

定義所有資料庫表的 ORM 模型。
目前包含 Position 模型，用於記錄交易倉位資訊。
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, JSON, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from db import Base


class Position(Base):
    """
    倉位資料模型
    
    記錄 TradingView bot 建立的倉位資訊，包括：
    - 交易對、方向（LONG/SHORT）、數量、進場價格
    - 倉位狀態（OPEN/CLOSING/CLOSED/ERROR）
    - 幣安訂單 ID 用於追蹤和查詢
    - 追蹤停損相關欄位（最高價、回調比例）
    """
    
    __tablename__ = "positions"
    
    # 主鍵：自動遞增的 ID
    id = Column(Integer, primary_key=True, index=True, comment="倉位 ID")
    
    # 交易資訊
    symbol = Column(String(20), nullable=False, index=True, comment="交易對，例如：BTCUSDT")
    side = Column(String(10), nullable=False, comment="倉位方向：LONG 或 SHORT")
    qty = Column(Float, nullable=False, comment="倉位數量")
    entry_price = Column(Float, nullable=False, comment="進場價格")
    
    # 倉位狀態
    # OPEN: 倉位已建立，正在持倉中
    # CLOSING: 正在平倉中（已送出平倉訂單）
    # CLOSED: 倉位已完全平倉
    # ERROR: 發生錯誤（例如下單失敗）
    status = Column(String(20), default="OPEN", nullable=False, index=True, comment="倉位狀態：OPEN, CLOSING, CLOSED, ERROR")
    
    # 訂單追蹤
    binance_order_id = Column(Integer, nullable=True, unique=True, index=True, comment="幣安訂單 ID（建立倉位的訂單）")
    client_order_id = Column(String(50), nullable=True, comment="客戶端訂單 ID")
    
    # Bot 關聯
    bot_id = Column(Integer, nullable=True, index=True, comment="關聯的 Bot 設定 ID")
    tv_signal_log_id = Column(Integer, nullable=True, index=True, comment="關聯的 TradingView Signal Log ID")
    
    # 追蹤停損相關欄位
    # highest_price: 追蹤停損用，記錄倉位建立後達到的最高價格（LONG）或最低價格（SHORT）
    # trail_callback: 回調比例（鎖利比例），例如 0.666 代表鎖住 2/3 的利潤，0 表示僅使用 base stop
    # dyn_profit_threshold_pct: 每筆倉位覆寫的獲利門檻百分比（例如 1.0 表示 1%），NULL 時使用全局配置
    # base_stop_loss_pct: 每筆倉位覆寫的基礎停損百分比（例如 0.5 表示 0.5%），NULL 時使用全局配置
    highest_price = Column(Float, nullable=True, comment="追蹤停損用：倉位建立後達到的最高價格（LONG）或最低價格（SHORT）")
    trail_callback = Column(Float, nullable=True, comment="追蹤停損回調比例（鎖利比例），例如 0.666 代表鎖住 2/3 的利潤，0 表示僅使用 base stop，NULL 時使用全局配置")
    dyn_profit_threshold_pct = Column(Float, nullable=True, comment="每筆倉位覆寫的獲利門檻百分比（例如 1.0 表示 1%），NULL 時使用全局配置")
    base_stop_loss_pct = Column(Float, nullable=True, comment="每筆倉位覆寫的基礎停損百分比（例如 0.5 表示 0.5%），NULL 時使用全局配置")
    
    # 停損/止盈機制控制
    # bot_stop_loss_enabled: 是否啟用 Bot 內建的停損機制（dynamic stop / base stop）
    # tv_signal_close_enabled: 是否啟用 TradingView 訊號關倉機制（position_size=0）
    bot_stop_loss_enabled = Column(Boolean, default=True, nullable=False, comment="是否啟用 Bot 內建的停損機制（dynamic stop / base stop）")
    tv_signal_close_enabled = Column(Boolean, default=True, nullable=False, comment="是否啟用 TradingView 訊號關倉機制（position_size=0）")
    
    # 平倉資訊
    exit_price = Column(Float, nullable=True, comment="平倉價格")
    exit_reason = Column(String(50), nullable=True, comment="平倉原因，例如 manual_close / trailing_stop / error")
    
    # 時間戳記
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, comment="建立時間（進場時間）")
    closed_at = Column(DateTime(timezone=True), nullable=True, comment="平倉時間（倉位關閉時間）")
    
    def __repr__(self):
        """字串表示，方便除錯"""
        return f"<Position(id={self.id}, symbol={self.symbol}, side={self.side}, qty={self.qty}, status={self.status})>"
    
    def to_dict(self):
        """
        將模型轉換為字典
        
        方便 JSON 序列化，用於 API 回應。
        
        Returns:
            dict: 包含所有欄位的字典
        """
        return {
            "id": self.id,
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.qty,
            "entry_price": self.entry_price,
            "status": self.status,
            "binance_order_id": self.binance_order_id,
            "client_order_id": self.client_order_id,
            "highest_price": self.highest_price,
            "trail_callback": self.trail_callback,
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "bot_id": self.bot_id,
            "tv_signal_log_id": self.tv_signal_log_id,
            "dyn_profit_threshold_pct": self.dyn_profit_threshold_pct,
            "base_stop_loss_pct": self.base_stop_loss_pct,
            "bot_stop_loss_enabled": self.bot_stop_loss_enabled,
            "tv_signal_close_enabled": self.tv_signal_close_enabled,
        }


class TVSignalConfig(Base):
    """
    TradingView Signal 策略配置模型
    
    定義 TradingView 策略（Signal），用於產生 webhook URL 和 alert JSON 範本。
    每個 Signal 可以對應多個 Bot，實現「一個策略、多個 Bot 實例」的架構。
    """
    
    __tablename__ = "tv_signal_configs"
    
    # 主鍵
    id = Column(Integer, primary_key=True, index=True, comment="Signal Config ID")
    
    # 基本資訊
    name = Column(String(100), nullable=False, comment="Signal 策略名稱")
    signal_key = Column(String(100), nullable=False, unique=True, index=True, comment="Signal Key（唯一），用於 TradingView alert JSON 中的 signal_key 欄位")
    description = Column(String(500), nullable=True, comment="策略描述")
    
    # 提示資訊（不影響邏輯，僅供參考）
    symbol_hint = Column(String(50), nullable=True, comment="建議交易對提示，例如 BTCUSDT")
    timeframe_hint = Column(String(20), nullable=True, comment="建議時間框架提示，例如 15m / 1h")
    
    # 狀態
    enabled = Column(Boolean, default=True, nullable=False, index=True, comment="是否啟用")
    
    # 時間戳記
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, comment="建立時間")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False, comment="更新時間")
    
    def __repr__(self):
        """字串表示，方便除錯"""
        return f"<TVSignalConfig(id={self.id}, name={self.name}, signal_key={self.signal_key}, enabled={self.enabled})>"
    
    def to_dict(self):
        """將模型轉換為字典"""
        return {
            "id": self.id,
            "name": self.name,
            "signal_key": self.signal_key,
            "description": self.description,
            "symbol_hint": self.symbol_hint,
            "timeframe_hint": self.timeframe_hint,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TradingViewSignalLog(Base):
    """
    TradingView Signal 日誌模型
    
    記錄所有從 TradingView 收到的交易訊號，用於追蹤和除錯。
    """
    
    __tablename__ = "tv_signal_logs"
    
    # 主鍵
    id = Column(Integer, primary_key=True, index=True, comment="Signal Log ID")
    
    # Signal 資訊（保留 bot_key 以兼容舊格式）
    bot_key = Column(String(100), nullable=True, index=True, comment="對應的 Bot Key（舊格式兼容），例如 btc_short_v1")
    signal_id = Column(Integer, ForeignKey("tv_signal_configs.id"), nullable=True, index=True, comment="關聯的 Signal Config ID（新格式）")
    signal = relationship("TVSignalConfig", foreign_keys=[signal_id])
    
    symbol = Column(String(50), nullable=False, index=True, comment="交易對，例如 BTCUSDT")
    side = Column(String(10), nullable=False, comment="交易方向：BUY 或 SELL")
    qty = Column(Float, nullable=False, comment="交易數量")
    position_size = Column(Float, nullable=True, comment="目標倉位大小（位置導向模式）。>0=多倉，<0=空倉，0=平倉")
    
    # 原始資料與處理狀態
    raw_body = Column(JSON, nullable=True, comment="完整的原始 payload（JSON 格式）")
    raw_payload = Column(Text, nullable=True, comment="完整的原始 payload（JSON 字串），用於 debug")
    received_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, comment="接收時間")
    processed = Column(Boolean, default=False, nullable=False, index=True, comment="是否已嘗試交給 Bot 處理")
    process_result = Column(String(255), nullable=True, comment="處理結果簡短描述，例如 OK: bot_ids=[1,2], position_ids=[10,11]")
    
    def __repr__(self):
        """字串表示，方便除錯"""
        return f"<TradingViewSignalLog(id={self.id}, bot_key={self.bot_key}, signal_id={self.signal_id}, symbol={self.symbol}, side={self.side}, processed={self.processed})>"
    
    def to_dict(self):
        """將模型轉換為字典"""
        return {
            "id": self.id,
            "bot_key": self.bot_key,
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.qty,
            "position_size": self.position_size,
            "raw_body": self.raw_body,
            "raw_payload": self.raw_payload,
            "received_at": self.received_at.isoformat() if self.received_at else None,
            "processed": self.processed,
            "process_result": self.process_result,
        }


class BotConfig(Base):
    """
    Bot 設定模型
    
    定義每個 Bot 的設定。
    當 TradingView 傳送 signal 時，系統會根據 signal_key 查找對應的 Signal Config，
    然後找到所有 enabled=True 且 signal_id 匹配的 Bot 設定並執行下單。
    
    保留 bot_key 欄位以兼容舊格式（直接使用 bot_key 的 webhook）。
    """
    
    __tablename__ = "bot_configs"
    
    # 主鍵
    id = Column(Integer, primary_key=True, index=True, comment="Bot ID")
    
    # 基本資訊
    name = Column(String(100), nullable=False, comment="Bot 名稱")
    bot_key = Column(String(100), nullable=False, unique=True, index=True, comment="Bot Key（唯一），保留以兼容舊格式")
    enabled = Column(Boolean, default=True, nullable=False, index=True, comment="是否啟用")
    
    # Signal 關聯（新架構）
    signal_id = Column(Integer, ForeignKey("tv_signal_configs.id"), nullable=True, index=True, comment="關聯的 Signal Config ID，NULL 表示不綁定 Signal（舊 Bot 或獨立 Bot）")
    signal = relationship("TVSignalConfig", foreign_keys=[signal_id], backref="bots")
    
    # 交易相關設定
    symbol = Column(String(50), nullable=False, default="BTCUSDT", comment="交易對，例如 BTCUSDT")
    use_signal_side = Column(Boolean, default=True, nullable=False, comment="是否使用 signal.side，True: 使用 signal.side，False: 使用 fixed_side")
    fixed_side = Column(String(10), nullable=True, comment="固定交易方向，當 use_signal_side=False 時使用，例如 BUY 或 SELL")
    qty = Column(Float, nullable=False, default=0.01, comment="交易數量（當 max_invest_usdt 為 NULL 時使用）")
    max_invest_usdt = Column(Float, nullable=True, comment="最大投資金額（USDT），如果設定則自動計算 qty = max_invest_usdt / entry_price")
    leverage = Column(Integer, nullable=False, default=20, comment="杠桿倍數")
    
    # Trailing / Stop 設定
    use_dynamic_stop = Column(Boolean, default=True, nullable=False, comment="是否使用 Dynamic Stop")
    trailing_callback_percent = Column(Float, nullable=True, comment="追蹤停損回調百分比，0~100，例如 1.0 代表 1%")
    base_stop_loss_pct = Column(Float, nullable=False, default=3.0, comment="基礎停損距離 (%)")
    
    # 交易模式設定
    trading_mode = Column(String(20), default="auto", nullable=False, comment="交易模式：auto（自動執行）, semi-auto（需使用者批准）, manual（僅手動）")
    
    # 時間戳記
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, comment="建立時間")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False, comment="更新時間")
    
    def __repr__(self):
        """字串表示，方便除錯"""
        return f"<BotConfig(id={self.id}, name={self.name}, bot_key={self.bot_key}, enabled={self.enabled})>"
    
    def to_dict(self):
        """將模型轉換為字典"""
        return {
            "id": self.id,
            "name": self.name,
            "bot_key": self.bot_key,
            "enabled": self.enabled,
            "symbol": self.symbol,
            "use_signal_side": self.use_signal_side,
            "fixed_side": self.fixed_side,
            "qty": self.qty,
            "max_invest_usdt": self.max_invest_usdt,
            "leverage": self.leverage,
            "use_dynamic_stop": self.use_dynamic_stop,
            "trailing_callback_percent": self.trailing_callback_percent,
            "base_stop_loss_pct": self.base_stop_loss_pct,
            "trading_mode": self.trading_mode,
            "signal_id": self.signal_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class PendingOrder(Base):
    """
    待批准訂單模型
    
    當 Bot 的 trading_mode 為 'semi-auto' 時，收到的交易訊號會先存入此表等待使用者批准。
    """
    
    __tablename__ = "pending_orders"
    
    # 主鍵
    id = Column(Integer, primary_key=True, index=True, comment="Pending Order ID")
    
    # Bot 和 Signal 關聯
    bot_id = Column(Integer, ForeignKey("bot_configs.id"), nullable=False, index=True, comment="關聯的 Bot ID")
    bot = relationship("BotConfig", foreign_keys=[bot_id])
    tv_signal_log_id = Column(Integer, ForeignKey("tv_signal_logs.id"), nullable=False, index=True, comment="關聯的 TradingView Signal Log ID")
    signal_log = relationship("TradingViewSignalLog", foreign_keys=[tv_signal_log_id])
    
    # 交易資訊（從 signal 中提取，用於顯示）
    symbol = Column(String(20), nullable=False, index=True, comment="交易對，例如：BTCUSDT")
    side = Column(String(10), nullable=False, comment="交易方向：BUY 或 SELL（訂單導向）或 LONG/SHORT（位置導向）")
    qty = Column(Float, nullable=True, comment="交易數量（如果可計算）")
    position_size = Column(Float, nullable=True, comment="目標倉位大小（位置導向模式），>0=多倉，<0=空倉，0=平倉")
    
    # 預計算的交易參數（用於批准後執行）
    # 這些欄位儲存了執行交易所需的所有資訊
    calculated_qty = Column(Float, nullable=True, comment="計算後的實際交易數量")
    calculated_side = Column(String(10), nullable=True, comment="計算後的實際交易方向（BUY/SELL）")
    is_position_based = Column(Boolean, default=False, nullable=False, comment="是否為位置導向模式")
    
    # 狀態
    # PENDING: 等待批准
    # APPROVED: 已批准，正在執行
    # REJECTED: 已拒絕
    # EXECUTED: 已執行（執行成功）
    # FAILED: 執行失敗
    status = Column(String(20), default="PENDING", nullable=False, index=True, comment="狀態：PENDING, APPROVED, REJECTED, EXECUTED, FAILED")
    
    # 處理資訊
    approved_at = Column(DateTime(timezone=True), nullable=True, comment="批准時間")
    rejected_at = Column(DateTime(timezone=True), nullable=True, comment="拒絕時間")
    executed_at = Column(DateTime(timezone=True), nullable=True, comment="執行時間")
    error_message = Column(Text, nullable=True, comment="錯誤訊息（如果執行失敗）")
    position_id = Column(Integer, nullable=True, comment="執行後建立的 Position ID（如果成功）")
    
    # 時間戳記
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, comment="建立時間（收到訊號時間）")
    
    def __repr__(self):
        """字串表示，方便除錯"""
        return f"<PendingOrder(id={self.id}, bot_id={self.bot_id}, symbol={self.symbol}, side={self.side}, status={self.status})>"
    
    def to_dict(self):
        """將模型轉換為字典"""
        return {
            "id": self.id,
            "bot_id": self.bot_id,
            "tv_signal_log_id": self.tv_signal_log_id,
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.qty,
            "position_size": self.position_size,
            "calculated_qty": self.calculated_qty,
            "calculated_side": self.calculated_side,
            "is_position_based": self.is_position_based,
            "status": self.status,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "rejected_at": self.rejected_at.isoformat() if self.rejected_at else None,
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
            "error_message": self.error_message,
            "position_id": self.position_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class PortfolioTrailingConfig(Base):
    """
    Portfolio Trailing Stop 配置模型
    
    存儲 Portfolio-level trailing stop 的持久化設定。
    使用單例模式（只有一條記錄，id=1）。
    """
    
    __tablename__ = "portfolio_trailing_config"
    
    # 主鍵（固定為 1，單例模式）
    # 注意：不能使用 default=1，需要在創建時明確指定 id=1
    id = Column(Integer, primary_key=True, comment="固定 ID（單例模式），應為 1")
    
    # 配置設定
    enabled = Column(Boolean, default=False, nullable=False, comment="是否啟用自動賣出")
    target_pnl = Column(Float, nullable=True, comment="目標 PnL（USDT），當達到此值時開始追蹤")
    lock_ratio = Column(Float, nullable=True, comment="Lock ratio（0~1），如果 None 則使用全局 lock_ratio")
    
    # 運行時狀態（不持久化，每次重啟後重置）
    # max_pnl_reached 和 last_check_time 仍然保存在內存中
    
    # 時間戳記
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, comment="建立時間")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False, comment="更新時間")
    
    def __repr__(self):
        """字串表示，方便除錯"""
        return f"<PortfolioTrailingConfig(id={self.id}, enabled={self.enabled}, target_pnl={self.target_pnl}, lock_ratio={self.lock_ratio})>"
    
    def to_dict(self):
        """將模型轉換為字典"""
        return {
            "id": self.id,
            "enabled": self.enabled,
            "target_pnl": self.target_pnl,
            "lock_ratio": self.lock_ratio,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

