@echo off
setlocal
cd /d "%~dp0"

rem Runs the whole gradient pipeline and exports the bundle the local_only
rem viewer needs. Double-click for a full run, or pass run_all.py flags:
rem     run_pipeline.bat --no-fetch
rem     run_pipeline.bat --windows 100

if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 (
        echo Python not found. Install Python 3.10 or later and try again.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
    if errorlevel 1 (
        echo Failed to install pipeline requirements.
        pause
        exit /b 1
    )
)

echo [1/2] fetch OSM + build gradient windows + sync to public/data/
".venv\Scripts\python.exe" run_all.py %*
if errorlevel 1 (
    echo.
    echo Pipeline failed. It needs Overpass ^(overpass-api.de and mirrors^) and
    echo the GSI elevation tiles ^(cyberjapandata.gsi.go.jp^) to be reachable.
    pause
    exit /b 1
)

echo.
echo [2/2] export geometry bundle for the local_only viewer
".venv\Scripts\python.exe" export_local_geometry.py
if errorlevel 1 (
    echo.
    echo Export failed. It also downloads the MLIT N06 archive
    echo ^(nlftp.mlit.go.jp^) on the first run.
    pause
    exit /b 1
)

echo.
echo Done. The viewer's two files are here:
echo   %CD%\local_only\geometry_bundle.json
echo   %CD%\local_only\facility_definitions.json
echo Run update_geometry.bat in the viewer repo to pull them in.
pause
