"""
FastAPI 應用程式入口點

提供 REST API 接收 TradingView webhook，並在幣安期貨測試網下單。
所有訂單都會記錄到資料庫中，方便追蹤和查詢。
"""

from fastapi import FastAPI, HTTPException, Depends, Header, Request, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import delete, and_, or_
from pydantic import BaseModel, Field
from typing import Optional, List
from dataclasses import dataclass
from copy import copy
import os
import asyncio
import logging
from logging.handlers import RotatingFileHandler
import time
import io
import json
import hashlib
from datetime import datetime, timezone, date, timedelta
from fastapi.responses import StreamingResponse
from authlib.integrations.starlette_client import OAuth
from dotenv import load_dotenv

# 載入 .env 檔案中的環境變數（必須在其他模組導入之前）
load_dotenv()

from db import init_db, get_db, SessionLocal
from models import Position, TradingViewSignalLog, BotConfig, TVSignalConfig, PortfolioTrailingConfig
from binance_client import (
    get_client, 
    get_mark_price, 
    open_futures_market_order, 
    close_futures_position,
    get_symbol_info,
    update_all_bots_invest_amount,
    format_quantity
)

# 設定日誌
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE = os.getenv("LOG_FILE", "tvbot.log")

logger = logging.getLogger("tvbot")
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

# 避免重複 handler
if not logger.handlers:
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # console handler
    ch = logging.StreamHandler()
    ch.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # rotating file handler
    fh = RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,  # 5MB
        backupCount=5,             # 保留 5 個舊檔案
        encoding="utf-8",
    )
    fh.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    fh.setFormatter(formatter)
    logger.addHandler(fh)

# ==================== Dynamic Stop 設定 ====================
# 從環境變數讀取 dynamic stop 設定，並提供合理預設值
# TODO: 之後會統一改用 TRAILING_CONFIG，這些變數保留作為向後兼容
DYN_TRAILING_ENABLED = os.getenv("DYN_TRAILING_ENABLED", "1") == "1"
DYN_PROFIT_THRESHOLD_PCT = float(os.getenv("DYN_PROFIT_THRESHOLD_PCT", "1.0"))   # PnL% >= 1% 才啟動鎖利
DYN_LOCK_RATIO_DEFAULT = float(os.getenv("DYN_LOCK_RATIO_DEFAULT", "0.5"))       # 預設鎖一半的利潤 (0.5)
DYN_BASE_SL_PCT = float(os.getenv("DYN_BASE_SL_PCT", "3.0"))                     # 還沒達門檻前，base 停損 -3%

# ==================== 非 bot 創建的 position 歷史最高價格追蹤 ====================
# 用於追蹤非 bot 創建的 position 的歷史最高/最低價格
# key: f"{symbol}|{position_side}" (例如: "BNBUSDT|LONG")
# value: {"highest_price": float, "entry_price": float, "side": str}
# 注意：這個映射只存在於記憶體中，應用重啟後會重置
_non_bot_position_tracking: dict[str, dict] = {}

# ==================== Binance Live Positions 停損配置覆寫 ====================
# 用於存儲 Binance Live Positions 的停損配置覆寫值
# key: f"{symbol}|{position_side}" (例如: "BNBUSDT|LONG")
# value: {"dyn_profit_threshold_pct": float | None, "base_stop_loss_pct": float | None, "trail_callback": float | None}
# 注意：這個映射只存在於記憶體中，應用重啟後會重置
_binance_position_stop_overrides: dict[str, dict] = {}

# ==================== Binance Portfolio Trailing Stop 狀態 ====================
# 用於存儲 Portfolio-level trailing stop 的運行時狀態
# 持久化配置（enabled, target_pnl, lock_ratio）存儲在資料庫中
# 運行時狀態（max_pnl_reached, last_check_time）只存在於記憶體中，應用重啟後會重置
_portfolio_trailing_runtime_state: dict = {
    "max_pnl_reached": None,
    "last_check_time": None
}

# ==================== 風控設定 ====================
# 允許交易的交易對列表
ALLOWED_SYMBOLS = {"BTCUSDT", "ETHUSDT"}

# 每個交易對的最大杠桿倍數
MAX_LEVERAGE_PER_SYMBOL = {
    "BTCUSDT": 20,
    "ETHUSDT": 10
}

# 每個交易對的最大交易數量
MAX_QTY_PER_SYMBOL = {
    "BTCUSDT": 0.05,
    "ETHUSDT": 1
}

# 初始化 FastAPI 應用程式
app = FastAPI(
    title="TradingView Binance Bot",
    description="接收 TradingView webhook 並在幣安期貨測試網下單",
    version="1.0.0"
)

# 設定 Session Middleware（用於 Google OAuth）
# 從環境變數讀取 secret key，用於簽署 session cookie
SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY", "your-secret-key-change-in-production")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET_KEY)

# 初始化 Jinja2 Templates
templates = Jinja2Templates(directory="templates")

# 掛載靜態檔案目錄（CSS、JS）
# 注意：必須在 static 目錄存在的情況下才能掛載
static_dir = "static"
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    logger.info(f"靜態檔案目錄已掛載: /static -> {static_dir}")
else:
    logger.warning(f"靜態檔案目錄不存在: {static_dir}。請確保 static 目錄存在。")

# 設定 Google OAuth
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
ADMIN_GOOGLE_EMAIL = os.getenv("ADMIN_GOOGLE_EMAIL", "")

# 判斷是否啟用 Google OAuth（必須三個環境變數都有值才啟用）
GOOGLE_OAUTH_ENABLED = bool(
    GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and ADMIN_GOOGLE_EMAIL
)

# OAuth 設定
oauth = OAuth(app)
if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
    oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={
            "scope": "openid email profile"
        }
    )

# 記錄 Google OAuth 狀態
if GOOGLE_OAUTH_ENABLED:
    logger.info(f"Google OAuth 已啟用，管理員 email = {ADMIN_GOOGLE_EMAIL}")
else:
    logger.warning("Google OAuth 未設定，啟用『開發模式無登入』，所有使用者視為管理員（勿用於正式環境）")

# 背景任務運行標誌
_trailing_worker_running = False


async def trailing_stop_worker():
    """
    追蹤停損背景任務
    
    每 5 秒檢查一次所有 OPEN 狀態的倉位（包括 bot 創建和非 bot 創建的倉位），
    根據 dynamic stop 邏輯決定是否需要關倉。
    """
    global _trailing_worker_running
    
    logger.info("追蹤停損背景任務已啟動")
    _trailing_worker_running = True
    
    while _trailing_worker_running:
        # 每一輪建立一個新的 DB session
        db = SessionLocal()
        try:
            # 從資料庫找出所有需要檢查的倉位
            # 只檢查 bot_stop_loss_enabled=True 的倉位（如果為 False，則跳過 Bot 內建的停損機制）
            # 只要是 status == "OPEN" 的倉位，就至少要吃 base stop（即使沒有設定 trail_callback）
            # Dynamic Stop 是否啟用由 DYN_TRAILING_ENABLED 和 lock_ratio 來決定
            positions = (
                db.query(Position)
                .filter(Position.status == "OPEN")
                .filter(Position.bot_stop_loss_enabled == True)
                .all()
            )
            
            if positions:
                logger.info(f"檢查 {len(positions)} 個開啟的倉位（bot_stop_loss_enabled=True）")
                # 記錄沒有 lock_ratio 的倉位（只使用 base stop）
                positions_without_lock = [p for p in positions if p.trail_callback is None]
                if positions_without_lock:
                    logger.debug(f"其中 {len(positions_without_lock)} 個倉位沒有設定 lock_ratio，將使用 base stop")
            
            # 對每個 position 進行檢查
            for position in positions:
                try:
                    await check_trailing_stop(position, db)
                except Exception as e:
                    logger.error(f"檢查倉位 {position.id} ({position.symbol}) 時發生錯誤: {e}")
                    # 繼續處理下一個倉位，不要因為單一倉位錯誤而停止整個任務
                    continue
            
            # 檢查 Binance 上的非 bot 創建倉位
            try:
                await check_binance_non_bot_positions(db)
            except Exception as e:
                logger.error(f"檢查 Binance 非 bot 創建倉位時發生錯誤: {e}")
                # 繼續執行，不要因為這個錯誤而停止整個任務
            
            # 檢查 Portfolio Trailing Stop
            try:
                await check_portfolio_trailing_stop(db)
            except Exception as e:
                logger.error(f"檢查 Portfolio Trailing Stop 時發生錯誤: {e}")
                # 繼續執行，不要因為這個錯誤而停止整個任務
        
        except Exception as e:
            logger.error(f"追蹤停損任務執行時發生錯誤: {e}")
            if db:
                db.rollback()
        
        finally:
            # 確保 DB session 一定會關閉
            if db:
                db.close()
        
        # 等待 5 秒後再次執行
        await asyncio.sleep(5)


async def check_binance_non_bot_positions(db: Session):
    """
    檢查 Binance 上的非 bot 創建倉位，並觸發停損（如果滿足條件）
    
    這個函數會：
    1. 從 Binance 獲取所有 open positions
    2. 對於每個 position，檢查是否有對應的資料庫記錄
    3. 如果沒有（非 bot 創建的），使用臨時 Position 對象來檢查停損
    4. 如果觸發停損，直接關閉 Binance 倉位
    """
    try:
        # 嘗試取得 Binance client
        client = get_client()
        
        # 使用 USDT-M Futures position info
        raw_positions = client.futures_position_information()
        
        for item in raw_positions:
            try:
                position_amt = float(item.get("positionAmt", "0") or 0)
            except (ValueError, TypeError):
                position_amt = 0.0
            
            if position_amt == 0:
                continue
            
            # 解析其他欄位
            try:
                entry_price = float(item.get("entryPrice", "0") or 0)
                mark_price = float(item.get("markPrice", "0") or 0)
                unrealized_pnl = float(item.get("unRealizedProfit", "0") or 0)
                leverage = int(float(item.get("leverage", "0") or 0))
            except (ValueError, TypeError) as e:
                logger.warning(f"解析 Binance position 欄位失敗: {item.get('symbol', 'unknown')}, 錯誤: {e}")
                continue
            
            symbol = item.get("symbol", "")
            side_local = "LONG" if position_amt > 0 else "SHORT"
            
            # 查找匹配的本地 Position（最新的 OPEN 倉位）
            local_pos = (
                db.query(Position)
                .filter(
                    Position.symbol == symbol.upper(),
                    Position.side == side_local,
                    Position.status == "OPEN",
                )
                .order_by(Position.id.desc())
                .first()
            )
            
            # 如果找到本地 Position，跳過（已經由 check_trailing_stop 處理）
            if local_pos:
                continue
            
            # 這是非 bot 創建的倉位，需要檢查停損
            tracking_key = f"{symbol}|{side_local}"
            override_key = f"{symbol}|{side_local}"
            overrides = _binance_position_stop_overrides.get(override_key, {})
            
            # 檢查是否已有追蹤記錄
            if tracking_key in _non_bot_position_tracking:
                tracked = _non_bot_position_tracking[tracking_key]
                tracked_entry = tracked.get("entry_price")
                tracked_highest = tracked.get("highest_price")
                
                # 如果 entry_price 改變，重置追蹤
                if tracked_entry is None or (abs(tracked_entry - entry_price) / max(abs(tracked_entry), abs(entry_price), 1.0)) > 0.001:
                    tracked_entry = entry_price
                    tracked_highest = None
            else:
                tracked_entry = entry_price
                tracked_highest = None
            
            # 更新歷史最高/最低價格
            if side_local == "LONG":
                if tracked_highest is None:
                    tracked_highest = max(mark_price, entry_price) if entry_price > 0 else mark_price
                else:
                    tracked_highest = max(tracked_highest, mark_price)
            else:
                if tracked_highest is None:
                    tracked_highest = min(mark_price, entry_price) if entry_price > 0 else mark_price
                else:
                    tracked_highest = min(tracked_highest, mark_price)
            
            if tracked_highest is None:
                tracked_highest = mark_price
            
            # 更新追蹤記錄
            _non_bot_position_tracking[tracking_key] = {
                "entry_price": tracked_entry,
                "highest_price": tracked_highest,
                "side": side_local
            }
            
            # 建立臨時 Position 物件
            class TempPosition:
                def __init__(self, entry_price, side, highest_price=None):
                    self.entry_price = entry_price
                    self.side = side
                    self.highest_price = highest_price
                    self.trail_callback = overrides.get("trail_callback")
                    self.dyn_profit_threshold_pct = overrides.get("dyn_profit_threshold_pct")
                    self.base_stop_loss_pct = overrides.get("base_stop_loss_pct")
                    self.symbol = symbol
            
            temp_pos = TempPosition(
                entry_price=tracked_entry if tracked_entry else entry_price,
                side=side_local,
                highest_price=tracked_highest
            )
            
            # 計算 unrealized_pnl_pct
            calculated_unrealized_pnl_pct = None
            if entry_price > 0 and abs(position_amt) > 0 and leverage > 0:
                notional = abs(position_amt) * entry_price
                if notional > 0:
                    margin = notional / leverage
                    if margin > 0:
                        calculated_unrealized_pnl_pct = (unrealized_pnl / margin) * 100.0
            
            # 使用 compute_stop_state 計算停損狀態（傳入 leverage 和 qty 用於 margin-based base stop 計算）
            stop_state = compute_stop_state(temp_pos, mark_price, calculated_unrealized_pnl_pct, leverage, abs(position_amt))
            
            # 根據 stop_mode 選擇對應的停損價格
            triggered = False
            mode = None
            dyn_stop = None
            
            if stop_state.stop_mode == "dynamic":
                dyn_stop = stop_state.dynamic_stop_price
                if dyn_stop is not None:
                    if side_local == "LONG":
                        triggered = mark_price <= dyn_stop
                    else:  # SHORT
                        triggered = mark_price >= dyn_stop
                    mode = "dynamic_trailing"
            elif stop_state.stop_mode == "base":
                dyn_stop = stop_state.base_stop_price
                if dyn_stop is not None:
                    if side_local == "LONG":
                        triggered = mark_price <= dyn_stop
                    else:  # SHORT
                        triggered = mark_price >= dyn_stop
                    mode = "base_stop"
                else:
                    logger.warning(
                        f"非 bot 創建倉位 {symbol} ({side_local}) base mode 但 base_stop_price 為 None"
                    )
            
            # 記錄檢查結果
            if dyn_stop is not None:
                logger.info(
                    f"非 bot 創建倉位 {symbol} ({side_local}) 停損檢查："
                    f"mark={mark_price:.6f}, dyn_stop={dyn_stop:.6f}, "
                    f"stop_mode={stop_state.stop_mode}, triggered={triggered}, mode={mode}"
                )
            elif stop_state.stop_mode == "base":
                logger.warning(
                    f"非 bot 創建倉位 {symbol} ({side_local}) base mode 但 dyn_stop 為 None, "
                    f"base_stop_price={stop_state.base_stop_price}"
                )
            
            # 如果觸發停損，關閉倉位
            if triggered:
                logger.info(
                    f"非 bot 創建倉位 {symbol} ({side_local}) 觸發 {mode}，"
                    f"目前價格: {mark_price}, 停損線: {dyn_stop}"
                )
                
                # auto_close_enabled 始終啟用（強制）
                # 呼叫關倉函式
                try:
                    close_order = close_futures_position(
                        symbol=symbol,
                        position_side=side_local,
                        qty=abs(position_amt),
                        position_id=None  # 非 bot 創建的倉位沒有 position_id
                    )
                    
                    logger.info(
                        f"非 bot 創建倉位 {symbol} ({side_local}) 已關倉，"
                        f"order_id={close_order.get('orderId', 'unknown')}"
                    )
                    
                    # 取得平倉價格
                    exit_price = get_exit_price_from_order(close_order, symbol)
                    
                    # 建立 Position 記錄（用於統計計算）
                    position = Position(
                        bot_id=None,  # 非 bot 創建的倉位
                        tv_signal_log_id=None,  # 非 bot 創建的倉位
                        symbol=symbol.upper(),
                        side=side_local,
                        qty=abs(position_amt),
                        entry_price=tracked_entry if tracked_entry else entry_price,
                        exit_price=exit_price,
                        status="CLOSED",
                        closed_at=datetime.now(timezone.utc),
                        exit_reason=mode,  # base_stop 或 dynamic_trailing
                        binance_order_id=int(close_order.get("orderId")) if close_order.get("orderId") else None,
                        client_order_id=close_order.get("clientOrderId"),
                        # 記錄停損相關配置（用於追蹤）
                        trail_callback=overrides.get("trail_callback"),
                        dyn_profit_threshold_pct=overrides.get("dyn_profit_threshold_pct"),
                        base_stop_loss_pct=overrides.get("base_stop_loss_pct"),
                        highest_price=tracked_highest if tracked_highest else None,
                    )
                    
                    db.add(position)
                    db.commit()
                    db.refresh(position)
                    
                    logger.info(
                        f"非 bot 創建倉位 {symbol} ({side_local}) 已建立資料庫記錄 "
                        f"(position_id={position.id}, exit_reason={mode}, exit_price={exit_price})"
                    )
                    
                    # 清理追蹤記錄
                    if tracking_key in _non_bot_position_tracking:
                        del _non_bot_position_tracking[tracking_key]
                        logger.debug(f"清理非 bot 倉位追蹤記錄: {tracking_key}")
                    
                except Exception as e:
                    logger.error(f"關閉非 bot 創建倉位 {symbol} ({side_local}) 失敗: {e}")
                    db.rollback()
                    # 繼續處理下一個倉位
                    continue
                    
    except Exception as e:
        logger.error(f"檢查 Binance 非 bot 創建倉位時發生錯誤: {e}")
        # 不要拋出異常，讓主循環繼續運行


async def check_portfolio_trailing_stop(db: Session):
    """
    檢查 Portfolio-level Trailing Stop
    
    邏輯：
    1. 計算所有 Binance Live Positions 的總 PnL
    2. 如果 enabled=True 且 target_pnl 已設定：
       - 如果總 PnL >= target_pnl 且 max_pnl_reached 為 None，記錄 max_pnl_reached
       - 如果總 PnL >= target_pnl，更新 max_pnl_reached（只增不減）
       - 如果 max_pnl_reached 已記錄，計算 sell_threshold = max_pnl_reached * lock_ratio
       - 如果總 PnL <= sell_threshold，觸發自動賣出所有倉位
    """
    global _portfolio_trailing_runtime_state
    
    # 從資料庫載入配置
    config = db.query(PortfolioTrailingConfig).filter(PortfolioTrailingConfig.id == 1).first()
    if not config or not config.enabled:
        return
    
    target_pnl = config.target_pnl
    if target_pnl is None:
        return
    
    try:
        client = get_client()
        raw_positions = client.futures_position_information()
        
        # 計算總 PnL
        total_pnl = 0.0
        for item in raw_positions:
            try:
                position_amt = float(item.get("positionAmt", "0") or 0)
            except (ValueError, TypeError):
                continue
            
            if position_amt == 0:
                continue
            
            try:
                unrealized_pnl = float(item.get("unRealizedProfit", "0") or 0)
                total_pnl += unrealized_pnl
            except (ValueError, TypeError):
                continue
        
        max_pnl_reached = _portfolio_trailing_runtime_state.get("max_pnl_reached")
        
        # 如果達到目標，記錄或更新 max_pnl_reached
        if total_pnl >= target_pnl:
            if max_pnl_reached is None or total_pnl > max_pnl_reached:
                _portfolio_trailing_runtime_state["max_pnl_reached"] = total_pnl
                max_pnl_reached = total_pnl
                logger.info(
                    f"[Portfolio Trailing] 總 PnL 達到目標 {target_pnl}，記錄最大 PnL: {max_pnl_reached}"
                )
        
        # 如果已記錄 max_pnl_reached，檢查是否需要賣出
        if max_pnl_reached is not None:
            # 取得有效的 lock_ratio（portfolio 設定優先，否則使用全局）
            lock_ratio = config.lock_ratio
            if lock_ratio is None:
                lock_ratio = TRAILING_CONFIG.lock_ratio if TRAILING_CONFIG.lock_ratio is not None else DYN_LOCK_RATIO_DEFAULT
            
            # 計算賣出門檻
            sell_threshold = max_pnl_reached * lock_ratio
            
            if total_pnl <= sell_threshold:
                logger.warning(
                    f"[Portfolio Trailing] 總 PnL ({total_pnl:.2f}) 已降至賣出門檻 ({sell_threshold:.2f} = "
                    f"{max_pnl_reached:.2f} × {lock_ratio})，開始關閉所有倉位"
                )
                
                # 關閉所有倉位
                closed_count = 0
                errors = []
                
                for item in raw_positions:
                    try:
                        position_amt = float(item.get("positionAmt", "0") or 0)
                    except (ValueError, TypeError):
                        continue
                    
                    if position_amt == 0:
                        continue
                    
                    symbol = item.get("symbol", "")
                    if not symbol:
                        continue
                    
                    position_side = "LONG" if position_amt > 0 else "SHORT"
                    
                    try:
                        side = "SELL" if position_side == "LONG" else "BUY"
                        qty = abs(position_amt)
                        
                        timestamp = int(time.time() * 1000)
                        client_order_id = f"TVBOT_PORTFOLIO_TRAILING_{timestamp}_{closed_count}"
                        
                        logger.info(f"[Portfolio Trailing] 關閉 {symbol} {position_side}，數量: {qty}")
                        
                        order = client.futures_create_order(
                            symbol=symbol,
                            side=side,
                            type="MARKET",
                            quantity=qty,
                            reduceOnly=True,
                            newClientOrderId=client_order_id
                        )
                        
                        closed_count += 1
                        logger.info(
                            f"[Portfolio Trailing] 成功關閉 {symbol} {position_side}，"
                            f"訂單ID: {order.get('orderId')}"
                        )
                    except Exception as e:
                        error_msg = f"{symbol} {position_side}: {str(e)}"
                        errors.append(error_msg)
                        logger.error(f"[Portfolio Trailing] 關閉 {symbol} {position_side} 失敗: {e}")
                
                # 重置 max_pnl_reached（所有倉位已關閉）
                _portfolio_trailing_runtime_state["max_pnl_reached"] = None
                
                logger.info(
                    f"[Portfolio Trailing] 自動賣出完成：關閉 {closed_count} 個倉位，"
                    f"錯誤: {len(errors)}"
                )
                if errors:
                    logger.error(f"[Portfolio Trailing] 關閉倉位時的錯誤: {errors}")
        
        # 更新最後檢查時間
        _portfolio_trailing_runtime_state["last_check_time"] = time.time()
        
    except Exception as e:
        logger.error(f"[Portfolio Trailing] 檢查時發生錯誤: {e}", exc_info=True)


def get_exit_price_from_order(close_order: dict, symbol: str) -> float:
    """
    從關倉訂單回傳中取得平倉價格
    
    優先順序：
    1. close_order.get("avgPrice") - 平均成交價格（存在且非空字串且 > 0）
    2. 查詢訂單詳情取得 avgPrice（如果訂單 ID 存在）
    3. close_order.get("price") - 訂單價格（存在且 > 0）
    4. get_mark_price(symbol) - 標記價格（fallback）
    
    此函式保證一定會回傳一個 float 值，不會拋出例外。
    
    Args:
        close_order: 幣安 API 回傳的關倉訂單資訊
        symbol: 交易對
    
    Returns:
        float: 平倉價格
    """
    try:
        # 優先使用 avgPrice（平均成交價格）- 這是 Binance 的實際成交平均價格
        # 檢查存在且不是空字串、不是 "0"、且 > 0
        avg_price = close_order.get("avgPrice")
        if avg_price is not None and avg_price != "" and str(avg_price).strip() != "0":
            try:
                avg_price_float = float(avg_price)
                if avg_price_float > 0:
                    logger.info(f"從訂單回傳取得 {symbol} 平倉價格 (avgPrice): {avg_price_float}")
                    return avg_price_float
            except (ValueError, TypeError):
                pass
        
        # 如果訂單回傳中沒有有效的 avgPrice，嘗試查詢訂單詳情
        order_id = close_order.get("orderId")
        if order_id:
            try:
                client = get_client()
                # 等待一小段時間確保訂單已成交
                time.sleep(0.3)
                # 查詢訂單詳情
                order_detail = client.futures_get_order(symbol=symbol, orderId=order_id)
                avg_price_detail = order_detail.get("avgPrice")
                if avg_price_detail:
                    try:
                        avg_price_float = float(avg_price_detail)
                        if avg_price_float > 0:
                            logger.info(f"從訂單詳情取得 {symbol} 平倉價格 (avgPrice): {avg_price_float}")
                            return avg_price_float
                    except (ValueError, TypeError):
                        pass
            except Exception as e:
                logger.debug(f"查詢訂單 {order_id} 詳情時發生錯誤: {e}")
        
        # 其次使用 price（訂單價格）
        price = close_order.get("price")
        if price is not None:
            try:
                price_float = float(price)
                if price_float > 0:
                    return price_float
            except (ValueError, TypeError):
                pass
        
        # 如果都沒有，使用標記價格作為 fallback
        logger.warning(f"無法從訂單中取得平倉價格，使用 {symbol} 標記價格作為 fallback")
        return get_mark_price(symbol)
    
    except (ValueError, TypeError) as e:
        # 如果轉換 float 失敗，使用標記價格作為 fallback
        logger.warning(
            f"從訂單中解析平倉價格時發生錯誤: {e}，"
            f"使用 {symbol} 標記價格作為 fallback"
        )
        return get_mark_price(symbol)
    
    except Exception as e:
        # 其他未預期的錯誤（例如 get_mark_price 失敗）
        # 記錄錯誤並回傳一個預設值，確保函式不會崩潰
        logger.error(
            f"取得平倉價格時發生未預期錯誤: {e}，"
            f"使用 {symbol} 標記價格作為 fallback"
        )
        try:
            return get_mark_price(symbol)
        except Exception:
            # 如果連標記價格都取得失敗，回傳 0.0 作為最後的 fallback
            logger.critical(
                f"無法取得 {symbol} 標記價格，回傳 0.0 作為預設值。"
                f"這表示幣安 API 連線可能有問題。"
            )
            return 0.0


@dataclass
class StopState:
    """停損狀態資訊"""
    stop_mode: str  # "dynamic", "base", "none", "dynamic_locked"
    base_stop_price: Optional[float]
    dynamic_stop_price: Optional[float]


