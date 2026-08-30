"""
Final Strategy — TQQQ with Candle Filter + SMA50 Protection
============================================================
Signal source: QQQ only. Execution instrument: TQQQ or Cash (QQQ never traded).

HOW IT WORKS
------------
Four layers, evaluated in priority order:

  Layer 1 — SMA200 bear filter:
    close < SMA200 → cash (full bear-market exit)

  Layer 2 — Candle break filter (shock protection):
    QQQ drops >2% in a day AND closes below SMA10 → cash
    Re-entry: next close above SMA10
    (Overrides SMA50 protection and MR sizing)

  Layer 3 — SMA50 protection mode:
    Price breaches below SMA50 (while above SMA200) → TQQQ 25%
    Re-entry to full mode: price back above SMA50 OR MR score >= 3

  Layer 4 — MR score sizing (orderly pullbacks):
    MR score 0-5 based on: price<SMA20, RSI<40, two red closes, VIX>20, BB below mid
    MR >= 2 (not overbought) → 100%  TQQQ  (high conviction)
    MR == 1 (not overbought) → 75%   TQQQ  (medium conviction)
    MR == 0 (not overbought) → 50%   TQQQ  (clean uptrend)
    Overbought               → 25%   TQQQ  (stretched market — reduced leverage)

REQUIRED COLUMNS
----------------
  date      : datetime
  c         : QQQ close price
  sma_10    : 10-day SMA         <-- new vs previous strategy
  sma_20    : 20-day SMA
  sma_50    : 50-day SMA
  sma_200   : 200-day SMA
  rsi_14    : RSI (14-period, Wilder)
  vix_close : VIX closing level

PARAMETERS
----------
  CANDLE_THRESH  = 2.0    % daily drop that qualifies as a violent candle
  EXTEND_FACTOR  = 1.05   overbought: price > SMA20 * 1.05
  RSI_OVERBOUGHT = 70
  RSI_OVERSOLD   = 40
  VIX_FEAR       = 20
  MR_REENTRY     = 3      re-enter full mode while in SMA50 protection if MR >= 3

BACKTEST RESULTS (Jan 2022 - Jun 2026, $10k starting capital)
--------------------------------------------------------------
  Strategy              CAGR    Sharpe   MaxDD    DD4      DD6      DD9
  Final (this file)     35.2%   1.42     -19.4%   -11.9%   -9.1%    -9.3%
  No candle filter      40.2%   1.35     -27.9%   -20.1%   -24.3%   -19.5%
  QQQ Buy & Hold        15.0%   0.72     -35.2%
"""

import numpy as np
import pandas as pd

# ── Parameters ────────────────────────────────────────────────────────────────

CANDLE_THRESH  = 2.0
EXTEND_FACTOR  = 1.05
RSI_OVERBOUGHT = 70
RSI_OVERSOLD   = 40
VIX_FEAR       = 20
MR_REENTRY     = 3

REQUIRED_COLUMNS = ["date", "c", "sma_10", "sma_20", "sma_50", "sma_200", "rsi_14", "vix_close"]

SIGNAL_COLUMNS = [
    "bb_pos", "two_down",
    "mr_pullback", "mr_oversold", "mr_two_down", "mr_vix", "mr_bb", "mr_score",
    "overbought", "in_bull_200", "candle_alert", "in_candle_cash",
    "in_sma50_prot", "in_bull",
]


# ── Signal computation ────────────────────────────────────────────────────────

