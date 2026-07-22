/* AI Trading Advisor — clean front end. Talks to the FastAPI backend at /api. */
"use strict";

const $ = (id) => document.getElementById(id);
const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const inr2 = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 });
const usd2 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
const isINR = (sym) => /\.(NS|BO)$/i.test(sym || "");
// The backend converts every USD-priced asset to ₹ at the data layer, so ALL
// numbers arriving here are already rupees. The browser never converts —
// except live Binance WebSocket ticks (raw USD), scaled by the payload fx_rate.
let lastCur = "INR", lastFx = null;
const fmt = (v) => (lastCur === "USD" ? usd2 : inr2).format(v);
const money = (_sym, v) => fmt(v);
const n = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v)) ? "–" : Number(v).toFixed(d);
const col = (v, hi = 60, lo = 40) => v >= hi ? "#22c55e" : v <= lo ? "#ef4444" : "#f59e0b";

let last = null, chartTF = "1y", charts = { main: null, rsi: null, eq: null };
// (live streaming state declared below)
let liveSeries = null, liveBar = null;   // for live-updating the last candle
let fxRate = 1;                          // USD→INR for crypto live streams
let names = {};        // symbol -> friendly name
let apOn = false;

/* ---------- helpers ---------- */
function toast(msg, ok = false) {
  const t = $("toast"); t.textContent = msg; t.className = ok ? "ok" : "";
  t.style.display = "block"; clearTimeout(t._h);
  t._h = setTimeout(() => (t.style.display = "none"), 6000);
}
async function api(path, opts = {}) {
  const res = await fetch("/api" + path, { headers: { "Content-Type": "application/json" }, ...opts });
  let body = null; try { body = await res.json(); } catch (_) {}
  if (!res.ok) throw new Error((body && body.detail) || res.statusText || "Request failed");
  return body;
}
const L = () => window.LightweightCharts || null;
function baseChart(el, h, intraday = false) {
  return L().createChart(el, {
    height: h, layout: { background: { color: "transparent" }, textColor: "#8b96ad" },
    grid: { vertLines: { color: "#1b2331" }, horzLines: { color: "#1b2331" } },
    rightPriceScale: { borderColor: "#26304a" },
    timeScale: { borderColor: "#26304a", timeVisible: intraday, secondsVisible: false },
    autoSize: true,
  });
}

/* ---------- tabs ---------- */
document.querySelectorAll(".tabs button").forEach((b) =>
  b.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".view").forEach((x) => x.classList.remove("active"));
    b.classList.add("active"); $("v-" + b.dataset.v).classList.add("active");
  }));

/* ---------- header ---------- */
async function loadHeader() {
  try { const r = await api("/market/regime");
    $("sRegime").textContent = `${r.label.replace(/_/g, " ")} ${n(r.score, 0)}`;
    $("sRegime").style.color = col(r.score);
  } catch (_) { $("sRegime").textContent = "—"; }
}

/* ---------- universe / browse ---------- */
async function loadUniverse() {
  let u;
  try { u = await api("/universe"); } catch (_) { return; }
  names = {};
  uni = { cry: u.crypto, stk: u.stocks };
  shown = { cry: 0, stk: 0 };
  stkNote = u.sources.groww_stocks ? "all NSE+BSE via Groww" : "add Groww token for all ~7000";

  // EVERY symbol is searchable via the search box (datalist handles thousands)
  const dl = $("qlist"); dl.innerHTML = "";
  const frag = document.createDocumentFragment();
  [...u.crypto, ...u.stocks].forEach((o) => {
    names[o.symbol] = o.name;
    const opt = document.createElement("option"); opt.value = o.symbol; opt.label = o.name; frag.appendChild(opt);
  });
  dl.appendChild(frag);

  ["cry", "stk"].forEach((w) => { $(w + "Chips").innerHTML = ""; appendChunk(w); });
  if (u.crypto.length > 12) $("cryMore").style.display = "inline";
  if (u.stocks.length > 12) $("stkMore").style.display = "inline";
}

/* Chips render in batches; the expanded box scrolls and auto-loads more until
   every coin / every stock is on screen. */
let uni = { cry: [], stk: [] }, shown = { cry: 0, stk: 0 }, stkNote = "";
const CHIP_CHUNK = 300;

function chipHTML(o) {
  names[o.symbol] = o.name;
  return `<div class="chip ${o.kind === "crypto" ? "cry" : ""}" onclick="analyze('${o.symbol}')">`
    + `${o.name}<span class="sub">${o.symbol}</span></div>`;
}

function appendChunk(which) {
  const list = uni[which], el = $(which + "Chips");
  if (!list.length || shown[which] >= list.length) { updateChipCount(which); return; }
  const next = list.slice(shown[which], shown[which] + CHIP_CHUNK);
  el.insertAdjacentHTML("beforeend", next.map(chipHTML).join(""));
  shown[which] += next.length;
  updateChipCount(which);
}

function updateChipCount(which) {
  const total = uni[which].length, n_ = shown[which];
  const more = total > n_ ? " — open & scroll for more, or type in search" : "";
  $(which + "Count").textContent = which === "stk"
    ? `(showing ${n_.toLocaleString()} of ${total.toLocaleString()} · ${stkNote}${more})`
    : `(showing ${n_.toLocaleString()} of ${total.toLocaleString()}${more})`;
}

function toggleChips(which) {
  const el = $(which + "Chips"), btn = $(which + "More");
  const opening = !el.classList.contains("open");
  el.classList.toggle("open", opening);
  btn.textContent = opening ? "Show less ▴" : "Show all ▾";
  if (opening) {
    el.onscroll = () => {
      if (el.scrollTop + el.clientHeight >= el.scrollHeight - 150) appendChunk(which);
    };
    appendChunk(which);
  } else {
    el.onscroll = null; el.scrollTop = 0;
    // trim back to the first batch so the page stays light when collapsed
    el.innerHTML = uni[which].slice(0, CHIP_CHUNK).map(chipHTML).join("");
    shown[which] = Math.min(shown[which], CHIP_CHUNK);
    updateChipCount(which);
  }
}

/* ---------- advisor ---------- */
async function analyze(sym) {
  if (sym) $("q").value = sym;
  const symbol = $("q").value.trim().toUpperCase();
  if (!symbol) return toast("Type or pick a symbol first.");
  document.querySelector('.tabs button[data-v="advisor"]').click();
  $("goBtn").disabled = true; $("goBtn").innerHTML = '<span class="spin"></span>';
  try {
    const [sig, ch] = await Promise.all([
      api(`/analyze/${encodeURIComponent(symbol)}`),
      api(`/chart/${encodeURIComponent(symbol)}?tf=${chartTF}`),
    ]);
    last = sig; renderVerdict(sig); renderCharts(ch, sig);
    startLive();
    $("result").style.display = "block";
    $("result").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) { toast("Analysis failed: " + e.message); }
  $("goBtn").disabled = false; $("goBtn").textContent = "Analyze";
}