def compute_stop_state(position: Position, mark_price: float, unrealized_pnl_pct: Optional[float] = None, leverage: Optional[int] = None, qty: Optional[float] = None) -> StopState:
    """
    計算倉位的停損狀態（純計算函數，不修改 DB 或下單）
    
    給定一個 Position 和當前標記價格，計算：
    - 當前生效的停損模式
    - base_stop_price 和 dynamic_stop_price
    
    此函數不會修改資料庫或下單，只進行計算。
    
    Args:
        position: Position 模型實例
        mark_price: 當前標記價格
        unrealized_pnl_pct: 未實現盈虧百分比（PnL%，基於 margin 計算），如果為 None 則使用價格百分比計算
        leverage: 杠桿倍數，用於計算基於 margin 的 Base SL%。如果為 None，則使用預設值 20
        qty: 倉位數量，用於計算 margin。如果為 None，則嘗試從 position.qty 獲取
    
    Returns:
        StopState: 停損狀態資訊
    """
    try:
        entry = position.entry_price
        best = position.highest_price  # LONG: 最高價; SHORT: 最低價
        mark = mark_price
        
        # 若 entry <= 0，返回 none
        if entry <= 0:
            return StopState(
                stop_mode="none",
                base_stop_price=None,
                dynamic_stop_price=None
            )
        
        # 獲取 qty 和 leverage（用於計算 margin-based base stop price）
        position_qty = qty if qty is not None else getattr(position, 'qty', 0)
        position_leverage = leverage if leverage is not None else 20  # 默認杠桿
        
        # 根據倉位方向取得對應的全局設定
        side_config = TRAILING_CONFIG.get_config_for_side(position.side)
        trailing_enabled = TRAILING_CONFIG.trailing_enabled if TRAILING_CONFIG.trailing_enabled is not None else DYN_TRAILING_ENABLED
        
        # 使用對應方向的全局設定作為默認值，如果沒有則使用環境變數
        base_sl_pct_default = side_config.base_sl_pct if side_config.base_sl_pct is not None else DYN_BASE_SL_PCT
        profit_threshold_pct_default = side_config.profit_threshold_pct if side_config.profit_threshold_pct is not None else DYN_PROFIT_THRESHOLD_PCT
        
        # 優先使用倉位覆寫值，如果沒有則使用全局配置
        if position.base_stop_loss_pct is not None:
            base_sl_pct = position.base_stop_loss_pct
        else:
            base_sl_pct = base_sl_pct_default
        
        if position.dyn_profit_threshold_pct is not None:
            profit_threshold_pct = position.dyn_profit_threshold_pct
        else:
            profit_threshold_pct = profit_threshold_pct_default
        
        # 先決定這筆單使用的 lock_ratio
        # trail_callback: null → 使用全局配置, 0 → base stop only, >0 → 使用該值作為 lock_ratio
        # 使用 getattr 安全地訪問屬性（支持 TempPosition 和 Position 對象）
        trail_callback_override = getattr(position, 'trail_callback', None)
        if trail_callback_override is None:
            # 使用對應方向的 TRAILING_CONFIG lock_ratio（如果有的話），否則使用預設值
            lock_ratio = side_config.lock_ratio if side_config.lock_ratio is not None else DYN_LOCK_RATIO_DEFAULT
        elif trail_callback_override == 0:
            lock_ratio = None
        else:
            lock_ratio = trail_callback_override
        
        # 範圍防呆
        if lock_ratio is not None:
            if lock_ratio <= 0:
                lock_ratio = None
            elif lock_ratio > 1:
                lock_ratio = 1.0
        
        # 處理 LONG 倉位
        if position.side == "LONG":
            # 如果 best 為 None，使用當前價格
            if best is None:
                best = mark
            
            # 更新 best（僅用於計算，不修改 DB）
            # 重要：best 只能上升，不能下降，這樣 dynamic stop 才能保持穩定
            # 這是 trailing stop 的核心邏輯：基於歷史最高價格計算停損
            if mark > best:
                best = mark
            
            # profit_pct 基於歷史最高價格（best）計算，而不是當前價格（mark）
            # 這樣即使當前價格下跌，只要歷史最高 profit_pct >= threshold，就會保持在 dynamic mode
            profit_pct = (best - entry) / entry * 100.0 if entry > 0 else 0.0
            
            # 計算 base stop price（基於 margin）
            # 根據需求：Stop price = best - (Margin * Base SL% / 100) / qty
            # Margin = (Entry Price * Qty) / Leverage
            # Base SL Amount (USDT) = Margin * (Base SL% / 100.0)
            # 對於 LONG: stop_price = best - (margin * base_sl_pct / 100) / qty
            base_stop_price = None
            if base_sl_pct > 0 and entry > 0 and position_qty > 0 and position_leverage > 0:
                # 計算 margin（使用 entry price 計算 margin）
                notional = entry * position_qty
                margin = notional / position_leverage
                # 使用 best 價格（如果 best 為 None，使用 entry）
                best_price = best if best is not None else entry
                # Base stop price = best - (margin * base_sl_pct / 100) / qty
                base_stop_price = best_price - (margin * base_sl_pct / 100.0) / position_qty
            
            # 計算 dynamic stop price
            dynamic_stop_price = None
            stop_mode = "none"
            
            # 檢查是否有覆寫值（用於決定是否啟用停損）
            dyn_profit_threshold_pct_override = getattr(position, 'dyn_profit_threshold_pct', None)
            base_stop_loss_pct_override = getattr(position, 'base_stop_loss_pct', None)
            has_override = (
                trail_callback_override is not None or 
                dyn_profit_threshold_pct_override is not None or 
                base_stop_loss_pct_override is not None
            )
            # 如果有覆寫值，即使全局 trailing_enabled 為 False，也應該啟用停損
            effective_trailing_enabled = trailing_enabled or (has_override and lock_ratio is not None)
            
            # 關鍵邏輯：一旦進入 dynamic mode，就應該保持在 dynamic mode
            # 判斷是否應該進入或保持在 dynamic mode：
            # 1. 如果 best（歷史最高價格）曾經達到過 threshold，就應該保持在 dynamic mode
            # 2. 使用基於 best 的 PnL% 來判斷，而不是當前價格
            
            # 計算基於 best（歷史最高價格）的 profit_pct（價格百分比）
            profit_pct_based_on_best = profit_pct  # 已經基於 best 計算
            
            # 如果提供了 unrealized_pnl_pct，我們需要計算基於 best 的 PnL%
            # 基於 best 的 PnL% = (best - entry) / entry * 100 * (entry * qty / leverage) / (entry * qty / leverage)
            # 簡化後：基於 best 的 PnL% = (best - entry) / entry * 100 * leverage / leverage
            # 實際上，PnL% 和價格百分比的比例是固定的（都基於相同的 margin）
            # 所以：基於 best 的 PnL% = unrealized_pnl_pct * (best - entry) / (mark - entry)
            if unrealized_pnl_pct is not None:
                # 使用當前 PnL% 來判斷是否進入 dynamic mode
                profit_pct_for_threshold = unrealized_pnl_pct
                # 計算基於 best 的 PnL%，用於判斷是否應該保持在 dynamic mode
                # 如果 mark != entry，使用比例計算；否則使用當前 PnL%
                if mark != entry and entry > 0:
                    # 計算基於 best 的 unrealized PnL（相對於 entry）
                    if position.side == "LONG":
                        best_unrealized_pnl_ratio = (best - entry) / (mark - entry) if (mark - entry) != 0 else 1.0
                    else:  # SHORT
                        best_unrealized_pnl_ratio = (entry - best) / (entry - mark) if (entry - mark) != 0 else 1.0
                    # 基於 best 的 PnL% = 當前 PnL% * (best 相對於 entry 的獲利比例) / (mark 相對於 entry 的獲利比例)
                    profit_pct_based_on_best_for_threshold = unrealized_pnl_pct * best_unrealized_pnl_ratio
                else:
                    # 如果 mark == entry，使用當前 PnL%（此時 best 應該也等於 entry）
                    profit_pct_based_on_best_for_threshold = unrealized_pnl_pct
            else:
                # Fallback：使用價格百分比（基於歷史最高價格）
                profit_pct_for_threshold = profit_pct
                profit_pct_based_on_best_for_threshold = profit_pct_based_on_best
            
            # 調試日誌：記錄停損模式判斷的關鍵參數
            logger.info(
                f"compute_stop_state LONG: symbol={getattr(position, 'symbol', 'unknown')}, "
                f"entry={entry:.4f}, best={best:.4f}, mark={mark:.4f}, "
                f"profit_pct(price)={profit_pct:.2f}%, unrealized_pnl_pct={unrealized_pnl_pct}, "
                f"profit_pct_for_threshold={profit_pct_for_threshold:.2f}%, profit_pct_based_on_best={profit_pct_based_on_best:.2f}%, "
                f"profit_threshold_pct={profit_threshold_pct:.2f}%, "
                f"trailing_enabled={trailing_enabled}, effective_trailing_enabled={effective_trailing_enabled}, "
                f"lock_ratio={lock_ratio}, has_override={has_override}, "
                f"trail_callback_override={trail_callback_override}"
            )
            
            # Case 1：Dynamic Trailing（鎖利）
            # 關鍵：一旦 best 曾經達到過 threshold，就應該保持在 dynamic mode
            # 使用基於 best 的 profit_pct 來判斷，而不是當前價格
            # 這樣即使當前價格下跌，只要 best 曾經達到過 threshold，就會保持在 dynamic mode
            if (
                effective_trailing_enabled
                and lock_ratio is not None
                and profit_pct_based_on_best_for_threshold >= profit_threshold_pct
            ):
                # dynamic_stop_price 基於 best（歷史最高價格）計算，永遠不會下降
                dynamic_stop_price = entry + (best - entry) * lock_ratio
                stop_mode = "dynamic"
                logger.info(f"✓ 進入 Dynamic 模式: dynamic_stop_price={dynamic_stop_price:.4f}, best={best:.4f}, entry={entry:.4f}, lock_ratio={lock_ratio}")
            else:
                # 記錄為什麼沒有進入 dynamic mode
                reasons = []
                if not effective_trailing_enabled:
                    reasons.append(f"effective_trailing_enabled=False (trailing_enabled={trailing_enabled}, has_override={has_override})")
                if lock_ratio is None:
                    reasons.append(f"lock_ratio=None (trail_callback_override={trail_callback_override})")
                if profit_pct_for_threshold < profit_threshold_pct:
                    reasons.append(f"profit_pct_for_threshold({profit_pct_for_threshold:.2f}%) < threshold({profit_threshold_pct:.2f}%)")
                logger.info(f"✗ 未進入 Dynamic 模式: {', '.join(reasons)}")
            
            # Case 2：Base Stop-Loss（只有當從未進入過 dynamic mode 時才顯示 base stop）
            # 一旦進入 dynamic mode，就不會返回 base mode
            # 如果有覆寫值，即使從未達到 threshold，也應該顯示 base stop
            if stop_mode != "dynamic" and base_sl_pct > 0 and (profit_pct_based_on_best_for_threshold < profit_threshold_pct or has_override):
                stop_mode = "base"
                logger.debug(f"保持在 Base 模式: profit_pct_based_on_best={profit_pct_based_on_best:.2f}% < threshold={profit_threshold_pct:.2f}% 或 has_override={has_override}")
            
            return StopState(
                stop_mode=stop_mode,
                base_stop_price=base_stop_price,
                dynamic_stop_price=dynamic_stop_price
            )
        
        # 處理 SHORT 倉位
        elif position.side == "SHORT":
            # 如果 best 為 None，使用當前價格
            if best is None:
                best = mark
            
            # 更新 best（僅用於計算，不修改 DB）
            # 重要：對於 SHORT，best 是最低價格，只能下降，不能上升
            # 這樣 dynamic stop 才能保持穩定：基於歷史最低價格計算停損
            if mark < best:
                best = mark
            
            # profit_pct 基於歷史最低價格（best）計算，而不是當前價格（mark）
            # 這樣即使當前價格上漲，只要歷史最高 profit_pct >= threshold，就會保持在 dynamic mode
            profit_pct = (entry - best) / entry * 100.0 if entry > 0 else 0.0
            
            # 計算 base stop price（基於 margin）
            # 根據需求：Stop price = best + (Margin * Base SL% / 100) / qty
            # Margin = (Entry Price * Qty) / Leverage
            # Base SL Amount (USDT) = Margin * (Base SL% / 100.0)
            # 對於 SHORT: stop_price = best + (margin * base_sl_pct / 100) / qty
            base_stop_price = None
            if base_sl_pct > 0 and entry > 0 and position_qty > 0 and position_leverage > 0:
                # 計算 margin（使用 entry price 計算 margin）
                notional = entry * position_qty
                margin = notional / position_leverage
                # 使用 best 價格（如果 best 為 None，使用 entry）
                best_price = best if best is not None else entry
                # Base stop price = best + (margin * base_sl_pct / 100) / qty
                base_stop_price = best_price + (margin * base_sl_pct / 100.0) / position_qty
            
            # 計算 dynamic stop price
            dynamic_stop_price = None
            stop_mode = "none"
            
            # 檢查是否有覆寫值（用於決定是否啟用停損）
            dyn_profit_threshold_pct_override = getattr(position, 'dyn_profit_threshold_pct', None)
            base_stop_loss_pct_override = getattr(position, 'base_stop_loss_pct', None)
            has_override = (
                trail_callback_override is not None or 
                dyn_profit_threshold_pct_override is not None or 
                base_stop_loss_pct_override is not None
            )
            # 如果有覆寫值，即使全局 trailing_enabled 為 False，也應該啟用停損
            effective_trailing_enabled = trailing_enabled or (has_override and lock_ratio is not None)
            
            # 關鍵邏輯：一旦進入 dynamic mode，就應該保持在 dynamic mode
            # 判斷是否應該進入或保持在 dynamic mode：
            # 1. 如果 best（歷史最低價格）曾經達到過 threshold，就應該保持在 dynamic mode
            # 2. 使用基於 best 的 PnL% 來判斷，而不是當前價格
            
            # 計算基於 best（歷史最低價格）的 profit_pct（價格百分比）
            profit_pct_based_on_best = profit_pct  # 已經基於 best 計算
            
            # 如果提供了 unrealized_pnl_pct，我們需要計算基於 best 的 PnL%
            # 基於 best 的 PnL% = (entry - best) / entry * 100 * (entry * qty / leverage) / (entry * qty / leverage)
            # 簡化後：基於 best 的 PnL% = (entry - best) / entry * 100 * leverage / leverage
            # 實際上，PnL% 和價格百分比的比例是固定的（都基於相同的 margin）
            # 所以：基於 best 的 PnL% = unrealized_pnl_pct * (entry - best) / (entry - mark)
            if unrealized_pnl_pct is not None:
                # 使用當前 PnL% 來判斷是否進入 dynamic mode
                profit_pct_for_threshold = unrealized_pnl_pct
                # 計算基於 best 的 PnL%，用於判斷是否應該保持在 dynamic mode
                # 如果 mark != entry，使用比例計算；否則使用當前 PnL%
                if mark != entry and entry > 0:
                    # 計算基於 best 的 unrealized PnL（相對於 entry）
                    # 對於 SHORT：best 是最低價格，獲利 = entry - best
                    best_unrealized_pnl_ratio = (entry - best) / (entry - mark) if (entry - mark) != 0 else 1.0
                    # 基於 best 的 PnL% = 當前 PnL% * (best 相對於 entry 的獲利比例) / (mark 相對於 entry 的獲利比例)
                    profit_pct_based_on_best_for_threshold = unrealized_pnl_pct * best_unrealized_pnl_ratio
                else:
                    # 如果 mark == entry，使用當前 PnL%（此時 best 應該也等於 entry）
                    profit_pct_based_on_best_for_threshold = unrealized_pnl_pct
            else:
                # Fallback：使用價格百分比（基於歷史最低價格）
                profit_pct_for_threshold = profit_pct
                profit_pct_based_on_best_for_threshold = profit_pct_based_on_best
            
            # 調試日誌：記錄停損模式判斷的關鍵參數
            logger.info(
                f"compute_stop_state SHORT: symbol={getattr(position, 'symbol', 'unknown')}, "
                f"entry={entry:.4f}, best={best:.4f}, mark={mark:.4f}, "
                f"profit_pct(price)={profit_pct:.2f}%, unrealized_pnl_pct={unrealized_pnl_pct}, "
                f"profit_pct_for_threshold={profit_pct_for_threshold:.2f}%, profit_pct_based_on_best={profit_pct_based_on_best:.2f}%, "
                f"profit_threshold_pct={profit_threshold_pct:.2f}%, "
                f"trailing_enabled={trailing_enabled}, effective_trailing_enabled={effective_trailing_enabled}, "
                f"lock_ratio={lock_ratio}, has_override={has_override}, "
                f"trail_callback_override={trail_callback_override}"
            )
            
            # Case 1：Dynamic Trailing（鎖利）
            # 關鍵：一旦 best 曾經達到過 threshold，就應該保持在 dynamic mode
            # 使用基於 best 的 profit_pct 來判斷，而不是當前價格
            # 這樣即使當前價格上漲，只要 best 曾經達到過 threshold，就會保持在 dynamic mode
            if (
                effective_trailing_enabled
                and lock_ratio is not None
                and profit_pct_based_on_best_for_threshold >= profit_threshold_pct
            ):
                # dynamic_stop_price 基於 best（歷史最低價格）計算，永遠不會上升
                dynamic_stop_price = entry - (entry - best) * lock_ratio
                stop_mode = "dynamic"
                logger.info(f"✓ 進入 Dynamic 模式: dynamic_stop_price={dynamic_stop_price:.4f}, best={best:.4f}, entry={entry:.4f}, lock_ratio={lock_ratio}")
            else:
                # 記錄為什麼沒有進入 dynamic mode
                reasons = []
                if not effective_trailing_enabled:
                    reasons.append(f"effective_trailing_enabled=False (trailing_enabled={trailing_enabled}, has_override={has_override})")
                if lock_ratio is None:
                    reasons.append(f"lock_ratio=None (trail_callback_override={trail_callback_override})")
                if profit_pct_for_threshold < profit_threshold_pct:
                    reasons.append(f"profit_pct_for_threshold({profit_pct_for_threshold:.2f}%) < threshold({profit_threshold_pct:.2f}%)")
                logger.info(f"✗ 未進入 Dynamic 模式: {', '.join(reasons)}")
            
            # Case 2：Base Stop-Loss（只有當從未進入過 dynamic mode 時才顯示 base stop）
            # 一旦進入 dynamic mode，就不會返回 base mode
            # 如果有覆寫值，即使從未達到 threshold，也應該顯示 base stop
            if stop_mode != "dynamic" and base_sl_pct > 0 and (profit_pct_based_on_best_for_threshold < profit_threshold_pct or has_override):
                stop_mode = "base"
            
            return StopState(
                stop_mode=stop_mode,
                base_stop_price=base_stop_price,
                dynamic_stop_price=dynamic_stop_price
            )
        
        # 未知方向
        else:
            return StopState(
                stop_mode="none",
                base_stop_price=None,
                dynamic_stop_price=None
            )
    
    except Exception as e:
        # 安全地獲取 position.id（TempPosition 可能沒有 id 屬性）
        pos_id = getattr(position, 'id', None)
        pos_symbol = getattr(position, 'symbol', 'unknown')
        if pos_id:
            logger.warning(f"計算倉位 {pos_id} ({pos_symbol}) 停損狀態時發生錯誤: {e}")
        else:
            logger.warning(f"計算倉位 ({pos_symbol}) 停損狀態時發生錯誤: {e}")
        return StopState(
            stop_mode="none",
            base_stop_price=None,
            dynamic_stop_price=None
        )


async def check_trailing_stop(position: Position, db: Session):
    """
    檢查單一倉位的 Dynamic Stop（動態停損）
    
    使用 dynamic stop 邏輯：
    1. 當 PnL% 達到門檻時，鎖住一部分利潤作為停損線
    2. 否則使用 base stop-loss（固定百分比停損）
    
    注意：此函數只會在 bot_stop_loss_enabled=True 時被調用。
    
    Args:
        position: Position 模型實例
        db: 資料庫 Session
    """
    # 雙重檢查：如果 bot_stop_loss_enabled 為 False，直接返回
    if not position.bot_stop_loss_enabled:
        logger.debug(f"倉位 {position.id} ({position.symbol}) bot_stop_loss_enabled=False，跳過 Bot 停損檢查")
        return
    
    try:
        # 取得目前標記價格
        current_price = get_mark_price(position.symbol)
        
        # 計算 dynamic stop 所需的共用變數
        entry = position.entry_price
        best = position.highest_price  # LONG: 最高價; SHORT: 最低價
        mark = current_price
        
        # 若 entry <= 0，嘗試從 Binance 查詢實際的 entry price
        if entry <= 0:
            logger.warning(
                f"倉位 {position.id} ({position.symbol}) entry_price={entry} 無效，"
                f"嘗試從 Binance 查詢實際 entry price"
            )
            try:
                client = get_client()
                positions_info = client.futures_position_information(symbol=position.symbol)
                for pos_info in positions_info:
                    position_amt = float(pos_info.get("positionAmt", "0") or 0)
                    if abs(position_amt) < 1e-8:
                        continue
                    
                    # 判斷方向是否匹配
                    side_local = "LONG" if position_amt > 0 else "SHORT"
                    if side_local != position.side:
                        continue
                    
                    # 取得 Binance 的 entry price
                    binance_entry = float(pos_info.get("entryPrice", "0") or 0)
                    if binance_entry > 0:
                        logger.info(
                            f"從 Binance 取得倉位 {position.id} ({position.symbol}) 的 entry_price: {binance_entry}"
                        )
                        position.entry_price = binance_entry
                        entry = binance_entry
                        # 如果 highest_price 也無效，使用當前 mark_price 初始化
                        if best is None or best <= 0:
                            position.highest_price = current_price
                            best = current_price
                        db.commit()
                        break
            except Exception as e:
                logger.error(
                    f"從 Binance 查詢倉位 {position.id} ({position.symbol}) entry_price 失敗: {e}"
                )
                # 如果查詢失敗，仍然跳過停損檢查
                return
        
        # 如果更新後 entry 仍然 <= 0，跳過停損檢查
        if entry <= 0:
            logger.warning(f"倉位 {position.id} ({position.symbol}) entry_price={entry} 仍然無效，跳過停損檢查")
            return
        
        # 計算 unrealized_pnl_pct（PnL%）用於判斷是否進入 dynamic mode
        # PnL% = (Unrealized PnL / 保證金) * 100
        # 保證金 = 名義價值 / 杠桿 = (Entry Price * Qty) / Leverage
        calculated_unrealized_pnl_pct = None
        if entry > 0 and position.qty > 0:
            # 計算 unrealized PnL
            if position.side == "LONG":
                unrealized_pnl_amount = (mark - entry) * position.qty
            else:  # SHORT
                unrealized_pnl_amount = (entry - mark) * position.qty
            # 獲取 leverage（從關聯的 Bot 或使用默認值）
            leverage = 20  # 默認杠桿
            if position.bot_id:
                try:
                    from models import BotConfig
                    bot = db.query(BotConfig).filter(BotConfig.id == position.bot_id).first()
                    if bot and bot.leverage:
                        leverage = bot.leverage
                except Exception as e:
                    logger.debug(f"無法取得倉位 {position.id} 的 Bot leverage: {e}")
            # 計算保證金
            notional = entry * position.qty
            if notional > 0 and leverage > 0:
                margin = notional / leverage
                if margin > 0:
                    calculated_unrealized_pnl_pct = (unrealized_pnl_amount / margin) * 100.0
        
        # 獲取 leverage 和 qty（用於 margin-based base stop 計算）
        leverage_for_stop = 20  # 默認杠桿
        qty_for_stop = getattr(position, 'qty', 0)
        if position.bot_id:
            try:
                from models import BotConfig
                bot = db.query(BotConfig).filter(BotConfig.id == position.bot_id).first()
                if bot and bot.leverage:
                    leverage_for_stop = bot.leverage
            except Exception as e:
                logger.debug(f"無法取得倉位 {position.id} 的 Bot leverage: {e}")
        
        # 使用 compute_stop_state 計算停損狀態（傳入 leverage 和 qty）
        stop_state = compute_stop_state(position, current_price, calculated_unrealized_pnl_pct, leverage_for_stop, qty_for_stop)
        
        # 根據倉位方向取得對應的全局設定
        side_config = TRAILING_CONFIG.get_config_for_side(position.side)
        trailing_enabled = TRAILING_CONFIG.trailing_enabled if TRAILING_CONFIG.trailing_enabled is not None else DYN_TRAILING_ENABLED
        
        # 使用對應方向的全局設定作為默認值，如果沒有則使用環境變數
        base_sl_pct_default = side_config.base_sl_pct if side_config.base_sl_pct is not None else DYN_BASE_SL_PCT
        profit_threshold_pct_default = side_config.profit_threshold_pct if side_config.profit_threshold_pct is not None else DYN_PROFIT_THRESHOLD_PCT
        
        # 優先使用倉位覆寫值，如果沒有則使用全局配置
        if position.base_stop_loss_pct is not None:
            base_sl_pct = position.base_stop_loss_pct
        else:
            base_sl_pct = base_sl_pct_default
        
        if position.dyn_profit_threshold_pct is not None:
            profit_threshold_pct = position.dyn_profit_threshold_pct
        else:
            profit_threshold_pct = profit_threshold_pct_default
        
        # 先決定這筆單使用的 lock_ratio
        # trail_callback: null → 使用全局配置, 0 → base stop only, >0 → 使用該值作為 lock_ratio
        if position.trail_callback is None:
            # 使用對應方向的 TRAILING_CONFIG lock_ratio（如果有的話），否則使用預設值
            lock_ratio = side_config.lock_ratio if side_config.lock_ratio is not None else DYN_LOCK_RATIO_DEFAULT
        elif position.trail_callback == 0:
            logger.info(
                f"倉位 {position.id} ({position.symbol}) trail_callback=0，僅使用 base stop-loss"
            )
            lock_ratio = None
        else:
            # 正常情況，使用每筆倉位自己的 lock_ratio
            lock_ratio = position.trail_callback
        
        # 記錄使用的停損配置值（用於調試和驗證）
        logger.info(
            f"[DynamicStop] pos_id={position.id} symbol={position.symbol} "
            f"profit_threshold={profit_threshold_pct}% "
            f"(override={position.dyn_profit_threshold_pct if position.dyn_profit_threshold_pct is not None else 'global'}) "
            f"lock_ratio={lock_ratio if lock_ratio is not None else 'base-only'} "
            f"(override={position.trail_callback if position.trail_callback is not None else 'global'}) "
            f"base_sl={base_sl_pct}% "
            f"(override={position.base_stop_loss_pct if position.base_stop_loss_pct is not None else 'global'})"
        )
        
        # 之後再做範圍防呆（只對 >0 的 lock_ratio 做 clamp）
        if lock_ratio is not None:
            if lock_ratio <= 0:
                logger.warning(
                    f"倉位 {position.id} ({position.symbol}) lock_ratio <= 0（值={lock_ratio}），忽略 dynamic，改用 base stop"
                )
                lock_ratio = None
            elif lock_ratio > 1:
                logger.warning(
                    f"倉位 {position.id} ({position.symbol}) lock_ratio > 1（值={lock_ratio}），已強制調整為 1.0"
                )
                lock_ratio = 1.0
        
        # 處理 LONG 倉位
        if position.side == "LONG":
            # 更新 highest_price（記錄最高價格）
            if best is None:
                # 如果還沒有設定最高價，設定為目前價格
                position.highest_price = current_price
                logger.info(f"倉位 {position.id} ({position.symbol}) LONG 初始化最高價: {position.highest_price}")
                db.commit()
                return
            elif current_price > best:
                position.highest_price = current_price
                best = current_price  # 更新 best 變數
                logger.info(f"倉位 {position.id} ({position.symbol}) LONG 更新最高價: {best}")
            
            # 計算 unrealized_pnl_pct（PnL%）用於判斷是否進入 dynamic mode
            # PnL% = (Unrealized PnL / 保證金) * 100
            # 保證金 = 名義價值 / 杠桿 = (Entry Price * Qty) / Leverage
            calculated_unrealized_pnl_pct = None
            if entry > 0 and position.qty > 0:
                # 計算 unrealized PnL
                unrealized_pnl_amount = (mark - entry) * position.qty
                # 獲取 leverage（從關聯的 Bot 或使用默認值）
                leverage = 20  # 默認杠桿
                if position.bot_id:
                    try:
                        from models import BotConfig
                        bot = db.query(BotConfig).filter(BotConfig.id == position.bot_id).first()
                        if bot and bot.leverage:
                            leverage = bot.leverage
                    except Exception as e:
                        logger.debug(f"無法取得倉位 {position.id} 的 Bot leverage: {e}")
                # 計算保證金
                notional = entry * position.qty
                if notional > 0 and leverage > 0:
                    margin = notional / leverage
                    if margin > 0:
                        calculated_unrealized_pnl_pct = (unrealized_pnl_amount / margin) * 100.0
            
            # 先更新完 best (= position.highest_price) 之後，使用 compute_stop_state 計算停損狀態（傳入 leverage 和 qty）
            stop_state = compute_stop_state(position, mark, calculated_unrealized_pnl_pct, leverage, position.qty)
            
            # 從 stop_state 取得停損價格和模式
            # 根據 stop_mode 選擇對應的停損價格
            triggered = False
            mode = None
            dyn_stop = None
            
            # 判斷是否觸發停損
            if stop_state.stop_mode == "dynamic":
                # Dynamic mode: 只使用 dynamic_stop_price
                dyn_stop = stop_state.dynamic_stop_price
                if dyn_stop is not None:
                    # LONG: 當價格下跌到 dynamic_stop_price 以下時觸發
                    triggered = mark <= dyn_stop
                    mode = "dynamic_trailing"
            elif stop_state.stop_mode == "base":
                # Base mode: 只使用 base_stop_price
                dyn_stop = stop_state.base_stop_price
                if dyn_stop is not None:
                    # LONG: 當價格下跌到 base_stop_price 以下時觸發
                    triggered = mark <= dyn_stop
                    mode = "base_stop"
                else:
                    logger.warning(
                        f"倉位 {position.id} ({position.symbol}) LONG base mode 但 base_stop_price 為 None"
                    )
            
            profit_pct = (best - entry) / entry * 100.0 if entry > 0 else 0.0
            
            # 如果有計算出 dyn_stop，就寫 info log
            if dyn_stop is not None:
                logger.info(
                    f"倉位 {position.id} ({position.symbol}) LONG 模式: {mode}, "
                    f"目前價格: {mark:.6f}, best: {best:.6f}, dyn_stop: {dyn_stop:.6f}, "
                    f"獲利%: {profit_pct:.2f}, lock_ratio: {lock_ratio}, base_sl_pct: {base_sl_pct}, "
                    f"觸發條件: mark <= dyn_stop ({mark:.6f} <= {dyn_stop:.6f} = {mark <= dyn_stop}), "
                    f"triggered={triggered}, stop_mode={stop_state.stop_mode}, "
                    f"dynamic_stop_price={stop_state.dynamic_stop_price}, base_stop_price={stop_state.base_stop_price}"
                )
            else:
                logger.warning(
                    f"倉位 {position.id} ({position.symbol}) LONG 沒有停損價格！"
                    f"stop_mode={stop_state.stop_mode}, "
                    f"dynamic_stop_price={stop_state.dynamic_stop_price}, "
                    f"base_stop_price={stop_state.base_stop_price}, "
                    f"triggered={triggered}, mode={mode}"
                )
            
            if triggered:
                logger.info(
                    f"倉位 {position.id} ({position.symbol}) LONG 觸發 {mode}，"
                    f"目前價格: {mark}, 停損線: {dyn_stop}, 獲利%: {profit_pct:.2f}"
                )
                
                # auto_close_enabled 始終啟用（強制）
                # 呼叫關倉函式
                try:
                    close_order = close_futures_position(
                        symbol=position.symbol,
                        position_side=position.side,  # "LONG"
                        qty=position.qty,
                        position_id=position.id
                    )
                    
                    # 取得平倉價格
                    exit_price = get_exit_price_from_order(close_order, position.symbol)
                    
                    # 更新倉位狀態與平倉資訊
                    position.status = "CLOSED"
                    position.closed_at = datetime.now(timezone.utc)
                    position.exit_price = exit_price
                    # exit_reason：dynamic_trailing or base_stop
                    position.exit_reason = "dynamic_stop" if mode == "dynamic_trailing" else "base_stop"
                    db.commit()
                    
                    logger.info(
                        f"倉位 {position.id} ({position.symbol}) LONG 已成功關倉（{mode}），"
                        f"訂單 ID: {close_order.get('orderId')}, 平倉價格: {exit_price}"
                    )
                
                except Exception as e:
                    logger.error(f"關閉倉位 {position.id} 失敗: {e}")
                    position.status = "ERROR"
                    db.commit()
                    raise
        
        # 處理 SHORT 倉位
        elif position.side == "SHORT":
            # 對於 SHORT，highest_price 欄位用來記錄最低價格（lowest_price）
            if best is None:
                # 如果還沒有設定最低價，設定為目前價格（第一次記錄）
                position.highest_price = current_price
                best = current_price  # 更新 best 變數
                logger.info(f"倉位 {position.id} ({position.symbol}) SHORT 初始化最低價: {best}")
                db.commit()
                return
            elif current_price < best:
                # 如果目前價格更低，更新為新的最低價（創新低）
                position.highest_price = current_price
                best = current_price  # 更新 best 變數
                logger.info(f"倉位 {position.id} ({position.symbol}) SHORT 更新最低價: {best}")
            
            profit_pct = (entry - best) / entry * 100.0 if entry > 0 else 0.0
            
            # 計算 unrealized_pnl_pct（PnL%）用於判斷是否進入 dynamic mode
            # PnL% = (Unrealized PnL / 保證金) * 100
            # 保證金 = 名義價值 / 杠桿 = (Entry Price * Qty) / Leverage
            calculated_unrealized_pnl_pct = None
            if entry > 0 and position.qty > 0:
                # 計算 unrealized PnL
                unrealized_pnl_amount = (entry - mark) * position.qty  # SHORT: entry - mark
                # 獲取 leverage（從關聯的 Bot 或使用默認值）
                leverage = 20  # 默認杠桿
                if position.bot_id:
                    try:
                        from models import BotConfig
                        bot = db.query(BotConfig).filter(BotConfig.id == position.bot_id).first()
                        if bot and bot.leverage:
                            leverage = bot.leverage
                    except Exception as e:
                        logger.debug(f"無法取得倉位 {position.id} 的 Bot leverage: {e}")
                # 計算保證金
                notional = entry * position.qty
                if notional > 0 and leverage > 0:
                    margin = notional / leverage
                    if margin > 0:
                        calculated_unrealized_pnl_pct = (unrealized_pnl_amount / margin) * 100.0
            
            # 使用 compute_stop_state 計算停損狀態（傳入 leverage 和 qty）
            stop_state = compute_stop_state(position, mark, calculated_unrealized_pnl_pct, leverage, position.qty)
            
            # 從 stop_state 取得停損價格和模式
            # 根據 stop_mode 選擇對應的停損價格
            triggered = False
            mode = None
            dyn_stop = None
            
            # 判斷是否觸發停損
            if stop_state.stop_mode == "dynamic":
                # Dynamic mode: 只使用 dynamic_stop_price
                dyn_stop = stop_state.dynamic_stop_price
                if dyn_stop is not None:
                    # SHORT: 當價格上漲到 dynamic_stop_price 以上時觸發
                    triggered = mark >= dyn_stop
                    mode = "dynamic_trailing"
            elif stop_state.stop_mode == "base":
                # Base mode: 只使用 base_stop_price
                dyn_stop = stop_state.base_stop_price
                if dyn_stop is not None:
                    # SHORT: 當價格上漲到 base_stop_price 以上時觸發
                    triggered = mark >= dyn_stop
                    mode = "base_stop"
            
            profit_pct = (entry - best) / entry * 100.0 if entry > 0 else 0.0
            if dyn_stop is not None:
                logger.info(
                    f"倉位 {position.id} ({position.symbol}) SHORT 模式: {mode}, "
                    f"目前價格: {mark:.6f}, 最低價(best): {best:.6f}, dyn_stop: {dyn_stop:.6f}, "
                    f"獲利%: {profit_pct:.2f}, lock_ratio: {lock_ratio}, base_sl_pct: {base_sl_pct}, "
                    f"觸發條件: mark >= dyn_stop ({mark:.6f} >= {dyn_stop:.6f} = {mark >= dyn_stop}), "
                    f"triggered={triggered}, stop_mode={stop_state.stop_mode}, "
                    f"dynamic_stop_price={stop_state.dynamic_stop_price}, base_stop_price={stop_state.base_stop_price}"
                )
            else:
                logger.warning(
                    f"倉位 {position.id} ({position.symbol}) SHORT 沒有停損價格！"
                    f"stop_mode={stop_state.stop_mode}, "
                    f"dynamic_stop_price={stop_state.dynamic_stop_price}, "
                    f"base_stop_price={stop_state.base_stop_price}"
                )
            
            if triggered:
                logger.info(
                    f"倉位 {position.id} ({position.symbol}) SHORT 觸發 {mode}，"
                    f"目前價格: {mark}, 停損線: {dyn_stop}, 獲利%: {profit_pct:.2f}"
                )
                
                # auto_close_enabled 始終啟用（強制）
                # 呼叫關倉函式
                try:
                    close_order = close_futures_position(
                        symbol=position.symbol,
                        position_side=position.side,  # "SHORT"
                        qty=position.qty,
                        position_id=position.id
                    )
                    
                    # 取得平倉價格
                    exit_price = get_exit_price_from_order(close_order, position.symbol)
                    
                    # 更新倉位狀態與平倉資訊
                    position.status = "CLOSED"
                    position.closed_at = datetime.now(timezone.utc)
                    position.exit_price = exit_price
                    position.exit_reason = "dynamic_stop" if mode == "dynamic_trailing" else "base_stop"
                    db.commit()
                    
                    logger.info(
                        f"倉位 {position.id} ({position.symbol}) SHORT 已成功關倉（{mode}），"
                        f"訂單 ID: {close_order.get('orderId')}, 平倉價格: {exit_price}"
                    )
                
                except Exception as e:
                    logger.error(f"關閉倉位 {position.id} 失敗: {e}")
                    position.status = "ERROR"
                    db.commit()
                    raise
        
        # 如果倉位方向不是 LONG 或 SHORT，記錄警告
        else:
            logger.warning(f"倉位 {position.id} ({position.symbol}) 有未知的方向: {position.side}")
            return
        
        db.commit()
    
    except Exception as e:
        logger.error(f"檢查倉位 {position.id} 追蹤停損時發生錯誤: {e}")
        raise