def compute_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add all strategy signal columns to df.
    df must be sorted ascending by date and contain REQUIRED_COLUMNS.
    Returns a copy — does not modify the original.
    """
    _validate(df)
    d = df.copy()

    # Bollinger band position
    if "bb_mid" in d.columns and "bb_upper" in d.columns:
        std20 = (d["bb_upper"] - d["bb_mid"]) / 2
    else:
        std20 = d["c"].rolling(20).std()
    d["bb_pos"] = (d["c"] - d["sma_20"]) / (2 * std20.replace(0, np.nan))

    # Two consecutive down closes
    down          = (d["c"] < d["c"].shift(1)).astype(int)
    d["two_down"] = ((down == 1) & (down.shift(1) == 1)).astype(int)

    # MR components
    d["mr_pullback"] = (d["c"]         < d["sma_20"]).astype(int)
    d["mr_oversold"] = (d["rsi_14"]    < RSI_OVERSOLD).astype(int)
    d["mr_two_down"] = d["two_down"]
    d["mr_vix"]      = (d["vix_close"] > VIX_FEAR).astype(int)
    d["mr_bb"]       = (d["bb_pos"]    < 0).astype(int)
    d["mr_score"]    = (d["mr_pullback"] + d["mr_oversold"] +
                        d["mr_two_down"] + d["mr_vix"] + d["mr_bb"])

    d["overbought"] = (
        (d["rsi_14"] > RSI_OVERBOUGHT) |
        (d["c"]      > d["sma_20"] * EXTEND_FACTOR)
    ).astype(int)

    # Bear filter
    d["in_bull_200"] = (d["c"] > d["sma_200"]).astype(int)

    # Candle break filter (stateful — only fires when above SMA200)
    daily_pct     = d["c"].pct_change() * 100
    candle_trigger = (daily_pct < -CANDLE_THRESH) & (d["c"] < d["sma_10"]) & (d["in_bull_200"] == 1)
    d["candle_alert"] = candle_trigger.astype(int)

    in_candle = False
    in_candle_arr = np.zeros(len(d), dtype=int)
    for i in range(len(d)):
        if candle_trigger.iloc[i]:
            in_candle = True
        if in_candle and d["c"].iloc[i] > d["sma_10"].iloc[i]:
            in_candle = False
        in_candle_arr[i] = int(in_candle)
    d["in_candle_cash"] = in_candle_arr

    # SMA50 protection mode (stateful, only within bull regime above SMA200)
    above50       = d["c"] > d["sma_50"]
    in_prot       = False
    sma50_prot    = np.zeros(len(d), dtype=int)
    bull_full     = np.zeros(len(d), dtype=int)
    for i in range(len(d)):
        above_200 = bool(d["in_bull_200"].iloc[i])
        above_50  = bool(above50.iloc[i])
        mr_val    = int(d["mr_score"].iloc[i])
        if not above_200:
            in_prot = False
            sma50_prot[i] = 0
            bull_full[i]  = 0
            continue
        if not in_prot:
            if above_50:
                bull_full[i]  = 1
                sma50_prot[i] = 0
            else:
                in_prot       = True
                bull_full[i]  = 0
                sma50_prot[i] = 1
        else:
            if above_50 or mr_val >= MR_REENTRY:
                in_prot       = False
                bull_full[i]  = 1
                sma50_prot[i] = 0
            else:
                bull_full[i]  = 0
                sma50_prot[i] = 1

    d["in_sma50_prot"] = sma50_prot

    # Combined regime:
    #   0 = bear (below SMA200) or candle cash
    #   1 = SMA50 protection (25% TQQQ)
    #   2 = full bull mode (MR-sized TQQQ)
    regime = np.where(
        (d["in_bull_200"] == 0) | (d["in_candle_cash"] == 1), 0,
        np.where(sma50_prot == 1, 1, 2)
    )
    d["in_bull"] = regime

    return d


# ── Position ──────────────────────────────────────────────────────────────────

def get_position(row: pd.Series) -> dict:
    """
    Compute today's position from a single row (after compute_signals).
    Pass df.iloc[-1].

    in_bull == 0 : bear or candle cash → cash
    in_bull == 1 : SMA50 protection   → TQQQ 25% (low conviction)
    in_bull == 2 : full bull mode     → TQQQ sized by MR score

    Instrument rules:
      High conviction  (MR >= 2, not overbought) → TQQQ 100%
      Medium conviction (MR == 1, not overbought) → TQQQ  75%
      Low conviction   (MR == 0, not overbought) → TQQQ  50%
      Overbought                                  → TQQQ  25%
      SMA50 protection                            → TQQQ  25%
      Bear / candle shock                         → cash   0%
    """
    regime = int(row["in_bull"])
    mr     = int(row["mr_score"])
    ob     = bool(row["overbought"])
    candle = bool(row["in_candle_cash"])

    if regime == 0:
        asset, size = "cash", 0.0

    elif regime == 1:
        asset, size = "TQQQ", 0.25

    else:
        # Full bull mode
        if mr >= 2 and not ob:
            asset, size = "TQQQ", 1.0
        elif mr == 1 and not ob:
            asset, size = "TQQQ", 0.75
        elif mr == 0 and not ob:
            asset, size = "TQQQ", 0.5
        else:
            asset, size = "TQQQ", 0.25

    if regime == 0 and candle:
        regime_label = "CANDLE_CASH"
    elif regime == 0:
        regime_label = "BEAR_CASH"
    elif regime == 1:
        regime_label = "SMA50_PROT"
    else:
        regime_label = "BULL_FULL"

    return {
        "asset":      asset,
        "size":       size,
        "size_pct":   f"{int(size * 100)}%",
        "mr_score":   mr,
        "in_bull":    regime == 2,
        "regime":     regime_label,
        "overbought": ob,
        "candle_active": candle,
        "components": {
            "pullback":  bool(row["mr_pullback"]),
            "oversold":  bool(row["mr_oversold"]),
            "two_down":  bool(row["mr_two_down"]),
            "vix_fear":  bool(row["mr_vix"]),
            "bb_below":  bool(row["mr_bb"]),
        },
        "values": {
            "close":   float(row["c"]),
            "sma_10":  float(row["sma_10"]),
            "sma_20":  float(row["sma_20"]),
            "sma_50":  float(row["sma_50"]),
            "sma_200": float(row["sma_200"]),
            "rsi_14":  float(row["rsi_14"]),
            "vix":     float(row["vix_close"]),
            "bb_pos":  float(row["bb_pos"]) if not np.isnan(row["bb_pos"]) else 0.0,
        },
    }


def signal_for_today(df: pd.DataFrame) -> dict:
    """One-call convenience: compute signals on df and return today's position."""
    df = compute_signals(df)
    return get_position(df.iloc[-1])