function renderVerdict(s) {
  const nm = names[s.symbol] || s.symbol;
  $("verdict").className = "verdict " + s.action;
  $("rName").textContent = nm;
  $("rSym").textContent = "  " + s.symbol;
  lastCur = s.currency || "INR"; lastFx = s.fx_rate || null;
  $("rPrice").textContent = fmt(s.entry);
  $("rBadge").className = "vbadge " + s.action;
  const rating = s.rating || s.action;
  $("rBadge").innerHTML = rating + "<small>" +
    ({ "STRONG BUY": "high-conviction", BUY: "signal", ACCUMULATE: "leaning bullish",
       HOLD: "wait", REDUCE: "leaning bearish", SELL: "signal",
       "STRONG SELL": "high-conviction" }[rating] || "") + "</small>";
  $("rSummary").textContent = s.summary || "";
  $("rConf").textContent = n(s.confidence, 0) + "%";
  $("rConf").style.color = col(s.confidence, 66, 40);
  $("rComp").innerHTML = n(s.composite_score, 0) + "/100"
    + (s.edge_score != null ? ` <span class="small muted">· edge ${n(s.edge_score, 1)}</span>` : "");
  $("rComp").style.color = col(s.composite_score);
  $("rRisk").textContent = s.risk_score + "/10";
  $("rRisk").style.color = s.risk_score >= 7 ? "#ef4444" : s.risk_score >= 4 ? "#f59e0b" : "#22c55e";
  $("rTrend").textContent = s.technical.trend.label.replace(/_/g, " ");
  $("rFx").textContent = s.currency_note || "";
  const cv = s.conviction;
  if (cv) {
    const pct = cv.passed / cv.total;
    const ccol = pct >= 0.75 ? "#22c55e" : pct >= 0.5 ? "#f59e0b" : "#ef4444";
    $("convict").innerHTML =
      `<div style="font-size:12px;color:var(--muted);font-weight:700;letter-spacing:.5px;margin-bottom:8px">
         CHART CONVICTION — <span style="color:${ccol}">${cv.passed}/${cv.total} checks passed</span>
         <span style="font-weight:500"> (STRONG BUY needs ≥6)</span></div>
       <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:6px">`
      + cv.checks.map((c) => `<div class="small" style="display:flex;gap:8px;align-items:center;
          background:var(--bg2);border:1px solid var(--line);border-radius:9px;padding:7px 10px">
          <span style="color:${c.ok ? "#22c55e" : "#5f6b83"};font-weight:800">${c.ok ? "✓" : "✗"}</span>${c.name}</div>`).join("")
      + `</div>`;
  } else { $("convict").innerHTML = ""; }

  $("pEntry").textContent = fmt(s.entry);
  $("pStop").textContent = fmt(s.stop_loss);
  $("pT1").textContent = fmt(s.target_1);
  $("pT2").textContent = fmt(s.target_2);
  $("rApprox").textContent = s.fx_rate ? `all prices in ₹ · USD→INR @ ₹${s.fx_rate.toFixed(2)} applied` : "";
  syncWatchBtn();
  $("rGuard") && ($("rGuard").innerHTML = s.guardrail
    ? `⚠ learning guardrail: ${s.guardrail.from} stepped down to ${s.guardrail.to} — this call type hit only ${s.guardrail.hit_rate}% in ${s.guardrail.regime} markets`
    : "");
  const pr = s.precision;
  $("rPrec").innerHTML = !pr ? "" : pr.passed
    ? `<span style="color:#22c55e">🎯 passes the ${pr.target}% precision gate (class measured ${pr.gate.hit_rate}%)</span>`
    : (pr.gate && s.rating === "HOLD" && (s.reasoning || []).some((x) => x.includes("Precision mode"))
        ? `<span style="color:#fbbf24">🎯 filtered by the ${pr.target}% precision gate</span>` : "");
  $("rTrack").textContent = s.track_record
    ? `🧠 calibrated by track record: ${s.track_record.hit_rate}% hit over ${s.track_record.samples} past ${s.track_record.scope}`
    : "";
  $("pRR").textContent = "1:" + n(s.risk_reward, 1);

  const labels = { technical: "Technical", fundamental: "Fundamental", news: "News", social: "Social", market: "Market" };
  const w = s.scores.weights_used || {};
  $("factors").innerHTML = Object.keys(labels).map((k) => {
    const v = s.scores[k];
    return `<div class="factor"><span class="name">${labels[k]}</span>`
      + `<span class="w">${Math.round((w[k] || 0) * 100)}%</span>`
      + `<div class="track"><i style="width:${v}%;background:${col(v)}"></i></div>`
      + `<span class="sc">${n(v, 0)}</span></div>`;
  }).join("");

  $("reasonList").innerHTML = s.reasoning.map((r) => `<li>${r}</li>`).join("");
  const newsMom = (s.news || {}).momentum;
  $("newsMomentum") && ($("newsMomentum").innerHTML = newsMom && newsMom.available
    ? (newsMom.surge
        ? `<span style="color:#fbbf24">🔥 news surge — ${newsMom.count_24h} headlines in 24h vs ${newsMom.baseline_per_day}/day normal</span>`
        : `<span class="muted">${newsMom.count_24h} headlines in 24h${newsMom.latest_age_hours != null ? " · latest " + newsMom.latest_age_hours + "h ago" : ""}</span>`)
    : "");

  // news
  const nw = s.news, sc = nw.label === "bullish" ? "#22c55e" : nw.label === "bearish" ? "#ef4444" : "#f59e0b";
  $("newsSenti").textContent = nw.label.toUpperCase();
  $("newsSenti").style = `background:${sc}22;color:${sc}`;
  $("newsMethod").textContent = nw.method === "llm" ? "AI-read headlines" : nw.method === "skipped" ? "" : "keyword scan";
  $("newsSummary").textContent = nw.summary || "";
  $("newsList").innerHTML = (nw.headlines || []).length
    ? nw.headlines.slice(0, 6).map((h) => `<div class="news-item">${h}</div>`).join("")
    : '<div class="muted small">No recent headlines found.</div>';

  // technical detail
  const t = s.technical, ind = t.indicators;
  const cell = (l, v, c) => `<div class="cell"><div class="l">${l}</div><div class="v" ${c ? `style="color:${c}"` : ""}>${v}</div></div>`;
  $("techKv").innerHTML =
    cell("RSI 14", n(ind.rsi14, 1)) + cell("ADX 14", n(ind.adx14, 1)) +
    cell("MACD hist", n(ind.macd_hist, 2), ind.macd_hist >= 0 ? "#22c55e" : "#ef4444") +
    cell("ATR %", n(t.atr_pct, 2) + "%") +
    cell("Supertrend", ind.supertrend_dir === 1 ? "bullish" : "bearish", ind.supertrend_dir === 1 ? "#22c55e" : "#ef4444") +
    cell("Weekly trend", (ind.weekly && ind.weekly.available) ? ind.weekly.trend : "–",
         ind.weekly && ind.weekly.trend === "up" ? "#22c55e" : ind.weekly && ind.weekly.trend === "down" ? "#ef4444" : undefined) +
    cell("RS vs market (3m)", ind.rs_3m_vs_bench != null ? (ind.rs_3m_vs_bench > 0 ? "+" : "") + ind.rs_3m_vs_bench + "%" : "–",
         ind.rs_3m_vs_bench > 0 ? "#22c55e" : ind.rs_3m_vs_bench < 0 ? "#ef4444" : undefined) +
    cell("OBV flow", ind.obv_slope > 0.05 ? "accumulation" : ind.obv_slope < -0.05 ? "distribution" : "flat",
         ind.obv_slope > 0.05 ? "#22c55e" : ind.obv_slope < -0.05 ? "#ef4444" : undefined) +
    cell("Up/Down volume", ind.updown_vol_ratio + "×") +
    cell("60-bar momentum", (ind.roc60_pct > 0 ? "+" : "") + ind.roc60_pct + "%") +
    cell("BB squeeze %", ind.bb_bandwidth_pct) +
    cell("52-week position", ind.pos_52w_pct + "%") +
    cell("Volume POC", (ind.volume_profile && ind.volume_profile.available) ? inr2.format(ind.volume_profile.poc) : "–") +
    cell("Value-area zone", (ind.volume_profile && ind.volume_profile.available) ? ind.volume_profile.zone.replace(/_/g, " ") : "–",
         (ind.volume_profile || {}).zone === "above_value" ? "#22c55e" : (ind.volume_profile || {}).zone === "below_value" ? "#ef4444" : undefined) +
    cell("Anchored VWAP", (ind.anchored_vwap && ind.anchored_vwap.available) ? inr2.format(ind.anchored_vwap.value) : "–",
         (ind.anchored_vwap || {}).above ? "#22c55e" : "#ef4444") +
    cell("Market structure", (ind.structure && ind.structure.available) ? ind.structure.label : "–",
         (ind.structure || {}).bias === "bullish" ? "#22c55e" : (ind.structure || {}).bias === "bearish" ? "#ef4444" : undefined) +
    cell("Break of structure", (ind.structure && ind.structure.bos) ? ind.structure.bos.toUpperCase() : "none",
         (ind.structure || {}).bos === "bullish" ? "#22c55e" : (ind.structure || {}).bos === "bearish" ? "#ef4444" : undefined) +
    cell("Trend quality (R²)", (ind.trend_quality && ind.trend_quality.available) ? ind.trend_quality.r2 : "–",
         ((ind.trend_quality || {}).r2 || 0) >= 0.6 ? "#22c55e" : undefined) +
    cell("Path efficiency", (ind.trend_quality && ind.trend_quality.available) ? ind.trend_quality.efficiency : "–") +
    cell("Fib retracement", (ind.fib && ind.fib.available) ? ind.fib.zone : "–",
         (ind.fib || {}).zone && ind.fib.zone.indexOf("golden") === 0 ? "#22c55e" : undefined) +
    cell("Monthly trend", (ind.monthly && ind.monthly.available) ? ind.monthly.trend : "–",
         (ind.monthly || {}).trend === "up" ? "#22c55e" : (ind.monthly || {}).trend === "down" ? "#ef4444" : undefined);
  $("patterns").innerHTML = (t.patterns || []).map((p) =>
    `<span class="pill" style="margin:2px">${p.name.replace(/_/g, " ")} · ${p.bars_ago}d</span>`).join("")
    || '<span class="muted small">No notable candlestick patterns recently.</span>';

  // fundamentals
  const f = s.fundamental, r2 = f.ratios || {};
  const fc = (l, v) => v === null || v === undefined ? "" : cell(l, v);
  $("fundKv").innerHTML =
    cell("Score", n(f.score, 0) + "/100") +
    cell("Coverage", Math.round((f.coverage || 0) * 100) + "%") +
    fc("P/E", r2.pe != null ? n(r2.pe, 1) : null) +
    fc("ROE", r2.roe != null ? n(r2.roe * 100, 1) + "%" : null) +
    fc("D/E", r2.debt_to_equity != null ? n(r2.debt_to_equity, 0) : null) +
    fc("Margin", r2.profit_margin != null ? n(r2.profit_margin * 100, 1) + "%" : null);
  $("fundNotes").innerHTML = (f.notes || []).slice(0, 6).map((x) => `<li>${x}</li>`).join("");

  // ---- India context: sector RS, delivery quality, earnings proximity ----
  const cellI = (l, v, c) =>
    `<div class="cell"><div class="l">${l}</div><div class="v" ${c ? `style="color:${c}"` : ""}>${v}</div></div>`;
  const dl = s.delivery || {}, srs = s.sector_rs, ein = s.earnings_in_days;
  $("indiaKv").innerHTML =
    cellI("Sector strength (3m)", srs != null ? (srs > 0 ? "+" : "") + srs + "%" : "–",
      srs > 0 ? "#22c55e" : srs < 0 ? "#ef4444" : undefined) +
    cellI("Sector index", s.sector_index || "–") +
    cellI("Delivery %", dl.available ? dl.latest_pct + "% (avg " + dl.avg_pct + "%)" : "n/a",
      dl.label === "strong_accumulation" ? "#22c55e" : dl.label === "churn" ? "#ef4444" : undefined) +
    cellI("Delivery read", dl.available ? dl.label.replace(/_/g, " ") : "NSE data unavailable") +
    cellI("Earnings in", ein != null ? ein + " day(s)" : "not scheduled/unknown",
      ein != null && ein <= 2 ? "#f59e0b" : undefined);
  const iNotes = [];
  if (srs != null) iNotes.push(srs > 0
    ? `Leading its own sector by ${srs}% — money is favouring this name inside its group.`
    : `Lagging its own sector by ${Math.abs(srs)}% — stronger names likely exist in the same space.`);
  if (dl.available) iNotes.push(`Delivery ${dl.latest_pct}% vs ${dl.avg_pct}% average — ${dl.note}.`);
  else iNotes.push("Delivery data needs NSE's daily file; unavailable right now, so this check is skipped rather than guessed.");
  if (ein != null && ein <= 2) iNotes.push(`Results due in ${ein} day(s) — rating is capped at HOLD because result-day gaps ignore technicals.`);
  $("indiaNotes").innerHTML = iNotes.map((x) => `<li>${x}</li>`).join("");

  // ---- advanced chart analytics ----
  const I = s.technical.indicators, cellA = (l, v, c) =>
    `<div class="cell"><div class="l">${l}</div><div class="v" ${c ? `style="color:${c}"` : ""}>${v}</div></div>`;
  const gr = "#22c55e", rd = "#ef4444", am = "#f59e0b";
  const ich = I.ichimoku || {}, dv = I.divergence_confluence || {}, vp = I.volume_profile || {},
        av = I.anchored_vwap || {}, st = I.structure || {}, tq = I.trend_quality || {},
        fb = I.fib || {}, mo = I.monthly || {};
  $("advKv").innerHTML =
    cellA("Ichimoku", ich.available ? (ich.verdict + " · " + (ich.position || "").replace(/_/g, " ")) : "–",
      ich.verdict === "bullish" ? gr : ich.verdict === "bearish" ? rd : am) +
    cellA("Multi-osc divergence", dv.available ? (dv.verdict === "none" ? "none" : dv.verdict + " (" + (dv.oscillators || []).join("+") + ")") : "–",
      dv.verdict === "bearish" ? rd : dv.verdict === "bullish" ? gr : undefined) +
    cellA("Volume profile", vp.available ? (vp.zone || "").replace(/_/g, " ") : "–",
      vp.zone === "above_value" ? gr : vp.zone === "below_value" ? rd : am) +
    cellA("POC (fair value)", vp.available ? inr2.format(vp.poc) : "–") +
    cellA("Anchored VWAP", av.available ? (av.above ? "above ✓" : "below ✗") + " " + inr2.format(av.value) : "–",
      av.above ? gr : rd) +
    cellA("Market structure", st.label ? st.label.replace(/_/g, " ") : "–",
      st.bias === "bullish" ? gr : st.bias === "bearish" ? rd : am) +
    cellA("Trend quality (R²)", tq.r2 != null ? tq.r2 : "–", tq.r2 >= 0.7 ? gr : tq.r2 >= 0.4 ? am : rd) +
    cellA("Fib retracement", fb.available ? fb.zone : "–") +
    cellA("Monthly trend", mo.available ? mo.trend : "–",
      mo.trend === "up" ? gr : mo.trend === "down" ? rd : am);
  const notes = [];
  if (ich.available && ich.tk_cross) notes.push(`Fresh ${ich.tk_cross} Tenkan/Kijun cross detected.`);
  if (dv.verdict === "bearish") notes.push(`Warning: ${dv.strength} oscillators show bearish divergence — momentum is not confirming price highs.`);
  if (vp.available) notes.push(`Heaviest traded volume sits at ${inr2.format(vp.poc)}; value area ${inr2.format(vp.val)}–${inr2.format(vp.vah)} (drawn on the chart).`);
  if (st.bos) notes.push(`Structure event: ${st.bos.replace(/_/g, " ")}.`);
  if (av.available) notes.push(`Average price paid since the 52-week low is ${inr2.format(av.value)} — holders are ${av.above ? "in profit" : "underwater"}.`);
  $("advNotes").innerHTML = notes.map((x) => `<li>${x}</li>`).join("");

  $("disc").textContent = s.disclaimer;
}