@app.on_event("shutdown")
async def shutdown_event():
    """應用程式關閉時執行"""
    global _trailing_worker_running
    _trailing_worker_running = False
    logger.info("追蹤停損背景任務已停止")


# 應用程式啟動時初始化資料庫
@app.on_event("startup")
async def startup_event():
    """應用程式啟動時執行"""
    init_db()
    
    # 初始化 Portfolio Trailing Config（如果不存在則創建預設值）
    db = SessionLocal()
    try:
        config = db.query(PortfolioTrailingConfig).filter(PortfolioTrailingConfig.id == 1).first()
        if not config:
            try:
                # 嘗試創建新記錄（明確指定 id=1）
                config = PortfolioTrailingConfig(
                    id=1,
                    enabled=False,
                    target_pnl=None,
                    lock_ratio=None
                )
                db.add(config)
                db.commit()
                db.refresh(config)
                logger.info("Portfolio Trailing Config 已初始化（預設值）")
            except Exception as create_error:
                # 如果創建失敗（可能是因為 id=1 已存在或其他原因），嘗試查詢
                db.rollback()
                config = db.query(PortfolioTrailingConfig).filter(PortfolioTrailingConfig.id == 1).first()
                if config:
                    logger.info(f"Portfolio Trailing Config 已存在: enabled={config.enabled}, target_pnl={config.target_pnl}, lock_ratio={config.lock_ratio}")
                else:
                    logger.error(f"創建 Portfolio Trailing Config 失敗，且查詢也失敗: {create_error}")
        else:
            logger.info(f"Portfolio Trailing Config 已載入: enabled={config.enabled}, target_pnl={config.target_pnl}, lock_ratio={config.lock_ratio}")
    except Exception as e:
        logger.error(f"初始化 Portfolio Trailing Config 時發生錯誤: {e}", exc_info=True)
        try:
            db.rollback()
        except:
            pass
    finally:
        try:
            db.close()
        except:
            pass
    
    # 嘗試初始化幣安客戶端（檢查環境變數是否設定）
    try:
        get_client()
        logger.info("幣安客戶端初始化成功")
    except Exception as e:
        logger.warning(f"幣安客戶端初始化失敗: {e}")
        logger.warning("請確保已設定 BINANCE_API_KEY 和 BINANCE_API_SECRET 環境變數")
    
    # 記錄 Dynamic Stop 設定值
    logger.info("Dynamic Stop 設定:")
    logger.info(f"  DYN_TRAILING_ENABLED: {DYN_TRAILING_ENABLED}")
    logger.info(f"  DYN_PROFIT_THRESHOLD_PCT: {DYN_PROFIT_THRESHOLD_PCT}%")
    logger.info(f"  DYN_LOCK_RATIO_DEFAULT: {DYN_LOCK_RATIO_DEFAULT}")
    logger.info(f"  DYN_BASE_SL_PCT: {DYN_BASE_SL_PCT}%")
    
    # 啟動追蹤停損背景任務
    asyncio.create_task(trailing_stop_worker())
    logger.info("追蹤停損背景任務已在啟動事件中建立")


# ==================== Pydantic 模型定義 ====================

class TradingViewSignal(BaseModel):
    """TradingView Webhook 訊號格式（舊版，保留向後兼容）"""
    secret: str = Field(..., description="Webhook 密鑰，用於驗證請求來源")
    symbol: str = Field(..., description="交易對，例如：BTCUSDT")
    side: str = Field(..., description="交易方向：BUY 或 SELL")
    qty: float = Field(..., description="交易數量")
    leverage: Optional[int] = Field(None, description="杠桿倍數，例如：10")
    trailing_callback_percent: Optional[float] = Field(None, description="追蹤停損回調百分比，例如：2.0 代表 2%")
    tag: Optional[str] = Field(None, description="可選的標籤，用於標記訂單來源")


class TradingViewSignalIn(BaseModel):
    """
    TradingView Webhook 輸入格式
    
    支援兩種模式：
    1. 新格式（推薦）：使用 signal_key，系統會查找所有 enabled=True 且 signal_id 匹配的 bots
    2. 舊格式（兼容）：使用 bot_key，直接查找對應的 bot
    
    若同時提供 signal_key 和 bot_key，優先使用 signal_key。
    
    位置導向模式（position-based）：
    - 如果提供 position_size，系統會根據目標倉位大小調整現有倉位
    - position_size > 0: 目標為多倉
    - position_size < 0: 目標為空倉
    - position_size == 0: 目標為平倉
    - 如果未提供 position_size，則使用舊的訂單導向模式（BUY=開多，SELL=開空）
    """
    secret: str = Field(..., description="Webhook 密鑰，用於驗證請求來源")
    signal_key: Optional[str] = Field(None, description="新格式：對應 TVSignalConfig.signal_key")
    bot_key: Optional[str] = Field(None, description="舊格式：對應 BotConfig.bot_key（兼容）")
    symbol: str = Field(..., description="交易對，例如：BTCUSDT")
    side: str = Field(..., description="交易方向：BUY 或 SELL")
    qty: float = Field(..., description="交易數量")
    position_size: Optional[float] = Field(None, description="目標倉位大小（位置導向模式）。>0=多倉，<0=空倉，0=平倉。如果未提供則使用舊的訂單導向模式")
    time: Optional[str] = Field(None, description="TradingView 傳來的時間字串（可選）")
    extra: Optional[dict] = Field(None, description="彈性欄位（可選）")


class PositionOut(BaseModel):
    """Position 回應格式
    
    時間欄位使用 datetime 型別，FastAPI 會自動序列化為 ISO8601 格式。
    Pydantic 會自動解析 ISO8601 字串為 datetime 物件。
    """
    id: int
    symbol: str
    side: str
    qty: float
    entry_price: float
    status: str
    binance_order_id: Optional[int] = None
    client_order_id: Optional[str] = None
    highest_price: Optional[float] = None
    trail_callback: Optional[float] = None
    dyn_profit_threshold_pct: Optional[float] = None
    base_stop_loss_pct: Optional[float] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    created_at: datetime
    closed_at: Optional[datetime] = None
    # 添加實際使用的值和來源標記（用於前端顯示和顏色標記）
    profit_threshold_value: Optional[float] = None
    profit_threshold_source: Optional[str] = None  # "override", "global", "default"
    lock_ratio_value: Optional[float] = None
    lock_ratio_source: Optional[str] = None  # "override", "global", "default"
    base_sl_value: Optional[float] = None
    base_sl_source: Optional[str] = None  # "override", "global", "default"
    # 添加停損狀態（僅對 OPEN 狀態的倉位有效）
    stop_mode: Optional[str] = None  # "dynamic", "base", "none"
    base_stop_price: Optional[float] = None
    dynamic_stop_price: Optional[float] = None
    # 停損/止盈機制控制
    bot_stop_loss_enabled: bool = True
    tv_signal_close_enabled: bool = True


class TrailingUpdate(BaseModel):
    """更新追蹤停損請求格式"""
    trailing_callback_percent: float = Field(..., description="追蹤停損回調百分比，例如：2.0 代表 2%")
    activation_profit_percent: Optional[float] = Field(None, description="啟動追蹤停損的獲利百分比，例如：1.0 代表先賺 1% 再啟動追蹤")


class TrailingSideConfig(BaseModel):
    """單側 (LONG 或 SHORT) 的 Trailing Stop 設定"""
    profit_threshold_pct: float = 1.0      # PnL% >= 此值才啟動鎖利
    lock_ratio: float = 2.0 / 3.0          # 鎖利比例 (約 0.67 = 2/3)
    base_sl_pct: float = 0.5               # 基礎停損距離 (%)


class TrailingConfig(BaseModel):
    """Trailing Stop 全域設定模型（分 LONG 和 SHORT）"""
    trailing_enabled: bool = True
    long_config: TrailingSideConfig = Field(default_factory=lambda: TrailingSideConfig())
    short_config: TrailingSideConfig = Field(default_factory=lambda: TrailingSideConfig())
    auto_close_enabled: bool = True        # Dynamic Stop 觸發時是否自動關倉
    
    # 向後兼容：提供舊的屬性訪問方式（返回 LONG 的設定）
    @property
    def profit_threshold_pct(self) -> float:
        """向後兼容：返回 LONG 的 profit_threshold_pct"""
        return self.long_config.profit_threshold_pct
    
    @property
    def lock_ratio(self) -> float:
        """向後兼容：返回 LONG 的 lock_ratio"""
        return self.long_config.lock_ratio
    
    @property
    def base_sl_pct(self) -> float:
        """向後兼容：返回 LONG 的 base_sl_pct"""
        return self.long_config.base_sl_pct
    
    def get_config_for_side(self, side: str) -> TrailingSideConfig:
        """根據倉位方向取得對應的設定"""
        side_upper = side.upper()
        if side_upper == "LONG":
            return self.long_config
        elif side_upper == "SHORT":
            return self.short_config
        else:
            # 預設返回 LONG 設定
            return self.long_config


class TrailingSideConfigUpdate(BaseModel):
    """更新單側 Trailing 設定的請求格式"""
    profit_threshold_pct: Optional[float] = None
    lock_ratio: Optional[float] = None
    base_sl_pct: Optional[float] = None


class TrailingConfigUpdate(BaseModel):
    """更新 Trailing 設定的請求格式"""
    trailing_enabled: Optional[bool] = None
    long_config: Optional[TrailingSideConfigUpdate] = None
    short_config: Optional[TrailingSideConfigUpdate] = None
    auto_close_enabled: Optional[bool] = None
    
    # 向後兼容：支援舊格式（會同時更新 LONG 和 SHORT）
    profit_threshold_pct: Optional[float] = None
    lock_ratio: Optional[float] = None
    base_sl_pct: Optional[float] = None


class BinanceCloseRequest(BaseModel):
    """Binance Live Position 關倉請求格式"""
    symbol: str = Field(..., description="交易對，例如 BTCUSDT")
    position_side: str = Field(..., description="倉位方向，LONG 或 SHORT")


class TVSignalConfigBase(BaseModel):
    """TradingView Signal Config 基礎模型"""
    name: str
    signal_key: str
    description: Optional[str] = None
    symbol_hint: Optional[str] = None
    timeframe_hint: Optional[str] = None
    enabled: bool = True


class TVSignalConfigCreate(TVSignalConfigBase):
    """建立 Signal Config 的請求格式"""
    pass


class TVSignalConfigUpdate(BaseModel):
    """更新 Signal Config 的請求格式"""
    name: Optional[str] = None
    signal_key: Optional[str] = None
    description: Optional[str] = None
    symbol_hint: Optional[str] = None
    timeframe_hint: Optional[str] = None
    enabled: Optional[bool] = None


class TVSignalConfigOut(TVSignalConfigBase):
    """Signal Config 回應格式"""
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True
        orm_mode = True


class BotConfigBase(BaseModel):
    """Bot 設定基礎模型"""
    name: str
    bot_key: str
    enabled: bool = True
    symbol: str = "BTCUSDT"
    use_signal_side: bool = True
    fixed_side: Optional[str] = None
    qty: float = 0.01
    max_invest_usdt: Optional[float] = None
    leverage: int = 20
    use_dynamic_stop: bool = True
    trailing_callback_percent: Optional[float] = None
    base_stop_loss_pct: float = 3.0
    signal_id: Optional[int] = None


class BotConfigCreate(BotConfigBase):
    """建立 Bot 設定的請求格式"""
    pass


class BotConfigUpdate(BaseModel):
    """更新 Bot 設定的請求格式"""
    name: Optional[str] = None
    enabled: Optional[bool] = None
    symbol: Optional[str] = None
    use_signal_side: Optional[bool] = None
    fixed_side: Optional[str] = None
    qty: Optional[float] = None
    max_invest_usdt: Optional[float] = None
    leverage: Optional[int] = None
    use_dynamic_stop: Optional[bool] = None
    trailing_callback_percent: Optional[float] = None
    base_stop_loss_pct: Optional[float] = None
    signal_id: Optional[int] = None


class BotConfigOut(BotConfigBase):
    """Bot 設定回應格式"""
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    signal: Optional[TVSignalConfigOut] = None  # 關聯的 Signal Config（可選）
    
    class Config:
        from_attributes = True
        orm_mode = True


class WebhookResponse(BaseModel):
    """Webhook 回應格式"""
    success: bool
    message: str
    position_id: int
    binance_order: dict


# ==================== Trailing 全域設定（in-memory）====================
# 目前執行緒內使用的全域設定（in-memory；之後必要時再寫 DB）
# TODO: 之後 dynamic stop 相關邏輯會統一改用此配置，而不是直接讀取 DYN_* 常數
# 注意：必須在 TrailingConfig 類定義之後才能初始化
# 從環境變數讀取預設值，如果沒有設定則使用預設值
# 這些值可以通過 .env 檔案設定：
#   DYN_PROFIT_THRESHOLD_PCT=1.0    (PnL% threshold)
#   DYN_LOCK_RATIO_DEFAULT=0.666    (Lock ratio, 例如 0.666 代表 2/3)
#   DYN_BASE_SL_PCT=0.5              (Base SL %)
# 初始化 TRAILING_CONFIG，從環境變數讀取設定
_default_profit_threshold = float(os.getenv("DYN_PROFIT_THRESHOLD_PCT", "1.0"))
_default_lock_ratio = float(os.getenv("DYN_LOCK_RATIO_DEFAULT", str(2.0 / 3.0)))
_default_base_sl = float(os.getenv("DYN_BASE_SL_PCT", "0.5"))

TRAILING_CONFIG = TrailingConfig(
    trailing_enabled=True,
    long_config=TrailingSideConfig(
        profit_threshold_pct=_default_profit_threshold,
        lock_ratio=_default_lock_ratio,
        base_sl_pct=_default_base_sl
    ),
    short_config=TrailingSideConfig(
        profit_threshold_pct=_default_profit_threshold,
        lock_ratio=_default_lock_ratio,
        base_sl_pct=_default_base_sl
    ),
    auto_close_enabled=True
)


# ==================== 認證依賴 ====================

def verify_admin_api_key(x_api_key: str = Header(...)):
    """
    驗證管理員 API Key（舊版，保留向後兼容）
    
    Args:
        x_api_key: 從 Header 取得的 X-API-KEY
    
    Raises:
        HTTPException: 當 API Key 不正確時
    """
    admin_api_key = os.getenv("ADMIN_API_KEY", "")
    
    if not admin_api_key:
        raise HTTPException(
            status_code=500,
            detail="伺服器未設定 ADMIN_API_KEY，請聯繫管理員"
        )
    
    if x_api_key != admin_api_key:
        raise HTTPException(
            status_code=401,
            detail="無效的 API Key"
        )
    
    return x_api_key


async def require_admin_user(request: Request):
    """
    驗證使用者是否為管理員（使用 Google OAuth Session）
    
    如果未設定 Google OAuth，則啟用『開發模式』，所有請求直接視為管理員。
    如果已啟用 Google OAuth，則檢查 session 中是否有 is_admin=True 和 email。
    如果不是管理員，統一使用 HTTPException 中斷流程：
    - 對於 HTML 請求：使用 307 + Location header 導向登入頁面
    - 對於 API 請求：回傳 401/403
    
    Args:
        request: FastAPI Request 物件
    
    Returns:
        dict: 包含 email 的使用者資訊
    
    Raises:
        HTTPException: 當使用者未登入或不是管理員時（僅在 OAuth 啟用時）
            - 未登入且為 HTML 請求：307 重定向到 /auth/login
            - 未登入且為 API 請求：401 Unauthorized
            - 已登入但不是管理員：403 Forbidden
    """
    # 開發模式：Google OAuth 未設定 → 直接回傳一個假的 admin 使用者
    if not GOOGLE_OAUTH_ENABLED:
        dev_email = ADMIN_GOOGLE_EMAIL or "dev-admin@example.com"
        return {"email": dev_email}
    
    session = request.session
    accept = request.headers.get("accept", "")
    
    # 檢查 session 中是否有管理員標記
    if not session.get("is_admin") or not session.get("email"):
        # 如果是 HTML 請求，使用 307 重定向到登入頁面
        if "text/html" in accept:
            raise HTTPException(
                status_code=status.HTTP_307_TEMPORARY_REDIRECT,
                detail="請先登入",
                headers={"Location": "/auth/login"},
            )
        # 否則回傳 401
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="請先登入",
        )
    
    # 檢查 email 是否為管理員 email
    email = session.get("email")
    if email != ADMIN_GOOGLE_EMAIL:
        # 統一使用 403，不再回傳 HTMLResponse
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="你沒有權限使用此系統",
        )
    
    # 驗證通過，回傳使用者資訊
    return {"email": email}


def verify_tradingview_secret(secret: str):
    """
    驗證 TradingView Webhook Secret
    
    Args:
        secret: 從請求取得的 secret
    
    Raises:
        HTTPException: 當 secret 不正確時
    """
    tradingview_secret = os.getenv("TRADINGVIEW_SECRET", "")
    
    if not tradingview_secret:
        raise HTTPException(
            status_code=500,
            detail="伺服器未設定 TRADINGVIEW_SECRET，請聯繫管理員"
        )
    
    if secret != tradingview_secret:
        raise HTTPException(
            status_code=401,
            detail="無效的 Webhook Secret"
        )


# ==================== API 端點 ====================

@app.get("/")
async def root():
    """根路徑，用於健康檢查"""
    return {
        "status": "ok",
        "message": "TradingView Binance Bot is running",
        "version": "1.0.0"
    }


# ==================== Google OAuth 路由 ====================

@app.get("/auth/login")
async def login(request: Request):
    """
    導向 Google OAuth 授權頁面。
    如果未設定 Google OAuth，顯示簡單提示頁。
    """
    if not GOOGLE_OAUTH_ENABLED:
        # 開發模式：顯示提示頁即可，避免 500
        return HTMLResponse(
            content="""
            <html>
                <head>
                    <meta charset="UTF-8">
                    <title>Google OAuth 未設定</title>
                    <style>
                        body {
                            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                            max-width: 600px;
                            margin: 50px auto;
                            padding: 20px;
                            line-height: 1.6;
                        }
                        h1 { color: #333; }
                        a {
                            display: inline-block;
                            margin-top: 20px;
                            padding: 10px 20px;
                            background-color: #007bff;
                            color: white;
                            text-decoration: none;
                            border-radius: 4px;
                        }
                        a:hover { background-color: #0056b3; }
                    </style>
                </head>
                <body>
                    <h1>Google OAuth 未設定</h1>
                    <p>目前系統在開發模式下執行，未啟用 Google 登入。</p>
                    <p>請設定 GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / ADMIN_GOOGLE_EMAIL 後重新啟動伺服器。</p>
                    <p><a href="/dashboard">回到 Dashboard</a></p>
                </body>
            </html>
            """,
            status_code=200,
        )
    
    # 正常情況：導向 Google OAuth
    redirect_uri = str(request.url_for("auth_callback"))
    return await oauth.google.authorize_redirect(request, redirect_uri)


@app.get("/auth/callback")
async def auth_callback(request: Request):
    """
    Google OAuth 回調處理
    
    接收 Google 回傳的 code，交換 token，取得使用者 email，
    並檢查是否為管理員 email。
    """
    try:
        token = await oauth.google.authorize_access_token(request)
        
        # 取得使用者資訊
        resp = await oauth.google.get('userinfo', token=token)
        user_info = resp.json()
        
        email = user_info.get("email")
        if not email:
            raise HTTPException(status_code=400, detail="無法取得 email")
        
        # 檢查是否為管理員 email
        if email == ADMIN_GOOGLE_EMAIL:
            # 設定 session
            request.session["is_admin"] = True
            request.session["email"] = email
            request.session["name"] = user_info.get("name", email)
            
            logger.info(f"管理員登入成功: {email}")
            return RedirectResponse(url="/dashboard")
        else:
            logger.warning(f"非管理員嘗試登入: {email}")
            return HTMLResponse(
                content=f"""
                <html>
                    <body>
                        <h1>未授權</h1>
                        <p>你沒有權限使用此系統。</p>
                        <p>Email: {email}</p>
                        <p><a href="/auth/logout">返回</a></p>
                    </body>
                </html>
                """,
                status_code=403
            )
    
    except Exception as e:
        logger.error(f"OAuth 回調處理失敗: {e}")
        raise HTTPException(status_code=500, detail=f"登入失敗: {str(e)}")


@app.get("/auth/logout")
async def logout(request: Request):
    """
    登出，清除 session
    """
    request.session.clear()
    return RedirectResponse(url="/")


# ==================== Dashboard 路由 ====================

@app.get("/me")
async def me(user: dict = Depends(require_admin_user)):
    """
    取得當前使用者資訊和 Binance 模式。
    在 Demo 模式下（GOOGLE_OAUTH_ENABLED=False），會自動返回假的管理員資訊。
    """
    # 如果已經在 require_admin_user 中通過驗證，直接返回
    user_email = user.get("email", "demo@example.com")
    
    # 取得 Binance 模式
    binance_mode = "demo"
    try:
        client, mode = get_client()
        binance_mode = mode
    except Exception:
        pass
    
    # 取得 TRADINGVIEW_SECRET（僅供管理員使用，用於生成 TradingView Alert Template）
    tradingview_secret = os.getenv("TRADINGVIEW_SECRET", "")
    
    return {
        "user_email": user_email,
        "binance_mode": binance_mode,
        "tradingview_secret": tradingview_secret,  # 用於前端生成模板
    }


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: dict = Depends(require_admin_user)
):
    """
    顯示倉位 Dashboard
    
    僅限已登入且通過管理員驗證的使用者使用（Google OAuth + ADMIN_GOOGLE_EMAIL）。
    前端使用 JavaScript 動態從 /positions API 載入資料。
    """
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user if isinstance(user, dict) else {},
            "user_email": user.get("email") if isinstance(user, dict) else "",
        }
    )


# ==================== 工具 API ====================

@app.get("/api/mark-price/{symbol}")
async def get_mark_price_api(
    symbol: str,
    user: dict = Depends(require_admin_user)
):
    """
    取得交易對的標記價格（用於前端計算 qty）
    
    僅限已登入的管理員使用。
    
    Args:
        symbol: 交易對，例如 BTCUSDT
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
    
    Returns:
        dict: 包含 mark_price 的字典
    """
    try:
        mark_price = get_mark_price(symbol.upper())
        return {
            "symbol": symbol.upper(),
            "mark_price": mark_price
        }
    except Exception as e:
        logger.error(f"取得 {symbol} 標記價格失敗: {e}")
        raise HTTPException(status_code=500, detail=f"無法取得 {symbol} 的標記價格: {str(e)}")


@app.get("/api/symbol-info/{symbol}")
async def get_symbol_info_api(
    symbol: str,
    user: dict = Depends(require_admin_user)
):
    """
    取得交易對的精度資訊（數量精度、價格精度、步長等）
    
    僅限已登入的管理員使用。
    
    Args:
        symbol: 交易對，例如 BTCUSDT
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
    
    Returns:
        dict: 包含精度資訊的字典
    """
    try:
        symbol_info = get_symbol_info(symbol.upper())
        # 只回傳必要的資訊，不包含 raw 原始資料
        return {
            "symbol": symbol.upper(),
            "quantityPrecision": symbol_info["quantityPrecision"],
            "stepSize": symbol_info["stepSize"],
            "pricePrecision": symbol_info["pricePrecision"],
            "tickSize": symbol_info["tickSize"],
            "minQty": symbol_info["minQty"],
            "maxQty": symbol_info["maxQty"] if symbol_info["maxQty"] != float("inf") else None
        }
    except Exception as e:
        logger.error(f"取得 {symbol} 精度資訊失敗: {e}")
        raise HTTPException(status_code=500, detail=f"無法取得 {symbol} 的精度資訊: {str(e)}")


# 舊版 webhook endpoint 已移除，改用下面的新版 endpoint 支援 bot_key


def calculate_qty_from_max_invest(bot: BotConfig, symbol: str, target_qty: Optional[float] = None) -> float:
    """
    根據 max_invest_usdt 計算 qty（在下單當下根據即時價格計算）
    
    此函數在 webhook 被觸發下單時才調用，會取得當下的即時標記價格來計算數量。
    
    Args:
        bot: Bot 設定
        symbol: 交易對
        target_qty: 目標數量（位置導向模式使用），如果為 None 則使用 bot.qty
    
    Returns:
        計算後的 qty
    """
    if bot.max_invest_usdt is not None and bot.max_invest_usdt > 0:
        try:
            # 取得當下的即時標記價格
            current_price = get_mark_price(symbol)
            if current_price and current_price > 0:
                max_qty_from_invest = bot.max_invest_usdt / current_price
                if target_qty is not None:
                    # 位置導向模式：使用較小值（不超過 max_invest_usdt）
                    qty = min(target_qty, max_qty_from_invest)
                    logger.info(
                        f"Bot {bot.id} 位置導向模式：max_invest_usdt={bot.max_invest_usdt} USDT, "
                        f"即時價格={current_price}, target_qty={target_qty}, "
                        f"max_qty_from_invest={max_qty_from_invest}, 最終 qty={qty}"
                    )
                else:
                    # 訂單導向模式：直接使用 max_invest_usdt 計算
                    qty = max_qty_from_invest
                    logger.info(
                        f"Bot {bot.id} 訂單導向模式：max_invest_usdt={bot.max_invest_usdt} USDT, "
                        f"即時價格={current_price}, 計算 qty={qty}"
                    )
                return qty
            else:
                logger.warning(f"Bot {bot.id} 無法取得 {symbol} 的即時價格，使用預設 qty")
                return target_qty if target_qty is not None else bot.qty
        except Exception as e:
            logger.warning(f"Bot {bot.id} 計算 qty 時發生錯誤: {e}，使用預設 qty")
            return target_qty if target_qty is not None else bot.qty
    else:
        # 沒有設定 max_invest_usdt，使用原始值
        return target_qty if target_qty is not None else bot.qty


def normalize_symbol_from_tv(tv_symbol: str) -> str:
    """
    Normalize TradingView symbol into a Binance-compatible futures symbol.
    
    Examples:
    - "BTCUSDT.P"           -> "BTCUSDT"
    - "BINANCE:BTCUSDT.P"   -> "BTCUSDT"
    - "btcusdt.p"           -> "BTCUSDT"
    - "BTCUSDT"             -> "BTCUSDT"
    - "BINANCE:ETHUSDT"     -> "ETHUSDT"
    
    Args:
        tv_symbol: TradingView symbol string
    
    Returns:
        str: Normalized Binance-compatible symbol (uppercase, no exchange prefix, no .P suffix)
    
    Raises:
        ValueError: If the normalized symbol is empty
    """
    if not tv_symbol:
        return tv_symbol
    
    s = tv_symbol.strip().upper()
    
    # Remove exchange prefix like "BINANCE:"
    if ":" in s:
        _, s = s.split(":", 1)
        s = s.strip()
    
    # Remove common TradingView perp suffix ".P"
    if s.endswith(".P"):
        s = s[:-2]
    
    # Final validation
    if not s:
        raise ValueError(f"Normalized symbol is empty. Original: '{tv_symbol}'")
    
    return s


def get_current_position_signed_qty(db: Session, bot_id: int, symbol: str) -> tuple[Optional[Position], float]:
    """
    取得當前 bot 的倉位（以帶符號的數量表示）
    
    Args:
        db: 資料庫 Session
        bot_id: Bot ID
        symbol: 交易對
    
    Returns:
        tuple: (Position 物件或 None, 帶符號的數量)
        - 如果沒有倉位，返回 (None, 0.0)
        - 如果是 LONG，返回 (position, +qty)
        - 如果是 SHORT，返回 (position, -qty)
    """
    position = db.query(Position).filter(
        Position.bot_id == bot_id,
        Position.symbol == symbol.upper(),
        Position.status == "OPEN"
    ).first()
    
    if not position:
        return None, 0.0
    
    # 轉換為帶符號的數量
    if position.side == "LONG":
        return position, position.qty
    elif position.side == "SHORT":
        return position, -position.qty
    else:
        return position, 0.0


