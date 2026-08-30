import os
import sys
import json
import boto3
import pandas as pd
import numpy as np
from datetime import datetime, date
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

WORKSPACE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ET = ZoneInfo("America/New_York")

PARQUET_QQQ = os.path.join(WORKSPACE, "TradingAutomation", "data", "QQQ_daily_full.parquet")
PARQUET_VIX = os.path.join(WORKSPACE, "TradingAutomation", "data", "VIX_daily.parquet")


def _sma(close: pd.Series, w: int) -> pd.Series:
    return close.rolling(w, min_periods=w).mean()


def _rsi(close: pd.Series, w: int = 14) -> pd.Series:
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / w, adjust=False, min_periods=w).mean()
    avg_loss = loss.ewm(alpha=1 / w, adjust=False, min_periods=w).mean()
    rs       = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def get_today_signal():
    """
    Compute today's live strategy signal from parquet + yfinance.
    Read-only — does NOT write to parquet.
    Returns a dict with 'asset', 'size_pct', 'mr_score', 'regime', etc.
    or None on failure.
    """
    try:
        import yfinance as yf

        # Load historical QQQ parquet
        hist = pd.read_parquet(PARQUET_QQQ)
        hist["date"] = pd.to_datetime(hist["date"]).dt.normalize()

        # Fetch 6 months from yfinance — fills gaps AND gives today's bar (read-only)
        ticker  = yf.Ticker("QQQ")
        yf_hist = ticker.history(period="6mo", interval="1d")
        if yf_hist.empty:
            return None
        today_date = pd.Timestamp(date.today()).normalize()
        yf_hist.index = pd.to_datetime(yf_hist.index).normalize().tz_localize(None)

        # Merge: insert any yfinance row missing from parquet; overwrite today's bar
        hist = hist.copy()
        for yf_date, row in yf_hist.iterrows():
            yf_date = pd.Timestamp(yf_date).normalize()
            mask = hist["date"] == yf_date
            new_row = pd.DataFrame([{
                "date": yf_date,
                "o": float(row["Open"]),
                "h": float(row["High"]),
                "l": float(row["Low"]),
                "c": float(row["Close"]),
                "v": float(row["Volume"]),
            }])
            if mask.any():
                # Overwrite existing row with accurate close (especially today)
                hist = hist[hist["date"] != yf_date]
            hist = pd.concat([hist, new_row], ignore_index=True)

        bar_date = pd.Timestamp(yf_hist.index[-1]).normalize()
        df = hist.sort_values("date").reset_index(drop=True)

        # Compute indicators
        c = df["c"]
        df["sma_10"]  = _sma(c, 10)
        df["sma_20"]  = _sma(c, 20)
        df["sma_50"]  = _sma(c, 50)
        df["sma_200"] = _sma(c, 200)
        df["rsi_14"]  = _rsi(c, 14)

        # Load VIX parquet
        vix_hist = pd.read_parquet(PARQUET_VIX)
        vix_hist["date"] = pd.to_datetime(vix_hist["date"]).dt.normalize()

        # Fetch today's VIX from yfinance
        vix_ticker = yf.Ticker("^VIX")
        vix_yf     = vix_ticker.history(period="2d", interval="1d")
        if not vix_yf.empty:
            today_vix = float(vix_yf["Close"].iloc[-1])
            vix_today_df = pd.DataFrame([{"date": pd.Timestamp(date.today()).normalize(), "vix_close": today_vix}])
            vix_all = pd.concat([vix_hist[["date", "vix_close"]], vix_today_df], ignore_index=True)
            vix_all = vix_all.drop_duplicates("date", keep="last")
        else:
            vix_all = vix_hist[["date", "vix_close"]]

        df = df.merge(vix_all[["date", "vix_close"]], on="date", how="left")
        df["vix_close"] = df["vix_close"].ffill()
        df = df.sort_values("date").reset_index(drop=True)

        if len(df) < 201:
            return None

        # Import and run strategy (strategy.py lives at workspace root)
        if WORKSPACE not in sys.path:
            sys.path.insert(0, WORKSPACE)
        from strategy import signal_for_today
        pos = signal_for_today(df)
        last  = df.iloc[-1]
        prev  = df.iloc[-2]
        daily_chg = (float(last["c"]) - float(prev["c"])) / float(prev["c"]) * 100
        pos["bar_date"]      = bar_date.strftime("%Y-%m-%d")
        pos["qqq_close"]     = float(last["c"])
        pos["prev_close"]    = float(prev["c"])
        pos["daily_chg_pct"] = daily_chg
        pos["vix"]           = float(last["vix_close"])
        pos["rsi"]           = float(last["rsi_14"])
        pos["sma_10"]        = float(last["sma_10"])
        pos["sma_20"]        = float(last["sma_20"])
        pos["sma_50"]        = float(last["sma_50"])
        pos["sma_200"]       = float(last["sma_200"])
        pos["bb_pos"]        = pos["values"].get("bb_pos", 0.0)
        return pos

    except Exception as e:
        return {"error": str(e)}