/* ---------- charts ---------- */
// Scope to the chart's own row: other UI reuses the .tf class for styling, and a
// stray listener there once set chartTF to undefined and broke every analysis.
document.querySelectorAll("#tfRow .tf").forEach((b) => b.addEventListener("click", async () => {
  if (!b.dataset.tf) return;                       // hard guard
  document.querySelectorAll("#tfRow .tf").forEach((x) => x.classList.remove("active"));
  b.classList.add("active"); chartTF = b.dataset.tf;
  const sym = (last && last.symbol) || $("q").value.trim().toUpperCase();
  if (!sym) return;
  try { const ch = await api(`/chart/${encodeURIComponent(sym)}?tf=${chartTF}`); renderCharts(ch, last || {}); startLive(); }
  catch (e) { toast("Chart failed: " + e.message); }
}));

function renderCharts(d, sig) {
  fxRate = d.fx_rate || 1;               // scales raw-USD live WebSocket ticks only
  if (!L()) { $("chart").innerHTML = '<p class="muted">Chart engine did not load — hard-refresh (Ctrl+Shift+R).</p>'; return; }
  ["main", "rsi"].forEach((k) => { if (charts[k]) { charts[k].remove(); charts[k] = null; } });
  liveSeries = null; liveBar = null;
  $("chart").innerHTML = ""; $("rsi").innerHTML = "";
  const c = baseChart($("chart"), 400, d.intraday); charts.main = c;
  const cand_ = c.addCandlestickSeries({ upColor: "#22c55e", downColor: "#ef4444", borderVisible: false, wickUpColor: "#22c55e", wickDownColor: "#ef4444" });
  cand_.setData(d.candles);
  liveSeries = cand_;                              // let the ticker update the last candle live
  liveBar = d.candles.length ? { ...d.candles[d.candles.length - 1] } : null;
  const vol = c.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" } });
  c.priceScale("vol").applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
  vol.setData(d.volume);
  const line = (data, color, w = 1) => data && data.length &&
    c.addLineSeries({ color, lineWidth: w, priceLineVisible: false, lastValueVisible: false }).setData(data);
  line(d.ema20, "#e8b64c"); line(d.ema50, "#3b82f6"); line(d.ema200, "#8b5cf6", 2);
  line(d.supertrend_bull, "#22c55e", 2); line(d.supertrend_bear, "#ef4444", 2);
  line(d.vwap, "#22d3ee", 1);
  line(d.avwap, "#f472b6", 2);                      // anchored VWAP from the 52w low
  if (d.levels && d.levels.poc) {
    const L_ = (p, c, t, st) => cand_.createPriceLine({ price: p, color: c, lineStyle: st,
      lineWidth: 1, title: t, axisLabelVisible: true });
    L_(d.levels.poc, "#eab308", "POC", 0);
    if (d.levels.vah) L_(d.levels.vah, "#eab308", "VA high", 2);
    if (d.levels.val) L_(d.levels.val, "#eab308", "VA low", 2);
  }
  const lvl = (p, cc, tt) => p != null && cand_.createPriceLine({ price: p, color: cc, lineStyle: 2, lineWidth: 1, title: tt });
  if (sig && sig.entry) { lvl(sig.entry, "#eef2f9", "entry"); lvl(sig.stop_loss, "#ef4444", "stop"); lvl(sig.target_1, "#22c55e", "T1"); lvl(sig.target_2, "#22c55e", "T2"); }
  const FB = d.levels || {};                        // fib retracement levels
  if (FB.fib_618) lvl(FB.fib_618, "#94a3b8", "fib 0.618");
  if (FB.fib_500) lvl(FB.fib_500, "#94a3b8", "fib 0.5");
  c.timeScale().fitContent();
  const r = baseChart($("rsi"), 96, d.intraday); charts.rsi = r;
  const rs = r.addLineSeries({ color: "#3b82f6", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
  rs.setData(d.rsi || []);
  [30, 70].forEach((p) => rs.createPriceLine({ price: p, color: "#26304a", lineStyle: 2, lineWidth: 1, title: String(p) }));
  r.timeScale().fitContent();
}


/* ---------- market-wide buy scanner ---------- */
let scanTimer = null;

async function scanStart(btn) {
  try {
    await api("/scan/start", { method: "POST" });
    toast("Scanning the entire market — results rank live below.", true);
    pollScan();
  } catch (e) { toast("Scan failed: " + e.message); }
}
async function scanStop() {
  try { await api("/scan/stop", { method: "POST" }); } catch (_) {}
  if (scanTimer) { clearInterval(scanTimer); scanTimer = null; }
  renderScan(await api("/scan/status"));
}
function pollScan() {
  if (scanTimer) clearInterval(scanTimer);
  const tick = async () => {
    try {
      const s = await api("/scan/status");
      renderScan(s);
      if (!s.running && scanTimer) { clearInterval(scanTimer); scanTimer = null; }
    } catch (_) {}
  };
  tick(); scanTimer = setInterval(tick, 3000);
}
let scanKind = "all", scanOffset = 0, scanRows = [];

function setScanKind(k, btn) {
  scanKind = k;
  document.querySelectorAll("#scanFilters .fbtn").forEach((b) => b.classList.remove("active"));
  btn.classList.add("active");
  loadScanResults(true);
}

async function loadScanResults(reset) {
  if (reset) { scanOffset = 0; scanRows = []; }
  const params = new URLSearchParams({
    limit: "100", offset: String(scanOffset), sort: $("scanSort").value,
    min_score: $("scanMinScore").value || "0",
  });
  const rv = $("scanRating").value;
  if (rv === "all") params.set("only_buy", "false");
  else if (rv !== "buy") { params.set("ratings", rv); params.set("only_buy", "false"); }
  else params.set("only_buy", "true");
  if (scanKind !== "all") params.set("kind", scanKind);
  try {
    const r = await api("/scan/results?" + params.toString());
    scanRows = scanRows.concat(r.rows);
    scanOffset += r.rows.length;
    renderScanRows(scanRows, r);
  } catch (_) {}
}

function renderScanRows(rows, meta) {
  if (meta) {
    const br = meta.counts.by_rating || {};
    $("scanShowing").innerHTML =
      `showing <b>${rows.length}</b> of ${meta.total_matching} matching · `
      + `buy-side found: <b style="color:#4ade80">${meta.counts.buy_side}</b> `
      + `(${br["STRONG BUY"] || 0} strong buy · ${br.BUY || 0} buy · ${br.ACCUMULATE || 0} accumulate) · `
      + `scanned ${meta.counts.all.toLocaleString()}`;
  } else $("scanShowing").textContent = "";
  $("scanMore").style.display = meta && rows.length < meta.total_matching ? "inline-block" : "none";
  $("scanTop").innerHTML = rows.length
    ? `<table><thead><tr><th>#</th><th>Symbol</th><th>Rating</th><th>Score</th><th>Edge</th><th>Conf.</th>
       <th>Risk</th><th>Trend</th><th>Price</th></tr></thead><tbody>`
      + rows.map((x, i) => {
          const cls = (x.rating || "").includes("BUY") || x.rating === "ACCUMULATE" ? "BUY"
                    : (x.rating || "").includes("SELL") || x.rating === "REDUCE" ? "SELL" : "HOLD";
          const col = cls === "BUY" ? "#22c55e22;color:#4ade80" : cls === "SELL" ? "#ef444422;color:#f87171" : "#f59e0b22;color:#fbbf24";
          return `<tr style="cursor:pointer" onclick="analyze('${x.symbol}')">
            <td class="muted">${i + 1}</td>
            <td><b>${x.symbol}</b> <span class="muted small">${x.kind === "crypto" ? "🪙" : "📊"}</span></td>
            <td><span class="senti" style="background:${col}">${x.rating || "–"}</span></td>
            <td><b>${n(x.composite, 0)}</b></td>
            <td style="color:${x.edge >= 65 ? "#22c55e" : x.edge >= 50 ? "#f59e0b" : "#8b96ad"}">${x.edge == null ? "–" : n(x.edge, 1)}</td>
            <td>${n(x.confidence, 0)}%</td>
            <td>${x.risk}/10</td><td class="small">${(x.trend || "").replace(/_/g, " ")}</td>
            <td>${inr2.format(x.price)}</td></tr>`;
        }).join("") + "</tbody></table>"
    : '<div class="muted small" style="padding:8px 0">No buy-rated candidates yet. In a weak market that is the honest answer — widen the filter to "All ratings" to see everything scanned, or wait for the scan to cover more symbols.</div>';
}

function renderScan(s) {
  const pct = s.total ? Math.round(100 * s.scanned / s.total) : 0;
  $("scanBar").style.width = pct + "%";
  $("scanBtn").style.display = s.running ? "none" : "inline-block";
  $("scanStopBtn").style.display = s.running ? "inline-block" : "none";
  const upd = s.updated_at ? new Date(s.updated_at + (s.updated_at.endsWith("Z") ? "" : "Z"))
    .toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }) : "";
  $("scanProg").textContent = s.total
    ? `${s.running ? "Scanning" : "Last scan"}: ${s.scanned.toLocaleString()}/${s.total.toLocaleString()} (${pct}%)`
      + ` · ${s.note}` + (s.errors ? ` · ${s.errors} skipped` : "") + (upd ? ` · updated ${upd}` : "")
    : `Ready — ${s.note || "press Scan entire market"}`;
  $("scanConc").textContent = s.concentration || "";
  const any = (s.result_counts || {}).all > 0;
  $("scanFilters").style.display = any ? "flex" : "none";
  // don't yank the table out from under someone who has paged through results
  if (any && (scanOffset <= 100 || !s.running)) loadScanResults(true);
  else $("scanTop").innerHTML = s.running
    ? '<div class="muted small" style="padding:8px 0">Results appear here as symbols are scanned…</div>'
    : '<div class="muted small" style="padding:8px 0">Press "Scan entire market" to begin.</div>';
}