@app.post("/webhook/tradingview", response_model=dict)
async def webhook_tradingview(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    接收 TradingView Webhook（支援 signal_key 和 bot_key）
    
    處理流程：
    1. 讀取原始 body 並儲存為 raw_payload
    2. 解析 JSON 並驗證 secret
    3. 先建立 TradingViewSignalLog 記錄（總是執行）
    4. 根據 signal_key 或 bot_key 查找對應的 enabled bots
    5. 為每個 bot 下單並建立 Position
    6. 更新 log 的 processed 和 process_result（總是執行）
    
    支援兩種模式：
    - 新格式（推薦）：使用 signal_key，查找所有 enabled=True 且 signal_id 匹配的 bots
    - 舊格式（兼容）：使用 bot_key，直接查找對應的 bot
    
    Args:
        request: FastAPI Request 物件
        db: 資料庫 Session
    
    Returns:
        dict: 處理結果，包含 success、message、signal_log_id、results 等
    """
    TRADINGVIEW_SECRET = os.getenv("TRADINGVIEW_SECRET", "")
    log = None
    signal_config = None
    
    try:
        # 讀取原始 body
        raw_bytes = await request.body()
        raw_text = raw_bytes.decode("utf-8") if raw_bytes else ""
        
        # 解析 JSON
        try:
            data = json.loads(raw_text) if raw_text else {}
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"無效的 JSON 格式: {str(e)}")
        
        # 建立 Pydantic 模型
        signal = TradingViewSignalIn(**data)
        
        # 驗證 secret
        if TRADINGVIEW_SECRET and signal.secret != TRADINGVIEW_SECRET:
            raise HTTPException(status_code=401, detail="無效的 Webhook Secret")
        
        # Normalize symbol from TradingView
        raw_symbol = signal.symbol
        try:
            normalized_symbol = normalize_symbol_from_tv(raw_symbol)
            logger.info(
                f"[TV Webhook] Symbol normalization: raw='{raw_symbol}' -> normalized='{normalized_symbol}'"
            )
        except (ValueError, AttributeError) as e:
            logger.error(f"[TV Webhook] Symbol normalization failed: {e}, raw='{raw_symbol}'")
            raise HTTPException(
                status_code=400,
                detail=f"Invalid symbol from TradingView: '{raw_symbol}'. Normalization failed: {str(e)}"
            )
        
        # Replace signal.symbol with normalized version for all business logic
        # Note: We preserve the raw_symbol in the raw_payload for debugging
        signal.symbol = normalized_symbol
        
        # 決定使用哪種模式（優先使用 signal_key）
        use_signal_key = signal.signal_key is not None and signal.signal_key != ""
        use_bot_key = signal.bot_key is not None and signal.bot_key != ""
        
        if not use_signal_key and not use_bot_key:
            raise HTTPException(status_code=400, detail="必須提供 signal_key 或 bot_key")
        
        # 檢查重複訊號（防止 TradingView 重複發送）
        # 建立訊號的唯一識別碼（排除 time 欄位，因為可能每次不同）
        signal_fingerprint = {
            "signal_key": signal.signal_key,
            "bot_key": signal.bot_key,
            "symbol": normalized_symbol,
            "side": signal.side.upper(),
            "qty": signal.qty,
            "position_size": signal.position_size,
        }
        # 建立 hash
        fingerprint_str = json.dumps(signal_fingerprint, sort_keys=True)
        signal_hash = hashlib.sha256(fingerprint_str.encode("utf-8")).hexdigest()[:16]
        
        # 檢查最近 60 秒內是否有相同的訊號
        time_threshold = datetime.now(timezone.utc) - timedelta(seconds=60)
        duplicate_log = (
            db.query(TradingViewSignalLog)
            .filter(
                TradingViewSignalLog.symbol == normalized_symbol.upper(),
                TradingViewSignalLog.side == signal.side.upper(),
                TradingViewSignalLog.qty == signal.qty,
                TradingViewSignalLog.position_size == signal.position_size,
                TradingViewSignalLog.received_at >= time_threshold
            )
        )
        
        # 如果使用 signal_key，也檢查 signal_id
        if use_signal_key:
            signal_config = db.query(TVSignalConfig).filter(TVSignalConfig.signal_key == signal.signal_key).first()
            if not signal_config:
                raise HTTPException(status_code=400, detail=f"找不到 signal_key='{signal.signal_key}' 的 Signal Config")
            signal_id = signal_config.id
            duplicate_log = duplicate_log.filter(TradingViewSignalLog.signal_id == signal_id)
        else:
            signal_id = None
            duplicate_log = duplicate_log.filter(TradingViewSignalLog.bot_key == signal.bot_key)
        
        existing_duplicate = duplicate_log.first()
        if existing_duplicate:
            logger.warning(
                f"檢測到重複訊號，跳過處理。當前訊號 hash={signal_hash}, "
                f"重複的 log_id={existing_duplicate.id}, "
                f"重複時間={existing_duplicate.received_at}"
            )
            return {
                "success": False,
                "message": "重複訊號，已於最近處理過",
                "duplicate_log_id": existing_duplicate.id,
                "signal_hash": signal_hash
            }
        
        # 1) 先建立 signal log（總是執行）
        signal_dict = signal.model_dump() if hasattr(signal, 'model_dump') else signal.dict()
        
        # 總是儲存 bot_key（如果請求中有提供），無論使用哪種模式
        bot_key_to_store = signal.bot_key if signal.bot_key else None
        
        log = TradingViewSignalLog(
            bot_key=bot_key_to_store,
            signal_id=signal_id,
            symbol=signal.symbol.upper(),
            side=signal.side.upper(),
            qty=signal.qty,
            position_size=signal.position_size,
            raw_body=signal_dict,
            raw_payload=raw_text
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        
        logger.info(
            f"[TV RAW] signal_log_id={log.id} payload={raw_text}"
        )
        logger.info(
            f"收到 TradingView signal, log_id={log.id}, "
            f"signal_key={signal.signal_key if use_signal_key else None}, "
            f"bot_key={signal.bot_key if use_bot_key else None}, "
            f"raw_symbol='{raw_symbol}', normalized_symbol='{normalized_symbol}', "
            f"side={log.side}, qty={log.qty}, "
            f"position_size={log.position_size} (mode={'position-based' if log.position_size is not None else 'order-based'})"
        )
        
        # 2) 查找 active bots
        if use_signal_key:
            # 新格式：根據 signal_id 查找所有 enabled bots
            bots = (
                db.query(BotConfig)
                .filter(BotConfig.signal_id == signal_id, BotConfig.enabled == True)
                .all()
            )
        else:
            # 舊格式：根據 bot_key 查找
            bots = (
                db.query(BotConfig)
                .filter(BotConfig.bot_key == signal.bot_key, BotConfig.enabled == True)
                .all()
            )
        
        if not bots:
            log.processed = True
            log.process_result = "no_active_bot"
            db.commit()
            return {
                "success": False,
                "message": "沒有啟用中的 bot",
                "signal_log_id": log.id
            }
        
        client = get_client()
        results = []
        EPS = 1e-8  # 浮點數比較的誤差範圍
        
        # 3) 為每個 bot 處理（位置導向或訂單導向）
        for bot in bots:
            try:
                # 決定 symbol（優先使用 bot.symbol，否則使用已經規範化後的 signal.symbol）
                # 如果 bot.symbol 存在，也需要規範化；否則使用已規範化的 signal.symbol
                if bot.symbol:
                    try:
                        symbol = normalize_symbol_from_tv(bot.symbol)
                        logger.debug(f"Bot {bot.id} 使用 bot.symbol='{bot.symbol}' -> normalized='{symbol}'")
                    except (ValueError, AttributeError) as e:
                        logger.warning(f"Bot {bot.id} 的 symbol='{bot.symbol}' 規範化失敗，使用 signal.symbol: {e}")
                        symbol = normalized_symbol
                else:
                    symbol = normalized_symbol
                
                # 檢查是否使用位置導向模式
                target_position_size = signal.position_size
                
                # 如果未提供 position_size，使用舊的訂單導向模式
                if target_position_size is None:
                    # ========== 舊的訂單導向模式（向後兼容） ==========
                    side = signal.side.upper()
                    if not bot.use_signal_side and bot.fixed_side:
                        side = bot.fixed_side.upper()
                    
                    if side not in ["BUY", "SELL"]:
                        raise ValueError(f"無效的下單方向: {side}")
                    
                    # 計算 qty：如果設定了 max_invest_usdt，則根據當前價格計算
                    qty = calculate_qty_from_max_invest(bot, symbol)
                    
                    # 設定杠桿
                    try:
                        client.futures_change_leverage(symbol=symbol, leverage=bot.leverage)
                        logger.info(f"成功設定 {symbol} 杠桿為 {bot.leverage}x")
                    except Exception as e:
                        logger.warning(f"設定杠桿時發生警告: {e}")
                    
                    # 下市價單
                    timestamp = int(time.time() * 1000)
                    client_order_id = f"bot_{bot.id}_{timestamp}"
                    
                    logger.info(f"Bot {bot.id} ({bot.name}) 下單（訂單導向）: {symbol} {side} {qty}")
                    
                    # 格式化數量以符合 Binance 精度要求
                    formatted_qty = format_quantity(symbol, qty)
                    formatted_qty_float = float(formatted_qty)
                    
                    order = client.futures_create_order(
                        symbol=symbol,
                        side="BUY" if side == "BUY" else "SELL",
                        type="MARKET",
                        quantity=formatted_qty,
                        newClientOrderId=client_order_id
                    )
                    
                    logger.info(f"Bot {bot.id} 成功下單: {order.get('orderId')}")
                    
                    # 取得 entry_price
                    entry_price = None
                    if order.get("avgPrice"):
                        try:
                            avg_price = float(order["avgPrice"])
                            if avg_price > 0:
                                entry_price = avg_price
                        except (ValueError, TypeError):
                            pass
                    
                    if entry_price is None or entry_price <= 0:
                        if order.get("price"):
                            try:
                                price = float(order["price"])
                                if price > 0:
                                    entry_price = price
                            except (ValueError, TypeError):
                                pass
                    
                    if entry_price is None or entry_price <= 0:
                        try:
                            entry_price = get_mark_price(symbol)
                            if entry_price <= 0:
                                logger.warning(f"Bot {bot.id} 下單 {symbol} 後，無法取得有效的 entry_price")
                        except Exception as e:
                            logger.error(f"Bot {bot.id} 下單 {symbol} 後，取得 mark_price 失敗: {e}")
                            entry_price = 0.0
                    
                    position_side = "LONG" if side == "BUY" else "SHORT"
                    
                    # 設定 trail_callback
                    trail_callback = None
                    if bot.use_dynamic_stop and bot.trailing_callback_percent is not None:
                        trail_callback = bot.trailing_callback_percent / 100.0
                    elif TRAILING_CONFIG.trailing_enabled and TRAILING_CONFIG.lock_ratio:
                        trail_callback = TRAILING_CONFIG.lock_ratio
                    
                    # 建立 Position 記錄
                    position = Position(
                        bot_id=bot.id,
                        tv_signal_log_id=log.id,
                        symbol=symbol,
                        side=position_side,
                        qty=formatted_qty_float,
                        status="OPEN",
                        binance_order_id=int(order.get("orderId")) if order.get("orderId") else None,
                        client_order_id=order.get("clientOrderId"),
                        entry_price=entry_price,
                        trail_callback=trail_callback,
                        highest_price=entry_price if trail_callback else None,
                    )
                    
                    db.add(position)
                    db.commit()
                    db.refresh(position)
                    
                    results.append(f"bot={bot.id}, position_id={position.id}, mode=order_based")
                    logger.info(f"Bot {bot.id} 成功建立 Position {position.id} (訂單導向)")
                    
                else:
                    # ========== 新的位置導向模式 ==========
                    # 正規化 target（將非常小的值視為 0）
                    if abs(target_position_size) < EPS:
                        target_position_size = 0.0
                    
                    # 取得當前倉位
                    current_position, current_qty_signed = get_current_position_signed_qty(db, bot.id, symbol)
                    
                    logger.info(
                        f"Bot {bot.id} ({bot.name}) 位置導向模式: "
                        f"symbol={symbol}, target={target_position_size}, current={current_qty_signed}"
                    )
                    
                    # 設定杠桿（在決定操作前先設定）
                    try:
                        client.futures_change_leverage(symbol=symbol, leverage=bot.leverage)
                        logger.info(f"成功設定 {symbol} 杠桿為 {bot.leverage}x")
                    except Exception as e:
                        logger.warning(f"設定杠桿時發生警告: {e}")
                    
                    # Case A: 目標為平倉 (target == 0)
                    if abs(target_position_size) < EPS:
                        if abs(current_qty_signed) < EPS:
                            # 已經平倉，無需操作
                            results.append(f"bot={bot.id}, result=flat_no_position")
                            logger.info(f"Bot {bot.id} 目標為平倉，當前已無倉位，無需操作")
                        else:
                            # 關閉現有倉位
                            if not current_position:
                                results.append(f"bot={bot.id}, error=current_position_not_found")
                                logger.warning(f"Bot {bot.id} 目標為平倉，但找不到當前倉位記錄")
                            else:
                                # 檢查 tv_signal_close_enabled 標誌
                                if not current_position.tv_signal_close_enabled:
                                    results.append(f"bot={bot.id}, result=tv_signal_close_disabled, position_id={current_position.id}")
                                    logger.info(
                                        f"Bot {bot.id} 收到 TradingView 平倉訊號，但倉位 {current_position.id} "
                                        f"tv_signal_close_enabled=False，跳過關倉"
                                    )
                                else:
                                    try:
                                        close_order = close_futures_position(
                                            symbol=symbol,
                                            position_side=current_position.side,
                                            qty=current_position.qty,
                                            position_id=current_position.id
                                        )
                                        
                                        # 使用統一的函數取得 exit_price（優先使用 avgPrice）
                                        exit_price = get_exit_price_from_order(close_order, symbol)
                                        
                                        current_position.status = "CLOSED"
                                        current_position.closed_at = datetime.now(timezone.utc)
                                        current_position.exit_price = exit_price
                                        current_position.exit_reason = "tv_exit"
                                        db.commit()
                                        
                                        results.append(f"bot={bot.id}, closed_position_id={current_position.id}")
                                        logger.info(f"Bot {bot.id} 成功平倉 Position {current_position.id}")
                                    except Exception as e:
                                        error_msg = f"bot={bot.id}, error=close_failed: {str(e)}"
                                        logger.exception(f"Bot {bot.id} 平倉失敗: {e}")
                                        results.append(error_msg)
                    
                    # Case B: 目標為多倉 (target > 0)
                    elif target_position_size > 0:
                        target_qty_raw = abs(target_position_size)
                        # 應用 max_invest_usdt 限制
                        target_qty = calculate_qty_from_max_invest(bot, symbol, target_qty_raw)
                        
                        if current_qty_signed > 0:
                            # 已經是多倉
                            diff = target_qty - current_qty_signed
                            if abs(diff) < EPS:
                                # 數量已匹配，無需操作
                                results.append(f"bot={bot.id}, result=long_qty_already_match, qty={current_qty_signed}")
                                logger.info(f"Bot {bot.id} 目標多倉數量已匹配，無需操作")
                            else:
                                # 需要調整數量（簡化：先關閉再開新倉，或可以實作部分平倉/加倉）
                                # 這裡採用簡單策略：如果差異大於 10%，則重新開倉
                                if abs(diff) / current_qty_signed > 0.1:
                                    # 先關閉現有倉位
                                    if current_position:
                                        try:
                                            close_order = close_futures_position(
                                                symbol=symbol,
                                                position_side=current_position.side,
                                                qty=current_position.qty,
                                                position_id=current_position.id
                                            )
                                            # 使用統一的函數取得 exit_price（優先使用 avgPrice）
                                            exit_price = get_exit_price_from_order(close_order, symbol)
                                            current_position.status = "CLOSED"
                                            current_position.closed_at = datetime.now(timezone.utc)
                                            current_position.exit_price = exit_price
                                            current_position.exit_reason = "tv_rebalance"
                                            db.commit()
                                        except Exception as e:
                                            logger.exception(f"Bot {bot.id} 調整多倉時關閉舊倉失敗: {e}")
                                    
                                    # 開新多倉
                                    try:
                                        # 格式化數量以符合 Binance 精度要求
                                        formatted_qty = format_quantity(symbol, target_qty)
                                        formatted_qty_float = float(formatted_qty)
                                        
                                        timestamp = int(time.time() * 1000)
                                        client_order_id = f"bot_{bot.id}_{timestamp}"
                                        order = client.futures_create_order(
                                            symbol=symbol,
                                            side="BUY",
                                            type="MARKET",
                                            quantity=formatted_qty,
                                            newClientOrderId=client_order_id
                                        )
                                        
                                        entry_price = float(order.get("avgPrice", 0)) or get_mark_price(symbol) or 0.0
                                        
                                        trail_callback = None
                                        if bot.use_dynamic_stop and bot.trailing_callback_percent is not None:
                                            trail_callback = bot.trailing_callback_percent / 100.0
                                        elif TRAILING_CONFIG.trailing_enabled and TRAILING_CONFIG.lock_ratio:
                                            trail_callback = TRAILING_CONFIG.lock_ratio
                                        
                                        position = Position(
                                            bot_id=bot.id,
                                            tv_signal_log_id=log.id,
                                            symbol=symbol,
                                            side="LONG",
                                            qty=formatted_qty_float,
                                            status="OPEN",
                                            binance_order_id=int(order.get("orderId")) if order.get("orderId") else None,
                                            client_order_id=order.get("clientOrderId"),
                                            entry_price=entry_price,
                                            trail_callback=trail_callback,
                                            highest_price=entry_price if trail_callback else None,
                                        )
                                        db.add(position)
                                        db.commit()
                                        db.refresh(position)
                                        
                                        results.append(f"bot={bot.id}, position_id={position.id}, result=rebalance_long")
                                        logger.info(f"Bot {bot.id} 成功調整多倉至 {target_qty}")
                                    except Exception as e:
                                        error_msg = f"bot={bot.id}, error=rebalance_long_failed: {str(e)}"
                                        logger.exception(f"Bot {bot.id} 調整多倉失敗: {e}")
                                        results.append(error_msg)
                                else:
                                    results.append(f"bot={bot.id}, result=long_qty_diff_small, diff={diff}")
                                    logger.info(f"Bot {bot.id} 多倉數量差異小於 10%，跳過調整")
                        elif current_qty_signed < 0:
                            # 當前是空倉，需要反轉為多倉
                            # 先關閉空倉
                            if current_position:
                                try:
                                    close_order = close_futures_position(
                                        symbol=symbol,
                                        position_side=current_position.side,
                                        qty=current_position.qty,
                                        position_id=current_position.id
                                    )
                                    exit_price = get_mark_price(symbol) if not close_order.get("avgPrice") else float(close_order["avgPrice"])
                                    current_position.status = "CLOSED"
                                    current_position.closed_at = datetime.now(timezone.utc)
                                    current_position.exit_price = exit_price
                                    current_position.exit_reason = "tv_reverse_to_long"
                                    db.commit()
                                except Exception as e:
                                    logger.exception(f"Bot {bot.id} 反轉倉位時關閉空倉失敗: {e}")
                            
                            # 開新多倉
                            try:
                                # 格式化數量以符合 Binance 精度要求
                                formatted_qty = format_quantity(symbol, target_qty)
                                formatted_qty_float = float(formatted_qty)
                                
                                timestamp = int(time.time() * 1000)
                                client_order_id = f"bot_{bot.id}_{timestamp}"
                                order = client.futures_create_order(
                                    symbol=symbol,
                                    side="BUY",
                                    type="MARKET",
                                    quantity=formatted_qty,
                                    newClientOrderId=client_order_id
                                )
                                
                                entry_price = float(order.get("avgPrice", 0)) or get_mark_price(symbol) or 0.0
                                
                                trail_callback = None
                                if bot.use_dynamic_stop and bot.trailing_callback_percent is not None:
                                    trail_callback = bot.trailing_callback_percent / 100.0
                                elif TRAILING_CONFIG.trailing_enabled and TRAILING_CONFIG.lock_ratio:
                                    trail_callback = TRAILING_CONFIG.lock_ratio
                                
                                position = Position(
                                    bot_id=bot.id,
                                    tv_signal_log_id=log.id,
                                    symbol=symbol,
                                    side="LONG",
                                    qty=formatted_qty_float,
                                    status="OPEN",
                                    binance_order_id=int(order.get("orderId")) if order.get("orderId") else None,
                                    client_order_id=order.get("clientOrderId"),
                                    entry_price=entry_price,
                                    trail_callback=trail_callback,
                                    highest_price=entry_price if trail_callback else None,
                                )
                                db.add(position)
                                db.commit()
                                db.refresh(position)
                                
                                results.append(f"bot={bot.id}, position_id={position.id}, result=reverse_short_to_long")
                                logger.info(f"Bot {bot.id} 成功反轉空倉為多倉 {target_qty}")
                            except Exception as e:
                                error_msg = f"bot={bot.id}, error=reverse_to_long_failed: {str(e)}"
                                logger.exception(f"Bot {bot.id} 反轉為多倉失敗: {e}")
                                results.append(error_msg)
                        else:
                            # 當前無倉位，開新多倉
                            try:
                                # 格式化數量以符合 Binance 精度要求
                                formatted_qty = format_quantity(symbol, target_qty)
                                formatted_qty_float = float(formatted_qty)
                                
                                timestamp = int(time.time() * 1000)
                                client_order_id = f"bot_{bot.id}_{timestamp}"
                                order = client.futures_create_order(
                                    symbol=symbol,
                                    side="BUY",
                                    type="MARKET",
                                    quantity=formatted_qty,
                                    newClientOrderId=client_order_id
                                )
                                
                                entry_price = float(order.get("avgPrice", 0)) or get_mark_price(symbol) or 0.0
                                
                                trail_callback = None
                                if bot.use_dynamic_stop and bot.trailing_callback_percent is not None:
                                    trail_callback = bot.trailing_callback_percent / 100.0
                                elif TRAILING_CONFIG.trailing_enabled and TRAILING_CONFIG.lock_ratio:
                                    trail_callback = TRAILING_CONFIG.lock_ratio
                                
                                position = Position(
                                    bot_id=bot.id,
                                    tv_signal_log_id=log.id,
                                    symbol=symbol,
                                    side="LONG",
                                    qty=formatted_qty_float,
                                    status="OPEN",
                                    binance_order_id=int(order.get("orderId")) if order.get("orderId") else None,
                                    client_order_id=order.get("clientOrderId"),
                                    entry_price=entry_price,
                                    trail_callback=trail_callback,
                                    highest_price=entry_price if trail_callback else None,
                                )
                                db.add(position)
                                db.commit()
                                db.refresh(position)
                                
                                results.append(f"bot={bot.id}, position_id={position.id}, result=open_long")
                                logger.info(f"Bot {bot.id} 成功開多倉 {target_qty}")
                            except Exception as e:
                                error_msg = f"bot={bot.id}, error=open_long_failed: {str(e)}"
                                logger.exception(f"Bot {bot.id} 開多倉失敗: {e}")
                                results.append(error_msg)
                    
                    # Case C: 目標為空倉 (target < 0)
                    else:
                        target_qty_raw = abs(target_position_size)
                        # 應用 max_invest_usdt 限制
                        target_qty = calculate_qty_from_max_invest(bot, symbol, target_qty_raw)
                        
                        if current_qty_signed < 0:
                            # 已經是空倉
                            diff = target_qty - abs(current_qty_signed)
                            if abs(diff) < EPS:
                                results.append(f"bot={bot.id}, result=short_qty_already_match, qty={abs(current_qty_signed)}")
                                logger.info(f"Bot {bot.id} 目標空倉數量已匹配，無需操作")
                            else:
                                # 需要調整（簡化：差異大於 10% 則重新開倉）
                                if abs(diff) / abs(current_qty_signed) > 0.1:
                                    if current_position:
                                        try:
                                            close_order = close_futures_position(
                                                symbol=symbol,
                                                position_side=current_position.side,
                                                qty=current_position.qty,
                                                position_id=current_position.id
                                            )
                                            # 使用統一的函數取得 exit_price（優先使用 avgPrice）
                                            exit_price = get_exit_price_from_order(close_order, symbol)
                                            current_position.status = "CLOSED"
                                            current_position.closed_at = datetime.now(timezone.utc)
                                            current_position.exit_price = exit_price
                                            current_position.exit_reason = "tv_rebalance"
                                            db.commit()
                                        except Exception as e:
                                            logger.exception(f"Bot {bot.id} 調整空倉時關閉舊倉失敗: {e}")
                                    
                                    try:
                                        # 格式化數量以符合 Binance 精度要求
                                        formatted_qty = format_quantity(symbol, target_qty)
                                        formatted_qty_float = float(formatted_qty)
                                        
                                        timestamp = int(time.time() * 1000)
                                        client_order_id = f"bot_{bot.id}_{timestamp}"
                                        order = client.futures_create_order(
                                            symbol=symbol,
                                            side="SELL",
                                            type="MARKET",
                                            quantity=formatted_qty,
                                            newClientOrderId=client_order_id
                                        )
                                        
                                        entry_price = float(order.get("avgPrice", 0)) or get_mark_price(symbol) or 0.0
                                        
                                        trail_callback = None
                                        if bot.use_dynamic_stop and bot.trailing_callback_percent is not None:
                                            trail_callback = bot.trailing_callback_percent / 100.0
                                        elif TRAILING_CONFIG.trailing_enabled and TRAILING_CONFIG.lock_ratio:
                                            trail_callback = TRAILING_CONFIG.lock_ratio
                                        
                                        position = Position(
                                            bot_id=bot.id,
                                            tv_signal_log_id=log.id,
                                            symbol=symbol,
                                            side="SHORT",
                                            qty=formatted_qty_float,
                                            status="OPEN",
                                            binance_order_id=int(order.get("orderId")) if order.get("orderId") else None,
                                            client_order_id=order.get("clientOrderId"),
                                            entry_price=entry_price,
                                            trail_callback=trail_callback,
                                            highest_price=entry_price if trail_callback else None,
                                        )
                                        db.add(position)
                                        db.commit()
                                        db.refresh(position)
                                        
                                        results.append(f"bot={bot.id}, position_id={position.id}, result=rebalance_short")
                                        logger.info(f"Bot {bot.id} 成功調整空倉至 {target_qty}")
                                    except Exception as e:
                                        error_msg = f"bot={bot.id}, error=rebalance_short_failed: {str(e)}"
                                        logger.exception(f"Bot {bot.id} 調整空倉失敗: {e}")
                                        results.append(error_msg)
                                else:
                                    results.append(f"bot={bot.id}, result=short_qty_diff_small, diff={diff}")
                                    logger.info(f"Bot {bot.id} 空倉數量差異小於 10%，跳過調整")
                        elif current_qty_signed > 0:
                            # 當前是多倉，需要反轉為空倉
                            if current_position:
                                try:
                                    close_order = close_futures_position(
                                        symbol=symbol,
                                        position_side=current_position.side,
                                        qty=current_position.qty,
                                        position_id=current_position.id
                                    )
                                    exit_price = get_mark_price(symbol) if not close_order.get("avgPrice") else float(close_order["avgPrice"])
                                    current_position.status = "CLOSED"
                                    current_position.closed_at = datetime.now(timezone.utc)
                                    current_position.exit_price = exit_price
                                    current_position.exit_reason = "tv_reverse_to_short"
                                    db.commit()
                                except Exception as e:
                                    logger.exception(f"Bot {bot.id} 反轉倉位時關閉多倉失敗: {e}")
                            
                            try:
                                # 格式化數量以符合 Binance 精度要求
                                formatted_qty = format_quantity(symbol, target_qty)
                                formatted_qty_float = float(formatted_qty)
                                
                                timestamp = int(time.time() * 1000)
                                client_order_id = f"bot_{bot.id}_{timestamp}"
                                order = client.futures_create_order(
                                    symbol=symbol,
                                    side="SELL",
                                    type="MARKET",
                                    quantity=formatted_qty,
                                    newClientOrderId=client_order_id
                                )
                                
                                entry_price = float(order.get("avgPrice", 0)) or get_mark_price(symbol) or 0.0
                                
                                trail_callback = None
                                if bot.use_dynamic_stop and bot.trailing_callback_percent is not None:
                                    trail_callback = bot.trailing_callback_percent / 100.0
                                elif TRAILING_CONFIG.trailing_enabled and TRAILING_CONFIG.lock_ratio:
                                    trail_callback = TRAILING_CONFIG.lock_ratio
                                
                                position = Position(
                                    bot_id=bot.id,
                                    tv_signal_log_id=log.id,
                                    symbol=symbol,
                                    side="SHORT",
                                    qty=formatted_qty_float,
                                    status="OPEN",
                                    binance_order_id=int(order.get("orderId")) if order.get("orderId") else None,
                                    client_order_id=order.get("clientOrderId"),
                                    entry_price=entry_price,
                                    trail_callback=trail_callback,
                                    highest_price=entry_price if trail_callback else None,
                                )
                                db.add(position)
                                db.commit()
                                db.refresh(position)
                                
                                results.append(f"bot={bot.id}, position_id={position.id}, result=reverse_long_to_short")
                                logger.info(f"Bot {bot.id} 成功反轉多倉為空倉 {target_qty}")
                            except Exception as e:
                                error_msg = f"bot={bot.id}, error=reverse_to_short_failed: {str(e)}"
                                logger.exception(f"Bot {bot.id} 反轉為空倉失敗: {e}")
                                results.append(error_msg)
                        else:
                            # 當前無倉位，開新空倉
                            try:
                                # 格式化數量以符合 Binance 精度要求
                                formatted_qty = format_quantity(symbol, target_qty)
                                formatted_qty_float = float(formatted_qty)
                                
                                timestamp = int(time.time() * 1000)
                                client_order_id = f"bot_{bot.id}_{timestamp}"
                                order = client.futures_create_order(
                                    symbol=symbol,
                                    side="SELL",
                                    type="MARKET",
                                    quantity=formatted_qty,
                                    newClientOrderId=client_order_id
                                )
                                
                                entry_price = float(order.get("avgPrice", 0)) or get_mark_price(symbol) or 0.0
                                
                                trail_callback = None
                                if bot.use_dynamic_stop and bot.trailing_callback_percent is not None:
                                    trail_callback = bot.trailing_callback_percent / 100.0
                                elif TRAILING_CONFIG.trailing_enabled and TRAILING_CONFIG.lock_ratio:
                                    trail_callback = TRAILING_CONFIG.lock_ratio
                                
                                position = Position(
                                    bot_id=bot.id,
                                    tv_signal_log_id=log.id,
                                    symbol=symbol,
                                    side="SHORT",
                                    qty=formatted_qty_float,
                                    status="OPEN",
                                    binance_order_id=int(order.get("orderId")) if order.get("orderId") else None,
                                    client_order_id=order.get("clientOrderId"),
                                    entry_price=entry_price,
                                    trail_callback=trail_callback,
                                    highest_price=entry_price if trail_callback else None,
                                )
                                db.add(position)
                                db.commit()
                                db.refresh(position)
                                
                                results.append(f"bot={bot.id}, position_id={position.id}, result=open_short")
                                logger.info(f"Bot {bot.id} 成功開空倉 {target_qty}")
                            except Exception as e:
                                error_msg = f"bot={bot.id}, error=open_short_failed: {str(e)}"
                                logger.exception(f"Bot {bot.id} 開空倉失敗: {e}")
                                results.append(error_msg)
                
            except Exception as e:
                error_msg = f"bot={bot.id}, error={str(e)}"
                logger.exception(f"Bot {bot.id} ({bot.name}) 處理失敗: {e}")
                results.append(error_msg)
                # 繼續處理下一個 bot
        
        # 4) 更新 log 的 processed 和 process_result（總是執行）
        log.processed = True
        log.process_result = "; ".join(results)
        db.commit()
        
        return {
            "success": True,
            "message": "訂單建立成功",
            "signal_log_id": log.id,
            "results": results
        }
    
    except HTTPException as http_err:
        # 如果是 HTTPException，且有 log，嘗試更新 log
        if log:
            try:
                log.processed = True
                log.process_result = f"HTTPException: {str(http_err.detail)}"
                db.commit()
            except:
                pass
        raise
    except Exception as e:
        # 其他錯誤，如果有 log，更新 log
        if log:
            try:
                log.processed = True
                log.process_result = f"ERROR: {str(e)}"
                db.commit()
            except:
                pass
        logger.exception(f"處理 TradingView webhook 時發生錯誤: {e}")
        raise HTTPException(status_code=500, detail=f"處理失敗: {str(e)}")




@app.get("/positions", response_model=List[PositionOut])
async def get_positions(
    user: dict = Depends(require_admin_user),
    symbol: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    查詢所有倉位記錄
    
    此端點僅限已登入且通過管理員驗證的使用者（Google OAuth + ADMIN_GOOGLE_EMAIL）使用。
    支援依交易對、狀態、日期範圍篩選。
    
    Args:
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
        symbol: 交易對篩選（可選）
        status: 狀態篩選（可選）
        start_date: 開始日期（YYYY-MM-DD格式，可選）
        end_date: 結束日期（YYYY-MM-DD格式，可選）
        db: 資料庫 Session
    
    Returns:
        List[PositionOut]: 倉位記錄列表
    """
    query = db.query(Position)
    
    if symbol:
        query = query.filter(Position.symbol == symbol.upper())
    
    if status:
        query = query.filter(Position.status == status.upper())
    
    if start_date:
        try:
            start_datetime = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            query = query.filter(Position.created_at >= start_datetime)
        except ValueError:
            logger.warning(f"Invalid start_date format: {start_date}")
    
    if end_date:
        try:
            end_datetime = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            # 結束日期包含整天，所以加一天並使用 < 而不是 <=
            end_datetime = end_datetime + timedelta(days=1)
            query = query.filter(Position.created_at < end_datetime)
        except ValueError:
            logger.warning(f"Invalid end_date format: {end_date}")
    
    positions = query.order_by(Position.created_at.desc()).all()
    
    # 計算每個 position 的實際使用的值和來源標記
    result = []
    for pos in positions:
        pos_dict = pos.to_dict()
        
        # 計算實際使用的值和來源標記
        # Profit Threshold
        profit_threshold_value = None
        profit_threshold_source = None
        if pos.dyn_profit_threshold_pct is not None:
            profit_threshold_value = pos.dyn_profit_threshold_pct
            profit_threshold_source = "override"  # position 的設定視為 override
        elif TRAILING_CONFIG.profit_threshold_pct is not None:
            profit_threshold_value = TRAILING_CONFIG.profit_threshold_pct
            profit_threshold_source = "global"
        else:
            profit_threshold_value = DYN_PROFIT_THRESHOLD_PCT
            profit_threshold_source = "default"
        
        # Lock Ratio
        lock_ratio_value = None
        lock_ratio_source = None
        if pos.trail_callback is not None:
            lock_ratio_value = pos.trail_callback
            lock_ratio_source = "override"  # position 的設定視為 override
        elif TRAILING_CONFIG.lock_ratio is not None:
            lock_ratio_value = TRAILING_CONFIG.lock_ratio
            lock_ratio_source = "global"
        else:
            lock_ratio_value = DYN_LOCK_RATIO_DEFAULT
            lock_ratio_source = "default"
        
        # Base SL%
        base_sl_value = None
        base_sl_source = None
        if pos.base_stop_loss_pct is not None:
            base_sl_value = pos.base_stop_loss_pct
            base_sl_source = "override"  # position 的設定視為 override
        elif TRAILING_CONFIG.base_sl_pct is not None:
            base_sl_value = TRAILING_CONFIG.base_sl_pct
            base_sl_source = "global"
        else:
            base_sl_value = DYN_BASE_SL_PCT
            base_sl_source = "default"
        
        # 計算停損狀態（僅對 OPEN 狀態的倉位）
        stop_mode = None
        base_stop_price = None
        dynamic_stop_price = None
        if pos.status == "OPEN" and pos.entry_price and pos.entry_price > 0:
            try:
                # 獲取當前標記價格
                current_mark_price = get_mark_price(pos.symbol)
                if current_mark_price and current_mark_price > 0:
                    # 計算 unrealized_pnl_pct（PnL%）
                    calculated_unrealized_pnl_pct = None
                    if pos.entry_price > 0 and pos.qty > 0:
                        # 獲取 leverage
                        leverage = 20  # 默認杠桿
                        if pos.bot_id:
                            try:
                                from models import BotConfig
                                bot = db.query(BotConfig).filter(BotConfig.id == pos.bot_id).first()
                                if bot and bot.leverage:
                                    leverage = bot.leverage
                            except Exception:
                                pass
                        # 計算 unrealized PnL
                        if pos.side == "LONG":
                            unrealized_pnl_amount = (current_mark_price - pos.entry_price) * pos.qty
                        else:  # SHORT
                            unrealized_pnl_amount = (pos.entry_price - current_mark_price) * pos.qty
                        # 計算保證金
                        notional = pos.entry_price * pos.qty
                        if notional > 0 and leverage > 0:
                            margin = notional / leverage
                            if margin > 0:
                                calculated_unrealized_pnl_pct = (unrealized_pnl_amount / margin) * 100.0
                    
                    # 使用 compute_stop_state 計算停損狀態（傳入 leverage 和 qty）
                    stop_state = compute_stop_state(pos, current_mark_price, calculated_unrealized_pnl_pct, leverage, pos.qty)
                    stop_mode = stop_state.stop_mode if stop_state.stop_mode != "none" else None
                    base_stop_price = round(stop_state.base_stop_price, 4) if stop_state.base_stop_price is not None and stop_state.base_stop_price > 0 else None
                    dynamic_stop_price = round(stop_state.dynamic_stop_price, 4) if stop_state.dynamic_stop_price is not None and stop_state.dynamic_stop_price > 0 else None
            except Exception as e:
                logger.debug(f"計算倉位 {pos.id} 的停損狀態失敗: {e}")
        
        # 添加額外字段
        pos_dict.update({
            "profit_threshold_value": profit_threshold_value,
            "profit_threshold_source": profit_threshold_source,
            "lock_ratio_value": lock_ratio_value,
            "lock_ratio_source": lock_ratio_source,
            "base_sl_value": base_sl_value,
            "base_sl_source": base_sl_source,
            "stop_mode": stop_mode,
            "base_stop_price": base_stop_price,
            "dynamic_stop_price": dynamic_stop_price,
        })
        
        result.append(PositionOut(**pos_dict))
    
    return result


def compute_realized_pnl(position: Position, db: Session = None) -> tuple:
    """
    計算倉位的已實現盈虧和盈虧百分比
    
    Args:
        position: Position 模型實例
        db: 可選的資料庫 Session，用於查詢 Bot 的 leverage
    
    Returns:
        tuple: (realized_pnl, pnl_pct)
    """
    if not position.entry_price or not position.qty:
        return 0.0, 0.0
    
    # 如果 exit_price 為 0 或 None，嘗試從 Binance 查詢
    exit_price = position.exit_price
    if not exit_price or exit_price <= 0:
        if position.status == "CLOSED" and position.binance_order_id:
            try:
                client = get_client()
                order_detail = client.futures_get_order(
                    symbol=position.symbol,
                    orderId=position.binance_order_id
                )
                if order_detail.get("avgPrice"):
                    exit_price = float(order_detail["avgPrice"])
                    if exit_price > 0:
                        # 更新資料庫中的 exit_price（需要在外部傳入 db session）
                        logger.info(f"從 Binance 查詢到倉位 {position.id} 的 exit_price: {exit_price}")
                        # 注意：這裡不直接更新 DB，因為沒有 db session，讓調用者處理
                        position.exit_price = exit_price  # 暫時更新物件，但不寫入 DB
            except Exception as e:
                logger.debug(f"查詢倉位 {position.id} 的訂單詳情失敗: {e}")
    
    if not exit_price or exit_price <= 0:
        # 如果還是沒有 exit_price，無法計算 PnL
        return 0.0, 0.0
    
    try:
        entry = float(position.entry_price)
        exit = float(exit_price)  # 使用處理過的 exit_price
        qty = float(position.qty)
        
        if entry <= 0 or qty <= 0:
            return 0.0, 0.0
        
        # 計算已實現盈虧
        if position.side == "LONG":
            realized = (exit - entry) * qty
        else:  # SHORT
            realized = (entry - exit) * qty
        
        # 計算盈虧百分比（基於保證金，而非名義價值）
        # PnL% = (Realized PnL / 保證金) * 100
        # 保證金 = 名義價值 / 杠桿 = (Entry Price * Qty) / Leverage
        entry_notional = entry * qty
        
        # 嘗試從關聯的 Bot 取得 leverage，如果沒有則使用預設值 20（常見的杠桿倍數）
        leverage = 20  # 預設杠桿倍數
        if position.bot_id and db is not None:
            try:
                from models import BotConfig
                bot = db.query(BotConfig).filter(BotConfig.id == position.bot_id).first()
                if bot and bot.leverage:
                    leverage = bot.leverage
            except Exception as e:
                logger.debug(f"無法取得倉位 {position.id} 的 Bot leverage: {e}")
                # 使用預設值 20
        
        if entry_notional > 0 and leverage > 0:
            margin = entry_notional / leverage
            if margin > 0:
                pnl_pct = (realized / margin) * 100.0
            else:
                pnl_pct = 0.0
        else:
            pnl_pct = 0.0
        
        return realized, pnl_pct
    except (ValueError, TypeError) as e:
        logger.warning(f"計算倉位 {position.id} 的 PnL 時發生錯誤: {e}")
        return 0.0, 0.0


@app.get("/bot-positions/stats")
async def get_bot_positions_stats(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    user: dict = Depends(require_admin_user),
    db: Session = Depends(get_db)
):
    """
    查詢 Bot/Manual Positions 統計數據（已平倉倉位），支援時間區間篩選
    
    此端點僅限已登入且通過管理員驗證的使用者使用。
    預設查詢最近 7 天的已平倉倉位（包含所有 bot 創建和手動創建的倉位）。
    
    Args:
        start_date: 開始日期（可選，預設為 7 天前）
        end_date: 結束日期（可選，預設為今天）
        user: 管理員使用者資訊
        db: 資料庫 Session
    
    Returns:
        dict: 包含 stats 統計資訊
    """
    # 設定預設日期範圍（最近 7 天）
    if end_date is None:
        end_date = date.today()
    
    if start_date is None:
        start_date = end_date - timedelta(days=7)
    
    # 確保 start_date <= end_date
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    
    # 查詢已平倉的倉位
    start_datetime = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    end_datetime = datetime.combine(end_date + timedelta(days=1), datetime.min.time()).replace(tzinfo=timezone.utc)
    
    query = (
        db.query(Position)
        .filter(Position.status == "CLOSED")
        .filter(Position.closed_at.isnot(None))
        .filter(Position.closed_at >= start_datetime)
        .filter(Position.closed_at < end_datetime)
    )
    
    positions = query.all()
    
    # 計算每一筆 Position 的 PnL，並更新 exit_price 為 0 的倉位
    pnl_list = []
    valid_positions = []  # 有有效 exit_price 的倉位
    for pos in positions:
        # 如果 exit_price 為 0，嘗試從 Binance 查詢並更新
        if (pos.status == "CLOSED" and 
            (not pos.exit_price or pos.exit_price <= 0) and 
            pos.binance_order_id):
            try:
                client = get_client()
                order_detail = client.futures_get_order(
                    symbol=pos.symbol,
                    orderId=pos.binance_order_id
                )
                if order_detail.get("avgPrice"):
                    exit_price = float(order_detail["avgPrice"])
                    if exit_price > 0:
                        pos.exit_price = exit_price
                        db.add(pos)
                        db.commit()
                        logger.info(f"更新倉位 {pos.id} 的 exit_price: {exit_price}")
            except Exception as e:
                logger.debug(f"查詢倉位 {pos.id} 的訂單詳情失敗: {e}")
        
        realized, pnl_pct = compute_realized_pnl(pos, db)
        # 只計算有有效 exit_price 的倉位
        if pos.exit_price and pos.exit_price > 0:
            pnl_list.append(realized)
            valid_positions.append(pos)
    
    # 計算統計數據（只計算有有效 exit_price 的倉位）
    wins = [pnl for pnl in pnl_list if pnl > 0]
    losses = [pnl for pnl in pnl_list if pnl < 0]
    
    win_count = len(wins)
    loss_count = len(losses)
    total_trades = len(valid_positions)  # 使用有效倉位數量
    
    win_rate = (win_count / total_trades * 100.0) if total_trades > 0 else 0.0
    
    profit_sum = sum(wins)
    loss_sum = sum(losses)  # 這會是負數
    
    # PnL Ratio = (平均盈利 / 平均虧損)，而不是 (總盈利 / 總虧損)
    average_profit = profit_sum / win_count if win_count > 0 else 0.0
    average_loss = abs(loss_sum) / loss_count if loss_count > 0 else 0.0
    
    if average_loss > 0:
        pnl_ratio = average_profit / average_loss
    else:
        pnl_ratio = None
    
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate": round(win_rate, 2),
        "profit_sum": round(profit_sum, 4),
        "loss_sum": round(loss_sum, 4),
        "pnl_ratio": round(pnl_ratio, 4) if pnl_ratio is not None else None,
        "total_trades": total_trades,
    }


