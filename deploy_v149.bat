@echo off
REM Deploy Camoufox v149 - ejecutar como administrador en el servidor
REM Prerequisito: Python 3.12+, 7z en PATH

set DEST=%LOCALAPPDATA%\camoufox\camoufox\Cache\browsers\official\149.0-beta.1
set ZIP=%~dp0dist\camoufox-149.0-beta.1-win.x86_64.zip

mkdir "%DEST%" 2>nul
7z x -y "%ZIP%" -o"%DEST%"
if exist "%DEST%\firefox" (
    xcopy /s /y "%DEST%\firefox\*" "%DEST%\"
    rmdir /s /q "%DEST%\firefox"
)
copy /y "%DEST%\firefox.exe" "%DEST%\camoufox.exe" 2>nul

echo {"version":"149.0","build":"beta.1","prerelease":false,"asset_id":null,"asset_size":null,"asset_updated_at":null} > "%DEST%\version.json"

set CFG=%LOCALAPPDATA%\camoufox\camoufox\Cache\config.json
mkdir "%LOCALAPPDATA%\camoufox\camoufox\Cache" 2>nul
echo {"channel":"official/stable","active_version":"browsers/official/149.0-beta.1","pinned":"149.0-beta.1"} > "%CFG%"

pip install -e "%~dp0pythonlib"
pip install geckordp websockets

echo.
echo Deploy completo. Binario en: %DEST%\camoufox.exe
pause
