"""
dashboard.py — web dashboard for the paper-trading ledger.

Pages
  /            Overview   — KPIs, verdict, equity curve, today/month P/L
  /bets        Bets       — every bet, filterable (open / won / lost), search
  /analytics   Analytics  — breakdowns by sport, bookmaker, EV bucket, odds band

Run locally:    python dashboard.py        → http://localhost:5000
PythonAnywhere: point the WSGI config at this file (see wsgi.py).
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

from flask import Flask, render_template
from jinja2 import DictLoader
import pandas as pd

import config
import ledger

app = Flask(__name__)

NUMERIC = ["soft_odds", "sharp_odds", "true_prob", "ev_pct",
           "clv_pct", "books_agree", "stake", "profit"]


# ── data helpers ──────────────────────────────────────────────────────────────

def _df() -> pd.DataFrame:
    df = ledger.load()
    if df.empty:
        return df
    for c in NUMERIC:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["_kick"] = pd.to_datetime(df["commence_time"], utc=True, errors="coerce")
    return df


def _settled(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["status"].isin(["won", "lost", "void"])]


def _decided(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["status"].isin(["won", "lost"])]


def _group_stats(df: pd.DataFrame, by: str) -> list[dict]:
    """Per-group betting stats over settled bets, sorted by profit."""
    out = []
    for key, g in _settled(df).groupby(by):
        dec = _decided(g)
        staked = g["stake"].sum()
        profit = g["profit"].sum()
        out.append({
            "key": str(key),
            "n": len(g),
            "wins": int((g["status"] == "won").sum()),
            "staked": round(staked, 2),
            "profit": round(profit, 2),
            "roi": round(100 * profit / staked, 1) if staked else 0.0,
            "win_rate": round(100 * (dec["status"] == "won").mean(), 1) if len(dec) else 0.0,
            "avg_ev": round(g["ev_pct"].mean(), 2),
        })
    out.sort(key=lambda r: r["profit"], reverse=True)
    return out


def _bucket_stats(df: pd.DataFrame, col: str, edges: list[float], labels: list[str]) -> list[dict]:
    s = _settled(df).copy()
    if s.empty:
        return []
    s["_b"] = pd.cut(s[col], bins=edges, labels=labels, right=False)
    out = []
    for label in labels:
        g = s[s["_b"] == label]
        if g.empty:
            continue
        dec = _decided(g)
        out.append({
            "key": label,
            "n": len(g),
            "win_rate": round(100 * (dec["status"] == "won").mean(), 1) if len(dec) else 0.0,
            "profit": round(g["profit"].sum(), 2),
        })
    return out


def _sport_label(key: str) -> str:
    return (key.replace("soccer_", "").replace("_", " ").title()
            if isinstance(key, str) else str(key))


# ── templates ─────────────────────────────────────────────────────────────────

BASE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="300">
<title>EV Scanner — {{ title }}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
:root{
  --bg:#0d1117; --panel:#161b22; --border:#30363d; --text:#c9d1d9;
  --muted:#8b949e; --green:#3fb950; --red:#f85149; --yellow:#d29922;
  --blue:#58a6ff; --side:215px;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text)}
a{color:inherit;text-decoration:none}

/* sidebar */
.sidebar{position:fixed;top:0;left:0;bottom:0;width:var(--side);
  background:var(--panel);border-right:1px solid var(--border);
  display:flex;flex-direction:column;padding:20px 0}
.logo{padding:0 20px 20px;border-bottom:1px solid var(--border)}
.logo .t1{font-size:17px;font-weight:700;color:#e6edf3}
.logo .t2{font-size:11px;color:var(--muted);margin-top:2px}
.nav{padding:14px 10px;flex:1}
.nav a{display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:6px;
  font-size:14px;color:var(--muted);margin-bottom:2px}
.nav a:hover{background:#1f242c;color:var(--text)}
.nav a.active{background:#1f6feb22;color:var(--blue);font-weight:600}
.nav .ico{width:18px;text-align:center}
.side-foot{padding:14px 20px;border-top:1px solid var(--border);font-size:12px;color:var(--muted)}
.side-foot .bk{font-size:18px;font-weight:700;margin-bottom:2px}

/* main */
.main{margin-left:var(--side);padding:28px 32px;max-width:1280px}
h1{font-size:19px;color:#e6edf3;margin-bottom:4px}
.sub{font-size:12px;color:var(--muted);margin-bottom:24px}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.5px;color:var(--muted);margin:30px 0 12px}

/* KPI cards */
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:14px 18px}
.card .label{font-size:11px;color:var(--muted);text-transform:uppercase;margin-bottom:6px}
.card .val{font-size:24px;font-weight:700}
.card .hint{font-size:11px;color:var(--muted);margin-top:4px}
.green{color:var(--green)} .red{color:var(--red)}
.yellow{color:var(--yellow)} .muted{color:var(--muted)} .blue{color:var(--blue)}

/* verdict + progress */
.verdict{margin:20px 0 0;padding:12px 18px;border-radius:6px;font-weight:600;font-size:14px}
.verdict.good{background:#0d2b1a;border:1px solid var(--green);color:var(--green)}
.verdict.bad{background:#2b0d0d;border:1px solid var(--red);color:var(--red)}
.verdict.wait{background:#1c1a0d;border:1px solid var(--yellow);color:var(--yellow)}
.progress{height:8px;background:#21262d;border-radius:4px;overflow:hidden;margin-top:10px}
.progress>div{height:100%;background:var(--yellow)}

/* panels, charts, tables */
.panel{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:18px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:9px 10px;border-bottom:2px solid var(--border);
  color:var(--muted);font-size:11px;text-transform:uppercase;white-space:nowrap}
td{padding:8px 10px;border-bottom:1px solid #21262d;white-space:nowrap}
tr:last-child td{border-bottom:none}
tbody tr:hover td{background:#1c2129}
.num{text-align:right}
.pill{display:inline-block;padding:2px 9px;border-radius:10px;font-size:11px;font-weight:600}
.pill-won{background:#0d2b1a;color:var(--green)}
.pill-lost{background:#2b0d0d;color:var(--red)}
.pill-open{background:#1c1a0d;color:var(--yellow)}
.pill-void{background:#21262d;color:var(--muted)}

/* bets page controls */
.controls{display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap}
.tab{padding:7px 16px;border:1px solid var(--border);border-radius:6px;background:var(--panel);
  color:var(--muted);font-size:13px;cursor:pointer}
.tab.active{border-color:var(--blue);color:var(--blue);font-weight:600}
.search{flex:1;min-width:200px;padding:7px 14px;border:1px solid var(--border);border-radius:6px;
  background:var(--panel);color:var(--text);font-size:13px;outline:none}
.search:focus{border-color:var(--blue)}

@media(max-width:860px){
  .sidebar{position:static;width:auto;flex-direction:row;align-items:center;padding:10px}
  .logo{padding:0 14px;border:none}
  .nav{display:flex;padding:0;flex:initial}
  .side-foot{display:none}
  .main{margin-left:0;padding:18px}
  .grid2{grid-template-columns:1fr}
}
</style>
</head>
<body>
<div class="sidebar">
  <div class="logo"><div class="t1">EV Scanner</div><div class="t2">paper trading — no real money</div></div>
  <div class="nav">
    <a href="/" class="{{ 'active' if page=='overview' }}"><span class="ico">&#9632;</span> Overview</a>
    <a href="/bets" class="{{ 'active' if page=='bets' }}"><span class="ico">&#9776;</span> Bets</a>
    <a href="/analytics" class="{{ 'active' if page=='analytics' }}"><span class="ico">&#9650;</span> Analytics</a>
  </div>
  <div class="side-foot">
    <div class="bk {{ 'green' if bankroll >= start_bankroll else 'red' }}">{{ '%.2f'|format(bankroll) }}</div>
    bankroll &middot; started {{ '%.0f'|format(start_bankroll) }}
  </div>
</div>
<div class="main">
{% block content %}{% endblock %}
</div>
</body>
</html>"""


