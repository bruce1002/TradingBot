/* ==================== Dashboard JavaScript ==================== */
/* 動態載入和渲染倉位表格，使用 Blofin 風格 */

// 格式化數字（保留小數位）
function fmtNumber(val, decimals = 4) {
  if (val === null || val === undefined || val === "") {
    return "-";
  }
  try {
    const num = parseFloat(val);
    if (isNaN(num)) return "-";
    return num.toFixed(decimals);
  } catch (e) {
    return "-";
  }
}

// 格式化百分比
function fmtPercent(val) {
  if (val === null || val === undefined || val === "") {
    return "-";
  }
  try {
    const num = parseFloat(val);
    if (isNaN(num)) return "-";
    return num.toFixed(2) + "%";
  } catch (e) {
    return "-";
  }
}

// 格式化日期時間（簡化顯示，轉換為本地時區）
function fmtDateTime(dtStr) {
  if (!dtStr) return "-";
  try {
    // 確保正確解析 ISO 格式的時間（包含時區資訊）
    // 如果字符串以 Z 結尾（UTC），或者包含時區偏移，Date 會自動處理
    // 如果沒有時區資訊，假設是 UTC 時間並轉換
    let dt;
    if (typeof dtStr === 'string') {
      // 如果字符串以 Z 結尾，明確表示 UTC
      if (dtStr.endsWith('Z')) {
        dt = new Date(dtStr);
      } else if (dtStr.includes('+') || dtStr.includes('-')) {
        // 包含時區偏移，直接解析
        dt = new Date(dtStr);
      } else {
        // 沒有時區資訊，假設是 UTC，添加 Z
        dt = new Date(dtStr + (dtStr.includes('T') ? 'Z' : ''));
      }
    } else {
      dt = new Date(dtStr);
    }
    
    // 檢查日期是否有效
    if (isNaN(dt.getTime())) {
      return dtStr.substring(0, 19); // 如果解析失敗，返回原始字符串的前19字元
    }
    
    // 轉換為本地時間並格式化（台灣時間 UTC+8）
    return dt.toLocaleString("zh-TW", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
      timeZone: "Asia/Taipei", // 明確指定台灣時區
    });
  } catch (e) {
    return dtStr.substring(0, 19); // 簡單截取前19字元
  }
}

// 計算 PnL（僅對 CLOSED 倉位）
function calculatePnL(position) {
  if (position.status !== "CLOSED" || !position.entry_price || !position.exit_price) {
    return { amount: null, percent: null, cls: "pnl-neutral" };
  }
  
  try {
    const entry = parseFloat(position.entry_price);
    const exit = parseFloat(position.exit_price);
    const qty = parseFloat(position.qty);
    
    if (isNaN(entry) || isNaN(exit) || isNaN(qty)) {
      return { amount: null, percent: null, cls: "pnl-neutral" };
    }
    
    let amount = 0;
    let percent = 0;
    
    if (position.side === "LONG") {
      amount = (exit - entry) * qty;
      percent = ((exit - entry) / entry) * 100;
    } else if (position.side === "SHORT") {
      amount = (entry - exit) * qty;
      percent = ((entry - exit) / entry) * 100;
    }
    
    const cls = amount > 0 ? "pnl-positive" : amount < 0 ? "pnl-negative" : "pnl-neutral";
    
    return { amount, percent, cls };
  } catch (e) {
    return { amount: null, percent: null, cls: "pnl-neutral" };
  }
}

// 取得 Dynamic Stop 顯示
function getDynamicStopDisplay(position) {
  const exitReason = position.exit_reason || "";
  
  // 根據 exit_reason 判斷是否為 dynamic stop
  if (exitReason === "dynamic_stop") {
    const trailCallback = position.trail_callback;
    if (trailCallback !== null && trailCallback !== undefined) {
      return {
        text: fmtPercent(trailCallback * 100),
        badgeCls: "badge-dyn",
      };
    }
  } else if (exitReason === "base_stop") {
    return {
      text: "Base",
      badgeCls: "badge-base",
    };
  }
  
  // 對於 OPEN 倉位，根據 trail_callback 判斷
  if (position.status === "OPEN") {
    if (position.trail_callback === null || position.trail_callback === undefined) {
      return {
        text: "None",
        badgeCls: "badge-none",
      };
    } else if (position.trail_callback === 0) {
      return {
        text: "Base Only",
        badgeCls: "badge-base",
      };
    } else {
      return {
        text: fmtPercent(position.trail_callback * 100),
        badgeCls: "badge-dyn",
      };
    }
  }
  
  return {
    text: "None",
    badgeCls: "badge-none",
  };
}

// 顯示表格載入指示器（小的覆蓋層）
function showTableLoading(container) {
  // 移除現有的載入指示器（如果有的話）
  const existingLoader = container.querySelector(".table-loading-overlay");
  if (existingLoader) return;
  
  // 創建小的載入指示器覆蓋層
  const loader = document.createElement("div");
  loader.className = "table-loading-overlay";
  loader.innerHTML = '<div class="loading-spinner-small"></div>';
  container.appendChild(loader);
}

// 隱藏表格載入指示器
function hideTableLoading(container) {
  const loader = container.querySelector(".table-loading-overlay");
  if (loader) {
    loader.remove();
  }
}

// 打開 TradingView 圖表
function openTradingViewChart(symbol) {
  if (!symbol || symbol === "-") return;
  
  // TradingView 格式：BINANCE:SYMBOL.P (perpetual futures)
  // 如果 symbol 已經是完整格式，直接使用；否則轉換為 BINANCE:SYMBOL.P
  let tvSymbol = symbol.toUpperCase();
  
  // 如果沒有 BINANCE: 前綴，添加它
  if (!tvSymbol.includes(":")) {
    tvSymbol = `BINANCE:${tvSymbol}`;
  }
  
  // 如果沒有 .P 後綴（perpetual），添加它
  if (!tvSymbol.endsWith(".P")) {
    tvSymbol = `${tvSymbol}.P`;
  }
  
  // 打開 TradingView 圖表
  const url = `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(tvSymbol)}`;
  window.open(url, "_blank");
}

// 渲染表格
function renderPositionsTable(data) {
  const container = document.getElementById("positions-table-container");
  if (!container) return;
  
  if (!data || data.length === 0) {
    container.innerHTML = '<div class="empty-state">目前沒有倉位記錄</div>';
    return;
  }
  
  // 只顯示最近 100 筆
  const positions = data.slice(0, 100);
  
  // 檢查是否已有表格（更新模式）
  const existingTable = container.querySelector("table");
  const existingTbody = existingTable ? existingTable.querySelector("tbody") : null;
  
  // 生成表格行的函數（避免重複代碼）
  function generateRowHtml(position, index) {
    const rowAlt = (index % 2) === 1;
    const sideCls = position.side === "LONG" ? "side-long" : "side-short";
    const statusCls = `status-${position.status.toLowerCase()}`;
    
    // 計算 PnL
    const pnl = calculatePnL(position);
    let pnlHtml = "-";
    if (pnl.amount !== null && pnl.percent !== null) {
      pnlHtml = `<span class="${pnl.cls}">${fmtNumber(pnl.amount)} / ${fmtPercent(pnl.percent)}</span>`;
    } else if (position.status === "OPEN") {
      pnlHtml = `<span class="pnl-neutral">-</span>`;
    }
    
    // Stop Mode 顯示
    let stopModeBadge = "";
    if (position.stop_mode === "dynamic" || position.stop_mode === "dynamic_trailing") {
      stopModeBadge = '<span class="tag-dyn">Dynamic</span>';
    } else if (position.stop_mode === "base" || position.stop_mode === "base_stop") {
      stopModeBadge = '<span class="tag-base">Base</span>';
    } else {
      stopModeBadge = '<span style="color: var(--tbl-muted);">—</span>';
    }
    
    // Stop Price 顯示（根據 Stop Mode 只顯示對應的價格，顏色與 Stop Mode 一致）
    let stopPriceDisplay = "—";
    if ((position.stop_mode === "dynamic" || position.stop_mode === "dynamic_trailing") && position.dynamic_stop_price !== null && position.dynamic_stop_price !== undefined && position.dynamic_stop_price > 0) {
      const dynPrice = fmtNumber(position.dynamic_stop_price, 4);
      stopPriceDisplay = `<span class="tag-dyn">${dynPrice}</span>`;
    } else if ((position.stop_mode === "base" || position.stop_mode === "base_stop") && position.base_stop_price !== null && position.base_stop_price !== undefined && position.base_stop_price > 0) {
      const basePrice = fmtNumber(position.base_stop_price, 4);
      stopPriceDisplay = `<span class="tag-base">${basePrice}</span>`;
    }
    
    // Profit Threshold 顯示（根據來源顯示不同顏色）
    let profitThresholdHtml = "—";
    let profitThresholdClass = "";
    if (position.profit_threshold_value !== null && position.profit_threshold_value !== undefined) {
      profitThresholdHtml = position.profit_threshold_value.toFixed(2) + "%";
      // 根據來源設定顏色：override=黃色, global=藍色, default=灰色
      if (position.profit_threshold_source === "override") {
        profitThresholdClass = "stop-value-override";  // 黃色
      } else if (position.profit_threshold_source === "global") {
        profitThresholdClass = "stop-value-global";  // 藍色
      } else {
        profitThresholdClass = "stop-value-default";  // 灰色
      }
    }
    profitThresholdHtml = profitThresholdHtml !== "—" 
      ? `<span class="${profitThresholdClass}">${profitThresholdHtml}</span>`
      : profitThresholdHtml;
    
    // Trail Callback (Lock Ratio) 顯示（根據來源顯示不同顏色）
    let trailCallbackHtml = "—";
    let trailCallbackClass = "";
    if (position.lock_ratio_value !== null && position.lock_ratio_value !== undefined) {
      if (position.lock_ratio_value === 0) {
        trailCallbackHtml = "Base Only";
      } else {
        trailCallbackHtml = fmtPercent(position.lock_ratio_value * 100);
      }
      // 根據來源設定顏色：override=黃色, global=藍色, default=灰色
      if (position.lock_ratio_source === "override") {
        trailCallbackClass = "stop-value-override";  // 黃色
      } else if (position.lock_ratio_source === "global") {
        trailCallbackClass = "stop-value-global";  // 藍色
      } else {
        trailCallbackClass = "stop-value-default";  // 灰色
      }
      trailCallbackHtml = `<span class="${trailCallbackClass}">${trailCallbackHtml}</span>`;
    }
    
    // Base SL% 顯示（根據來源顯示不同顏色）
    let baseSlHtml = "—";
    let baseSlClass = "";
    if (position.base_sl_value !== null && position.base_sl_value !== undefined) {
      baseSlHtml = position.base_sl_value.toFixed(2) + "%";
      // 根據來源設定顏色：override=黃色, global=藍色, default=灰色
      if (position.base_sl_source === "override") {
        baseSlClass = "stop-value-override";  // 黃色
      } else if (position.base_sl_source === "global") {
        baseSlClass = "stop-value-global";  // 藍色
      } else {
        baseSlClass = "stop-value-default";  // 灰色
      }
      baseSlHtml = `<span class="${baseSlClass}">${baseSlHtml}</span>`;
    }
    
    // Mechanism Controls (only for OPEN positions)
    let mechanismHtml = "<span class='text-muted'>-</span>";
    if (position.status === "OPEN") {
      const botStopEnabled = position.bot_stop_loss_enabled !== false; // Default to true
      const tvSignalEnabled = position.tv_signal_close_enabled !== false; // Default to true
      
      mechanismHtml = `
        <div class="mechanism-controls">
          <div class="mechanism-toggle">
            <label class="toggle-label" title="Bot 內建停損機制（Dynamic Stop / Base Stop）">
              <span class="toggle-text">Bot SL</span>
              <div class="toggle-wrapper">
                <input type="checkbox" 
                       class="mechanism-checkbox" 
                       data-position-id="${position.id}"
                       data-mechanism="bot_stop_loss"
                       ${botStopEnabled ? 'checked' : ''}
                       onchange="updateMechanismConfig(${position.id}, 'bot_stop_loss_enabled', this.checked)">
                <span class="toggle-slider"></span>
              </div>
            </label>
          </div>
          <div class="mechanism-toggle">
            <label class="toggle-label" title="TradingView 訊號關倉機制（position_size=0）">
              <span class="toggle-text">TV Close</span>
              <div class="toggle-wrapper">
                <input type="checkbox" 
                       class="mechanism-checkbox" 
                       data-position-id="${position.id}"
                       data-mechanism="tv_signal_close"
                       ${tvSignalEnabled ? 'checked' : ''}
                       onchange="updateMechanismConfig(${position.id}, 'tv_signal_close_enabled', this.checked)">
                <span class="toggle-slider"></span>
              </div>
            </label>
          </div>
        </div>
      `;
    }
    
    // Actions 按鈕
    let actionsHtml = "<span class='text-muted'>-</span>";
    if (position.status === "OPEN") {
      actionsHtml = `
        <div class="action-buttons">
          <button class="action-btn action-btn-close" 
                  onclick="closePosition(${position.id}, '${position.symbol}')">
            關倉
          </button>
          <button class="action-btn" 
                  onclick="openStopConfigModal(${position.id})">
            停損設定
          </button>
        </div>
      `;
    }
    
    return `
      <tr class="${rowAlt ? "row-alt" : ""}">
        <td class="text-center">${position.id}</td>
        <td><strong><a href="#" data-symbol="${position.symbol || ""}" class="symbol-link" title="點擊打開 TradingView 圖表">${position.symbol || "-"}</a></strong></td>
        <td><span class="${sideCls}">${position.side || "-"}</span></td>
        <td class="text-right">${fmtNumber(position.qty, 6)}</td>
        <td class="text-right">${fmtNumber(position.entry_price, 4)}</td>
        <td class="text-right">${fmtNumber(position.exit_price, 4)}</td>
        <td class="text-center">
          <span class="status-badge ${statusCls}">${position.status || "-"}</span>
        </td>
        <td>${position.exit_reason || "-"}</td>
        <td class="text-right">${pnlHtml}</td>
        <td class="text-right">${profitThresholdHtml}</td>
        <td class="text-right">${trailCallbackHtml}</td>
        <td class="text-right">${baseSlHtml}</td>
        <td class="text-center">${stopModeBadge}</td>
        <td class="text-right" style="font-size: 12px;">${stopPriceDisplay}</td>
        <td class="text-center">${mechanismHtml}</td>
        <td>${fmtDateTime(position.created_at)}</td>
        <td>${fmtDateTime(position.closed_at)}</td>
        <td class="text-center">${actionsHtml}</td>
      </tr>
    `;
  }
  
  if (existingTbody) {
    // 更新模式：只更新 tbody 內容，保留表格結構
    let tbodyHtml = "";
    positions.forEach((position, index) => {
      tbodyHtml += generateRowHtml(position, index);
    });
    existingTbody.innerHTML = tbodyHtml;
    return;
  }
  
  // 創建模式：建立完整的表格
  let html = `
    <table class="positions-table">
      <thead>
        <tr>
          <th class="text-center">ID</th>
          <th>Symbol</th>
          <th>Side</th>
          <th class="text-right">Qty</th>
          <th class="text-right">Entry Price</th>
          <th class="text-right">Exit Price</th>
          <th class="text-center">Status</th>
          <th>Exit Reason</th>
          <th class="text-right">PnL</th>
          <th class="text-right">PnL% Threshold</th>
          <th class="text-right">Lock</th>
          <th class="text-right">Base SL%</th>
          <th class="text-center">Stop Mode</th>
          <th class="text-right">Stop Price</th>
          <th class="text-center">Mechanisms</th>
          <th>Created At</th>
          <th>Closed At</th>
          <th class="text-center">Actions</th>
        </tr>
      </thead>
      <tbody>
  `;
  
  positions.forEach((position, index) => {
    html += generateRowHtml(position, index);
  });
  
  html += `
      </tbody>
    </table>
  `;
  
  container.innerHTML = html;
}

// 載入倉位資料
// 取得篩選參數
function getFilterParams() {
  const symbolEl = document.getElementById("filter-symbol");
  const statusEl = document.getElementById("filter-status");
  const startDateEl = document.getElementById("filter-start-date");
  const endDateEl = document.getElementById("filter-end-date");
  const symbol = (symbolEl && symbolEl.value) ? symbolEl.value : "";
  const status = (statusEl && statusEl.value) ? statusEl.value : "";
  const startDate = (startDateEl && startDateEl.value) ? startDateEl.value : "";
  const endDate = (endDateEl && endDateEl.value) ? endDateEl.value : "";
  
  const params = new URLSearchParams();
  if (symbol) params.append("symbol", symbol);
  if (status) params.append("status", status);
  if (startDate) params.append("start_date", startDate);
  if (endDate) params.append("end_date", endDate);
  
  return params.toString();
}

// 清除篩選
function clearFilters() {
  document.getElementById("filter-symbol").value = "";
  document.getElementById("filter-status").value = "";
  document.getElementById("filter-start-date").value = "";
  document.getElementById("filter-end-date").value = "";
  loadPositions();
}

// 更新 Symbol 下拉選單
async function updateSymbolDropdown() {
  try {
    const response = await fetch("/positions");
    if (await handleFetchError(response)) return;
    if (!response.ok) return;
    
    const positions = await response.json();
    const symbols = [...new Set(positions.map(p => p.symbol))].sort();
    
    const select = document.getElementById("filter-symbol");
    if (!select) return;
    
    // 保留 "All" 選項
    const currentValue = select.value;
    select.innerHTML = '<option value="">All</option>';
    
    symbols.forEach(symbol => {
      const option = document.createElement("option");
      option.value = symbol;
      option.textContent = symbol;
      select.appendChild(option);
    });
    
    // 恢復之前選擇的值
    if (currentValue) {
      select.value = currentValue;
    }
  } catch (error) {
    console.error("更新 Symbol 下拉選單失敗:", error);
  }
}

async function loadPositions() {
  const container = document.getElementById("positions-table-container");
  if (!container) return;
  
  // 檢查是否已有表格（避免首次載入時閃爍）
  const existingTable = container.querySelector("table");
  const isFirstLoad = !existingTable;
  
  // 首次載入時顯示載入中，否則顯示小的載入指示器
  if (isFirstLoad) {
    container.innerHTML = '<div class="loading-state"><div class="loading-spinner"></div> 載入中...</div>';
  } else {
    // 保留表格，只添加小的載入指示器
    showTableLoading(container);
  }
  
  try {
    const filterParams = getFilterParams();
    const url = filterParams ? `/positions?${filterParams}` : "/positions";
    const response = await fetch(url);
    if (await handleFetchError(response)) return;
    
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    
    const data = await response.json();
    renderPositionsTable(data);
    hideTableLoading(container);
    
    // 更新 Symbol 下拉選單
    updateSymbolDropdown();
  } catch (error) {
    console.error("載入倉位資料失敗:", error);
    hideTableLoading(container);
    // 如果是首次載入失敗，顯示錯誤；否則保持舊資料
    if (isFirstLoad) {
      container.innerHTML = `<div class="empty-state">載入失敗: ${error.message}</div>`;
    }
  }
}

