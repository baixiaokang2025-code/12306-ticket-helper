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
- **候补增强**：候补结果置顶高亮、候补专属提醒、候补信息自动复制、候补直达一键打开。
- **通知渠道**：
  - 邮件（SMTP）
  - 企业微信机器人 webhook
- **快捷跳转**：结果表格可选中某条线路，点击“打开选中下单页”直接打开对应日期/站点页面。
- **指定车次直达**：可直接输入车次并一键打开对应下单页，无需先在结果表格中选中。
- **车次辅助定位**：打开下单页时会自动复制车次到剪贴板，便于在12306页面快速筛选对应车次。
- **登录检测**：在“通知设置”页粘贴 Cookie 后可检测登录状态（仅检测，不会自动下单）。
- **稳态重试**：支持查询重试次数、指数退避、请求超时配置。
- **人工提交辅助**：命中后可自动复制关键信息到剪贴板，展示倒计时指引，并支持一键打开推荐直达/候补直达下单页。
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
bash scripts/build_macos.sh v1.1.4
```
产物：
- `dist/12306余票助手.app`
- `12306-ticket-helper-macos-v1.1.4.zip`

### Windows
在 `cmd` 或 `PowerShell` 中执行：
```bat
cd %USERPROFILE%\Desktop\12306-ticket-helper
scripts\build_windows.bat v1.1.4
```
产物：
- `dist\12306余票助手\12306余票助手.exe`
- `12306-ticket-helper-windows-v1.1.4.zip`

## 全流程发布（CI + Releases + Packages）
1. 提交代码并推送 `main`：触发 CI（依赖安装 + `py_compile` 语法检查）。
2. 打版本 tag（如 `v1.1.4`）并推送：触发跨平台打包。
3. Actions 自动执行：
   - 构建 `macOS` / `Windows` 安装包
   - 上传到 GitHub Release
   - 生成 `SHA256SUMS.txt` 校验文件
   - 发布到 GitHub Container Registry（`/packages`）

常用命令：
```bash
git add .
git commit -m "chore: release v1.1.4"
git push origin main
git tag v1.1.4
git push origin v1.1.4
```

## `/packages` 是什么
- GitHub 的 `/packages` 页面展示的是该仓库发布到 `ghcr.io` 的容器包。
- 本项目会发布 `12306-ticket-helper-assets`，镜像内包含本次 release 的 zip 安装包与 `SHA256SUMS.txt`。
- 拉取示例：
```bash
docker pull ghcr.io/<你的用户名>/12306-ticket-helper-assets:v1.1.4
```

## 目录
- `main.py`：主界面与监控逻辑
- `ticket_client.py`：12306 查询客户端
- `notifier.py`：邮件/企业微信通知
- `app_config.py`：配置加载与保存
- `scripts/`：macOS / Windows 打包脚本
- `.github/workflows/ci.yml`：CI 语法检查
- `.github/workflows/build-release-assets.yml`：构建、Release、Packages 全流程
- `packages/release-assets/Dockerfile`：Packages 镜像构建模板
