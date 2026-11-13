@echo off
chcp 65001 >nul
cd /d "%~dp0"
REM Usage: build.bat          -> Kotha-Windows-Setup.exe (lite, ~13 MB)
REM        build.bat offline  -> Kotha-Windows-Offline-Setup.exe (with offline Whisper)
set COMMON=--noconfirm --clean --windowed --onedir --name Kotha --icon assets\kotha.ico --add-data "assets;assets" --hidden-import pystray._win32 --collect-all uiautomation --exclude-module unittest --exclude-module pydoc --exclude-module lib2to3 --exclude-module setuptools --exclude-module plat_mac --collect-data soundcard --exclude-module plat_mac

if /i "%1"=="offline" (
  python -m pip install -r requirements-offline.txt pyinstaller || goto :fail
  python -m PyInstaller %COMMON% --distpath dist_full --workpath build\full --collect-all faster_whisper --collect-all ctranslate2 --collect-all onnxruntime --collect-all tokenizers --collect-all av kotha.py || goto :fail
  set NSISFLAG=-DFULL
) else (
  python -m pip install -r requirements.txt pyinstaller || goto :fail
  python -m PyInstaller %COMMON% --distpath dist_lite --workpath build\lite --exclude-module faster_whisper --exclude-module ctranslate2 --exclude-module onnxruntime --exclude-module av --exclude-module tokenizers --exclude-module huggingface_hub kotha.py || goto :fail
  set NSISFLAG=
)

set MAKENSIS="%ProgramFiles(x86)%\NSIS\makensis.exe"
if not exist %MAKENSIS% set MAKENSIS="%ProgramFiles%\NSIS\makensis.exe"
if not exist %MAKENSIS% (
  echo NSIS not found. App is ready in dist_lite\Kotha or dist_full\Kotha.
  echo Install NSIS from https://nsis.sourceforge.io to also build the installer.
  pause & exit /b 0
)
if not exist output mkdir output
%MAKENSIS% %NSISFLAG% kotha.nsi || goto :fail
echo Done. See the output folder.
pause & exit /b 0
:fail
echo Build failed.
pause & exit /b 1
