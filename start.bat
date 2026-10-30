@echo off
rem ImageFinder launcher - double click to start
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found. Please install Python 3 and check "Add to PATH".
  echo Download: https://www.python.org/downloads/
  pause
  exit /b 1
)

python -c "import PIL" >nul 2>nul
if errorlevel 1 (
  echo Installing dependency: Pillow ...
  python -m pip install pillow -i https://pypi.tuna.tsinghua.edu.cn/simple
  python -c "import PIL" >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] Failed to install Pillow. Run manually:
    echo    python -m pip install pillow
    pause
    exit /b 1
  )
)

echo Starting ImageFinder at http://127.0.0.1:8765 ...
echo Browser will open automatically. Close this window to stop the app.
python app.py
pause
