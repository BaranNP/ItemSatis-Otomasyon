@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo Python bulunamadi. Python 3.10 veya ustunu yukleyip tekrar deneyin.
  echo https://www.python.org/downloads/
  pause
  exit /b 1
)

py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
  echo Python 3.10 veya ustu gerekli. Yukleyip tekrar deneyin.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Ilk kurulum yapiliyor...
  py -3 -m venv .venv
  if errorlevel 1 goto :failed
)

echo Gerekli bilesenler kontrol ediliyor...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :failed

echo Uygulama aciliyor...
".venv\Scripts\python.exe" app.py
if errorlevel 1 goto :failed
exit /b 0

:failed
echo.
echo Kurulum veya baslatma basarisiz. Internet baglantisini ve Python kurulumunu kontrol edin.
pause
exit /b 1
