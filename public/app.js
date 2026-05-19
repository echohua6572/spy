const DATA_PATHS = {
  prices: "/data/spy_holdings_prices.csv",
  holdings: "/data/spy_current_stock_holdings.csv",
};

const TRADE_COST = 0.001;
const MOMENTUM_WINDOWS = [21, 63, 126, 252];

const state = {
  priceData: null,
  cachedHoldings: [],
  latestHoldings: [],
  holdingsError: null,
  quotes: {},
  quoteRefreshedAt: null,
};

const $ = (id) => document.getElementById(id);

function normalizeTicker(value) {
  return String(value || "").trim().replaceAll(".", "-");
}

function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let inQuotes = false;

  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    const next = text[i + 1];
    if (char === '"') {
      if (inQuotes && next === '"') {
        field += '"';
        i += 1;
      } else {
        inQuotes = !inQuotes;
      }
    } else if (char === "," && !inQuotes) {
      row.push(field);
      field = "";
    } else if ((char === "\n" || char === "\r") && !inQuotes) {
      if (char === "\r" && next === "\n") {
        i += 1;
      }
      row.push(field);
      if (row.some((cell) => cell !== "")) {
        rows.push(row);
      }
      row = [];
      field = "";
    } else {
      field += char;
    }
  }

  if (field || row.length) {
    row.push(field);
    if (row.some((cell) => cell !== "")) {
      rows.push(row);
    }
  }
  return rows;
}

function parsePrices(text) {
  const rows = parseCsv(text);
  const symbols = rows[0].slice(1).map(normalizeTicker);
  const values = new Map(symbols.map((symbol) => [symbol, []]));
  const dates = [];

  for (const row of rows.slice(1)) {
    dates.push(row[0]);
    symbols.forEach((symbol, index) => {
      const raw = row[index + 1];
      const value = raw === undefined || raw === "" ? NaN : Number(raw);
      values.get(symbol).push(Number.isFinite(value) ? value : NaN);
    });
  }

  return { dates, symbols, values };
}

function parseHoldings(text) {
  const rows = parseCsv(text);
  const headers = rows[0].map((header) => header.trim());
  return rows.slice(1).map((row) => {
    const item = {};
    headers.forEach((header, index) => {
      item[header] = row[index] ?? "";
    });
    return {
      ticker: normalizeTicker(item.Ticker),
      description: item.Description || item.Name || "",
      sector: item.Sector || "Unknown",
      weight: Number(String(item.weight || item["Weight %"] || "0").replace("%", "")) || 0,
    };
  }).filter((item) => item.ticker);
}

async function fetchText(path) {
  const response = await fetch(`${path}?v=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`${path} returned ${response.status}`);
  }
  return response.text();
}

async function loadData() {
  const [priceText, holdingsText] = await Promise.all([
    fetchText(DATA_PATHS.prices),
    fetchText(DATA_PATHS.holdings),
  ]);
  state.priceData = parsePrices(priceText);
  state.cachedHoldings = parseHoldings(holdingsText);
}

async function fetchLatestHoldings() {
  state.holdingsError = null;
  try {
    const response = await fetch(`/api/latest-holdings?t=${Date.now()}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok || !Array.isArray(payload.holdings)) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    state.latestHoldings = payload.holdings.map((item) => ({
      ticker: normalizeTicker(item.ticker || item.Ticker),
      description: item.description || item.Description || item.name || "",
      sector: item.sector || "Unknown",
      weight: Number(item.weight) || 0,
    })).filter((item) => item.ticker);
  } catch (error) {
    state.latestHoldings = [];
    state.holdingsError = error.message;
  }
}

function applyHoldingsUniverse() {
  const weightFilter = Number($("weight-filter-select").value);
  const cachedByTicker = new Map(state.cachedHoldings.map((item) => [item.ticker, item]));
  const latestByTicker = new Map();

  for (const item of state.latestHoldings) {
    const cached = cachedByTicker.get(item.ticker);
    latestByTicker.set(item.ticker, {
      ...item,
      sector: cached?.sector || item.sector || "Unknown",
      description: item.description || cached?.description || "",
    });
  }

  const usingLatest = latestByTicker.size > 0;
  const holdingsMap = usingLatest ? latestByTicker : cachedByTicker;
  const cachedSymbols = new Set(cachedByTicker.keys());
  const latestSymbols = new Set(latestByTicker.keys());
  let holdings = Array.from(holdingsMap.values());

  if (weightFilter > 0) {
    holdings = holdings.filter((item) => item.weight > weightFilter);
  }

  const holdingSymbols = new Set(holdings.map((item) => item.ticker));
  const priceSymbols = state.priceData.symbols.filter((symbol) => holdingSymbols.has(symbol));
  const missingHistory = holdings
    .map((item) => item.ticker)
    .filter((ticker) => !state.priceData.values.has(ticker));

  return {
    holdings,
    holdingByTicker: new Map(holdings.map((item) => [item.ticker, item])),
    symbols: priceSymbols,
    missingHistory,
    added: usingLatest ? [...latestSymbols].filter((symbol) => !cachedSymbols.has(symbol)) : [],
    removed: usingLatest ? [...cachedSymbols].filter((symbol) => !latestSymbols.has(symbol)) : [],
    source: usingLatest ? "实时检查持仓" : "本地缓存持仓",
  };
}

