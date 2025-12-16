#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
BloFin Live PnL Dashboard (with Trailing Control, Auto/Manual Close, Logs & Diagnostics)
新增：Open Positions 區塊的表格邊線/格線樣式（Header 與每列四邊 + 直向分隔視覺）
"""

import uuid
import hmac
import hashlib
import base64
import json
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Tuple

import requests
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components


# ================== CONFIG ==================
API_FILE = "Blofin_API.txt"
LOG_FILE = "autostop_log.csv"
DEFAULT_SYMBOL = "BTC-USDT"
REFRESH_INTERVAL_SEC = 5
# ========== API KEY LOADING ==========
@st.cache_data(show_spinner=False)
def load_api_keys(path: str) -> Dict[str, str]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"API settings file not found: {path}")

    kv: Dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            kv[k.strip()] = v.strip()

    missing = [k for k in ("API_KEY", "API_SECRET", "API_PASSPHRASE") if not kv.get(k)]
    if missing:
        raise ValueError(f"Blofin_API.txt missing fields: {', '.join(missing)}")

    return {
        "API_KEY": kv["API_KEY"],
        "API_SECRET": kv["API_SECRET"],
        "API_PASSPHRASE": kv["API_PASSPHRASE"],
    }


# ========== SIGN / HTTP HELPERS ==========
def get_base_url(use_demo: bool) -> str:
    return "https://demo-trading-openapi.blofin.com" if use_demo else "https://openapi.blofin.com"


def sign_request(method: str, path: str, body: Optional[Dict[str, Any]], api_secret: str) -> Tuple[str, str, str]:
    import time as _time
    method = method.upper()
    timestamp = str(int(_time.time() * 1000))
    nonce = str(uuid.uuid4())
    body_str = "" if body is None else json.dumps(body)
    prehash = f"{path}{method}{timestamp}{nonce}{body_str}"
    hex_signature = hmac.new(
        api_secret.encode("utf-8"),
        prehash.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest().encode("utf-8")
    signature = base64.b64encode(hex_signature).decode("utf-8")
    return signature, timestamp, nonce


def make_headers(method: str, path: str, body: Optional[Dict[str, Any]],
                 api_key: str, api_secret: str, api_passphrase: str) -> Dict[str, str]:
    sign, ts, nonce = sign_request(method, path, body, api_secret)
    return {
        "ACCESS-KEY": api_key,
        "ACCESS-SIGN": sign,
        "ACCESS-TIMESTAMP": ts,
        "ACCESS-NONCE": nonce,
        "ACCESS-PASSPHRASE": api_passphrase,
        "Content-Type": "application/json",
    }


def private_get(base_url: str, path: str, params: Optional[Dict[str, Any]],
                api_key: str, api_secret: str, api_passphrase: str) -> Dict[str, Any]:
    from urllib.parse import urlencode
    if params:
        query = urlencode(params)
        signed_path = f"{path}?{query}"
        url = f"{base_url}{signed_path}"
    else:
        signed_path = path
        url = f"{base_url}{path}"
    headers = make_headers("GET", signed_path, None, api_key, api_secret, api_passphrase)
    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "0":
        raise RuntimeError(f"API error: code={data.get('code')} msg={data.get('msg')}")
    return data


def private_post(base_url: str, path: str, body: Dict[str, Any],
                 api_key: str, api_secret: str, api_passphrase: str) -> Dict[str, Any]:
    signed_path = path
    url = f"{base_url}{path}"
    headers = make_headers("POST", signed_path, body, api_key, api_secret, api_passphrase)
    resp = requests.post(url, headers=headers, json=body, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "0":
        raise RuntimeError(f"API error: code={data.get('code')} msg={data.get('msg')}")
    return data


# ========== BLOFIN WRAPPERS ==========
def get_last_price(base_url: str, inst_id: str) -> float:
    url = f"{base_url}/api/v1/market/tickers"
    params = {"instId": inst_id}
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "0":
        raise RuntimeError(f"API error: code={data.get('code')} msg={data.get('msg')}")
    rows = data.get("data", [])
    if not rows:
        raise RuntimeError("No ticker data returned")
    row = rows[0]
    return float(row["last"])


def get_positions(base_url: str, inst_id: Optional[str],
                  api_key: str, api_secret: str, api_passphrase: str) -> List[Dict[str, Any]]:
    params = {"instId": inst_id} if inst_id else None
    res = private_get(base_url, "/api/v1/account/positions", params,
                      api_key, api_secret, api_passphrase)
    return res.get("data", [])


def get_copytrading_positions(base_url: str, inst_id: Optional[str],
                              api_key: str, api_secret: str, api_passphrase: str) -> List[Dict[str, Any]]:
    """Fetch positions from copy-trading/bot accounts."""
    try:
        params = {"instId": inst_id} if inst_id else None
        res = private_get(base_url, "/api/v1/copytrading/account/positions-by-contract", params,
                          api_key, api_secret, api_passphrase)
        positions = res.get("data", [])
        # Mark these as bot positions for identification
        for pos in positions:
            pos["_source"] = "bot"
        return positions
    except Exception as e:
        # If copy-trading endpoint doesn't exist or fails, return empty list
        return []


def get_all_positions(base_url: str, inst_id: Optional[str],
                      api_key: str, api_secret: str, api_passphrase: str,
                      include_bot: bool = True) -> List[Dict[str, Any]]:
    """Fetch both regular and bot positions, merging them together."""
    regular_positions = get_positions(base_url, inst_id, api_key, api_secret, api_passphrase)
    for pos in regular_positions:
        pos["_source"] = "manual"
    
    all_positions = regular_positions.copy()
    
    if include_bot:
        bot_positions = get_copytrading_positions(base_url, inst_id, api_key, api_secret, api_passphrase)
        all_positions.extend(bot_positions)
    
    return all_positions


def get_active_orders(base_url: str, inst_id: Optional[str],
                      api_key: str, api_secret: str, api_passphrase: str) -> List[Dict[str, Any]]:
    params = {"instId": inst_id} if inst_id else None
    res = private_get(base_url, "/api/v1/trade/orders-pending", params,
                      api_key, api_secret, api_passphrase)
    return res.get("data", [])


def close_position(base_url: str, inst_id: str, margin_mode: str, position_side: str,
                   api_key: str, api_secret: str, api_passphrase: str) -> Dict[str, Any]:
    body = {
        "instId": inst_id,
        "marginMode": margin_mode,
        "positionSide": position_side,  # "long" / "short" / "net"
        "clientOrderId": "",
    }
    path = "/api/v1/trade/close-position"
    return private_post(base_url, path, body, api_key, api_secret, api_passphrase)


# ========== UTILITIES ==========
def detect_side(position_side: str, qty: float) -> str:
    if position_side == "long":
        return "Long"
    if position_side == "short":
        return "Short"
    if position_side == "net":
        if qty > 0:
            return "Long"
        elif qty < 0:
            return "Short"
    return "-"


def positions_summary(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    total_pos_usdt = 0.0
    total_unreal = 0.0
    long_count = short_count = 0
    for r in rows:
        margin_val = float(r.get("margin") or r.get("initialMargin") or 0)
        lev_val = float(r.get("leverage") or 0)
        notional = margin_val * lev_val
        total_pos_usdt += abs(notional)

        unreal = float(r.get("unrealizedPnl", 0) or 0)
        total_unreal += unreal

        qty = float(r.get("positions", 0) or 0)
        side = r.get("positionSide", "net")
        if side == "long" or (side == "net" and qty > 0):
            long_count += 1
        elif side == "short" or (side == "net" and qty < 0):
            short_count += 1
    return {
        "total_positions_size": total_pos_usdt,
        "total_unrealized": total_unreal,
        "long_count": long_count,
        "short_count": short_count,
    }


def orders_summary(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    return {
        "total_orders": len(rows),
        "buy_orders": sum(1 for r in rows if r.get("side") == "buy"),
        "sell_orders": sum(1 for r in rows if r.get("side") == "sell"),
    }


def show_countdown(seconds: int):
    components.html(
        f"""
        <div style="
            position:fixed;
            top:8px;
            right:16px;
            z-index:9999;
            color:#aaa;
            font-size:13px;
            background-color:rgba(0,0,0,0.4);
            padding:4px 10px;
            border-radius:12px;">
          ⏱ Auto refresh in <span id="sec">{seconds}</span> s
        </div>
        <script>
        var counter = {seconds};
        var el = document.getElementById("sec");
        setInterval(function(){{
            if(!el) return;
            counter -= 1;
            if(counter < 0) counter = 0;
            el.textContent = counter.toString();
        }}, 1000);
        </script>
        """,
        height=40,
    )


def safe_rerun():
    try:
        st.rerun()
    except AttributeError:
        try:
            st.experimental_rerun()
        except AttributeError:
            pass


# ====== NEW: table cell HTML with borders (header & rows) ======
def inject_table_css_once():
    st.markdown(
        """