// 更新機制配置
async function updateMechanismConfig(positionId, mechanismField, enabled) {
  // 取得觸發事件的 checkbox
  const checkbox = document.querySelector(
    `input[data-position-id="${positionId}"][data-mechanism="${mechanismField.replace('_enabled', '')}"]`
  );
  
  if (!checkbox) {
    console.error("找不到對應的 checkbox");
    return;
  }
  
  const originalChecked = checkbox.checked;
  
  // 暫時禁用 checkbox 防止重複點擊
  checkbox.disabled = true;
  
  try {
    const updateData = {};
    updateData[mechanismField] = enabled;
    
    const response = await fetch(`/positions/${positionId}/mechanism-config`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(updateData),
    });
    
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "更新失敗");
    }
    
    const result = await response.json();
    
    // 顯示成功訊息（可選）
    const mechanismName = mechanismField === "bot_stop_loss_enabled" ? "Bot 停損" : "TV 訊號關倉";
    const statusText = enabled ? "啟用" : "停用";
    console.log(`${mechanismName} 已${statusText}`);
    
    // 重新載入資料以更新顯示
    await loadPositions();
  } catch (error) {
    console.error("更新機制配置失敗:", error);
    alert(`更新失敗: ${error.message}`);
    // 恢復 checkbox 狀態
    checkbox.checked = !originalChecked;
  } finally {
    checkbox.disabled = false;
  }
}

// 關閉倉位
async function closePosition(positionId, symbol) {
  if (!confirm(`確定要關閉倉位 #${positionId} (${symbol}) 嗎？`)) {
    return;
  }
  
  const btn = event.target;
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "關倉中...";
  
  try {
    const response = await fetch(`/positions/${positionId}/close`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
    });
    if (await handleFetchError(response)) return;
    
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "關倉失敗");
    }
    
    const result = await response.json();
    alert(`關倉成功！平倉價格: ${result.exit_price || "-"}`);
    
    // 重新載入資料
    await loadPositions();
  } catch (error) {
    console.error("關倉失敗:", error);
    alert(`關倉失敗: ${error.message}`);
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

// 打開停損配置編輯模態框
async function openStopConfigModal(positionId) {
  // 先載入倉位資料以取得當前配置
  try {
    const response = await fetch(`/positions?status=OPEN`);
    if (await handleFetchError(response)) return;
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    
    const positions = await response.json();
    const position = positions.find(p => p.id === positionId);
    if (!position) {
      alert(`找不到倉位 #${positionId}`);
      return;
    }
    
    // 建立模態框 HTML
    const modalHtml = `
      <div id="stop-config-modal" class="modal-overlay">
        <div class="modal-content">
          <div class="modal-header">
            <h3>停損配置 - 倉位 #${positionId} (${position.symbol})</h3>
            <button class="modal-close" onclick="closeStopConfigModal()">&times;</button>
          </div>
          <div class="modal-body">
            <div class="modal-form-group">
              <label>PnL% 門檻 (%) <small>留空使用全局配置</small></label>
              <input type="number" id="stop-config-profit-threshold" step="0.1" min="0" 
                     value="${position.dyn_profit_threshold_pct != null ? position.dyn_profit_threshold_pct : ''}"
                     placeholder="例如: 1.0">
            </div>
            <div class="modal-form-group">
              <label>鎖利比例 (0~1) <small>留空使用全局配置，0 = 僅使用 base stop</small></label>
              <input type="number" id="stop-config-lock-ratio" step="0.01" min="0" max="1"
                     value="${position.trail_callback != null ? position.trail_callback : ''}"
                     placeholder="例如: 0.666">
            </div>
            <div class="modal-form-group">
              <label>基礎停損 (%) <small>留空使用全局配置</small></label>
              <input type="number" id="stop-config-base-sl" step="0.1" min="0"
                     value="${position.base_stop_loss_pct != null ? position.base_stop_loss_pct : ''}"
                     placeholder="例如: 0.5">
            </div>
            <div class="modal-form-group">
              <label class="checkbox-label">
                <input type="checkbox" id="stop-config-clear-overrides">
                <span>使用全局默認值（清除所有覆寫）</span>
              </label>
            </div>
          </div>
          <div class="modal-footer">
            <button onclick="closeStopConfigModal()" class="btn-secondary">取消</button>
            <button onclick="saveStopConfig(${positionId})" class="action-btn">儲存</button>
          </div>
        </div>
      </div>
    `;
    
    // 移除舊的模態框（如果存在）
    const oldModal = document.getElementById("stop-config-modal");
    if (oldModal) oldModal.remove();
    
    // 添加到頁面
    document.body.insertAdjacentHTML("beforeend", modalHtml);
    
    // 點擊背景關閉
    document.getElementById("stop-config-modal").addEventListener("click", function(e) {
      if (e.target.id === "stop-config-modal") {
        closeStopConfigModal();
      }
    });
  } catch (error) {
    console.error("載入倉位配置失敗:", error);
    alert(`載入倉位配置失敗: ${error.message}`);
  }
}

// 關閉停損配置模態框
function closeStopConfigModal() {
  const modal = document.getElementById("stop-config-modal");
  if (modal) modal.remove();
}

// 儲存停損配置
async function saveStopConfig(positionId) {
  const profitInput = document.getElementById("stop-config-profit-threshold");
  const lockInput = document.getElementById("stop-config-lock-ratio");
  const baseSlInput = document.getElementById("stop-config-base-sl");
  const clearCheckbox = document.getElementById("stop-config-clear-overrides");
  
  if (!profitInput || !lockInput || !baseSlInput || !clearCheckbox) {
    alert("找不到輸入欄位");
    return;
  }
  
  const payload = {
    clear_overrides: clearCheckbox.checked,
  };
  
  if (!clearCheckbox.checked) {
    // 只有當不勾選「使用全局默認值」時才設置值
    const profitVal = profitInput.value.trim();
    const lockVal = lockInput.value.trim();
    const baseSlVal = baseSlInput.value.trim();
    
    payload.dyn_profit_threshold_pct = profitVal ? parseFloat(profitVal) : null;
    payload.base_stop_loss_pct = baseSlVal ? parseFloat(baseSlVal) : null;
    payload.trail_callback = lockVal ? parseFloat(lockVal) : null;
  }
  
  try {
    const response = await fetch(`/positions/${positionId}/stop-config`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
    if (await handleFetchError(response)) return;
    
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "儲存失敗");
    }
    
    alert("停損配置已更新！");
    closeStopConfigModal();
    
    // 重新載入資料
    await loadPositions();
  } catch (error) {
    console.error("儲存停損配置失敗:", error);
    alert(`儲存失敗: ${error.message}`);
  }
}