async function refreshQuotes(symbols) {
  $("refresh-quotes-button").disabled = true;
  $("refresh-quotes-button").textContent = "正在刷新...";
  try {
    const response = await fetch("/api/quotes", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ symbols }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    state.quotes = payload.quotes || {};
    state.quoteRefreshedAt = new Date();
  } catch (error) {
    addNotice("danger", `最新报价刷新失败，暂时使用历史缓存价格：${error.message}`);
    state.quotes = {};
    state.quoteRefreshedAt = new Date();
  } finally {
    $("refresh-quotes-button").disabled = false;
    $("refresh-quotes-button").textContent = "刷新最新报价";
  }
}

function buildLivePrices(symbols) {
  const base = state.priceData;
  const dates = [...base.dates];
  const quoteTimes = Object.values(state.quotes)
    .map((quote) => quote.marketTime)
    .filter(Boolean);
  const quoteDate = quoteTimes.length
    ? new Date(Math.max(...quoteTimes) * 1000).toISOString().slice(0, 10)
    : dates[dates.length - 1];

  let liveIndex = dates.indexOf(quoteDate);
  if (liveIndex === -1) {
    dates.push(quoteDate);
    liveIndex = dates.length - 1;
  }

  const values = new Map();
  for (const symbol of symbols) {
    const source = base.values.get(symbol) || [];
    const series = [...source];
    while (series.length < dates.length) {
      series.push(series[series.length - 1] ?? NaN);
    }
    const quote = state.quotes[symbol];
    if (quote && Number.isFinite(quote.price)) {
      series[liveIndex] = quote.price;
    }
    values.set(symbol, series);
  }

  return { dates, symbols, values, quoteDate };
}

function lastMonthEndIndex(dates) {
  const monthToIndex = new Map();
  dates.forEach((date, index) => monthToIndex.set(date.slice(0, 7), index));
  const indices = [...monthToIndex.values()];
  if (indices.length > 1) {
    const lastIndex = indices[indices.length - 1];
    const day = Number(dates[lastIndex].slice(8, 10));
    if (lastIndex === dates.length - 1 && day < 25) {
      indices.pop();
    }
  }
  return indices[indices.length - 1];
}

function mean(values) {
  const clean = values.filter(Number.isFinite);
  return clean.reduce((sum, value) => sum + value, 0) / clean.length;
}

function std(values) {
  const clean = values.filter(Number.isFinite);
  if (clean.length <= 1) return NaN;
  const avg = mean(clean);
  const variance = clean.reduce((sum, value) => sum + (value - avg) ** 2, 0) / (clean.length - 1);
  return Math.sqrt(variance);
}

function valueAt(livePrices, symbol, index) {
  const series = livePrices.values.get(symbol);
  return series ? series[index] : NaN;
}

function volatility63(series, index) {
  if (index < 63) return NaN;
  const returns = [];
  for (let i = index - 62; i <= index; i += 1) {
    const previous = series[i - 1];
    const current = series[i];
    if (!Number.isFinite(previous) || !Number.isFinite(current) || previous === 0) {
      return NaN;
    }
    returns.push(current / previous - 1);
  }
  return std(returns) * Math.sqrt(252);
}