<style>
:root{
  --tbl-bg:#11151f;
  --tbl-bg-alt:#161b26;
  --tbl-border:#222836;
  --tbl-header:#1a2131;
  --tbl-text:#d5d9e5;
  --tbl-muted:#9099ad;
  --tbl-positive:#40ffb3;
  --tbl-negative:#ff6b6b;
}

/* 表格樣式調整，貼近 dark dataframe */
.cell-box{
  position:relative;
  padding:10px 14px;
  border:1px solid transparent;
  background:var(--tbl-bg);
  color:var(--tbl-text);
  font-size:13px;
  font-variant-numeric:tabular-nums;
  display:flex;
  align-items:center;
  min-height:44px;
}
.cell-first{ border-left:1px solid transparent; }
.cell-last{ border-right:1px solid transparent; }
.cell-header{
  font-weight:600;
  background:var(--tbl-header);
  color:var(--tbl-text);
}
.cell-center{
  display:flex;
  align-items:center;
  justify-content:center;
  text-align:center;
}
.cell-right{ text-align:right; }
.cell-row-last{ border-bottom:1px solid transparent; }
.cell-row-alt{ background:var(--tbl-bg-alt); }

/* badge styles */
.badge{
  display:inline-block;
  padding:3px 10px;
  border-radius:999px;
  font-weight:600;
  font-size:12px;
  text-transform:uppercase;
  letter-spacing:0.4px;
}
.badge-base{
  background:rgba(59,130,246,0.18);
  color:#90caf9;
  border:1px solid rgba(59,130,246,0.35);
}
.badge-dyn{
  background:rgba(252,211,77,0.22);
  color:#fcd34d;
  border:1px solid rgba(252,211,77,0.35);
}
.pnl-positive,
.pnl-negative,
.pnl-neutral{
  display:inline-block;
  font-weight:700;
}
.pnl-positive{ color:var(--tbl-positive); }
.pnl-negative{ color:var(--tbl-negative); }
.pnl-neutral{ color:var(--tbl-muted); font-weight:600; }

div[data-testid="column"] > div:has(.cell-box){
  padding:0 !important;
  margin:0 !important;
  min-height:100%;
  display:flex;
  align-items:stretch;
  background:var(--tbl-bg);
}

div[data-testid="stHorizontalBlock"]{
  gap:0 !important;
}

