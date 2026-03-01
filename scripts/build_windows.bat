@echo off
setlocal

cd /d %~dp0\..

set APP_NAME=12306余票助手
set VERSION_RAW=%~1
if "%VERSION_RAW%"=="" set VERSION_RAW=v1.1.0
set VERSION=%VERSION_RAW%
if /I not "%VERSION:~0,1%"=="v" set VERSION=v%VERSION_RAW%
set ZIP_NAME=12306-ticket-helper-windows-%VERSION%.zip

if not exist .venv-build (
  py -3 -m venv .venv-build
)

call .venv-build\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt -r requirements-build.txt

pyinstaller --noconfirm --clean --windowed --name "%APP_NAME%" main.py

if exist "%APP_NAME%" rmdir /s /q "%APP_NAME%"
xcopy /E /I /Y "dist\%APP_NAME%" "%APP_NAME%" >nul

if exist "%ZIP_NAME%" del /f /q "%ZIP_NAME%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path 'dist\%APP_NAME%\*' -DestinationPath '%ZIP_NAME%' -Force"

echo 构建完成：dist\%APP_NAME%\%APP_NAME%.exe
echo 已复制到：%APP_NAME%\%APP_NAME%.exe
echo 已打包：%ZIP_NAME%
endlocal
