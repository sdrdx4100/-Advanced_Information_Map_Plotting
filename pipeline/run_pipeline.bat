@echo off
setlocal
cd /d "%~dp0"

rem Runs the whole gradient pipeline and exports the bundle the local_only
rem viewer needs. Double-click for a full run, or pass run_all.py flags:
rem     run_pipeline.bat --no-fetch
rem     run_pipeline.bat --windows 100
rem
rem Double-clicking closes the window the moment this exits, so it holds at
rem the end and on failure. Nothing presses a key under Claude Code or CI, so
rem the hold is skipped when CLAUDECODE or NO_PAUSE is set.

if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 (
        echo Python not found. Install Python 3.10 or later and try again.
        call :hold
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
    if errorlevel 1 (
        echo Failed to install pipeline requirements.
        call :hold
        exit /b 1
    )
)

echo [1/2] fetch OSM + build gradient windows + sync to public/data/
".venv\Scripts\python.exe" run_all.py %*
if errorlevel 1 (
    echo.
    echo Pipeline failed. It needs Overpass ^(overpass-api.de and mirrors^) and
    echo the GSI elevation tiles ^(cyberjapandata.gsi.go.jp^) to be reachable.
    call :hold
    exit /b 1
)

echo.
echo [2/2] export geometry bundle for the local_only viewer
".venv\Scripts\python.exe" export_local_geometry.py
if errorlevel 1 (
    echo.
    echo Export failed. It also downloads the MLIT N06 archive
    echo ^(nlftp.mlit.go.jp^) on the first run.
    call :hold
    exit /b 1
)

echo.
echo Done. The viewer's two files are here:
echo   %CD%\local_only\geometry_bundle.json
echo   %CD%\local_only\facility_definitions.json
echo Run update_geometry.bat in the viewer repo to pull them in.
call :hold
exit /b 0

:hold
if defined CLAUDECODE goto :eof
if defined NO_PAUSE goto :eof
pause
goto :eof
