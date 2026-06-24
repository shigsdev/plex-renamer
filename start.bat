@echo off
REM Double-click to launch Plex Renamer and open it in your browser.
cd /d "%~dp0"

REM Prefer the real Python (the bare "python" on PATH can be a Microsoft Store
REM stub). Fall back to "python" / "py" if this exact path isn't present.
set "PYEXE=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"

echo Starting Plex Renamer at http://127.0.0.1:5055
echo Using: %PYEXE%
echo (Keep this window open. Close it to stop the server.)
echo.

start "" http://127.0.0.1:5055
"%PYEXE%" app.py

echo.
echo The server has stopped. If it errored above, install dependencies with:
echo     "%PYEXE%" -m pip install -r requirements.txt
pause
