/* ArbitrageOS dashboard. Vanilla JS, no build step. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const main = $("#main");
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (v, d = 2) => v == null || isNaN(v) ? "–" : (v < 0 ? "−$" : "$") + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const money0 = (v) => money(v, 0);
const pct = (v, d = 0) => v == null || isNaN(v) ? "–" : (v * 100).toFixed(d) + "%";
const sign = (v) => (v < 0 ? "neg" : v > 0 ? "pos" : "");
const int = (v) => v == null ? "–" : Number(v).toLocaleString("en-US");
const localDate = () => { const d = new Date(); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); };
const monthName = (m) => new Date(m + "-15T12:00:00").toLocaleString("en-US", { month: "short" });

let STATE = { settings: null };

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json", "X-ArbOS": "1" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const data = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "show" + (err ? " err" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = ""), err ? 6000 : 3000);
}

async function run(btn, fn) {
  if (btn) btn.disabled = true;
  try { return await fn(); } catch (e) { toast(e.message, true); } finally { if (btn) btn.disabled = false; }
}

function modal(html) {
  $("#modal-body").innerHTML = html;
  const d = $("#modal");
  if (!d.open) d.showModal();
  return $("#modal-body");
}
function closeModal() { $("#modal").close(); }
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal" || e.target.closest("[data-close]")) closeModal(); });

function verdictTag(v) { return `<span class="tag ${esc(v || "new")}">${esc(v === "NO_MATCH" ? "NO MATCH" : v || "NEW")}</span>`; }

// ------------------------------------------------------------------ router
const PAGES = {};
async function route() {
  const page = (location.hash.replace(/^#\//, "").split("?")[0]) || "overview";
  $$(".side a[data-page]").forEach((a) => a.classList.toggle("on", a.dataset.page === page));
  const fn = PAGES[page] || PAGES.overview;
  main.onclick = null;
  main.onchange = null;
  main.innerHTML = `<p class="muted">Loading…</p>`;
  try { await fn(); } catch (e) { main.innerHTML = `<div class="panel"><b>Couldn't load this page.</b><p class="muted">${esc(e.message)}</p></div>`; }
  main.focus();
}
window.addEventListener("hashchange", route);

async function refreshMode() {
  STATE.settings = await api("/api/settings");
  const s = STATE.settings;
  $("#mode-badge").innerHTML = s.mode === "live"
    ? `<b>Live mode</b><br>Keepa ${s.keepa_ready ? "connected" : "not set"} · Amazon ${s.spapi_ready ? "connected" : "not set"}`
    : `<b>Demo mode</b><br>Sample data, fictional brands. Add keys in Settings to go live.`;
}

// ---------------------------------------------------------------- overview
function monthlyChart(rows, goal) {
  if (!rows.length) return `<div class="empty">No sales yet. Profit by month appears here after your first sale.</div>`;
  const W = 720, H = 240, L = 54, B = 30, T = 18;
  const vals = rows.map((r) => r.net_profit);
  const hi = Math.max(...vals, 100) * 1.2, lo = Math.min(0, ...vals) * 1.2;
  const y = (v) => T + (hi - v) / (hi - lo) * (H - T - B);
  const bw = Math.min(46, (W - L - 10) / rows.length * 0.6);
  const step = (W - L - 10) / rows.length;
  const ticks = [];
  const tickStep = hi > 4000 ? 2000 : hi > 1500 ? 500 : hi > 600 ? 250 : 100;
  for (let v = Math.ceil(lo / tickStep) * tickStep; v <= hi; v += tickStep) ticks.push(v);
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Net profit by month">
    ${ticks.map((v) => `<line x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line-2)"/><text x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${money0(v)}</text>`).join("")}
    <line x1="${L}" x2="${W}" y1="${y(0)}" y2="${y(0)}" stroke="var(--ink-3)"/>
    ${rows.map((r, i) => {
      const x = L + i * step + (step - bw) / 2, v = r.net_profit, top = y(Math.max(v, 0)), h = Math.abs(y(v) - y(0));
      const last = i === rows.length - 1;
      return `<g><rect x="${x}" y="${top}" width="${bw}" height="${Math.max(h, 1)}" rx="2" fill="${v < 0 ? "var(--pass)" : last ? "var(--teal)" : "var(--ink-3)"}" opacity="${v < 0 ? .6 : last ? 1 : .55}"><title>${esc(r.month)}: ${money(v)} net profit, ${int(r.units)} units</title></rect>
        <text class="lbl" x="${x + bw / 2}" y="${v < 0 ? y(0) - 6 : top - 6}" text-anchor="middle">${money0(v)}</text>
        <text x="${x + bw / 2}" y="${H - 10}" text-anchor="middle">${monthName(r.month)}</text></g>`;
    }).join("")}
  </svg>`;
}

PAGES.overview = async () => {
  const s = await api("/api/summary");
  const g = s.goal, p = s.position;
  const fill = Math.max(0, Math.min(1, g.profit_this_month / g.target));
  const pace = Math.max(0, Math.min(1, g.pace_this_month / g.target));
  const done = s.checklist.filter((c) => c.done).length;
  const lc = s.lead_counts || {};
  main.innerHTML = `
    <div class="spread"><h1>${esc(s.business)}</h1><span class="muted small">${new Date(s.today + "T12:00:00").toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" })}</span></div>
    <p class="lede">${g.months_to_goal ? `At your current ${pct(g.observed_roi)} return and ${g.turns_per_month.toFixed(2)} inventory turns a month, reinvesting everything reaches ${money0(g.target)} a month in about <b>${g.months_to_goal} months</b>.` : `At current returns the model doesn't reach ${money0(g.target)} a month within 3 years; raise ROI per deal or turn inventory faster.`}</p>
    <section class="goal">
      <div class="spread"><div><span class="big">${money(g.profit_this_month, 0)}</span><span class="of">net profit in ${monthName(g.month)} of ${money0(g.target)} goal</span></div>
      <span class="muted small">Pace for the month: ${money0(g.pace_this_month)}</span></div>
      <div class="ruler" aria-hidden="true"><div class="track"></div><div class="fill" style="width:${fill * 100}%"></div>
        <div class="pace" style="left:${pace * 100}%"><span>pace</span></div>
        <div class="ticks"><span>$0</span><span>${money0(g.target / 4)}</span><span>${money0(g.target / 2)}</span><span>${money0(g.target * 3 / 4)}</span><span>${money0(g.target)}</span></div></div>
    </section>
    <div class="figures">
      <div><div class="v">${money0(p.cash)}</div><div class="k">Cash to buy with</div><div class="s">keep ~10% as buffer</div></div>
      <div><div class="v">${money0(p.inventory_at_cost)}</div><div class="k">Stock at cost</div><div class="s">${int(p.units_on_hand)} units</div></div>
      <div><div class="v">${pct(g.observed_roi)}</div><div class="k">ROI on sold units</div><div class="s">after every fee</div></div>
      <div><div class="v">${g.turns_per_month.toFixed(2)}×</div><div class="k">Turns per month</div><div class="s">last 60 days</div></div>
      <div><div class="v">${money0(g.capital_needed)}</div><div class="k">Capital for goal</div><div class="s">at today's ROI and turns</div></div>
    </div>
    <div class="grid2">
      <div><h2>Net profit by month</h2><div class="chart panel">${monthlyChart(s.monthly, g.target)}</div>
        <h2>Pipeline</h2>
        <div class="row small">
          <a class="btn sm primary" href="#/deals?run=1">Find best deals</a>
          <a class="btn sm" href="#/leads">${int(lc.BUY || 0)} BUY leads</a>
          <a class="btn sm" href="#/leads">${int(lc.MAYBE || 0)} MAYBE</a>
          <a class="btn sm" href="#/inventory">${int((p.by_status.bought || {}).units || 0)} units to prep</a>
          <a class="btn sm" href="#/shipments">${int((p.by_status.shipped || {}).units || 0)} units inbound</a>
          <a class="btn sm" href="#/inventory">${int((p.by_status.live || {}).units || 0)} units live</a>
          <a class="btn sm" href="#/setup">Checklist ${done}/${s.checklist.length}</a>
        </div>
      </div>
      <div><div class="spread"><h2>Needs attention</h2><button class="btn sm" id="sync">Sync now</button></div>
        <div class="panel">${s.alerts.length ? `<ul class="alerts">${s.alerts.slice(0, 12).map((a) => `<li class="${esc(a.severity)}"><div class="a-t">${esc(a.title)}</div><div class="a-d">${esc(a.detail || "")}</div></li>`).join("")}</ul>` : `<div class="empty">Nothing needs you right now.</div>`}</div>
        <h2>Recent activity</h2>
        <div class="small">${s.events.slice(0, 8).map((e) => `<div class="act"><span>${esc(e.message)}</span><span class="muted">${esc(e.at.slice(5, 16).replace("T", " "))}</span></div>`).join("") || `<p class="muted">No activity yet.</p>`}</div>
      </div>
    </div>`;
  $("#sync").onclick = (e) => run(e.target, async () => {
    const r = await api("/api/sync", {});
    toast(`Synced: ${r.sales_added ?? 0} new sales`);
    route();
  });
};

// ------------------------------------------------------------- deal check
function waterfall(ev) {
  const f = ev.fees;
  const items = [
    ["Sale price", ev.sell_price, "in"],
    ["Referral fee", -f.referral, "out"],
    [`FBA fee${f.season === "peak" ? " (peak)" : ""} + fuel`, -f.fulfillment, "out"],
    ["Placement fee", -f.placement, "out"],
    ["Storage (est.)", -f.storage, "out"],
    ["Prep and label", -f.prep, "out"],
    ["Ship to Amazon", -f.inbound_shipping, "out"],
    ["Returns reserve", -f.returns_reserve, "out"],
    ["Your cost (landed)", -ev.landed_cost, "out"],
  ];
  const total = ev.sell_price;
  let run = total;
  const rows = items.map(([k, v, kind], i) => {
    let from, to;
    if (i === 0) { from = 0; to = total; } else { from = run + v; to = run; run += v; }
    const l = Math.max(0, Math.min(from, to)) / total * 100, w = Math.abs(to - from) / total * 100;
    return `<div>${esc(k)}</div><div class="bar"><i class="${kind}" style="left:${l}%;width:${Math.max(w, .4)}%"></i></div><div class="amt">${v < 0 ? "−" : ""}${money(Math.abs(v))}</div>`;
  }).join("");
  const keep = Math.max(0, ev.profit) / total * 100;
  return `<div class="wf">${rows}<div><b>Profit per unit</b></div><div class="bar"><i class="keep" style="left:0;width:${Math.max(keep, .4)}%"></i></div><div class="amt"><b class="${sign(ev.profit)}">${money(ev.profit)}</b></div></div>`;
}

function evaluationView(ev, lead) {
  if (ev.verdict === "UNKNOWN" || !ev.fees) return `<div class="panel"><b>Can't score this listing:</b> ${esc(ev.reason || "missing data")}</div>`;
  const p = ev.product;
  const fails = ev.checks.filter((c) => c.status === "fail").length;
  const sub = ev.verdict === "BUY" ? "passes every check" : ev.verdict === "MAYBE" ? "check the amber items" : `${fails} check${fails === 1 ? "" : "s"} failed`;
  return `
  <div class="panel">
    <div class="verdict">
      <div class="sticker ${esc(ev.verdict)}"><div><b>${esc(ev.verdict)}</b><small>${esc(sub)}</small></div></div>
      <div>
        <div class="small muted">${esc(p.brand || "")} · ${esc(p.category || "")} · <a href="https://www.amazon.com/dp/${esc(p.asin)}" target="_blank" rel="noopener">${esc(p.asin)}</a>${p.source === "demo" ? " · demo data" : ""}</div>
        <div style="font-weight:600;margin:2px 0 12px">${esc(p.title)}</div>
        <div class="kv">
          <div><div class="v ${sign(ev.profit)}">${money(ev.profit)}</div><div class="k">profit per unit</div></div>
          <div><div class="v">${pct(ev.roi)}</div><div class="k">ROI · ${pct(ev.margin)} margin</div></div>
          <div><div class="v">${money(ev.max_buy_cost)}</div><div class="k">most you should pay</div></div>
          <div><div class="v">${int(ev.suggested_qty)}</div><div class="k">units to buy</div></div>
        </div>
      </div>
    </div>
  </div>
  <div class="grid2" style="margin-top:18px">
    <div><h3>Where the ${money(ev.sell_price)} goes</h3>${waterfall(ev)}
      <p class="small muted">Sell price: ${esc(ev.price_note)}. Fees: ${esc(ev.fees.source === "rate-card" ? "2026 rate card" : ev.fees.source)}, ${esc(ev.fees.tier.replace("_", " "))}, ${ev.fees.shipping_weight_lb} lb shipping weight.${ev.fees.notes.length ? " " + esc(ev.fees.notes.join("; ")) + "." : ""}</p></div>
    <div><h3>Buy filter</h3><ul class="checks">${ev.checks.map((c) => `<li><span class="dot ${esc(c.status)}" title="${esc(c.status)}"></span><span>${esc(c.label)}</span><span class="muted">${esc(c.detail)}</span></li>`).join("")}</ul>
      <p class="small muted">Demand: ~${int(ev.monthly_sales_est)} sales a month on the listing (${esc(ev.sales_method)}), about ${ev.your_monthly_share} a month for you.</p></div>
  </div>
  <div class="row" style="margin-top:14px">
    ${lead ? "" : `<button class="btn" data-act="save-lead">Save as lead</button>`}
    <button class="btn primary" data-act="buy" ${ev.verdict === "PASS" ? "" : ""}>Record a purchase</button>
  </div>`;
}

PAGES.check = async () => {
  const q = new URLSearchParams(location.hash.split("?")[1] || "");
  main.innerHTML = `
    <h1>Check a deal</h1>
    <p class="lede">Paste an ASIN, a UPC or an Amazon link with what one unit costs you. ArbitrageOS runs the 2026 fee stack and your buy filter.</p>
    <form id="chk" class="panel">
      <div class="fields">
        <div class="wide"><label for="cq">ASIN, UPC or Amazon link</label><input id="cq" name="query" required value="${esc(q.get("q") || "")}" placeholder="B0DEMO0003 or 000000100031"></div>
        <div><label for="cc">Your cost per unit ($)</label><input id="cc" name="buy_cost" type="number" step="0.01" min="0" required value="${esc(q.get("cost") || "")}"></div>
        <div><label for="ccb">Cashback (%)</label><input id="ccb" name="cashback_pct" type="number" step="0.1" min="0" placeholder="${STATE.settings ? (STATE.settings.costs.default_cashback_pct * 100).toFixed(0) : 2}"></div>
        <div><label for="csp">Sell price override ($)</label><input id="csp" name="sell_price" type="number" step="0.01" min="0" placeholder="use Buy Box"></div>
        <div><label for="cex">Days until expiry</label><input id="cex" name="expiry_days" type="number" min="0" placeholder="if it expires"></div>
      </div>
      <div class="row" style="margin-top:14px"><button class="btn primary">Check deal</button><span class="small muted">Demo UPCs run 000000100017 to 000000100246.</span></div>
    </form>
    <div id="chk-out" style="margin-top:20px"></div>`;
  const form = $("#chk");
  form.onsubmit = (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(form));
    run(form.querySelector("button"), async () => {
      const r = await api("/api/check", body);
      const out = $("#chk-out");
      out.innerHTML = r.results.map((ev, i) => `<section data-i="${i}">${r.results.length > 1 ? `<h2>Match ${i + 1}</h2>` : ""}${evaluationView(ev)}</section>`).join("");
      out.onclick = (ev) => {
        const b = ev.target.closest("[data-act]");
        if (!b) return;
        const res = r.results[+b.closest("section").dataset.i];
        if (b.dataset.act === "save-lead") run(b, async () => {
          await api("/api/leads", { asin: res.asin, buy_cost: body.buy_cost, cashback_pct: body.cashback_pct, title: res.product.title, upc: res.product.upc, retailer: "" });
          toast("Saved to leads");
        });
        if (b.dataset.act === "buy") purchaseForm([{ asin: res.asin, title: res.product.title, qty: res.suggested_qty || 1, unit_cost: body.buy_cost }]);
      };
    });
  };
  if (q.get("q") && q.get("cost")) form.requestSubmit();
};


// --------------------------------------------------------------- find deals
let scanTimer = null;
PAGES.deals = async () => {
  const q = new URLSearchParams(location.hash.split("?")[1] || "");
  const st = await api("/api/scan");
  const sort = sessionStorage.getItem("dealSort") || "margin";
  const inc = sessionStorage.getItem("dealInclude") || "good";
  const d = st.defaults;
  const ready = st.sources.filter((x) => x.ready);
  const live = st.mode === "live";
  main.innerHTML = `
    <div class="spread"><h1>Find deals</h1><span class="small muted" id="lastscan"></span></div>
    <p class="lede">One click pulls discounted products from your deal sources, matches each to its Amazon listing, runs every fee and your buy filter, and ranks what's left by margin.</p>
    <section class="finder panel">
      <div class="finder-go">
        <button class="btn primary big" id="go" ${ready.length ? "" : "disabled"}>Find best deals</button>
        <button class="btn" id="stop" hidden>Stop</button>
      </div>
      <div class="finder-opts">
        <div class="srcs">${st.sources.map((x) => `<label class="src ${x.ready ? "" : "off"}"><input type="checkbox" value="${esc(x.key)}" ${x.ready ? "checked" : "disabled"}> <span><b>${esc(x.name)}</b><br><span class="muted small">${esc(x.note)}</span></span></label>`).join("")}</div>
        <div class="fields" style="margin-top:12px">
          <div><label for="fmax">Products to check</label><select id="fmax">${[25, 60, 100, 200].map((n) => `<option ${n === +d.max_items ? "selected" : ""} value="${n}">${n}${live ? ` (≈${Math.ceil(n * 3 / 20)} min of Keepa)` : ""}</option>`).join("")}</select></div>
          <div><label for="fdisc">Retailer discount at least</label><select id="fdisc">${[0.2, 0.3, 0.4, 0.5].map((v) => `<option ${Math.abs(v - d.min_discount) < .001 ? "selected" : ""} value="${v}">${v * 100}% off</option>`).join("")}</select></div>
          <div><label for="fprice">Highest buy price</label><select id="fprice">${[50, 100, 150, 250].map((v) => `<option ${v === +d.max_price ? "selected" : ""} value="${v}">$${v}</option>`).join("")}</select></div>
        </div>
        ${ready.length ? "" : `<p class="small" style="margin-bottom:0">No source is ready yet. ${live ? "Add a Best Buy API key or a lead feed, plus your Keepa key, in <a href='#/settings'>Settings</a>." : ""}</p>`}
      </div>
      <div class="prog" id="prog" hidden><div class="bar"><i id="pbar"></i></div>
        <div class="spread small"><span id="pstage"></span><span id="pcount" class="muted"></span></div>
        <div class="small muted" id="plog"></div></div>
    </section>
    <div class="spread" style="margin-top:24px"><h2 style="margin:0">Best deals</h2>
      <div class="row">
        <div class="seg" id="dinc">${[["buy", "Buy only"], ["good", "Buy and maybe"], ["all", "Everything"]].map(([k, t]) => `<button data-v="${k}" class="${inc === k ? "on" : ""}">${t}</button>`).join("")}</div>
        <div class="seg" id="dsort">${[["margin", "Margin"], ["roi", "ROI"], ["profit", "Profit per unit"], ["monthly", "Profit per month"]].map(([k, t]) => `<button data-v="${k}" class="${sort === k ? "on" : ""}">${t}</button>`).join("")}</div>
      </div></div>
    <div id="dres" style="margin-top:12px"></div>`;
  const opts = () => ({ sources: $$(".src input:checked").map((c) => c.value), max_items: +$("#fmax").value, min_discount: +$("#fdisc").value, max_price: +$("#fprice").value });
  $("#go").onclick = (e) => run(e.target, async () => { await api("/api/scan", opts()); poll(); });
  $("#stop").onclick = (e) => run(e.target, async () => { await api("/api/scan/cancel", {}); });
  $("#dsort").onclick = (e) => { const b = e.target.closest("button"); if (b) { sessionStorage.setItem("dealSort", b.dataset.v); loadDeals(); $$("#dsort button").forEach((x) => x.classList.toggle("on", x === b)); } };
  $("#dinc").onclick = (e) => { const b = e.target.closest("button"); if (b) { sessionStorage.setItem("dealInclude", b.dataset.v); loadDeals(); $$("#dinc button").forEach((x) => x.classList.toggle("on", x === b)); } };
  showState(st.state, st.last);
  await loadDeals();
  if (st.state.status === "running") poll();
  else if (q.get("run") === "1" && ready.length) { history.replaceState(null, "", "#/deals"); $("#go").click(); }
};

function showState(s, last) {
  const running = s.status === "running";
  if (!$("#go")) return;
  $("#go").hidden = running;
  $("#stop").hidden = !running;
  $("#prog").hidden = !(running || s.status === "done" || s.status === "cancelled" || s.status === "error");
  $("#pbar").style.width = `${Math.round((s.progress || 0) * 100)}%`;
  $("#pbar").className = s.status === "error" ? "err" : "";
  $("#pstage").textContent = s.status === "error" ? `Scan failed: ${s.error}` : (s.stage || "");
  $("#pcount").textContent = s.total ? `${int(s.checked)} of ${int(s.total)} checked · ${int(s.buys)} buy · ${int(s.maybes)} maybe${s.est_minutes ? ` · about ${s.est_minutes} min of Keepa tokens` : ""}` : "";
  $("#plog").textContent = (s.log || []).join(" · ");
  if (last && last.finished_at) $("#lastscan").textContent = `Last scan ${new Date(last.finished_at).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}: ${last.message || last.status}`;
}

function poll() {
  clearInterval(scanTimer);
  let seen = 0;
  scanTimer = setInterval(async () => {
    if (!$("#prog")) return clearInterval(scanTimer);
    const st = await api("/api/scan").catch(() => null);
    if (!st) return;
    showState(st.state, st.last);
    if (st.state.checked !== seen) { seen = st.state.checked; loadDeals(); }
    if (st.state.status !== "running") {
      clearInterval(scanTimer);
      loadDeals();
      if (st.state.status === "done") toast(st.state.stage);
    }
  }, 900);
}

async function loadDeals() {
  const box = $("#dres");
  if (!box) return;
  const sort = sessionStorage.getItem("dealSort") || "margin", inc = sessionStorage.getItem("dealInclude") || "good";
  const r = await api(`/api/deals?sort=${sort}&include=${inc}`);
  if (!r.scan) { box.innerHTML = `<div class="panel empty">No scans yet. Press Find best deals.</div>`; return; }
  if (!r.deals.length) { box.innerHTML = `<div class="panel empty">${r.scan.status === "running" ? "Checking deals…" : "Nothing in this scan passed. Try a bigger scan or a lower discount."}</div>`; return; }
  box.innerHTML = `<div class="tbl-wrap"><table><thead><tr><th class="n">#</th><th>Verdict</th><th>Product</th><th>Buy at</th><th class="n">Cost</th><th class="n">Sells for</th><th class="n">Profit</th><th class="n">Margin</th><th class="n">ROI</th><th class="n">Sales/mo</th><th class="n">Profit/mo</th><th class="n">Qty</th><th></th></tr></thead><tbody>
    ${r.deals.map((l, i) => { const ev = l.evaluation || {}; return `<tr data-id="${l.id}">
      <td class="n muted">${i + 1}</td><td>${verdictTag(l.verdict)}${l.flip ? ` <span class="pill">flip</span>` : ""}${l.status === "bought" ? ` <span class="pill">bought</span>` : ""}</td>
      <td><span class="t" title="${esc(l.title)}">${esc(l.title || l.upc)}</span><a class="small" href="https://www.amazon.com/dp/${esc(l.asin || "")}" target="_blank" rel="noopener">${esc(l.asin || "")}</a>${l.regular_price ? ` <span class="small muted">was ${money(l.regular_price)} (${pct(1 - l.buy_cost / l.regular_price)} off)</span>` : ""}</td>
      <td style="white-space:nowrap">${l.retailer_url ? `<a href="${esc(l.retailer_url)}" target="_blank" rel="noopener">${esc(l.retailer || "link")}</a>` : esc(l.retailer || "")}</td>
      <td class="n">${money(l.buy_cost)}</td><td class="n">${money(ev.sell_price)}</td>
      <td class="n ${sign(l.profit)}"><b>${money(l.profit)}</b></td><td class="n"><b>${pct(l.margin)}</b></td><td class="n">${pct(l.roi)}</td>
      <td class="n">${int(l.monthly_sales)}</td><td class="n">${money0(l.monthly_profit)}</td><td class="n">${int(l.suggested_qty)}</td>
      <td class="n" style="white-space:nowrap"><button class="btn sm" data-a="view">Details</button> ${l.verdict !== "PASS" && l.status !== "bought" ? `<button class="btn sm" data-a="buy">Buy</button>` : ""}</td></tr>`; }).join("")}
  </tbody></table></div>
  <p class="small muted">Profit per month = profit per unit × your expected share of the listing's monthly sales. Prices move fast; check the retailer page before you buy.</p>`;
  box.onclick = (e) => {
    const b = e.target.closest("[data-a]");
    if (!b) return;
    const l = r.deals.find((x) => x.id === +b.closest("tr").dataset.id);
    if (b.dataset.a === "buy") purchaseForm([{ asin: l.asin, title: l.title, qty: l.suggested_qty || 1, unit_cost: l.buy_cost, lead_id: l.id }], { retailer: l.retailer });
    if (b.dataset.a === "view") {
      const body = modal(`<div class="spread"><h2 style="margin-top:0">Deal details</h2><button class="btn sm" data-close>Close</button></div>${evaluationView(l.evaluation, l)}`);
      body.onclick = (ev) => { if (ev.target.closest("[data-act=buy]")) purchaseForm([{ asin: l.asin, title: l.title, qty: l.suggested_qty || 1, unit_cost: l.buy_cost, lead_id: l.id }], { retailer: l.retailer }); };
    }
  };
}

// ------------------------------------------------------------------ leads
PAGES.leads = async () => {
  const filt = sessionStorage.getItem("leadFilter") || "open";
  const all = (await api("/api/leads")).leads;
  const show = all.filter((l) => filt === "all" ? true : filt === "open" ? ["new", "scored"].includes(l.status) : l.verdict === filt && ["new", "scored"].includes(l.status));
  const count = (v) => all.filter((l) => l.verdict === v && ["new", "scored"].includes(l.status)).length;
  main.innerHTML = `
    <div class="spread"><h1>Leads</h1><div class="row"><button class="btn" id="add-lead">Add a lead</button><button class="btn primary" id="imp">Import CSV</button></div></div>
    <p class="lede">Retail deals waiting for a decision. Import a lead list or a Tactical Arbitrage export, and each row is matched to Amazon and scored against your buy filter.</p>
    <div class="toolbar"><div class="seg" id="lf">
      ${[["open", `Open (${all.filter((l) => ["new", "scored"].includes(l.status)).length})`], ["BUY", `Buy (${count("BUY")})`], ["MAYBE", `Maybe (${count("MAYBE")})`], ["PASS", `Pass (${count("PASS")})`], ["all", "All"]]
        .map(([k, t]) => `<button data-f="${k}" class="${filt === k ? "on" : ""}">${t}</button>`).join("")}
    </div><button class="btn sm" id="rescore">Rescore open leads</button></div>
    ${show.length ? `<div class="tbl-wrap"><table><thead><tr><th>Verdict</th><th>Product</th><th>Retailer</th><th class="n">Cost</th><th class="n">Max cost</th><th class="n">Profit</th><th class="n">ROI</th><th class="n">Sales/mo</th><th class="n">Qty</th><th></th></tr></thead><tbody>
      ${show.map((l) => `<tr data-id="${l.id}">
        <td>${verdictTag(l.verdict)}${l.status === "bought" ? ` <span class="pill">bought</span>` : l.status === "skipped" ? ` <span class="pill">skipped</span>` : ""}</td>
        <td><span class="t" title="${esc(l.title)}">${esc(l.title || l.upc || l.asin)}</span><span class="small muted">${esc(l.asin || "")} ${l.upc ? "· UPC " + esc(l.upc) : ""}</span></td>
        <td>${l.retailer_url ? `<a href="${esc(l.retailer_url)}" target="_blank" rel="noopener">${esc(l.retailer || "link")}</a>` : esc(l.retailer || "")}</td>
        <td class="n">${money(l.buy_cost)}</td><td class="n">${money(l.max_buy_cost)}</td>
        <td class="n ${sign(l.profit)}">${money(l.profit)}</td><td class="n">${pct(l.roi)}</td><td class="n">${int(l.monthly_sales)}</td><td class="n">${int(l.suggested_qty)}</td>
        <td class="n"><button class="btn sm" data-a="view">Details</button> ${l.verdict !== "PASS" && l.asin ? `<button class="btn sm" data-a="buy">Buy</button>` : ""} ${["new", "scored"].includes(l.status) ? `<button class="btn sm" data-a="skip">Skip</button>` : ""}</td></tr>`).join("")}
    </tbody></table></div>` : `<div class="panel empty">No leads here. Import a CSV with a cost column and an ASIN or UPC column, or add one by hand.</div>`}`;
  $("#lf").onclick = (e) => { const b = e.target.closest("button"); if (b) { sessionStorage.setItem("leadFilter", b.dataset.f); route(); } };
  $("#rescore").onclick = (e) => run(e.target, async () => { const r = await api("/api/leads/score", { rescore: true }); toast(`Rescored ${r.scored} leads`); route(); });
  $("#imp").onclick = importDialog;
  $("#add-lead").onclick = addLeadDialog;
  main.onclick = (e) => {
    const b = e.target.closest("[data-a]");
    if (!b) return;
    const l = all.find((x) => x.id === +b.closest("tr").dataset.id);
    if (b.dataset.a === "skip") run(b, async () => { await api(`/api/leads/${l.id}/status`, { status: "skipped" }); route(); });
    if (b.dataset.a === "buy") purchaseForm([{ asin: l.asin, title: l.title, qty: l.suggested_qty || 1, unit_cost: l.buy_cost, lead_id: l.id }], { retailer: l.retailer });
    if (b.dataset.a === "view") {
      const body = modal(`<div class="spread"><h2 style="margin-top:0">Lead details</h2><button class="btn sm" data-close>Close</button></div>${l.evaluation && l.evaluation.fees ? evaluationView(l.evaluation, l) : `<p>${esc((l.evaluation || {}).reason || "Not scored yet.")}</p>`}`);
      body.onclick = (ev) => { const x = ev.target.closest("[data-act=buy]"); if (x) purchaseForm([{ asin: l.asin, title: l.title, qty: l.suggested_qty || 1, unit_cost: l.buy_cost, lead_id: l.id }], { retailer: l.retailer }); };
    }
  };
};

function importDialog() {
  const b = modal(`<h2 style="margin-top:0">Import leads</h2>
    <p class="small muted">Any CSV with a cost column and an ASIN or UPC column works. Columns named like retailer, URL, title, cashback and notes are picked up too.</p>
    <label for="impf">CSV file</label><input type="file" id="impf" accept=".csv,.tsv,.txt,text/csv">
    <label for="impt" style="margin-top:12px">Or paste rows</label><textarea id="impt" placeholder="UPC,Cost,Retailer,URL&#10;000000100031,4.49,Walgreens,https://..."></textarea>
    <div class="row" style="margin-top:14px"><button class="btn primary" id="impgo">Import and score</button><button class="btn" data-close>Cancel</button></div>`);
  $("#impf", b).onchange = async (e) => { const f = e.target.files[0]; if (f) $("#impt", b).value = await f.text(); };
  $("#impgo", b).onclick = (e) => run(e.target, async () => {
    const r = await api("/api/leads/import", { csv: $("#impt", b).value, source: "csv" });
    const c = r.score.counts || {};
    toast(`Imported ${r.imported}: ${c.BUY || 0} buy, ${c.MAYBE || 0} maybe, ${c.PASS || 0} pass, ${c.NO_MATCH || 0} unmatched`);
    closeModal(); route();
  });
}

function addLeadDialog() {
  const b = modal(`<h2 style="margin-top:0">Add a lead</h2><form id="lf2"><div class="fields">
    <div><label>ASIN</label><input name="asin"></div><div><label>or UPC</label><input name="upc"></div>
    <div><label>Cost per unit ($)</label><input name="buy_cost" type="number" step="0.01" required></div>
    <div><label>Cashback (%)</label><input name="cashback_pct" type="number" step="0.1"></div>
    <div><label>Retailer</label><input name="retailer" list="retailers"></div>
    <div class="wide"><label>Product link</label><input name="retailer_url"></div>
    <div class="wide"><label>Notes</label><input name="notes"></div></div>
    <datalist id="retailers">${(STATE.settings.watchlists.preferred_retailers || []).map((r) => `<option value="${esc(r)}">`).join("")}</datalist>
    <div class="row" style="margin-top:14px"><button class="btn primary">Add and score</button><button type="button" class="btn" data-close>Cancel</button></div></form>`);
  $("#lf2", b).onsubmit = (e) => { e.preventDefault(); run(e.submitter, async () => { await api("/api/leads", Object.fromEntries(new FormData(e.target))); closeModal(); toast("Lead added"); route(); }); };
}

// --------------------------------------------------------------- purchases
function purchaseForm(items, defaults = {}) {
  const today = localDate();
  const itemRow = (it = {}) => `<tr><td><input name="asin" value="${esc(it.asin || "")}" required></td><td><input name="title" value="${esc(it.title || "")}"></td>
    <td><input name="qty" type="number" min="1" value="${esc(it.qty || 1)}" required style="width:70px"></td>
    <td><input name="unit_cost" type="number" step="0.01" min="0" value="${esc(it.unit_cost || "")}" required style="width:90px"></td>
    <td><input name="expiry" type="date" style="width:140px"></td><td><input type="hidden" name="lead_id" value="${esc(it.lead_id || "")}"><button type="button" class="btn sm" data-rm>Remove</button></td></tr>`;
  const b = modal(`<h2 style="margin-top:0">Record a purchase</h2>
    <p class="small muted">One purchase = one order or receipt. Attach the receipt so you can answer an Amazon invoice request.</p>
    <form id="pf"><div class="fields">
      <div><label>Date</label><input name="bought_on" type="date" value="${today}"></div>
      <div><label>Retailer</label><input name="retailer" value="${esc(defaults.retailer || "")}" list="retailers2"></div>
      <div><label>Order number</label><input name="order_number"></div>
      <div><label>Proof type</label><select name="receipt_kind"><option value="online_order">Online order page</option><option value="store_receipt">Store receipt</option><option value="invoice">Wholesale invoice</option></select></div>
      <div><label>Sales tax paid ($)</label><input name="sales_tax" type="number" step="0.01" value="0"></div>
      <div><label>Cashback earned ($)</label><input name="cashback" type="number" step="0.01" value="0"></div>
      <div><label>Shipping paid ($)</label><input name="shipping" type="number" step="0.01" value="0"></div>
      <div><label>Receipt file</label><input name="receipt" type="file" accept="image/*,.pdf"></div>
    </div>
    <datalist id="retailers2">${(STATE.settings.watchlists.preferred_retailers || []).map((r) => `<option value="${esc(r)}">`).join("")}</datalist>
    <h3>Items</h3><div class="tbl-wrap"><table class="items-tbl"><thead><tr><th>ASIN</th><th>Title</th><th>Qty</th><th>Unit cost</th><th>Expiry</th><th></th></tr></thead><tbody id="pitems">${items.map(itemRow).join("") || itemRow()}</tbody></table></div>
    <div class="row" style="margin-top:8px"><button type="button" class="btn sm" id="padd">Add item</button></div>
    <div class="row" style="margin-top:16px"><button class="btn primary">Save purchase</button><button type="button" class="btn" data-close>Cancel</button></div></form>`);
  $("#padd", b).onclick = () => $("#pitems", b).insertAdjacentHTML("beforeend", itemRow());
  $("#pitems", b).onclick = (e) => { if (e.target.closest("[data-rm]")) e.target.closest("tr").remove(); };
  $("#pf", b).onsubmit = (e) => {
    e.preventDefault();
    const f = e.target, fd = new FormData(f);
    const body = Object.fromEntries([...fd].filter(([k]) => !["asin", "title", "qty", "unit_cost", "expiry", "lead_id", "receipt"].includes(k)));
    body.items = $$("#pitems tr", b).map((tr) => {
      const g = (n) => tr.querySelector(`[name=${n}]`).value;
      return { asin: g("asin"), title: g("title"), qty: +g("qty"), unit_cost: +g("unit_cost"), expiry: g("expiry") || null, lead_id: g("lead_id") ? +g("lead_id") : null };
    });
    run(e.submitter, async () => {
      const r = await api("/api/purchases", body);
      const file = f.receipt.files[0];
      if (file) await uploadReceipt(r.purchase_id, file);
      closeModal();
      toast(`Purchase saved: ${body.items.reduce((s, i) => s + i.qty, 0)} units in inventory`);
      location.hash = "#/inventory";
      route();
    });
  };
}

function fileB64(file) {
  return new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(String(r.result).split(",")[1]); r.onerror = rej; r.readAsDataURL(file); });
}
async function uploadReceipt(pid, file) {
  await api(`/api/purchases/${pid}/receipt`, { filename: file.name, content_b64: await fileB64(file) });
}

// --------------------------------------------------------------- inventory
PAGES.inventory = async () => {
  const filt = sessionStorage.getItem("invFilter") || "active";
  const lots = (await api(`/api/lots${filt === "active" ? "?active=1" : ""}`)).lots;
  const statuses = ["bought", "prepped", "shipped", "live", "sold_out", "removed"];
  main.innerHTML = `
    <div class="spread"><h1>Inventory</h1><button class="btn primary" id="newp">Record a purchase</button></div>
    <p class="lede">Every lot you bought, from the receipt to the last unit sold. Select lots to print FNSKU labels or build an FBA shipment.</p>
    <div class="toolbar">
      <div class="seg" id="if">${[["active", "In stock"], ["all", "All lots"]].map(([k, t]) => `<button data-f="${k}" class="${filt === k ? "on" : ""}">${t}</button>`).join("")}</div>
      <select id="lfmt" style="width:auto">${Object.entries({ avery5160: "30 per sheet (Avery 5160)", avery5167: "80 per sheet (Avery 5167)", thermal225: "Thermal 2.25 × 1.25 in", thermal2x1: "Thermal 2 × 1 in" }).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select>
      <button class="btn" id="plab">Print labels</button><button class="btn" id="mship">Create shipment</button>
      <span class="small muted" id="selc">0 selected</span>
    </div>
    ${lots.length ? `<div class="tbl-wrap"><table><thead><tr><th><input type="checkbox" id="selall" aria-label="Select all"></th><th>Status</th><th>Product</th><th>SKU / FNSKU</th><th class="n">Left / bought</th><th class="n">Landed cost</th><th class="n">Floor</th><th class="n">Ceiling</th><th>Bought</th><th>Proof</th></tr></thead><tbody>
      ${lots.map((l) => `<tr data-id="${l.id}" data-pid="${l.purchase_id}">
        <td><input type="checkbox" class="sel" aria-label="Select lot"></td>
        <td><select class="st" style="width:auto;padding:3px 6px">${statuses.map((s) => `<option ${s === l.status ? "selected" : ""} value="${s}">${s.replace("_", " ")}</option>`).join("")}</select></td>
        <td><span class="t" title="${esc(l.title)}">${esc(l.title)}</span><a class="small" href="https://www.amazon.com/dp/${esc(l.asin)}" target="_blank" rel="noopener">${esc(l.asin)}</a></td>
        <td class="small"><span class="mono">${esc(l.sku)}</span><br><input class="fn mono" value="${esc(l.fnsku || "")}" placeholder="FNSKU" style="padding:2px 6px;font-size:12px;width:170px"></td>
        <td class="n">${int(l.qty_left)} / ${int(l.qty)}</td><td class="n">${money(l.landed_unit_cost)}</td><td class="n">${money(l.min_price)}</td><td class="n">${money(l.max_price)}</td>
        <td class="small" style="white-space:nowrap">${esc(l.bought_on || "")}<br><span class="muted">${esc(l.retailer || "")}</span></td>
        <td class="small" style="white-space:nowrap">${l.receipt_path ? `<a href="/receipt/${l.purchase_id}" target="_blank">receipt</a>` : `<label class="btn sm" style="display:inline-block;margin:0">Attach<input type="file" class="rc" accept="image/*,.pdf" hidden></label>`}<br><a href="#" data-inv="${esc(l.asin)}">invoice pack</a></td></tr>`).join("")}
    </tbody></table></div>` : `<div class="panel empty">No stock yet. Buy a deal from Leads or record a purchase.</div>`}`;
  const sel = () => $$(".sel:checked").map((c) => +c.closest("tr").dataset.id);
  const upd = () => ($("#selc").textContent = `${sel().length} selected`);
  $("#if").onclick = (e) => { const b = e.target.closest("button"); if (b) { sessionStorage.setItem("invFilter", b.dataset.f); route(); } };
  $("#newp").onclick = () => purchaseForm([]);
  if ($("#selall")) $("#selall").onchange = (e) => { $$(".sel").forEach((c) => (c.checked = e.target.checked)); upd(); };
  main.onchange = async (e) => {
    const tr = e.target.closest("tr[data-id]");
    if (e.target.classList.contains("sel")) return upd();
    if (!tr) return;
    if (e.target.classList.contains("st")) run(null, async () => { await api(`/api/lots/${tr.dataset.id}`, { status: e.target.value }); toast("Status updated"); });
    if (e.target.classList.contains("fn")) run(null, async () => { await api(`/api/lots/${tr.dataset.id}`, { fnsku: e.target.value.trim().toUpperCase() }); toast("FNSKU saved"); });
    if (e.target.classList.contains("rc") && e.target.files[0]) run(null, async () => { await uploadReceipt(tr.dataset.pid, e.target.files[0]); toast("Receipt attached"); route(); });
  };
  $("#plab").onclick = () => {
    const ids = sel();
    if (!ids.length) return toast("Select the lots to label first", true);
    window.open(`/labels?lots=${ids.join(",")}&fmt=${$("#lfmt").value}`, "_blank");
  };
  $("#mship").onclick = (e) => {
    const ids = sel();
    if (!ids.length) return toast("Select the lots to ship first", true);
    run(e.target, async () => { await api("/api/shipments", { lot_ids: ids }); toast("Shipment created"); location.hash = "#/shipments"; });
  };
  main.onclick = (e) => {
    const a = e.target.closest("[data-inv]");
    if (!a) return;
    e.preventDefault();
    run(null, async () => {
      const r = await api(`/api/invoice/${a.dataset.inv}`);
      modal(`<div class="spread"><h2 style="margin-top:0">Invoice pack for ${esc(r.asin)}</h2><button class="btn sm" data-close>Close</button></div>
        <p>${int(r.units_bought)} units bought, ${int(r.documented_units)} with proof attached${r.has_invoice ? ", including a wholesale invoice" : ". No wholesale invoice yet: Amazon officially accepts invoices, and retail receipts are a fallback"}.</p>
        <div class="tbl-wrap"><table><thead><tr><th>Date</th><th>Retailer</th><th>Order</th><th class="n">Units</th><th class="n">Unit cost</th><th>Proof</th></tr></thead><tbody>
        ${r.purchases.map((p) => `<tr><td>${esc(p.bought_on)}</td><td>${esc(p.retailer || "")}</td><td class="mono">${esc(p.order_number || "")}</td><td class="n">${p.qty}</td><td class="n">${money(p.unit_cost)}</td><td>${p.receipt_path ? `<a href="/receipt/${p.id}" target="_blank">${esc(p.receipt_kind.replace("_", " "))}</a>` : `<span class="neg">missing</span>`}</td></tr>`).join("")}
        </tbody></table></div>`);
    });
  };
};

// --------------------------------------------------------------- shipments
PAGES.shipments = async () => {
  const sh = (await api("/api/shipments")).shipments;
  main.innerHTML = `<h1>Shipments</h1>
    <p class="lede">Group labeled lots into an FBA shipment, create it in Seller Central (Send to Amazon), then track it here until Amazon receives it. Closing a shipment marks its lots live.</p>
    ${sh.length ? sh.map((s) => `<section class="panel" style="margin-bottom:14px" data-id="${s.id}">
      <div class="spread"><div><b>${esc(s.name)}</b> <span class="pill ${s.status === "closed" ? "live" : "shipped"}">${esc(s.status)}</span></div>
      <div class="row"><input class="asid mono" placeholder="Amazon shipment ID (FBA...)" value="${esc(s.amazon_shipment_id || "")}" style="width:220px">
        <select class="sst" style="width:auto">${["draft", "shipped", "receiving", "closed"].map((x) => `<option ${x === s.status ? "selected" : ""}>${x}</option>`).join("")}</select>
        <button class="btn sm" data-a="save">Save</button><a class="btn sm" href="/labels?lots=${s.lots.map((l) => l.id).join(",")}&fmt=thermal225" target="_blank">Labels</a></div></div>
      <p class="small muted">${int(s.units)} units in ${s.lots.length} SKUs · placement option: ${esc((s.placement_option || "").replace("_", " "))} · created ${esc(s.created_at.slice(0, 10))}</p>
      <div class="tbl-wrap"><table><thead><tr><th>SKU</th><th>FNSKU</th><th>Product</th><th class="n">Units</th><th>Status</th></tr></thead><tbody>
      ${s.lots.map((l) => `<tr><td class="mono small">${esc(l.sku)}</td><td class="mono small">${esc(l.fnsku || "–")}</td><td><span class="t">${esc(l.title)}</span></td><td class="n">${l.qty}</td><td><span class="pill ${esc(l.status)}">${esc(l.status.replace("_", " "))}</span></td></tr>`).join("")}
      </tbody></table></div></section>`).join("") : `<div class="panel empty">No shipments yet. In Inventory, select lots and choose Create shipment.</div>`}`;
  main.onclick = (e) => {
    const b = e.target.closest("[data-a=save]");
    if (!b) return;
    const s = b.closest("section");
    run(b, async () => { await api(`/api/shipments/${s.dataset.id}`, { status: $(".sst", s).value, amazon_shipment_id: $(".asid", s).value.trim() || null }); toast("Shipment updated"); route(); });
  };
};

// ------------------------------------------------------------------- sales
PAGES.sales = async () => {
  const d = await api("/api/sales");
  const tot = d.monthly.reduce((a, m) => ({ rev: a.rev + m.revenue, np: a.np + m.net_profit }), { rev: 0, np: 0 });
  main.innerHTML = `<div class="spread"><h1>Sales and profit</h1><div class="row"><button class="btn" id="addexp">Add expense</button><button class="btn" id="addcash">Add or take out cash</button></div></div>
    <p class="lede">${money0(tot.rev)} in sales and ${money0(tot.np)} net profit to date. Fees come from Amazon's settlement data in live mode; cost of goods is matched oldest lot first.</p>
    <h2>By month</h2>
    <div class="tbl-wrap"><table><thead><tr><th>Month</th><th class="n">Units</th><th class="n">Sales</th><th class="n">Amazon fees</th><th class="n">Refunds</th><th class="n">Cost of goods</th><th class="n">Expenses</th><th class="n">Net profit</th><th class="n">ROI</th><th class="n">Stock bought</th></tr></thead><tbody>
    ${d.monthly.slice().reverse().map((m) => `<tr><td>${esc(m.month)}</td><td class="n">${int(m.units)}</td><td class="n">${money(m.revenue)}</td><td class="n">${money(m.fees)}</td><td class="n">${money(m.refunds)}</td><td class="n">${money(m.cogs)}</td><td class="n">${money(m.expenses)}</td><td class="n ${sign(m.net_profit)}"><b>${money(m.net_profit)}</b></td><td class="n">${pct(m.roi)}</td><td class="n">${money(m.inventory_bought)}</td></tr>`).join("") || `<tr><td colspan="10" class="empty">No sales yet.</td></tr>`}
    </tbody></table></div>
    <h2>Best SKUs</h2>
    <div class="tbl-wrap"><table><thead><tr><th>Product</th><th>SKU</th><th class="n">Units</th><th class="n">Sales</th><th class="n">Profit</th><th class="n">ROI</th><th>Last sale</th></tr></thead><tbody>
    ${d.skus.slice(0, 25).map((s) => `<tr><td><span class="t">${esc(s.title || s.asin)}</span></td><td class="mono small">${esc(s.sku)}</td><td class="n">${int(s.units)}</td><td class="n">${money(s.revenue)}</td><td class="n ${sign(s.profit)}">${money(s.profit)}</td><td class="n">${pct(s.roi)}</td><td class="small">${esc((s.last_sale || "").slice(0, 10))}</td></tr>`).join("") || `<tr><td colspan="7" class="empty">No sales yet.</td></tr>`}
    </tbody></table></div>
    <h2>Recent orders</h2>
    <div class="tbl-wrap"><table><thead><tr><th>Date</th><th>Order</th><th>Product</th><th class="n">Price</th><th class="n">Fees</th><th class="n">Cost</th><th class="n">Profit</th></tr></thead><tbody>
    ${d.sales.slice(0, 60).map((s) => `<tr><td class="small">${esc(s.sold_at.slice(0, 10))}</td><td class="mono small">${esc(s.order_id)}</td><td><span class="t">${esc(s.title || s.sku)}</span></td><td class="n">${money(s.item_price)}</td><td class="n">${money(s.referral_fee + s.fba_fee + s.other_fees)}</td><td class="n">${money(s.cogs)}</td><td class="n ${sign(s.profit)}">${money(s.profit)}${s.refund ? ` <span class="pill">refund</span>` : ""}</td></tr>`).join("") || `<tr><td colspan="7" class="empty">No orders yet.</td></tr>`}
    </tbody></table></div>`;
  $("#addexp").onclick = () => {
    const b = modal(`<h2 style="margin-top:0">Add expense</h2><form id="ef"><div class="fields"><div><label>Date</label><input name="spent_on" type="date" value="${localDate()}"></div>
      <div><label>Category</label><select name="category"><option>subscription</option><option>supplies</option><option>prep center</option><option>shipping</option><option>other</option></select></div>
      <div><label>Vendor</label><input name="vendor"></div><div><label>Amount ($)</label><input name="amount" type="number" step="0.01" required></div></div>
      <div class="row" style="margin-top:14px"><button class="btn primary">Add expense</button><button type="button" class="btn" data-close>Cancel</button></div></form>`);
    $("#ef", b).onsubmit = (e) => { e.preventDefault(); run(e.submitter, async () => { await api("/api/expenses", Object.fromEntries(new FormData(e.target))); closeModal(); toast("Expense added"); route(); }); };
  };
  $("#addcash").onclick = () => {
    const b = modal(`<h2 style="margin-top:0">Add or take out cash</h2><form id="cf"><div class="fields"><div><label>Date</label><input name="moved_on" type="date" value="${localDate()}"></div>
      <div><label>Type</label><select name="kind"><option value="deposit">Add money to the business</option><option value="owner_draw">Pay yourself</option><option value="withdrawal">Other withdrawal</option></select></div>
      <div><label>Amount ($)</label><input name="amount" type="number" step="0.01" required></div><div><label>Note</label><input name="notes"></div></div>
      <div class="row" style="margin-top:14px"><button class="btn primary">Save</button><button type="button" class="btn" data-close>Cancel</button></div></form>`);
    $("#cf", b).onsubmit = (e) => { e.preventDefault(); run(e.submitter, async () => { await api("/api/cash", Object.fromEntries(new FormData(e.target))); closeModal(); toast("Saved"); route(); }); };
  };
};

// --------------------------------------------------------------- repricing
PAGES.repricing = async () => {
  const rows = (await api("/api/repricing")).rows;
  const live = STATE.settings.mode === "live" && STATE.settings.spapi_ready;
  main.innerHTML = `<h1>Repricing</h1>
    <p class="lede">Each SKU's floor keeps at least ${pct(STATE.settings.rules.reprice_floor_roi)} ROI and ${money(STATE.settings.rules.reprice_floor_profit)} profit after fees; the ceiling stops the repricer chasing a spike. Load these into Amazon Automate Pricing (free) or a repricer.</p>
    <div class="toolbar"><a class="btn" href="/export/prices?kind=amazon">Download for Seller Central</a><a class="btn" href="/export/prices?kind=repricer">Download for BQool or Aura</a>
      <button class="btn primary" id="push" ${live ? "" : "disabled title='Connect the Selling Partner API in Settings'"}>Push to Amazon</button>
      <button class="btn sm" id="refm">Refresh market prices</button></div>
    ${rows.length ? `<div class="tbl-wrap"><table><thead><tr><th>Product</th><th>SKU</th><th class="n">Units</th><th class="n">Landed cost</th><th class="n">Floor</th><th class="n">Market</th><th class="n">90-day avg</th><th class="n">Ceiling</th><th class="n">List at</th><th>Status</th></tr></thead><tbody>
    ${rows.map((r) => `<tr><td><span class="t">${esc(r.title)}</span></td><td class="mono small">${esc(r.sku)}</td><td class="n">${int(r.qty_left)}</td><td class="n">${money(r.landed_cost)}</td><td class="n">${money(r.min_price)}</td><td class="n">${money(r.market_price)}</td><td class="n">${money(r.avg90)}</td><td class="n">${money(r.max_price)}</td><td class="n"><b>${money(r.price)}</b></td><td>${r.status === "ok" ? `<span class="pill live">ok</span>` : `<span class="pill bought">${esc(r.status)}</span>`}</td></tr>`).join("")}
    </tbody></table></div>` : `<div class="panel empty">No stock to price yet.</div>`}`;
  $("#push").onclick = (e) => run(e.target, async () => { const r = await api("/api/repricing/push", {}); toast(r.error || `Pushed ${r.pushed} SKUs${r.errors && r.errors.length ? `, ${r.errors.length} failed` : ""}`, !!r.error); });
  $("#refm").onclick = (e) => run(e.target, async () => { await api("/api/repricing?refresh=1"); route(); });
};

// ------------------------------------------------------------------- setup
PAGES.setup = async () => {
  const items = (await api("/api/checklist")).items;
  const done = items.filter((i) => i.done).length;
  let phase = "";
  main.innerHTML = `<h1>Launch checklist</h1>
    <p class="lede">${done} of ${items.length} done. These steps need your own ID, signature or bank details, so they happen outside this app; tick them off as you go.</p>
    <div class="panel"><ul class="checklist">${items.map((i) => {
      const head = i.phase !== phase ? ((phase = i.phase), `<li style="display:block;border:0;padding:14px 0 2px"><h3 style="margin:0">${esc(i.phase)}</h3></li>`) : "";
      return head + `<li class="${i.done ? "done" : ""}"><input type="checkbox" data-k="${esc(i.key)}" ${i.done ? "checked" : ""} aria-label="${esc(i.title)}">
        <div><div class="ct">${esc(i.title)}</div><div class="cd">${esc(i.detail)}</div></div>
        <div>${i.link ? `<a class="btn sm" href="${esc(i.link)}" target="_blank" rel="noopener">Open site</a>` : ""}</div></li>`;
    }).join("")}</ul></div>`;
  main.onchange = (e) => { const k = e.target.dataset.k; if (k) run(null, async () => { await api("/api/checklist", { key: k, done: e.target.checked }); route(); }); };
};

// ---------------------------------------------------------------- settings
PAGES.settings = async () => {
  const s = await api("/api/settings");
  const r = s.rules, c = s.costs, b = s.business;
  const num = (name, val, step = "0.01", lab) => `<div><label>${lab}</label><input name="${name}" type="number" step="${step}" value="${esc(val)}"></div>`;
  main.innerHTML = `<h1>Settings</h1>
  <form id="sf">
    <h2>Mode</h2>
    <div class="panel"><div class="row"><div class="seg" id="mode">${["demo", "live"].map((m) => `<button type="button" data-m="${m}" class="${s.mode === m ? "on" : ""}">${m === "demo" ? "Demo" : "Live"}</button>`).join("")}</div>
      <span class="small muted">${s.mode === "demo" ? "Demo uses the fictional catalog. Live uses Keepa for market data and your Amazon account for gating, fees, sales and inventory." : "Live: real market data and your Amazon account."}</span></div></div>
    <h2>Business</h2>
    <div class="panel fields">
      <div class="wide"><label>Store name</label><input name="business.name" value="${esc(b.name)}"></div>
      ${num("business.starting_capital", b.starting_capital, "1", "Starting capital ($)")}
      ${num("business.monthly_profit_goal", b.monthly_profit_goal, "100", "Monthly profit goal ($)")}
      ${num("business.sales_tax_rate", b.sales_tax_rate, "0.0001", "Sales tax rate (0.0725 = 7.25%)")}
      <div><label>Buying with a resale certificate</label><select name="business.use_resale_certificate"><option value="true" ${b.use_resale_certificate ? "selected" : ""}>Yes, tax-free</option><option value="false" ${b.use_resale_certificate ? "" : "selected"}>No, I pay sales tax</option></select></div>
    </div>
    <h2>Buy filter</h2>
    <div class="panel fields">
      ${num("rules.min_roi", r.min_roi, "0.01", "Minimum ROI (0.30 = 30%)")}
      ${num("rules.min_roi_fast_mover", r.min_roi_fast_mover, "0.01", "ROI for fast sellers")}
      ${num("rules.fast_mover_monthly_sales", r.fast_mover_monthly_sales, "1", "Fast seller = sales/month")}
      ${num("rules.min_profit", r.min_profit, "0.25", "Minimum profit per unit ($)")}
      ${num("rules.min_monthly_sales", r.min_monthly_sales, "1", "Minimum listing sales/month")}
      ${num("rules.max_fba_sellers", r.max_fba_sellers, "1", "Most FBA sellers allowed")}
      ${num("rules.max_share_of_capital_per_sku", r.max_share_of_capital_per_sku, "0.01", "Max share of cash per SKU")}
      ${num("rules.months_of_stock", r.months_of_stock, "0.25", "Months of stock to buy")}
      ${num("rules.reprice_floor_roi", r.reprice_floor_roi, "0.01", "Repricer floor ROI")}
      ${num("rules.reprice_floor_profit", r.reprice_floor_profit, "0.25", "Repricer floor profit ($)")}
    </div>
    <h2>Costs</h2>
    <div class="panel fields">
      ${num("costs.prep_per_unit", c.prep_per_unit, "0.05", "Prep per unit ($)")}
      ${num("costs.inbound_per_lb", c.inbound_per_lb, "0.01", "Shipping to Amazon per lb ($)")}
      <div><label>Shipment splits</label><select name="costs.placement_option">${[["amazon_optimized", "Amazon-optimized (no fee)"], ["partial", "Partial (2-3 sites)"], ["minimal", "Minimal (1 site)"]].map(([k, v]) => `<option value="${k}" ${c.placement_option === k ? "selected" : ""}>${v}</option>`).join("")}</select></div>
      ${num("costs.returns_reserve_pct", c.returns_reserve_pct, "0.005", "Returns reserve (share of price)")}
      ${num("costs.default_cashback_pct", c.default_cashback_pct, "0.005", "Default cashback (0.02 = 2%)")}
      ${num("costs.fuel_surcharge_pct", c.fuel_surcharge_pct, "0.005", "FBA fuel surcharge")}
      ${num("costs.monthly_tool_costs", c.monthly_tool_costs, "1", "Tools per month ($)")}
    </div>
    <h2>Keepa</h2>
    <div class="panel"><div class="fields"><div class="wide"><label>API key</label><input name="keepa.api_key" type="password" autocomplete="off" value="${esc(s.keepa.api_key)}" placeholder="from keepa.com, API access"></div></div>
      <div class="row" style="margin-top:10px"><button type="button" class="btn sm" id="tk">Test Keepa</button><span class="small muted" id="tkr"></span></div></div>
    <h2>Deal sources</h2>
    <div class="panel"><p class="small muted" style="margin-top:0">Find deals pulls from these. A Best Buy API key is free at developer.bestbuy.com. Lead feeds are CSV links from a lead-list service (one per line).</p>
      <div class="fields">
      <div class="wide"><label>Best Buy API key</label><input name="deal_sources.bestbuy_api_key" type="password" autocomplete="off" value="${esc(s.deal_sources.bestbuy_api_key)}"></div>
      <div class="wide"><label>Lead feed URLs</label><textarea name="deal_sources.feed_urls" style="min-height:70px">${esc((s.deal_sources.feed_urls || []).join("\n"))}</textarea></div>
      <div><label>Scan the deal sources every morning</label><select name="deal_sources.daily_scan"><option value="true" ${s.deal_sources.daily_scan ? "selected" : ""}>Yes, with the daily job</option><option value="false" ${s.deal_sources.daily_scan ? "" : "selected"}>No</option></select></div>
      </div></div>
    <h2>Amazon Selling Partner API</h2>
    <div class="panel"><p class="small muted" style="margin-top:0">Seller Central › Apps and Services › Develop Apps › add a private app with the Pricing, Inventory, Product Listing, Finance and Accounting roles, then Authorize to get a refresh token.</p>
      <div class="fields">
      <div class="wide"><label>LWA client ID</label><input name="spapi.lwa_client_id" value="${esc(s.spapi.lwa_client_id)}" autocomplete="off"></div>
      <div class="wide"><label>LWA client secret</label><input name="spapi.lwa_client_secret" type="password" value="${esc(s.spapi.lwa_client_secret)}" autocomplete="off"></div>
      <div class="wide"><label>Refresh token</label><input name="spapi.refresh_token" type="password" value="${esc(s.spapi.refresh_token)}" autocomplete="off"></div>
      <div><label>Seller ID (merchant token)</label><input name="spapi.seller_id" value="${esc(s.spapi.seller_id)}"></div>
      </div><div class="row" style="margin-top:10px"><button type="button" class="btn sm" id="ts">Test Amazon</button><span class="small muted" id="tsr"></span></div></div>
    <h2>Brand watchlists</h2>
    <div class="panel fields">
      <div class="wide"><label>IP-complaint brands (one per line)</label><textarea name="watchlists.ip_risk_brands">${esc(s.watchlists.ip_risk_brands.join("\n"))}</textarea></div>
      <div class="wide"><label>Never buy (one per line)</label><textarea name="watchlists.blocked_brands">${esc(s.watchlists.blocked_brands.join("\n"))}</textarea></div>
    </div>
    <div class="row" style="margin:20px 0"><button class="btn primary">Save settings</button></div>
  </form>
  <h2>Data</h2>
  <div class="panel row"><button class="btn" id="seed" ${s.mode === "live" ? "disabled" : ""}>Reload demo data</button><button class="btn danger" id="wipe">Wipe all data</button>
    <span class="small muted">Database and receipts live in the data folder inside ArbitrageOS. Back it up like any document.</span></div>`;
  let mode = s.mode;
  $("#mode").onclick = (e) => { const x = e.target.closest("button"); if (!x) return; mode = x.dataset.m; $$("#mode button").forEach((y) => y.classList.toggle("on", y === x)); };
  $("#sf").onsubmit = (e) => {
    e.preventDefault();
    const out = { mode };
    for (const [k, v] of new FormData(e.target)) {
      const [sec, key] = k.split(".");
      out[sec] = out[sec] || {};
      let val = v;
      if (sec === "watchlists" || k === "deal_sources.feed_urls") val = v.split("\n").map((x) => x.trim()).filter(Boolean);
      else if (v === "true" || v === "false") val = v === "true";
      else if (e.target.elements[k].type === "number") val = Number(v);
      out[sec][key] = val;
    }
    if (mode === "live" && !out.keepa.api_key) return toast("Add a Keepa API key before switching to live", true);
    run(e.submitter, async () => { await api("/api/settings", out); await refreshMode(); toast("Settings saved"); route(); });
  };
  $("#tk").onclick = (e) => run(e.target, async () => { const r = await api("/api/test/keepa", {}); $("#tkr").textContent = `Connected: ${r.tokens_left} tokens left, refills ${r.refill_rate}/min`; });
  $("#ts").onclick = (e) => run(e.target, async () => { const r = await api("/api/test/spapi", {}); $("#tsr").textContent = `Connected: ${r.marketplaces.join(", ")}`; });
  $("#seed").onclick = (e) => run(e.target, async () => { const r = await api("/api/demo/seed", {}); toast(`Demo data loaded: ${r.sales} sales`); });
  $("#wipe").onclick = (e) => {
    const t = prompt("This deletes every lead, purchase, lot, sale and expense. Type WIPE to confirm.");
    if (t === "WIPE") run(e.target, async () => { await api("/api/demo/wipe", { confirm: "WIPE" }); toast("All data wiped"); });
  };
};

// -------------------------------------------------------------------- boot
(async () => {
  try { await refreshMode(); } catch (e) { toast("Can't reach the ArbitrageOS server. Is it running?", true); }
  route();
})();
