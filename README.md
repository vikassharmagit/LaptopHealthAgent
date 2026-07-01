# Laptop Health Agent

Laptop Health Agent is a local Windows dashboard for monitoring laptop health, cleaning up storage, reviewing performance, and applying safe fixes with confirmation.

## What It Does

- Monitors CPU, memory, GPU, battery, disk usage, temperatures, and top processes.
- Finds large files, duplicate files, old downloads, and cleanup opportunities.
- Lets you search a port number and force-kill the task that owns it.
- Shows startup programs with disable and enable actions.
- Opens system tools from the dashboard, including System About, Task Manager, Power & Battery, and Windows Security.
- Provides a Fix Center with grouped diagnostics and repair workflows.
- Creates a packaged one-file Windows executable for sharing with another laptop or PC.

## Fix Center

The Fix Center includes:

- Storage
- Performance
- Startup
- Network
- Security
- Battery

It also shows:

- Admin mode status
- Temperature and fan warnings
- Windows Event Viewer error/critical scan
- BSOD minidump scan
- Driver and device health scan
- Battery report status
- App uninstall recommendations
- Browser cache cleanup
- Safe archive/compress actions for old downloads
- One-click safe free-space cleanup

## Run From Source

From `cmd`:

```bat
cd /d C:\Users\vikas\OneDrive\Documents\LaptopHealthAgent
python -m uvicorn laptop_health_agent.api:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

If `python` is not available, try:

```bat
py -m uvicorn laptop_health_agent.api:app --host 127.0.0.1 --port 8000
```

## Build A One-File EXE

To create a single executable you can send to someone else:

```bat
cd /d C:\Users\vikas\OneDrive\Documents\LaptopHealthAgent
build_onefile_exe.bat
```

The output will be:

```text
dist\LaptopHealthAgent.exe
```

That is the file to share.

## Build A Folder Package

If you want a folder-based distribution instead:

```bat
build_exe.bat
```

That produces:

```text
dist\LaptopHealthAgent\
```

## Notes

- Some actions require Administrator privileges, especially killing elevated processes or changing protected startup entries.
- The app stores runtime data in the user profile location when packaged as an executable.
- Browser cache cleanup and archive actions are intentionally confirmation-based.
- The app is Windows-focused and uses Windows-specific APIs and commands.

## Project Structure

- `laptop_health_agent/` backend and diagnostics code
- `static/` dashboard UI
- `config/defaults.json` app settings and whitelists
- `run_app.py` launcher for packaging
- `build_exe.bat` folder build script
- `build_onefile_exe.bat` one-file build script

