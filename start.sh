#!/bin/bash
# Start health check server + trading scheduler

export PYTHONPATH=/home/runner/workspace

# Start health check server in background (keeps repl pingable)
python TradingAutomation/qqq_trader/healthcheck.py &

# Start scheduler in foreground (fires at 3:55 PM ET on market days)
cd TradingAutomation/qqq_trader && python main.py
