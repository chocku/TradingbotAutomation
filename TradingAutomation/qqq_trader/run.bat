@echo off
cd /d "C:\Users\chock\OneDrive\Desktop\Trading Projects\TradingAutomation\qqq_trader"
venv\Scripts\python.exe main.py --now >> logs\scheduler.log 2>&1
