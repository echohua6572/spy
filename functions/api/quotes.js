const YAHOO_SPARK_URL = "https://query1.finance.yahoo.com/v7/finance/spark";

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

function chunk(values, size) {
  const chunks = [];
  for (let i = 0; i < values.length; i += size) {
    chunks.push(values.slice(i, i + size));
  }
  return chunks;
}

async function fetchQuoteChunk(symbols) {
  const url = new URL(YAHOO_SPARK_URL);
  url.searchParams.set("symbols", symbols.join(","));
  url.searchParams.set("range", "5d");
  url.searchParams.set("interval", "1d");

  const response = await fetch(url, {
    headers: {
      "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "accept": "application/json,text/plain,*/*",
      "accept-language": "en-US,en;q=0.9",
      "origin": "https://finance.yahoo.com",
      "referer": "https://finance.yahoo.com/",
    },
  });
  if (!response.ok) {
    throw new Error(`Yahoo returned ${response.status}`);
  }
  return response.json();
}

export async function onRequestPost({ request }) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "Invalid JSON body" }, 400);
  }

  const symbols = Array.isArray(body.symbols)
    ? [...new Set(body.symbols.map((item) => String(item).trim()).filter(Boolean))]
    : [];
  if (!symbols.length || symbols.length > 600) {
    return json({ error: "symbols must contain 1-600 tickers" }, 400);
  }

  const quotes = {};
  const failures = [];

  for (const group of chunk(symbols, 20)) {
    try {
      const payload = await fetchQuoteChunk(group);
      for (const item of payload?.spark?.result || []) {
        const symbol = item.symbol;
        const response = item.response?.[0];
        const meta = response?.meta || {};
        let price = meta.regularMarketPrice;
        if (price == null) {
          const closes = response?.indicators?.quote?.[0]?.close || [];
          price = [...closes].reverse().find((value) => value != null);
        }
        if (symbol && price != null) {
          quotes[symbol] = {
            price: Number(price),
            marketTime: meta.regularMarketTime || null,
            currency: meta.currency || "USD",
          };
        }
      }
    } catch (error) {
      failures.push({ symbols: group, error: error.message });
    }
  }

  return json({ quotes, failures });
}

export async function onRequestGet() {
  return json({ error: "Use POST with { symbols: [...] }" }, 405);
}
