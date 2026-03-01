# 12306 余票助手（UI）

这是一个 **合法合规** 的桌面工具，只提供：
- 多线路余票查询
- 定时刷新监控
- 到票弹窗提醒
- 邮件通知 / 企业微信机器人通知
- 一键打开 12306 官网人工下单

不提供自动登录、自动下单、绕过验证码。

## 功能说明
- **多线路监控**：可维护多条线路（日期+出发+到达），一键同时查询。
- **过滤与提醒**：支持车次过滤、座位过滤、去重提醒。
- **通知渠道**：
  - 邮件（SMTP）
  - 企业微信机器人 webhook
- **配置持久化**：退出时自动保存到 `settings.json`。

## 运行环境
- Python 3.10+
- macOS / Windows
- `tkinter`（Python 自带）

## 本地运行
```bash
cd ~/Desktop/12306-ui-helper
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## 通知配置
- 邮件通知通常需要填写 **SMTP 授权码**（不是邮箱登录密码）。
- 企业微信通知填写群机器人 `webhook` 地址即可。

## 打包
### macOS
```bash
cd ~/Desktop/12306-ui-helper
bash scripts/build_macos.sh
```
产物：`dist/12306余票助手.app`

### Windows
在 `cmd` 或 `PowerShell` 中执行：
```bat
cd %USERPROFILE%\Desktop\12306-ui-helper
scripts\build_windows.bat
```
产物：`dist\12306余票助手\12306余票助手.exe`

## 目录
- `main.py`：主界面与监控逻辑
- `ticket_client.py`：12306 查询客户端
- `notifier.py`：邮件/企业微信通知
- `app_config.py`：配置加载与保存
- `scripts/`：macOS / Windows 打包脚本
