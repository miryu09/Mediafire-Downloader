@echo off
setlocal
cd /d "%~dp0"

where winget >nul 2>nul
if errorlevel 1 goto winget_missing

echo Checking Python 3.14...
py -3.14 --version >nul 2>nul
if errorlevel 1 (
    call :ensure_package Python.Python.3.14 "Python 3.14"
    if errorlevel 1 goto prerequisite_error
)

echo.
echo Checking Microsoft Edge WebView2 Runtime...
reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" /v pv >nul 2>nul
if errorlevel 1 reg query "HKLM\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" /v pv >nul 2>nul
if errorlevel 1 (
    call :ensure_package Microsoft.EdgeWebView2Runtime "Microsoft Edge WebView2 Runtime"
    if errorlevel 1 goto prerequisite_error
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating Python virtual environment...
    py -3.14 -m venv .venv
    if errorlevel 1 goto setup_error
)

for /f "delims=" %%H in ('py -3.14 -c "import hashlib,pathlib,sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())" requirements.txt') do set "REQUIREMENTS_HASH=%%H"
if not defined REQUIREMENTS_HASH goto setup_error

set "REQUIREMENTS_MARKER=.venv\requirements.sha256"
set "INSTALLED_REQUIREMENTS_HASH="
if exist "%REQUIREMENTS_MARKER%" set /p INSTALLED_REQUIREMENTS_HASH=<"%REQUIREMENTS_MARKER%"
if "%INSTALLED_REQUIREMENTS_HASH%"=="%REQUIREMENTS_HASH%" goto start_app

echo Installing required packages...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto setup_error
>"%REQUIREMENTS_MARKER%" echo %REQUIREMENTS_HASH%

:start_app
echo Starting MediaFire Downloader...
".venv\Scripts\python.exe" gui.py
if errorlevel 1 goto app_error
exit /b 0

:setup_error
echo.
echo Could not prepare the Python environment. Check that Python 3.14 is installed.
if /i not "%~1"=="--vbs" pause
exit /b 1

:app_error
echo.
echo The app exited with an error.
if /i not "%~1"=="--vbs" pause
exit /b 1

:winget_missing
echo Windows Package Manager (winget) is required to check and install prerequisites.
echo Install App Installer from the Microsoft Store, then run this file again.
if /i not "%~1"=="--vbs" pause
exit /b 1

:prerequisite_error
echo.
echo Could not install or update a required component.
echo Check your internet connection and approve any Windows installation prompts.
if /i not "%~1"=="--vbs" pause
exit /b 1

:ensure_package
echo Installing %~2...
winget install --id %~1 --exact --source winget --silent --accept-package-agreements --accept-source-agreements
if errorlevel 1 exit /b 1
exit /b 0