/* ---------- precision mode ---------- */
function pmStatusText(p) {
  if (!p) return "";
  const g = p.gate;
  if (!g) return "Armed — no graded history yet. Analyze daily; after grading (~5 days) the gate finds where your target lives.";
  const cls = `composite ≥${g.min_composite} · conviction ${g.min_conviction}`;
  return g.met
    ? `Gate ACTIVE: ${cls} — this class measured ${g.hit_rate}% over ${g.samples} graded calls (target ${p.target}%).`
    : `Target ${p.target}% not reached yet — best measured class: ${g.hit_rate}% (${cls}, ${g.samples} calls). Buys are filtered until a class earns it.`;
}
async function loadPrecision() {
  try {
    const p = await api("/settings/precision");
    $("pmOn").checked = !!p.enabled; $("pmTarget").value = p.target;
    $("pmStatus").textContent = p.enabled ? pmStatusText(p) : "Off — all ratings shown unfiltered. " + pmStatusText(p);
  } catch (_) {}
}
async function savePrecision(btn) {
  btn.disabled = true;
  try {
    const p = await api("/settings/precision", { method: "POST",
      body: JSON.stringify({ enabled: $("pmOn").checked, target: +$("pmTarget").value }) });
    $("pmStatus").textContent = p.enabled ? pmStatusText(p) : "Off — all ratings shown unfiltered.";
    toast(p.enabled ? `Precision mode ON — aiming for a measured ${p.target}%.` : "Precision mode off.", true);
  } catch (e) { toast(e.message); }
  btn.disabled = false;
}

