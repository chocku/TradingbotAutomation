"""Generate dashboard.html from trades.jsonl (local or S3)."""
import json
import os
import logging
from datetime import datetime, timezone
from performance import build_performance, save_local

log = logging.getLogger(__name__)

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
LOG_PATH  = os.path.join(BASE_DIR, "logs", "trades.jsonl")
OUT_PATH  = os.path.join(BASE_DIR, "dashboard.html")

# S3 support
USE_S3 = os.environ.get("USE_S3", "false").lower() == "true"
if USE_S3:
    try:
        from utils.s3_utils import read_log_from_s3, upload_dashboard_to_s3
    except ImportError:
        read_log_from_s3 = None
        upload_dashboard_to_s3 = None
else:
    read_log_from_s3 = None
    upload_dashboard_to_s3 = None


def load_trades():
    """Load trades from S3 or local file."""
    trades = []

    # Try S3 first if enabled
    if USE_S3 and read_log_from_s3:
        try:
            trades = read_log_from_s3()
            log.info("Loaded %d trades from S3", len(trades))
            trades.sort(key=lambda x: x["ts"], reverse=True)
            return trades
        except Exception as e:
            log.error("Failed to load from S3: %s — falling back to local", e)

    # Fall back to local file
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH) as f:
            for line in f:
                line = line.strip()
                if line:
                    trades.append(json.loads(line))

    trades.sort(key=lambda x: x["ts"], reverse=True)
    return trades


def fmt_ts(ts_str):
    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    dt_local = dt.astimezone()
    return dt_local.strftime("%b %d, %Y  %I:%M %p")


def signal_card(trade):
    sig = trade.get("signal", {})
    if not sig:
        return ""
    c   = sig.get("components", {})
    v   = sig.get("values", {})
    mr  = sig.get("mr_score", 0)
    ticker = trade["ticker"]
    alloc  = int(trade["allocation_pct"] * 100)
    regime = "BULL" if sig.get("in_bull") else "BEAR"
    ob     = sig.get("overbought", False)

    ticker_color = "#e05c5c" if ticker == "TQQQ" else "#4a9eff" if ticker == "QQQ" else "#888"

    def dot(active):
        color = "#4caf50" if active else "#444"
        return f'<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:{color};margin-right:6px"></span>'

    components_html = f"""
        <div class="comp-row">{dot(c.get('pullback'))} Pullback <span class="val">close {v.get('close',''):.2f} vs SMA20 {v.get('sma_20',''):.2f}</span></div>
        <div class="comp-row">{dot(c.get('oversold'))} Oversold <span class="val">RSI {v.get('rsi_14',''):.1f}</span></div>
        <div class="comp-row">{dot(c.get('two_down'))} Two Down Days</div>
        <div class="comp-row">{dot(c.get('vix_fear'))} VIX Fear <span class="val">VIX {v.get('vix',''):.2f}</span></div>
        <div class="comp-row">{dot(c.get('bb_below'))} Below BB Midline <span class="val">BB pos {v.get('bb_pos',''):.2f}</span></div>
    """

    mr_dots = "".join(
        f'<span style="display:inline-block;width:14px;height:14px;border-radius:50%;'
        f'background:{"#4caf50" if i < mr else "#333"};margin:2px"></span>'
        for i in range(5)
    )

    error_html = ""
    if trade.get("error"):
        error_html = f'<div class="error-badge">⚠ {trade["error"]}</div>'

    return f"""
    <div class="signal-card">
        <div class="card-header">
            <div>
                <span class="ticker" style="color:{ticker_color}">{ticker}</span>
                <span class="alloc">{alloc}%</span>
                <span class="regime-badge {'bull' if regime=='BULL' else 'bear'}">{regime}</span>
                {'<span class="ob-badge">OVERBOUGHT</span>' if ob else ''}
            </div>
            <div class="ts">{fmt_ts(trade['ts'])}</div>
        </div>
        <div class="card-body">
            <div class="mr-row">
                <span class="label">MR Score</span>
                <span style="margin-right:8px">{mr}/5</span>{mr_dots}
            </div>
            <div class="components">{components_html}</div>
            <div class="metrics">
                <div class="metric"><div class="metric-label">Equity</div><div class="metric-val">${trade['portfolio_equity']:,.2f}</div></div>
                <div class="metric"><div class="metric-label">Price</div><div class="metric-val">${trade['execution_price']:.2f}</div></div>
                <div class="metric"><div class="metric-label">Target Shares</div><div class="metric-val">{trade['target_shares']}</div></div>
                <div class="metric"><div class="metric-label">Delta</div><div class="metric-val" style="color:{'#4caf50' if trade['delta']>0 else '#e05c5c' if trade['delta']<0 else '#888'}">{'+' if trade['delta']>0 else ''}{trade['delta']}</div></div>
            </div>
            {f'<div class="order-id">Order ID: {trade["order_id"]}</div>' if trade.get("order_id") else '<div class="order-id no-order">No order submitted</div>'}
            {error_html}
        </div>
    </div>
    """