@app.get("/bot-positions/export")
async def export_bot_positions_excel(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    user: dict = Depends(require_admin_user),
    db: Session = Depends(get_db)
):
    """
    匯出 Bot/Manual Positions 為 Excel 檔案（已平倉倉位）
    
    此端點僅限已登入且通過管理員驗證的使用者使用。
    包含所有 bot 創建和手動創建的倉位。
    
    Args:
        start_date: 開始日期（可選，預設為 7 天前）
        end_date: 結束日期（可選，預設為今天）
        user: 管理員使用者資訊
        db: 資料庫 Session
    
    Returns:
        StreamingResponse: Excel 檔案下載
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill
        
        # 設定預設日期範圍（與 /bot-positions/stats 一致）
        if end_date is None:
            end_date = date.today()
        
        if start_date is None:
            start_date = end_date - timedelta(days=7)
        
        # 確保 start_date <= end_date
        if start_date > end_date:
            start_date, end_date = end_date, start_date
        
        # 查詢已平倉的倉位
        start_datetime = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=timezone.utc)
        end_datetime = datetime.combine(end_date + timedelta(days=1), datetime.min.time()).replace(tzinfo=timezone.utc)
        
        query = (
            db.query(Position)
            .filter(Position.status == "CLOSED")
            .filter(Position.closed_at.isnot(None))
            .filter(Position.closed_at >= start_datetime)
            .filter(Position.closed_at < end_datetime)
            .order_by(Position.closed_at.desc())
        )
        
        positions = query.all()
        
        # 建立 Excel 工作簿
        wb = Workbook()
        ws = wb.active
        ws.title = "Positions"
        
        # 設定標題樣式
        header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF")
        header_alignment = Alignment(horizontal="center", vertical="center")
        
        # 寫入標題行
        headers = [
            "ID", "Symbol", "Side", "Qty", "Entry Price", "Exit Price",
            "Realized PnL", "PnL%", "Exit Reason", "Created At", "Closed At"
        ]
        for col_idx, header in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = header_alignment
        
        # 初始化 row_idx（如果沒有資料，至少從標題行之後開始）
        row_idx = 1
        
        # 寫入資料行
        for row_idx, pos in enumerate(positions, start=2):
            realized, pnl_pct = compute_realized_pnl(pos, db)
            
            ws.cell(row=row_idx, column=1, value=pos.id)
            ws.cell(row=row_idx, column=2, value=pos.symbol)
            ws.cell(row=row_idx, column=3, value=pos.side)
            ws.cell(row=row_idx, column=4, value=pos.qty)
            ws.cell(row=row_idx, column=5, value=pos.entry_price)
            ws.cell(row=row_idx, column=6, value=pos.exit_price)
            ws.cell(row=row_idx, column=7, value=round(realized, 4))
            ws.cell(row=row_idx, column=8, value=round(pnl_pct, 2))
            ws.cell(row=row_idx, column=9, value=pos.exit_reason or "")
            ws.cell(row=row_idx, column=10, value=pos.created_at.isoformat() if pos.created_at else "")
            ws.cell(row=row_idx, column=11, value=pos.closed_at.isoformat() if pos.closed_at else "")
            
            # 根據 PnL 設定顏色
            if realized > 0:
                ws.cell(row=row_idx, column=7).fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
            elif realized < 0:
                ws.cell(row=row_idx, column=7).fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
        
        # 計算統計數據
        wins = [p for p in positions if compute_realized_pnl(p)[0] > 0]
        losses = [p for p in positions if compute_realized_pnl(p)[0] < 0]
        
        win_count = len(wins)
        loss_count = len(losses)
        total_trades = win_count + loss_count
        win_rate = (win_count / total_trades * 100.0) if total_trades > 0 else 0.0
        profit_sum = sum(compute_realized_pnl(p)[0] for p in wins)
        loss_sum = sum(compute_realized_pnl(p)[0] for p in losses)
        # PnL Ratio = (平均盈利 / 平均虧損)，而不是 (總盈利 / 總虧損)
        average_profit = profit_sum / win_count if win_count > 0 else 0.0
        average_loss = abs(loss_sum) / loss_count if loss_count > 0 else 0.0
        pnl_ratio = average_profit / average_loss if average_loss > 0 else None
        
        # 在資料下方寫入統計資訊
        # 如果沒有資料，row_idx 仍然是 1（標題行），所以統計從第 3 行開始
        # 如果有資料，row_idx 是最後一筆資料的行號，統計從 row_idx + 2 開始
        stats_row = row_idx + 2
        ws.cell(row=stats_row, column=1, value="Stats").font = Font(bold=True)
        stats_row += 1
        
        stats_data = [
            ("Start Date", start_date.isoformat()),
            ("End Date", end_date.isoformat()),
            ("Win Count", win_count),
            ("Loss Count", loss_count),
            ("Total Trades", total_trades),
            ("Win Rate (%)", round(win_rate, 2)),
            ("Profit Sum", round(profit_sum, 4)),
            ("Loss Sum", round(loss_sum, 4)),
            ("PnL Ratio", round(pnl_ratio, 4) if pnl_ratio is not None else "N/A"),
        ]
        
        for stat_row_idx, (label, value) in enumerate(stats_data, start=stats_row):
            ws.cell(row=stat_row_idx, column=1, value=label).font = Font(bold=True)
            ws.cell(row=stat_row_idx, column=2, value=value)
        
        # 調整欄位寬度
        column_widths = [8, 12, 8, 10, 12, 12, 12, 10, 15, 20, 20]
        for col_idx, width in enumerate(column_widths, start=1):
            ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = width
        
        # 將工作簿寫入記憶體
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        
        # 產生檔案名稱
        start_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")
        filename = f"bot_positions_{start_str}_{end_str}.xlsx"
        
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="openpyxl 未安裝，請執行: pip install openpyxl"
        )
    except Exception as e:
        logger.exception(f"匯出 Excel 時發生錯誤: {e}")
        raise HTTPException(status_code=500, detail=f"匯出失敗: {str(e)}")


@app.get("/binance/open-positions")
async def get_binance_open_positions(
    user: dict = Depends(require_admin_user),
    db: Session = Depends(get_db)
):
    """
    取得目前 Binance Futures 帳戶的所有未平倉部位（不寫入本地 DB）。
    僅限已登入的管理員使用。
    
    注意：此 endpoint 會使用與 Bot 相同的 Binance 連線設定（測試網/正式網）。
    如果 USE_TESTNET=1，則會查詢 Binance Futures Testnet 的倉位。
    
    每個倉位會包含：
    - 基本資訊（symbol, position_amt, entry_price, mark_price, etc.）
    - unrealized_pnl_pct: 未實現盈虧百分比
    - stop_mode: 停損模式（"dynamic", "base", "none"）
    - base_stop_price: 基礎停損價格（如有）
    - dynamic_stop_price: 動態停損價格（如有）
    
    Args:
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
        db: 資料庫 Session（用於查找對應的本地 Position）
    
    Returns:
        List[dict]: Binance Futures 未平倉部位列表（包含 PnL% 和停損資訊）
    
    Raises:
        HTTPException: 當 Binance API 呼叫失敗時
            - 400: API Key/Secret 未設定
            - 500: 其他 Binance API 錯誤
    """
    try:
        # 嘗試取得 Binance client
        client = get_client()
        
        # 使用 USDT-M Futures position info
        raw_positions = client.futures_position_information()
        
        positions = []
        for item in raw_positions:
            # 只保留持倉不為 0 的部位
            try:
                position_amt = float(item.get("positionAmt", "0") or 0)
            except (ValueError, TypeError):
                position_amt = 0.0
            
            if position_amt == 0:
                # 當 position 關閉時（position_amt == 0），清理追蹤記錄
                symbol_for_cleanup = item.get("symbol", "")
                if symbol_for_cleanup:
                    # 清理對應的追蹤記錄
                    for side_cleanup in ["LONG", "SHORT"]:
                        tracking_key_cleanup = f"{symbol_for_cleanup}|{side_cleanup}"
                        if tracking_key_cleanup in _non_bot_position_tracking:
                            del _non_bot_position_tracking[tracking_key_cleanup]
                            logger.debug(f"清理非 bot 倉位追蹤記錄: {tracking_key_cleanup}")
                continue
            
            # 解析其他欄位（必須在 position_amt != 0 之後）
            
            # 解析其他欄位
            try:
                entry_price = float(item.get("entryPrice", "0") or 0)
                mark_price = float(item.get("markPrice", "0") or 0)
                unrealized_pnl = float(item.get("unRealizedProfit", "0") or 0)
                leverage = int(float(item.get("leverage", "0") or 0))
                isolated_wallet = float(item.get("isolatedWallet", "0") or 0)
                update_time = int(item.get("updateTime", 0) or 0)
            except (ValueError, TypeError) as e:
                logger.warning(f"解析 Binance position 欄位失敗: {item.get('symbol', 'unknown')}, 錯誤: {e}")
                continue
            
            margin_type = item.get("marginType", "")
            symbol = item.get("symbol", "")
            
            # 計算 unrealized PnL 百分比（基於保證金，而非名義價值）
            # PnL% = (Unrealized PnL / 保證金) * 100
            # 保證金 = 名義價值 / 杠桿 = (Position Size * Entry Price) / Leverage
            unrealized_pnl_pct = None
            if entry_price > 0 and abs(position_amt) > 0 and leverage > 0:
                notional = abs(position_amt) * entry_price
                if notional > 0:
                    # 計算保證金
                    margin = notional / leverage
                    if margin > 0:
                        # PnL% = (Unrealized PnL / 保證金) * 100
                        unrealized_pnl_pct = (unrealized_pnl / margin) * 100.0
            # 如果無法計算（例如 leverage = 0），unrealized_pnl_pct 保持為 None
            
            # 計算停損資訊：查找對應的本地 Position
            stop_mode = "none"
            base_stop_price = None
            dynamic_stop_price = None
            
            # 決定本地 Position 的 side
            side_local = "LONG" if position_amt > 0 else "SHORT"
            
            # 查找匹配的本地 Position（最新的 OPEN 倉位）
            # 注意：這個查詢在 try 塊外執行，確保 local_pos 在後續代碼中可用
            # 使用大小寫不敏感的匹配（symbol 應該都是大寫，但為了安全起見）
            local_pos = (
                db.query(Position)
                .filter(
                    Position.symbol == symbol.upper(),
                    Position.side == side_local,
                    Position.status == "OPEN",
                )
                .order_by(Position.id.desc())
                .first()
            )
            
            # 調試日誌：記錄匹配結果
            if local_pos:
                logger.debug(
                    f"Binance position {symbol} ({side_local}) 匹配到本地 Position ID={local_pos.id}, "
                    f"entry_price={local_pos.entry_price}, highest_price={local_pos.highest_price}"
                )
            else:
                logger.debug(
                    f"Binance position {symbol} ({side_local}) 沒有匹配的本地 Position（可能是手動開倉）"
                )
            
            try:
                
                # 清理已關閉的 position 的追蹤記錄
                # 如果找到了本地 Position，說明這是由 bot 創建的，不需要追蹤
                # 如果找不到本地 Position，我們會使用記憶體追蹤來維持 highest_price
                
                if local_pos:
                    # 找到匹配的本地 Position（bot 創建的倉位）
                    logger.debug(
                        f"Binance Live Position {symbol} ({side_local}) 匹配到本地 Position ID={local_pos.id}"
                    )
                    # 檢查是否有停損配置覆寫值（Binance Live Positions 的覆寫優先於本地 Position）
                    override_key = f"{symbol}|{side_local}"
                    overrides = _binance_position_stop_overrides.get(override_key, {})
                    
                    # 如果本地 Position 的 entry_price 無效，使用 Binance 的 entryPrice 作為 fallback
                    if local_pos.entry_price <= 0 and entry_price > 0:
                        logger.warning(
                            f"本地倉位 {local_pos.id} ({symbol}) entry_price={local_pos.entry_price} 無效，"
                            f"使用 Binance entryPrice={entry_price} 作為 fallback 計算停損"
                        )
                        # 建立一個臨時的 Position 物件用於計算
                        from copy import copy
                        temp_pos = copy(local_pos)
                        temp_pos.entry_price = entry_price
                        # 如果 highest_price 也無效，使用當前 mark_price
                        if temp_pos.highest_price is None or temp_pos.highest_price <= 0:
                            temp_pos.highest_price = mark_price
                        # 應用覆寫值（如果存在）
                        if overrides.get("dyn_profit_threshold_pct") is not None:
                            temp_pos.dyn_profit_threshold_pct = overrides["dyn_profit_threshold_pct"]
                        if overrides.get("base_stop_loss_pct") is not None:
                            temp_pos.base_stop_loss_pct = overrides["base_stop_loss_pct"]
                        if overrides.get("trail_callback") is not None:
                            temp_pos.trail_callback = overrides["trail_callback"]
                        # 計算 unrealized_pnl_pct（PnL%）用於判斷是否進入 dynamic mode
                        # 對於 bot 創建的 position，需要計算 PnL%
                        calculated_unrealized_pnl_pct = None
                        if entry_price > 0 and abs(position_amt) > 0 and leverage > 0:
                            notional = abs(position_amt) * entry_price
                            if notional > 0:
                                margin = notional / leverage
                                if margin > 0:
                                    calculated_unrealized_pnl_pct = (unrealized_pnl / margin) * 100.0
                        stop_state = compute_stop_state(temp_pos, mark_price, calculated_unrealized_pnl_pct, leverage, abs(position_amt))
                    else:
                        # 應用覆寫值（如果存在）
                        if overrides.get("dyn_profit_threshold_pct") is not None:
                            local_pos.dyn_profit_threshold_pct = overrides["dyn_profit_threshold_pct"]
                        if overrides.get("base_stop_loss_pct") is not None:
                            local_pos.base_stop_loss_pct = overrides["base_stop_loss_pct"]
                        if overrides.get("trail_callback") is not None:
                            local_pos.trail_callback = overrides["trail_callback"]
                        # 計算 unrealized_pnl_pct（PnL%）用於判斷是否進入 dynamic mode
                        # 對於 bot 創建的 position，需要計算 PnL%
                        calculated_unrealized_pnl_pct = None
                        if entry_price > 0 and abs(position_amt) > 0 and leverage > 0:
                            notional = abs(position_amt) * entry_price
                            if notional > 0:
                                margin = notional / leverage
                                if margin > 0:
                                    calculated_unrealized_pnl_pct = (unrealized_pnl / margin) * 100.0
                        # 使用 compute_stop_state 計算停損狀態（傳入 leverage 和 qty）
                        stop_state = compute_stop_state(local_pos, mark_price, calculated_unrealized_pnl_pct, leverage, abs(position_amt))
                    stop_mode = stop_state.stop_mode
                    base_stop_price = stop_state.base_stop_price
                    dynamic_stop_price = stop_state.dynamic_stop_price
                else:
                    # 非 bot 創建的 position：使用 Binance 資料建立臨時 Position 物件來計算停損
                    # 關鍵：使用記憶體中的追蹤記錄來維持歷史最高/最低價格
                    # 這樣即使當前價格下跌，dynamic stop 也能保持穩定
                    logger.debug(
                        f"Binance Live Position {symbol} ({side_local}) 沒有匹配的本地 Position（非 bot 創建）"
                    )
                    
                    # 建立追蹤 key：使用 symbol 和 side 來唯一標識一個 position
                    tracking_key = f"{symbol}|{side_local}"
                    
                    # 檢查是否有停損配置覆寫值（在檢查追蹤記錄之前）
                    override_key = f"{symbol}|{side_local}"
                    overrides = _binance_position_stop_overrides.get(override_key, {})
                    
                    # 檢查是否已有追蹤記錄
                    if tracking_key in _non_bot_position_tracking:
                        tracked = _non_bot_position_tracking[tracking_key]
                        tracked_entry = tracked.get("entry_price")
                        tracked_highest = tracked.get("highest_price")
                        tracked_side = tracked.get("side")
                        
                        # 如果 entry_price 改變（可能是同一個 symbol 但不同的 position），重置追蹤
                        # 使用相對誤差而不是絕對誤差，避免小數點精度問題
                        if tracked_entry is None or (abs(tracked_entry - entry_price) / max(abs(tracked_entry), abs(entry_price), 1.0)) > 0.001:
                            logger.debug(
                                f"非 bot 倉位 {symbol} ({side_local}) entry_price 改變："
                                f"舊={tracked_entry}, 新={entry_price}，重置追蹤記錄"
                            )
                            tracked_entry = entry_price
                            tracked_highest = None
                            tracked_side = side_local
                    else:
                        # 首次看到這個 position，初始化追蹤
                        tracked_entry = entry_price
                        tracked_highest = None
                        tracked_side = side_local
                    
                    # 更新歷史最高/最低價格（只能上升/下降，不能回退）
                    if side_local == "LONG":
                        # LONG：highest_price 只能上升，不能下降
                        if tracked_highest is None:
                            tracked_highest = max(mark_price, entry_price) if entry_price > 0 else mark_price
                        else:
                            # 只能更新為更高的價格
                            tracked_highest = max(tracked_highest, mark_price)
                    else:
                        # SHORT：highest_price 欄位實際存儲的是最低價格，只能下降，不能上升
                        if tracked_highest is None:
                            tracked_highest = min(mark_price, entry_price) if entry_price > 0 else mark_price
                        else:
                            # 只能更新為更低的價格
                            tracked_highest = min(tracked_highest, mark_price)
                    
                    # 確保 tracked_highest 不為 None（使用當前 mark_price 作為 fallback）
                    if tracked_highest is None:
                        tracked_highest = mark_price
                    
                    # 更新追蹤記錄
                    _non_bot_position_tracking[tracking_key] = {
                        "entry_price": tracked_entry,
                        "highest_price": tracked_highest,
                        "side": tracked_side
                    }
                    
                    # 建立臨時 Position 物件
                    class TempPosition:
                        def __init__(self, entry_price, side, highest_price=None):
                            self.entry_price = entry_price
                            self.side = side
                            self.highest_price = highest_price  # LONG: 最高價, SHORT: 最低價
                            # 使用覆寫值（如果存在），否則使用 None（會使用全局配置）
                            self.trail_callback = overrides.get("trail_callback")
                            self.dyn_profit_threshold_pct = overrides.get("dyn_profit_threshold_pct")
                            self.base_stop_loss_pct = overrides.get("base_stop_loss_pct")
                    
                    temp_pos = TempPosition(
                        entry_price=tracked_entry if tracked_entry else entry_price,  # 使用追蹤的 entry_price（更準確）
                        side=side_local,
                        highest_price=tracked_highest  # 使用追蹤的歷史最高/最低價格
                    )
                    # 使用已計算的 unrealized_pnl_pct（PnL%）來判斷是否進入 dynamic mode（傳入 leverage 和 qty）
                    stop_state = compute_stop_state(temp_pos, mark_price, unrealized_pnl_pct, leverage, abs(position_amt))
                    stop_mode = stop_state.stop_mode
                    base_stop_price = stop_state.base_stop_price
                    dynamic_stop_price = stop_state.dynamic_stop_price
                    
                    # 調試日誌：記錄非 bot 創建的 position 的停損計算結果
                    # 使用 info 級別以便追蹤問題
                    # 計算 profit_pct 用於調試
                    profit_pct_debug = ((tracked_highest - tracked_entry) / tracked_entry * 100.0) if (tracked_entry and tracked_entry > 0 and tracked_highest) else 0.0
                    # 安全地格式化可能為 None 的值
                    tracked_highest_str = f"{tracked_highest:.4f}" if tracked_highest else "None"
                    base_stop_str = f"{base_stop_price:.4f}" if base_stop_price else "None"
                    dynamic_stop_str = f"{dynamic_stop_price:.4f}" if dynamic_stop_price else "None"
                    logger.info(
                        f"非 bot 創建的倉位 {symbol} ({side_local}) 停損計算："
                        f"entry={entry_price:.4f}, mark={mark_price:.4f}, "
                        f"tracked_highest={tracked_highest_str}, "
                        f"profit_pct={profit_pct_debug:.2f}%, "
                        f"trail_callback={temp_pos.trail_callback}, "
                        f"dyn_profit_threshold_pct={temp_pos.dyn_profit_threshold_pct}, "
                        f"overrides={overrides}, "
                        f"stop_mode={stop_mode}, "
                        f"base_stop={base_stop_str}, "
                        f"dynamic_stop={dynamic_stop_str}"
                    )
                
            except Exception as e:
                # 如果計算停損狀態失敗，記錄警告和完整的堆疊追蹤
                logger.warning(f"計算倉位 {symbol} ({side_local}) 停損狀態時發生錯誤: {e}")
                # 即使計算失敗，也嘗試使用基本配置計算 base stop（如果有配置）
                try:
                    # 使用全局配置嘗試計算 base stop
                    base_sl_pct = TRAILING_CONFIG.base_sl_pct if TRAILING_CONFIG.base_sl_pct is not None else DYN_BASE_SL_PCT
                    if base_sl_pct > 0 and entry_price > 0:
                        if side_local == "LONG":
                            base_stop_price = entry_price * (1 - base_sl_pct / 100.0)
                            stop_mode = "base"
                        else:  # SHORT
                            base_stop_price = entry_price * (1 + base_sl_pct / 100.0)
                            stop_mode = "base"
                        dynamic_stop_price = None
                    else:
                        stop_mode = "none"
                        base_stop_price = None
                        dynamic_stop_price = None
                except Exception as e2:
                    logger.warning(f"計算 base stop 也失敗: {e2}")
                    stop_mode = "none"
                    base_stop_price = None
                    dynamic_stop_price = None
            
            # 檢查是否有停損配置覆寫值（用於前端顯示）
            # override_key 應該已經在 try 塊中定義（兩個分支都會定義）
            if 'override_key' not in locals():
                override_key = f"{symbol}|{side_local}"
            overrides = _binance_position_stop_overrides.get(override_key, {})
            
            # 確保 local_pos 已定義（在 try 塊外已定義，但如果 try 塊失敗，需要重新查找）
            if 'local_pos' not in locals():
                # 如果沒有在 try 塊中定義，重新查找
                local_pos = (
                    db.query(Position)
                    .filter(
                        Position.symbol == symbol,
                        Position.side == side_local,
                        Position.status == "OPEN",
                    )
                    .order_by(Position.id.desc())
                    .first()
                )
            
            # 計算實際使用的值和來源標記
            # 優先順序：override (手動設定) > local_pos (bot position 設定，也視為 override) > global (全局設定) > default (默認值)
            # 來源標記：
            #   "override" - 手動填的停損設定（黃色）
            #   "global" - 全局設定（藍色）
            #   "default" - 默認值（灰色）
            
            # Profit Threshold (dyn_profit_threshold_pct)
            profit_threshold_value = None
            profit_threshold_source = None
            if overrides.get("dyn_profit_threshold_pct") is not None:
                profit_threshold_value = overrides["dyn_profit_threshold_pct"]
                profit_threshold_source = "override"
            elif local_pos and local_pos.dyn_profit_threshold_pct is not None:
                profit_threshold_value = local_pos.dyn_profit_threshold_pct
                profit_threshold_source = "override"  # bot position 的設定也視為 override
            elif TRAILING_CONFIG.profit_threshold_pct is not None:
                profit_threshold_value = TRAILING_CONFIG.profit_threshold_pct
                profit_threshold_source = "global"
            else:
                profit_threshold_value = DYN_PROFIT_THRESHOLD_PCT
                profit_threshold_source = "default"
            
            # Lock Ratio (trail_callback)
            lock_ratio_value = None
            lock_ratio_source = None
            if overrides.get("trail_callback") is not None:
                lock_ratio_value = overrides["trail_callback"]
                lock_ratio_source = "override"
            elif local_pos and local_pos.trail_callback is not None:
                lock_ratio_value = local_pos.trail_callback
                lock_ratio_source = "override"  # bot position 的設定也視為 override
            elif TRAILING_CONFIG.lock_ratio is not None:
                lock_ratio_value = TRAILING_CONFIG.lock_ratio
                lock_ratio_source = "global"
            else:
                lock_ratio_value = DYN_LOCK_RATIO_DEFAULT
                lock_ratio_source = "default"
            
            # Base SL% (base_stop_loss_pct)
            base_sl_value = None
            base_sl_source = None
            if overrides.get("base_stop_loss_pct") is not None:
                base_sl_value = overrides["base_stop_loss_pct"]
                base_sl_source = "override"
            elif local_pos and local_pos.base_stop_loss_pct is not None:
                base_sl_value = local_pos.base_stop_loss_pct
                base_sl_source = "override"  # bot position 的設定也視為 override
            elif TRAILING_CONFIG.base_sl_pct is not None:
                base_sl_value = TRAILING_CONFIG.base_sl_pct
                base_sl_source = "global"
            else:
                base_sl_value = DYN_BASE_SL_PCT
                base_sl_source = "default"
            
            # 確保所有欄位都包含在回應中，無資料時使用 null
            positions.append({
                "symbol": symbol,
                "position_amt": position_amt,
                "entry_price": entry_price,
                "mark_price": mark_price,
                "unrealized_pnl": unrealized_pnl,
                "unrealized_pnl_pct": round(unrealized_pnl_pct, 4) if unrealized_pnl_pct is not None else None,  # 保留 4 位小數，無資料時為 null
                "leverage": leverage,
                "margin_type": margin_type,
                "isolated_wallet": isolated_wallet,
                "update_time": update_time,
                "stop_mode": stop_mode if stop_mode != "none" else None,  # "dynamic", "base", 或 None（前端會顯示為 —）
                "base_stop_price": round(base_stop_price, 4) if base_stop_price is not None and base_stop_price > 0 else None,  # 無資料時為 null，保留 4 位小數
                "dynamic_stop_price": round(dynamic_stop_price, 4) if dynamic_stop_price is not None and dynamic_stop_price > 0 else None,  # 無資料時為 null，保留 4 位小數
                # 添加停損配置覆寫值（用於前端顯示，保留原始覆寫值）
                "dyn_profit_threshold_pct": overrides.get("dyn_profit_threshold_pct"),
                "base_stop_loss_pct": overrides.get("base_stop_loss_pct"),
                "trail_callback": overrides.get("trail_callback"),
                # 添加實際使用的值和來源標記（用於前端顯示和顏色標記）
                "profit_threshold_value": profit_threshold_value,
                "profit_threshold_source": profit_threshold_source,  # "override", "local", "global", "default"
                "lock_ratio_value": lock_ratio_value,
                "lock_ratio_source": lock_ratio_source,  # "override", "local", "global", "default"
                "base_sl_value": base_sl_value,
                "base_sl_source": base_sl_source,  # "override", "local", "global", "default"
                # 添加標識：是否為 bot 創建的倉位（用於前端顯示和區分）
                "is_bot_position": local_pos is not None,
                "bot_position_id": local_pos.id if local_pos else None,
            })
        
        logger.info(
            f"取得 Binance open positions: {len(positions)} 筆 "
            f"(包含 bot 創建的倉位和手動創建的倉位)"
        )
        return positions
        
    except ValueError as e:
        # API Key/Secret 未設定
        error_msg = str(e)
        logger.error(f"Binance API 設定錯誤: {error_msg}")
        raise HTTPException(
            status_code=400,
            detail=f"Binance API 未設定: {error_msg}。請在環境變數中設定 BINANCE_API_KEY 和 BINANCE_API_SECRET。"
        )
    except Exception as e:
        # 其他 Binance API 錯誤
        logger.exception("取得 Binance open positions 失敗")
        raise HTTPException(
            status_code=500,
            detail=f"Binance API 錯誤: {str(e)}"
        )


@app.post("/binance/positions/close")
async def close_binance_live_position(
    payload: BinanceCloseRequest,
    user: dict = Depends(require_admin_user),
    db: Session = Depends(get_db)
):
    """
    關閉 Binance Live Position（不屬於 bot 管理的倉位）。
    僅限已登入的管理員使用。
    
    Args:
        payload: 關倉請求（包含 symbol 和 position_side）
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
    
    Returns:
        dict: 關倉訂單資訊
    
    Raises:
        HTTPException: 當關倉失敗時
    """
    try:
        client = get_client()
        symbol = payload.symbol.upper()
        position_side = payload.position_side.upper()
        
        # 取得最新的倉位資訊
        positions = client.futures_position_information(symbol=symbol)
        
        if not positions:
            raise HTTPException(
                status_code=404,
                detail=f"找不到交易對 {symbol} 的倉位資訊"
            )
        
        # 找到該交易對的倉位（通常只有一個）
        position_info = positions[0]
        
        try:
            position_amt = float(position_info.get("positionAmt", "0") or 0)
        except (ValueError, TypeError):
            position_amt = 0.0
        
        # 檢查倉位是否為 0
        if position_amt == 0:
            raise HTTPException(
                status_code=400,
                detail=f"{symbol} 目前沒有未平倉部位"
            )
        
        # 驗證倉位方向
        actual_side = "LONG" if position_amt > 0 else "SHORT"
        if actual_side != position_side:
            raise HTTPException(
                status_code=400,
                detail=f"倉位方向不匹配：實際為 {actual_side}，請求為 {position_side}"
            )
        
        # 使用 close_futures_position 的邏輯，但不需要 position_id
        # 由於 close_futures_position 需要 position_id，我們直接在這裡實作
        if position_side == "LONG":
            side = "SELL"  # 平多：賣出
        elif position_side == "SHORT":
            side = "BUY"   # 平空：買入
        else:
            raise HTTPException(
                status_code=400,
                detail=f"不支援的 position_side: {position_side}，必須是 LONG 或 SHORT"
            )
        
        qty = abs(position_amt)
        
        # 產生自訂的 client order ID
        timestamp = int(time.time() * 1000)
        client_order_id = f"TVBOT_CLOSE_LIVE_{timestamp}"
        
        logger.info(f"關閉 Binance Live Position: {symbol} {position_side}，數量: {qty}, 下單方向: {side}")
        
        # 建立市價單關倉
        order = client.futures_create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=qty,
            reduceOnly=True,  # 設定為只減倉，確保是平倉單
            newClientOrderId=client_order_id
        )
        
        logger.info(f"成功關閉 Binance Live Position: {symbol}，訂單ID: {order.get('orderId')}")
        
        # 取得平倉價格
        exit_price = get_exit_price_from_order(order, symbol)
        
        # 取得 entry_price（從 Binance position info）
        entry_price = float(position_info.get("entryPrice", "0") or 0)
        if entry_price <= 0:
            # 如果 entry_price 無效，嘗試從追蹤記錄取得
            tracking_key = f"{symbol}|{position_side}"
            if tracking_key in _non_bot_position_tracking:
                tracked_entry = _non_bot_position_tracking[tracking_key].get("entry_price")
                if tracked_entry and tracked_entry > 0:
                    entry_price = tracked_entry
        
        # 查找現有的 OPEN 倉位（優先更新現有倉位，而不是建立新倉位）
        existing_position = (
            db.query(Position)
            .filter(
                Position.symbol == symbol.upper(),
                Position.side == position_side,
                Position.status == "OPEN",
            )
            .order_by(Position.id.desc())
            .first()
        )
        
        try:
            if existing_position:
                # 更新現有倉位
                existing_position.status = "CLOSED"
                existing_position.closed_at = datetime.now(timezone.utc)
                existing_position.exit_price = exit_price
                existing_position.exit_reason = "manual_close"
                # 更新訂單資訊（如果有的話）
                if order.get("orderId"):
                    existing_position.binance_order_id = int(order.get("orderId"))
                if order.get("clientOrderId"):
                    existing_position.client_order_id = order.get("clientOrderId")
                # 如果 entry_price 有效且現有倉位的 entry_price 無效，更新它
                if entry_price > 0 and (existing_position.entry_price <= 0 or existing_position.entry_price is None):
                    existing_position.entry_price = entry_price
                
                db.commit()
                db.refresh(existing_position)
                
                logger.info(
                    f"已更新現有倉位 {symbol} ({position_side}) "
                    f"(position_id={existing_position.id}, exit_reason=manual_close, exit_price={exit_price})"
                )
                position = existing_position
            else:
                # 沒有現有倉位，建立新的 Position 記錄（用於統計計算）
                position = Position(
                    bot_id=None,  # 非 bot 創建的倉位
                    tv_signal_log_id=None,  # 非 bot 創建的倉位
                    symbol=symbol.upper(),
                    side=position_side,
                    qty=qty,
                    entry_price=entry_price if entry_price > 0 else exit_price,  # 如果 entry_price 無效，使用 exit_price 作為 fallback
                    exit_price=exit_price,
                    status="CLOSED",
                    closed_at=datetime.now(timezone.utc),
                    exit_reason="manual_close",  # 手動關閉
                    binance_order_id=int(order.get("orderId")) if order.get("orderId") else None,
                    client_order_id=order.get("clientOrderId"),
                )
                
                db.add(position)
                db.commit()
                db.refresh(position)
                
                logger.info(
                    f"非 bot 創建倉位 {symbol} ({position_side}) 已建立資料庫記錄 "
                    f"(position_id={position.id}, exit_reason=manual_close, exit_price={exit_price})"
                )
            
            # 清理追蹤記錄
            tracking_key = f"{symbol}|{position_side}"
            if tracking_key in _non_bot_position_tracking:
                del _non_bot_position_tracking[tracking_key]
                logger.debug(f"清理非 bot 倉位追蹤記錄: {tracking_key}")
            
        except Exception as e:
            logger.error(f"更新/建立倉位 {symbol} ({position_side}) 資料庫記錄失敗: {e}")
            db.rollback()
            raise
        
        # 返回關鍵資訊
        return {
            "success": True,
            "symbol": symbol,
            "position_side": position_side,
            "order_id": order.get("orderId"),
            "client_order_id": order.get("clientOrderId"),
            "executed_qty": float(order.get("executedQty", 0) or 0),
            "avg_price": float(order.get("avgPrice", 0) or 0),
            "status": order.get("status"),
        }
        
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Binance API 設定錯誤: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Binance API 未設定: {str(e)}。請在環境變數中設定 BINANCE_API_KEY 和 BINANCE_API_SECRET。"
        )
    except Exception as e:
        logger.exception("關閉 Binance Live Position 失敗")
        raise HTTPException(
            status_code=500,
            detail=f"關閉倉位失敗: {str(e)}"
        )


@app.get("/settings/trailing", response_model=TrailingConfig)
async def get_trailing_settings(
    user: dict = Depends(require_admin_user)
):
    """
    取得 Trailing Stop 全域設定。
    僅限已登入的管理員使用。
    
    Args:
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
    
    Returns:
        TrailingConfig: 目前的 Trailing 設定
    """
    return TRAILING_CONFIG


@app.post("/settings/trailing", response_model=TrailingConfig)
async def update_trailing_settings(
    payload: TrailingConfigUpdate,
    user: dict = Depends(require_admin_user),
    db: Session = Depends(get_db)
):
    """
    更新 Trailing Stop 全域設定。
    僅限已登入的管理員使用。
    
    當 lock_ratio 更新時，會自動更新所有 OPEN 狀態的倉位中，
    原本使用舊的全局 lock_ratio 的倉位（trail_callback 等於舊值），
    讓它們使用新的 lock_ratio。
    
    Args:
        payload: 要更新的設定（只更新提供的欄位）
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
        db: 資料庫 Session
    
    Returns:
        TrailingConfig: 更新後的 Trailing 設定
    
    Raises:
        HTTPException: 當設定值無效時
    """
    global TRAILING_CONFIG
    
    # 保存舊的 lock_ratio 值（用於更新使用舊全局值的倉位）- 使用 LONG 作為參考
    old_lock_ratio_long = TRAILING_CONFIG.long_config.lock_ratio
    old_lock_ratio_short = TRAILING_CONFIG.short_config.lock_ratio
    
    # 使用 dict() 方法（Pydantic v1/v2 兼容）
    if hasattr(TRAILING_CONFIG, 'model_dump'):
        updated = TRAILING_CONFIG.model_dump()
    else:
        updated = TRAILING_CONFIG.dict()
    
    if hasattr(payload, 'model_dump'):
        data = payload.model_dump(exclude_unset=True)
    else:
        data = payload.dict(exclude_unset=True)
    
    # Force trailing_enabled and auto_close_enabled to always be True
    data['trailing_enabled'] = True
    data['auto_close_enabled'] = True
    
    # 處理向後兼容：如果提供了舊格式的設定（profit_threshold_pct, lock_ratio, base_sl_pct），
    # 同時更新 LONG 和 SHORT 的設定
    if "profit_threshold_pct" in data or "lock_ratio" in data or "base_sl_pct" in data:
        if "long_config" not in data:
            if hasattr(TRAILING_CONFIG.long_config, 'model_dump'):
                data['long_config'] = TRAILING_CONFIG.long_config.model_dump()
            else:
                data['long_config'] = TRAILING_CONFIG.long_config.dict()
        if "short_config" not in data:
            if hasattr(TRAILING_CONFIG.short_config, 'model_dump'):
                data['short_config'] = TRAILING_CONFIG.short_config.model_dump()
            else:
                data['short_config'] = TRAILING_CONFIG.short_config.dict()
        
        # 同時更新 LONG 和 SHORT
        if "profit_threshold_pct" in data:
            data['long_config']['profit_threshold_pct'] = data['profit_threshold_pct']
            data['short_config']['profit_threshold_pct'] = data['profit_threshold_pct']
            del data['profit_threshold_pct']
        
        if "lock_ratio" in data:
            data['long_config']['lock_ratio'] = data['lock_ratio']
            data['short_config']['lock_ratio'] = data['lock_ratio']
            del data['lock_ratio']
        
        if "base_sl_pct" in data:
            data['long_config']['base_sl_pct'] = data['base_sl_pct']
            data['short_config']['base_sl_pct'] = data['base_sl_pct']
            del data['base_sl_pct']
    
    # 處理 LONG 設定
    if "long_config" in data:
        long_data = data['long_config']
        if not isinstance(long_data, dict):
            if hasattr(long_data, 'model_dump'):
                long_data = long_data.model_dump(exclude_unset=True)
            else:
                long_data = long_data.dict(exclude_unset=True)
        
        # 驗證 LONG 設定
        if "lock_ratio" in long_data:
            if long_data["lock_ratio"] is not None and long_data["lock_ratio"] < 0:
                raise HTTPException(status_code=400, detail="LONG lock_ratio 不能小於 0")
            if long_data["lock_ratio"] is not None and long_data["lock_ratio"] > 1:
                logger.warning(f"LONG lock_ratio > 1（值={long_data['lock_ratio']}），已強制調整為 1.0")
                long_data["lock_ratio"] = 1.0
        
        if "profit_threshold_pct" in long_data:
            if long_data["profit_threshold_pct"] is not None and long_data["profit_threshold_pct"] < 0:
                raise HTTPException(status_code=400, detail="LONG profit_threshold_pct 不能小於 0")
        
        if "base_sl_pct" in long_data:
            if long_data["base_sl_pct"] is not None and long_data["base_sl_pct"] < 0:
                raise HTTPException(status_code=400, detail="LONG base_sl_pct 不能小於 0")
        
        # 更新 LONG 設定
        if 'long_config' not in updated:
            updated['long_config'] = {}
        updated['long_config'].update(long_data)
        data['long_config'] = updated['long_config']
    
    # 處理 SHORT 設定
    if "short_config" in data:
        short_data = data['short_config']
        if not isinstance(short_data, dict):
            if hasattr(short_data, 'model_dump'):
                short_data = short_data.model_dump(exclude_unset=True)
            else:
                short_data = short_data.dict(exclude_unset=True)
        
        # 驗證 SHORT 設定
        if "lock_ratio" in short_data:
            if short_data["lock_ratio"] is not None and short_data["lock_ratio"] < 0:
                raise HTTPException(status_code=400, detail="SHORT lock_ratio 不能小於 0")
            if short_data["lock_ratio"] is not None and short_data["lock_ratio"] > 1:
                logger.warning(f"SHORT lock_ratio > 1（值={short_data['lock_ratio']}），已強制調整為 1.0")
                short_data["lock_ratio"] = 1.0
        
        if "profit_threshold_pct" in short_data:
            if short_data["profit_threshold_pct"] is not None and short_data["profit_threshold_pct"] < 0:
                raise HTTPException(status_code=400, detail="SHORT profit_threshold_pct 不能小於 0")
        
        if "base_sl_pct" in short_data:
            if short_data["base_sl_pct"] is not None and short_data["base_sl_pct"] < 0:
                raise HTTPException(status_code=400, detail="SHORT base_sl_pct 不能小於 0")
        
        # 更新 SHORT 設定
        if 'short_config' not in updated:
            updated['short_config'] = {}
        updated['short_config'].update(short_data)
        data['short_config'] = updated['short_config']
    
    # 更新其他欄位（trailing_enabled, auto_close_enabled）
    for key in ['trailing_enabled', 'auto_close_enabled']:
        if key in data:
            updated[key] = data[key]
    
    # 重新建立 TrailingConfig 物件
    TRAILING_CONFIG = TrailingConfig(**updated)
    
    # 處理 lock_ratio 更新後的倉位同步
    # 如果 LONG 或 SHORT 的 lock_ratio 被更新，更新對應的 OPEN 倉位
    for side_name, side_key, old_ratio in [("LONG", "long_config", old_lock_ratio_long), 
                                           ("SHORT", "short_config", old_lock_ratio_short)]:
        side_config_updated = "long_config" in data or "short_config" in data
        new_ratio = getattr(TRAILING_CONFIG, side_key).lock_ratio
        
        if side_config_updated and old_ratio != new_ratio:
            try:
                # 找出所有 OPEN 狀態、對應方向且 trail_callback 等於舊全局 lock_ratio 的倉位
                if old_ratio is not None:
                    positions_to_update = (
                        db.query(Position)
                        .filter(
                            Position.status == "OPEN",
                            Position.side == side_name,
                            Position.trail_callback == old_ratio
                        )
                        .all()
                    )
                else:
                    positions_to_update = (
                        db.query(Position)
                        .filter(
                            Position.status == "OPEN",
                            Position.side == side_name,
                            Position.trail_callback.is_(None)
                        )
                        .all()
                    )
                
                if positions_to_update:
                    for position in positions_to_update:
                        position.trail_callback = new_ratio
                        logger.info(
                            f"更新倉位 {position.id} ({position.symbol}) {side_name} 的 lock_ratio: "
                            f"{old_ratio} -> {new_ratio} (跟隨全局配置更新)"
                        )
                    
                    db.commit()
                    logger.info(f"已更新 {len(positions_to_update)} 個 {side_name} OPEN 倉位的 lock_ratio 以匹配新的全局配置")
            except Exception as e:
                logger.error(f"更新 {side_name} 倉位的 lock_ratio 時發生錯誤: {e}", exc_info=True)
                db.rollback()
    
    # 使用兼容的序列化方法
    if hasattr(TRAILING_CONFIG, 'model_dump_json'):
        config_json = TRAILING_CONFIG.model_dump_json()
    else:
        import json
        config_json = json.dumps(TRAILING_CONFIG.dict())
    
    logger.info(f"更新 Trailing 設定: {config_json}")
    return TRAILING_CONFIG


@app.get("/signals", response_model=List[dict])
async def list_signals(
    limit: int = 50,
    db: Session = Depends(get_db),
    user: dict = Depends(require_admin_user)
):
    """
    取得 TradingView Signal 日誌列表
    
    僅限已登入的管理員使用。
    
    Args:
        limit: 返回的最大數量，預設 50
        db: 資料庫 Session
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
    
    Returns:
        List[dict]: Signal 日誌列表
    """
    q = (
        db.query(TradingViewSignalLog)
        .order_by(TradingViewSignalLog.id.desc())
        .limit(limit)
    )
    rows = q.all()
    return [
        {
            "id": r.id,
            "bot_key": r.bot_key,
            "signal_id": r.signal_id,
            "symbol": r.symbol,
            "side": r.side,
            "qty": r.qty,
            "position_size": r.position_size,
            "received_at": r.received_at.isoformat() if r.received_at else None,
            "processed": r.processed,
            "process_result": r.process_result,
            "raw_payload": r.raw_payload,
        }
        for r in rows
    ]


@app.get("/signals/{signal_id}", response_model=dict)
async def get_signal_detail(
    signal_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_admin_user)
):
    """
    取得單筆 TradingView Signal 詳細資料（包含 raw_payload）
    
    僅限已登入的管理員使用。
    
    Args:
        signal_id: Signal Log ID
        db: 資料庫 Session
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
    
    Returns:
        dict: Signal 詳細資料，包含 raw_payload
    
    Raises:
        HTTPException: 當 Signal 不存在時
    """
    signal = db.query(TradingViewSignalLog).filter(TradingViewSignalLog.id == signal_id).first()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    
    return {
        "id": signal.id,
        "bot_key": signal.bot_key,
        "signal_id": signal.signal_id,
        "symbol": signal.symbol,
        "side": signal.side,
        "qty": signal.qty,
        "position_size": signal.position_size,
        "raw_body": signal.raw_body,
        "raw_payload": signal.raw_payload,
        "received_at": signal.received_at.isoformat() if signal.received_at else None,
        "processed": signal.processed,
        "process_result": signal.process_result,
    }


# ==================== Signal Config CRUD APIs ====================

@app.get("/signal-configs", response_model=List[TVSignalConfigOut])
async def list_signal_configs(
    db: Session = Depends(get_db),
    user: dict = Depends(require_admin_user)
):
    """
    取得所有 Signal Config 列表
    
    僅限已登入的管理員使用。
    
    Args:
        db: 資料庫 Session
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
    
    Returns:
        List[TVSignalConfigOut]: Signal Config 列表
    """
    configs = db.query(TVSignalConfig).order_by(TVSignalConfig.id.desc()).all()
    return [
        TVSignalConfigOut.model_validate(c) if hasattr(TVSignalConfigOut, 'model_validate') else TVSignalConfigOut.from_orm(c)
        for c in configs
    ]


@app.post("/signal-configs", response_model=TVSignalConfigOut)
async def create_signal_config(
    config: TVSignalConfigCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_admin_user)
):
    """
    建立新的 Signal Config
    
    僅限已登入的管理員使用。
    
    Args:
        config: Signal Config 資料
        db: 資料庫 Session
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
    
    Returns:
        TVSignalConfigOut: 建立的 Signal Config
    
    Raises:
        HTTPException: 當 signal_key 已存在時
    """
    # 檢查 signal_key 是否已存在
    existing = db.query(TVSignalConfig).filter(TVSignalConfig.signal_key == config.signal_key).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"signal_key '{config.signal_key}' 已存在")
    
    # 建立
    db_config = TVSignalConfig(
        name=config.name,
        signal_key=config.signal_key,
        description=config.description,
        symbol_hint=config.symbol_hint,
        timeframe_hint=config.timeframe_hint,
        enabled=config.enabled,
    )
    
    db.add(db_config)
    db.commit()
    db.refresh(db_config)
    
    logger.info(f"建立 Signal Config: {db_config.id} ({db_config.name}, signal_key={db_config.signal_key})")
    
    return TVSignalConfigOut.model_validate(db_config) if hasattr(TVSignalConfigOut, 'model_validate') else TVSignalConfigOut.from_orm(db_config)


@app.get("/signal-configs/{signal_id}", response_model=TVSignalConfigOut)
async def get_signal_config(
    signal_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_admin_user)
):
    """
    取得單一 Signal Config
    
    僅限已登入的管理員使用。
    
    Args:
        signal_id: Signal Config ID
        db: 資料庫 Session
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
    
    Returns:
        TVSignalConfigOut: Signal Config
    
    Raises:
        HTTPException: 當 Signal Config 不存在時
    """
    config = db.query(TVSignalConfig).filter(TVSignalConfig.id == signal_id).first()
    if not config:
        raise HTTPException(status_code=404, detail=f"Signal Config {signal_id} 不存在")
    
    return TVSignalConfigOut.model_validate(config) if hasattr(TVSignalConfigOut, 'model_validate') else TVSignalConfigOut.from_orm(config)


@app.put("/signal-configs/{signal_id}", response_model=TVSignalConfigOut)
async def update_signal_config(
    signal_id: int,
    config_update: TVSignalConfigUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_admin_user)
):
    """
    更新 Signal Config
    
    僅限已登入的管理員使用。
    
    Args:
        signal_id: Signal Config ID
        config_update: 更新資料
        db: 資料庫 Session
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
    
    Returns:
        TVSignalConfigOut: 更新後的 Signal Config
    
    Raises:
        HTTPException: 當 Signal Config 不存在或 signal_key 已存在時
    """
    config = db.query(TVSignalConfig).filter(TVSignalConfig.id == signal_id).first()
    if not config:
        raise HTTPException(status_code=404, detail=f"Signal Config {signal_id} 不存在")
    
    # 如果更新 signal_key，檢查是否重複
    if config_update.signal_key is not None and config_update.signal_key != config.signal_key:
        existing = db.query(TVSignalConfig).filter(TVSignalConfig.signal_key == config_update.signal_key).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"signal_key '{config_update.signal_key}' 已存在")
    
    # 取得更新資料
    update_data = config_update.model_dump(exclude_unset=True) if hasattr(config_update, 'model_dump') else config_update.dict(exclude_unset=True)
    
    # 更新欄位
    for key, value in update_data.items():
        if value is not None:
            setattr(config, key, value)
    
    db.commit()
    db.refresh(config)
    
    logger.info(f"更新 Signal Config: {config.id} ({config.name})")
    
    return TVSignalConfigOut.model_validate(config) if hasattr(TVSignalConfigOut, 'model_validate') else TVSignalConfigOut.from_orm(config)


@app.delete("/signal-configs/{signal_id}")
async def delete_signal_config(
    signal_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_admin_user)
):
    """
    刪除 Signal Config
    
    僅限已登入的管理員使用。
    
    注意：如果該 Signal Config 下已有關聯的 Bots，將無法刪除（需先移除關聯或刪除 Bots）。
    或者，可以選擇將 enabled 設為 False（軟刪除）而非真正刪除。
    
    Args:
        signal_id: Signal Config ID
        db: 資料庫 Session
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
    
    Returns:
        dict: 刪除結果
    
    Raises:
        HTTPException: 當 Signal Config 不存在或仍有關聯的 Bots 時
    """
    config = db.query(TVSignalConfig).filter(TVSignalConfig.id == signal_id).first()
    if not config:
        raise HTTPException(status_code=404, detail=f"Signal Config {signal_id} 不存在")
    
    # 檢查是否有關聯的 Bots
    bots_count = db.query(BotConfig).filter(BotConfig.signal_id == signal_id).count()
    if bots_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"無法刪除 Signal Config {signal_id}：仍有 {bots_count} 個 Bot 關聯到此 Signal。請先移除 Bot 關聯或將 Signal 設為 disabled。"
        )
    
    db.delete(config)
    db.commit()
    
    logger.info(f"刪除 Signal Config: {signal_id} ({config.name})")
    
    return {"success": True, "message": f"Signal Config {signal_id} 已刪除"}


@app.get("/bots", response_model=List[BotConfigOut])
async def list_bots(
    db: Session = Depends(get_db),
    user: dict = Depends(require_admin_user)
):
    """
    取得所有 Bot 設定
    
    僅限已登入的管理員使用。
    
    Args:
        db: 資料庫 Session
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
    
    Returns:
        List[BotConfigOut]: Bot 設定列表（包含關聯的 Signal Config 資訊）
    """
    bots = db.query(BotConfig).order_by(BotConfig.id.desc()).all()
    result = []
    for bot in bots:
        # 載入關聯的 signal（如果有的話）
        signal_config_obj = None
        if bot.signal_id:
            signal_config_obj = db.query(TVSignalConfig).filter(TVSignalConfig.id == bot.signal_id).first()
        
        # 手動構建 BotConfigOut，避免 relationship 序列化問題
        bot_out_dict = {
            "id": bot.id,
            "name": bot.name,
            "bot_key": bot.bot_key,
            "enabled": bot.enabled,
            "symbol": bot.symbol,
            "use_signal_side": bot.use_signal_side,
            "fixed_side": bot.fixed_side,
            "qty": bot.qty,
            "max_invest_usdt": bot.max_invest_usdt,
            "leverage": bot.leverage,
            "use_dynamic_stop": bot.use_dynamic_stop,
            "trailing_callback_percent": bot.trailing_callback_percent,
            "base_stop_loss_pct": bot.base_stop_loss_pct,
            "signal_id": bot.signal_id,
            "created_at": bot.created_at,
            "updated_at": bot.updated_at,
        }
        bot_out = BotConfigOut(**bot_out_dict)
        
        if signal_config_obj:
            signal_dict = {
                "id": signal_config_obj.id,
                "name": signal_config_obj.name,
                "signal_key": signal_config_obj.signal_key,
                "description": signal_config_obj.description,
                "symbol_hint": signal_config_obj.symbol_hint,
                "timeframe_hint": signal_config_obj.timeframe_hint,
                "enabled": signal_config_obj.enabled,
                "created_at": signal_config_obj.created_at,
                "updated_at": signal_config_obj.updated_at,
            }
            bot_out.signal = TVSignalConfigOut(**signal_dict)
        
        result.append(bot_out)
    return result


@app.post("/bots", response_model=BotConfigOut)
async def create_bot(
    bot: BotConfigCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_admin_user)
):
    """
    建立新的 Bot 設定
    
    僅限已登入的管理員使用。
    
    Args:
        bot: Bot 設定資料
        db: 資料庫 Session
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
    
    Returns:
        BotConfigOut: 建立的 Bot 設定
    
    Raises:
        HTTPException: 當設定值無效時
    """
    # 驗證
    # 如果設定了 max_invest_usdt，驗證它必須大於 0
    if bot.max_invest_usdt is not None:
        if bot.max_invest_usdt <= 0:
            raise HTTPException(status_code=400, detail="max_invest_usdt 必須大於 0")
    # 如果沒有設定 max_invest_usdt，則 qty 必須大於 0
    if bot.max_invest_usdt is None:
        if bot.qty <= 0:
            raise HTTPException(status_code=400, detail="qty 必須大於 0（當 max_invest_usdt 未設定時）")
    
    if bot.trailing_callback_percent is not None:
        if bot.trailing_callback_percent < 0 or bot.trailing_callback_percent > 100:
            raise HTTPException(status_code=400, detail="trailing_callback_percent 必須在 0~100 之間")
    
    # 如果 use_signal_side=True，則自動將 fixed_side=None
    if bot.use_signal_side:
        bot.fixed_side = None
    
    # 如果 fixed_side 有值，轉成大寫
    if bot.fixed_side:
        bot.fixed_side = bot.fixed_side.upper()
        if bot.fixed_side not in ["BUY", "SELL"]:
            raise HTTPException(status_code=400, detail="fixed_side 必須是 BUY 或 SELL")
    
    # 檢查 bot_key 是否已存在
    existing = db.query(BotConfig).filter(BotConfig.bot_key == bot.bot_key).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"bot_key '{bot.bot_key}' 已存在")
    
    # 如果提供了 signal_id，驗證 Signal Config 存在且 enabled
    if bot.signal_id is not None:
        signal_config = db.query(TVSignalConfig).filter(TVSignalConfig.id == bot.signal_id).first()
        if not signal_config:
            raise HTTPException(status_code=404, detail=f"找不到 signal_id={bot.signal_id} 的 Signal Config")
        if not signal_config.enabled:
            raise HTTPException(status_code=400, detail=f"Signal Config {bot.signal_id} 未啟用，無法建立關聯的 Bot")
    
    # 建立
    db_bot = BotConfig(
        name=bot.name,
        bot_key=bot.bot_key,
        enabled=bot.enabled,
        symbol=bot.symbol.upper(),
        use_signal_side=bot.use_signal_side,
        fixed_side=bot.fixed_side,
        qty=bot.qty,
        max_invest_usdt=bot.max_invest_usdt,
        leverage=bot.leverage,
        use_dynamic_stop=bot.use_dynamic_stop,
        trailing_callback_percent=bot.trailing_callback_percent,
        base_stop_loss_pct=bot.base_stop_loss_pct,
        signal_id=bot.signal_id,
    )
    
    try:
        db.add(db_bot)
        db.commit()
        db.refresh(db_bot)
        logger.info(f"建立 Bot 設定: {db_bot.id} ({db_bot.name}, bot_key={db_bot.bot_key}, signal_id={db_bot.signal_id}, max_invest_usdt={db_bot.max_invest_usdt})")
    except Exception as e:
        db.rollback()
        logger.error(f"建立 Bot 時發生資料庫錯誤: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"建立 Bot 時發生資料庫錯誤: {str(e)}")
    
    # 載入關聯的 signal（如果有的話）
    signal_config_obj = None
    if db_bot.signal_id:
        signal_config_obj = db.query(TVSignalConfig).filter(TVSignalConfig.id == db_bot.signal_id).first()
    
    # 構建回應，包含 signal 資訊
    # 直接手動構建，避免 relationship 序列化問題
    try:
        bot_out_dict = {
            "id": db_bot.id,
            "name": db_bot.name,
            "bot_key": db_bot.bot_key,
            "enabled": db_bot.enabled,
            "symbol": db_bot.symbol,
            "use_signal_side": db_bot.use_signal_side,
            "fixed_side": db_bot.fixed_side,
            "qty": db_bot.qty,
            "max_invest_usdt": db_bot.max_invest_usdt,
            "leverage": db_bot.leverage,
            "use_dynamic_stop": db_bot.use_dynamic_stop,
            "trailing_callback_percent": db_bot.trailing_callback_percent,
            "base_stop_loss_pct": db_bot.base_stop_loss_pct,
            "signal_id": db_bot.signal_id,
            "created_at": db_bot.created_at,
            "updated_at": db_bot.updated_at,
        }
        bot_out = BotConfigOut(**bot_out_dict)
        
        # 如果 signal 存在，添加到回應中
        if signal_config_obj:
            signal_dict = {
                "id": signal_config_obj.id,
                "name": signal_config_obj.name,
                "signal_key": signal_config_obj.signal_key,
                "description": signal_config_obj.description,
                "symbol_hint": signal_config_obj.symbol_hint,
                "timeframe_hint": signal_config_obj.timeframe_hint,
                "enabled": signal_config_obj.enabled,
                "created_at": signal_config_obj.created_at,
                "updated_at": signal_config_obj.updated_at,
            }
            bot_out.signal = TVSignalConfigOut(**signal_dict)
    except Exception as e:
        logger.error(f"構建 BotConfigOut 回應時發生錯誤: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"建立 Bot 成功，但序列化回應時發生錯誤: {str(e)}")
    
    return bot_out


@app.get("/bots/{bot_id}", response_model=BotConfigOut)
async def get_bot(
    bot_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_admin_user)
):
    """
    取得單一 Bot 設定
    
    僅限已登入的管理員使用。
    
    Args:
        bot_id: Bot ID
        db: 資料庫 Session
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
    
    Returns:
        BotConfigOut: Bot 設定
    
    Raises:
        HTTPException: 當 Bot 不存在時
    """
    bot = db.query(BotConfig).filter(BotConfig.id == bot_id).first()
    if not bot:
        raise HTTPException(status_code=404, detail=f"Bot {bot_id} 不存在")
    
    # 載入關聯的 signal（如果有的話）
    signal_config_obj = None
    if bot.signal_id:
        signal_config_obj = db.query(TVSignalConfig).filter(TVSignalConfig.id == bot.signal_id).first()
    
    # 構建回應，包含 signal 資訊
    bot_out_dict = {
        "id": bot.id,
        "name": bot.name,
        "bot_key": bot.bot_key,
        "enabled": bot.enabled,
        "symbol": bot.symbol,
        "use_signal_side": bot.use_signal_side,
        "fixed_side": bot.fixed_side,
        "qty": bot.qty,
        "max_invest_usdt": bot.max_invest_usdt,
        "leverage": bot.leverage,
        "use_dynamic_stop": bot.use_dynamic_stop,
        "trailing_callback_percent": bot.trailing_callback_percent,
        "base_stop_loss_pct": bot.base_stop_loss_pct,
        "signal_id": bot.signal_id,
        "created_at": bot.created_at,
        "updated_at": bot.updated_at,
    }
    bot_out = BotConfigOut(**bot_out_dict)
    
    if signal_config_obj:
        signal_dict = {
            "id": signal_config_obj.id,
            "name": signal_config_obj.name,
            "signal_key": signal_config_obj.signal_key,
            "description": signal_config_obj.description,
            "symbol_hint": signal_config_obj.symbol_hint,
            "timeframe_hint": signal_config_obj.timeframe_hint,
            "enabled": signal_config_obj.enabled,
            "created_at": signal_config_obj.created_at,
            "updated_at": signal_config_obj.updated_at,
        }
        bot_out.signal = TVSignalConfigOut(**signal_dict)
    
    return bot_out


@app.put("/bots/{bot_id}", response_model=BotConfigOut)
async def update_bot(
    bot_id: int,
    bot_update: BotConfigUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_admin_user)
):
    """
    更新 Bot 設定
    
    僅限已登入的管理員使用。
    
    Args:
        bot_id: Bot ID
        bot_update: 要更新的欄位
        db: 資料庫 Session
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
    
    Returns:
        BotConfigOut: 更新後的 Bot 設定
    
    Raises:
        HTTPException: 當 Bot 不存在或設定值無效時
    """
    bot = db.query(BotConfig).filter(BotConfig.id == bot_id).first()
    if not bot:
        raise HTTPException(status_code=404, detail=f"Bot {bot_id} 不存在")
    
    # 取得更新資料
    update_data = bot_update.model_dump(exclude_unset=True) if hasattr(bot_update, 'model_dump') else bot_update.dict(exclude_unset=True)
    
    # 驗證
    if "max_invest_usdt" in update_data and update_data["max_invest_usdt"] is not None:
        if update_data["max_invest_usdt"] <= 0:
            raise HTTPException(status_code=400, detail="max_invest_usdt 必須大於 0")
    if "qty" in update_data and update_data["qty"] is not None and update_data["qty"] <= 0:
        raise HTTPException(status_code=400, detail="qty 必須大於 0（當 max_invest_usdt 未設定時）")
    
    if "trailing_callback_percent" in update_data and update_data["trailing_callback_percent"] is not None:
        if update_data["trailing_callback_percent"] < 0 or update_data["trailing_callback_percent"] > 100:
            raise HTTPException(status_code=400, detail="trailing_callback_percent 必須在 0~100 之間")
    
    # 如果 use_signal_side=True，則自動將 fixed_side=None
    if update_data.get("use_signal_side") is True:
        update_data["fixed_side"] = None
    
    # 如果 fixed_side 有值，轉成大寫
    if "fixed_side" in update_data and update_data["fixed_side"]:
        update_data["fixed_side"] = update_data["fixed_side"].upper()
        if update_data["fixed_side"] not in ["BUY", "SELL"]:
            raise HTTPException(status_code=400, detail="fixed_side 必須是 BUY 或 SELL")
    
    # 如果更新 signal_id，驗證 Signal Config 存在且 enabled
    if "signal_id" in update_data and update_data["signal_id"] is not None:
        signal_config = db.query(TVSignalConfig).filter(TVSignalConfig.id == update_data["signal_id"]).first()
        if not signal_config:
            raise HTTPException(status_code=404, detail=f"找不到 signal_id={update_data['signal_id']} 的 Signal Config")
        if not signal_config.enabled:
            raise HTTPException(status_code=400, detail=f"Signal Config {update_data['signal_id']} 未啟用，無法關聯到此 Bot")
    
    # 更新
    for key, value in update_data.items():
        if value is not None:
            setattr(bot, key, value)
    
    # 自動更新 updated_at（SQLAlchemy 的 onupdate 會處理，但我們確保一下）
    from datetime import datetime, timezone
    bot.updated_at = datetime.now(timezone.utc)
    
    db.commit()
    db.refresh(bot)
    
    # 載入關聯的 signal（如果有的話）
    signal_config_obj = None
    if bot.signal_id:
        signal_config_obj = db.query(TVSignalConfig).filter(TVSignalConfig.id == bot.signal_id).first()
    
    logger.info(f"更新 Bot 設定: {bot.id} ({bot.name}, signal_id={bot.signal_id})")
    
    # 構建回應，包含 signal 資訊
    bot_out_dict = {
        "id": bot.id,
        "name": bot.name,
        "bot_key": bot.bot_key,
        "enabled": bot.enabled,
        "symbol": bot.symbol,
        "use_signal_side": bot.use_signal_side,
        "fixed_side": bot.fixed_side,
        "qty": bot.qty,
        "max_invest_usdt": bot.max_invest_usdt,
        "leverage": bot.leverage,
        "use_dynamic_stop": bot.use_dynamic_stop,
        "trailing_callback_percent": bot.trailing_callback_percent,
        "base_stop_loss_pct": bot.base_stop_loss_pct,
        "signal_id": bot.signal_id,
        "created_at": bot.created_at,
        "updated_at": bot.updated_at,
    }
    bot_out = BotConfigOut(**bot_out_dict)
    
    if signal_config_obj:
        signal_dict = {
            "id": signal_config_obj.id,
            "name": signal_config_obj.name,
            "signal_key": signal_config_obj.signal_key,
            "description": signal_config_obj.description,
            "symbol_hint": signal_config_obj.symbol_hint,
            "timeframe_hint": signal_config_obj.timeframe_hint,
            "enabled": signal_config_obj.enabled,
            "created_at": signal_config_obj.created_at,
            "updated_at": signal_config_obj.updated_at,
        }
        bot_out.signal = TVSignalConfigOut(**signal_dict)
    
    return bot_out


@app.post("/bots/{bot_id}/enable")
async def enable_bot(
    bot_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_admin_user)
):
    """
    啟用 Bot
    
    僅限已登入的管理員使用。
    
    Args:
        bot_id: Bot ID
        db: 資料庫 Session
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
    
    Returns:
        dict: 操作結果
    
    Raises:
        HTTPException: 當 Bot 不存在時
    """
    bot = db.query(BotConfig).filter(BotConfig.id == bot_id).first()
    if not bot:
        raise HTTPException(status_code=404, detail=f"Bot {bot_id} 不存在")
    
    bot.enabled = True
    bot.updated_at = datetime.now(timezone.utc)
    db.commit()
    
    logger.info(f"啟用 Bot: {bot.id} ({bot.name})")
    
    return {"status": "enabled", "bot_id": bot.id}


@app.post("/bots/{bot_id}/disable")
async def disable_bot(
    bot_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_admin_user)
):
    """
    停用 Bot
    
    僅限已登入的管理員使用。
    
    Args:
        bot_id: Bot ID
        db: 資料庫 Session
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
    
    Returns:
        dict: 操作結果
    
    Raises:
        HTTPException: 當 Bot 不存在時
    """
    bot = db.query(BotConfig).filter(BotConfig.id == bot_id).first()
    if not bot:
        raise HTTPException(status_code=404, detail=f"Bot {bot_id} 不存在")
    
    bot.enabled = False
    bot.updated_at = datetime.now(timezone.utc)
    db.commit()
    
    logger.info(f"停用 Bot: {bot.id} ({bot.name})")
    
    return {"status": "disabled", "bot_id": bot.id}


@app.delete("/bots/{bot_id}")
async def delete_bot(
    bot_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_admin_user)
):
    """
    刪除 Bot
    
    僅限已登入的管理員使用。
    
    注意：刪除 Bot 前會檢查是否有關聯的 OPEN 倉位。如果有，建議先關閉倉位再刪除 Bot。
    
    Args:
        bot_id: Bot ID
        db: 資料庫 Session
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
    
    Returns:
        dict: 刪除結果
    
    Raises:
        HTTPException: 當 Bot 不存在時
    """
    bot = db.query(BotConfig).filter(BotConfig.id == bot_id).first()
    if not bot:
        raise HTTPException(status_code=404, detail=f"Bot {bot_id} 不存在")
    
    # 檢查是否有關聯的 OPEN 倉位
    open_positions_count = db.query(Position).filter(
        Position.bot_id == bot_id,
        Position.status == "OPEN"
    ).count()
    
    if open_positions_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"無法刪除 Bot {bot_id}：仍有 {open_positions_count} 個 OPEN 倉位關聯到此 Bot。請先關閉倉位再刪除 Bot。"
        )
    
    bot_name = bot.name
    db.delete(bot)
    db.commit()
    
    logger.info(f"刪除 Bot: {bot_id} ({bot_name})")
    
    return {"success": True, "message": f"Bot {bot_id} ({bot_name}) 已刪除"}


class BulkUpdateInvestAmountRequest(BaseModel):
    """批量更新投資金額的請求格式"""
    max_invest_usdt: float = Field(..., gt=0, description="新的投資金額（USDT），必須大於 0")
    bot_ids: Optional[List[int]] = Field(None, description="可選的 Bot ID 列表，如果提供則僅更新這些 Bot，否則更新所有 Bot")


@app.post("/bots/bulk-update-invest-amount")
async def bulk_update_invest_amount(
    request: BulkUpdateInvestAmountRequest,
    user: dict = Depends(require_admin_user)
):
    """
    批量調整所有 Bot 的投資金額（max_invest_usdt）
    
    僅限已登入的管理員使用。
    可以一次更新所有 Bot，或僅更新指定的 Bot IDs。
    
    Args:
        request: 包含 max_invest_usdt 和可選的 bot_ids
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
    
    Returns:
        dict: 包含以下欄位的字典：
            - success: 是否成功
            - updated_count: 更新的 Bot 數量
            - bot_ids: 已更新的 Bot ID 列表
            - message: 操作結果訊息
    
    Raises:
        HTTPException: 當 max_invest_usdt 無效時
    """
    try:
        result = update_all_bots_invest_amount(
            max_invest_usdt=request.max_invest_usdt,
            bot_ids=request.bot_ids
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"批量更新 Bot 投資金額失敗: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"批量更新失敗: {str(e)}")


# ==================== Position Stop Config API ====================

class PositionStopConfigUpdate(BaseModel):
    """更新倉位停損配置的請求格式"""
    dyn_profit_threshold_pct: Optional[float] = Field(None, description="PnL% 門檻百分比覆寫（例如 1.0 表示 1%），null 表示使用全局配置")
    base_stop_loss_pct: Optional[float] = Field(None, description="基礎停損百分比覆寫（例如 0.5 表示 0.5%），null 表示使用全局配置")
    trail_callback: Optional[float] = Field(None, description="鎖利比例覆寫（0~1），0 表示僅使用 base stop，null 表示使用全局配置")
    clear_overrides: bool = Field(False, description="如果為 true，清除所有覆寫值（設為 null）")


class PositionMechanismConfigUpdate(BaseModel):
    """更新倉位停損/止盈機制啟用狀態的請求格式"""
    bot_stop_loss_enabled: Optional[bool] = Field(None, description="是否啟用 Bot 內建的停損機制（dynamic stop / base stop）")
    tv_signal_close_enabled: Optional[bool] = Field(None, description="是否啟用 TradingView 訊號關倉機制（position_size=0）")


class PositionMechanismConfigUpdate(BaseModel):
    """更新倉位停損/止盈機制啟用狀態的請求格式"""
    bot_stop_loss_enabled: Optional[bool] = Field(None, description="是否啟用 Bot 內建的停損機制（dynamic stop / base stop）")
    tv_signal_close_enabled: Optional[bool] = Field(None, description="是否啟用 TradingView 訊號關倉機制（position_size=0）")


@app.patch("/positions/{pos_id}/stop-config", response_model=PositionOut)
async def update_position_stop_config(
    pos_id: int,
    update: PositionStopConfigUpdate,
    user: dict = Depends(require_admin_user),
    db: Session = Depends(get_db)
):
    """
    更新倉位的停損配置覆寫值
    
    允許為每筆倉位設定獨立的停損配置，覆寫全局配置：
    - dyn_profit_threshold_pct: PnL% 門檻百分比
    - base_stop_loss_pct: 基礎停損百分比
    - trail_callback: 鎖利比例（0~1），0 表示僅使用 base stop
    
    如果 clear_overrides=True，會清除所有覆寫值（設為 null），恢復使用全局配置。
    
    Args:
        pos_id: 倉位 ID
        update: 更新請求
        user: 管理員使用者資訊
        db: 資料庫 Session
    
    Returns:
        PositionOut: 更新後的倉位資訊
    
    Raises:
        HTTPException: 當倉位不存在時
    """
    position = db.query(Position).filter(Position.id == pos_id).first()
    if not position:
        raise HTTPException(status_code=404, detail="Position not found")
    
    if update.clear_overrides:
        position.dyn_profit_threshold_pct = None
        position.base_stop_loss_pct = None
        # trail_callback 保留原值，除非明確指定
        if update.trail_callback is not None:
            position.trail_callback = update.trail_callback
    else:
        if update.dyn_profit_threshold_pct is not None:
            if update.dyn_profit_threshold_pct < 0:
                logger.warning(
                    f"倉位 {position.id} dyn_profit_threshold_pct < 0 ({update.dyn_profit_threshold_pct})，已設為 0"
                )
                position.dyn_profit_threshold_pct = 0.0
            else:
                position.dyn_profit_threshold_pct = update.dyn_profit_threshold_pct
        
        if update.base_stop_loss_pct is not None:
            if update.base_stop_loss_pct < 0:
                logger.warning(
                    f"倉位 {position.id} base_stop_loss_pct < 0 ({update.base_stop_loss_pct})，已設為 0"
                )
                position.base_stop_loss_pct = 0.0
            else:
                position.base_stop_loss_pct = update.base_stop_loss_pct
        
        if update.trail_callback is not None:
            # clamp to [0, 1] and handle 0 meaning "base stop only"
            val = update.trail_callback
            if val < 0:
                logger.warning(
                    f"倉位 {position.id} trail_callback < 0 ({val})，已設為 0 (base-stop only)"
                )
                val = 0.0
            elif val > 1:
                logger.warning(
                    f"倉位 {position.id} trail_callback > 1 ({val})，已調整為 1.0"
                )
                val = 1.0
            position.trail_callback = val
    
    db.commit()
    db.refresh(position)
    
    logger.info(
        f"倉位 {position.id} ({position.symbol}) 停損配置已更新："
        f"dyn_profit_threshold_pct={position.dyn_profit_threshold_pct}, "
        f"base_stop_loss_pct={position.base_stop_loss_pct}, "
        f"trail_callback={position.trail_callback}"
    )
    
    # 計算實際使用的值和來源標記（與 get_positions 中的邏輯一致）
    pos_dict = position.to_dict()
    
    # Profit Threshold
    profit_threshold_value = None
    profit_threshold_source = None
    if position.dyn_profit_threshold_pct is not None:
        profit_threshold_value = position.dyn_profit_threshold_pct
        profit_threshold_source = "override"
    elif TRAILING_CONFIG.profit_threshold_pct is not None:
        profit_threshold_value = TRAILING_CONFIG.profit_threshold_pct
        profit_threshold_source = "global"
    else:
        profit_threshold_value = DYN_PROFIT_THRESHOLD_PCT
        profit_threshold_source = "default"
    
    # Lock Ratio
    lock_ratio_value = None
    lock_ratio_source = None
    if position.trail_callback is not None:
        lock_ratio_value = position.trail_callback
        lock_ratio_source = "override"
    elif TRAILING_CONFIG.lock_ratio is not None:
        lock_ratio_value = TRAILING_CONFIG.lock_ratio
        lock_ratio_source = "global"
    else:
        lock_ratio_value = DYN_LOCK_RATIO_DEFAULT
        lock_ratio_source = "default"
    
    # Base SL%
    base_sl_value = None
    base_sl_source = None
    if position.base_stop_loss_pct is not None:
        base_sl_value = position.base_stop_loss_pct
        base_sl_source = "override"
    elif TRAILING_CONFIG.base_sl_pct is not None:
        base_sl_value = TRAILING_CONFIG.base_sl_pct
        base_sl_source = "global"
    else:
        base_sl_value = DYN_BASE_SL_PCT
        base_sl_source = "default"
    
    # 添加額外字段
    pos_dict.update({
        "profit_threshold_value": profit_threshold_value,
        "profit_threshold_source": profit_threshold_source,
        "lock_ratio_value": lock_ratio_value,
        "lock_ratio_source": lock_ratio_source,
        "base_sl_value": base_sl_value,
        "base_sl_source": base_sl_source,
    })
    
    # 使用 from_orm 或 model_validate 返回 PositionOut
    return PositionOut(**pos_dict)


@app.patch("/positions/{pos_id}/mechanism-config", response_model=PositionOut)
async def update_position_mechanism_config(
    pos_id: int,
    update: PositionMechanismConfigUpdate,
    user: dict = Depends(require_admin_user),
    db: Session = Depends(get_db)
):
    """
    更新倉位的停損/止盈機制啟用狀態
    
    允許為每筆倉位獨立控制兩種停損/止盈機制：
    - bot_stop_loss_enabled: Bot 內建的停損機制（dynamic stop / base stop）
    - tv_signal_close_enabled: TradingView 訊號關倉機制（position_size=0）
    
    預設值都是 True（兩種機制都啟用）。
    可以設定為 False 來停用特定機制。
    
    範例：
    - bot_stop_loss_enabled=True, tv_signal_close_enabled=False: 只使用 Bot 停損，忽略 TradingView 關倉訊號
    - bot_stop_loss_enabled=False, tv_signal_close_enabled=True: 只使用 TradingView 關倉訊號，不使用 Bot 停損
    - bot_stop_loss_enabled=True, tv_signal_close_enabled=True: 兩種機制都啟用（預設）
    
    Args:
        pos_id: 倉位 ID
        update: 更新請求
        user: 管理員使用者資訊
        db: 資料庫 Session
    
    Returns:
        PositionOut: 更新後的倉位資訊
    
    Raises:
        HTTPException: 當倉位不存在時
    """
    position = db.query(Position).filter(Position.id == pos_id).first()
    if not position:
        raise HTTPException(status_code=404, detail="Position not found")
    
    if position.status != "OPEN":
        raise HTTPException(
            status_code=400, 
            detail=f"只能更新 OPEN 狀態的倉位，當前狀態: {position.status}"
        )
    
    # 更新標誌
    if update.bot_stop_loss_enabled is not None:
        position.bot_stop_loss_enabled = update.bot_stop_loss_enabled
        logger.info(
            f"倉位 {position.id} ({position.symbol}) bot_stop_loss_enabled "
            f"已更新為 {update.bot_stop_loss_enabled}"
        )
    
    if update.tv_signal_close_enabled is not None:
        position.tv_signal_close_enabled = update.tv_signal_close_enabled
        logger.info(
            f"倉位 {position.id} ({position.symbol}) tv_signal_close_enabled "
            f"已更新為 {update.tv_signal_close_enabled}"
        )
    
    db.commit()
    db.refresh(position)
    
    logger.info(
        f"倉位 {position.id} ({position.symbol}) 機制配置已更新："
        f"bot_stop_loss_enabled={position.bot_stop_loss_enabled}, "
        f"tv_signal_close_enabled={position.tv_signal_close_enabled}"
    )
    
    # 使用 to_dict() 然後轉換為 PositionOut，確保包含所有欄位
    pos_dict = position.to_dict()
    return PositionOut(**pos_dict)


class BinancePositionStopConfigUpdate(BaseModel):
    """更新 Binance Live Position 停損配置的請求格式"""
    symbol: str = Field(..., description="交易對，例如：BTCUSDT")
    position_side: str = Field(..., description="倉位方向：LONG 或 SHORT")
    dyn_profit_threshold_pct: Optional[float] = Field(None, description="PnL% 門檻百分比覆寫（例如 1.0 表示 1%），null 表示使用全局配置")
    base_stop_loss_pct: Optional[float] = Field(None, description="基礎停損百分比覆寫（例如 0.5 表示 0.5%），null 表示使用全局配置")
    trail_callback: Optional[float] = Field(None, description="鎖利比例覆寫（0~1），0 表示僅使用 base stop，null 表示使用全局配置")
    clear_overrides: bool = Field(False, description="如果為 true，清除所有覆寫值（設為 null）")


class PortfolioTrailingConfigOut(BaseModel):
    """Portfolio Trailing Stop 設定模型（API 回應用）"""
    enabled: bool = Field(False, description="是否啟用自動賣出")
    target_pnl: Optional[float] = Field(None, description="目標 PnL（USDT），當達到此值時開始追蹤")
    lock_ratio: Optional[float] = Field(None, description="Lock ratio（0~1），如果 None 則使用全局 lock_ratio")
    max_pnl_reached: Optional[float] = Field(None, description="已達到的最大 PnL（只讀）")


class PortfolioTrailingConfigUpdate(BaseModel):
    """更新 Portfolio Trailing Stop 設定的請求格式"""
    enabled: Optional[bool] = None
    target_pnl: Optional[float] = None
    lock_ratio: Optional[float] = None


@app.patch("/binance/positions/stop-config", response_model=dict)
async def update_binance_position_stop_config(
    update: BinancePositionStopConfigUpdate,
    user: dict = Depends(require_admin_user)
):
    """
    更新 Binance Live Position 的停損配置覆寫值
    
    允許為每個 Binance Live Position 設定獨立的停損配置，覆寫全局配置。
    這些覆寫值存儲在記憶體中，應用重啟後會重置。
    
    Args:
        update: 更新請求（包含 symbol, position_side 和覆寫值）
        user: 管理員使用者資訊
    
    Returns:
        dict: 更新結果
    
    Raises:
        HTTPException: 當參數無效時
    """
    if update.position_side.upper() not in ["LONG", "SHORT"]:
        raise HTTPException(status_code=400, detail="position_side 必須是 LONG 或 SHORT")
    
    override_key = f"{update.symbol.upper()}|{update.position_side.upper()}"
    
    if update.clear_overrides:
        # 清除覆寫值
        if override_key in _binance_position_stop_overrides:
            del _binance_position_stop_overrides[override_key]
        logger.info(f"已清除 Binance Live Position {override_key} 的停損配置覆寫")
    else:
        # 更新覆寫值
        overrides = _binance_position_stop_overrides.get(override_key, {})
        
        if update.dyn_profit_threshold_pct is not None:
            if update.dyn_profit_threshold_pct < 0:
                logger.warning(
                    f"Binance Live Position {override_key} dyn_profit_threshold_pct < 0 ({update.dyn_profit_threshold_pct})，已設為 0"
                )
                overrides["dyn_profit_threshold_pct"] = 0.0
            else:
                overrides["dyn_profit_threshold_pct"] = update.dyn_profit_threshold_pct
        
        if update.base_stop_loss_pct is not None:
            if update.base_stop_loss_pct < 0:
                logger.warning(
                    f"Binance Live Position {override_key} base_stop_loss_pct < 0 ({update.base_stop_loss_pct})，已設為 0"
                )
                overrides["base_stop_loss_pct"] = 0.0
            else:
                overrides["base_stop_loss_pct"] = update.base_stop_loss_pct
        
        if update.trail_callback is not None:
            # clamp to [0, 1] and handle 0 meaning "base stop only"
            val = update.trail_callback
            if val < 0:
                logger.warning(
                    f"Binance Live Position {override_key} trail_callback < 0 ({val})，已設為 0 (base-stop only)"
                )
                val = 0.0
            elif val > 1:
                logger.warning(
                    f"Binance Live Position {override_key} trail_callback > 1 ({val})，已調整為 1.0"
                )
                val = 1.0
            overrides["trail_callback"] = val
        
        _binance_position_stop_overrides[override_key] = overrides
        
        logger.info(
            f"Binance Live Position {override_key} 停損配置已更新："
            f"dyn_profit_threshold_pct={overrides.get('dyn_profit_threshold_pct')}, "
            f"base_stop_loss_pct={overrides.get('base_stop_loss_pct')}, "
            f"trail_callback={overrides.get('trail_callback')}"
        )
    
    # 計算實際使用的值和來源標記（與 get_binance_open_positions 中的邏輯一致）
    overrides = _binance_position_stop_overrides.get(override_key, {})
    
    # 查找對應的本地 Position（如果存在）
    from models import Position
    db = next(get_db())
    try:
        local_pos = (
            db.query(Position)
            .filter(
                Position.symbol == update.symbol.upper(),
                Position.side == update.position_side.upper(),
                Position.status == "OPEN",
            )
            .order_by(Position.id.desc())
            .first()
        )
    except Exception:
        local_pos = None
    finally:
        db.close()
    
    # Profit Threshold
    profit_threshold_value = None
    profit_threshold_source = None
    if overrides.get("dyn_profit_threshold_pct") is not None:
        profit_threshold_value = overrides["dyn_profit_threshold_pct"]
        profit_threshold_source = "override"
    elif local_pos and local_pos.dyn_profit_threshold_pct is not None:
        profit_threshold_value = local_pos.dyn_profit_threshold_pct
        profit_threshold_source = "override"
    elif TRAILING_CONFIG.profit_threshold_pct is not None:
        profit_threshold_value = TRAILING_CONFIG.profit_threshold_pct
        profit_threshold_source = "global"
    else:
        profit_threshold_value = DYN_PROFIT_THRESHOLD_PCT
        profit_threshold_source = "default"
    
    # Lock Ratio
    lock_ratio_value = None
    lock_ratio_source = None
    if overrides.get("trail_callback") is not None:
        lock_ratio_value = overrides["trail_callback"]
        lock_ratio_source = "override"
    elif local_pos and local_pos.trail_callback is not None:
        lock_ratio_value = local_pos.trail_callback
        lock_ratio_source = "override"
    elif TRAILING_CONFIG.lock_ratio is not None:
        lock_ratio_value = TRAILING_CONFIG.lock_ratio
        lock_ratio_source = "global"
    else:
        lock_ratio_value = DYN_LOCK_RATIO_DEFAULT
        lock_ratio_source = "default"
    
    # Base SL%
    base_sl_value = None
    base_sl_source = None
    if overrides.get("base_stop_loss_pct") is not None:
        base_sl_value = overrides["base_stop_loss_pct"]
        base_sl_source = "override"
    elif local_pos and local_pos.base_stop_loss_pct is not None:
        base_sl_value = local_pos.base_stop_loss_pct
        base_sl_source = "override"
    elif TRAILING_CONFIG.base_sl_pct is not None:
        base_sl_value = TRAILING_CONFIG.base_sl_pct
        base_sl_source = "global"
    else:
        base_sl_value = DYN_BASE_SL_PCT
        base_sl_source = "default"
    
    return {
        "success": True,
        "symbol": update.symbol.upper(),
        "position_side": update.position_side.upper(),
        "overrides": overrides,
        # 添加實際使用的值和來源標記（用於前端顯示和顏色標記）
        "profit_threshold_value": profit_threshold_value,
        "profit_threshold_source": profit_threshold_source,
        "lock_ratio_value": lock_ratio_value,
        "lock_ratio_source": lock_ratio_source,
        "base_sl_value": base_sl_value,
        "base_sl_source": base_sl_source,
    }


@app.get("/binance/portfolio/summary")
async def get_binance_portfolio_summary(
    user: dict = Depends(require_admin_user),
    db: Session = Depends(get_db)
):
    """
    取得 Binance Live Positions 的 Portfolio 摘要（總 PnL）。
    僅限已登入的管理員使用。
    
    Returns:
        dict: {
            "total_unrealized_pnl": float,  # 總未實現盈虧（USDT）
            "position_count": int,          # 倉位數量
            "portfolio_trailing": {         # Portfolio trailing 狀態
                "enabled": bool,
                "target_pnl": float | None,
                "lock_ratio": float | None,
                "max_pnl_reached": float | None
            }
        }
    
    Raises:
        HTTPException: 當 Binance API 呼叫失敗時
    """
    global _portfolio_trailing_runtime_state
    
    # 從資料庫載入持久化配置
    try:
        config = db.query(PortfolioTrailingConfig).filter(PortfolioTrailingConfig.id == 1).first()
        if not config:
            # 如果不存在，創建預設配置
            try:
                config = PortfolioTrailingConfig(
                    id=1,
                    enabled=False,
                    target_pnl=None,
                    lock_ratio=None
                )
                db.add(config)
                db.commit()
                db.refresh(config)
            except Exception as create_error:
                # 如果創建失敗，回滾並使用預設值
                db.rollback()
                logger.error(f"創建 Portfolio Trailing Config 失敗: {create_error}", exc_info=True)
                # 使用內存中的預設值
                config = None
    except Exception as db_error:
        logger.error(f"查詢 Portfolio Trailing Config 失敗: {db_error}", exc_info=True)
        config = None
    
    # 如果無法從資料庫載入，使用預設值
    if not config:
        enabled = False
        target_pnl = None
        lock_ratio = None
    else:
        enabled = config.enabled
        target_pnl = config.target_pnl
        lock_ratio = config.lock_ratio
    
    try:
        client = get_client()
        raw_positions = client.futures_position_information()
        
        total_pnl = 0.0
        position_count = 0
        
        for item in raw_positions:
            try:
                position_amt = float(item.get("positionAmt", "0") or 0)
            except (ValueError, TypeError):
                position_amt = 0.0
            
            if position_amt == 0:
                continue
            
            try:
                unrealized_pnl = float(item.get("unRealizedProfit", "0") or 0)
                total_pnl += unrealized_pnl
                position_count += 1
            except (ValueError, TypeError):
                continue
        
        # 使用全局 lock_ratio 如果 portfolio lock_ratio 為 None
        effective_lock_ratio = lock_ratio
        if effective_lock_ratio is None:
            effective_lock_ratio = TRAILING_CONFIG.lock_ratio if TRAILING_CONFIG.lock_ratio is not None else DYN_LOCK_RATIO_DEFAULT
        
        return {
            "total_unrealized_pnl": total_pnl,
            "position_count": position_count,
            "portfolio_trailing": {
                "enabled": enabled,
                "target_pnl": target_pnl,
                "lock_ratio": lock_ratio,
                "max_pnl_reached": _portfolio_trailing_runtime_state.get("max_pnl_reached"),
                "effective_lock_ratio": effective_lock_ratio
            }
        }
    except Exception as e:
        logger.exception("取得 Portfolio Summary 失敗")
        raise HTTPException(
            status_code=500,
            detail=f"取得 Portfolio Summary 失敗: {str(e)}"
        )


@app.post("/binance/positions/close-all")
async def close_all_binance_positions(
    user: dict = Depends(require_admin_user),
    db: Session = Depends(get_db)
):
    """
    關閉所有 Binance Live Positions。
    僅限已登入的管理員使用。
    
    Returns:
        dict: {
            "success": bool,
            "closed_count": int,
            "errors": List[str]
        }
    
    Raises:
        HTTPException: 當 Binance API 呼叫失敗時
    """
    try:
        client = get_client()
        raw_positions = client.futures_position_information()
        
        closed_count = 0
        errors = []
        db_positions_to_update = []  # 收集需要更新的 Position 記錄
        
        for item in raw_positions:
            try:
                position_amt = float(item.get("positionAmt", "0") or 0)
            except (ValueError, TypeError):
                continue
            
            if position_amt == 0:
                continue
            
            symbol = item.get("symbol", "")
            if not symbol:
                continue
            
            position_side = "LONG" if position_amt > 0 else "SHORT"
            
            try:
                # 使用現有的關倉邏輯
                side = "SELL" if position_side == "LONG" else "BUY"
                qty = abs(position_amt)
                
                timestamp = int(time.time() * 1000)
                client_order_id = f"TVBOT_CLOSE_ALL_{timestamp}_{closed_count}"
                
                logger.info(f"關閉所有倉位: {symbol} {position_side}，數量: {qty}")
                
                order = client.futures_create_order(
                    symbol=symbol,
                    side=side,
                    type="MARKET",
                    quantity=qty,
                    reduceOnly=True,
                    newClientOrderId=client_order_id
                )
                
                # 取得平倉價格
                exit_price = get_exit_price_from_order(order, symbol)
                
                # 更新資料庫中的 Position 記錄（如果存在）
                # 查找所有匹配的 OPEN 狀態 Position 記錄
                matching_positions = (
                    db.query(Position)
                    .filter(
                        Position.symbol == symbol.upper(),
                        Position.side == position_side,
                        Position.status == "OPEN"
                    )
                    .all()
                )
                
                # 記錄需要更新的 Position 記錄和相關資訊
                for db_position in matching_positions:
                    db_positions_to_update.append({
                        "position": db_position,
                        "exit_price": exit_price,
                        "order": order
                    })
                
                closed_count += 1
                logger.info(
                    f"成功關閉 {symbol} {position_side}，訂單ID: {order.get('orderId')}，"
                    f"找到 {len(matching_positions)} 筆需要更新的資料庫記錄"
                )
            except Exception as e:
                error_msg = f"{symbol} {position_side}: {str(e)}"
                errors.append(error_msg)
                logger.error(f"關閉 {symbol} {position_side} 失敗: {e}")
        
        # 批量更新所有 Position 記錄
        updated_count = 0
        for update_info in db_positions_to_update:
            try:
                db_position = update_info["position"]
                exit_price = update_info["exit_price"]
                order = update_info["order"]
                
                db_position.status = "CLOSED"
                db_position.closed_at = datetime.now(timezone.utc)
                db_position.exit_price = exit_price
                db_position.exit_reason = "manual_close_all"
                # 更新訂單 ID（如果可用）
                if order.get("orderId"):
                    try:
                        db_position.binance_order_id = int(order["orderId"])
                    except (ValueError, TypeError):
                        pass
                if order.get("clientOrderId"):
                    db_position.client_order_id = order["clientOrderId"]
                
                updated_count += 1
                logger.info(
                    f"已更新資料庫 Position 記錄 {db_position.id} ({db_position.symbol} {db_position.side}) 狀態為 CLOSED"
                )
            except Exception as db_update_error:
                logger.error(
                    f"更新資料庫 Position 記錄 {update_info['position'].id} 失敗: {db_update_error}"
                )
                # 繼續處理其他記錄，不影響整體流程
        
        # 提交所有資料庫變更
        try:
            if db_positions_to_update:
                db.commit()
                logger.info(f"成功更新 {updated_count} 筆 Position 記錄狀態為 CLOSED")
        except Exception as commit_error:
            logger.error(f"提交資料庫變更失敗: {commit_error}")
            db.rollback()
            errors.append(f"資料庫更新失敗: {str(commit_error)}")
        
        return {
            "success": len(errors) == 0,
            "closed_count": closed_count,
            "updated_db_records": updated_count,
            "errors": errors
        }
    except Exception as e:
        logger.exception("關閉所有倉位失敗")
        raise HTTPException(
            status_code=500,
            detail=f"關閉所有倉位失敗: {str(e)}"
        )


@app.get("/binance/portfolio/trailing", response_model=PortfolioTrailingConfigOut)
async def get_portfolio_trailing_config(
    user: dict = Depends(require_admin_user),
    db: Session = Depends(get_db)
):
    """
    取得 Portfolio Trailing Stop 設定。
    僅限已登入的管理員使用。
    
    Returns:
        PortfolioTrailingConfigOut: 目前的 Portfolio Trailing 設定
    """
    global _portfolio_trailing_runtime_state
    
    # 從資料庫載入配置
    try:
        config = db.query(PortfolioTrailingConfig).filter(PortfolioTrailingConfig.id == 1).first()
        if not config:
            # 如果不存在，創建預設配置
            try:
                config = PortfolioTrailingConfig(
                    id=1,
                    enabled=False,
                    target_pnl=None,
                    lock_ratio=None
                )
                db.add(config)
                db.commit()
                db.refresh(config)
            except Exception as create_error:
                db.rollback()
                logger.error(f"創建 Portfolio Trailing Config 失敗: {create_error}", exc_info=True)
                # 返回預設值
                return PortfolioTrailingConfigOut(
                    enabled=False,
                    target_pnl=None,
                    lock_ratio=None,
                    max_pnl_reached=_portfolio_trailing_runtime_state.get("max_pnl_reached")
                )
        
        return PortfolioTrailingConfigOut(
            enabled=config.enabled,
            target_pnl=config.target_pnl,
            lock_ratio=config.lock_ratio,
            max_pnl_reached=_portfolio_trailing_runtime_state.get("max_pnl_reached")
        )
    except Exception as db_error:
        logger.error(f"查詢 Portfolio Trailing Config 失敗: {db_error}", exc_info=True)
        # 返回預設值而不是拋出異常
        return PortfolioTrailingConfigOut(
            enabled=False,
            target_pnl=None,
            lock_ratio=None,
            max_pnl_reached=_portfolio_trailing_runtime_state.get("max_pnl_reached")
        )


@app.post("/binance/portfolio/trailing", response_model=PortfolioTrailingConfigOut)
async def update_portfolio_trailing_config(
    payload: PortfolioTrailingConfigUpdate,
    user: dict = Depends(require_admin_user),
    db: Session = Depends(get_db)
):
    """
    更新 Portfolio Trailing Stop 設定。
    僅限已登入的管理員使用。
    設定會持久化到資料庫，即使系統重啟也會保留。
    
    Args:
        payload: 要更新的設定（只更新提供的欄位）
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
        db: 資料庫 Session
    
    Returns:
        PortfolioTrailingConfigOut: 更新後的 Portfolio Trailing 設定
    
    Raises:
        HTTPException: 當設定值無效時
    """
    global _portfolio_trailing_runtime_state
    
    # 從資料庫載入或創建配置
    try:
        config = db.query(PortfolioTrailingConfig).filter(PortfolioTrailingConfig.id == 1).first()
        if not config:
            try:
                config = PortfolioTrailingConfig(id=1, enabled=False, target_pnl=None, lock_ratio=None)
                db.add(config)
                db.flush()
            except Exception as create_error:
                db.rollback()
                logger.error(f"創建 Portfolio Trailing Config 失敗: {create_error}", exc_info=True)
                raise HTTPException(
                    status_code=500,
                    detail=f"無法創建配置記錄: {str(create_error)}"
                )
    except HTTPException:
        raise
    except Exception as db_error:
        logger.error(f"查詢 Portfolio Trailing Config 失敗: {db_error}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"資料庫錯誤: {str(db_error)}"
        )
    
    if hasattr(payload, 'model_dump'):
        data = payload.model_dump(exclude_unset=True)
    else:
        data = payload.dict(exclude_unset=True)
    
    # 範圍防呆
    if "lock_ratio" in data and data["lock_ratio"] is not None:
        if data["lock_ratio"] < 0:
            raise HTTPException(
                status_code=400,
                detail="lock_ratio 不能小於 0"
            )
        if data["lock_ratio"] > 1:
            logger.warning(f"lock_ratio > 1（值={data['lock_ratio']}），已強制調整為 1.0")
            data["lock_ratio"] = 1.0
    
    # 更新設定並保存到資料庫
    if "enabled" in data:
        config.enabled = data["enabled"]
        # 如果停用，重置 max_pnl_reached
        if not data["enabled"]:
            _portfolio_trailing_runtime_state["max_pnl_reached"] = None
    
    if "target_pnl" in data:
        old_target_pnl = config.target_pnl
        config.target_pnl = data["target_pnl"]
        # 如果更新 target_pnl，重置 max_pnl_reached（需要重新達到目標）
        if data["target_pnl"] is not None and data["target_pnl"] != old_target_pnl:
            _portfolio_trailing_runtime_state["max_pnl_reached"] = None
    
    if "lock_ratio" in data:
        config.lock_ratio = data["lock_ratio"]
    
    try:
        db.commit()
        db.refresh(config)
        logger.info(f"更新 Portfolio Trailing 設定（已持久化）: enabled={config.enabled}, "
                    f"target_pnl={config.target_pnl}, lock_ratio={config.lock_ratio}")
    except Exception as commit_error:
        db.rollback()
        logger.error(f"提交 Portfolio Trailing 設定失敗: {commit_error}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"無法保存設定: {str(commit_error)}"
        )
    
    return PortfolioTrailingConfigOut(
        enabled=config.enabled,
        target_pnl=config.target_pnl,
        lock_ratio=config.lock_ratio,
        max_pnl_reached=_portfolio_trailing_runtime_state.get("max_pnl_reached")
    )


@app.delete("/positions/{pos_id}", response_model=dict)
async def delete_position_record(
    pos_id: int,
    user: dict = Depends(require_admin_user),
    db: Session = Depends(get_db)
):
    """
    刪除指定的倉位記錄。
    
    注意：此操作僅會刪除本地資料庫中的倉位記錄，不會對 Binance 上的實際倉位進行任何操作。
    如果倉位仍為 OPEN 狀態，請務必確認實際倉位已關閉，否則刪除紀錄後將無法自動追蹤該倉位。
    """
    position = db.query(Position).filter(Position.id == pos_id).first()
    if not position:
        raise HTTPException(status_code=404, detail="Position not found")
    
    symbol = position.symbol
    status = position.status
    db.delete(position)
    db.commit()
    
    logger.info(f"刪除倉位記錄: id={pos_id}, symbol={symbol}, status={status}")
    
    return {
        "success": True,
        "message": f"Position {pos_id} ({symbol}) 已刪除",
        "position_id": pos_id,
        "status": status,
    }


@app.post("/positions/{pos_id}/close", response_model=dict)
async def close_position(
    pos_id: int,
    user: dict = Depends(require_admin_user),
    db: Session = Depends(get_db)
):
    """
    關閉倉位
    
    從資料庫取出 Position，如果是 OPEN 狀態，呼叫 close_futures_position 關倉，
    並更新 status 為 CLOSED，設定 closed_at 為現在時間。
    
    此端點僅限已登入且通過管理員驗證的使用者（Google OAuth + ADMIN_GOOGLE_EMAIL）使用。
    
    Args:
        pos_id: 倉位 ID
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
        db: 資料庫 Session
    
    Returns:
        dict: 關倉結果
    """
    # 從資料庫取出 Position
    position = db.query(Position).filter(Position.id == pos_id).first()
    
    if not position:
        raise HTTPException(status_code=404, detail="找不到指定的倉位記錄")
    
    if position.status != "OPEN":
        raise HTTPException(
            status_code=400,
            detail=f"倉位狀態為 {position.status}，無法關閉。只有 OPEN 狀態的倉位可以關閉。"
        )
    
    try:
        # 呼叫幣安 API 關倉
        close_order = close_futures_position(
            symbol=position.symbol,
            position_side=position.side,  # LONG 或 SHORT
            qty=position.qty,
            position_id=position.id
        )
        
        # 取得平倉價格
        exit_price = get_exit_price_from_order(close_order, position.symbol)
        
        # 更新 Position 記錄與平倉資訊
        position.status = "CLOSED"
        position.closed_at = datetime.now(timezone.utc)
        position.exit_price = exit_price
        position.exit_reason = "manual_close"
        db.commit()
        
        return {
            "success": True,
            "message": "倉位已成功關閉",
            "position_id": position.id,
            "closed_at": position.closed_at.isoformat(),
            "exit_price": exit_price,
            "exit_reason": "manual_close",
            "binance_order": close_order
        }
    
    except Exception as e:
        # 如果關倉失敗，更新狀態為 ERROR
        position.status = "ERROR"
        db.commit()
        raise HTTPException(status_code=500, detail=f"關倉失敗: {str(e)}")


@app.post("/positions/{pos_id}/trailing", response_model=PositionOut)
async def update_trailing(
    pos_id: int,
    trailing_update: TrailingUpdate,
    user: dict = Depends(require_admin_user),
    db: Session = Depends(get_db)
):
    """
    更新追蹤停損設定
    
    將 trailing_callback_percent 轉換為 ratio（例如 2.0 -> 0.02），
    如果 highest_price 為空，初始化為目前的標記價格。
    
    此端點僅限已登入且通過管理員驗證的使用者（Google OAuth + ADMIN_GOOGLE_EMAIL）使用。
    
    Args:
        pos_id: 倉位 ID
        trailing_update: 追蹤停損更新資料
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
        db: 資料庫 Session
    
    Returns:
        PositionOut: 更新後的倉位資訊
    """
    # 從資料庫取出 Position
    position = db.query(Position).filter(Position.id == pos_id).first()
    
    if not position:
        raise HTTPException(status_code=404, detail="找不到指定的倉位記錄")
    
    if position.status != "OPEN":
        raise HTTPException(
            status_code=400,
            detail=f"只能更新 OPEN 狀態倉位的追蹤停損設定，目前狀態為 {position.status}"
        )
    
    try:
        pct = trailing_update.trailing_callback_percent
        
        if pct < 0 or pct > 100:
            logger.warning(
                f"更新倉位 {position.id} ({position.symbol}) 的 trailing_callback_percent 非法: {pct}，必須在 0~100 之間"
            )
            raise HTTPException(
                status_code=400,
                detail="trailing_callback_percent 必須在 0~100 之間"
            )
        
        if pct == 0:
            logger.info(
                f"倉位 {position.id} ({position.symbol}) 更新 trailing_callback_percent=0，僅使用 base stop-loss"
            )
            position.trail_callback = 0.0
        else:
            trail_callback_ratio = pct / 100.0
            position.trail_callback = trail_callback_ratio
            logger.info(
                f"倉位 {position.id} ({position.symbol}) 更新 trailing_callback_percent={pct}%，lock_ratio={trail_callback_ratio}"
            )
        
        # 如果 highest_price 為空，初始化為目前的標記價格
        if position.highest_price is None:
            mark_price = get_mark_price(position.symbol)
            position.highest_price = mark_price
        
        db.commit()
        db.refresh(position)
        
        return PositionOut(**position.to_dict())
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新追蹤停損設定失敗: {str(e)}")


@app.get("/health")
async def health_check():
    """健康檢查端點"""
    try:
        # 檢查幣安連線
        client = get_client()
        # 簡單測試：取得 BTCUSDT 標記價格
        mark_price = get_mark_price("BTCUSDT")
        
        return {
            "status": "healthy",
            "binance_connected": True,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "binance_connected": False,
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        )


# ==================== Admin Prune Endpoints ====================

@app.delete("/admin/signal-logs/prune")
def prune_signal_logs(
    days: int = Query(30, ge=1, le=365, description="保留最近幾天的 signal logs"),
    db: Session = Depends(get_db),
    user: dict = Depends(require_admin_user),
):
    """
    清理舊的 TradingView Signal Logs
    
    僅限已登入的管理員使用。
    
    Args:
        days: 保留最近幾天的 logs（預設 30 天）
        db: 資料庫 Session
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
    
    Returns:
        dict: 清理結果，包含刪除的筆數和截止時間
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    stmt = delete(TradingViewSignalLog).where(TradingViewSignalLog.received_at < cutoff)
    result = db.execute(stmt)
    db.commit()
    
    deleted_count = getattr(result, "rowcount", None)
    logger.info(f"Prune signal logs: 刪除 {deleted_count} 筆（保留最近 {days} 天，截止時間: {cutoff.isoformat()}）")
    
    return {
        "success": True,
        "deleted": deleted_count,
        "cutoff": cutoff.isoformat(),
        "days": days,
    }


