@echo off
setlocal
cd /d "%~dp0"
echo MediaConverter GUI - instalacja
if exist ".venv\Scripts\python.exe" goto validate_venv
where py >nul 2>&1
if not errorlevel 1 (
  py -3.12 -c "import sys" >nul 2>&1
  if not errorlevel 1 (
    py -3.12 -m venv .venv
    goto validate_venv
  )
  py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
  if not errorlevel 1 (
    py -3 -m venv .venv
    goto validate_venv
  )
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
if not errorlevel 1 (
  python -m venv .venv
  goto validate_venv
)
where uv >nul 2>&1
if not errorlevel 1 (
  for /f "usebackq delims=" %%P in (`uv python find 3.12 --system --offline --cache-dir "%CD%\.uv-cache" 2^>nul`) do set "MC_UV_PY=%%P"
)
if not defined MC_UV_PY (
  for /f "usebackq delims=" %%P in (`uv python find --system --offline --cache-dir "%CD%\.uv-cache" 2^>nul`) do set "MC_UV_PY=%%P"
)
if defined MC_UV_PY (
  "%MC_UV_PY%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
  if not errorlevel 1 (
    "%MC_UV_PY%" -m venv .venv
    goto validate_venv
  )
)
echo Wymagany jest Python 3.12 lub nowszy. Zainstaluj go i uruchom ponownie ten skrypt.
echo https://www.python.org/downloads/windows/
pause
exit /b 1

:validate_venv
if not exist ".venv\Scripts\python.exe" (
  echo Nie udalo sie utworzyc srodowiska .venv.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
if errorlevel 1 (
  echo Istniejace srodowisko .venv korzysta ze starszej wersji Pythona.
  echo Zmien jego nazwe lub usun je recznie, a nastepnie uruchom instalator ponownie.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements.lock
if errorlevel 1 (
  echo Instalacja zaleznosci nie powiodla sie. Sprawdz polaczenie lub pakiety offline.
  pause
  exit /b 1
)
where ffmpeg >nul 2>&1
if errorlevel 1 echo Nie znaleziono FFmpeg w PATH. Skonfiguruj sciezke w aplikacji. https://ffmpeg.org/download.html
where ffprobe >nul 2>&1
if errorlevel 1 echo Nie znaleziono FFprobe w PATH. Skonfiguruj sciezke w aplikacji. https://ffmpeg.org/download.html
echo Instalacja zakonczona. Uruchom start.bat.
pause
