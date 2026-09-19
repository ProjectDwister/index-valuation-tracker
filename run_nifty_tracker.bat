@echo off
cd /d "%~dp0"
python nifty_tracker_update.py --workbook "NIFTY_Valuation_Backtest_Live_Tracker.xlsx"
echo.
echo Press any key to close...
pause >nul
