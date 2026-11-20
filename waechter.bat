@echo off
rem Startet den Waechter, z. B. aus der Windows-Aufgabenplanung.
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist logs mkdir logs
call .venv\Scripts\activate.bat
python waechter.py --ki >> logs\waechter.log 2>&1