// 設定追蹤停損（保留向後兼容）
async function setTrailingStop(positionId, symbol) {
  const pct = prompt(`請輸入追蹤停損回調百分比（例如：2.0 代表 2%）:\n倉位 #${positionId} (${symbol})`);
  
  if (pct === null || pct === "") {
    return; // 使用者取消
  }
  
  const pctNum = parseFloat(pct);
  if (isNaN(pctNum) || pctNum < 0 || pctNum > 100) {
    alert("追蹤停損百分比必須在 0~100 之間");
    return;
  }
  
  const btn = event.target;
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "設定中...";
  
  try {
    const response = await fetch(`/positions/${positionId}/trailing`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        trailing_callback_percent: pctNum,
      }),
    });
    if (await handleFetchError(response)) return;
    
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "設定失敗");
    }
    
    alert("追蹤停損設定成功！");
    
    // 重新載入資料
    await loadPositions();
  } catch (error) {
    console.error("設定追蹤停損失敗:", error);
    alert(`設定失敗: ${error.message}`);
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

// 全域錯誤處理：如果 fetch 返回 401，重定向到登入頁面（僅在 OAuth 啟用時）
async function handleFetchError(response) {
  if (response.status === 401) {
    // 檢查是否為 Demo 模式（無需登入）
    // 如果是 Demo 模式，不重定向，直接返回 false
    // 後端會在 GOOGLE_OAUTH_ENABLED=false 時自動允許訪問
    return false;  // Demo 模式下不重定向
  }
  return false;
}

// 載入使用者資訊
async function loadUserInfo() {
  try {
    const response = await fetch("/me");
    // Demo 模式下，即使返回 401 也不重定向（後端會自動允許訪問）
    if (response.status === 401) {
      // 在 Demo 模式下，後端會自動返回假的管理員資訊，不會真的返回 401
      // 如果確實返回 401，可能是其他錯誤，不重定向
      console.warn("無法載入使用者資訊，但繼續使用 Demo 模式");
      return;
    }
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    
    // 保存 TradingView Secret 到全局變量
    if (data.tradingview_secret) {
      tradingviewSecret = data.tradingview_secret;
    }
    
    // 更新 user-email
    const userEmailEl = document.getElementById("user-email");
    if (userEmailEl) {
      userEmailEl.textContent = `登入帳號：${data.user_email || "未知"}`;
    }
    
    // 更新 binance-mode-badge
    const badgeEl = document.getElementById("binance-mode-badge");
    if (badgeEl) {
      const mode = data.binance_mode || "demo";
      badgeEl.textContent = mode === "live" ? "LIVE" : "DEMO";
      badgeEl.className = mode === "live" 
        ? "badge badge-danger" 
        : "badge badge-success";
    }
    
    // 如果已經有 signal-key 輸入，更新模板
    const signalKeyEl = document.getElementById("signal-key");
    if (signalKeyEl && signalKeyEl.value) {
      updateSignalAlertTemplate();
    }
  } catch (error) {
    console.error("載入使用者資訊失敗:", error);
    // Demo 模式下，即使失敗也不重定向，繼續使用 Demo 模式
    // 設置默認值
    const userEmailEl = document.getElementById("user-email");
    if (userEmailEl) {
      userEmailEl.textContent = "登入帳號：Demo Mode";
    }
    const badgeEl = document.getElementById("binance-mode-badge");
    if (badgeEl) {
      badgeEl.textContent = "DEMO";
      badgeEl.className = "badge badge-success";
    }
  }
}

// 登出
function handleLogout() {
  fetch("/auth/logout", { method: "POST" })
    .then(() => {
      window.location.href = "/auth/login/google";
    })
    .catch((error) => {
      console.error("登出失敗:", error);
      window.location.href = "/auth/login/google";
    });
}

// ==================== Trailing Settings ====================

// 載入 Trailing 設定
async function loadTrailingSettings() {
  try {
    const resp = await fetch("/settings/trailing");
    if (resp.status === 401 || resp.status === 403) {
      return; // 未登入的情況下先不處理
    }
    if (!resp.ok) {
      console.error("載入 trailing 設定失敗:", resp.status, resp.statusText);
      return;
    }
    const cfg = await resp.json();
    // trailing_enabled and auto_close_enabled are always True, no need to set checkboxes
    
    // 載入 LONG 設定
    if (cfg.long_config) {
      document.getElementById("profit-threshold-long").value = cfg.long_config.profit_threshold_pct || "";
      document.getElementById("lock-ratio-long").value = cfg.long_config.lock_ratio || "";
      document.getElementById("base-sl-long").value = cfg.long_config.base_sl_pct || "";
    }
    
    // 載入 SHORT 設定
    if (cfg.short_config) {
      document.getElementById("profit-threshold-short").value = cfg.short_config.profit_threshold_pct || "";
      document.getElementById("lock-ratio-short").value = cfg.short_config.lock_ratio || "";
      document.getElementById("base-sl-short").value = cfg.short_config.base_sl_pct || "";
    }
  } catch (err) {
    console.error("loadTrailingSettings error:", err);
  }
}

// 儲存 Trailing 設定
async function saveTrailingSettings() {
  // 輔助函數：解析數值，空值或無效值返回 null
  const parseValue = (value) => {
    const trimmed = String(value).trim();
    if (!trimmed || trimmed === "") return null;
    const parsed = parseFloat(trimmed);
    return isNaN(parsed) ? null : parsed;
  };

  // Get input elements safely (compatible with older browsers)
  const profitThresholdLong = document.getElementById("profit-threshold-long");
  const lockRatioLong = document.getElementById("lock-ratio-long");
  const baseSlLong = document.getElementById("base-sl-long");
  const profitThresholdShort = document.getElementById("profit-threshold-short");
  const lockRatioShort = document.getElementById("lock-ratio-short");
  const baseSlShort = document.getElementById("base-sl-short");

  const payload = {
    trailing_enabled: true,  // Always enabled
    long_config: {
      profit_threshold_pct: parseValue(profitThresholdLong ? profitThresholdLong.value : ""),
      lock_ratio: parseValue(lockRatioLong ? lockRatioLong.value : ""),
      base_sl_pct: parseValue(baseSlLong ? baseSlLong.value : ""),
    },
    short_config: {
      profit_threshold_pct: parseValue(profitThresholdShort ? profitThresholdShort.value : ""),
      lock_ratio: parseValue(lockRatioShort ? lockRatioShort.value : ""),
      base_sl_pct: parseValue(baseSlShort ? baseSlShort.value : ""),
    },
    auto_close_enabled: true,  // Always enabled
  };

  const btn = document.getElementById("save-trailing-settings");
  const originalText = (btn && btn.textContent) ? btn.textContent : "Save";
  if (btn) {
    btn.disabled = true;
    btn.textContent = "儲存中...";
  }

  try {
    const resp = await fetch("/settings/trailing", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (await handleFetchError(resp)) {
      if (btn) {
        btn.disabled = false;
        btn.textContent = originalText;
      }
      return;
    }

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${resp.status}: ${resp.statusText}`);
    }

    const result = await resp.json();
    alert("Trailing 設定已更新");
    
    // 重新載入設定以確保顯示最新值
    await loadTrailingSettings();
  } catch (err) {
    console.error("儲存 Trailing 設定失敗:", err);
    alert(`更新失敗：${err.message || String(err)}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  }
}

// ==================== Binance Live Positions ====================

// 只更新 Portfolio Summary 的值（Total PnL, Position Count, Max PnL Reached）
// 不會更新 Portfolio Trailing Stop 的配置輸入欄位
async function loadPortfolioSummaryOnly() {
  try {
    const response = await fetch("/binance/portfolio/summary");
    if (await handleFetchError(response)) return;
    
    if (!response.ok) {
      console.error("載入 Portfolio Summary 失敗:", response.status);
      return;
    }
    
    const data = await response.json();
    
    // 新的 API 結構：{long: {...}, short: {...}}
    if (!data || !data.long || !data.short) {
      console.error("Portfolio Summary 資料格式錯誤:", data);
      return;
    }
    
    // 更新 LONG Portfolio Summary
    const longData = data.long;
    const longPnlEl = document.getElementById("total-pnl-long");
    if (longPnlEl) {
      const pnl = longData.total_unrealized_pnl || 0;
      longPnlEl.textContent = fmtNumber(pnl, 2) + " USDT";
      longPnlEl.style.color = pnl > 0 ? "var(--pnl-positive, #00ff88)" : pnl < 0 ? "var(--pnl-negative, #ff4444)" : "var(--text-primary, #fff)";
    }
    
    const longCountEl = document.getElementById("position-count-long");
    if (longCountEl) {
      longCountEl.textContent = longData.position_count || 0;
    }
    
    const longMaxPnlEl = document.getElementById("max-pnl-reached-long");
    if (longMaxPnlEl && longData.portfolio_trailing) {
      const maxPnl = longData.portfolio_trailing.max_pnl_reached;
      if (maxPnl !== null && maxPnl !== undefined) {
        longMaxPnlEl.textContent = fmtNumber(maxPnl, 2) + " USDT";
        longMaxPnlEl.style.color = "var(--pnl-positive, #00ff88)";
      } else {
        longMaxPnlEl.textContent = "-";
        longMaxPnlEl.style.color = "var(--text-primary, #fff)";
      }
    }
    
    // 更新 SHORT Portfolio Summary
    const shortData = data.short;
    const shortPnlEl = document.getElementById("total-pnl-short");
    if (shortPnlEl) {
      const pnl = shortData.total_unrealized_pnl || 0;
      shortPnlEl.textContent = fmtNumber(pnl, 2) + " USDT";
      shortPnlEl.style.color = pnl > 0 ? "var(--pnl-positive, #00ff88)" : pnl < 0 ? "var(--pnl-negative, #ff4444)" : "var(--text-primary, #fff)";
    }
    
    const shortCountEl = document.getElementById("position-count-short");
    if (shortCountEl) {
      shortCountEl.textContent = shortData.position_count || 0;
    }
    
    const shortMaxPnlEl = document.getElementById("max-pnl-reached-short");
    if (shortMaxPnlEl && shortData.portfolio_trailing) {
      const maxPnl = shortData.portfolio_trailing.max_pnl_reached;
      if (maxPnl !== null && maxPnl !== undefined) {
        shortMaxPnlEl.textContent = fmtNumber(maxPnl, 2) + " USDT";
        shortMaxPnlEl.style.color = "var(--pnl-positive, #00ff88)";
      } else {
        shortMaxPnlEl.textContent = "-";
        shortMaxPnlEl.style.color = "var(--text-primary, #fff)";
      }
    }
    
    // 注意：這裡不更新 Portfolio Trailing Stop 的配置輸入欄位
    // 讓用戶可以安心填寫配置而不會被自動刷新打斷
  } catch (err) {
    console.error("loadPortfolioSummaryOnly error:", err);
  }
}

// 載入 Portfolio Summary（包括配置輸入欄位）
// 用於完整載入，例如：保存配置後或初始頁面載入
async function loadPortfolioSummary() {
  try {
    const response = await fetch("/binance/portfolio/summary");
    if (await handleFetchError(response)) return;
    
    if (!response.ok) {
      console.error("載入 Portfolio Summary 失敗:", response.status);
      // 顯示錯誤但不清空現有值
      const errorText = await response.text().catch(() => "");
      console.error("錯誤詳情:", errorText);
      return;
    }
    
    const data = await response.json();
    
    // 新的 API 結構：{long: {...}, short: {...}}
    if (!data || !data.long || !data.short) {
      console.error("Portfolio Summary 資料格式錯誤:", data);
      return;
    }
    
    // 更新 LONG Portfolio Summary
    const longData = data.long;
    const longPnlEl = document.getElementById("total-pnl-long");
    if (longPnlEl) {
      const pnl = longData.total_unrealized_pnl || 0;
      longPnlEl.textContent = fmtNumber(pnl, 2) + " USDT";
      longPnlEl.style.color = pnl > 0 ? "var(--pnl-positive, #00ff88)" : pnl < 0 ? "var(--pnl-negative, #ff4444)" : "var(--text-primary, #fff)";
    }
    
    const longCountEl = document.getElementById("position-count-long");
    if (longCountEl) {
      longCountEl.textContent = longData.position_count || 0;
    }
    
    const longMaxPnlEl = document.getElementById("max-pnl-reached-long");
    if (longMaxPnlEl && longData.portfolio_trailing) {
      const maxPnl = longData.portfolio_trailing.max_pnl_reached;
      if (maxPnl !== null && maxPnl !== undefined) {
        longMaxPnlEl.textContent = fmtNumber(maxPnl, 2) + " USDT";
        longMaxPnlEl.style.color = "var(--pnl-positive, #00ff88)";
      } else {
        longMaxPnlEl.textContent = "-";
        longMaxPnlEl.style.color = "var(--text-primary, #fff)";
      }
    }
    
    // 更新 LONG Portfolio Trailing 設定
    if (longData.portfolio_trailing) {
      const longEnabledCheckbox = document.getElementById("portfolio-trailing-enabled-long");
      const longTargetPnlInput = document.getElementById("portfolio-target-pnl-long");
      const longLockRatioInput = document.getElementById("portfolio-lock-ratio-long");
      
      if (longEnabledCheckbox) {
        longEnabledCheckbox.checked = longData.portfolio_trailing.enabled || false;
      }
      
      if (longTargetPnlInput) {
        longTargetPnlInput.value = longData.portfolio_trailing.target_pnl ? String(longData.portfolio_trailing.target_pnl) : "";
      }
      
      if (longLockRatioInput) {
        longLockRatioInput.value = longData.portfolio_trailing.lock_ratio ? String(longData.portfolio_trailing.lock_ratio) : "";
      }
    }
    
    // 更新 SHORT Portfolio Summary
    const shortData = data.short;
    const shortPnlEl = document.getElementById("total-pnl-short");
    if (shortPnlEl) {
      const pnl = shortData.total_unrealized_pnl || 0;
      shortPnlEl.textContent = fmtNumber(pnl, 2) + " USDT";
      shortPnlEl.style.color = pnl > 0 ? "var(--pnl-positive, #00ff88)" : pnl < 0 ? "var(--pnl-negative, #ff4444)" : "var(--text-primary, #fff)";
    }
    
    const shortCountEl = document.getElementById("position-count-short");
    if (shortCountEl) {
      shortCountEl.textContent = shortData.position_count || 0;
    }
    
    const shortMaxPnlEl = document.getElementById("max-pnl-reached-short");
    if (shortMaxPnlEl && shortData.portfolio_trailing) {
      const maxPnl = shortData.portfolio_trailing.max_pnl_reached;
      if (maxPnl !== null && maxPnl !== undefined) {
        shortMaxPnlEl.textContent = fmtNumber(maxPnl, 2) + " USDT";
        shortMaxPnlEl.style.color = "var(--pnl-positive, #00ff88)";
      } else {
        shortMaxPnlEl.textContent = "-";
        shortMaxPnlEl.style.color = "var(--text-primary, #fff)";
      }
    }
    
    // 更新 SHORT Portfolio Trailing 設定
    if (shortData.portfolio_trailing) {
      const shortEnabledCheckbox = document.getElementById("portfolio-trailing-enabled-short");
      const shortTargetPnlInput = document.getElementById("portfolio-target-pnl-short");
      const shortLockRatioInput = document.getElementById("portfolio-lock-ratio-short");
      
      if (shortEnabledCheckbox) {
        shortEnabledCheckbox.checked = shortData.portfolio_trailing.enabled || false;
      }
      
      if (shortTargetPnlInput) {
        shortTargetPnlInput.value = shortData.portfolio_trailing.target_pnl ? String(shortData.portfolio_trailing.target_pnl) : "";
      }
      
      if (shortLockRatioInput) {
        shortLockRatioInput.value = shortData.portfolio_trailing.lock_ratio ? String(shortData.portfolio_trailing.lock_ratio) : "";
      }
    }
  } catch (err) {
    console.error("loadPortfolioSummary error:", err);
  }
}

// 載入 Binance Live Positions
async function loadBinancePositions() {
  const container = document.getElementById("binance-table-container");
  if (!container) return;
  
  // 只載入 Portfolio Summary 的值（不更新配置輸入欄位，避免打斷用戶輸入）
  await loadPortfolioSummaryOnly();
  
  // 檢查是否已有表格（避免首次載入時閃爍）
  const existingTable = container.querySelector("table");
  const isFirstLoad = !existingTable;
  
  // 首次載入時顯示載入中，否則顯示小的載入指示器
  if (isFirstLoad) {
    container.innerHTML = '<div class="loading-state"><div class="loading-spinner"></div> 載入 Binance 倉位中...</div>';
  } else {
    // 保留表格，只添加小的載入指示器
    showTableLoading(container);
  }
  
  try {
    const response = await fetch("/binance/open-positions");
    
    if (await handleFetchError(response)) return;
    
    // 如果收到 400，表示 API 未設定，顯示友好提示
    if (response.status === 400) {
      let errorDetail = "Binance API 未設定";
      try {
        const errorData = await response.json();
        errorDetail = errorData.detail || errorDetail;
      } catch (e) {
        // 如果無法解析 JSON，使用預設訊息
      }
      
      container.innerHTML = `
        <div class="empty-state">
          <p style="color: var(--tbl-muted); margin-bottom: 10px;">${errorDetail}</p>
          <p style="color: var(--tbl-muted); font-size: 12px;">
            請在環境變數中設定 BINANCE_API_KEY 和 BINANCE_API_SECRET。
            <br>
            注意：此功能會使用與 Bot 相同的 Binance 連線設定（測試網/正式網由 USE_TESTNET 控制）。
          </p>
        </div>
      `;
      return;
    }
    
    if (!response.ok) {
      let errorMsg = `HTTP ${response.status}: ${response.statusText}`;
      try {
        const errorData = await response.json();
        errorMsg = errorData.detail || errorMsg;
      } catch (e) {
        // 如果無法解析 JSON，使用預設訊息
      }
      throw new Error(errorMsg);
    }
    
    const data = await response.json();
    renderBinancePositionsTable(data);
    hideTableLoading(container);
  } catch (error) {
    console.error("載入 Binance 倉位失敗:", error);
    hideTableLoading(container);
    // 如果是首次載入失敗，顯示錯誤；否則保持舊資料
    if (isFirstLoad) {
      container.innerHTML = `<div class="empty-state">載入 Binance 倉位失敗：${error.message}</div>`;
    }
  }
}

// 渲染 Binance Live Positions 表格
function renderBinancePositionsTable(data) {
  const container = document.getElementById("binance-table-container");
  if (!container) return;
  
  if (!data || data.length === 0) {
    container.innerHTML = '<div class="empty-state">目前沒有 Binance 未平倉部位。</div>';
    return;
  }
  
  // 檢查是否已有表格（更新模式）
  const existingTable = container.querySelector("table");
  const existingTbody = existingTable ? existingTable.querySelector("tbody") : null;
  
  // 生成表格行的函數
  function generateBinanceRowHtml(p, index) {
    const rowAlt = (index % 2) === 1;
    const side = p.position_amt > 0 ? "LONG" : "SHORT";
    const absSize = Math.abs(p.position_amt);
    const sideCls = side === "LONG" ? "side-long" : "side-short";
    
    // PnL 顏色
    const pnlClass =
      p.unrealized_pnl > 0
        ? "pnl-positive"
        : p.unrealized_pnl < 0
        ? "pnl-negative"
        : "pnl-neutral";
    
    // PnL% 顏色（與 PnL 使用相同的顏色邏輯）
    const pnlPctClass =
      p.unrealized_pnl_pct > 0
        ? "pnl-positive"
        : p.unrealized_pnl_pct < 0
        ? "pnl-negative"
        : "pnl-neutral";
    
    // 格式化數字
    const formattedSize = fmtNumber(absSize, 6);
    // 計算 USDT 價值（數量 * 進場價格）和 Margin
    let sizeWithUsdt = formattedSize;
    
    // 計算 USDT 價值（Notional Value = Size * Entry Price）
    let usdtValueStr = "";
    let marginStr = "";
    if (p.entry_price && p.entry_price > 0) {
      const usdtValue = absSize * p.entry_price;
      usdtValueStr = `(${fmtNumber(usdtValue, 2)} USDT)`;
      
      // 計算 Margin = (Size * Entry Price) / Leverage = Notional / Leverage
      if (p.leverage && p.leverage > 0) {
        const margin = usdtValue / p.leverage;
        const formattedMargin = fmtNumber(margin, 2);
        marginStr = ` / Margin: ${formattedMargin} USDT`;
      }
    }
    
    // 組合顯示
    if (usdtValueStr) {
      sizeWithUsdt = `${formattedSize} ${usdtValueStr}${marginStr}`;
    } else if (marginStr) {
      sizeWithUsdt = `${formattedSize}${marginStr}`;
    }
    const formattedEntry = p.entry_price ? fmtNumber(p.entry_price, 4) : "-";
    const formattedMark = p.mark_price ? fmtNumber(p.mark_price, 4) : "-";
    const formattedPnL = p.unrealized_pnl !== null && p.unrealized_pnl !== undefined 
      ? fmtNumber(p.unrealized_pnl, 4) 
      : "0";
    
    // 格式化 PnL%
    const formattedPnLPct = p.unrealized_pnl_pct !== null && p.unrealized_pnl_pct !== undefined
      ? fmtNumber(p.unrealized_pnl_pct, 2)
      : "0.00";
    
    // Stop Mode 顯示
    let stopModeBadge = "";
    if (p.stop_mode === "dynamic" || p.stop_mode === "dynamic_trailing") {
      stopModeBadge = '<span class="tag-dyn">Dynamic</span>';
    } else if (p.stop_mode === "base" || p.stop_mode === "base_stop") {
      stopModeBadge = '<span class="tag-base">Base</span>';
    } else {
      stopModeBadge = '<span style="color: var(--tbl-muted);">—</span>';
    }
    
    // Stop Price 顯示（根據 Stop Mode 只顯示對應的價格，顏色與 Stop Mode 一致）
    let stopPriceDisplay = "—";
    if ((p.stop_mode === "dynamic" || p.stop_mode === "dynamic_trailing") && p.dynamic_stop_price !== null && p.dynamic_stop_price !== undefined && p.dynamic_stop_price > 0) {
      const dynPrice = fmtNumber(p.dynamic_stop_price, 4);
      stopPriceDisplay = `<span class="tag-dyn">${dynPrice}</span>`;
    } else if ((p.stop_mode === "base" || p.stop_mode === "base_stop") && p.base_stop_price !== null && p.base_stop_price !== undefined && p.base_stop_price > 0) {
      const basePrice = fmtNumber(p.base_stop_price, 4);
      stopPriceDisplay = `<span class="tag-base">${basePrice}</span>`;
    }
    
    // 格式化時間
    let updatedAt = "-";
    if (p.update_time && p.update_time > 0) {
      try {
        // 確保時間正確處理（如果是字符串，添加 Z 如果沒有時區資訊）
        const updateTimeStr = typeof p.update_time === 'string' 
          ? (p.update_time.endsWith('Z') || p.update_time.includes('+') || p.update_time.includes('-') 
            ? p.update_time 
            : p.update_time + (p.update_time.includes('T') ? 'Z' : ''))
          : p.update_time;
        updatedAt = new Date(updateTimeStr).toLocaleString("zh-TW", {
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: false,
          timeZone: "Asia/Taipei", // 明確指定台灣時區
        });
      } catch (e) {
        updatedAt = "-";
      }
    }
    
    // Profit Threshold 顯示（根據來源顯示不同顏色）
    let profitThresholdHtml = "—";
    let profitThresholdClass = "";
    if (p.profit_threshold_value !== null && p.profit_threshold_value !== undefined) {
      profitThresholdHtml = p.profit_threshold_value.toFixed(2) + "%";
      // 根據來源設定顏色：override=黃色, global=藍色, default=灰色
      if (p.profit_threshold_source === "override") {
        profitThresholdClass = "stop-value-override";  // 黃色
      } else if (p.profit_threshold_source === "global") {
        profitThresholdClass = "stop-value-global";  // 藍色
      } else {
        profitThresholdClass = "stop-value-default";  // 灰色
      }
    }
    profitThresholdHtml = profitThresholdHtml !== "—" 
      ? `<span class="${profitThresholdClass}">${profitThresholdHtml}</span>`
      : profitThresholdHtml;
    
    // Trail Callback (Lock Ratio) 顯示（根據來源顯示不同顏色）
    let trailCallbackHtml = "—";
    let trailCallbackClass = "";
    if (p.lock_ratio_value !== null && p.lock_ratio_value !== undefined) {
      if (p.lock_ratio_value === 0) {
        trailCallbackHtml = "Base Only";
      } else {
        trailCallbackHtml = fmtPercent(p.lock_ratio_value * 100);
      }
      // 根據來源設定顏色：override=黃色, global=藍色, default=灰色
      if (p.lock_ratio_source === "override") {
        trailCallbackClass = "stop-value-override";  // 黃色
      } else if (p.lock_ratio_source === "global") {
        trailCallbackClass = "stop-value-global";  // 藍色
      } else {
        trailCallbackClass = "stop-value-default";  // 灰色
      }
      trailCallbackHtml = `<span class="${trailCallbackClass}">${trailCallbackHtml}</span>`;
    }
    
    // Base SL% 顯示（根據來源顯示不同顏色）
    let baseSlHtml = "—";
    let baseSlClass = "";
    if (p.base_sl_value !== null && p.base_sl_value !== undefined) {
      baseSlHtml = p.base_sl_value.toFixed(2) + "%";
      // 根據來源設定顏色：override=黃色, global=藍色, default=灰色
      if (p.base_sl_source === "override") {
        baseSlClass = "stop-value-override";  // 黃色
      } else if (p.base_sl_source === "global") {
        baseSlClass = "stop-value-global";  // 藍色
      } else {
        baseSlClass = "stop-value-default";  // 灰色
      }
      baseSlHtml = `<span class="${baseSlClass}">${baseSlHtml}</span>`;
    }
    
    // Actions 按鈕
    const actionsHtml = `
      <div class="action-buttons">
        <button class="action-btn action-btn-close" 
                onclick="closeBinancePosition('${p.symbol}', '${side}')">
          Close
        </button>
        <button class="action-btn" 
                onclick="openBinanceStopConfigModal('${p.symbol}', '${side}')">
          停損設定
        </button>
      </div>
    `;
    
    return `
      <tr class="${rowAlt ? "row-alt" : ""}">
        <td><strong><a href="#" data-symbol="${p.symbol || ""}" class="symbol-link" title="點擊打開 TradingView 圖表">${p.symbol || "-"}</a></strong></td>
        <td class="text-center"><span class="${sideCls}">${side}</span></td>
        <td class="text-right">${sizeWithUsdt}</td>
        <td class="text-center">${p.leverage || 0}x</td>
        <td class="text-right">${formattedEntry}</td>
        <td class="text-right">${formattedMark}</td>
        <td class="text-right"><span class="${pnlClass}">${formattedPnL}</span></td>
        <td class="text-right"><span class="${pnlPctClass}">${formattedPnLPct}%</span></td>
        <td class="text-right">${profitThresholdHtml}</td>
        <td class="text-right">${trailCallbackHtml}</td>
        <td class="text-right">${baseSlHtml}</td>
        <td class="text-center">${stopModeBadge}</td>
        <td class="text-right" style="font-size: 12px;">${stopPriceDisplay}</td>
        <td class="text-center">${p.margin_type || "-"}</td>
        <td>${updatedAt}</td>
        <td class="text-center">${actionsHtml}</td>
      </tr>
    `;
  }
  
  if (existingTbody) {
    // 更新模式：只更新 tbody 內容，保留表格結構
    let tbodyHtml = "";
    data.forEach((p, index) => {
      tbodyHtml += generateBinanceRowHtml(p, index);
    });
    existingTbody.innerHTML = tbodyHtml;
    return;
  }
  
  // 創建模式：建立完整的表格
  let html = `
    <table class="positions-table">
      <thead>
        <tr>
          <th>Symbol</th>
          <th class="text-center">Side</th>
          <th class="text-right">Size</th>
          <th class="text-center">Leverage</th>
          <th class="text-right">Entry Price</th>
          <th class="text-right">Mark Price</th>
          <th class="text-right">Unrealized PnL</th>
          <th class="text-right">PnL%</th>
          <th class="text-right">PnL% Threshold</th>
          <th class="text-right">Lock</th>
          <th class="text-right">Base SL%</th>
          <th class="text-center">Stop Mode</th>
          <th class="text-right">Stop Price</th>
          <th class="text-center">Margin Type</th>
          <th>Updated At</th>
          <th class="text-center">Actions</th>
        </tr>
      </thead>
      <tbody>
  `;
  
  data.forEach((p, index) => {
    html += generateBinanceRowHtml(p, index);
  });
  
  html += `
      </tbody>
    </table>
  `;
  
  container.innerHTML = html;
}

// 打開 Binance Live Position 停損配置編輯模態框
async function openBinanceStopConfigModal(symbol, side) {
  // 先載入 Binance Live Positions 資料以取得當前配置
  try {
    const response = await fetch(`/binance/open-positions`);
    if (await handleFetchError(response)) return;
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    
    const positions = await response.json();
    const position = positions.find(p => 
      p.symbol === symbol && 
      ((p.position_amt > 0 && side === "LONG") || (p.position_amt < 0 && side === "SHORT"))
    );
    if (!position) {
      alert(`找不到倉位 ${symbol} (${side})`);
      return;
    }
    
    // 建立模態框 HTML
    const modalHtml = `
      <div id="binance-stop-config-modal" class="modal-overlay">
        <div class="modal-content">
          <div class="modal-header">
            <h3>停損配置 - ${symbol} (${side})</h3>
            <button class="modal-close" onclick="closeBinanceStopConfigModal()">&times;</button>
          </div>
          <div class="modal-body">
            <div class="modal-form-group">
              <label>PnL% 門檻 (%) <small>留空使用全局配置</small></label>
              <input type="number" id="binance-stop-config-profit-threshold" step="0.1" min="0" 
                     value="${position.dyn_profit_threshold_pct != null ? position.dyn_profit_threshold_pct : ''}"
                     placeholder="例如: 1.0">
            </div>
            <div class="modal-form-group">
              <label>鎖利比例 (0~1) <small>留空使用全局配置，0 = 僅使用 base stop</small></label>
              <input type="number" id="binance-stop-config-lock-ratio" step="0.01" min="0" max="1"
                     value="${position.trail_callback != null ? position.trail_callback : ''}"
                     placeholder="例如: 0.666">
            </div>
            <div class="modal-form-group">
              <label>基礎停損 (%) <small>留空使用全局配置</small></label>
              <input type="number" id="binance-stop-config-base-sl" step="0.1" min="0"
                     value="${position.base_stop_loss_pct != null ? position.base_stop_loss_pct : ''}"
                     placeholder="例如: 0.5">
            </div>
            <div class="modal-form-group">
              <label class="checkbox-label">
                <input type="checkbox" id="binance-stop-config-clear-overrides">
                <span>使用全局默認值（清除所有覆寫）</span>
              </label>
            </div>
          </div>
          <div class="modal-footer">
            <button onclick="closeBinanceStopConfigModal()" class="btn-secondary">取消</button>
            <button onclick="saveBinanceStopConfig('${symbol}', '${side}')" class="action-btn">儲存</button>
          </div>
        </div>
      </div>
    `;
    
    // 移除舊的模態框（如果存在）
    const oldModal = document.getElementById("binance-stop-config-modal");
    if (oldModal) oldModal.remove();
    
    // 添加到頁面
    document.body.insertAdjacentHTML("beforeend", modalHtml);
    
    // 點擊背景關閉
    document.getElementById("binance-stop-config-modal").addEventListener("click", function(e) {
      if (e.target.id === "binance-stop-config-modal") {
        closeBinanceStopConfigModal();
      }
    });
  } catch (error) {
    console.error("載入 Binance 倉位配置失敗:", error);
    alert(`載入倉位配置失敗: ${error.message}`);
  }
}

// 關閉 Binance Live Position 停損配置模態框
function closeBinanceStopConfigModal() {
  const modal = document.getElementById("binance-stop-config-modal");
  if (modal) modal.remove();
}

// 儲存 Binance Live Position 停損配置
async function saveBinanceStopConfig(symbol, side) {
  const profitInput = document.getElementById("binance-stop-config-profit-threshold");
  const lockInput = document.getElementById("binance-stop-config-lock-ratio");
  const baseSlInput = document.getElementById("binance-stop-config-base-sl");
  const clearCheckbox = document.getElementById("binance-stop-config-clear-overrides");
  
  if (!profitInput || !lockInput || !baseSlInput || !clearCheckbox) {
    alert("找不到輸入欄位");
    return;
  }
  
  const payload = {
    symbol: symbol,
    position_side: side,
    clear_overrides: clearCheckbox.checked,
  };
  
  if (!clearCheckbox.checked) {
    // 只有當不勾選「使用全局默認值」時才設置值
    const profitVal = profitInput.value.trim();
    const lockVal = lockInput.value.trim();
    const baseSlVal = baseSlInput.value.trim();
    
    payload.dyn_profit_threshold_pct = profitVal ? parseFloat(profitVal) : null;
    payload.base_stop_loss_pct = baseSlVal ? parseFloat(baseSlVal) : null;
    payload.trail_callback = lockVal ? parseFloat(lockVal) : null;
  }
  
  try {
    const response = await fetch(`/binance/positions/stop-config`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
    if (await handleFetchError(response)) return;
    
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "儲存失敗");
    }
    
    alert("停損配置已更新！");
    closeBinanceStopConfigModal();
    
    // 重新載入資料
    await loadBinancePositions();
  } catch (error) {
    console.error("儲存停損配置失敗:", error);
    alert(`儲存失敗: ${error.message}`);
  }
}

// 確保函數在全局作用域中可用（用於 onclick 事件）
window.openBinanceStopConfigModal = openBinanceStopConfigModal;
window.closeBinanceStopConfigModal = closeBinanceStopConfigModal;
window.saveBinanceStopConfig = saveBinanceStopConfig;

// 關閉所有 Binance Live Positions
async function closeAllBinancePositions() {
  if (!confirm("確定要關閉所有 Binance Live Positions 嗎？此操作無法撤銷。")) {
    return;
  }

  const btn = document.getElementById("close-all-positions-btn");
  if (btn) {
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "關倉中...";
    
    try {
      const resp = await fetch("/binance/positions/close-all", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
      });

      if (await handleFetchError(resp)) return;

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }

      const result = await resp.json();
      alert(`成功關閉 ${result.closed_count} 個倉位${result.errors && result.errors.length > 0 ? `\n錯誤: ${result.errors.join(", ")}` : ""}`);

      // 重新載入 Binance positions 和 summary
      await loadBinancePositions();
      
      // 也重新載入 Bot Positions（因為可能更新了現有倉位）
      await loadPositions();
    } catch (error) {
      console.error("關閉所有 Binance Positions 失敗:", error);
      alert(`關閉失敗：${error.message}`);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = originalText;
      }
    }
  }
}