OVERVIEW = """{% extends 'base.html' %}{% block content %}
<h1>Overview</h1>
<div class="sub">updated {{ now }} UTC &middot; auto-refreshes every 5 min</div>

<div class="cards">
  <div class="card"><div class="label">Bankroll</div>
    <div class="val {{ 'green' if profit_total > 0 else 'red' if profit_total < 0 else 'muted' }}">{{ '%.2f'|format(bankroll) }}</div>
    <div class="hint">{{ '%+.2f'|format(profit_total) }} all-time</div></div>
  <div class="card"><div class="label">ROI</div>
    <div class="val {{ 'green' if roi > 0 else 'red' if roi < 0 else 'muted' }}">{{ '%+.1f'|format(roi) }}%</div>
    <div class="hint">on {{ '%.2f'|format(staked) }} staked</div></div>
  <div class="card"><div class="label">Avg CLV</div>
    {% if avg_clv is not none %}
    <div class="val {{ 'green' if avg_clv > 0 else 'red' }}">{{ '%+.2f'|format(avg_clv) }}%</div>
    <div class="hint">n={{ clv_n }} — the metric that matters</div>
    {% else %}<div class="val muted">—</div><div class="hint">no closing lines captured yet</div>{% endif %}</div>
  <div class="card"><div class="label">Win rate</div>
    <div class="val muted">{{ win_rate }}%</div><div class="hint">{{ wins }}W / {{ losses }}L</div></div>
  <div class="card"><div class="label">Settled / Open</div>
    <div class="val muted">{{ settled_n }} <span style="font-size:14px">/</span> <span class="yellow">{{ open_n }}</span></div>
    <div class="hint">{{ '%.2f'|format(open_stake) }} at risk in open bets</div></div>
  <div class="card"><div class="label">Today</div>
    <div class="val {{ 'green' if p_today > 0 else 'red' if p_today < 0 else 'muted' }}">{{ '%+.2f'|format(p_today) }}</div>
    <div class="hint">{{ n_today }} bet(s) settled today</div></div>
  <div class="card"><div class="label">This month</div>
    <div class="val {{ 'green' if p_month > 0 else 'red' if p_month < 0 else 'muted' }}">{{ '%+.2f'|format(p_month) }}</div>
    <div class="hint">{{ n_month }} bet(s) settled in {{ month_name }}</div></div>
  <div class="card"><div class="label">Avg EV at bet</div>
    <div class="val blue">{{ '%+.2f'|format(avg_ev) }}%</div><div class="hint">edge bought per bet</div></div>
</div>

{% if verdict %}
<div class="verdict {{ verdict_cls }}">{{ verdict }}
{% if verdict_cls == 'wait' %}<div class="progress"><div style="width:{{ pct200 }}%"></div></div>{% endif %}
</div>
{% endif %}

{% if equity %}
<h2>Bankroll over time</h2>
<div class="panel"><canvas id="eq" height="90"></canvas></div>
{% endif %}

<div class="grid2">
  <div>
    <h2>Last settled</h2>
    <div class="panel" style="padding:0">
    <table><thead><tr><th>#</th><th>Pick</th><th class="num">Odds</th><th>Result</th><th class="num">P/L</th></tr></thead>
    <tbody>
    {% for b in last_settled %}
      <tr><td>{{ b.bet_id }}</td>
          <td title="{{ b.event }}">{{ b.selection }}<br><span class="muted" style="font-size:11px">{{ b.event }}</span></td>
          <td class="num">{{ b.soft_odds }}</td>
          <td><span class="pill pill-{{ b.status }}">{{ b.status|upper }}</span></td>
          <td class="num {{ 'green' if b.profit > 0 else 'red' if b.profit < 0 else 'muted' }}">{{ '%+.2f'|format(b.profit) }}</td></tr>
    {% else %}<tr><td colspan="5" class="muted" style="padding:18px">Nothing settled yet.</td></tr>{% endfor %}
    </tbody></table></div>
  </div>
  <div>
    <h2>Next kickoffs</h2>
    <div class="panel" style="padding:0">
    <table><thead><tr><th>Kickoff (UTC)</th><th>Pick</th><th class="num">Odds</th><th class="num">EV%</th></tr></thead>
    <tbody>
    {% for b in next_kicks %}
      <tr><td>{{ b.kick }}</td>
          <td title="{{ b.event }}">{{ b.selection }}<br><span class="muted" style="font-size:11px">{{ b.event }}</span></td>
          <td class="num">{{ b.soft_odds }}</td>
          <td class="num blue">{{ '%+.1f'|format(b.ev_pct) }}</td></tr>
    {% else %}<tr><td colspan="4" class="muted" style="padding:18px">No upcoming open bets.</td></tr>{% endfor %}
    </tbody></table></div>
  </div>
</div>

{% if equity %}
<script>
new Chart(document.getElementById('eq'),{type:'line',data:{
 labels:{{ equity_labels|tojson }},
 datasets:[
  {label:'Bankroll',data:{{ equity|tojson }},borderColor:'#3fb950',
   backgroundColor:'rgba(63,185,80,.07)',fill:true,tension:.3,pointRadius:2},
  {label:'Start',data:{{ start_line|tojson }},borderColor:'#30363d',borderDash:[5,5],pointRadius:0}
 ]},options:{plugins:{legend:{labels:{color:'#8b949e'}}},
 scales:{x:{ticks:{color:'#8b949e',maxTicksLimit:12},grid:{color:'#21262d'}},
         y:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}}}}});
</script>
{% endif %}
{% endblock %}"""