def trade_row(trade):
    sig    = trade.get("signal", {})
    mr     = sig.get("mr_score", "-") if sig else "-"
    ticker = trade["ticker"]
    alloc  = int(trade["allocation_pct"] * 100)
    delta  = trade["delta"]
    has_order = bool(trade.get("order_id"))
    has_error = bool(trade.get("error"))

    ticker_color = "#e05c5c" if ticker == "TQQQ" else "#4a9eff" if ticker == "QQQ" else "#888"
    status_html = (
        '<span class="badge-error">Error</span>' if has_error else
        '<span class="badge-order">Ordered</span>' if has_order else
        '<span class="badge-none">No trade</span>'
    )
    delta_str = f"+{delta}" if delta > 0 else str(delta)
    delta_color = "#4caf50" if delta > 0 else "#e05c5c" if delta < 0 else "#888"

    pb = trade.get("position_before")
    if pb and pb.get("shares", 0) > 0:
        pb_ticker_color = "#e05c5c" if pb["ticker"] == "TQQQ" else "#4a9eff" if pb["ticker"] == "QQQ" else "#888"
        position_html = f'<span style="color:{pb_ticker_color};font-weight:600">{pb["shares"]} {pb["ticker"]}</span>'
    else:
        position_html = '<span style="color:#555">Flat</span>'

    return f"""
    <tr>
        <td>{fmt_ts(trade['ts'])}</td>
        <td style="color:{ticker_color};font-weight:600">{ticker}</td>
        <td>{alloc}%</td>
        <td>{mr}</td>
        <td>{position_html}</td>
        <td>${trade['execution_price']:.2f}</td>
        <td>${trade['portfolio_equity']:,.0f}</td>
        <td>{trade['current_shares']:.0f} → {trade['target_shares']}</td>
        <td style="color:{delta_color};font-weight:600">{delta_str}</td>
        <td>{status_html}</td>
    </tr>
    """


def _money(value):
    return f"${value:,.0f}"