def get_last_trade():
    """Fetch the most recent trade from S3."""
    try:
        bucket = os.environ.get("S3_BUCKET", "qqq-trading-logs-chock")
        s3 = boto3.client("s3")
        obj = s3.get_object(Bucket=bucket, Key="trade_log.csv")
        lines = obj["Body"].read().decode().strip().splitlines()
        if len(lines) < 2:
            return None
        import csv, io
        reader = list(csv.DictReader(io.StringIO("\n".join(lines))))
        return reader[-1] if reader else None
    except Exception as e:
        return None


def render_status():
    now = datetime.now(ET)
    trade = get_last_trade()
    signal = get_today_signal()

    # ── Build today's signal section ──────────────────────────────────────────
    if signal and "error" not in signal:
        asset     = signal.get("asset", "—")
        size_pct  = signal.get("size_pct", "—")
        mr_score  = signal.get("mr_score", 0)
        regime    = signal.get("regime", "—")
        overbought = signal.get("overbought", False)
        bar_date  = signal.get("bar_date", "—")
        qqq_close = signal.get("qqq_close", 0)
        vix       = signal.get("vix", 0)
        rsi       = signal.get("rsi", 0)
        comps     = signal.get("components", {})

        if asset == "cash":
            sig_color = "#f85149"
            sig_label = "Cash 🛑"
        elif asset == "TQQQ":
            sig_color = "#3fb950"
            sig_label = f"TQQQ {size_pct} {'🚀' if size_pct == '100%' else '📈'}"
        else:
            sig_color = "#58a6ff"
            sig_label = f"QQQ {size_pct}"

        # Plain-English reason
        candle_active = signal.get("candle_active", False)
        if regime == "CANDLE_CASH":
            reason = "QQQ dropped >2% in a single day and closed below its 10-day SMA — shock protection is active. Staying in cash until price recovers above SMA10."
        elif regime == "BEAR_CASH":
            reason = f"QQQ is below its 200-day SMA (${signal['values']['sma_200']:.2f}) — full bear market exit. No positions until price reclaims SMA200."
        elif regime == "SMA50_PROT":
            reason = f"QQQ broke below its 50-day SMA (${signal['values']['sma_50']:.2f}) while still above SMA200 — reduced to TQQQ 25% protection mode. Re-enters full mode when price reclaims SMA50 or MR score hits 3+."
        elif overbought:
            reason = f"Market is extended — RSI {rsi:.1f} or price more than 5% above SMA20 (${signal['values']['sma_20']:.2f}). Reduced to TQQQ 25% until conditions normalise."
        elif mr_score >= 2:
            active_comps = [l for l, v in [
                ("pullback below SMA20", comps.get("pullback")),
                ("RSI oversold", comps.get("oversold")),
                ("two consecutive red closes", comps.get("two_down")),
                ("VIX above 20", comps.get("vix_fear")),
                ("below Bollinger mid", comps.get("bb_below")),
            ] if v]
            reason = f"High-conviction pullback — MR score {mr_score}/5 ({', '.join(active_comps)}). Strong mean-reversion setup — full TQQQ 100%."
        elif mr_score == 1:
            active_comps = [l for l, v in [
                ("pullback below SMA20", comps.get("pullback")),
                ("RSI oversold", comps.get("oversold")),
                ("two consecutive red closes", comps.get("two_down")),
                ("VIX above 20", comps.get("vix_fear")),
                ("below Bollinger mid", comps.get("bb_below")),
            ] if v]
            reason = f"Mild pullback — MR score 1/5 ({active_comps[0] if active_comps else 'minor weakness'}). Medium conviction — TQQQ 75%."
        else:
            above_sma10 = qqq_close > signal.get("sma_10", 0)
            if above_sma10:
                sma10_note = "Price is also above SMA10."
            else:
                sma10_note = (
                    f"Price is below SMA10 (${signal['sma_10']:.2f}), but that alone is not an exit: "
                    "the candle-shock rule requires both a >2% daily drop and a close below SMA10."
                )
            reason = (
                "Bull regime — price is above SMA20, SMA50, and SMA200; "
                f"MR score is 0/5 and RSI is neutral. {sma10_note} "
                "Standard TQQQ 50% allocation."
            )

        regime_colors = {
            "BULL_FULL":   "#3fb950",
            "SMA50_PROT":  "#d29922",
            "BEAR_CASH":   "#f85149",
            "CANDLE_CASH": "#f85149",
        }
        regime_color = regime_colors.get(regime, "#8b949e")

        mr_dots = "".join(
            f'<span style="display:inline-block;width:10px;height:10px;border-radius:50%;'
            f'background:{"#58a6ff" if i < mr_score else "#30363d"};margin-right:3px"></span>'
            for i in range(5)
        )

        comp_items = [
            ("Pullback (close < SMA20)", comps.get("pullback", False)),
            ("Oversold (RSI < 40)",      comps.get("oversold", False)),
            ("Two red closes",           comps.get("two_down", False)),
            ("VIX fear (> 20)",          comps.get("vix_fear", False)),
            ("BB below mid",             comps.get("bb_below", False)),
        ]
        comp_html = " ".join(
            f'<span style="font-size:11px;padding:2px 7px;border-radius:12px;'
            f'background:{"#1f3d2a" if active else "#1c2128"};'
            f'color:{"#3fb950" if active else "#484f58"};'
            f'border:1px solid {"#2ea043" if active else "#30363d"}">'
            f'{"✓" if active else "○"} {label}</span>'
            for label, active in comp_items
        )

        # Pull extra values for parameter table
        daily_chg_pct = signal.get("daily_chg_pct", 0)
        prev_close    = signal.get("prev_close", 0)
        sma_10_v      = signal.get("sma_10", 0)
        sma_20_v      = signal.get("sma_20", 0)
        sma_50_v      = signal.get("sma_50", 0)
        sma_200_v     = signal.get("sma_200", 0)
        bb_pos_v      = signal.get("bb_pos", 0)
        candle_active = signal.get("candle_active", False)

        def _flag(active, true_label="⚡ YES", false_label="— no"):
            color = "#f85149" if active else "#484f58"
            return f'<span style="color:{color};font-weight:{"600" if active else "400"}">{true_label if active else false_label}</span>'

        def _vs(val, ref, better_above=True):
            above = val > ref
            if better_above:
                color = "#3fb950" if above else "#f85149"
                label = f"{'▲' if above else '▼'} {'above' if above else 'below'}"
            else:
                color = "#f85149" if above else "#3fb950"
                label = f"{'▲' if above else '▼'} {'above' if above else 'below'}"
            return f'<span style="color:{color}">{label}</span>'

        chg_color = "#3fb950" if daily_chg_pct >= 0 else "#f85149"
        chg_sign  = "+" if daily_chg_pct >= 0 else ""

        # Build parameter rows: (Parameter, Value, Threshold, Status)
        param_rows = [
            ("QQQ Close",    f"${qqq_close:,.2f}",       f"prev ${prev_close:,.2f}",      f'<span style="color:{chg_color}">{chg_sign}{daily_chg_pct:.2f}%</span>'),
            ("Daily Change", f"{chg_sign}{daily_chg_pct:.2f}%", "trigger: < −2%",         _flag(daily_chg_pct < -2, "⚡ SHOCK", "— ok")),
            ("vs SMA10",     f"${sma_10_v:,.2f}",         "shock only if also drop < −2%", _vs(qqq_close, sma_10_v)),
            ("vs SMA20",     f"${sma_20_v:,.2f}",         "pullback if below",             _vs(qqq_close, sma_20_v)),
            ("vs SMA50",     f"${sma_50_v:,.2f}",         "protection if below",           _vs(qqq_close, sma_50_v)),
            ("vs SMA200",    f"${sma_200_v:,.2f}",        "bear exit if below",            _vs(qqq_close, sma_200_v)),
            ("RSI (14)",     f"{rsi:.1f}",                "oversold < 40 / overbought > 70", (
                '<span style="color:#f85149">Oversold</span>' if rsi < 40
                else '<span style="color:#d29922">Overbought</span>' if rsi > 70
                else '<span style="color:#3fb950">Neutral</span>'
            )),
            ("VIX",          f"{vix:.1f}",               "fear above 20",                 _flag(vix > 20, "⚡ FEAR", "— calm")),
            ("BB Position",  f"{bb_pos_v:+.2f}",         "pullback if < 0",               _flag(bb_pos_v < 0, "⚡ below mid", "— above mid")),
            ("MR Score",     f"{mr_score} / 5",          "≥2 = high conviction",          (
                '<span style="color:#3fb950;font-weight:600">High conviction</span>' if mr_score >= 2
                else '<span style="color:#d29922">Mild</span>' if mr_score == 1
                else '<span style="color:#484f58">None</span>'
            )),
            ("Overbought",   "RSI>70 or >SMA20×1.05",   "trigger: either true",          _flag(overbought, "⚡ YES", "— no")),
            ("Candle Shock", "drop >2% + below SMA10",   "trigger: both true",            _flag(candle_active, "⚡ ACTIVE", "— clear")),
            ("Regime",       regime,                     "—",                             f'<span style="color:{regime_color};font-weight:600">{regime}</span>'),
        ]

        table_rows_html = "".join(
            f"""<tr>
              <td class="pt-param">{p}</td>
              <td class="pt-val">{v}</td>
              <td class="pt-thresh">{t}</td>
              <td class="pt-status">{s}</td>
            </tr>"""
            for p, v, t, s in param_rows
        )

        signal_section = f"""
        <div class="section-label">Today's Signal &nbsp;<span style="font-size:11px;color:#484f58">({bar_date})</span></div>
        <div class="card">
          <div class="label">Signal</div>
          <div class="value" style="color:{sig_color}">{sig_label}</div>
        </div>
        <div class="card" style="flex-direction:column;align-items:flex-start;gap:6px">
          <div class="label">Why</div>
          <div style="font-size:13px;color:#c9d1d9;line-height:1.5">{reason}</div>
        </div>
        <div class="card" style="flex-direction:column;align-items:flex-start;gap:10px;padding:16px 0 12px">
          <div class="label" style="padding-left:20px">Signal Parameters</div>
          <table class="param-table">
            <thead>
              <tr>
                <th>Parameter</th><th>Value</th><th>Threshold</th><th>Status</th>
              </tr>
            </thead>
            <tbody>
              {table_rows_html}
            </tbody>
          </table>
        </div>
        {"<div class='card' style='border-color:#d29922'><div class='label'>⚠️ Overbought</div><div class='value' style='font-size:13px;color:#d29922'>Extended above SMA20 × 1.05 or RSI ≥ 70</div></div>" if overbought else ""}
        """
    elif signal and "error" in signal:
        signal_section = f'<div class="card error"><div class="label">Signal Error</div><div class="value" style="font-size:12px;color:#8b949e">{signal["error"][:80]}</div></div>'
    else:
        signal_section = '<div class="card"><div class="label">Today\'s Signal</div><div class="value" style="color:#8b949e">Unavailable</div></div>'

    if trade:
        ts = trade.get("ts", "—")[:19].replace("T", " ").replace("Z", "")
        ticker = trade.get("ticker", "—")
        alloc = int(float(trade.get("allocation_pct", 0)) * 100)
        direction = trade.get("direction", "—")
        equity = float(trade.get("portfolio_equity", 0))
        delta = int(float(trade.get("delta", 0)))
        order_id = trade.get("order_id", "None") or "None"
        error = trade.get("error", "") or ""

        if ticker == "cash" or direction == "flat":
            position_html = '<span style="color:#f85149;font-weight:700">Cash</span>'
        elif ticker == "TQQQ":
            position_html = '<span style="color:#3fb950;font-weight:700">TQQQ 100%</span>'
        else:
            position_html = f'<span style="color:#58a6ff;font-weight:700">{ticker} {alloc}%</span>'

        if delta > 0:
            action = f'<span style="color:#3fb950">BUY {delta} shares</span>'
        elif delta < 0:
            action = f'<span style="color:#f85149">SELL {abs(delta)} shares</span>'
        else:
            action = '<span style="color:#8b949e">No trade</span>'

        trade_section = f"""
        <div class="card">
          <div class="label">Last Run</div>
          <div class="value">{ts} ET</div>
        </div>
        <div class="card">
          <div class="label">Position</div>
          <div class="value">{position_html}</div>
        </div>
        <div class="card">
          <div class="label">Action</div>
          <div class="value">{action}</div>
        </div>
        <div class="card">
          <div class="label">Portfolio Equity</div>
          <div class="value">${equity:,.2f}</div>
        </div>
        <div class="card">
          <div class="label">Order ID</div>
          <div class="value" style="font-size:13px;font-family:monospace;color:#8b949e">{order_id[:20] if order_id != 'None' else '—'}</div>
        </div>
        {"<div class='card error'><div class='label'>Error</div><div class='value'>" + error + "</div></div>" if error else ""}
        """
    else:
        trade_section = '<div class="card"><div class="label">Last Trade</div><div class="value" style="color:#8b949e">No trades yet</div></div>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="60">