/* ---------- watchlist + telegram ---------- */
let watchSet = new Set();
function ratingPill(r) {
  const c = (r || "").includes("BUY") || r === "ACCUMULATE" ? ["#22c55e22", "#4ade80"]
    : (r || "").includes("SELL") || r === "REDUCE" ? ["#ef444422", "#f87171"] : ["#f59e0b22", "#fbbf24"];
  return `<span class="senti" style="background:${c[0]};color:${c[1]}">${r || "–"}</span>`;
}
async function loadWatch() { try { const w = await api("/watchlist"); renderWatch(w.items); } catch (_) {} }
function renderWatch(items) {
  watchSet = new Set(items.map((i) => i.symbol)); syncWatchBtn();
  $("watchWrap").innerHTML = items.length
    ? `<table><thead><tr><th>Symbol</th><th>Rating</th><th>Score</th><th>Price</th><th>Last change</th><th></th></tr></thead><tbody>`
      + items.map((i) => `<tr><td style="cursor:pointer" onclick="analyze('${i.symbol}')"><b>${i.symbol}</b></td>
        <td>${ratingPill(i.last_rating)}</td><td>${i.last_composite != null ? n(i.last_composite, 0) : "–"}</td>
        <td>${i.last_price != null ? inr2.format(i.last_price) : "–"}</td>
        <td class="small muted">${i.prev_rating ? i.prev_rating + " → " + i.last_rating : "—"}</td>
        <td><button class="btn ghost" style="min-height:30px;padding:3px 10px;font-size:12px" onclick="removeWatch('${i.symbol}')">✕</button></td></tr>`).join("")
      + "</tbody></table>"
    : '<div class="muted small">Nothing watched yet — analyze a symbol and press ☆ Watch.</div>';
}
async function watchToggle() {
  if (!last) return;
  const s = last.symbol;
  try {
    const w = await api(`/watchlist/${encodeURIComponent(s)}`, { method: watchSet.has(s) ? "DELETE" : "POST" });
    renderWatch(w.items);
    toast(watchSet.has(s) ? `${s} added — rating changes will alert you.` : `${s} removed from watchlist.`, true);
  } catch (e) { toast(e.message); }
}
function syncWatchBtn() {
  const b = $("watchBtn"); if (!b) return;
  b.textContent = last && watchSet.has(last.symbol) ? "★ Watching" : "☆ Watch";
}
async function removeWatch(sym) {
  try { const w = await api(`/watchlist/${encodeURIComponent(sym)}`, { method: "DELETE" }); renderWatch(w.items); } catch (_) {}
}
async function refreshWatch(btn) {
  btn.disabled = true; const old = btn.textContent; btn.innerHTML = '<span class="spin"></span>';
  try {
    const r = await api("/watchlist/refresh", { method: "POST" });
    renderWatch(r.items);
    toast(r.changes.length ? r.changes.map((c) => `${c.symbol}: ${c.from} → ${c.to}`).join(" · ")
                           : "Checked — no rating changes.", true);
  } catch (e) { toast(e.message); }
  btn.disabled = false; btn.textContent = old;
}
async function saveTelegram(btn) {
  const t = $("tgToken").value.trim(), c = $("tgChat").value.trim();
  if (!t || !c) return toast("Both bot token and chat ID are needed.");
  btn.disabled = true; const old = btn.textContent; btn.innerHTML = '<span class="spin"></span>';
  try {
    const r = await api("/settings/telegram", { method: "POST", body: JSON.stringify({ bot_token: t, chat_id: c }) });
    $("tgMsg").innerHTML = r.connected
      ? '<span style="color:#22c55e">✓ Connected — check Telegram for the test message.</span>'
      : `<span style="color:#ef4444">✗ ${r.error || "failed"} — check both values.</span>`;
    if (r.connected) { $("tgToken").value = ""; toast("Telegram alerts ON.", true); }
  } catch (e) { toast(e.message); }
  btn.disabled = false; btn.textContent = old;
}

