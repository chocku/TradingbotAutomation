# QQQ Trading Bot

An automated end-of-day position manager for QQQ/TQQQ using the Alpaca paper-trading API.

## Overview

The bot runs a 4-layer regime-based strategy at 3:55 PM ET on NYSE trading days:
1. SMA-200 trend filter
2. 2% candle-drop filter
3. SMA-50 protection
4. MR-score sizing → targets QQQ or TQQQ allocation

Trades are executed as Market-On-Close (MOC) orders via Alpaca. Logs and a dashboard HTML are saved locally and optionally pushed to AWS S3.

## Structure

```
TradingAutomation/
  data/                     # Parquet data files (QQQ, TQQQ, VIX)
  qqq_trader/
    main.py                 # Entry point — scheduler or one-shot runner
    dashboard.py            # Generates dashboard.html from trade log
    config.py               # Timing, paths, AWS settings
    signal_engine/          # Wraps strategy.py into a StrategySignal
    orders/                 # Sizing, reconciliation, order execution
    data/                   # Data fetcher (Alpaca + yfinance fallback)
    utils/                  # Calendar, logger, notifier, S3, secrets
    logs/trades.jsonl       # Local trade log
strategy.py                 # Core strategy logic (SMA, RSI, MR-score)
manual_bot.py               # One-shot manual run from project root
```

## How to Run

### Scheduled (daily at 3:55 PM ET)
Use the **QQQ Scheduler** workflow — it starts `main.py` which waits for market time.

### Manual one-shot (any time)
```bash
python manual_bot.py
```

Or with a dry-run (full pipeline, no orders submitted):
```bash
cd TradingAutomation/qqq_trader && python main.py --dry-run
```

## Required Secrets (set in Replit Secrets panel)

| Key | Description |
|-----|-------------|
| `ALPACA_KEY` | Alpaca API key ID |
| `ALPACA_SECRET` | Alpaca API secret |
| `GMAIL_APP_PASSWORD` | Gmail app password for notifications |
| `AWS_ACCESS_KEY_ID` | AWS key for S3 logging |
| `AWS_SECRET_ACCESS_KEY` | AWS secret for S3 logging |

## Environment Variables (set automatically)

| Key | Value |
|-----|-------|
| `AWS_REGION` | `us-east-1` |
| `S3_BUCKET` | `qqq-trading-logs-chock` |
| `USE_S3` | `true` |

## User Preferences

- Keep the project's existing structure and stack — do not restructure.
- Paper trading (`paper=True`) is active; swap to live only after extended monitoring.