<title>QQQ Trading Bot</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #0d1117;
    color: #e6edf3;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 32px 16px;
  }}
  .container {{ width: 100%; max-width: 540px; }}
  .header {{ text-align: center; margin-bottom: 32px; }}
  .status-dot {{
    display: inline-block;
    width: 10px; height: 10px;
    background: #3fb950;
    border-radius: 50%;
    margin-right: 8px;
    animation: pulse 2s infinite;
  }}
  @keyframes pulse {{
    0%, 100% {{ opacity: 1; }}
    50% {{ opacity: 0.4; }}
  }}
  h1 {{ font-size: 28px; font-weight: 700; margin-bottom: 6px; }}
  .subtitle {{ color: #8b949e; font-size: 14px; }}
  .grid {{ display: grid; gap: 12px; }}
  .card {{
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
    padding: 16px 20px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }}
  .card.error {{ border-color: #f85149; }}
  .label {{ font-size: 13px; color: #8b949e; }}
  .value {{ font-size: 16px; font-weight: 600; }}
  .next {{ text-align: center; margin-top: 24px; font-size: 13px; color: #8b949e; }}
  .next strong {{ color: #58a6ff; }}
  .deck-link {{
    display: block;
    text-align: center;
    margin-top: 24px;
    color: #58a6ff;
    font-size: 13px;
    text-decoration: none;
  }}
  .deck-link:hover {{ text-decoration: underline; }}
  .section-label {{
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #484f58;
    margin-top: 20px;
    margin-bottom: 4px;
    padding-left: 4px;
  }}
  .param-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }}
  .param-table thead tr {{
    border-bottom: 1px solid #30363d;
  }}
  .param-table th {{
    text-align: left;
    padding: 6px 20px;
    font-size: 11px;
    font-weight: 600;
    color: #484f58;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }}
  .param-table tbody tr {{
    border-bottom: 1px solid #21262d;
  }}
  .param-table tbody tr:last-child {{
    border-bottom: none;
  }}
  .param-table tbody tr:hover {{
    background: #1c2128;
  }}
  .pt-param {{
    padding: 8px 20px;
    color: #8b949e;
    font-weight: 500;
    white-space: nowrap;
  }}
  .pt-val {{
    padding: 8px 12px;
    color: #e6edf3;
    font-family: monospace;
    font-size: 12px;
  }}
  .pt-thresh {{
    padding: 8px 12px;
    color: #484f58;
    font-size: 11px;
  }}
  .pt-status {{
    padding: 8px 20px 8px 12px;
    text-align: right;
    white-space: nowrap;
    font-size: 12px;
  }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div style="margin-bottom:12px"><span class="status-dot"></span><span style="font-size:13px;color:#3fb950;font-weight:600">Bot Running</span></div>
    <h1>QQQ / TQQQ Trading Bot</h1>
    <p class="subtitle">Fires at 3:55 PM ET on NYSE market days &nbsp;·&nbsp; {now.strftime("%b %d, %Y %I:%M %p ET")}</p>
  </div>
  <div class="grid">
    {signal_section}
    <div class="section-label" style="margin-top:20px">Last Executed Trade</div>
    {trade_section}
  </div>
  <p class="next">Next scheduled run: <strong>3:55 PM ET</strong> on the next market day</p>
  <a class="deck-link" href="/deck/trading_bot_deck.html">📊 View Project Deck →</a>
  <a class="deck-link" href="https://qqq-trading-logs-chock.s3.amazonaws.com/dashboard.html" target="_blank" rel="noopener">📋 View Trade Log →</a>
</div>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        file_path = os.path.join(WORKSPACE, self.path.lstrip("/"))
        if self.path != "/" and os.path.isfile(file_path):
            with open(file_path, "rb") as f:
                content = f.read()
            self.send_response(200)
            if file_path.endswith(".html"):
                self.send_header("Content-Type", "text/html")
            else:
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(file_path)}"')
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        else:
            html = render_status().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

    def log_message(self, format, *args):
        print(f"{self.address_string()} - {format % args}")


if __name__ == "__main__":
    HTTPServer.allow_reuse_address = True
    server = HTTPServer(("0.0.0.0", 5000), Handler)
    print("Server running on port 5000")
    server.serve_forever()