BETS = """{% extends 'base.html' %}{% block content %}
<h1>Bets</h1>
<div class="sub">{{ rows|length }} total &middot; {{ open_n }} open &middot; {{ won_n }} won &middot; {{ lost_n }} lost</div>

<div class="controls">
  <div class="tab active" data-f="all">All</div>
  <div class="tab" data-f="open">Open</div>
  <div class="tab" data-f="won">Won</div>
  <div class="tab" data-f="lost">Lost</div>
  <input class="search" id="q" placeholder="Search team, event, bookmaker...">
</div>

<div class="panel" style="padding:0;overflow-x:auto">
<table id="tbl">
<thead><tr>
  <th>#</th><th>Kickoff (UTC)</th><th>Event</th><th>Pick</th><th>Book</th>
  <th class="num">Odds</th><th class="num">Fair</th><th class="num">EV%</th>
  <th class="num">CLV%</th><th class="num">Stake</th><th>Status</th><th class="num">P/L</th>
</tr></thead>
<tbody>
{% for b in rows %}
<tr data-s="{{ b.status }}">
  <td>{{ b.bet_id }}</td>
  <td>{{ b.kick }}</td>
  <td>{{ b.event }}</td>
  <td><b>{{ b.selection }}</b></td>
  <td>{{ b.soft_book }}</td>
  <td class="num">{{ b.soft_odds }}</td>
  <td class="num muted">{{ b.fair }}</td>
  <td class="num blue">{{ '%+.1f'|format(b.ev_pct) }}</td>
  <td class="num {{ 'green' if b.clv and b.clv > 0 else 'red' if b.clv and b.clv < 0 else 'muted' }}">
      {{ '%+.1f'|format(b.clv) if b.clv is not none else '—' }}</td>
  <td class="num">{{ b.stake }}</td>
  <td><span class="pill pill-{{ b.status }}">{{ b.status|upper }}</span></td>
  <td class="num {{ 'green' if b.profit and b.profit > 0 else 'red' if b.profit and b.profit < 0 else 'muted' }}">
      {{ '%+.2f'|format(b.profit) if b.profit is not none else '—' }}</td>
</tr>
{% endfor %}
</tbody>
</table>
</div>

<script>
const tabs=document.querySelectorAll('.tab'),q=document.getElementById('q'),
      rows=document.querySelectorAll('#tbl tbody tr');
let f='all';
function apply(){
  const t=q.value.toLowerCase();
  rows.forEach(r=>{
    const okF = f==='all' || r.dataset.s===f;
    const okQ = !t || r.textContent.toLowerCase().includes(t);
    r.style.display = okF && okQ ? '' : 'none';
  });
}
tabs.forEach(b=>b.onclick=()=>{tabs.forEach(x=>x.classList.remove('active'));
  b.classList.add('active');f=b.dataset.f;apply();});
q.oninput=apply;
</script>
{% endblock %}"""


