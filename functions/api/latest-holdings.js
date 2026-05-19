const HOLDINGS_URL = "https://companiesmarketcap.com/eur/spdr-sp-500-etf/holdings/";

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

function decodeEntities(value) {
  return value
    .replaceAll("&nbsp;", " ")
    .replaceAll("&amp;", "&")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&quot;", '"')
    .replaceAll("&#39;", "'")
    .replace(/&#(\d+);/g, (_, code) => String.fromCharCode(Number(code)));
}

function stripTags(html) {
  return decodeEntities(html.replace(/<script[\s\S]*?<\/script>/gi, "")
    .replace(/<style[\s\S]*?<\/style>/gi, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim());
}

function cellValues(rowHtml) {
  return [...rowHtml.matchAll(/<t[dh][^>]*>([\s\S]*?)<\/t[dh]>/gi)]
    .map((match) => stripTags(match[1]));
}

function parseWeight(value) {
  const parsed = Number(String(value).replace("%", "").replace(",", "").trim());
  return Number.isFinite(parsed) ? parsed : NaN;
}

function normalizeTicker(value) {
  return String(value || "").trim().replaceAll(".", "-");
}

function isStockLike(row) {
  const name = String(row.description || "").toUpperCase();
  const ticker = String(row.ticker || "").toUpperCase();
  return ticker
    && ticker !== "-"
    && ticker !== "USD"
    && Number.isFinite(row.weight)
    && name !== "US DOLLAR"
    && !/(CASH|EQUIVALENTS|COLLATERAL|FUTURE|INDEX|CONTRA)/i.test(name);
}

function parseHoldings(html) {
  const tableMatch = html.match(/<table[\s\S]*?<\/table>/i);
  if (!tableMatch) {
    throw new Error("Holdings table not found");
  }

  const rows = [...tableMatch[0].matchAll(/<tr[\s\S]*?<\/tr>/gi)]
    .map((match) => cellValues(match[0]))
    .filter((cells) => cells.length >= 3);
  if (!rows.length) {
    throw new Error("Holdings rows not found");
  }

  const header = rows[0].map((cell) => cell.toLowerCase());
  let tickerIndex = header.findIndex((cell) => cell.includes("ticker"));
  let nameIndex = header.findIndex((cell) => cell.includes("name"));
  let weightIndex = header.findIndex((cell) => cell.includes("weight"));
  let dataRows = rows.slice(1);

  if (tickerIndex === -1 || nameIndex === -1 || weightIndex === -1) {
    // CompaniesMarketCap often omits the table header in the server-rendered
    // HTML. In that shape the columns are: weight, name, ticker, shares.
    weightIndex = 0;
    nameIndex = 1;
    tickerIndex = 2;
    dataRows = rows;
  }

  const seen = new Set();
  const holdings = [];
  for (const cells of dataRows) {
    const row = {
      ticker: normalizeTicker(cells[tickerIndex]),
      description: cells[nameIndex] || "",
      weight: parseWeight(cells[weightIndex]),
    };
    if (!isStockLike(row) || seen.has(row.ticker)) {
      continue;
    }
    seen.add(row.ticker);
    holdings.push(row);
  }

  return holdings.sort((a, b) => b.weight - a.weight);
}

export async function onRequestGet() {
  try {
    const response = await fetch(HOLDINGS_URL, {
      headers: {
        "user-agent": "Mozilla/5.0",
        "accept": "text/html",
      },
    });
    if (!response.ok) {
      throw new Error(`companiesmarketcap returned ${response.status}`);
    }
    const html = await response.text();
    return json({
      holdings: parseHoldings(html),
      source: HOLDINGS_URL,
      fetchedAt: new Date().toISOString(),
    });
  } catch (error) {
    return json({ holdings: [], error: error.message }, 502);
  }
}