.close-cell-holder{
  height:100%;
  display:flex;
  align-items:center;
  justify-content:center;
  background:var(--tbl-bg);
}
.close-cell-holder-alt{
  background:var(--tbl-bg-alt);
}

div[data-testid="column"]:has(.close-cell-holder) > div button{
  width:36px;
  height:36px;
  margin:0;
  border-radius:12px;
  background:var(--tbl-header);
  border:1px solid var(--tbl-border);
  color:var(--tbl-text);
  font-size:16px;
  line-height:1;
}
div[data-testid="column"]:has(.close-cell-holder) > div button:hover{
  background:var(--tbl-bg-alt);
  border-color:var(--tbl-positive);
  color:var(--tbl-positive);
}
div[data-testid="column"]:has(.close-cell-holder){
  display:flex;
  align-items:center;
  justify-content:center;
}
div[data-testid="column"]:has(.close-cell-holder) > div button{
  transform:translateY(-15px);
}
</style>

button[data-testid="baseButton-secondary"]{
  width:100%;
  margin-top:4px;
  font-size:13px;
  border-radius:4px;
  padding:4px 10px;
}
</style>
        """,
        unsafe_allow_html=True,
    )


def cell_html(
    content: str,
    first: bool = False,
    last: bool = False,
    header: bool = False,
    align: str = "left",
    row_last: bool = False,
    extra_cls: str = "",
) -> str:
    cls = "cell-box"
    if first:
        cls += " cell-first"
    if last:
        cls += " cell-last"
    if header:
        cls += " cell-header"
    if align == "center":
        cls += " cell-center"
    elif align == "right":
        cls += " cell-right"
    if row_last:
        cls += " cell-row-last"
    if extra_cls:
        cls += f" {extra_cls}"
    return f"<div class='{cls}'>{content}</div>"


# --- trailing + base-stop helpers ---
def update_trailing_state(
    positions_rows: List[Dict[str, Any]],
    profit_threshold_pct: float,
    lock_ratio: float,
    base_sl_pct: float,
    trailing_enabled: bool,
):
    if "trailing" not in st.session_state:
        st.session_state["trailing"] = {}
    state = st.session_state["trailing"]
    active_keys = set()

    for pos in positions_rows:
        inst_id = pos["instId"]
        margin_mode = pos.get("marginMode", "")
        position_side = pos.get("positionSide", "")
        qty = float(pos.get("positions", 0) or 0)
        if qty == 0:
            continue

        side = detect_side(position_side, qty)
        if side == "-":
            continue

        entry = float(pos.get("averagePrice", 0) or 0)
        mark = float(pos.get("markPrice", 0) or 0)

        key = f"{inst_id}|{margin_mode}|{position_side}"
        active_keys.add(key)

        if key not in state:
            state[key] = {
                "entry": entry,
                "best": mark,
                "side": side,
                "profit_pct": 0.0,
                "dyn_stop": None,
                "triggered": False,
                "is_trailing": False,
            }
        else:
            if abs(state[key]["entry"] - entry) > 1e-8:
                state[key].update(
                    entry=entry,
                    best=mark,
                    profit_pct=0.0,
                    dyn_stop=None,
                    triggered=False,
                    is_trailing=False,
                )

        best = state[key]["best"]
        if side == "Long":
            best = max(best, mark)
            profit_pct = (best - entry) / entry * 100 if entry != 0 else 0.0
        else:
            best = min(best, mark)
            profit_pct = (entry - best) / entry * 100 if entry != 0 else 0.0

        dyn_stop = None
        triggered = False
        is_trailing = False

        # Trailing stop
        if trailing_enabled and profit_pct >= profit_threshold_pct:
            if side == "Long":
                dyn_stop = entry + (best - entry) * lock_ratio
                triggered = mark <= dyn_stop
            else:
                dyn_stop = entry - (entry - best) * lock_ratio
                triggered = mark >= dyn_stop
            is_trailing = True

        # Base stop-loss
        elif base_sl_pct > 0 and entry > 0:
            if side == "Long":
                dyn_stop = entry * (1 - base_sl_pct / 100.0)
                triggered = mark <= dyn_stop
            elif side == "Short":
                dyn_stop = entry * (1 + base_sl_pct / 100.0)
                triggered = mark >= dyn_stop
            is_trailing = False

        state[key]["best"] = best
        state[key]["profit_pct"] = profit_pct
        state[key]["dyn_stop"] = dyn_stop
        state[key]["triggered"] = triggered
        state[key]["is_trailing"] = is_trailing

    # 清理不再存在的 key，同時清理 auto_closed
    if "auto_closed" not in st.session_state:
        st.session_state["auto_closed"] = {}
    
    for k in list(state.keys()):
        if k not in active_keys:
            del state[k]
            # 同步清理 auto_closed 中對應的 key
            st.session_state["auto_closed"].pop(k, None)

    return state


def merge_positions_with_trailing(
    positions_rows: List[Dict[str, Any]],
    trailing_state: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    merged = []
    for pos in positions_rows:
        inst_id = pos["instId"]
        margin_mode = pos.get("marginMode", "")
        position_side = pos.get("positionSide", "")
        qty = float(pos.get("positions", 0) or 0)
        side = detect_side(position_side, qty)
        entry = float(pos.get("averagePrice", 0) or 0)
        mark = float(pos.get("markPrice", 0) or 0)

        if entry != 0:
            if side == "Long":
                profit_pct_now = (mark - entry) / entry * 100
            elif side == "Short":
                profit_pct_now = (entry - mark) / entry * 100
            else:
                profit_pct_now = 0.0
        else:
            profit_pct_now = 0.0

        key = f"{inst_id}|{margin_mode}|{position_side}"
        t = trailing_state.get(key, {})

        row = dict(pos)
        row["SidePretty"] = side
        row["EntryPrice"] = entry
        row["MarkPrice"] = mark
        row["profitPctNow"] = round(profit_pct_now, 4)
        row["trailBest"] = t.get("best")
        row["trailProfitPct"] = round(t.get("profit_pct", 0.0), 4) if t else None
        row["trailDynStop"] = t.get("dyn_stop")
        row["trailTriggered"] = bool(t.get("triggered", False))
        row["isTrailingStop"] = bool(t.get("is_trailing", False))
        merged.append(row)
    return merged


# ========== LOGGING HELPERS ==========
def append_autostop_log(
    inst_id: str,
    side: str,
    margin_mode: str,
    position_side: str,
    qty: float,
    entry: float,
    close_price: float,
    dyn_stop: Optional[float],
    trail_best: Optional[float],
    pnl: float,
    pnl_pct: float,
    profit_threshold_pct: float,
    lock_ratio: float,
    base_sl_pct: float,
    stop_source: str,
    stop_kind: str,
):
    now = datetime.now(timezone.utc)
    data = {
        "time": now.isoformat(),
        "date": now.date().isoformat(),
        "instId": inst_id,
        "side": side,
        "marginMode": margin_mode,
        "positionSide": position_side,
        "qty": qty,
        "entryPrice": entry,
        "closePrice": close_price,
        "trailBest": trail_best,
        "dynamicStop": dyn_stop,
        "pnl": pnl,
        "pnlPct": pnl_pct,
        "profitThresholdPct": profit_threshold_pct,
        "lockRatio": lock_ratio,
        "baseSlPct": base_sl_pct,
        "source": stop_source,
        "stopKind": stop_kind,
    }
    df_row = pd.DataFrame([data])
    if os.path.exists(LOG_FILE):
        df_row.to_csv(LOG_FILE, mode="a", header=False, index=False)
    else:
        df_row.to_csv(LOG_FILE, mode="w", header=True, index=False)


def load_autostop_logs() -> Optional[pd.DataFrame]:
    if not os.path.exists(LOG_FILE):
        return None
    if os.path.getsize(LOG_FILE) == 0:
        return None
    try:
        df = pd.read_csv(LOG_FILE, parse_dates=["time"])
    except Exception:
        try:
            df = pd.read_csv(LOG_FILE)
        except Exception:
            return None
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], errors="coerce")
    else:
        df["time"] = pd.NaT
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    else:
        df["date"] = pd.to_datetime(df["time"], errors="coerce").dt.date
    if df["date"].isna().all():
        return None
    return df


def save_autostop_logs(df: pd.DataFrame):
    df.to_csv(LOG_FILE, index=False)


def prune_autostop_logs(days: int = 30):
    """
    只保留最近 N 天的 autostop logs，刪除舊資料
    
    Args:
        days: 保留最近幾天的 logs（預設 30 天）
    """
    df = load_autostop_logs()
    if df is None or df.empty:
        return
    
    # 使用 "time" 欄位作為時間基準
    time_col = "time"
    if time_col not in df.columns:
        return
    
    # 轉換為 datetime，無法 parse 的設為 NaT
    df[time_col] = pd.to_datetime(df[time_col], errors="coerce")
    
    # 計算截止時間（UTC）
    cutoff = pd.Timestamp.utcnow() - pd.Timedelta(days=days)
    
    # 過濾出時間 >= cutoff 的資料（並排除 NaT）
    df_pruned = df[df[time_col] >= cutoff].copy()
    
    # 如果還有 "date" 欄位，也要更新
    if "date" in df_pruned.columns:
        df_pruned["date"] = pd.to_datetime(df_pruned[time_col], errors="coerce").dt.date
    
    # 儲存清理後的資料
    save_autostop_logs(df_pruned)


# ========== STREAMLIT APP ==========
def main():
    st.set_page_config(page_title="BloFin Live Dashboard", page_icon="📈", layout="wide")

    if "auto_refresh" not in st.session_state:
        st.session_state["auto_refresh"] = True
    if "auto_closed" not in st.session_state:
        st.session_state["auto_closed"] = {}
    inject_table_css_once()  # <--- 新增：只注入一次 CSS

    show_countdown(REFRESH_INTERVAL_SEC)

    st.title("📈 BloFin Live PnL Dashboard")
    st.caption(f"Last updated: {pd.Timestamp.now():%Y-%m-%d %H:%M:%S}")

    with st.sidebar:
        st.header("⚙️ Settings")
        env = st.radio("Environment", ["Demo", "Live"], index=0)
        use_demo = env == "Demo"
        base_url = get_base_url(use_demo)

        auto = st.checkbox("Auto refresh", value=st.session_state["auto_refresh"])
        st.session_state["auto_refresh"] = auto

        st.caption(f"Base URL: `{base_url}`")
        st.caption(f"API file: `{API_FILE}`")

        st.markdown("---")
        st.subheader("🎯 Trailing 2/3-Profit Settings")
        trailing_enabled = st.checkbox("Enable trailing stop info", value=True)
        profit_threshold_pct = st.number_input(
            "Profit threshold (%) before trailing starts",
            min_value=0.0, value=1.0, step=0.1
        )
        lock_ratio = st.number_input(
            "Lock ratio (e.g. 0.67 ≈ 2/3 profit)",
            min_value=0.0, max_value=1.0, value=2.0 / 3.0, step=0.05
        )
        base_sl_pct = st.number_input(
            "Base stop-loss distance (%)",
            min_value=0.0, value=0.5, step=0.1,
            help="If profit threshold not reached, Dynamic Stop = entry price +/- this %."
        )
        auto_close_enabled = st.checkbox(
            "Auto close when Dynamic Stop triggered",
            value=True,
        )
        include_bot_positions = st.checkbox(
            "Include bot/copy-trading positions",
            value=True,
            help="Fetch positions from copy-trading/bot accounts in addition to manual positions"
        )
        st.caption(
            "Dynamic Stop = trailing stop after threshold; "
            "otherwise fallback to: entry * (1 - SL%) for long, entry * (1 + SL%) for short."
        )
        
        st.markdown("---")
        with st.expander("🧹 Autostop Logs Maintenance", expanded=False):
            days = st.number_input(
                "只保留最近幾天的 autostop logs",
                min_value=1,
                max_value=365,
                value=30,
                step=1,
            )
            if st.button("Prune Logs", type="secondary", use_container_width=True):
                try:
                    prune_autostop_logs(days)
                    st.success(f"已保留最近 {days} 天的 autostop logs，舊資料已清除。")
                except Exception as e:
                    st.error(f"Prune logs 失敗: {e}")

    # API keys
    symbol = DEFAULT_SYMBOL

    try:
        keys = load_api_keys(API_FILE)
    except Exception as e:
        st.error(f"Failed to load API keys: {e}")
        if st.session_state.get("auto_refresh", False):
            time.sleep(REFRESH_INTERVAL_SEC)
            safe_rerun()
        return
    api_key = keys["API_KEY"]
    api_secret = keys["API_SECRET"]
    api_passphrase = keys["API_PASSPHRASE"]

    # fetch data (with retry on error)
    with st.spinner("Fetching data from BloFin..."):
        try:
            last_price = get_last_price(base_url, DEFAULT_SYMBOL)
            positions_rows = get_all_positions(base_url, None, api_key, api_secret, api_passphrase, include_bot=include_bot_positions)
            orders_rows = get_active_orders(base_url, None, api_key, api_secret, api_passphrase)
        except Exception as e:
            st.error(f"Error fetching data: {e}")
            if st.session_state.get("auto_refresh", False):
                time.sleep(REFRESH_INTERVAL_SEC)
                safe_rerun()
            return

    trailing_state = update_trailing_state(
        positions_rows,
        profit_threshold_pct=profit_threshold_pct,
        lock_ratio=lock_ratio,
        base_sl_pct=base_sl_pct,
        trailing_enabled=trailing_enabled,
    )
    merged_positions = merge_positions_with_trailing(positions_rows, trailing_state)

    # --- AUTO CLOSE ---
    if auto_close_enabled:
        auto_closed: Dict[str, float] = st.session_state["auto_closed"]
        for pos in merged_positions:
            inst = pos["instId"]
            margin_mode = pos.get("marginMode", "")
            position_side = pos.get("positionSide", "")
            qty = float(pos.get("positions", 0) or 0)
            if qty == 0:
                continue
            key = f"{inst}|{margin_mode}|{position_side}"
            t = trailing_state.get(key)
            if not t:
                continue
            dyn_stop = t.get("dyn_stop")
            if dyn_stop is None:
                continue
            if not t.get("triggered", False):
                continue
            if key in auto_closed:
                continue

            side_pretty = pos.get("SidePretty")
            entry = float(pos.get("EntryPrice", 0) or 0)
            close_price = float(pos.get("MarkPrice", 0) or 0)
            pnl_amt = float(pos.get("unrealizedPnl", 0) or 0)
            pnl_pct = float(pos.get("profitPctNow", 0) or 0)
            trail_best = t.get("best")

            try:
                res = close_position(
                    base_url=base_url,
                    inst_id=inst,
                    margin_mode=margin_mode,
                    position_side=position_side,
                    api_key=api_key,
                    api_secret=api_secret,
                    api_passphrase=api_passphrase,
                )
                auto_closed[key] = time.time()
                stop_kind = "Trailing" if t.get("is_trailing") else "Base"
                append_autostop_log(
                    inst_id=inst,
                    side=side_pretty,
                    margin_mode=margin_mode,
                    position_side=position_side,
                    qty=qty,
                    entry=entry,
                    close_price=close_price,
                    dyn_stop=dyn_stop,
                    trail_best=trail_best,
                    pnl=pnl_amt,
                    pnl_pct=pnl_pct,
                    profit_threshold_pct=profit_threshold_pct,
                    lock_ratio=lock_ratio,
                    base_sl_pct=base_sl_pct,
                    stop_source="Auto",
                    stop_kind=stop_kind,
                )
                st.success(
                    f"🔔 Auto-closed {inst} ({position_side}) at Dynamic Stop. code={res.get('code')}"
                )
            except Exception as e:
                st.error(f"[Auto] close fail {inst} mm={margin_mode} ps={position_side} -> {e}")

    # top metrics
    pos_sum = positions_summary(positions_rows)
    ord_sum = orders_summary(orders_rows)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(label=f"{DEFAULT_SYMBOL} Last Price", value=f"{last_price:.4f}")
    c2.metric(label="Open Position Size (USDT, all coins)", value=f"{pos_sum['total_positions_size']:.4f}")
    c3.metric(label="Total Unrealized PnL", value=f"{pos_sum['total_unrealized']:.4f}")
    c4.metric(label="Active Orders", value=str(ord_sum["total_orders"]),
              delta=f"Buy {ord_sum['buy_orders']} / Sell {ord_sum['sell_orders']}")
    total_positions_count = pos_sum["long_count"] + pos_sum["short_count"]
    c5.metric(
        label="Open Positions (L/S)",
        value=str(total_positions_count),
        delta=f"Long {pos_sum['long_count']} / Short {pos_sum['short_count']}"
    )

    st.markdown("---")
    st.subheader("📒 Open Positions (All Coins)")
    st.markdown(
        "<span style='color:#ffcc00;font-weight:600'>Trailing Dynamic Stop</span> "
        "vs <span style='color:#888888;font-weight:600'>Default Stop Loss</span>",
        unsafe_allow_html=True,
    )

    if not merged_positions:
        st.info("No open positions.")
    else:
        # build display rows
        table_data = []
        for p in merged_positions:
            inst = p.get("instId", "")
            side = p.get("SidePretty", "-")
            lever_raw = p.get("leverage") or p.get("lever") or ""
            try:
                lev_num = float(lever_raw) if lever_raw not in ("", "-", None) else 0.0
            except Exception:
                lev_num = 0.0
            lever_display = lever_raw if lever_raw not in ("", None) else "-"

            qty = float(p.get("positions", 0) or 0)
            entry = float(p.get("EntryPrice", 0) or 0)
            mark = float(p.get("MarkPrice", 0) or 0)
            pnl_amt = float(p.get("unrealizedPnl", 0) or 0)
            pnl_rate = float(p.get("profitPctNow", 0) or 0)
            dyn_stop_val = p.get("trailDynStop")
            trig = p.get("trailTriggered")
            is_trailing_stop = p.get("isTrailingStop", False)
            trail_best_val = p.get("trailBest")
            if trail_best_val is not None:
                try:
                    trail_best_val = round(float(trail_best_val), 4)
                except (TypeError, ValueError):
                    trail_best_val = trail_best_val

            margin_val = float(p.get("margin") or p.get("initialMargin") or 0)
            pos_size_usdt = margin_val * lev_num
            invest_amount = margin_val

            if dyn_stop_val is not None:
                stop_type = "trailing" if is_trailing_stop else "default"
            else:
                stop_type = "none"

            table_data.append(
                {
                    "Coin": inst,
                    "Side": side,
                    "Leverage": lever_display,
                    "Pos Size (USDT)": round(pos_size_usdt, 4),
                    "Invest Amt (USDT)": round(invest_amount, 4),
                    "Entry Price": round(entry, 4),
                    "Current Price": round(mark, 4),
                    "PnL (USDT)": round(pnl_amt, 4),
                    "PnL (%)": round(pnl_rate, 2),
                    "Trail Best": trail_best_val,
                    "Dynamic Stop": dyn_stop_val,
                    "StopType": stop_type,
                    "Triggered": trig,
                    "marginMode": p.get("marginMode", ""),
                    "positionSide": p.get("positionSide", ""),
                    "Source": p.get("_source", "manual"),
                }
            )

        df = pd.DataFrame(table_data)
        df.insert(0, "#", range(1, len(df) + 1))

        col_defs = [0.7, 1.8, 0.8, 0.7, 1.9, 1.8, 1.6, 1.6, 2.1, 1.6, 1.6, 0.8, 0.8]
        headers = [
            "#",
            "Coin",
            "Side",
            "Lev",
            "Pos Size (USDT)",
            "Invest Amt (USDT)",
            "Entry",
            "Current",
            "PnL (USDT / %)",
            "Trail Best",
            "Dynamic Stop",
            "Source",
            "Close",
        ]
        header_align = [
            "center", "left", "left", "center", "right", "right", "right", "right",
            "right", "right", "center", "center", "center"
        ]
        h_cols = st.columns(col_defs, gap="small", vertical_alignment="center")
        for i, text in enumerate(headers):
            first = i == 0
            last = i == len(headers) - 1
            h_cols[i].markdown(
                cell_html(text, first=first, last=last, header=True, align=header_align[i]),
                unsafe_allow_html=True,
            )

        def fmt_number(val, decimals=4):
            try:
                return f"{float(val):.{decimals}f}"
            except (TypeError, ValueError):
                return "-"

        last_idx = len(df)
        for _, row in df.iterrows():
            cols = st.columns(col_defs, gap="small", vertical_alignment="center")
            row_index = int(row["#"])
            is_last_row = row_index == last_idx
            row_alt = (row_index % 2) == 0

            coin = row["Coin"]
            side = row["Side"]
            lev = row["Leverage"]
            pos_size = row["Pos Size (USDT)"]
            invest_amt = row["Invest Amt (USDT)"]
            entry = row["Entry Price"]
            mark = row["Current Price"]
            pnl_amt = row["PnL (USDT)"]
            pnl_pct = row["PnL (%)"]
            trail_best = row["Trail Best"]
            dyn_stop = row["Dynamic Stop"]
            stop_type = row["StopType"]
            trig = row["Triggered"]

            cols[0].markdown(cell_html(str(row_index), first=True, align="center", row_last=is_last_row,
                                       extra_cls="cell-row-alt" if row_alt else ""), unsafe_allow_html=True)
            cols[1].markdown(cell_html(f"**{coin}**", row_last=is_last_row, extra_cls="cell-row-alt" if row_alt else ""), unsafe_allow_html=True)
            cols[2].markdown(cell_html(side, row_last=is_last_row, extra_cls="cell-row-alt" if row_alt else ""), unsafe_allow_html=True)
            cols[3].markdown(cell_html(str(lev), align="center", row_last=is_last_row, extra_cls="cell-row-alt" if row_alt else ""), unsafe_allow_html=True)
            cols[4].markdown(cell_html(fmt_number(pos_size), align="right", row_last=is_last_row, extra_cls="cell-row-alt" if row_alt else ""), unsafe_allow_html=True)
            cols[5].markdown(cell_html(fmt_number(invest_amt), align="right", row_last=is_last_row, extra_cls="cell-row-alt" if row_alt else ""), unsafe_allow_html=True)
            cols[6].markdown(cell_html(fmt_number(entry), align="right", row_last=is_last_row, extra_cls="cell-row-alt" if row_alt else ""), unsafe_allow_html=True)
            cols[7].markdown(cell_html(fmt_number(mark), align="right", row_last=is_last_row, extra_cls="cell-row-alt" if row_alt else ""), unsafe_allow_html=True)

            try:
                pnl_amt_val = float(pnl_amt)
            except (TypeError, ValueError):
                pnl_amt_val = 0.0
            try:
                pnl_pct_val = float(pnl_pct)
            except (TypeError, ValueError):
                pnl_pct_val = 0.0
            pnl_cls = "pnl-neutral"
            if pnl_amt_val > 0:
                pnl_cls = "pnl-positive"
            elif pnl_amt_val < 0:
                pnl_cls = "pnl-negative"
            pnl_html = f"<span class='{pnl_cls}'>{pnl_amt_val:.4f} / {pnl_pct_val:.2f}%</span>"
            cols[8].markdown(cell_html(pnl_html, align="right", row_last=is_last_row, extra_cls="cell-row-alt" if row_alt else ""), unsafe_allow_html=True)

            trail_display = "-" if trail_best is None or pd.isna(trail_best) else fmt_number(trail_best)
            cols[9].markdown(cell_html(trail_display, align="right", row_last=is_last_row, extra_cls="cell-row-alt" if row_alt else ""), unsafe_allow_html=True)

            if dyn_stop is None:
                dyn_display = "<span class='badge badge-base'>None</span>"
            else:
                badge_cls = "badge-dyn" if stop_type == "trailing" else "badge-base"
                dyn_display = f"<span class='badge {badge_cls}'>{fmt_number(dyn_stop)}</span>"
            cols[10].markdown(cell_html(dyn_display, align="right", row_last=is_last_row, extra_cls="cell-row-alt" if row_alt else ""), unsafe_allow_html=True)

            source_label = row.get("Source", "manual")
            source_display = "🤖 Bot" if source_label == "bot" else "👤 Manual"
            cols[11].markdown(cell_html(source_display, align="center", row_last=is_last_row, extra_cls="cell-row-alt" if row_alt else ""), unsafe_allow_html=True)

            with cols[12]:
                st.markdown(
                    cell_html("", last=True, row_last=is_last_row, align="center",
                              extra_cls=("close-cell-holder close-cell-holder-alt" if row_alt else "close-cell-holder")),
                    unsafe_allow_html=True,
                )
                submit_key = f"close_{coin}_{row['positionSide']}_{row_index}"
                if st.button("✖", key=submit_key, use_container_width=True):
                    try:
                        qty = 0.0
                        for p0 in merged_positions:
                            if p0["instId"] == coin and p0["positionSide"] == row["positionSide"]:
                                qty = float(p0.get("positions", 0) or 0)
                                break

                        key = f"{coin}|{row['marginMode']}|{row['positionSide']}"
                        t_state = trailing_state.get(key, {})
                        dyn_stop_snap = t_state.get("dyn_stop")
                        trail_best_snap = t_state.get("best")
                        side_pretty = side

                        res = close_position(
                            base_url=base_url,
                            inst_id=coin,
                            margin_mode=row["marginMode"],
                            position_side=row["positionSide"],
                            api_key=api_key,
                            api_secret=api_secret,
                            api_passphrase=api_passphrase,
                        )

                        if dyn_stop_snap is not None:
                            stop_kind = "Trailing" if t_state.get("is_trailing") else "Base"
                        else:
                            stop_kind = "Manual"
                        append_autostop_log(
                            inst_id=coin,
                            side=side_pretty,
                            margin_mode=row["marginMode"],
                            position_side=row["positionSide"],
                            qty=qty,
                            entry=entry,
                            close_price=mark,
                            dyn_stop=dyn_stop_snap,
                            trail_best=trail_best_snap,
                            pnl=pnl_amt,
                            pnl_pct=pnl_pct,
                            profit_threshold_pct=profit_threshold_pct,
                            lock_ratio=lock_ratio,
                            base_sl_pct=base_sl_pct,
                            stop_source="Manual",
                            stop_kind=stop_kind,
                        )
                        st.success(f"✅ Manual close sent for {coin} ({row['positionSide']}). code={res.get('code')}")
                    except Exception as e:
                        st.error(f"Failed to close {coin} ({row['positionSide']}): {e}")

        # ======= Auto-close diagnostics =======
        with st.expander("🛠 Auto-close diagnostics (why a row didn't close)"):
            rows_info = []
            for p in merged_positions:
                inst = p["instId"]
                margin_mode = p.get("marginMode", "")
                position_side = p.get("positionSide", "")
                qty = float(p.get("positions", 0) or 0)
                key = f"{inst}|{margin_mode}|{position_side}"
                t = trailing_state.get(key, {})
                dyn_stop = t.get("dyn_stop")
                triggered = bool(t.get("triggered", False))

                reason = []
                if not auto_close_enabled:
                    reason.append("auto_close disabled")
                if qty == 0:
                    reason.append("qty==0 (no position)")
                if dyn_stop is None:
                    reason.append("dyn_stop is None")
                if not triggered:
                    reason.append("not triggered")
                if key in st.session_state.get("auto_closed", {}):
                    reason.append("already auto-closed (marked)")
                if not reason:
                    reason = ["READY → will send close-position on refresh"]

                rows_info.append({
                    "instId": inst,
                    "marginMode": margin_mode,
                    "positionSide": position_side,
                    "qty": qty,
                    "dyn_stop": dyn_stop,
                    "triggered": triggered,
                    "status": "; ".join(reason),
                })

            st.dataframe(pd.DataFrame(rows_info), use_container_width=True)

    st.markdown("---")
    st.subheader("📑 Active Orders (All Coins)")
    if not orders_rows:
        st.info("No active orders.")
    else:
        st.dataframe(orders_rows, use_container_width=True)

    st.markdown("---")
    st.subheader("📜 Stop Logs (Auto / Manual)")

    df_logs = load_autostop_logs()

    with st.expander("⬆️ Import logs from CSV (merge into current log)"):
        uploaded = st.file_uploader("Choose CSV file", type=["csv"])
        if uploaded is not None:
            try:
                new_df = pd.read_csv(uploaded, parse_dates=["time"])
                if "date" in new_df.columns:
                    new_df["date"] = pd.to_datetime(new_df["date"]).dt.date
                else:
                    new_df["date"] = new_df["time"].dt.date

                if df_logs is not None:
                    combined = pd.concat([df_logs, new_df], ignore_index=True)
                else:
                    combined = new_df

                subset_cols = [c for c in ["time", "instId", "side", "qty", "source"] if c in combined.columns]
                if subset_cols:
                    combined = combined.drop_duplicates(subset=subset_cols, keep="last")

                save_autostop_logs(combined)
                df_logs = combined
                st.success("Log file imported and merged successfully.")
            except Exception as e:
                st.error(f"Failed to import CSV: {e}")

    if df_logs is None or df_logs.empty:
        st.info("No stop logs yet.")
    else:
        csv_bytes = df_logs.to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Download full log CSV",
            data=csv_bytes,
            file_name="autostop_log.csv",
            mime="text/csv",
        )

        min_date = df_logs["date"].min()
        max_date = df_logs["date"].max()
        default_end = max_date
        default_start = max(min_date, default_end - timedelta(days=7))

        date_range = st.date_input(
            "Select date range",
            (default_start, default_end),
            min_value=min_date,
            max_value=max_date,
        )

        if isinstance(date_range, (tuple, list)):
            start_date, end_date = date_range
        else:
            start_date = min_date
            end_date = date_range

        mask = (df_logs["date"] >= start_date) & (df_logs["date"] <= end_date)
        df_sel = df_logs.loc[mask].copy()

        if df_sel.empty:
            st.warning("No auto-stop events in the selected period.")
        else:
            total_pnl = df_sel["pnl"].sum()
            avg_pnl_pct = df_sel["pnlPct"].mean()
            win_count = (df_sel["pnl"] > 0).sum()
            loss_count = (df_sel["pnl"] < 0).sum()
            total_trades = len(df_sel)
            win_rate = (win_count / total_trades * 100.0) if total_trades > 0 else 0.0

            if "source" in df_sel.columns:
                auto_count = (df_sel["source"] == "Auto").sum()
                manual_count = (df_sel["source"] == "Manual").sum()
            else:
                auto_count = 0
                manual_count = 0

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total PnL (USDT)", f"{total_pnl:.4f}")
            c2.metric("Average PnL (%)", f"{avg_pnl_pct:.2f}%")
            c3.metric("Win / Loss Count", f"{win_count} / {loss_count}")
            c4.metric("Win Rate", f"{win_rate:.1f}%")

            st.markdown(
                f"**Source:** 🤖 Auto: {auto_count}  ｜ 🧑‍💻 Manual: {manual_count}"
            )

            show_cols = [
                "time", "instId", "side", "qty",
                "entryPrice", "closePrice",
                "dynamicStop", "pnl", "pnlPct",
            ]
            if "source" in df_sel.columns:
                show_cols.append("source")
            if "stopKind" in df_sel.columns:
                show_cols.append("stopKind")

            ordered_cols = [c for c in show_cols if c in df_sel.columns]
            remaining_cols = [c for c in df_sel.columns if c not in ordered_cols]
            display_cols = ordered_cols + remaining_cols

            df_view = df_sel[display_cols].sort_values("time", ascending=False).copy()
            df_view.insert(0, "#", range(1, len(df_view) + 1))

            def color_rows(row: pd.Series):
                pnl_val = row.get("pnl")
                if pd.isna(pnl_val):
                    color = "#d5d9e5"
                elif pnl_val > 0:
                    color = "#16c784"
                elif pnl_val < 0:
                    color = "#ff6b6b"
                else:
                    color = "#d5d9e5"
                return [f"color:{color}"] * len(row)

            styled_view = df_view.style.apply(color_rows, axis=1)

            st.dataframe(
                styled_view,
                use_container_width=True,
                hide_index=True,
            )

            total_profit = df_sel.loc[df_sel["pnl"] > 0, "pnl"].sum()
            total_loss = df_sel.loc[df_sel["pnl"] < 0, "pnl"].sum()
            earn_loss_ratio = (total_profit / abs(total_loss)) if total_loss != 0 else None

            st.markdown("---")
            b1, b2, b3 = st.columns(3)
            b1.markdown(
                f"**Total Profit (PnL > 0)：** "
                f"<span style='color:#16c784;font-weight:600'>{total_profit:.4f} USDT</span>",
                unsafe_allow_html=True,
            )
            b2.markdown(
                f"**Total Loss (PnL < 0)：** "
                f"<span style='color:#ff4d4f;font-weight:600'>{total_loss:.4f} USDT</span>",
                unsafe_allow_html=True,
            )
            if earn_loss_ratio is not None:
                b3.markdown(
                    f"**Earn/Loss Ratio：** "
                    f"<span style='font-weight:600'>{earn_loss_ratio:.2f}</span>",
                    unsafe_allow_html=True,
                )
            else:
                b3.markdown("**Earn/Loss Ratio：** N/A")

    if st.session_state.get("auto_refresh", False):
        time.sleep(REFRESH_INTERVAL_SEC)
        safe_rerun()


if __name__ == "__main__":
    main()