ANALYTICS = """{% extends 'base.html' %}{% block content %}
<h1>Analytics</h1>
<div class="sub">all breakdowns are over settled bets only ({{ settled_n }} so far)</div>

{% if settled_n == 0 %}
<div class="panel muted">Nothing settled yet — analytics will appear after your first results.</div>
{% else %}

<div class="grid2">
  <div><h2>Profit by sport</h2><div class="panel"><canvas id="cSport" height="170"></canvas></div></div>
  <div><h2>Profit by bookmaker</h2><div class="panel"><canvas id="cBook" height="170"></canvas></div></div>
</div>
<div class="grid2">
  <div><h2>Win rate by EV bucket</h2><div class="panel"><canvas id="cEv" height="170"></canvas></div></div>
  <div><h2>Profit by odds band</h2><div class="panel"><canvas id="cOdds" height="170"></canvas></div></div>
</div>

<h2>Daily P/L</h2>
<div class="panel"><canvas id="cDay" height="80"></canvas></div>

<h2>Per-sport detail</h2>
<div class="panel" style="padding:0;overflow-x:auto">
<table>
<thead><tr><th>Sport</th><th class="num">Bets</th><th class="num">Wins</th>
<th class="num">Win rate</th><th class="num">Staked</th><th class="num">Profit</th>
<th class="num">ROI</th><th class="num">Avg EV</th></tr></thead>
<tbody>
{% for r in by_sport %}
<tr><td>{{ r.label }}</td><td class="num">{{ r.n }}</td><td class="num">{{ r.wins }}</td>
<td class="num">{{ r.win_rate }}%</td><td class="num">{{ r.staked }}</td>
<td class="num {{ 'green' if r.profit > 0 else 'red' if r.profit < 0 else 'muted' }}">{{ '%+.2f'|format(r.profit) }}</td>
<td class="num">{{ '%+.1f'|format(r.roi) }}%</td><td class="num blue">{{ '%+.2f'|format(r.avg_ev) }}%</td></tr>
{% endfor %}
</tbody></table></div>

<h2>Per-bookmaker detail</h2>
<div class="panel" style="padding:0;overflow-x:auto">
<table>
<thead><tr><th>Bookmaker</th><th class="num">Bets</th><th class="num">Wins</th>
<th class="num">Win rate</th><th class="num">Staked</th><th class="num">Profit</th><th class="num">ROI</th></tr></thead>
<tbody>
{% for r in by_book %}
<tr><td>{{ r.key }}</td><td class="num">{{ r.n }}</td><td class="num">{{ r.wins }}</td>
<td class="num">{{ r.win_rate }}%</td><td class="num">{{ r.staked }}</td>
<td class="num {{ 'green' if r.profit > 0 else 'red' if r.profit < 0 else 'muted' }}">{{ '%+.2f'|format(r.profit) }}</td>
<td class="num">{{ '%+.1f'|format(r.roi) }}%</td></tr>
{% endfor %}
</tbody></table></div>

<script>
const gridOpt={plugins:{legend:{display:false}},
  scales:{x:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}},
          y:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}}}};
function colors(a){return a.map(v=>v>=0?'#3fb950':'#f85149');}

new Chart(document.getElementById('cSport'),{type:'bar',data:{
  labels:{{ sport_labels|tojson }},
  datasets:[{data:{{ sport_profit|tojson }},backgroundColor:colors({{ sport_profit|tojson }})}]},options:gridOpt});

new Chart(document.getElementById('cBook'),{type:'bar',data:{
  labels:{{ book_labels|tojson }},
  datasets:[{data:{{ book_profit|tojson }},backgroundColor:colors({{ book_profit|tojson }})}]},options:gridOpt});

new Chart(document.getElementById('cEv'),{type:'bar',data:{
  labels:{{ ev_labels|tojson }},
  datasets:[{data:{{ ev_winrate|tojson }},backgroundColor:'#58a6ff'}]},
  options:{...gridOpt,scales:{...gridOpt.scales,y:{...gridOpt.scales.y,max:100,
    title:{display:true,text:'win rate %',color:'#8b949e'}}}}});

new Chart(document.getElementById('cOdds'),{type:'bar',data:{
  labels:{{ odds_labels|tojson }},
  datasets:[{data:{{ odds_profit|tojson }},backgroundColor:colors({{ odds_profit|tojson }})}]},options:gridOpt});

new Chart(document.getElementById('cDay'),{type:'bar',data:{
  labels:{{ day_labels|tojson }},
  datasets:[{data:{{ day_profit|tojson }},backgroundColor:colors({{ day_profit|tojson }})}]},options:gridOpt});
</script>
{% endif %}
{% endblock %}"""