// 重置 Max PnL Reached (LONG or SHORT)
async function resetMaxPnlReached(side) {
  const sideName = side === "long" ? "LONG" : "SHORT";
  if (!confirm(`確定要重置 ${sideName} 的 Max PnL Reached 嗎？此操作會清除已記錄的最大 PnL 值。`)) {
    return;
  }

  const btn = document.getElementById(`reset-max-pnl-btn-${side}`);
  const originalText = btn ? btn.textContent : "Reset";
  if (btn) {
    btn.disabled = true;
    btn.textContent = "重置中...";
  }

  try {
    const resp = await fetch(`/binance/portfolio/trailing/reset-max-pnl?side=${side}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
    });

    if (await handleFetchError(resp)) {
      if (btn) {
        btn.disabled = false;
        btn.textContent = originalText;
      }
      return;
    }

    if (!resp.ok) {
      let errorMessage = `HTTP ${resp.status}`;
      try {
        const errorData = await resp.json();
        errorMessage = errorData.detail || errorData.message || errorMessage;
      } catch (e) {
        const text = await resp.text().catch(() => "");
        if (text) {
          errorMessage = text;
        }
      }
      throw new Error(errorMessage);
    }

    const result = await resp.json();
    alert(result.message || `Max PnL Reached (${sideName}) 已重置`);

    // 重新載入 Portfolio Summary 以更新顯示
    await loadPortfolioSummaryOnly();
  } catch (error) {
    console.error(`重置 Max PnL Reached (${sideName}) 失敗:`, error);
    alert(`重置失敗：${error.message || String(error)}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  }
}

// 儲存 Portfolio Trailing 設定 (LONG or SHORT)
async function savePortfolioTrailingConfig(side) {
  const sideName = side === "long" ? "LONG" : "SHORT";
  const enabledCheckbox = document.getElementById(`portfolio-trailing-enabled-${side}`);
  const targetPnlInput = document.getElementById(`portfolio-target-pnl-${side}`);
  const lockRatioInput = document.getElementById(`portfolio-lock-ratio-${side}`);
  
  if (!enabledCheckbox || !targetPnlInput || !lockRatioInput) {
    alert(`找不到 ${sideName} 設定欄位`);
    return;
  }
  
  const payload = {
    enabled: enabledCheckbox.checked,
  };
  
  const targetPnlVal = targetPnlInput.value.trim();
  if (targetPnlVal) {
    payload.target_pnl = parseFloat(targetPnlVal);
  }
  
  const lockRatioVal = lockRatioInput.value.trim();
  if (lockRatioVal) {
    payload.lock_ratio = parseFloat(lockRatioVal);
  }

  const btn = document.getElementById(`save-portfolio-trailing-btn-${side}`);
  const originalText = btn ? btn.textContent : "Save Config";
  if (btn) {
    btn.disabled = true;
    btn.textContent = "儲存中...";
  }
  
  try {
    const resp = await fetch(`/binance/portfolio/trailing?side=${side}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
    
    if (await handleFetchError(resp)) {
      if (btn) {
        btn.disabled = false;
        btn.textContent = originalText;
      }
      return;
    }
    
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      const errorMsg = err.detail || err.message || `HTTP ${resp.status}`;
      throw new Error(errorMsg);
    }
    
    alert(`Portfolio Trailing 設定 (${sideName}) 已更新！`);
    
    // 重新載入 summary
    await loadPortfolioSummary();
  } catch (error) {
    console.error(`儲存 Portfolio Trailing 設定 (${sideName}) 失敗:`, error);
    const errorMsg = error.message || String(error);
    alert(`儲存失敗: ${errorMsg}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  }
}

// 關閉 Binance Live Position
async function closeBinancePosition(symbol, side) {
  if (!confirm(`確定要平倉 ${symbol} (${side}) 嗎？`)) {
    return;
  }

  const btn = event.target;
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "關倉中...";

  try {
    const resp = await fetch("/binance/positions/close", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ symbol, position_side: side }),
    });

    if (await handleFetchError(resp)) return;

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }

    const result = await resp.json();
    alert(`平倉成功！訂單ID: ${result.order_id}, 平均價格: ${result.avg_price || "-"}`);

    // 重新載入 Binance positions
    await loadBinancePositions();
    
    // 也重新載入 Bot Positions（因為可能更新了現有倉位）
    await loadPositions();
  } catch (error) {
    console.error("關閉 Binance Position 失敗:", error);
    alert(`平倉失敗：${error.message}`);
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

// ==================== Signals ====================

// 載入 Signals
async function loadSignals() {
  const container = document.getElementById("signals-table-container");
  if (!container) {
    console.error("signals-table-container not found");
    return;
  }
  
  const existingTable = container.querySelector("table");
  const isFirstLoad = !existingTable;
  
  if (isFirstLoad) {
    container.innerHTML = '<div class="loading-state"><div class="loading-spinner"></div> 載入中...</div>';
  } else {
    showTableLoading(container);
  }
  
  try {
    const response = await fetch("/signals");
    
    if (await handleFetchError(response)) return;
    
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    
    const data = await response.json();
    renderSignalsTable(data);
    hideTableLoading(container);
  } catch (error) {
    console.error("載入 Signals 失敗:", error);
    hideTableLoading(container);
    if (isFirstLoad) {
      container.innerHTML = `<div class="empty-state">載入失敗: ${error.message}</div>`;
    }
  }
}

// 清除所有 Signal Logs
async function clearAllSignalLogs() {
  if (!confirm("確定要清除所有 Signal Logs 嗎？此操作無法復原。")) {
    return;
  }
  
  try {
    const response = await fetch("/admin/signal-logs/clear", {
      method: "DELETE",
    });
    
    if (await handleFetchError(response)) return;
    
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${response.status}`);
    }
    
    const result = await response.json();
    alert(`成功清除 ${result.deleted} 筆 Signal Logs`);
    
    // 強制重新載入 Signal Logs 表格
    const container = document.getElementById("signals-table-container");
    if (container) {
      // 清空容器並顯示載入狀態
      container.innerHTML = '<div class="loading-state"><div class="loading-spinner"></div> 載入中...</div>';
      
      // 重新載入列表
      await loadSignals();
    } else {
      console.error("signals-table-container not found");
    }
  } catch (error) {
    console.error("清除 Signal Logs 失敗:", error);
    alert(`清除失敗: ${error.message}`);
  }
}

// 排序狀態
let signalsSortState = {
  column: null,
  direction: 'asc' // 'asc' or 'desc'
};

// 排序 Signals 資料
function sortSignalsData(data, column, direction) {
  if (!data || data.length === 0) return data;
  
  const sorted = [...data].sort((a, b) => {
    let aVal, bVal;
    
    switch(column) {
      case 'id':
        aVal = a.id || 0;
        bVal = b.id || 0;
        return direction === 'asc' ? aVal - bVal : bVal - aVal;
      case 'bot_key':
        aVal = (a.bot_key || '').toLowerCase();
        bVal = (b.bot_key || '').toLowerCase();
        return direction === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
      case 'symbol':
        aVal = (a.symbol || '').toLowerCase();
        bVal = (b.symbol || '').toLowerCase();
        return direction === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
      case 'side':
        aVal = (a.side || '').toLowerCase();
        bVal = (b.side || '').toLowerCase();
        return direction === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
      case 'qty':
        aVal = parseFloat(a.qty || 0);
        bVal = parseFloat(b.qty || 0);
        return direction === 'asc' ? aVal - bVal : bVal - aVal;
      case 'position_size':
        aVal = parseFloat(a.position_size || 0);
        bVal = parseFloat(b.position_size || 0);
        return direction === 'asc' ? aVal - bVal : bVal - aVal;
      case 'received_at':
        aVal = a.received_at ? new Date(a.received_at).getTime() : 0;
        bVal = b.received_at ? new Date(b.received_at).getTime() : 0;
        return direction === 'asc' ? aVal - bVal : bVal - aVal;
      case 'processed':
        aVal = a.processed ? 1 : 0;
        bVal = b.processed ? 1 : 0;
        return direction === 'asc' ? aVal - bVal : bVal - aVal;
      default:
        return 0;
    }
  });
  
  return sorted;
}

// 渲染 Signals 表格
function renderSignalsTable(data) {
  // 更新計數
  const countSpan = document.getElementById("signals-count");
  if (countSpan) {
    countSpan.textContent = data && data.length > 0 ? `(${data.length})` : '';
  }
  
  const container = document.getElementById("signals-table-container");
  if (!container) {
    console.error("signals-table-container not found");
    return;
  }
  
  if (!data || data.length === 0) {
    container.innerHTML = '<div class="empty-state">目前沒有 Signal 記錄</div>';
    return;
  }
  
  // 應用排序
  const sortedData = signalsSortState.column 
    ? sortSignalsData(data, signalsSortState.column, signalsSortState.direction)
    : data;
  
  const existingTable = container.querySelector("table");
  const existingTbody = existingTable ? existingTable.querySelector("tbody") : null;
  
  function generateSignalRowHtml(signal, index) {
    const rowAlt = (index % 2) === 1;
    const processedClass = signal.processed ? "status-open" : "status-closed";
    const processedText = signal.processed ? "✓" : "✗";
    
    // 格式化 position_size
    let positionSizeDisplay = "-";
    let positionSizeClass = "";
    if (signal.position_size !== null && signal.position_size !== undefined) {
      const posSize = parseFloat(signal.position_size);
      if (!isNaN(posSize)) {
        if (posSize > 0) {
          positionSizeDisplay = `+${fmtNumber(posSize, 6)}`;
          positionSizeClass = "pnl-positive";
        } else if (posSize < 0) {
          positionSizeDisplay = fmtNumber(posSize, 6);
          positionSizeClass = "pnl-negative";
        } else {
          positionSizeDisplay = "0";
        }
      }
    }
    
    return `
      <tr class="${rowAlt ? "row-alt" : ""}">
        <td><strong>${signal.id || "-"}</strong></td>
        <td>${signal.bot_key || "-"}</td>
        <td><a href="#" onclick="openTradingViewChart('${signal.symbol || ""}'); return false;" class="symbol-link" title="點擊打開 TradingView 圖表">${signal.symbol || "-"}</a></td>
        <td class="text-center"><span class="side-${signal.side === 'BUY' ? 'long' : 'short'}">${signal.side || "-"}</span></td>
        <td class="text-right">${fmtNumber(signal.qty || 0, 6)}</td>
        <td class="text-right"><span class="${positionSizeClass}">${positionSizeDisplay}</span></td>
        <td>${fmtDateTime(signal.received_at)}</td>
        <td class="text-center"><span class="${processedClass}">${processedText}</span></td>
        <td>${signal.process_result || "-"}</td>
        <td class="text-center">
          <button class="action-btn signal-view-btn" data-signal-id="${signal.id}">View</button>
        </td>
      </tr>
    `;
  }
  
  // 生成排序指示器
  function getSortIndicator(col) {
    if (signalsSortState.column === col) {
      return signalsSortState.direction === 'asc' ? ' ↑' : ' ↓';
    }
    return '';
  }
  
  // 生成可排序的 th
  function generateSortableTh(col, label, className = '') {
    const isActive = signalsSortState.column === col;
    const sortClass = isActive ? 'sort-active' : '';
    const indicator = getSortIndicator(col);
    return `<th class="${className} ${sortClass} sortable" data-column="${col}" style="cursor: pointer; user-select: none;">
      ${label}${indicator}
    </th>`;
  }
  
  if (existingTbody) {
    let tbodyHtml = "";
    sortedData.forEach((signal, index) => {
      tbodyHtml += generateSignalRowHtml(signal, index);
    });
    existingTbody.innerHTML = tbodyHtml;
    
    // 綁定 View 按鈕事件（更新現有表格時）
    document.querySelectorAll(".signal-view-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const signalId = btn.getAttribute("data-signal-id");
        openSignalDetailModal(signalId);
      });
    });
    
    // 重新綁定排序事件
    bindSortEvents();
    return;
  }
  
  // 建立完整表格
  let html = `
    <table class="positions-table">
      <thead>
        <tr>
          ${generateSortableTh('id', 'ID', 'text-center')}
          ${generateSortableTh('bot_key', 'Bot Key', '')}
          ${generateSortableTh('symbol', 'Symbol', '')}
          ${generateSortableTh('side', 'Side', 'text-center')}
          ${generateSortableTh('qty', 'Qty', 'text-right')}
          ${generateSortableTh('position_size', 'Position Size', 'text-right')}
          ${generateSortableTh('received_at', 'Received At', '')}
          ${generateSortableTh('processed', 'Processed', 'text-center')}
          <th>Result</th>
          <th class="text-center">Actions</th>
        </tr>
      </thead>
      <tbody>
  `;
  
  sortedData.forEach((signal, index) => {
    html += generateSignalRowHtml(signal, index);
  });
  
  html += `
      </tbody>
    </table>
  `;
  
  container.innerHTML = html;
  
  // 綁定 View 按鈕事件
  document.querySelectorAll(".signal-view-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const signalId = btn.getAttribute("data-signal-id");
      openSignalDetailModal(signalId);
    });
  });
  
  // 綁定排序事件
  bindSortEvents();
}

// 綁定排序事件
function bindSortEvents() {
  document.querySelectorAll("#signals-table-container .sortable").forEach((th) => {
    th.addEventListener("click", () => {
      const column = th.getAttribute("data-column");
      if (!column) return;
      
      // 切換排序方向
      if (signalsSortState.column === column) {
        signalsSortState.direction = signalsSortState.direction === 'asc' ? 'desc' : 'asc';
      } else {
        signalsSortState.column = column;
        signalsSortState.direction = 'asc';
      }
      
      // 重新載入並渲染（會自動應用排序）
      loadSignals();
    });
  });
}

// 切換 Signal Logs 表格顯示/隱藏
function toggleSignalsTable() {
  const container = document.getElementById("signals-table-container");
  const toggleBtn = document.getElementById("toggle-signals-btn");
  
  if (!container || !toggleBtn) return;
  
  const isVisible = container.style.display !== 'none';
  container.style.display = isVisible ? 'none' : 'block';
  toggleBtn.textContent = isVisible ? '+' : '−';
  toggleBtn.title = isVisible ? '展開' : '收合';
}

// ==================== Bots ====================

// 載入 Bots
async function loadBots() {
  const container = document.getElementById("bots-table-container");
  if (!container) return;
  
  const existingTable = container.querySelector("table");
  const isFirstLoad = !existingTable;
  
  if (isFirstLoad) {
    container.innerHTML = '<div class="loading-state"><div class="loading-spinner"></div> 載入中...</div>';
  } else {
    showTableLoading(container);
  }
  
  try {
    const response = await fetch("/bots");
    
    if (await handleFetchError(response)) return;
    
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    
    const data = await response.json();
    renderBotsTable(data);
    hideTableLoading(container);
  } catch (error) {
    console.error("載入 Bots 失敗:", error);
    hideTableLoading(container);
    if (isFirstLoad) {
      container.innerHTML = `<div class="empty-state">載入失敗: ${error.message}</div>`;
    }
  }
}

// 排序狀態（Bots）
let botsSortState = {
  column: null,
  direction: 'asc' // 'asc' or 'desc'
};

// 排序 Bots 資料
function sortBotsData(data, column, direction) {
  if (!data || data.length === 0) return data;
  
  const sorted = [...data].sort((a, b) => {
    let aVal, bVal;
    
    switch(column) {
      case 'id':
        aVal = a.id || 0;
        bVal = b.id || 0;
        return direction === 'asc' ? aVal - bVal : bVal - aVal;
      case 'name':
        aVal = (a.name || '').toLowerCase();
        bVal = (b.name || '').toLowerCase();
        return direction === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
      case 'bot_key':
        aVal = (a.bot_key || '').toLowerCase();
        bVal = (b.bot_key || '').toLowerCase();
        return direction === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
      case 'status':
        aVal = a.enabled ? 1 : 0;
        bVal = b.enabled ? 1 : 0;
        return direction === 'asc' ? aVal - bVal : bVal - aVal;
      case 'symbol':
        aVal = (a.symbol || '').toLowerCase();
        bVal = (b.symbol || '').toLowerCase();
        return direction === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
      case 'side':
        const aSide = a.use_signal_side ? "signal" : (a.fixed_side || "").toLowerCase();
        const bSide = b.use_signal_side ? "signal" : (b.fixed_side || "").toLowerCase();
        return direction === 'asc' ? aSide.localeCompare(bSide) : bSide.localeCompare(aSide);
      case 'qty':
        aVal = parseFloat(a.qty || 0);
        bVal = parseFloat(b.qty || 0);
        return direction === 'asc' ? aVal - bVal : bVal - aVal;
      case 'leverage':
        aVal = parseInt(a.leverage || 0);
        bVal = parseInt(b.leverage || 0);
        return direction === 'asc' ? aVal - bVal : bVal - aVal;
      case 'max_invest_usdt':
        aVal = a.max_invest_usdt !== null && a.max_invest_usdt !== undefined ? parseFloat(a.max_invest_usdt) : -1;
        bVal = b.max_invest_usdt !== null && b.max_invest_usdt !== undefined ? parseFloat(b.max_invest_usdt) : -1;
        return direction === 'asc' ? aVal - bVal : bVal - aVal;
      case 'signal':
        const aSignal = (a.signal && a.signal.name) ? a.signal.name.toLowerCase() : "";
        const bSignal = (b.signal && b.signal.name) ? b.signal.name.toLowerCase() : "";
        return direction === 'asc' ? aSignal.localeCompare(bSignal) : bSignal.localeCompare(aSignal);
      case 'trailing':
        aVal = a.trailing_callback_percent !== null && a.trailing_callback_percent !== undefined 
          ? parseFloat(a.trailing_callback_percent) 
          : -1;
        bVal = b.trailing_callback_percent !== null && b.trailing_callback_percent !== undefined 
          ? parseFloat(b.trailing_callback_percent) 
          : -1;
        return direction === 'asc' ? aVal - bVal : bVal - aVal;
      default:
        return 0;
    }
  });
  
  return sorted;
}

// 渲染 Bots 表格
function renderBotsTable(data) {
  const container = document.getElementById("bots-table-container");
  if (!container) return;
  
  if (!data || data.length === 0) {
    container.innerHTML = '<div class="empty-state">目前沒有 Bot 設定</div>';
    return;
  }
  
  // 應用排序
  const sortedData = botsSortState.column 
    ? sortBotsData(data, botsSortState.column, botsSortState.direction)
    : data;
  
  const existingTable = container.querySelector("table");
  const existingTbody = existingTable ? existingTable.querySelector("tbody") : null;
  
  function generateBotRowHtml(bot, index) {
    const rowAlt = (index % 2) === 1;
    const enabledClass = bot.enabled ? "status-open" : "status-closed";
    const enabledText = bot.enabled ? "啟用" : "停用";
    
    const sideDisplay = bot.use_signal_side ? "Signal" : (bot.fixed_side || "-");
    const trailingDisplay = bot.trailing_callback_percent !== null && bot.trailing_callback_percent !== undefined
      ? `${bot.trailing_callback_percent}%`
      : "-";
    const signalDisplay = bot.signal ? bot.signal.name : "—";
    const maxInvestDisplay = bot.max_invest_usdt !== null && bot.max_invest_usdt !== undefined
      ? `${fmtNumber(bot.max_invest_usdt, 2)} USDT`
      : "-";
    
    const actionsHtml = `
      <div class="action-buttons">
        <button class="action-btn" onclick="openBotFormFromEdit(${bot.id})">Edit</button>
        ${bot.enabled 
          ? `<button class="action-btn action-btn-disable" onclick="toggleBot(${bot.id}, false)">Disable</button>`
          : `<button class="action-btn action-btn-enable" onclick="toggleBot(${bot.id}, true)">Enable</button>`
        }
        <button class="action-btn action-btn-delete" onclick="deleteBot(${bot.id}, '${(bot.name || '').replace(/'/g, "\\'")}')">Delete</button>
      </div>
    `;
    
    return `
      <tr class="${rowAlt ? "row-alt" : ""}">
        <td><strong>${bot.id || "-"}</strong></td>
        <td>${bot.name || "-"}</td>
        <td><code>${bot.bot_key || "-"}</code></td>
        <td class="text-center"><span class="${enabledClass}">${enabledText}</span></td>
        <td><a href="#" onclick="openTradingViewChart('${bot.symbol || ""}'); return false;" class="symbol-link" title="點擊打開 TradingView 圖表">${bot.symbol || "-"}</a></td>
        <td class="text-center">${sideDisplay}</td>
        <td class="text-right">${fmtNumber(bot.qty || 0, 6)}</td>
        <td class="text-center">${bot.leverage || 0}x</td>
        <td class="text-right">${maxInvestDisplay}</td>
        <td>${signalDisplay}</td>
        <td class="text-center">${trailingDisplay}</td>
        <td class="text-center">${actionsHtml}</td>
      </tr>
    `;
  }
  
  // 生成排序指示器
  function getSortIndicator(col) {
    if (botsSortState.column === col) {
      return botsSortState.direction === 'asc' ? ' ↑' : ' ↓';
    }
    return '';
  }
  
  // 生成可排序的 th
  function generateSortableTh(col, label, className = '') {
    const isActive = botsSortState.column === col;
    const sortClass = isActive ? 'sort-active' : '';
    const indicator = getSortIndicator(col);
    return `<th class="${className} ${sortClass} sortable" data-column="${col}" style="cursor: pointer; user-select: none;">
      ${label}${indicator}
    </th>`;
  }
  
  // 強制重建表格以確保所有欄位都正確顯示（包括 Max Invest）
  // 這樣可以避免緩存問題和欄位不匹配的情況
  
  // 建立完整表格
  let html = `
    <table class="positions-table">
      <thead>
        <tr>
          ${generateSortableTh('id', 'ID', 'text-center')}
          ${generateSortableTh('name', 'Name', '')}
          ${generateSortableTh('bot_key', 'Bot Key', '')}
          ${generateSortableTh('status', 'Status', 'text-center')}
          ${generateSortableTh('symbol', 'Symbol', '')}
          ${generateSortableTh('side', 'Side', 'text-center')}
          ${generateSortableTh('qty', 'Qty', 'text-right')}
          ${generateSortableTh('leverage', 'Leverage', 'text-center')}
          ${generateSortableTh('max_invest_usdt', 'Max Invest (USDT)', 'text-right')}
          ${generateSortableTh('signal', 'Signal', '')}
          ${generateSortableTh('trailing', 'Trailing', 'text-center')}
          <th class="text-center">Actions</th>
        </tr>
      </thead>
      <tbody>
  `;
  
  sortedData.forEach((bot, index) => {
    html += generateBotRowHtml(bot, index);
  });
  
  html += `
      </tbody>
    </table>
  `;
  
  container.innerHTML = html;
  
  // 綁定排序事件
  bindBotsSortEvents();
}

// 綁定 Bots 排序事件
function bindBotsSortEvents() {
  document.querySelectorAll("#bots-table-container .sortable").forEach((th) => {
    th.addEventListener("click", () => {
      const column = th.getAttribute("data-column");
      if (!column) return;
      
      // 切換排序方向
      if (botsSortState.column === column) {
        botsSortState.direction = botsSortState.direction === 'asc' ? 'desc' : 'asc';
      } else {
        botsSortState.column = column;
        botsSortState.direction = 'asc';
      }
      
      // 重新載入並渲染（會自動應用排序）
      loadBots();
    });
  });
}

// 切換 Bot 啟用/停用
async function toggleBot(botId, enable) {
  const endpoint = enable ? `/bots/${botId}/enable` : `/bots/${botId}/disable`;
  
  try {
    const resp = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
    });

    if (await handleFetchError(resp)) return;

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }

    alert(`Bot ${enable ? "已啟用" : "已停用"}`);
    await loadBots();
  } catch (error) {
    console.error("切換 Bot 狀態失敗:", error);
    alert(`操作失敗：${error.message}`);
  }
}

// 刪除 Bot
async function deleteBot(botId, botName) {
  const confirmMsg = `確定要刪除此 Bot 嗎？\n\nBot ID: ${botId}\n名稱: ${botName}\n\n注意：如果此 Bot 下仍有 OPEN 倉位，將無法刪除。`;
  
  if (!confirm(confirmMsg)) {
    return;
  }
  
  try {
    const resp = await fetch(`/bots/${botId}`, {
      method: "DELETE",
    });
    
    if (await handleFetchError(resp)) return;
    
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.detail || `HTTP ${resp.status}`);
    }
    
    const result = await resp.json().catch(() => ({}));
    alert(result.message || `Bot ${botId} 已刪除`);
    await loadBots();
  } catch (error) {
    console.error("刪除 Bot 失敗:", error);
    alert(`刪除失敗：${error.message}`);
  }
}

// 批量更新所有 Bot 的投資金額
async function bulkUpdateInvestAmount() {
  console.log("bulkUpdateInvestAmount called");
  const input = document.getElementById("bulk-update-invest-amount");
  const resultDiv = document.getElementById("bulk-update-result");
  
  if (!input) {
    console.error("bulk-update-invest-amount input not found");
    return;
  }
  if (!resultDiv) {
    console.error("bulk-update-result div not found");
    return;
  }
  
  const maxInvestUsdt = parseFloat(input.value);
  console.log("Parsed maxInvestUsdt:", maxInvestUsdt);
  
  // 驗證輸入
  if (isNaN(maxInvestUsdt) || maxInvestUsdt <= 0) {
    resultDiv.style.display = "block";
    resultDiv.style.backgroundColor = "#ff6b6b";
    resultDiv.style.color = "#fff";
    resultDiv.style.border = "1px solid #ff6b6b";
    resultDiv.textContent = "請輸入有效的投資金額（必須大於 0）";
    setTimeout(() => {
      resultDiv.style.display = "none";
    }, 5000);
    return;
  }
  
  // 獲取按鈕並保存原始文字（在確認對話框之前）
  const btn = document.getElementById("btn-bulk-update-invest-amount");
  if (!btn) {
    console.error("btn-bulk-update-invest-amount button not found");
    return;
  }
  
  // 防止重複點擊：如果按鈕已經是 "更新中..." 狀態，則不執行
  if (btn.disabled || btn.textContent === "更新中...") {
    console.log("Update already in progress, ignoring duplicate click");
    return;
  }
  
  // 保存原始文字（確保不是 "更新中..."）
  const originalText = (btn.textContent && btn.textContent !== "更新中...") 
    ? btn.textContent 
    : "Update All Bots";
  
  // 確認對話框
  const confirmMsg = `確定要將所有 Bot 的投資金額（max_invest_usdt）更新為 ${maxInvestUsdt} USDT 嗎？\n\n此操作會影響所有 Bot 的設定。`;
  console.log("Showing confirm dialog:", confirmMsg);
  const confirmed = window.confirm(confirmMsg);
  console.log("User confirmed:", confirmed);
  if (!confirmed) {
    console.log("User cancelled the operation");
    return;
  }
  
  // 提示輸入密碼
  const password = prompt("請輸入密碼以更新 Max Invest USDT:");
  if (password === null) {
    // 用戶取消
    console.log("User cancelled password entry");
    return;
  }
  if (!password || password.trim() === "") {
    resultDiv.style.display = "block";
    resultDiv.style.backgroundColor = "#ff6b6b";
    resultDiv.style.color = "#fff";
    resultDiv.style.border = "1px solid #ff6b6b";
    resultDiv.textContent = "密碼不能為空";
    setTimeout(() => {
      resultDiv.style.display = "none";
    }, 5000);
    return;
  }
  
  // 設置按鈕為載入狀態
  btn.disabled = true;
  btn.textContent = "更新中...";
  console.log("Button set to loading state, originalText saved as:", originalText);
  
  try {
    const resp = await fetch("/bots/bulk-update-invest-amount", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        max_invest_usdt: maxInvestUsdt,
        max_invest_password: password.trim()
      }),
    });
    
    if (!resp.ok) {
      let errorMessage = `HTTP ${resp.status}`;
      try {
        const errorData = await resp.json();
        errorMessage = errorData.detail || errorData.message || errorMessage;
      } catch (e) {
        // 如果無法解析 JSON，嘗試讀取文本
        const text = await resp.text().catch(() => "");
        if (text) {
          errorMessage = text;
        }
      }
      throw new Error(errorMessage);
    }
    
    const result = await resp.json();
    
    // 顯示成功訊息
    resultDiv.style.display = "block";
    resultDiv.style.backgroundColor = "#40ffb3";
    resultDiv.style.color = "#11151f";
    resultDiv.style.border = "1px solid #40ffb3";
    resultDiv.textContent = `✓ ${result.message || `成功更新 ${result.updated_count} 個 Bot`}`;
    
    // 清空輸入框
    input.value = "";
    
    // 重新載入 Bots 列表
    await loadBots();
    
    // 在 loadBots 完成後立即恢復按鈕狀態
    const restoreBtnAfterLoad = document.getElementById("btn-bulk-update-invest-amount");
    if (restoreBtnAfterLoad) {
      restoreBtnAfterLoad.disabled = false;
      // 確保恢復為正確的文字（如果不是 "Update All Bots"，則使用默認值）
      const textToRestore = (originalText && originalText !== "更新中...") ? originalText : "Update All Bots";
      restoreBtnAfterLoad.textContent = textToRestore;
      console.log("Button state restored after loadBots to:", textToRestore, "(originalText was:", originalText + ")");
    }
    
    // 5 秒後隱藏訊息
    setTimeout(() => {
      resultDiv.style.display = "none";
    }, 5000);
    
  } catch (error) {
    console.error("批量更新投資金額失敗:", error);
    resultDiv.style.display = "block";
    resultDiv.style.backgroundColor = "#ff6b6b";
    resultDiv.style.color = "#fff";
    resultDiv.style.border = "1px solid #ff6b6b";
    // 正確提取錯誤訊息
    const errorMessage = error.message || String(error) || "未知錯誤";
    resultDiv.textContent = `批量更新失敗：${errorMessage}`;
    setTimeout(() => {
      resultDiv.style.display = "none";
    }, 5000);
  } finally {
    // 重新獲取按鈕元素（因為 loadBots 可能影響 DOM）
    // 確保按鈕狀態總是被恢復
    const restoreBtn = document.getElementById("btn-bulk-update-invest-amount");
    if (restoreBtn) {
      restoreBtn.disabled = false;
      // 確保恢復為正確的文字（如果不是 "Update All Bots"，則使用默認值）
      const textToRestore = (originalText && originalText !== "更新中...") ? originalText : "Update All Bots";
      restoreBtn.textContent = textToRestore;
      console.log("Button state restored in finally to:", textToRestore, "(originalText was:", originalText + ")");
    } else {
      console.warn("Could not find button to restore state");
    }
  }
}

// ==================== Bot Form Functions ====================

// 初始化 Symbol 建議清單
function initSymbolSuggestions() {
  const datalist = document.getElementById("symbol-suggestions");
  if (!datalist) return;

  // 常見 USDT 合約清單
  const symbols = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "DOGEUSDT",
    "LTCUSDT",
    "OPUSDT",
    "ARBUSDT",
    "LINKUSDT",
    "AVAXUSDT",
    "DOTUSDT",
    "MATICUSDT",
    "UNIUSDT",
    "ATOMUSDT",
    "ETCUSDT",
    "FILUSDT",
    "NEARUSDT",
    "APTUSDT",
  ];

  datalist.innerHTML = symbols.map((s) => `<option value="${s}"></option>`).join("");
}

// 當前編輯的 Bot ID（null 表示創建模式）
let currentEditingBotId = null;

// 重置 Bot 表單
function resetBotForm() {
  currentEditingBotId = null;
  document.getElementById("bot-name").value = "";
  document.getElementById("bot-key").value = "";
  document.getElementById("bot-symbol").value = "BTCUSDT";
  document.getElementById("bot-qty").value = "0.01";
  document.getElementById("bot-max-invest-usdt").value = "50";
  document.getElementById("bot-leverage").value = "5";
  const signalSelect = document.getElementById("bot-signal-id");
  if (signalSelect) {
    signalSelect.value = "";
  }
  const useSignalSideCheckbox = document.getElementById("bot-use-signal-side");
  const fixedSideSelect = document.getElementById("bot-fixed-side");
  if (useSignalSideCheckbox) {
    useSignalSideCheckbox.checked = true;
  }
  if (fixedSideSelect) {
    fixedSideSelect.value = "";
    fixedSideSelect.disabled = true; // 當 use_signal_side 為 true 時，fixed_side 應該被禁用
  }
  
  // 重置表單標題和按鈕
  const formTitle = document.querySelector("#bot-form-container h3");
  if (formTitle) {
    formTitle.textContent = "Create New Bot";
  }
  const submitBtn = document.querySelector("#bot-create-form button[type='submit']");
  if (submitBtn) {
    submitBtn.textContent = "Create Bot";
  }
  
  // 重置 bot_key 欄位為可編輯
  const botKeyInput = document.getElementById("bot-key");
  if (botKeyInput) {
    botKeyInput.readOnly = false;
    botKeyInput.style.backgroundColor = "";
    botKeyInput.style.cursor = "";
  }
  
  document.getElementById("bot-use-dynamic-stop").checked = true;
  document.getElementById("bot-trailing-callback-percent").value = "10";
  document.getElementById("bot-base-stop-loss-pct").value = "5.0";
  document.getElementById("bot-enabled").checked = true;

  const err = document.getElementById("bot-form-error");
  if (err) {
    err.style.display = "none";
    err.textContent = "";
  }
  
  // 重新啟用提交按鈕
  const formEl = document.getElementById("bot-create-form");
  if (formEl) {
    const submitBtn = formEl.querySelector('button[type="submit"]');
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = "Create Bot";
    }
  }
}

// 顯示表單錯誤訊息
function showBotFormError(msg) {
  const errorEl = document.getElementById("bot-form-error");
  if (!errorEl) return;
  errorEl.textContent = msg;
  errorEl.style.display = "block";
  // 滾動到錯誤訊息
  errorEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// 提交 Bot 表單
async function onSubmitBotForm(event) {
  event.preventDefault();
  const errorEl = document.getElementById("bot-form-error");
  if (errorEl) {
    errorEl.style.display = "none";
    errorEl.textContent = "";
  }

  // 讀取欄位值
  const name = document.getElementById("bot-name").value.trim();
  const botKey = document.getElementById("bot-key").value.trim();
  const symbol = document.getElementById("bot-symbol").value.trim().toUpperCase();
  const qtyInput = document.getElementById("bot-qty").value.trim();
  let qty = qtyInput !== "" ? parseFloat(qtyInput) : 0.01; // 預設值
  // 四捨五入到小數第4位
  if (!isNaN(qty) && qty > 0) {
    qty = Math.round(qty * 10000) / 10000;
  }
  const maxInvestUsdtInput = document.getElementById("bot-max-invest-usdt").value.trim();
  const maxInvestUsdt = maxInvestUsdtInput !== "" ? parseFloat(maxInvestUsdtInput) : null;
  const leverage = parseInt(document.getElementById("bot-leverage").value, 10);
  const useSignalSide = document.getElementById("bot-use-signal-side").checked;
  const fixedSide = document.getElementById("bot-fixed-side").value || null;
  const useDynamicStop = document.getElementById("bot-use-dynamic-stop").checked;
  const trailingPct = document.getElementById("bot-trailing-callback-percent").value.trim();
  const baseSlPct = document.getElementById("bot-base-stop-loss-pct").value.trim();
  const enabled = document.getElementById("bot-enabled").checked;

  // 基本驗證
  if (!name || !botKey || !symbol) {
    showBotFormError("Name / Bot Key / Symbol 為必填欄位");
    return;
  }
  
  // 驗證 max_invest_usdt 或 qty（至少要有其中一個）
  if (maxInvestUsdt !== null) {
    if (isNaN(maxInvestUsdt) || maxInvestUsdt <= 0) {
      showBotFormError("Max Invest USDT 必須大於 0");
      return;
    }
  } else {
    if (!qty || qty <= 0 || isNaN(qty)) {
      showBotFormError("Qty 或 Max Invest USDT 必須至少設定一個，且大於 0");
      return;
    }
  }
  
  if (!leverage || leverage <= 0 || leverage > 125 || isNaN(leverage)) {
    showBotFormError("Leverage 必須介於 1~125");
    return;
  }
  
  if (!useSignalSide && !fixedSide) {
    showBotFormError("未使用 signal side 時，Fixed Side 不可為空");
    return;
  }

  // trailing / base SL 可為空，但若有值需檢查範圍
  let trailingCallbackPercent = trailingPct !== "" ? parseFloat(trailingPct) : null;
  if (trailingCallbackPercent !== null && (isNaN(trailingCallbackPercent) || trailingCallbackPercent < 0 || trailingCallbackPercent > 100)) {
    showBotFormError("Dynamic Lock (%) 必須在 0~100 之間");
    return;
  }

  let baseStopLossPct = baseSlPct !== "" ? parseFloat(baseSlPct) : null;
  if (baseStopLossPct !== null && (isNaN(baseStopLossPct) || baseStopLossPct < 0 || baseStopLossPct > 100)) {
    showBotFormError("Base SL (%) 必須在 0~100 之間");
    return;
  }

  // 如果 baseStopLossPct 為 null，使用預設值 5.0
  if (baseStopLossPct === null) {
    baseStopLossPct = 5.0;
  }

  const signalIdSelect = document.getElementById("bot-signal-id");
  const signalId = signalIdSelect && signalIdSelect.value ? parseInt(signalIdSelect.value, 10) : null;

  // 構建 payload（編輯模式時不需要包含所有欄位，只包含要更新的）
  const payload = {};
  if (currentEditingBotId) {
    // 編輯模式：只包含要更新的欄位
    payload.name = name;
    payload.symbol = symbol;
    payload.enabled = enabled;
    payload.use_signal_side = useSignalSide;
    payload.fixed_side = fixedSide;
    payload.qty = qty;
    payload.max_invest_usdt = maxInvestUsdt;
    payload.leverage = leverage;
    payload.use_dynamic_stop = useDynamicStop;
    payload.trailing_callback_percent = trailingCallbackPercent;
    payload.base_stop_loss_pct = baseStopLossPct;
    payload.signal_id = signalId;
    
    // 編輯模式：如果 max_invest_usdt 被改變，需要密碼
    // 先獲取當前 bot 的 max_invest_usdt 值來比較（從已載入的 bot 列表中）
    const botsList = await fetch("/bots").then(r => r.ok ? r.json() : []).catch(() => []);
    const currentBot = botsList.find(b => b.id === currentEditingBotId);
    if (currentBot) {
      // 正確比較 max_invest_usdt（處理 null 值的情況）
      const oldMaxInvest = currentBot.max_invest_usdt;
      const newMaxInvest = maxInvestUsdt;
      const hasChanged = (oldMaxInvest === null && newMaxInvest !== null) ||
                        (oldMaxInvest !== null && newMaxInvest === null) ||
                        (oldMaxInvest !== null && newMaxInvest !== null && Math.abs(oldMaxInvest - newMaxInvest) > 0.0001);
      
      if (hasChanged) {
        // max_invest_usdt 被改變了，需要密碼
        const password = prompt("請輸入密碼以更新 Max Invest USDT:");
        if (password === null) {
          // 用戶取消
          return;
        }
        if (!password || password.trim() === "") {
          showBotFormError("密碼不能為空");
          return;
        }
        payload.max_invest_password = password.trim();
      }
    }
    
    // 編輯模式：直接提交
    await submitBotPayload(payload, event.target);
  } else {
    // 創建模式：先顯示確認模態框
    payload.name = name;
    payload.bot_key = botKey;
    payload.symbol = symbol;
    payload.enabled = enabled;
    payload.use_signal_side = useSignalSide;
    payload.fixed_side = fixedSide;
    payload.qty = qty;
    payload.max_invest_usdt = maxInvestUsdt;
    payload.leverage = leverage;
    payload.use_dynamic_stop = useDynamicStop;
    payload.trailing_callback_percent = trailingCallbackPercent;
    payload.base_stop_loss_pct = baseStopLossPct;
    payload.signal_id = signalId;
    
    // 顯示確認模態框
    showBotConfirmModal(payload, event.target);
  }
}

// 顯示 Bot 創建確認模態框
function showBotConfirmModal(payload, formElement) {
  const modal = document.getElementById("bot-confirm-modal");
  const detailsEl = document.getElementById("bot-confirm-details");
  
  // 獲取 Signal 名稱（如果有）
  let signalName = "(None - independent bot)";
  if (payload.signal_id) {
    const signalSelect = document.getElementById("bot-signal-id");
    if (signalSelect) {
      const selectedOption = signalSelect.options[signalSelect.selectedIndex];
      if (selectedOption) {
        signalName = selectedOption.text || `Signal ID: ${payload.signal_id}`;
      }
    }
  }
  
  // 構建詳細信息 HTML
  const detailsHtml = `
    <div style="display: grid; grid-template-columns: 1fr 2fr; gap: 12px 16px; font-size: 13px;">
      <div style="color: var(--tbl-muted); font-weight: 500;">Name:</div>
      <div style="color: var(--tbl-text);">${escapeHtml(payload.name)}</div>
      
      <div style="color: var(--tbl-muted); font-weight: 500;">Bot Key:</div>
      <div style="color: var(--tbl-text); font-family: monospace;">${escapeHtml(payload.bot_key)}</div>
      
      <div style="color: var(--tbl-muted); font-weight: 500;">Symbol:</div>
      <div style="color: var(--tbl-text);">${escapeHtml(payload.symbol)}</div>
      
      <div style="color: var(--tbl-muted); font-weight: 500;">Quantity:</div>
      <div style="color: var(--tbl-text);">${payload.qty || "—"}</div>
      
      <div style="color: var(--tbl-muted); font-weight: 500;">Max Invest (USDT):</div>
      <div style="color: var(--tbl-text);">${payload.max_invest_usdt !== null && payload.max_invest_usdt !== undefined ? payload.max_invest_usdt.toFixed(2) : "—"}</div>
      
      <div style="color: var(--tbl-muted); font-weight: 500;">Leverage:</div>
      <div style="color: var(--tbl-text);">${payload.leverage}x</div>
      
      <div style="color: var(--tbl-muted); font-weight: 500;">Signal:</div>
      <div style="color: var(--tbl-text);">${escapeHtml(signalName)}</div>
      
      <div style="color: var(--tbl-muted); font-weight: 500;">Side Source:</div>
      <div style="color: var(--tbl-text);">${payload.use_signal_side ? "Use side from TradingView signal" : `Fixed: ${payload.fixed_side === "BUY" ? "LONG" : "SHORT"}`}</div>
      
      <div style="color: var(--tbl-muted); font-weight: 500;">Dynamic Stop:</div>
      <div style="color: var(--tbl-text);">${payload.use_dynamic_stop ? "✓ Enabled" : "✗ Disabled"}</div>
      
      ${payload.use_dynamic_stop ? `
      <div style="color: var(--tbl-muted); font-weight: 500;">Dynamic Lock (%):</div>
      <div style="color: var(--tbl-text);">${payload.trailing_callback_percent !== null && payload.trailing_callback_percent !== undefined ? payload.trailing_callback_percent.toFixed(2) + "%" : "—"}</div>
      ` : ""}
      
      <div style="color: var(--tbl-muted); font-weight: 500;">Base SL (%):</div>
      <div style="color: var(--tbl-text);">${payload.base_stop_loss_pct !== null && payload.base_stop_loss_pct !== undefined ? payload.base_stop_loss_pct.toFixed(2) + "%" : "—"}</div>
      
      <div style="color: var(--tbl-muted); font-weight: 500;">Enabled:</div>
      <div style="color: var(--tbl-text);">${payload.enabled ? "✓ Yes" : "✗ No"}</div>
    </div>
  `;
  
  detailsEl.innerHTML = detailsHtml;
  
  // 存儲 payload 和 form element 供確認時使用
  modal._pendingPayload = payload;
  modal._formElement = formElement;
  
  // 顯示模態框
  modal.classList.remove("hidden");
  
  // 綁定確認按鈕事件（使用 once 選項避免重複綁定）
  const confirmBtn = document.getElementById("bot-confirm-submit");
  const cancelBtn = document.getElementById("bot-confirm-cancel");
  const closeBtn = document.getElementById("bot-confirm-close");
  
  // 移除舊的事件監聽器（通過重新綁定）
  const handleConfirm = async () => {
    const modal = document.getElementById("bot-confirm-modal");
    if (modal._pendingPayload && modal._formElement) {
      await submitBotPayload(modal._pendingPayload, modal._formElement);
      closeBotConfirmModal();
    }
  };
  
  // 移除舊的監聽器並添加新的
  const newConfirmBtn = confirmBtn.cloneNode(true);
  confirmBtn.parentNode.replaceChild(newConfirmBtn, confirmBtn);
  document.getElementById("bot-confirm-submit").addEventListener("click", handleConfirm);
  
  const newCancelBtn = cancelBtn.cloneNode(true);
  cancelBtn.parentNode.replaceChild(newCancelBtn, cancelBtn);
  document.getElementById("bot-confirm-cancel").addEventListener("click", closeBotConfirmModal);
  
  const newCloseBtn = closeBtn.cloneNode(true);
  closeBtn.parentNode.replaceChild(newCloseBtn, closeBtn);
  document.getElementById("bot-confirm-close").addEventListener("click", closeBotConfirmModal);
  
  // 點擊模態框外部關閉（移除舊的監聽器）
  const handleModalClick = (e) => {
    if (e.target === modal) {
      closeBotConfirmModal();
    }
  };
  // 移除舊的監聽器（如果有的話）
  modal.removeEventListener("click", modal._handleModalClick);
  modal._handleModalClick = handleModalClick;
  modal.addEventListener("click", handleModalClick);
}

// 關閉 Bot 確認模態框
function closeBotConfirmModal() {
  const modal = document.getElementById("bot-confirm-modal");
  modal.classList.add("hidden");
  modal._pendingPayload = null;
  modal._formElement = null;
}

// 提交 Bot payload（實際的 API 調用）
async function submitBotPayload(payload, formElement) {
  try {
    const submitBtn = formElement.querySelector('button[type="submit"]');
    const originalText = submitBtn.textContent;
    submitBtn.disabled = true;
    submitBtn.textContent = currentEditingBotId ? "Updating..." : "Creating...";

    const url = currentEditingBotId ? `/bots/${currentEditingBotId}` : "/bots";
    const method = currentEditingBotId ? "PUT" : "POST";

    const resp = await fetch(url, {
      method: method,
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    if (await handleFetchError(resp)) {
      submitBtn.disabled = false;
      submitBtn.textContent = originalText;
      return;
    }

    if (!resp.ok) {
      let errorMessage = `${currentEditingBotId ? 'Update' : 'Create'} bot failed: HTTP ${resp.status}`;
      try {
        const data = await resp.json();
        errorMessage = data.detail || data.message || errorMessage;
      } catch (e) {
        // 如果無法解析 JSON，使用預設錯誤訊息
        const text = await resp.text().catch(() => "");
        if (text) {
          errorMessage = text;
        }
      }
      showBotFormError(errorMessage);
      submitBtn.disabled = false;
      submitBtn.textContent = originalText;
      return;
    }

    // 成功：關閉表單、重新載入 bots 列表
    document.getElementById("bot-form-container").style.display = "none";
    resetBotForm();
    await loadBots();
    
    // 顯示成功訊息
    alert(`Bot ${currentEditingBotId ? '更新' : '建立'}成功！`);
  } catch (e) {
    console.error(`${currentEditingBotId ? 'Update' : 'Create'} bot error:`, e);
    const errorMessage = e.message || String(e) || `${currentEditingBotId ? 'Update' : 'Create'} bot failed`;
    showBotFormError(errorMessage);
    const submitBtn = formElement.querySelector('button[type="submit"]');
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = currentEditingBotId ? "Update Bot" : "Create Bot";
    }
  }
}

// HTML 轉義函數
function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// 從編輯模式開啟 Bot 表單
async function openBotFormFromEdit(botId) {
  document.getElementById("bot-form-container").style.display = "block";
  resetBotForm();
  currentEditingBotId = botId;
  
  // 更新表單標題和按鈕
  const formTitle = document.querySelector("#bot-form-container h3");
  if (formTitle) {
    formTitle.textContent = "Edit Bot";
  }
  const submitBtn = document.querySelector("#bot-create-form button[type='submit']");
  if (submitBtn) {
    submitBtn.textContent = "Update Bot";
  }
  
  try {
    const response = await fetch(`/bots/${botId}`);
    if (!response.ok) {
      showBotFormError(`無法載入 Bot 資料: HTTP ${response.status}`);
      return;
    }
    
    const bot = await response.json();
    
    // 填入表單欄位
    document.getElementById("bot-name").value = bot.name || "";
    const botKeyInput = document.getElementById("bot-key");
    botKeyInput.value = bot.bot_key || "";
    // 編輯模式下，bot_key 應該是只讀的（因為它是唯一鍵）
    botKeyInput.readOnly = true;
    botKeyInput.style.backgroundColor = "var(--bg-secondary)";
    botKeyInput.style.cursor = "not-allowed";
    document.getElementById("bot-symbol").value = bot.symbol || "BTCUSDT";
    document.getElementById("bot-qty").value = bot.qty || "0.01";
    document.getElementById("bot-max-invest-usdt").value = bot.max_invest_usdt || "";
    document.getElementById("bot-leverage").value = bot.leverage || "20";
    document.getElementById("bot-use-signal-side").checked = bot.use_signal_side !== false;
    document.getElementById("bot-fixed-side").value = bot.fixed_side || "";
    document.getElementById("bot-use-dynamic-stop").checked = bot.use_dynamic_stop !== false;
    document.getElementById("bot-trailing-callback-percent").value = bot.trailing_callback_percent || "";
    document.getElementById("bot-base-stop-loss-pct").value = bot.base_stop_loss_pct || "3.0";
    document.getElementById("bot-enabled").checked = bot.enabled !== false;
    
    // 設定 signal
    const signalSelect = document.getElementById("bot-signal-id");
    if (signalSelect && bot.signal_id) {
      signalSelect.value = String(bot.signal_id);
    }
    
    // 更新 fixed_side 的可用性
    const useSignalSideCheckbox = document.getElementById("bot-use-signal-side");
    const fixedSideSelect = document.getElementById("bot-fixed-side");
    if (useSignalSideCheckbox && fixedSideSelect) {
      fixedSideSelect.disabled = useSignalSideCheckbox.checked;
    }
    
    // 如果設定了 max_invest_usdt，重新計算 qty
    if (bot.max_invest_usdt) {
      await recalculateQtyFromMaxInvest();
    }
    
    // 綁定 leverage 和 max_invest_usdt 的 change 事件，自動重新計算 qty
    bindQtyRecalculation();
    
    // 滾動到表單
    document.getElementById("bot-form-container").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (error) {
    console.error("載入 Bot 資料失敗:", error);
    showBotFormError(`載入 Bot 資料失敗: ${error.message}`);
  }
}

// 重新計算 qty（根據 max_invest_usdt 和當前價格）
async function recalculateQtyFromMaxInvest() {
  const symbolInput = document.getElementById("bot-symbol");
  const maxInvestInput = document.getElementById("bot-max-invest-usdt");
  const qtyInput = document.getElementById("bot-qty");
  
  if (!symbolInput || !maxInvestInput || !qtyInput) return;
  
  const symbol = symbolInput.value.trim().toUpperCase();
  const maxInvestUsdt = parseFloat(maxInvestInput.value.trim());
  
  if (!symbol || isNaN(maxInvestUsdt) || maxInvestUsdt <= 0) {
    return;
  }
  
  try {
    // 取得當前價格（需要後端 API endpoint）
    const response = await fetch(`/api/mark-price/${symbol}`);
    if (response.ok) {
      const data = await response.json();
      const currentPrice = parseFloat(data.mark_price);
      if (currentPrice && currentPrice > 0) {
        // 計算 qty = max_invest_usdt / currentPrice
        let calculatedQty = maxInvestUsdt / currentPrice;
        // 四捨五入到小數第4位
        calculatedQty = Math.round(calculatedQty * 10000) / 10000;
        qtyInput.value = calculatedQty.toFixed(4);
        console.log(`根據 max_invest_usdt=${maxInvestUsdt} 和當前價格=${currentPrice}，計算 qty=${calculatedQty}`);
      }
    }
  } catch (error) {
    console.warn("無法取得當前價格，無法重新計算 qty:", error);
  }
}

// 綁定 qty 重新計算的事件
function bindQtyRecalculation() {
  const leverageInput = document.getElementById("bot-leverage");
  const maxInvestInput = document.getElementById("bot-max-invest-usdt");
  const symbolInput = document.getElementById("bot-symbol");
  
  // 移除舊的事件監聽器（如果有的話）
  if (leverageInput) {
    leverageInput.removeEventListener("change", recalculateQtyFromMaxInvest);
    leverageInput.removeEventListener("input", recalculateQtyFromMaxInvest);
  }
  if (maxInvestInput) {
    maxInvestInput.removeEventListener("change", recalculateQtyFromMaxInvest);
    maxInvestInput.removeEventListener("input", recalculateQtyFromMaxInvest);
  }
  if (symbolInput) {
    symbolInput.removeEventListener("change", recalculateQtyFromMaxInvest);
  }
  
  // 綁定新的事件監聽器
  if (leverageInput) {
    leverageInput.addEventListener("change", recalculateQtyFromMaxInvest);
    leverageInput.addEventListener("input", recalculateQtyFromMaxInvest);
  }
  if (maxInvestInput) {
    maxInvestInput.addEventListener("change", recalculateQtyFromMaxInvest);
    maxInvestInput.addEventListener("input", recalculateQtyFromMaxInvest);
  }
  if (symbolInput) {
    symbolInput.addEventListener("change", recalculateQtyFromMaxInvest);
  }
}

// 初始化 Bots Tab
function initBotsTab() {
  // 綁定批量更新投資金額按鈕
  const bulkUpdateBtn = document.getElementById("btn-bulk-update-invest-amount");
  if (bulkUpdateBtn) {
    bulkUpdateBtn.addEventListener("click", function(e) {
      e.preventDefault();
      e.stopPropagation();
      try {
        bulkUpdateInvestAmount();
      } catch (error) {
        console.error("Error in bulkUpdateInvestAmount:", error);
        alert("發生錯誤：" + error.message);
      }
    });
    // 支援 Enter 鍵提交
    const bulkUpdateInput = document.getElementById("bulk-update-invest-amount");
    if (bulkUpdateInput) {
      bulkUpdateInput.addEventListener("keypress", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          try {
            bulkUpdateInvestAmount();
          } catch (error) {
            console.error("Error in bulkUpdateInvestAmount:", error);
            alert("發生錯誤：" + error.message);
          }
        }
      });
    }
  } else {
    console.warn("btn-bulk-update-invest-amount button not found");
  }
  
  const openBtn = document.getElementById("btn-open-bot-form");
  const cancelBtn = document.getElementById("btn-cancel-bot-form");
  const formContainer = document.getElementById("bot-form-container");
  const formEl = document.getElementById("bot-create-form");
  const useSignalSideCheckbox = document.getElementById("bot-use-signal-side");
  const fixedSideSelect = document.getElementById("bot-fixed-side");

  if (openBtn && formContainer) {
    openBtn.addEventListener("click", () => {
      formContainer.style.display = formContainer.style.display === "none" ? "block" : "block";
      resetBotForm();
      // 滾動到表單
      formContainer.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  }

  if (cancelBtn && formContainer) {
    cancelBtn.addEventListener("click", () => {
      formContainer.style.display = "none";
      resetBotForm();
    });
  }

  if (formEl) {
    formEl.addEventListener("submit", onSubmitBotForm);
  }

  // 當 use_signal_side 改變時，更新 fixed_side 的可用性
  if (useSignalSideCheckbox && fixedSideSelect) {
    useSignalSideCheckbox.addEventListener("change", (e) => {
      if (e.target.checked) {
        fixedSideSelect.value = "";
        fixedSideSelect.disabled = true;
      } else {
        fixedSideSelect.disabled = false;
      }
    });
    // 初始化狀態
    if (useSignalSideCheckbox.checked) {
      fixedSideSelect.disabled = true;
    }
  }

  initSymbolSuggestions();
  
  // 載入 Signal Configs 到 Bot 表單的下拉選單
  loadSignalConfigsForBotForm();
  
  // 綁定 qty 重新計算的事件（創建模式）
  bindQtyRecalculation();
}

// ==================== Signal Config Functions ====================

// 儲存 signal configs 資料，用於快速查找
let signalConfigsCache = {};

// 載入 Signal Configs（用於 Bot 表單的下拉選單）
async function loadSignalConfigsForBotForm() {
  const select = document.getElementById("bot-signal-id");
  if (!select) return;
  
  try {
    const response = await fetch("/signal-configs");
    if (response.status === 401 || response.status === 403) {
      return; // 未登入，不處理
    }
    if (!response.ok) {
      console.warn("載入 Signal Configs 失敗:", response.status);
      return;
    }
    
    const configs = await response.json();
    // 清空 cache 並重新建立
    signalConfigsCache = {};
    
    // 保留第一個選項（None）
    const firstOption = select.querySelector("option");
    select.innerHTML = firstOption ? firstOption.outerHTML : '<option value="">(None - independent bot)</option>';
    
    // 添加所有 enabled 的 Signal Configs
    configs
      .filter(c => c.enabled)
      .forEach(config => {
        const option = document.createElement("option");
        option.value = String(config.id);
        option.textContent = `${config.name} (${config.signal_key})`;
        select.appendChild(option);
        // 儲存到 cache（確保包含 symbol_hint）
        signalConfigsCache[config.id] = config;
      });
    
    // 綁定 change 事件：當選擇 signal 時，自動填入 symbol
    select.addEventListener("change", async function() {
      const selectedSignalId = this.value;
      const symbolInput = document.getElementById("bot-symbol");
      
      if (!symbolInput) return;
      
      if (!selectedSignalId || selectedSignalId === "") {
        // 如果取消選擇 signal，不清空 symbol（讓用戶保留）
        return;
      }
      
      // 先從 cache 查找
      const cachedConfig = signalConfigsCache[selectedSignalId];
      if (cachedConfig && cachedConfig.symbol_hint) {
        symbolInput.value = cachedConfig.symbol_hint.toUpperCase();
        return;
      }
      
      // 如果 cache 沒有，去 fetch 詳細資料
      try {
        const detailResponse = await fetch(`/signal-configs/${selectedSignalId}`);
        if (detailResponse.ok) {
          const signalData = await detailResponse.json();
          if (signalData.symbol_hint) {
            symbolInput.value = signalData.symbol_hint.toUpperCase();
            // 更新 cache
            signalConfigsCache[selectedSignalId] = signalData;
          }
        }
      } catch (error) {
        console.warn("無法取得 signal 詳細資料:", error);
      }
    });
  } catch (error) {
    console.error("載入 Signal Configs 失敗:", error);
  }
}

// 載入 Signal Configs（用於 Signals tab）
async function loadSignalConfigs() {
  const container = document.getElementById("signal-configs-table-container");
  if (!container) {
    console.error("signal-configs-table-container not found");
    return;
  }
  
  const existingTable = container.querySelector("table");
  const isFirstLoad = !existingTable;
  
  if (isFirstLoad) {
    container.innerHTML = '<div class="loading-state"><div class="loading-spinner"></div> 載入中...</div>';
  } else {
    showTableLoading(container);
  }
  
  try {
    const response = await fetch("/signal-configs");
    
    if (await handleFetchError(response)) return;
    
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    
    const data = await response.json();
    renderSignalConfigsTable(data);
    hideTableLoading(container);
  } catch (error) {
    console.error("載入 Signal Configs 失敗:", error);
    hideTableLoading(container);
    if (isFirstLoad) {
      container.innerHTML = `<div class="empty-state">載入失敗: ${error.message}</div>`;
    }
  }
}

// 排序狀態（Signal Configs）
let signalConfigsSortState = {
  column: null,
  direction: 'asc' // 'asc' or 'desc'
};

// 排序 Signal Configs 資料
function sortSignalConfigsData(data, column, direction) {
  if (!data || data.length === 0) return data;
  
  const sorted = [...data].sort((a, b) => {
    let aVal, bVal;
    
    switch(column) {
      case 'id':
        aVal = a.id || 0;
        bVal = b.id || 0;
        return direction === 'asc' ? aVal - bVal : bVal - aVal;
      case 'name':
        aVal = (a.name || '').toLowerCase();
        bVal = (b.name || '').toLowerCase();
        return direction === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
      case 'signal_key':
        aVal = (a.signal_key || '').toLowerCase();
        bVal = (b.signal_key || '').toLowerCase();
        return direction === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
      case 'symbol_tf':
        const aSymbolTf = [a.symbol_hint, a.timeframe_hint].filter(Boolean).join(" / ") || "";
        const bSymbolTf = [b.symbol_hint, b.timeframe_hint].filter(Boolean).join(" / ") || "";
        aVal = aSymbolTf.toLowerCase();
        bVal = bSymbolTf.toLowerCase();
        return direction === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
      case 'enabled':
        aVal = a.enabled ? 1 : 0;
        bVal = b.enabled ? 1 : 0;
        return direction === 'asc' ? aVal - bVal : bVal - aVal;
      case 'created_at':
        aVal = a.created_at ? new Date(a.created_at).getTime() : 0;
        bVal = b.created_at ? new Date(b.created_at).getTime() : 0;
        return direction === 'asc' ? aVal - bVal : bVal - aVal;
      default:
        return 0;
    }
  });
  
  return sorted;
}

// 渲染 Signal Configs 表格
function renderSignalConfigsTable(data) {
  // 更新計數
  const countSpan = document.getElementById("signal-configs-count");
  if (countSpan) {
    countSpan.textContent = data && data.length > 0 ? `(${data.length})` : '';
  }
  
  const container = document.getElementById("signal-configs-table-container");
  if (!container) {
    console.error("signal-configs-table-container not found");
    return;
  }
  
  if (!data || data.length === 0) {
    container.innerHTML = '<div class="empty-state">目前沒有 Signal Config</div>';
    return;
  }
  
  // 應用排序
  const sortedData = signalConfigsSortState.column 
    ? sortSignalConfigsData(data, signalConfigsSortState.column, signalConfigsSortState.direction)
    : data;
  
  const existingTable = container.querySelector("table");
  const existingTbody = existingTable ? existingTable.querySelector("tbody") : null;
  
  function generateSignalConfigRowHtml(config, index) {
    const rowAlt = (index % 2) === 1;
    const enabledClass = config.enabled ? "status-open" : "status-closed";
    const enabledText = config.enabled ? "啟用" : "停用";
    const symbolTf = [config.symbol_hint, config.timeframe_hint].filter(Boolean).join(" / ") || "—";
    
    const actionsHtml = `
      <div class="action-buttons">
        <button class="action-btn" onclick="openSignalFormFromEdit(${config.id})">Edit</button>
        <button class="action-btn" onclick="openBotFormFromSignal(${config.id}, '${config.signal_key}')">Create Bot</button>
        <button class="action-btn action-btn-disable" onclick="deleteSignalConfig(${config.id})">Delete</button>
      </div>
    `;
    
    return `
      <tr class="${rowAlt ? "row-alt" : ""}">
        <td><strong>${config.id || "-"}</strong></td>
        <td>${config.name || "-"}</td>
        <td><code>${config.signal_key || "-"}</code></td>
        <td>${symbolTf}</td>
        <td class="text-center"><span class="${enabledClass}">${enabledText}</span></td>
        <td>${fmtDateTime(config.created_at)}</td>
        <td class="text-center">${actionsHtml}</td>
      </tr>
    `;
  }
  
  // 生成排序指示器
  function getSortIndicator(col) {
    if (signalConfigsSortState.column === col) {
      return signalConfigsSortState.direction === 'asc' ? ' ↑' : ' ↓';
    }
    return '';
  }
  
  // 生成可排序的 th
  function generateSortableTh(col, label, className = '') {
    const isActive = signalConfigsSortState.column === col;
    const sortClass = isActive ? 'sort-active' : '';
    const indicator = getSortIndicator(col);
    return `<th class="${className} ${sortClass} sortable" data-column="${col}" style="cursor: pointer; user-select: none;">
      ${label}${indicator}
    </th>`;
  }
  
  if (existingTbody) {
    let tbodyHtml = "";
    sortedData.forEach((config, index) => {
      tbodyHtml += generateSignalConfigRowHtml(config, index);
    });
    existingTbody.innerHTML = tbodyHtml;
    
    // 重新綁定排序事件
    bindSignalConfigsSortEvents();
    return;
  }
  
  // 建立完整表格
  let html = `
    <table class="positions-table">
      <thead>
        <tr>
          ${generateSortableTh('id', 'ID', 'text-center')}
          ${generateSortableTh('name', 'Name', '')}
          ${generateSortableTh('signal_key', 'Signal Key', '')}
          ${generateSortableTh('symbol_tf', 'Symbol / TF', '')}
          ${generateSortableTh('enabled', 'Enabled', 'text-center')}
          ${generateSortableTh('created_at', 'Created', '')}
          <th class="text-center">Actions</th>
        </tr>
      </thead>
      <tbody>
  `;
  
  sortedData.forEach((config, index) => {
    html += generateSignalConfigRowHtml(config, index);
  });
  
  html += `
      </tbody>
    </table>
  `;
  
  container.innerHTML = html;
  
  // 綁定排序事件
  bindSignalConfigsSortEvents();
}

// 綁定 Signal Configs 排序事件
function bindSignalConfigsSortEvents() {
  document.querySelectorAll("#signal-configs-table-container .sortable").forEach((th) => {
    th.addEventListener("click", () => {
      const column = th.getAttribute("data-column");
      if (!column) return;
      
      // 切換排序方向
      if (signalConfigsSortState.column === column) {
        signalConfigsSortState.direction = signalConfigsSortState.direction === 'asc' ? 'desc' : 'asc';
      } else {
        signalConfigsSortState.column = column;
        signalConfigsSortState.direction = 'asc';
      }
      
      // 重新載入並渲染（會自動應用排序）
      loadSignalConfigs();
    });
  });
}

// 切換 Signal Configs Section 顯示/隱藏
function toggleSignalConfigsSection() {
  const section = document.getElementById("signal-configs-section");
  const container = document.getElementById("signal-configs-table-container");
  const toggleBtn = document.getElementById("toggle-signal-configs-section-btn");
  const formContainer = document.getElementById("signal-form-container");

  if (!section || !toggleBtn) return;
  
  const isHidden = container && container.style.display === "none";
  
  if (container) {
    container.style.display = isHidden ? "" : "none";
  }
  if (formContainer && !isHidden) {
    // 如果隱藏 section，也隱藏 form
    formContainer.style.display = "none";
  }
  
  toggleBtn.textContent = isHidden ? "▼" : "▶";
}

// 切換 Signal Logs Section 顯示/隱藏
function toggleSignalLogsSection() {
  const container = document.getElementById("signals-table-container");
  const toggleBtn = document.getElementById("toggle-signal-logs-section-btn");

  if (!container || !toggleBtn) return;
  
  const isHidden = container.style.display === "none";
  container.style.display = isHidden ? "" : "none";
  toggleBtn.textContent = isHidden ? "▼" : "▶";
}

// 切換 Signal Configs 表格顯示/隱藏（保留向後兼容）
function toggleSignalConfigsTable() {
  const container = document.getElementById("signal-configs-table-container");
  const toggleBtn = document.getElementById("toggle-signal-configs-btn");

  if (!container || !toggleBtn) return;
  
  const isVisible = container.style.display !== 'none';
  container.style.display = isVisible ? 'none' : 'block';
  toggleBtn.textContent = isVisible ? '+' : '−';
  toggleBtn.title = isVisible ? '展開' : '收合';
}

// 從 Signal 開啟 Bot 表單
async function openBotFormFromSignal(signalId, signalKey) {
  document.getElementById("bot-form-container").style.display = "block";
  resetBotForm();
  
  // 設定 signal 下拉選單
  const select = document.getElementById("bot-signal-id");
  if (select) {
    select.value = String(signalId);
  }
  
  // 預填 bot_key 和 name
  const keyInput = document.getElementById("bot-key");
  if (keyInput && !keyInput.value) {
    keyInput.value = signalKey + "_v1";
  }
  const nameInput = document.getElementById("bot-name");
  if (nameInput && !nameInput.value) {
    nameInput.value = signalKey + " Bot";
  }
  
  // 取得 signal 的詳細資料，自動填入 symbol（優先執行，確保 symbol 被正確填入）
  let symbolToFill = null;
  try {
    // 先從 cache 查找
    const cachedConfig = signalConfigsCache[signalId];
    if (cachedConfig && cachedConfig.symbol_hint) {
      symbolToFill = cachedConfig.symbol_hint.toUpperCase();
    } else {
      // 如果 cache 沒有，去 fetch 詳細資料
      const response = await fetch(`/signal-configs/${signalId}`);
      if (response.ok) {
        const signalData = await response.json();
        if (signalData.symbol_hint) {
          symbolToFill = signalData.symbol_hint.toUpperCase();
        }
        // 更新 cache
        signalConfigsCache[signalId] = signalData;
      }
    }
  } catch (error) {
    console.warn("無法取得 signal 詳細資料:", error);
  }
  
  // 填入 symbol（如果找到）
  if (symbolToFill) {
    const symbolInput = document.getElementById("bot-symbol");
    if (symbolInput) {
      symbolInput.value = symbolToFill;
      console.log(`從 signal ${signalId} 自動填入 symbol: ${symbolToFill}`);
    }
  } else {
    // 如果沒有找到 symbol_hint，嘗試觸發 change 事件（可能 change 事件會從其他地方取得）
    if (select) {
      // 使用 setTimeout 確保 change 事件處理器已經綁定
      setTimeout(() => {
        select.dispatchEvent(new Event("change", { bubbles: true }));
      }, 100);
    }
  }
  
  // 滾動到表單
  document.getElementById("bot-form-container").scrollIntoView({ behavior: "smooth", block: "nearest" });
  
  // 切換到 Bots tab
  const botsTab = document.querySelector('.tab-button[data-tab="bots"]');
  if (botsTab) {
    botsTab.click();
  }
}

// 初始化 Signal Config 表單
let currentEditingSignalId = null;

function resetSignalForm() {
  document.getElementById("signal-name").value = "";
  document.getElementById("signal-key").value = "";
  document.getElementById("signal-description").value = "";
  document.getElementById("signal-symbol-hint").value = "";
  document.getElementById("signal-timeframe-hint").value = "";
  document.getElementById("signal-enabled").checked = true;
  document.getElementById("signal-alert-template").value = "";
  
  const titleEl = document.getElementById("signal-form-title");
  if (titleEl) {
    titleEl.textContent = "Create New Signal";
  }
  const submitBtn = document.getElementById("signal-form-submit-btn");
  if (submitBtn) {
    submitBtn.disabled = false;
    submitBtn.textContent = "Save";
  }
  currentEditingSignalId = null;
  
  const err = document.getElementById("signal-form-error");
  if (err) {
    err.style.display = "none";
    err.textContent = "";
  }
}

// 全局變量：保存 TradingView Secret（從 /me API 獲取）
let tradingviewSecret = "";

function updateSignalAlertTemplate() {
  const signalKey = document.getElementById("signal-key").value;
  const templateEl = document.getElementById("signal-alert-template");
  if (!templateEl || !signalKey) {
    templateEl.value = "";
    return;
  }
  
  // 使用實際的 secret 值，如果沒有則使用占位符
  const secretValue = tradingviewSecret || "YOUR_TRADINGVIEW_SECRET";
  
  // 生成 TradingView Alert JSON 範本
  const template = `{
  "secret": "${secretValue}",
  "signal_key": "${signalKey}",
  "symbol": "{{ticker}}",
  "side": "{{strategy.order.action}}",
  "qty": "{{strategy.order.contracts}}",
  "position_size": {{strategy.position_size}},
  "time": "{{timenow}}"
}`;
  
  templateEl.value = template;
}

// 從編輯模式開啟 Signal 表單
async function openSignalFormFromEdit(signalId) {
  try {
    const response = await fetch(`/signal-configs/${signalId}`);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    
    const config = await response.json();
    
    document.getElementById("signal-form-container").style.display = "block";
    document.getElementById("signal-name").value = config.name || "";
    document.getElementById("signal-key").value = config.signal_key || "";
    document.getElementById("signal-description").value = config.description || "";
    document.getElementById("signal-symbol-hint").value = config.symbol_hint || "";
    document.getElementById("signal-timeframe-hint").value = config.timeframe_hint || "";
    document.getElementById("signal-enabled").checked = config.enabled !== false;
    
    updateSignalAlertTemplate();
    
    const titleEl = document.getElementById("signal-form-title");
    if (titleEl) {
      titleEl.textContent = `Edit Signal: ${config.name}`;
    }
    const submitBtn = document.getElementById("signal-form-submit-btn");
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = "Update";
    }
    currentEditingSignalId = signalId;
    
    // 滾動到表單
    document.getElementById("signal-form-container").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (error) {
    console.error("載入 Signal Config 失敗:", error);
    alert(`載入失敗: ${error.message}`);
  }
}

// 提交 Signal 表單
async function onSubmitSignalForm(event) {
  event.preventDefault();
  const errorEl = document.getElementById("signal-form-error");
  if (errorEl) {
    errorEl.style.display = "none";
    errorEl.textContent = "";
  }
  
  const name = document.getElementById("signal-name").value.trim();
  const signalKey = document.getElementById("signal-key").value.trim();
  const description = document.getElementById("signal-description").value.trim();
  const symbolHint = document.getElementById("signal-symbol-hint").value.trim();
  const timeframeHint = document.getElementById("signal-timeframe-hint").value.trim();
  const enabled = document.getElementById("signal-enabled").checked;
  
  if (!name || !signalKey) {
    showSignalFormError("Name 和 Signal Key 為必填欄位");
    return;
  }
  
  const payload = {
    name,
    signal_key: signalKey,
    description: description || null,
    symbol_hint: symbolHint || null,
    timeframe_hint: timeframeHint || null,
    enabled,
  };
  
  try {
    const submitBtn = event.target.querySelector('button[type="submit"]');
    const originalText = submitBtn.textContent;
    submitBtn.disabled = true;
    submitBtn.textContent = currentEditingSignalId ? "Updating..." : "Creating...";
    
    const url = currentEditingSignalId 
      ? `/signal-configs/${currentEditingSignalId}`
      : "/signal-configs";
    const method = currentEditingSignalId ? "PUT" : "POST";
    
    const resp = await fetch(url, {
      method,
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
    
    if (await handleFetchError(resp)) return;
    
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      const detail = data.detail || `操作失敗: HTTP ${resp.status}`;
      showSignalFormError(detail);
      submitBtn.disabled = false;
      submitBtn.textContent = originalText;
      return;
    }
    
    // 成功：關閉表單、重新載入列表
    document.getElementById("signal-form-container").style.display = "none";
    resetSignalForm();
    await loadSignalConfigs();
    await loadSignalConfigsForBotForm(); // 更新 Bot 表單的下拉選單
    
    alert(`Signal ${currentEditingSignalId ? "更新" : "建立"}成功！`);
  } catch (e) {
    console.error("Signal form error:", e);
    showSignalFormError(`操作失敗: ${e.message}`);
    const submitBtn = event.target.querySelector('button[type="submit"]');
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = currentEditingSignalId ? "Update" : "Save";
    }
  }
}

function showSignalFormError(msg) {
  const errorEl = document.getElementById("signal-form-error");
  if (!errorEl) return;
  errorEl.textContent = msg;
  errorEl.style.display = "block";
  errorEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// 刪除 Signal Config
async function deleteSignalConfig(signalId) {
  if (!confirm("確定要刪除此 Signal Config 嗎？如果此 Signal 下有關聯的 Bots，將無法刪除。")) {
    return;
  }
  
  try {
    const resp = await fetch(`/signal-configs/${signalId}`, {
      method: "DELETE",
    });
    
    if (await handleFetchError(resp)) return;
    
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.detail || `HTTP ${resp.status}`);
    }
    
    alert("Signal Config 已刪除");
    await loadSignalConfigs();
    await loadSignalConfigsForBotForm(); // 更新 Bot 表單的下拉選單
  } catch (error) {
    console.error("刪除 Signal Config 失敗:", error);
    alert(`刪除失敗: ${error.message}`);
  }
}

// 開啟 Signal 詳細資料 Modal
async function openSignalDetailModal(signalId) {
  try {
    const res = await fetch(`/signals/${signalId}`);
    
    if (res.status === 401 || res.status === 403) {
      window.location.href = "/auth/login/google";
      return;
    }
    
    if (!res.ok) {
      const errorData = await res.json().catch(() => ({}));
      alert(`載入 Signal 詳細資料失敗: ${errorData.detail || `HTTP ${res.status}`}`);
      return;
    }
    
    const data = await res.json();
    
    const metaEl = document.getElementById("signal-detail-meta");
    const rawEl = document.getElementById("signal-detail-raw");
    
    if (!metaEl || !rawEl) {
      console.error("找不到 modal 元素");
      return;
    }
    
    const meta = {
      id: data.id,
      bot_key: data.bot_key || null,
      signal_id: data.signal_id || null,
      symbol: data.symbol,
      side: data.side,
      qty: data.qty,
      received_at: data.received_at,
      processed: data.processed,
      process_result: data.process_result || null,
    };
    
    metaEl.textContent = JSON.stringify(meta, null, 2);
    
    // raw_payload 若為字串，盡量 pretty print，如果 parse 失敗就原樣顯示
    let rawText = data.raw_payload || "";
    try {
      if (rawText) {
        const obj = JSON.parse(rawText);
        rawText = JSON.stringify(obj, null, 2);
      }
    } catch (e) {
      // ignore, keep original
    }
    rawEl.textContent = rawText || "(no raw payload)";
    
    const modal = document.getElementById("signal-detail-modal");
    if (modal) {
      modal.classList.remove("hidden");
    }
  } catch (err) {
    console.error("openSignalDetailModal error", err);
    alert("載入 Signal 詳細資料時發生錯誤，請稍後再試");
  }
}

// 初始化 Signal Config Tab
function initSignalConfigsTab() {
  const openBtn = document.getElementById("btn-open-signal-form");
  const cancelBtn = document.getElementById("btn-cancel-signal-form");
  const formContainer = document.getElementById("signal-form-container");
  const formEl = document.getElementById("signal-create-form");
  const signalKeyInput = document.getElementById("signal-key");
  
  if (openBtn && formContainer) {
    openBtn.addEventListener("click", () => {
      formContainer.style.display = formContainer.style.display === "none" ? "block" : "block";
      resetSignalForm();
      formContainer.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  }
  
  if (cancelBtn && formContainer) {
    cancelBtn.addEventListener("click", () => {
      formContainer.style.display = "none";
      resetSignalForm();
    });
  }
  
  if (formEl) {
    formEl.addEventListener("submit", onSubmitSignalForm);
  }
  
  // 當 signal_key 改變時，更新 alert template
  if (signalKeyInput) {
    signalKeyInput.addEventListener("input", updateSignalAlertTemplate);
  }
  
  // 綁定複製按鈕
  const copyBtn = document.getElementById("btn-copy-alert-template");
  if (copyBtn) {
    copyBtn.addEventListener("click", copyAlertTemplate);
  }
}

// 複製 TradingView Alert Template 到剪貼板
function copyAlertTemplate() {
  const templateEl = document.getElementById("signal-alert-template");
  const copyBtn = document.getElementById("btn-copy-alert-template");
  const copyBtnText = document.getElementById("copy-btn-text");
  
  if (!templateEl || !templateEl.value) {
    alert("沒有可複製的內容");
    return;
  }
  
  try {
    // 使用 Clipboard API 複製
    navigator.clipboard.writeText(templateEl.value).then(() => {
      // 顯示成功反饋
      if (copyBtnText) {
        const originalText = copyBtnText.textContent;
        copyBtnText.textContent = "Copied!";
        if (copyBtn) {
          copyBtn.style.backgroundColor = "var(--tbl-positive)";
        }
        
        // 2 秒後恢復原狀
        setTimeout(() => {
          if (copyBtnText) {
            copyBtnText.textContent = originalText;
          }
          if (copyBtn) {
            copyBtn.style.backgroundColor = "";
          }
        }, 2000);
      }
    }).catch((err) => {
      // 如果 Clipboard API 失敗，使用 fallback 方法
      console.warn("Clipboard API 失敗，使用 fallback 方法:", err);
      fallbackCopyTextToClipboard(templateEl.value);
    });
  } catch (err) {
    console.error("複製失敗:", err);
    // 使用 fallback 方法
    fallbackCopyTextToClipboard(templateEl.value);
  }
}

// Fallback 複製方法（兼容舊瀏覽器）
function fallbackCopyTextToClipboard(text) {
  const textArea = document.createElement("textarea");
  textArea.value = text;
  textArea.style.position = "fixed";
  textArea.style.left = "-999999px";
  textArea.style.top = "-999999px";
  document.body.appendChild(textArea);
  textArea.focus();
  textArea.select();
  
  try {
    const successful = document.execCommand("copy");
    if (successful) {
      const copyBtn = document.getElementById("btn-copy-alert-template");
      const copyBtnText = document.getElementById("copy-btn-text");
      if (copyBtnText) {
        const originalText = copyBtnText.textContent;
        copyBtnText.textContent = "Copied!";
        if (copyBtn) {
          copyBtn.style.backgroundColor = "var(--tbl-positive)";
        }
        setTimeout(() => {
          if (copyBtnText) {
            copyBtnText.textContent = originalText;
          }
          if (copyBtn) {
            copyBtn.style.backgroundColor = "";
          }
        }, 2000);
      }
    } else {
      alert("複製失敗，請手動選擇並複製");
    }
  } catch (err) {
    console.error("Fallback 複製失敗:", err);
    alert("複製失敗，請手動選擇並複製");
  } finally {
    document.body.removeChild(textArea);
  }
}

// 載入 Bot Positions 統計數據
async function loadBotPositionsStats() {
  const statsEl = document.getElementById("bot-positions-stats");
  if (!statsEl) return;
  
  // 從 date input 讀取日期
  const startDateInput = document.getElementById("bot-start-date");
  const endDateInput = document.getElementById("bot-end-date");
  
  // 如果日期輸入框為空，設定預設值（最近 7 天）
  const today = new Date();
  const sevenDaysAgo = new Date(today);
  sevenDaysAgo.setDate(today.getDate() - 7);
  
  if (!startDateInput.value) {
    startDateInput.value = sevenDaysAgo.toISOString().split('T')[0];
  }
  if (!endDateInput.value) {
    endDateInput.value = today.toISOString().split('T')[0];
  }
  
  const startDate = startDateInput.value;
  const endDate = endDateInput.value;
  
  try {
    // 組合 query string
    const params = [];
    if (startDate) params.push(`start_date=${startDate}`);
    if (endDate) params.push(`end_date=${endDate}`);
    const queryString = params.length > 0 ? "?" + params.join("&") : "";
    
    const response = await fetch(`/bot-positions/stats${queryString}`);
    
    if (await handleFetchError(response)) return;
    
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    
    const stats = await response.json();
    renderBotStats(stats);
  } catch (error) {
    console.error("載入統計數據失敗:", error);
    if (statsEl) {
      statsEl.innerHTML = `<div class="empty-state">載入統計數據失敗: ${error.message}</div>`;
    }
  }
}

// 渲染統計資訊
function renderBotStats(stats) {
  const el = document.getElementById("bot-positions-stats");
  if (!el || !stats) {
    if (el) el.innerHTML = "";
    return;
  }
  
  const winRateStr = stats.total_trades > 0
    ? stats.win_rate.toFixed(2) + "%"
    : "—";
  
  const pnlRatioStr = stats.pnl_ratio != null
    ? stats.pnl_ratio.toFixed(2)
    : "—";
  
  // 根據數值設定顏色
  const profitClass = stats.profit_sum > 0 ? "pnl-positive" : "";
  const lossClass = stats.loss_sum < 0 ? "pnl-negative" : "";
  
  el.innerHTML = `
    <div class="bot-stat-item">Win: <strong>${stats.win_count || 0}</strong></div>
    <div class="bot-stat-item">Loss: <strong>${stats.loss_count || 0}</strong></div>
    <div class="bot-stat-item">Total Trades: <strong>${stats.total_trades || 0}</strong></div>
    <div class="bot-stat-item">Win Rate: <strong>${winRateStr}</strong></div>
    <div class="bot-stat-item">Profit: <strong class="${profitClass}">${(stats.profit_sum || 0).toFixed(4)}</strong></div>
    <div class="bot-stat-item">Loss: <strong class="${lossClass}">${(stats.loss_sum || 0).toFixed(4)}</strong></div>
    <div class="bot-stat-item">PnL Ratio: <strong>${pnlRatioStr}</strong></div>
  `;
}

// 確保函數在全局作用域中可用（用於 onclick 事件）
window.openStopConfigModal = openStopConfigModal;
window.closeStopConfigModal = closeStopConfigModal;
window.saveStopConfig = saveStopConfig;

// 頁面載入完成後執行
document.addEventListener("DOMContentLoaded", function() {
  // 先載入使用者資訊
  loadUserInfo();

  // 載入倉位資料
  loadPositions();
  // 載入 Portfolio Trailing Stop 設定（包括配置輸入欄位）- 必須在 loadBinancePositions 之前，確保配置先載入
  loadPortfolioSummary();
  loadBinancePositions();
  loadSignals();
  loadSignalConfigs();  // 載入 Signal Configs
  loadBots();
  loadTrailingSettings();  // 載入 Trailing 設定
  loadBotPositionsStats();  // 載入統計數據
  
  // 初始化 Signal Detail Modal 關閉按鈕
  const modalCloseBtn = document.getElementById("signal-detail-close");
  const modal = document.getElementById("signal-detail-modal");
  if (modalCloseBtn && modal) {
    modalCloseBtn.addEventListener("click", () => {
      modal.classList.add("hidden");
    });
    
    // 點擊背景關閉 modal
    modal.addEventListener("click", (e) => {
      if (e.target.id === "signal-detail-modal") {
        modal.classList.add("hidden");
      }
    });
  }
  
  // 初始化 Bots Tab（包含表單）
  initBotsTab();
  
  // 初始化 Signal Configs Tab
  initSignalConfigsTab();
  
  // 綁定清除 Signal Logs 按鈕
  document.getElementById("clear-signal-logs-btn")?.addEventListener("click", () => {
    clearAllSignalLogs();
  });
  
  // 綁定切換 Signal Logs 顯示/隱藏按鈕
  document.getElementById("toggle-signals-btn")?.addEventListener("click", () => {
    toggleSignalsTable();
  });
  
  // 綁定切換 Signal Configs Section 顯示/隱藏按鈕
  document.getElementById("toggle-signal-configs-section-btn")?.addEventListener("click", () => {
    toggleSignalConfigsSection();
  });
  
  // 綁定切換 Signal Logs Section 顯示/隱藏按鈕
  document.getElementById("toggle-signal-logs-section-btn")?.addEventListener("click", () => {
    toggleSignalLogsSection();
  });
  
  // 保留舊的 toggle-signal-configs-btn 以向後兼容（如果存在）
  document.getElementById("toggle-signal-configs-btn")?.addEventListener("click", () => {
    toggleSignalConfigsTable();
  });
  
  // 綁定篩選和刪除按鈕事件
  document.getElementById("apply-filter-btn")?.addEventListener("click", () => {
    loadPositions();
  });
  
  document.getElementById("clear-filter-btn")?.addEventListener("click", () => {
    clearFilters();
  });
  
  document.getElementById("delete-old-positions-btn")?.addEventListener("click", () => {
    deleteOldPositions();
  });
  
  document.getElementById("clear-error-positions-btn")?.addEventListener("click", () => {
    clearErrorPositions();
  });
  
  // 刪除舊倉位記錄
  async function deleteOldPositions() {
    const daysInput = document.getElementById("delete-days");
    if (!daysInput) return;
    
    const days = parseInt(daysInput.value);
    if (isNaN(days) || days < 1 || days > 365) {
      alert("請輸入有效的天數（1-365）");
      return;
    }
    
    const includeErrorEl = document.getElementById("delete-include-error");
    const includeError = (includeErrorEl && includeErrorEl.checked) ? includeErrorEl.checked : false;
    const confirmMsg = includeError 
      ? `確定要刪除 ${days} 天前關閉的倉位記錄和所有 ERROR 狀態的倉位嗎？此操作無法復原。`
      : `確定要刪除 ${days} 天前關閉的倉位記錄嗎？此操作無法復原。`;
    
    if (!confirm(confirmMsg)) {
      return;
    }
    
    try {
      const url = `/admin/positions/prune-closed?days=${days}&include_error=${includeError}`;
      const response = await fetch(url, {
        method: "DELETE",
      });
      
      if (await handleFetchError(response)) return;
      
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${response.status}`);
      }
      
      const result = await response.json();
      alert(`成功刪除 ${result.deleted} 筆倉位記錄`);
      loadPositions(); // 重新載入列表
    } catch (error) {
      console.error("刪除舊倉位記錄失敗:", error);
      alert(`刪除失敗: ${error.message}`);
    }
  }
  
  // 清除所有 ERROR 狀態的倉位
  async function clearErrorPositions() {
    if (!confirm("確定要清除所有 ERROR 狀態的倉位記錄嗎？此操作無法復原。")) {
      return;
    }
    
    try {
      const response = await fetch("/admin/positions/clear-error", {
        method: "DELETE",
      });
      
      if (await handleFetchError(response)) return;
      
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${response.status}`);
      }
      
      const result = await response.json();
      alert(`成功清除 ${result.deleted} 筆 ERROR 狀態的倉位記錄`);
      loadPositions(); // 重新載入列表
    } catch (error) {
      console.error("清除 ERROR 倉位記錄失敗:", error);
      alert(`清除失敗: ${error.message}`);
    }
  }
  
  // 綁定 Bot Positions 的篩選和匯出按鈕事件
  
  document.getElementById("bot-apply-filter")?.addEventListener("click", () => {
    loadBotPositionsStats();
  });
  
  document.getElementById("bot-export-excel")?.addEventListener("click", () => {
    const startDateInput = document.getElementById("bot-start-date");
    const endDateInput = document.getElementById("bot-end-date");

    const start = (startDateInput && startDateInput.value) ? startDateInput.value : "";
    const end = (endDateInput && endDateInput.value) ? endDateInput.value : "";
    
    let url = "/bot-positions/export";
    const params = [];
    if (start) params.push(`start_date=${start}`);
    if (end) params.push(`end_date=${end}`);
    if (params.length > 0) {
      url += "?" + params.join("&");
    }
    
    // 直接觸發下載
    window.location.href = url;
  });
  
  // Tab 切換邏輯
  const tabButtons = document.querySelectorAll(".tab-button");
  tabButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const tab = btn.dataset.tab;
      
      // 更新按鈕狀態
      tabButtons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      
      // 更新容器顯示
      document
        .querySelectorAll(".positions-container")
        .forEach((c) => c.classList.remove("active"));
      
      if (tab === "bot") {
        document
          .getElementById("bot-positions-container")
          .classList.add("active");
      } else if (tab === "binance") {
        document
          .getElementById("binance-positions-container")
          .classList.add("active");
            } else if (tab === "signals") {
              document
                .getElementById("signals-container")
                .classList.add("active");
              loadSignalConfigs();  // 載入 Signal Configs
            } else if (tab === "bots") {
        document
          .getElementById("bots-container")
          .classList.add("active");
        // 確保 Bots tab 初始化（如果還沒初始化）
        if (document.getElementById("bot-create-form") && !document.getElementById("bot-create-form").hasAttribute("data-initialized")) {
          initBotsTab();
          document.getElementById("bot-create-form").setAttribute("data-initialized", "true");
        }
      }
    });
  });
  
  // 設定登出按鈕
  const logoutBtn = document.getElementById("logout-btn");
  if (logoutBtn) {
    logoutBtn.addEventListener("click", handleLogout);
  }
  
  // 設定 Trailing Settings Save 按鈕
  const saveTrailingBtn = document.getElementById("save-trailing-settings");
  if (saveTrailingBtn) {
    saveTrailingBtn.addEventListener("click", saveTrailingSettings);
  }
  
  // 設定 Portfolio Controls 按鈕
  const closeAllBtn = document.getElementById("close-all-positions-btn");
  if (closeAllBtn) {
    closeAllBtn.addEventListener("click", closeAllBinancePositions);
  }
  
  // LONG Portfolio Trailing Config buttons
  const savePortfolioTrailingBtnLong = document.getElementById("save-portfolio-trailing-btn-long");
  if (savePortfolioTrailingBtnLong) {
    savePortfolioTrailingBtnLong.addEventListener("click", () => savePortfolioTrailingConfig("long"));
  }

  const resetMaxPnlBtnLong = document.getElementById("reset-max-pnl-btn-long");
  if (resetMaxPnlBtnLong) {
    resetMaxPnlBtnLong.addEventListener("click", () => resetMaxPnlReached("long"));
  }

  // SHORT Portfolio Trailing Config buttons
  const savePortfolioTrailingBtnShort = document.getElementById("save-portfolio-trailing-btn-short");
  if (savePortfolioTrailingBtnShort) {
    savePortfolioTrailingBtnShort.addEventListener("click", () => savePortfolioTrailingConfig("short"));
  }

  const resetMaxPnlBtnShort = document.getElementById("reset-max-pnl-btn-short");
  if (resetMaxPnlBtnShort) {
    resetMaxPnlBtnShort.addEventListener("click", () => resetMaxPnlReached("short"));
  }
  
  // 設定 Symbol 連結點擊事件（使用事件委派，因為表格會動態更新）
  document.addEventListener("click", function(e) {
    if (e.target.classList.contains("symbol-link") || e.target.closest(".symbol-link")) {
      e.preventDefault();
      const link = e.target.classList.contains("symbol-link") ? e.target : e.target.closest(".symbol-link");
      const symbol = link.getAttribute("data-symbol");
      if (symbol && symbol !== "-" && symbol !== "") {
        openTradingViewChart(symbol);
      }
    }
  });
  
  // 自動刷新功能（可調整間隔）
  let refreshIntervalId = null;
  
  // 從 localStorage 讀取保存的刷新間隔，預設 5000ms
  function getRefreshInterval() {
    const saved = localStorage.getItem("dashboard_refresh_interval");
    return saved ? parseInt(saved, 10) : 5000;
  }
  
  // 設定刷新間隔
  function setRefreshInterval(intervalMs) {
    // 清除現有的 interval
    if (refreshIntervalId) {
      clearInterval(refreshIntervalId);
    }
    
    // 保存到 localStorage
    localStorage.setItem("dashboard_refresh_interval", intervalMs.toString());
    
    // 設定新的 interval
    refreshIntervalId = setInterval(() => {
      // 只重新載入當前顯示的 tab
      const activeTab = document.querySelector(".tab-button.active");
      if (activeTab) {
        const tab = activeTab.dataset.tab;
        if (tab === "bot") {
          loadPositions();
        } else if (tab === "binance") {
          loadBinancePositions();
        } else if (tab === "signals") {
          loadSignals();
          loadSignalConfigs();  // 載入 Signal Configs
        } else if (tab === "bots") {
          loadBots();
        }
      }
    }, intervalMs);
  }
  
  // 初始化刷新間隔選擇器
  const refreshIntervalSelect = document.getElementById("refresh-interval");
  if (refreshIntervalSelect) {
    // 設定當前值
    const currentInterval = getRefreshInterval();
    refreshIntervalSelect.value = currentInterval.toString();
    
    // 監聽變化
    refreshIntervalSelect.addEventListener("change", function() {
      const newInterval = parseInt(this.value, 10);
      setRefreshInterval(newInterval);
    });
  }
  
  // 啟動自動刷新
  setRefreshInterval(getRefreshInterval());
});