function compositeMomentum(livePrices, holdingByTicker, dateIndex, topN, sectorCap) {
  if (dateIndex < 253) return [];
  const raw = [];

  for (const symbol of livePrices.symbols) {
    const series = livePrices.values.get(symbol);
    const current = series[dateIndex];
    if (!Number.isFinite(current) || current <= 0) continue;

    const row = { symbol };
    let complete = true;
    for (const window of MOMENTUM_WINDOWS) {
      const past = series[dateIndex - window];
      if (!Number.isFinite(past) || past <= 0) {
        complete = false;
        break;
      }
      row[`mom_${window}`] = current / past - 1;
    }
    row.vol_63 = volatility63(series, dateIndex);
    if (!complete || !Number.isFinite(row.vol_63)) continue;
    raw.push(row);
  }

  if (raw.length < topN) return [];

  for (const window of MOMENTUM_WINDOWS) {
    const key = `mom_${window}`;
    const avg = mean(raw.map((row) => row[key]));
    const deviation = std(raw.map((row) => row[key]));
    for (const row of raw) {
      row.score = (row.score || 0) + (deviation ? (row[key] - avg) / deviation : 0);
    }
  }

  const volAvg = mean(raw.map((row) => row.vol_63));
  const volStd = std(raw.map((row) => row.vol_63));
  for (const row of raw) {
    row.score -= volStd ? 0.5 * ((row.vol_63 - volAvg) / volStd) : 0;
  }

  raw.sort((a, b) => b.score - a.score);
  const selected = [];
  const sectorCounts = new Map();

  for (const row of raw) {
    const sector = holdingByTicker.get(row.symbol)?.sector || "Unknown";
    if (sector !== "Unknown" && sectorCap > 0) {
      const count = sectorCounts.get(sector) || 0;
      if (count >= sectorCap) continue;
      sectorCounts.set(sector, count + 1);
    }
    selected.push({ ...row, sector, rank: selected.length + 1 });
    if (selected.length >= topN) break;
  }

  return selected;
}

function buildPositions(scores, livePrices, holdingByTicker, dateIndex, capital, topN) {
  const targetValue = capital / topN;
  let invested = 0;
  const rows = scores.map((row) => {
    const price = valueAt(livePrices, row.symbol, dateIndex);
    const shares = price > 0 ? Math.floor(targetValue / price) : 0;
    const actual = shares * price;
    invested += actual;
    const holding = holdingByTicker.get(row.symbol) || {};
    return {
      rank: row.rank,
      symbol: row.symbol,
      name: holding.description || "",
      sector: row.sector,
      price,
      targetWeight: 1 / topN,
      targetValue,
      shares,
      actual,
      gap: targetValue - actual,
      score: row.score,
      mom21: row.mom_21,
      mom63: row.mom_63,
      mom126: row.mom_126,
      mom252: row.mom_252,
      vol63: row.vol_63,
      spyWeight: (holding.weight || 0) / 100,
    };
  });
  return { rows, invested, cash: capital - invested, targetValue };
}

function formatCurrency(value, decimals = 0) {
  return Number(value || 0).toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: decimals,
    minimumFractionDigits: decimals,
  });
}

function formatPercent(value) {
  return Number(value).toLocaleString("en-US", {
    style: "percent",
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });
}