app.jinja_loader = DictLoader({
    "base.html": BASE,
    "overview.html": OVERVIEW,
    "bets.html": BETS,
    "analytics.html": ANALYTICS,
})


def _base_ctx(page: str) -> dict:
    s = ledger.stats()
    return {
        "page": page,
        "title": page.title(),
        "bankroll": s.get("bankroll", config.STARTING_BANKROLL),
        "start_bankroll": config.STARTING_BANKROLL,
    }


# ── routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def overview():
    df, s = _df(), ledger.stats()
    now = datetime.now(timezone.utc)
    ctx = _base_ctx("overview")

    settled = _settled(df).copy() if not df.empty else pd.DataFrame()
    open_df = df[df["status"] == "open"] if not df.empty else pd.DataFrame()

    # today / this month (by kickoff time of the settled match)
    p_today = n_today = p_month = n_month = 0
    if not settled.empty:
        k = settled["_kick"]
        today_m = (k.dt.date == now.date())
        month_m = (k.dt.year == now.year) & (k.dt.month == now.month)
        p_today = settled.loc[today_m, "profit"].sum()
        n_today = int(today_m.sum())
        p_month = settled.loc[month_m, "profit"].sum()
        n_month = int(month_m.sum())

    # equity curve in kickoff order
    equity, equity_labels = [], []
    if not settled.empty:
        chron = settled.sort_values("_kick")
        cum = config.STARTING_BANKROLL + chron["profit"].fillna(0).cumsum()
        equity = [round(v, 2) for v in cum]
        equity_labels = [d.strftime("%d %b") if pd.notna(d) else "?" for d in chron["_kick"]]

    # verdict
    n, clv = s.get("settled", 0), s.get("avg_clv")
    verdict = verdict_cls = ""
    if n >= 200 and clv is not None:
        if clv > 0:
            verdict, verdict_cls = (
                f"VERDICT after {n} bets: REAL EDGE — avg CLV +{clv:.2f}%. "
                "Time to discuss real money.", "good")
        else:
            verdict, verdict_cls = (
                f"VERDICT after {n} bets: NO EDGE — avg CLV {clv:.2f}%. "
                "Do NOT bet real money.", "bad")
    elif n > 0:
        verdict, verdict_cls = (
            f"Building the sample: {n}/200 settled. The CLV verdict comes at 200 bets.", "wait")

    last_settled = []
    if not settled.empty:
        for _, r in settled.sort_values("_kick", ascending=False).head(6).iterrows():
            last_settled.append({
                "bet_id": int(r["bet_id"]), "event": r["event"],
                "selection": r["selection"], "soft_odds": r["soft_odds"],
                "status": r["status"],
                "profit": float(r["profit"]) if pd.notna(r["profit"]) else 0.0,
            })

    next_kicks = []
    if not open_df.empty:
        fut = open_df[open_df["_kick"] > now].sort_values("_kick").head(6)
        for _, r in fut.iterrows():
            next_kicks.append({
                "kick": r["_kick"].strftime("%a %d %b %H:%M"),
                "event": r["event"], "selection": r["selection"],
                "soft_odds": r["soft_odds"], "ev_pct": float(r["ev_pct"]),
            })

    decided_n = s.get("settled", 0)
    wins = int((settled["status"] == "won").sum()) if not settled.empty else 0
    losses = int((settled["status"] == "lost").sum()) if not settled.empty else 0

    ctx.update({
        "now": now.strftime("%d %b %Y %H:%M"),
        "profit_total": s.get("profit", 0.0) or 0.0,
        "roi": s.get("roi_pct", 0.0) or 0.0,
        "staked": s.get("staked", 0.0) or 0.0,
        "avg_clv": s.get("avg_clv"), "clv_n": s.get("clv_n", 0),
        "win_rate": s.get("win_rate", 0.0), "wins": wins, "losses": losses,
        "settled_n": decided_n, "open_n": s.get("open", 0),
        "open_stake": float(open_df["stake"].sum()) if not open_df.empty else 0.0,
        "p_today": float(p_today), "n_today": n_today,
        "p_month": float(p_month), "n_month": n_month,
        "month_name": now.strftime("%B"),
        "avg_ev": s.get("avg_ev", 0.0) or 0.0,
        "verdict": verdict, "verdict_cls": verdict_cls,
        "pct200": min(100, round(100 * decided_n / 200)),
        "equity": equity, "equity_labels": equity_labels,
        "start_line": [config.STARTING_BANKROLL] * len(equity),
        "last_settled": last_settled, "next_kicks": next_kicks,
    })
    return render_template("overview.html", **ctx)


