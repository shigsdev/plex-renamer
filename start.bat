@echo off
REM Double-click to launch Plex Renamer and open it in your browser.
cd /d "%~dp0"
echo Starting Plex Renamer at http://127.0.0.1:5055 ...
start "" http://127.0.0.1:5055
python app.py
pause
