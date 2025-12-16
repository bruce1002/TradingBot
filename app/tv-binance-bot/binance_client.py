"""
幣安 API 客戶端模組

負責建立和管理幣安期貨測試網的 API 連線。
提供統一的介面來下單、查詢訂單狀態、取得標記價格等操作。
"""

from binance.client import Client
from binance.exceptions import BinanceAPIException
import os
import logging
import time
import math
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

# 載入 .env 檔案中的環境變數（如果尚未載入）
load_dotenv()

# 設定日誌
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 全域 Client 實例
_client: Optional[Client] = None

# 交易對資訊快取（symbol -> symbol_info）
_symbol_info_cache: Dict[str, Dict[str, Any]] = {}


def get_client() -> Client:
    """
    取得已初始化好的幣安 Client 實例（單例模式）
    
    從環境變數讀取配置：
    - BINANCE_API_KEY: 幣安 API Key（必填）
    - BINANCE_API_SECRET: 幣安 API Secret（必填）
    - USE_TESTNET: 是否使用測試網，預設為 "1"（1=測試網, 0=正式網）
    
    Returns:
        Client: 幣安 API Client 實例
    
    Raises:
        ValueError: 當 API 金鑰未設定時
    """
    global _client
    
    if _client is not None:
        return _client


    use_testnet = os.getenv("USE_TESTNET", "1") == "1"

    if use_testnet:
        api_key = os.getenv("BINANCE_TESTNET_API_KEY")
        api_secret = os.getenv("BINANCE_TESTNET_API_SECRET")
    else:
        api_key = os.getenv("BINANCE_MAINNET_API_KEY")
        api_secret = os.getenv("BINANCE_MAINNET_API_SECRET")

    
    if not api_key or not api_secret:
        error_msg = "請設定 BINANCE_API_KEY 和 BINANCE_API_SECRET 環境變數"
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    # 從環境變數決定是否使用測試網（預設為 1，即使用測試網）
    use_testnet = os.getenv("USE_TESTNET", "1").strip()
    is_testnet = use_testnet == "1"
    
    try:
        # 建立 Client 實例
        _client = Client(
            api_key=api_key,
            api_secret=api_secret,
            testnet=is_testnet
        )
        
        network = "測試網" if is_testnet else "正式網"
        logger.info(f"已連線至幣安期貨{network}")
        
        return _client
    
    except Exception as e:
        logger.error(f"初始化幣安 Client 失敗: {e}")
        raise