/* ---------- backtest ---------- */
async function backtest() {
  $("btBtn").disabled = true; $("btStatus").innerHTML = '<span class="spin"></span> replaying…';
  try {
    const r = await api("/backtest", { method: "POST", body: JSON.stringify({
      symbol: $("btSym").value.trim().toUpperCase(), period: $("btPeriod").value, params: {} }) });
    const m = r.metrics, pc = (v) => v > 0 ? "#22c55e" : v < 0 ? "#ef4444" : undefined;
    const k = (l, v, c) => `<div class="cell"><div class="l">${l}</div><div class="v" ${c ? `style="color:${c}"` : ""}>${v}</div></div>`;
    $("btKv").innerHTML =
      k("Trades", m.total_trades) + k("Win rate", n(m.win_rate, 1) + "%") +
      k("Total return", n(m.total_return_pct, 1) + "%", pc(m.total_return_pct)) +
      k("CAGR", n(m.cagr_pct, 1) + "%", pc(m.cagr_pct)) +
      k("Max DD", n(m.max_drawdown_pct, 1) + "%", "#ef4444") +
      k("Sharpe", n(m.sharpe_ratio, 2)) + k("Sortino", n(m.sortino_ratio, 2)) +
      k("Profit factor", m.profit_factor == null ? "∞" : n(m.profit_factor, 2)) +
      k("Final equity", inr.format(m.final_equity));
    $("btOut").style.display = "block";
    if (L()) {
      if (charts.eq) { charts.eq.remove(); charts.eq = null; }
      $("eqChart").innerHTML = "";
      const c = baseChart($("eqChart"), 280); charts.eq = c;
      const s = c.addAreaSeries({ lineColor: "#3b82f6", topColor: "rgba(59,130,246,.35)", bottomColor: "rgba(59,130,246,.02)" });
      s.setData(r.equity_curve.map((p) => ({ time: p.date, value: p.equity })));
      c.timeScale().fitContent();
    }
    $("btStatus").textContent = "";
  } catch (e) { toast("Backtest failed: " + e.message); $("btStatus").textContent = ""; }
  $("btBtn").disabled = false;
}

