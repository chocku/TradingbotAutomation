"""Manual bot - run anytime you need (executes one cycle immediately)."""
import sys
import os
from pathlib import Path

# Set up paths
project_dir = Path(__file__).parent
qqq_trader_dir = project_dir / "TradingAutomation" / "qqq_trader"
trading_automation_dir = project_dir / "TradingAutomation"
# Insert in reverse priority order (last insert = highest priority on sys.path)
# qqq_trader_dir must be first so `from main import run_pipeline` resolves correctly.
sys.path.insert(0, str(project_dir))
sys.path.insert(0, str(trading_automation_dir))
sys.path.insert(0, str(qqq_trader_dir))

# Load environment
from dotenv import load_dotenv
load_dotenv(qqq_trader_dir / ".env", override=False)  # Replit Secrets take precedence

# Change to qqq_trader directory for relative imports
os.chdir(qqq_trader_dir)

# Run bot once
from main import run_pipeline

print("=" * 60)
print("QQQ TRADING BOT - MANUAL RUN")
print("=" * 60)
print()

try:
    run_pipeline()
    print()
    print("=" * 60)
    print("RUN COMPLETED SUCCESSFULLY")
    print("=" * 60)
except Exception as e:
    print()
    print("ERROR:", str(e))
    import traceback
    traceback.print_exc()