def get_symbol_info(symbol: str) -> Dict[str, Any]:
    """
    取得交易對的精度資訊（數量精度、價格精度、步長等）
    
    使用快取機制，避免重複呼叫 API。
    
    Args:
        symbol: 交易對，例如 "BTCUSDT"
    
    Returns:
        dict: 包含以下欄位的字典：
            - quantityPrecision: 數量精度（小數位數）
            - stepSize: 數量步長（例如 "0.001"）
            - pricePrecision: 價格精度（小數位數）
            - tickSize: 價格步長（例如 "0.01"）
            - minQty: 最小數量
            - maxQty: 最大數量
    
    Raises:
        Exception: 當 API 呼叫失敗或找不到交易對時
    """
    global _symbol_info_cache
    
    symbol_upper = symbol.upper()
    
    # 檢查快取
    if symbol_upper in _symbol_info_cache:
        return _symbol_info_cache[symbol_upper]
    
    try:
        client = get_client()
        
        # 取得所有交易對資訊
        exchange_info = client.futures_exchange_info()
        
        # 查找目標交易對
        symbol_info = None
        for s in exchange_info.get("symbols", []):
            if s.get("symbol") == symbol_upper:
                symbol_info = s
                break
        
        if not symbol_info:
            error_msg = f"找不到交易對 {symbol_upper} 的資訊"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        # 從 filters 中提取精度資訊
        filters = symbol_info.get("filters", [])
        quantity_precision = symbol_info.get("quantityPrecision", 8)  # 預設 8 位小數
        price_precision = symbol_info.get("pricePrecision", 8)  # 預設 8 位小數
        step_size = "1"  # 預設步長
        tick_size = "0.01"  # 預設價格步長
        min_qty = 0.0
        max_qty = float("inf")
        
        for f in filters:
            if f.get("filterType") == "LOT_SIZE":
                step_size = f.get("stepSize", "1")
                min_qty = float(f.get("minQty", "0"))
                max_qty = float(f.get("maxQty", "0") or "inf")
            elif f.get("filterType") == "PRICE_FILTER":
                tick_size = f.get("tickSize", "0.01")
        
        # 計算實際的精度（從 stepSize 計算）
        # 例如 stepSize="0.001" -> precision=3
        step_size_float = float(step_size)
        if step_size_float < 1:
            # 計算小數位數
            precision = len(step_size.rstrip("0").split(".")[-1]) if "." in step_size else 0
        else:
            precision = 0
        
        result = {
            "quantityPrecision": precision,
            "stepSize": step_size,
            "stepSizeFloat": step_size_float,
            "pricePrecision": price_precision,
            "tickSize": tick_size,
            "minQty": min_qty,
            "maxQty": max_qty,
            "raw": symbol_info  # 保留原始資訊供除錯用
        }
        
        # 存入快取
        _symbol_info_cache[symbol_upper] = result
        
        logger.info(
            f"取得 {symbol_upper} 精度資訊: "
            f"quantityPrecision={precision}, stepSize={step_size}, "
            f"pricePrecision={price_precision}, tickSize={tick_size}"
        )
        
        return result
    
    except BinanceAPIException as e:
        error_msg = f"取得 {symbol_upper} 精度資訊失敗: {e.message} (錯誤碼: {e.code})"
        logger.error(error_msg)
        raise Exception(error_msg)
    except Exception as e:
        error_msg = f"取得 {symbol_upper} 精度資訊時發生錯誤: {e}"
        logger.error(error_msg)
        raise Exception(error_msg)


def format_quantity(symbol: str, qty: float) -> str:
    """
    根據交易對的精度格式化數量
    
    會根據 stepSize 來調整數量，確保符合 Binance 的要求。
    
    Args:
        symbol: 交易對，例如 "BTCUSDT"
        qty: 原始數量（float）
    
    Returns:
        str: 格式化後的數量字串
    """
    try:
        symbol_info = get_symbol_info(symbol)
        step_size = symbol_info["stepSizeFloat"]
        min_qty = symbol_info["minQty"]
        
        # 根據 stepSize 調整數量
        # 例如 stepSize=0.001，則數量必須是 0.001 的倍數
        if step_size > 0:
            # 向下取整到最近的 stepSize 倍數
            adjusted_qty = math.floor(qty / step_size) * step_size
        else:
            adjusted_qty = qty
        
        # 檢查是否小於最小數量
        if adjusted_qty < min_qty:
            logger.warning(
                f"格式化後的數量 {adjusted_qty} 小於最小數量 {min_qty} "
                f"（原始數量: {qty}），將使用最小數量"
            )
            adjusted_qty = min_qty
            # 確保最小數量也是 stepSize 的倍數
            if step_size > 0:
                adjusted_qty = math.ceil(adjusted_qty / step_size) * step_size
        
        # 根據精度格式化
        precision = symbol_info["quantityPrecision"]
        formatted = f"{adjusted_qty:.{precision}f}".rstrip("0").rstrip(".")
        
        # 如果格式化後為空或為 "0"，至少保留一位小數
        if not formatted or formatted == "0":
            formatted = f"{adjusted_qty:.{max(1, precision)}f}".rstrip("0").rstrip(".")
        
        logger.debug(f"格式化 {symbol} 數量: {qty} -> {formatted} (stepSize={step_size}, precision={precision}, minQty={min_qty})")
        
        return formatted
    
    except Exception as e:
        logger.warning(f"格式化 {symbol} 數量時發生錯誤: {e}，使用原始值")
        # 如果取得精度失敗，使用預設精度（8 位小數）
        return f"{qty:.8f}".rstrip("0").rstrip(".")