function formatNumber(value, decimals = 2) {
  return Number(value || 0).toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function setMetric(id, value) {
  $(id).textContent = value;
}

function clearNotices() {
  $("notice-stack").innerHTML = "";
}

function addNotice(type, text) {
  const node = document.createElement("div");
  node.className = `notice ${type}`;
  node.textContent = text;
  $("notice-stack").append(node);
}

function renderTable(tableId, rows) {
  const table = $(tableId);
  const headers = [
    "排名", "代码", "名称", "行业", "价格", "目标权重", "目标金额", "建议股数",
    "实际金额", "剩余差额", "综合分", "1M动量", "3M动量", "6M动量", "12M动量",
    "63日波动率", "SPY权重",
  ];

  table.innerHTML = `
    <thead><tr>${headers.map((header) => `<th>${header}</th>`).join("")}</tr></thead>
    <tbody></tbody>
  `;
  const body = table.querySelector("tbody");

  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${row.rank}</td>
      <td><span class="symbol">${row.symbol}</span></td>
      <td>${row.name}</td>
      <td>${row.sector}</td>
      <td>${formatCurrency(row.price, 2)}</td>
      <td>${formatPercent(row.targetWeight)}</td>
      <td>${formatCurrency(row.targetValue)}</td>
      <td>${row.shares}</td>
      <td>${formatCurrency(row.actual)}</td>
      <td>${formatCurrency(row.gap)}</td>
      <td>${formatNumber(row.score)}</td>
      <td class="${row.mom21 >= 0 ? "positive" : "negative"}">${formatPercent(row.mom21)}</td>
      <td class="${row.mom63 >= 0 ? "positive" : "negative"}">${formatPercent(row.mom63)}</td>
      <td class="${row.mom126 >= 0 ? "positive" : "negative"}">${formatPercent(row.mom126)}</td>
      <td class="${row.mom252 >= 0 ? "positive" : "negative"}">${formatPercent(row.mom252)}</td>
      <td>${formatPercent(row.vol63)}</td>
      <td>${formatPercent(row.spyWeight)}</td>
    `;
    body.append(tr);
  }
}

function renderApp() {
  clearNotices();
  const universe = applyHoldingsUniverse();
  const livePrices = buildLivePrices(universe.symbols);
  const topN = Number($("top-n-select").value);
  const sectorCap = Number($("sector-cap-select").value);
  const capital = Number($("capital-input").value) || 100000;
  const rebalanceIndex = lastMonthEndIndex(livePrices.dates);
  const latestIndex = livePrices.dates.length - 1;
  const rebalanceScores = compositeMomentum(livePrices, universe.holdingByTicker, rebalanceIndex, topN, sectorCap);
  const latestScores = compositeMomentum(livePrices, universe.holdingByTicker, latestIndex, topN, sectorCap);
  const monthly = buildPositions(rebalanceScores, livePrices, universe.holdingByTicker, rebalanceIndex, capital, topN);
  const today = buildPositions(latestScores, livePrices, universe.holdingByTicker, latestIndex, capital, topN);

  setMetric("rebalance-date", livePrices.dates[rebalanceIndex]);
  setMetric("quote-date", livePrices.quoteDate);
  setMetric("quote-count", `${Object.keys(state.quotes).length} / ${universe.symbols.length}`);
  setMetric("cache-date", state.priceData.dates[state.priceData.dates.length - 1]);
  setMetric("refresh-time", state.quoteRefreshedAt ? state.quoteRefreshedAt.toLocaleString("zh-CN") : "--");
  setMetric("pool-source", `股票池来源：${universe.source}`);
  setMetric("pool-count", `SPY 持仓数量：${universe.holdings.length}`);
  setMetric("price-count", `可计算历史价格数量：${universe.symbols.length}`);

  if (state.holdingsError) {
    addNotice("warning", `实时持仓检查失败，当前使用本地缓存持仓：${state.holdingsError}`);
  } else if (universe.added.length || universe.removed.length) {
    addNotice(
      "warning",
      `检测到 SPY 持仓变化。新增：${universe.added.join(", ") || "无"}；移除：${universe.removed.join(", ") || "无"}。`,
    );
  } else {
    addNotice("success", "已检查 SPY 持仓：未发现变化。");
  }

  if (universe.missingHistory.length) {
    addNotice(
      "warning",
      `${universe.missingHistory.length} 只当前持仓缺少历史价格缓存，暂不参与动量排名：${universe.missingHistory.slice(0, 12).join(", ")}`,
    );
  }

  setMetric("monthly-invested", formatCurrency(monthly.invested));
  setMetric("monthly-cash", formatCurrency(monthly.cash));
  setMetric("monthly-target", formatCurrency(monthly.targetValue));
  setMetric("today-invested", formatCurrency(today.invested));
  setMetric("today-cash", formatCurrency(today.cash));
  setMetric("today-target", formatCurrency(today.targetValue));

  renderTable("monthly-table", monthly.rows);
  renderTable("today-table", today.rows);
}

async function triggerHistoryUpdate() {
  $("trigger-update-button").disabled = true;
  $("trigger-update-button").textContent = "正在触发...";
  $("update-result").textContent = "";
  try {
    const response = await fetch("/api/dispatch-update", { method: "POST" });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    $("update-result").textContent = payload.message || "已触发 GitHub Actions 后台更新。";
  } catch (error) {
    $("update-result").textContent = `触发失败：${error.message}`;
  } finally {
    $("trigger-update-button").disabled = false;
    $("trigger-update-button").textContent = "触发后台更新历史缓存";
  }
}

function bindEvents() {
  for (const id of ["capital-input", "top-n-select", "sector-cap-select", "weight-filter-select"]) {
    $(id).addEventListener("change", renderApp);
  }

  $("refresh-quotes-button").addEventListener("click", async () => {
    const universe = applyHoldingsUniverse();
    await refreshQuotes(universe.symbols);
    renderApp();
  });

  $("refresh-holdings-button").addEventListener("click", async () => {
    $("refresh-holdings-button").disabled = true;
    $("refresh-holdings-button").textContent = "正在检查...";
    await fetchLatestHoldings();
    $("refresh-holdings-button").disabled = false;
    $("refresh-holdings-button").textContent = "重新检查 SPY 持仓";
    renderApp();
  });

  $("trigger-update-button").addEventListener("click", triggerHistoryUpdate);

  document.querySelectorAll(".tab-button").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".tab-button").forEach((item) => item.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      $(`${button.dataset.tab}-panel`).classList.add("active");
    });
  });
}

async function init() {
  bindEvents();
  try {
    await loadData();
    await fetchLatestHoldings();
    const universe = applyHoldingsUniverse();
    await refreshQuotes(universe.symbols);
    renderApp();
  } catch (error) {
    addNotice("danger", `页面初始化失败：${error.message}`);
  }
}

init();