/* ---------- strategy report card ---------- */
async function reportCard(btn) {
  btn.disabled = true; const old = btn.textContent; btn.innerHTML = '<span class="spin"></span> testing 10 stocks…';
  try {
    const r = await api("/backtest/batch", { method: "POST", body: JSON.stringify({}) });
    const a = r.aggregate;
    const k = (l, v, c) => `<div class="cell"><div class="l">${l}</div><div class="v" ${c ? `style="color:${c}"` : ""}>${v}</div></div>`;
    $("rcKv").innerHTML =
      k("Symbols tested", r.symbols_tested) + k("Total trades", a.total_trades) +
      k("Win rate", a.win_rate_pct != null ? a.win_rate_pct + "%" : "–",
        a.win_rate_pct >= 50 ? "#22c55e" : "#f59e0b") +
      k("Profit factor", a.profit_factor != null ? a.profit_factor : "–",
        a.profit_factor >= 1 ? "#22c55e" : "#ef4444") +
      k("Avg P&L / trade", a.avg_pnl_per_trade != null ? inr2.format(a.avg_pnl_per_trade) : "–",
        a.avg_pnl_per_trade > 0 ? "#22c55e" : "#ef4444");
    $("rcNote").textContent = r.honest_note;
    $("rcTable").innerHTML = `<table><thead><tr><th>Symbol</th><th>Trades</th><th>Win %</th>
      <th>Return %</th><th>PF</th></tr></thead><tbody>`
      + r.per_symbol.map((x) => x.error
        ? `<tr><td>${x.symbol}</td><td colspan="4" class="muted small">${x.error}</td></tr>`
        : `<tr><td><b>${x.symbol}</b></td><td>${x.trades}</td><td>${n(x.win_rate, 1)}%</td>
           <td style="color:${x.total_return_pct >= 0 ? "#22c55e" : "#ef4444"}">${n(x.total_return_pct, 1)}%</td>
           <td>${x.profit_factor == null ? "∞" : n(x.profit_factor, 2)}</td></tr>`).join("")
      + `</tbody></table>`;
    $("rcOut").style.display = "block";
  } catch (e) { toast("Report failed: " + e.message); }
  btn.disabled = false; btn.textContent = old;
}

/* ---------- groww token ---------- */
async function saveGrowwToken(btn) {
  const tok = $("growwTok").value.trim();
  if (!tok) return toast("Paste your Groww token first.");
  btn.disabled = true; const old = btn.textContent; btn.innerHTML = '<span class="spin"></span>';
  try {
    const r = await api("/settings/groww-token", { method: "POST", body: JSON.stringify({ token: tok }) });
    if (r.connected) {
      const probe = r.probe ? ` · live probe ${r.probe.symbol} ${inr2.format(r.probe.ltp)}` : "";
      $("growwTokMsg").innerHTML = `<span style="color:#22c55e">✓ Connected — ${r.instruments.toLocaleString()} instruments${probe}. All NSE+BSE now scannable.</span>`;
      $("growwTok").value = "";
      toast("Groww connected — all stocks unlocked.", true);
      loadUniverse();
    } else {
      $("growwTokMsg").innerHTML = `<span style="color:#ef4444">✗ ${r.error || "Could not connect"} — tokens expire daily, generate a fresh one.</span>`;
    }
  } catch (e) { toast("Failed: " + e.message); }
  btn.disabled = false; btn.textContent = old;
}

/* ---------- learning loop ---------- */
async function gradeSignals(btn) {
  btn.disabled = true; const old = btn.textContent; btn.innerHTML = '<span class="spin"></span> grading…';
  try {
    const r = await api("/learn/evaluate", { method: "POST" });
    toast(`Graded ${r.evaluated} signals — ${r.wins} right, ${r.losses} wrong.`, true);
  } catch (e) { toast("Grading failed: " + e.message); }
  await loadTrackRecord();
  btn.disabled = false; btn.textContent = old;
}