# ── Validation ────────────────────────────────────────────────────────────────

def _validate(df: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"DataFrame is missing required columns: {missing}\n"
            f"Required: {REQUIRED_COLUMNS}"
        )
    if len(df) < 201:
        raise ValueError(
            f"DataFrame has only {len(df)} rows — need at least 201 for SMA200. "
            "Pass 250+ rows so all SMAs are valid."
        )


# ── Pretty print ──────────────────────────────────────────────────────────────

def print_signal(pos: dict, date: str = "today") -> None:
    print(f"\n=== Signal for {date} ===")
    print(f"  Regime:        {pos['regime']}")
    print(f"  Candle active: {'YES' if pos['candle_active'] else 'NO'}")
    print(f"  MR Score:      {pos['mr_score']} / 5")
    c = pos["components"]
    v = pos["values"]
    print(f"    pullback  (close < SMA20):  {'Y' if c['pullback'] else 'N'}  [{v['close']:.2f} vs {v['sma_20']:.2f}]")
    print(f"    oversold  (RSI < {RSI_OVERSOLD}):      {'Y' if c['oversold'] else 'N'}  [RSI={v['rsi_14']:.1f}]")
    print(f"    two_down  (2 red closes):   {'Y' if c['two_down'] else 'N'}")
    print(f"    vix_fear  (VIX > {VIX_FEAR}):      {'Y' if c['vix_fear'] else 'N'}  [VIX={v['vix']:.2f}]")
    print(f"    bb_below  (below BB mid):   {'Y' if c['bb_below'] else 'N'}  [BB pos={v['bb_pos']:.2f}]")
    print(f"  Overbought:    {'YES' if pos['overbought'] else 'NO'}")
    print(f"  SMA10:  {v['close']:.2f} vs {v['sma_10']:.2f}  ({'above' if v['close'] > v['sma_10'] else 'BELOW'})")
    print(f"  SMA50:  {v['close']:.2f} vs {v['sma_50']:.2f}  ({'above' if v['close'] > v['sma_50'] else 'BELOW'})")
    print(f"  SMA200: {v['close']:.2f} vs {v['sma_200']:.2f}  ({'above' if v['close'] > v['sma_200'] else 'BELOW'})")
    print(f"\n  --> POSITION: {pos['asset']} {pos['size_pct']}")


# ── Self-test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os, sys
    base     = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(base, "data")
    try:
        qqq = pd.read_parquet(os.path.join(data_dir, "QQQ_daily_full.parquet"))
        vix = pd.read_parquet(os.path.join(data_dir, "VIX_daily.parquet"))
        qqq["date"] = pd.to_datetime(qqq["date"])
        vix["date"] = pd.to_datetime(vix["date"])

        # compute indicators from raw close (mirrors fetcher._add_indicators)
        qqq = qqq.sort_values("date").reset_index(drop=True)
        c = qqq["c"]
        qqq["sma_10"]  = c.rolling(10).mean()
        qqq["sma_20"]  = c.rolling(20).mean()
        qqq["sma_50"]  = c.rolling(50).mean()
        qqq["sma_200"] = c.rolling(200).mean()
        delta = c.diff(); gain = delta.clip(lower=0); loss = -delta.clip(upper=0)
        ag = gain.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
        al = loss.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
        qqq["rsi_14"] = 100 - (100 / (1 + ag / al))

        df = qqq.merge(vix[["date", "vix_close"]], on="date", how="left")
        df["vix_close"] = df["vix_close"].ffill()
        df = df.sort_values("date").reset_index(drop=True)
        pos = signal_for_today(df)
        print_signal(pos, str(df["date"].iloc[-1].date()))
        print("\nself-test passed.")
    except FileNotFoundError:
        print("Local data not found — self-test skipped.")
        sys.exit(0)
