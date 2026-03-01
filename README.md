# 12306 余票助手（UI）

这是一个 **合法合规** 的桌面工具，只提供：
- 多线路余票查询
- 定时刷新监控
- 有票/候补弹窗提醒（分级）
- 邮件通知 / 企业微信机器人通知
- 一键打开 12306 官网人工下单（支持从选中线路直接跳转）
- 12306 登录状态检测（基于你手动粘贴的 Cookie）

不提供自动登录、自动下单、绕过验证码。

## 功能说明
- **多线路监控**：可维护多条线路（日期+出发+到达），一键同时查询。
- **分组与启停**：线路支持分组、启用/停用、按分组批量启停。
- **过滤与提醒**：支持车次过滤、座位过滤、去重提醒。
- **候补提示**：当返回座位为“候补”时，会单独标记为候补提醒。
- **通知渠道**：
  - 邮件（SMTP）
  - 企业微信机器人 webhook
- **快捷跳转**：结果表格可选中某条线路，点击“打开选中下单页”直接打开对应日期/站点页面。
- **登录检测**：在“通知设置”页粘贴 Cookie 后可检测登录状态（仅检测，不会自动下单）。
- **稳态重试**：支持查询重试次数、指数退避、请求超时配置。
- **错误分类面板**：自动统计网络/风控/参数/接口等错误类别与最近错误。
- **智能中转方案**：支持自定义中转站、换乘等待区间、最大方案数，自动给出 1 次中转建议并可一键打开首段/次段下单页。
- **配置持久化**：退出时自动保存到 `settings.json`。

## 运行环境
- Python 3.10+
- macOS / Windows
- `tkinter`（Python 自带）

## 本地运行
```bash
cd ~/Desktop/12306-ticket-helper
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## 通知配置
- 邮件通知通常需要填写 **SMTP 授权码**（不是邮箱登录密码）。
- 企业微信通知填写群机器人 `webhook` 地址即可。

## 登录状态检测（可选）
- 本工具不会自动读取浏览器登录态。
- 如需检测，可手动从浏览器复制 12306 Cookie 粘贴到“通知设置”页。
- 检测仅用于提示 Cookie 是否可用，不会执行自动下单。
- 可识别 `RAIL_EXPIRATION` 并提示 Cookie 剩余有效时长。

## 打包
### macOS
```bash
cd ~/Desktop/12306-ticket-helper
bash scripts/build_macos.sh v1.1.0
```
产物：
- `dist/12306余票助手.app`
- `12306-ticket-helper-macos-v1.1.0.zip`

### Windows
在 `cmd` 或 `PowerShell` 中执行：
```bat
cd %USERPROFILE%\Desktop\12306-ticket-helper
scripts\build_windows.bat v1.1.0
```
产物：
- `dist\12306余票助手\12306余票助手.exe`
- `12306-ticket-helper-windows-v1.1.0.zip`

## Releases
- 推送 tag（如 `v1.1.0`）后，GitHub Actions 会自动构建 Windows + macOS 包并上传到对应 Release。
- 也可在 Actions 页手动触发 `Build Release Assets`，输入目标 tag 后执行。

## 目录
- `main.py`：主界面与监控逻辑
- `ticket_client.py`：12306 查询客户端
- `notifier.py`：邮件/企业微信通知
- `app_config.py`：配置加载与保存
- `scripts/`：macOS / Windows 打包脚本
