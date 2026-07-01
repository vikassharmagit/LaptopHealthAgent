@echo off
setlocal
cd /d "%~dp0"

python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --uac-admin ^
  --name LaptopHealthAgent ^
  --onefile ^
  --add-data "static;static" ^
  --add-data "config;config" ^
  --hidden-import uvicorn ^
  --hidden-import uvicorn.loops.auto ^
  --hidden-import uvicorn.protocols.http.auto ^
  --hidden-import uvicorn.protocols.websockets.auto ^
  --hidden-import uvicorn.lifespan.on ^
  run_app.py

echo.
echo One-file build complete. Run or share:
echo dist\LaptopHealthAgent.exe
endlocal
