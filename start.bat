@echo off
REM Double-click to launch Plex Renamer and open it in your browser.
REM Runs inside a project-local virtual environment (.venv), creating it on
REM first launch so dependencies never touch your global Python.
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
if exist "%VENV_PY%" goto run

REM --- First run: build .venv ------------------------------------------------
REM Prefer the real Python (the bare "python" on PATH can be a Microsoft Store
REM stub). Fall back to "python" if this exact path isn't present.
set "BASEPY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not exist "%BASEPY%" set "BASEPY=python"

echo First run: creating virtual environment in .venv using %BASEPY%
"%BASEPY%" -m venv .venv
if errorlevel 1 goto venvfail

echo Installing dependencies...
"%VENV_PY%" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto pipfail
echo.

:run
echo Starting Plex Renamer at http://127.0.0.1:5055
echo Using: %VENV_PY%
echo (Keep this window open. Close it to stop the server.)
echo.

start "" http://127.0.0.1:5055
"%VENV_PY%" app.py

echo.
echo The server has stopped. If it errored about a missing module, update the
echo virtual environment's dependencies with:
echo     "%VENV_PY%" -m pip install -r requirements.txt
pause
goto :eof

:venvfail
echo.
echo Could not create the virtual environment. Make sure Python 3.11+ is
echo installed (https://www.python.org/downloads/) and try again.
pause
goto :eof

:pipfail
echo.
echo Dependency install failed. Fix the error above, then delete the .venv
echo folder and run start.bat again.
pause
goto :eof
