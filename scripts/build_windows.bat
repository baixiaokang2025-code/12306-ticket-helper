@echo off
setlocal

cd /d %~dp0\..

if not exist .venv-build (
  py -3 -m venv .venv-build
)

call .venv-build\Scripts\activate
pip install -r requirements.txt -r requirements-build.txt

pyinstaller --noconfirm --clean --windowed --name 12306余票助手 main.py

if exist "12306余票助手" rmdir /s /q "12306余票助手"
xcopy /E /I /Y "dist\12306余票助手" "12306余票助手" >nul

echo 构建完成：dist\12306余票助手\12306余票助手.exe
echo 已复制到：12306余票助手\12306余票助手.exe
endlocal