@app.route("/bets")
def bets():
    df = _df()
    ctx = _base_ctx("bets")
    rows = []
    if not df.empty:
        df = df.sort_values("_kick", ascending=False)
        for _, r in df.iterrows():
            fair = round(1.0 / r["true_prob"], 2) if pd.notna(r["true_prob"]) and r["true_prob"] else "—"
            rows.append({
                "bet_id": int(r["bet_id"]),
                "kick": r["_kick"].strftime("%d %b %H:%M") if pd.notna(r["_kick"]) else "?",
                "event": r["event"], "selection": r["selection"],
                "soft_book": r["soft_book"], "soft_odds": r["soft_odds"],
                "fair": fair, "ev_pct": float(r["ev_pct"]),
                "clv": float(r["clv_pct"]) if pd.notna(r["clv_pct"]) else None,
                "stake": r["stake"], "status": r["status"],
                "profit": float(r["profit"]) if pd.notna(r["profit"]) else None,
            })
    ctx.update({
        "rows": rows,
        "open_n": sum(1 for r in rows if r["status"] == "open"),
        "won_n": sum(1 for r in rows if r["status"] == "won"),
        "lost_n": sum(1 for r in rows if r["status"] == "lost"),
    })
    return render_template("bets.html", **ctx)


@app.route("/analytics")
def analytics():
    df = _df()
    ctx = _base_ctx("analytics")
    settled = _settled(df) if not df.empty else pd.DataFrame()
    ctx["settled_n"] = len(settled)

    if not settled.empty:
        by_sport = _group_stats(df, "sport")
        for r in by_sport:
            r["label"] = _sport_label(r["key"])
        by_book = _group_stats(df, "soft_book")

        ev_b = _bucket_stats(df, "ev_pct", [2, 3, 4, 6, 100], ["2-3%", "3-4%", "4-6%", "6%+"])
        odds_b = _bucket_stats(df, "soft_odds", [1, 1.5, 2.5, 4, 100],
                               ["<1.5", "1.5-2.5", "2.5-4", "4+"])

        day = settled.copy()
        day["_d"] = day["_kick"].dt.strftime("%d %b")
        daily = day.groupby("_d", sort=False)["profit"].sum()
        # keep chronological order
        order = day.sort_values("_kick")["_d"].unique()
        daily = daily.reindex(order)

        ctx.update({
            "by_sport": by_sport, "by_book": by_book,
            "sport_labels": [r["label"] for r in by_sport],
            "sport_profit": [r["profit"] for r in by_sport],
            "book_labels": [r["key"] for r in by_book[:12]],
            "book_profit": [r["profit"] for r in by_book[:12]],
            "ev_labels": [r["key"] for r in ev_b],
            "ev_winrate": [r["win_rate"] for r in ev_b],
            "odds_labels": [r["key"] for r in odds_b],
            "odds_profit": [r["profit"] for r in odds_b],
            "day_labels": list(daily.index),
            "day_profit": [round(v, 2) for v in daily.values],
        })
    return render_template("analytics.html", **ctx)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