@app.delete("/admin/signal-logs/clear")
def clear_all_signal_logs(
    db: Session = Depends(get_db),
    user: dict = Depends(require_admin_user),
):
    """
    清除所有 TradingView Signal Logs
    
    僅限已登入的管理員使用。
    此操作會刪除所有 signal logs，無法復原，請謹慎使用。
    
    Args:
        db: 資料庫 Session
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
    
    Returns:
        dict: 清理結果，包含刪除的筆數
    """
    # 先計算總數
    total_count = db.query(TradingViewSignalLog).count()
    
    # 刪除所有記錄
    stmt = delete(TradingViewSignalLog)
    result = db.execute(stmt)
    db.commit()
    
    deleted_count = getattr(result, "rowcount", None) or total_count
    logger.info(f"Clear all signal logs: 刪除 {deleted_count} 筆")
    
    return {
        "success": True,
        "deleted": deleted_count,
        "message": f"成功清除 {deleted_count} 筆 signal logs"
    }


@app.delete("/admin/positions/prune-closed")
def prune_closed_positions(
    days: int = Query(30, ge=1, le=365, description="保留最近幾天內關閉的倉位"),
    include_error: bool = Query(False, description="是否同時刪除 ERROR 狀態的倉位"),
    db: Session = Depends(get_db),
    user: dict = Depends(require_admin_user),
):
    """
    清理舊的已關閉倉位記錄
    
    僅限已登入的管理員使用。
    
    Args:
        days: 保留最近幾天內關閉的倉位（預設 30 天）
        include_error: 是否同時刪除 ERROR 狀態的倉位（預設 False）
        db: 資料庫 Session
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
    
    Returns:
        dict: 清理結果，包含刪除的筆數和截止時間
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    # 建立刪除條件
    if include_error:
        # 如果 include_error=True，刪除 CLOSED 且超過指定天數的，以及所有 ERROR 狀態的
        stmt = delete(Position).where(
            or_(
                # CLOSED 且超過指定天數
                and_(
                    Position.status == "CLOSED",
                    Position.closed_at.isnot(None),
                    Position.closed_at < cutoff
                ),
                # 或 ERROR 狀態（不限日期）
                Position.status == "ERROR"
            )
        )
    else:
        # 只刪除 CLOSED 且超過指定天數的
        stmt = delete(Position).where(
            and_(
                Position.status == "CLOSED",
                Position.closed_at.isnot(None),
                Position.closed_at < cutoff
            )
        )
    result = db.execute(stmt)
    db.commit()
    
    deleted_count = getattr(result, "rowcount", None)
    error_info = "（包含 ERROR 狀態）" if include_error else ""
    logger.info(f"Prune closed positions: 刪除 {deleted_count} 筆{error_info}（保留最近 {days} 天內關閉的倉位，截止時間: {cutoff.isoformat()}）")
    
    return {
        "success": True,
        "deleted": deleted_count,
        "cutoff": cutoff.isoformat(),
        "days": days,
        "include_error": include_error,
    }


@app.delete("/admin/positions/clear-error")
def clear_error_positions(
    db: Session = Depends(get_db),
    user: dict = Depends(require_admin_user),
):
    """
    清除所有 ERROR 狀態的倉位記錄
    
    僅限已登入的管理員使用。
    此操作會刪除所有 ERROR 狀態的倉位，無法復原，請謹慎使用。
    
    Args:
        db: 資料庫 Session
        user: 管理員使用者資訊（由 Depends(require_admin_user) 自動驗證）
    
    Returns:
        dict: 清理結果，包含刪除的筆數
    """
    # 先計算 ERROR 狀態的總數
    error_count = db.query(Position).filter(Position.status == "ERROR").count()
    
    # 刪除所有 ERROR 狀態的記錄
    stmt = delete(Position).where(Position.status == "ERROR")
    result = db.execute(stmt)
    db.commit()
    
    deleted_count = getattr(result, "rowcount", None) or error_count
    logger.info(f"Clear error positions: 刪除 {deleted_count} 筆 ERROR 狀態的倉位")
    
    return {
        "success": True,
        "deleted": deleted_count,
        "message": f"成功清除 {deleted_count} 筆 ERROR 狀態的倉位"
    }


# ==================== 主程式入口 ====================

if __name__ == "__main__":
    import uvicorn
    
    # 開發模式下啟動伺服器
    # 預設監聽 0.0.0.0:8000
    # 正式環境建議使用生產級 ASGI 伺服器（如 gunicorn + uvicorn workers）
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True  # 開發模式：程式碼變更時自動重載
    )