def get_mark_price(symbol: str) -> float:
    """
    取得期貨標記價格（Mark Price）
    
    Args:
        symbol: 交易對，例如 "BTCUSDT"
    
    Returns:
        float: 標記價格
    
    Raises:
        Exception: 當 API 呼叫失敗時
    """
    try:
        client = get_client()
        
        # 取得標記價格（會回傳列表）
        ticker = client.futures_mark_price(symbol=symbol)
        
        # 如果回傳是列表，取第一個元素
        if isinstance(ticker, list) and len(ticker) > 0:
            mark_price = float(ticker[0]["markPrice"])
        else:
            mark_price = float(ticker["markPrice"])
        
        logger.info(f"取得 {symbol} 標記價格: {mark_price}")
        return mark_price
    
    except BinanceAPIException as e:
        error_msg = f"取得 {symbol} 標記價格失敗: {e.message} (錯誤碼: {e.code})"
        logger.error(error_msg)
        raise Exception(error_msg)
    except Exception as e:
        error_msg = f"取得 {symbol} 標記價格時發生錯誤: {e}"
        logger.error(error_msg)
        raise Exception(error_msg)


def open_futures_market_order(
    symbol: str,
    side: str,
    qty: float,
    leverage: int,
    tag: Optional[str] = None
) -> Dict[str, Any]:
    """
    開啟期貨市價單（開倉）
    
    流程：
    1. 設定交易對的杠桿倍數
    2. 使用 MARKET order 下單
    3. 使用自訂的 newClientOrderId（格式：TVBOT_<timestamp>_<tag>）
    
    Args:
        symbol: 交易對，例如 "BTCUSDT"
        side: 交易方向，"BUY" 或 "SELL"
        qty: 交易數量
        leverage: 杠桿倍數（例如 10 代表 10 倍杠桿）
        tag: 可選的標籤，會加在 newClientOrderId 後面
    
    Returns:
        dict: 幣安 API 回傳的訂單資訊
    
    Raises:
        Exception: 當 API 呼叫失敗時
    """
    try:
        client = get_client()
        
        # 1. 設定杠桿倍數
        logger.info(f"設定 {symbol} 杠桿為 {leverage}x")
        try:
            client.futures_change_leverage(symbol=symbol, leverage=leverage)
            logger.info(f"成功設定 {symbol} 杠桿為 {leverage}x")
        except BinanceAPIException as e:
            # 如果杠桿已經設定過，可能會有錯誤，但可以繼續
            logger.warning(f"設定杠桿時發生警告: {e.message} (錯誤碼: {e.code})")
        
        # 2. 產生自訂的 client order ID
        timestamp = int(time.time() * 1000)  # 毫秒時間戳
        if tag:
            client_order_id = f"TVBOT_{timestamp}_{tag}"
        else:
            client_order_id = f"TVBOT_{timestamp}"
        
        # 3. 根據交易對精度格式化數量
        formatted_qty = format_quantity(symbol, qty)
        qty_float = float(formatted_qty)
        
        if qty_float <= 0:
            error_msg = f"格式化後的數量 {formatted_qty} 無效（原始數量: {qty}）"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        logger.info(f"建立 {symbol} {side} 市價單，數量: {qty} -> {formatted_qty}, 杠桿: {leverage}x, 訂單ID: {client_order_id}")
        
        # 4. 建立市價單
        order = client.futures_create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=formatted_qty,
            newClientOrderId=client_order_id
        )
        
        logger.info(f"成功建立訂單: {order.get('orderId')}, 狀態: {order.get('status')}")
        return order
    
    except BinanceAPIException as e:
        error_msg = f"建立 {symbol} 市價單失敗: {e.message} (錯誤碼: {e.code})"
        logger.error(error_msg)
        raise Exception(error_msg)
    except Exception as e:
        error_msg = f"建立 {symbol} 市價單時發生錯誤: {e}"
        logger.error(error_msg)
        raise Exception(error_msg)


