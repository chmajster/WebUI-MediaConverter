@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Brak srodowiska aplikacji. Najpierw uruchom install.bat.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -c "import sys, nicegui, pydantic, psutil; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
if errorlevel 1 (
  echo Brak wymaganych bibliotek lub Python jest starszy niz 3.12. Uruchom install.bat.
  pause
  exit /b 1
)
.venv\Scripts\python.exe main.py %*
if errorlevel 1 (
  echo Uruchomienie nie powiodlo sie. Sprawdz komunikaty powyzej.
  pause
  exit /b 1
)