def performance_section(performance):
    summary = performance.get("summary", {})
    rows = performance.get("rows", [])
    if not rows:
        return '<p class="muted">Performance history will appear after at least two market-day price records are available.</p>'

    # SVG is self-contained so the public S3 page has no JavaScript dependency.
    width, height, pad = 900, 250, 42
    values = [v for r in rows for v in (r["strategy_value"], r["qqq_value"])]
    lo, hi = min(values), max(values)
    span = max(hi - lo, 1)
    def x_position(index):
        return pad + index * (width - 2 * pad) / max(len(rows) - 1, 1)

    def points(key):
        return " ".join(
            f"{x_position(i):.1f},"
            f"{height-pad - (r[key]-lo) / span * (height-2*pad):.1f}"
            for i, r in enumerate(rows)
        )
    label_indexes = sorted(set([0, len(rows) // 2, len(rows) - 1]))
    date_labels = "".join(
        f'<text x="{x_position(i):.1f}" y="{height-10}" text-anchor="'
        f'{"start" if i == 0 else "end" if i == len(rows)-1 else "middle"}" '
        f'fill="#777" font-size="11">{rows[i]["date"][5:]} → {rows[i]["next_date"][5:]}</text>'
        for i in label_indexes
    )
    chart = f"""
    <div class="chart-wrap">
      <svg viewBox="0 0 {width} {height}" role="img" aria-label="Strategy and QQQ performance starting at $100,000">
        <line x1="{pad}" y1="{height-pad}" x2="{width-pad}" y2="{height-pad}" stroke="#333"/>
        <polyline points="{points('qqq_value')}" fill="none" stroke="#58a6ff" stroke-width="2"/>
        <polyline points="{points('strategy_value')}" fill="none" stroke="#4caf50" stroke-width="2.5"/>
        {date_labels}
      </svg>
      <div class="legend"><span class="green">● Model strategy</span><span class="blue">● QQQ buy &amp; hold</span>
      <span class="muted">Normalized start: $100,000 · close-to-close target allocation</span></div>
    </div>"""
    daily_rows = "".join(
        f"<tr><td>{r['date']}</td><td>{r['next_date']}</td><td>{r['signal']}</td><td>{r['condition']}</td>"
        f"<td>{r['regime']}</td><td>{r['strategy_return']:+.2f}%</td>"
        f"<td>{r['qqq_return']:+.2f}%</td><td>{r['alpha']:+.2f}%</td>"
        f"<td>{_money(r['strategy_value'])}</td><td>{_money(r['qqq_value'])}</td></tr>"
        for r in reversed(rows)
    )
    grouped = {}
    for row in rows:
        bucket = grouped.setdefault(row["condition"], {"days": 0, "strategy": 0.0, "qqq": 0.0})
        bucket["days"] += 1
        bucket["strategy"] += row["strategy_return"]
        bucket["qqq"] += row["qqq_return"]
    condition_rows = "".join(
        f"<tr><td>{condition}</td><td>{data['days']}</td><td>{data['strategy'] / data['days']:+.2f}%</td>"
        f"<td>{data['qqq'] / data['days']:+.2f}%</td>"
        f"<td>{(data['strategy'] - data['qqq']) / data['days']:+.2f}%</td></tr>"
        for condition, data in sorted(
            grouped.items(),
            key=lambda item: (item[1]["strategy"] - item[1]["qqq"]) / item[1]["days"],
        )
    )
    return f"""
    <div class="perf-cards">
      <div><span>Model strategy</span><strong>{_money(summary['strategy_value'])}</strong><em>{summary['strategy_return']:+.2f}%</em></div>
      <div><span>QQQ benchmark</span><strong>{_money(summary['qqq_value'])}</strong><em>{summary['qqq_return']:+.2f}%</em></div>
      <div><span>Difference</span><strong>{_money(summary['outperformance'])}</strong><em>{summary['days']} evaluated days</em></div>
    </div>
    {chart}
    <h3>Performance by condition</h3>
    <div class="table-wrap"><table><thead><tr>
      <th>Condition</th><th>Days</th><th>Avg strategy/day</th><th>Avg QQQ/day</th><th>Avg difference/day</th>
    </tr></thead><tbody>{condition_rows}</tbody></table></div>
    <h3>Daily results</h3>
    <div class="table-wrap"><table><thead><tr>
      <th>Signal date</th><th>Return date</th><th>Signal</th><th>Condition</th><th>Market state</th>
      <th>Strategy</th><th>QQQ</th><th>Difference</th><th>Strategy value</th><th>QQQ value</th>
    </tr></thead><tbody>{daily_rows}</tbody></table></div>"""


def build():
    trades = load_trades()
    performance = build_performance(trades)
    save_local(performance)
    if USE_S3:
        try:
            from utils.s3_utils import upload_performance_to_s3
            upload_performance_to_s3(performance)
        except Exception as e:
            log.warning("Failed to upload performance data to S3: %s", e)
    latest = next((t for t in trades if t.get("signal")), None)
    generated = datetime.now().strftime("%b %d, %Y %I:%M %p")

    card_html  = signal_card(latest) if latest else "<p>No signal data yet.</p>"
    rows_html  = "\n".join(trade_row(t) for t in trades)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QQQ Trader Dashboard</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0f0f0f; color: #e0e0e0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; padding: 24px; }}
  h1 {{ font-size: 20px; font-weight: 600; margin-bottom: 4px; }}
  .generated {{ font-size: 12px; color: #555; margin-bottom: 24px; }}
  h2 {{ font-size: 13px; text-transform: uppercase; letter-spacing: 1px; color: #666; margin-bottom: 12px; }}

  /* Signal card */
  .signal-card {{ background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 10px; padding: 20px; margin-bottom: 32px; max-width: 680px; }}
  .card-header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }}
  .ticker {{ font-size: 32px; font-weight: 700; margin-right: 10px; }}
  .alloc {{ font-size: 24px; color: #aaa; margin-right: 10px; }}
  .regime-badge {{ display:inline-block; padding: 3px 10px; border-radius: 4px; font-size: 12px; font-weight: 600; }}
  .regime-badge.bull {{ background: #1a3a1a; color: #4caf50; }}
  .regime-badge.bear {{ background: #3a1a1a; color: #e05c5c; }}
  .ob-badge {{ display:inline-block; padding: 3px 10px; border-radius: 4px; font-size: 12px; font-weight: 600; background: #3a2a00; color: #f5a623; margin-left: 6px; }}
  .ts {{ font-size: 12px; color: #555; text-align: right; }}

  .mr-row {{ display:flex; align-items:center; gap:6px; margin-bottom:14px; font-size:14px; color:#aaa; }}
  .label {{ color:#555; margin-right:4px; }}

  .components {{ margin-bottom: 16px; }}
  .comp-row {{ font-size: 13px; color: #aaa; padding: 4px 0; display:flex; align-items:center; }}
  .val {{ color: #666; margin-left: 6px; font-size: 12px; }}

  .metrics {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 12px; }}
  .metric {{ background: #111; border: 1px solid #222; border-radius: 6px; padding: 10px 16px; min-width: 110px; }}
  .metric-label {{ font-size: 11px; color: #555; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.5px; }}
  .metric-val {{ font-size: 16px; font-weight: 600; }}

  .order-id {{ font-size: 11px; color: #444; font-family: monospace; margin-top: 6px; }}
  .no-order {{ color: #333; }}
  .error-badge {{ margin-top: 8px; background: #3a1a1a; color: #e05c5c; font-size: 12px; padding: 6px 10px; border-radius: 4px; }}

  /* Table */
  .table-wrap {{ overflow-x: auto; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align: left; padding: 8px 12px; color: #555; font-weight: 500; border-bottom: 1px solid #222; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }}
  td {{ padding: 10px 12px; border-bottom: 1px solid #1a1a1a; }}
  tr:hover td {{ background: #151515; }}

  .badge-order {{ background: #1a3a1a; color: #4caf50; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
  .badge-error {{ background: #3a1a1a; color: #e05c5c; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
  .badge-none  {{ background: #1a1a1a; color: #555; padding: 2px 8px; border-radius: 4px; font-size: 11px; }}
  .muted {{ color:#666; font-size:13px; }}
  .performance {{ margin: 36px 0; }}
  .perf-cards {{ display:flex; gap:12px; flex-wrap:wrap; margin-bottom:18px; }}
  .perf-cards > div {{ background:#1a1a1a; border:1px solid #2a2a2a; border-radius:8px; padding:14px 18px; min-width:180px; }}
  .perf-cards span, .perf-cards em {{ display:block; color:#666; font-size:11px; font-style:normal; text-transform:uppercase; letter-spacing:.4px; }}
  .perf-cards strong {{ display:block; font-size:22px; margin:5px 0; }}
  .perf-cards em {{ color:#4caf50; text-transform:none; }}
  .chart-wrap {{ background:#151515; border:1px solid #242424; border-radius:8px; padding:12px; max-width:940px; }}
  .chart-wrap svg {{ width:100%; height:auto; display:block; }}
  .legend {{ display:flex; gap:20px; font-size:12px; padding:0 8px 4px; }}
  .green {{ color:#4caf50; }} .blue {{ color:#58a6ff; }}
  h3 {{ color:#888; font-size:12px; margin:22px 0 8px; font-weight:600; }}
  .tabs {{ display:flex; gap:6px; border-bottom:1px solid #242424; margin:22px 0 26px; }}
  .tab {{ background:transparent; color:#777; border:0; border-bottom:2px solid transparent; padding:10px 14px; cursor:pointer; font-size:13px; }}
  .tab:hover {{ color:#ccc; }}
  .tab.active {{ color:#fff; border-bottom-color:#4caf50; }}
  .tab-panel {{ display:none; }}
  .tab-panel.active {{ display:block; }}
</style>
</head>
<body>

<h1>QQQ Trader Dashboard</h1>
<div class="generated">Generated {generated}</div>

<nav class="tabs" role="tablist" aria-label="Dashboard sections">
  <button class="tab active" role="tab" aria-selected="true" data-tab="signal">Current Signal</button>
  <button class="tab" role="tab" aria-selected="false" data-tab="performance">Performance</button>
  <button class="tab" role="tab" aria-selected="false" data-tab="trades">Trade Log</button>
</nav>

<section id="signal" class="tab-panel active" role="tabpanel">
  <h2>Latest Signal</h2>
  {card_html}
</section>

<section id="performance" class="tab-panel" role="tabpanel">
  <h2>Performance — $100,000 Starting Value</h2>
  {performance_section(performance)}
</section>

<section id="trades" class="tab-panel" role="tabpanel">
  <h2>Trade Log</h2>
  <div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>Time</th>
        <th>Ticker</th>
        <th>Alloc</th>
        <th>MR</th>
        <th>Position Before</th>
        <th>Price</th>
        <th>Equity</th>
        <th>Shares</th>
        <th>Delta</th>
        <th>Status</th>
      </tr>
    </thead>
    <tbody>
      {rows_html}
    </tbody>
  </table>
  </div>
</section>

{"<scr" + "ipt>"}
  document.querySelectorAll('.tab').forEach(function(button) {{
    button.addEventListener('click', function() {{
      document.querySelectorAll('.tab').forEach(function(tab) {{
        tab.classList.remove('active');
        tab.setAttribute('aria-selected', 'false');
      }});
      document.querySelectorAll('.tab-panel').forEach(function(panel) {{
        panel.classList.remove('active');
      }});
      button.classList.add('active');
      button.setAttribute('aria-selected', 'true');
      document.getElementById(button.dataset.tab).classList.add('active');
    }});
  }});
{"</scr" + "ipt>"}

</body>
</html>"""

    # Write locally
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Dashboard written to {OUT_PATH}")

    # Upload to S3 if enabled
    if USE_S3 and upload_dashboard_to_s3:
        try:
            upload_dashboard_to_s3(html)
            from utils.s3_utils import get_dashboard_url
            dashboard_url = get_dashboard_url()
            print(f"Dashboard uploaded to S3: {dashboard_url}")
        except Exception as e:
            log.warning("Failed to upload dashboard to S3: %s", e)


if __name__ == "__main__":
    build()