def close_futures_position(
    symbol: str,
    position_side: str,
    qty: float,
    position_id: int
) -> Dict[str, Any]:
    """
    關閉期貨倉位
    
    根據 position_side 決定下單方向：
    - position_side = "LONG" -> 下 SELL 單（賣出平多）
    - position_side = "SHORT" -> 下 BUY 單（買入平空）
    
    使用 MARKET order 關倉。
    
    Args:
        symbol: 交易對，例如 "BTCUSDT"
        position_side: 倉位方向，"LONG" 或 "SHORT"
        qty: 平倉數量
        position_id: 倉位 ID（用於追蹤和標記）
    
    Returns:
        dict: 幣安 API 回傳的訂單資訊
    
    Raises:
        Exception: 當 API 呼叫失敗時
    """
    try:
        client = get_client()
        
        # 根據 position_side 決定下單方向
        if position_side.upper() == "LONG":
            side = "SELL"  # 平多：賣出
        elif position_side.upper() == "SHORT":
            side = "BUY"   # 平空：買入
        else:
            error_msg = f"不支援的 position_side: {position_side}，必須是 LONG 或 SHORT"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        # 產生自訂的 client order ID（用於關倉）
        timestamp = int(time.time() * 1000)
        client_order_id = f"TVBOT_CLOSE_{timestamp}_POS{position_id}"
        
        # 根據交易對精度格式化數量
        formatted_qty = format_quantity(symbol, qty)
        qty_float = float(formatted_qty)
        
        if qty_float <= 0:
            error_msg = f"格式化後的數量 {formatted_qty} 無效（原始數量: {qty}）"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        logger.info(f"關閉 {symbol} {position_side} 倉位，數量: {qty} -> {formatted_qty}, 下單方向: {side}, 訂單ID: {client_order_id}")
        
        # 建立市價單關倉
        order = client.futures_create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=formatted_qty,
            reduceOnly=True,  # 設定為只減倉，確保是平倉單
            newClientOrderId=client_order_id
        )
        
        logger.info(f"成功建立平倉訂單: {order.get('orderId')}, 狀態: {order.get('status')}")
        
        # 市價單建立後，可能需要查詢訂單詳情來取得 avgPrice
        # 如果訂單回傳中沒有 avgPrice 或 avgPrice 為 0/空，嘗試查詢訂單詳情
        order_id = order.get("orderId")
        avg_price = order.get("avgPrice")
        # 檢查 avgPrice 是否存在且有效（不是空字符串、不是 "0"、不是 0）
        has_valid_avg_price = (
            avg_price is not None and 
            avg_price != "" and 
            str(avg_price).strip() != "0"
        )
        if has_valid_avg_price:
            try:
                avg_price_float = float(avg_price)
                has_valid_avg_price = avg_price_float > 0
            except (ValueError, TypeError):
                has_valid_avg_price = False
        
        if order_id and not has_valid_avg_price:
            try:
                # 等待一小段時間讓訂單成交
                time.sleep(0.5)
                # 查詢訂單詳情
                order_detail = client.futures_get_order(symbol=symbol, orderId=order_id)
                avg_price_detail = order_detail.get("avgPrice")
                if avg_price_detail:
                    try:
                        avg_price_float = float(avg_price_detail)
                        if avg_price_float > 0:
                            order["avgPrice"] = avg_price_detail
                            logger.info(f"從訂單詳情取得 avgPrice: {avg_price_detail}")
                    except (ValueError, TypeError):
                        pass
            except Exception as e:
                logger.warning(f"查詢訂單 {order_id} 詳情失敗: {e}，將使用訂單回傳的資訊")
        
        return order
    
    except BinanceAPIException as e:
        error_msg = f"關閉 {symbol} {position_side} 倉位失敗: {e.message} (錯誤碼: {e.code})"
        logger.error(error_msg)
        raise Exception(error_msg)
    except Exception as e:
        error_msg = f"關閉 {symbol} {position_side} 倉位時發生錯誤: {e}"
        logger.error(error_msg)
        raise Exception(error_msg)


