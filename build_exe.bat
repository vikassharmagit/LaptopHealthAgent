@echo off
setlocal
cd /d "%~dp0"

python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --uac-admin ^
  --name LaptopHealthAgent ^
  --onedir ^
  --add-data "static;static" ^
  --add-data "config;config" ^
  --hidden-import uvicorn ^
  --hidden-import uvicorn.loops.auto ^
  --hidden-import uvicorn.protocols.http.auto ^
  --hidden-import uvicorn.protocols.websockets.auto ^
  --hidden-import uvicorn.lifespan.on ^
  run_app.py

echo.
echo Build complete. Run:
echo dist\LaptopHealthAgent\LaptopHealthAgent.exe
endlocal