async function loadTrackRecord() {
  try {
    const t = await api("/learn/track-record");
    $("lrnOut").innerHTML = t.by_rating.length
      ? `<table><thead><tr><th>Rating</th><th>Signals</th><th>Graded</th><th>Hit rate</th><th>Avg move</th></tr></thead><tbody>`
        + t.by_rating.map((r) => `<tr><td><b>${r.rating}</b></td><td>${r.samples}</td><td>${r.graded}</td>
            <td style="color:${r.hit_rate >= 50 ? "#22c55e" : r.hit_rate == null ? "inherit" : "#f59e0b"}">${r.hit_rate == null ? "–" : r.hit_rate + "%"}</td>
            <td>${r.avg_move_pct > 0 ? "+" : ""}${r.avg_move_pct}%</td></tr>`).join("") + "</tbody></table>"
      : '<div class="muted small">No graded signals yet — analyze things today, come back in ~a week, press Grade.</div>';
    const bands = (t.by_conviction || []).filter((b) => b.graded);
    if (bands.length) {
      $("lrnOut").insertAdjacentHTML("beforeend",
        `<div class="muted small" style="margin:12px 0 6px;font-weight:700;text-transform:uppercase;letter-spacing:.5px">By chart conviction (does the checklist work?)</div>`
        + `<table><thead><tr><th>Conviction</th><th>Graded</th><th>Hit rate</th></tr></thead><tbody>`
        + bands.map((b) => `<tr><td><b>${b.band}</b></td><td>${b.graded}</td>
            <td style="color:${b.hit_rate >= 50 ? "#22c55e" : "#f59e0b"}">${b.hit_rate}%</td></tr>`).join("")
        + "</tbody></table>");
    }
    const pc = (t.precision_curve || []).filter((r) => r.samples >= 15).slice(0, 8);
    if (pc.length) {
      $("lrnOut").insertAdjacentHTML("beforeend",
        `<div class="muted small" style="margin:12px 0 6px;font-weight:700;text-transform:uppercase;letter-spacing:.5px">Strictness vs measured accuracy (where your target lives)</div>`
        + `<table><thead><tr><th>Conviction</th><th>Composite ≥</th><th>Graded</th><th>Hit rate</th></tr></thead><tbody>`
        + pc.map((r) => `<tr><td>${r.conviction}</td><td>${r.min_composite}</td><td>${r.samples}</td>
            <td style="color:${r.hit_rate >= 70 ? "#22c55e" : r.hit_rate >= 55 ? "#f59e0b" : "#ef4444"}">${r.hit_rate}%</td></tr>`).join("")
        + "</tbody></table>");
    }
    $("lrnLessons").innerHTML = (t.lessons || []).map((x) => `<li>${x}</li>`).join("");
    if (t.auto) {
      const at = t.auto.last_grade_at ? new Date(t.auto.last_grade_at).toLocaleString("en-IN",
        { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "not yet (runs ~1 min after server start)";
      $("lrnLessons").insertAdjacentHTML("beforeend",
        `<li>Auto-grading: ${t.auto.enabled ? "ON — daily" : "starts with the server"} · last run: ${at}</li>`);
    }
  } catch (_) {}
}

/* ---------- LIVE: Binance WebSocket for crypto, market-aware poll for stocks ---------- */
let liveWS = null, liveTimer = null, livePrev = null, liveThrottle = 0;
const IST_OFFSET = 19800;                                  // +5:30 in seconds
const KLINE_IV = { "5m": "5m", "15m": "15m", "1h": "1h" }; // native Binance intraday

function isCrypto(sym) { return sym.includes("-") && !/\.(NS|BO)$/.test(sym); }

function binancePair(sym) {
  const [base, quote] = sym.toUpperCase().split("-");
  if (!base || !quote || quote === "INR") return null;
  return (base + (quote === "USD" ? "USDT" : quote)).toLowerCase();
}

function nseOpen() {
  const ist = new Date(new Date().toLocaleString("en-US", { timeZone: "Asia/Kolkata" }));
  const day = ist.getDay(); if (day === 0 || day === 6) return false;
  const mins = ist.getHours() * 60 + ist.getMinutes();
  return mins >= 555 && mins <= 930;   // 9:15–15:30 IST
}

function stopLive() {
  if (liveWS) { try { liveWS.onmessage = null; liveWS.close(); } catch (_) {} liveWS = null; }
  if (liveTimer) { clearInterval(liveTimer); liveTimer = null; }
  livePrev = null;
}

function paintPrice(price) {
  const el = $("rLive");
  let arrow = "", color = "#8b96ad";
  if (livePrev != null) {
    if (price > livePrev) { arrow = "▲"; color = "#22c55e"; }
    else if (price < livePrev) { arrow = "▼"; color = "#ef4444"; }
  }
  $("rPrice").textContent = fmt(price);
  if (el) el.innerHTML = `<span style="color:${color}">🟢 LIVE ${fmt(price)} ${arrow}</span>`;
  livePrev = price;
}

function growLastBar(price) {
  if (liveSeries && liveBar) {
    liveBar.close = price;
    liveBar.high = Math.max(liveBar.high, price);
    liveBar.low = Math.min(liveBar.low, price);
    try { liveSeries.update(liveBar); } catch (_) {}
  }
}

function startLive() {
  stopLive();
  if (!last) return;
  const sym = last.symbol;
  const fx = lastFx || 1;                      // Binance streams USD → show ₹

  if (isCrypto(sym)) {
    const pair = binancePair(sym);
    if (!pair) { pollLive(5000); return; }
    const iv = KLINE_IV[chartTF];
    try {
      if (iv) {
        liveWS = new WebSocket(`wss://stream.binance.com:9443/ws/${pair}@kline_${iv}`);
        liveWS.onmessage = (ev) => {
          try {
            const k = JSON.parse(ev.data).k;
            const bar = { time: Math.floor(k.t / 1000) + IST_OFFSET,
                          open: +k.o * fx, high: +k.h * fx, low: +k.l * fx, close: +k.c * fx };
            if (liveSeries) { try { liveSeries.update(bar); } catch (_) {} }
            paintPrice(+k.c * fx);
          } catch (_) {}
        };
      } else {
        liveWS = new WebSocket(`wss://stream.binance.com:9443/ws/${pair}@trade`);
        liveWS.onmessage = (ev) => {
          const now = performance.now();
          if (now - liveThrottle < 200) return; liveThrottle = now;
          try {
            const d = JSON.parse(ev.data);
            if (d.p) { const p = parseFloat(d.p) * fx; growLastBar(p); paintPrice(p); }
          } catch (_) {}
        };
      }
      liveWS.onerror = () => { stopLive(); pollLive(4000); };
    } catch (_) { pollLive(4000); }
    return;
  }

  if (!nseOpen()) {
    const el = $("rLive");
    if (el) el.innerHTML = '<span style="color:#8b96ad">● market closed — showing last close (NSE/BSE trade 9:15–3:30 IST)</span>';
    return;
  }
  pollLive(5000);
}

function pollLive(ms) {
  const tick = async () => {
    if (!last) return;
    try { const r = await api(`/price/${encodeURIComponent(last.symbol)}`); growLastBar(r.price); paintPrice(r.price); }
    catch (_) {}
  };
  tick(); liveTimer = setInterval(tick, ms);
}

/* ---------- boot ---------- */
$("q").addEventListener("keydown", (e) => { if (e.key === "Enter") analyze(); });
loadHeader(); loadUniverse();
api("/scan/status").then(renderScan).catch(() => {});
loadTrackRecord();
loadWatch();
loadPrecision();
setInterval(loadHeader, 30000);