def update_all_bots_invest_amount(max_invest_usdt: float, bot_ids: Optional[List[int]] = None) -> Dict[str, Any]:
    """
    調整所有 Bot 的投資金額（max_invest_usdt）
    
    可以一次更新所有 Bot，或僅更新指定的 Bot IDs。
    
    Args:
        max_invest_usdt: 新的投資金額（USDT），必須大於 0
        bot_ids: 可選的 Bot ID 列表，如果提供則僅更新這些 Bot，否則更新所有 Bot
    
    Returns:
        dict: 包含以下欄位的字典：
            - success: 是否成功
            - updated_count: 更新的 Bot 數量
            - bot_ids: 已更新的 Bot ID 列表
            - message: 操作結果訊息
    
    Raises:
        ValueError: 當 max_invest_usdt 無效時
        Exception: 當資料庫操作失敗時
    
    範例:
        # 更新所有 Bot 的投資金額為 200 USDT
        result = update_all_bots_invest_amount(200.0)
        
        # 僅更新特定 Bot
        result = update_all_bots_invest_amount(150.0, bot_ids=[1, 2, 3])
    """
    try:
        # 驗證輸入
        if max_invest_usdt <= 0:
            error_msg = "max_invest_usdt 必須大於 0"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        # 動態導入資料庫相關模組（避免循環導入）
        try:
            from db import SessionLocal
            from models import BotConfig
            from datetime import datetime, timezone
        except ImportError as e:
            error_msg = f"無法導入資料庫模組: {e}，請確保 db.py 和 models.py 存在"
            logger.error(error_msg)
            raise Exception(error_msg)
        
        # 建立資料庫會話
        db = SessionLocal()
        try:
            # 查詢要更新的 Bot
            if bot_ids is not None and len(bot_ids) > 0:
                # 僅更新指定的 Bot
                bots = db.query(BotConfig).filter(BotConfig.id.in_(bot_ids)).all()
                if not bots:
                    error_msg = f"找不到指定的 Bot IDs: {bot_ids}"
                    logger.warning(error_msg)
                    return {
                        "success": False,
                        "updated_count": 0,
                        "bot_ids": [],
                        "message": error_msg
                    }
            else:
                # 更新所有 Bot
                bots = db.query(BotConfig).all()
            
            # 更新每個 Bot 的 max_invest_usdt
            updated_ids = []
            for bot in bots:
                bot.max_invest_usdt = max_invest_usdt
                bot.updated_at = datetime.now(timezone.utc)
                updated_ids.append(bot.id)
                logger.info(f"更新 Bot {bot.id} ({bot.name}) 的 max_invest_usdt 為 {max_invest_usdt} USDT")
            
            # 提交變更
            db.commit()
            
            success_msg = f"成功更新 {len(updated_ids)} 個 Bot 的投資金額為 {max_invest_usdt} USDT"
            logger.info(success_msg)
            
            return {
                "success": True,
                "updated_count": len(updated_ids),
                "bot_ids": updated_ids,
                "message": success_msg
            }
        
        except Exception as e:
            # 發生錯誤時回滾
            db.rollback()
            error_msg = f"更新 Bot 投資金額時發生資料庫錯誤: {e}"
            logger.error(error_msg, exc_info=True)
            raise Exception(error_msg)
        
        finally:
            # 確保關閉資料庫連線
            db.close()
    
    except ValueError:
        # 重新拋出驗證錯誤
        raise
    except Exception as e:
        error_msg = f"調整 Bot 投資金額時發生錯誤: {e}"
        logger.error(error_msg, exc_info=True)
        raise Exception(error_msg)

