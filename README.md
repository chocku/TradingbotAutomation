# TradingbotAutomation

Automated end-of-day QQQ signal strategy that trades TQQQ in an Alpaca paper account.

## Overview

- Runs at 3:55 PM ET on NYSE trading days.
- Uses QQQ and VIX market data to calculate the strategy signal.
- Uses TQQQ for invested allocations and cash for fully defensive regimes.
- Cancels conflicting open orders before rebalancing.
- Stores durable logs, theoretical performance history, and the static dashboard in Amazon S3.
- Sends start, completion, and error notifications through Gmail.

The performance dashboard uses a theoretical close-to-close model normalized to $100,000. It is not fill-adjusted Alpaca account performance.

## Strategy allocations

| Regime | Allocation |
| --- | --- |
| Bear market | 0% invested |
| Candle shock | 0% invested |
| SMA50 protection | 25% TQQQ |
| Overbought | 25% TQQQ |
| Mean-reversion score 0 | 50% TQQQ |
| Mean-reversion score 1 | 75% TQQQ |
| Mean-reversion score 2+ | 100% TQQQ |

QQQ is always the signal source; TQQQ is the traded instrument.

## Project layout

- `strategy.py` — core indicators and allocation logic.
- `TradingAutomation/qqq_trader/main.py` — scheduled trading pipeline.
- `TradingAutomation/qqq_trader/data/` — historical data fetching and repair.
- `TradingAutomation/qqq_trader/orders/` — sizing, order cancellation, execution, and reconciliation.
- `TradingAutomation/qqq_trader/performance.py` — normalized strategy and benchmark history.
- `TradingAutomation/qqq_trader/dashboard.py` — static dashboard generation.
- `TradingAutomation/qqq_trader/healthcheck.py` — read-only signal and status rendering.
- `deck/trading_bot_deck.html` — project presentation.

## Required secrets

Configure these as Replit Secrets; never commit their values:

- `ALPACA_KEY`
- `ALPACA_SECRET`
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `GMAIL_APP_PASSWORD`
- `SESSION_SECRET`

See `replit.md` for architecture and operational details.

## Safety

The current configuration targets Alpaca paper trading. Review the strategy, account endpoint, and sizing behavior carefully before making any live-trading changes.