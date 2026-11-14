@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Najpierw uruchom install.bat.
  pause
  exit /b 1
)
echo Instalowanie dokladnych wersji okreslonych w requirements.lock.
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements.lock
if errorlevel 1 (
  echo Aktualizacja nie powiodla sie.
  pause
  exit /b 1
)
echo Zaleznosci sa zgodne z requirements.lock.
pause
