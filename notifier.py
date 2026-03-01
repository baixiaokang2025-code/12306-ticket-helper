from __future__ import annotations

import smtplib
from datetime import datetime
from email.header import Header
from email.mime.text import MIMEText
from typing import List

import requests

from app_config import EmailConfig, WeComConfig


class NotificationSender:
    def send(self, lines: List[str], email_cfg: EmailConfig, wecom_cfg: WeComConfig, *, title: str) -> List[str]:
        errors: List[str] = []

        if email_cfg.enabled:
            try:
                self._send_email(lines, email_cfg, title=title)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"邮件通知失败：{exc}")

        if wecom_cfg.enabled:
            try:
                self._send_wecom(lines, wecom_cfg, title=title)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"企业微信通知失败：{exc}")

        return errors

    def _send_email(self, lines: List[str], cfg: EmailConfig, *, title: str) -> None:
        host = cfg.smtp_host.strip()
        username = cfg.username.strip()
        password = cfg.password
        to_addrs = self._split_receivers(cfg.to_addrs)
        if not host:
            raise ValueError("SMTP 主机不能为空")
        if not username:
            raise ValueError("邮箱用户名不能为空")
        if not password:
            raise ValueError("邮箱密码/授权码不能为空")
        if not to_addrs:
            raise ValueError("收件人不能为空")

        sender = cfg.from_addr.strip() or username
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        body = f"{title}\n时间：{now}\n\n" + "\n".join(lines)

        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = Header(title, "utf-8")
        msg["From"] = sender
        msg["To"] = ", ".join(to_addrs)

        if cfg.use_ssl:
            with smtplib.SMTP_SSL(host, int(cfg.smtp_port), timeout=15) as smtp:
                smtp.login(username, password)
                smtp.sendmail(sender, to_addrs, msg.as_string())
        else:
            with smtplib.SMTP(host, int(cfg.smtp_port), timeout=15) as smtp:
                smtp.ehlo()
                try:
                    smtp.starttls()
                    smtp.ehlo()
                except Exception:
                    # Some SMTP servers may not support STARTTLS.
                    pass
                smtp.login(username, password)
                smtp.sendmail(sender, to_addrs, msg.as_string())

    def _send_wecom(self, lines: List[str], cfg: WeComConfig, *, title: str) -> None:
        webhook = cfg.webhook.strip()
        if not webhook:
            raise ValueError("Webhook 不能为空")

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        text = "\n".join(lines)
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": f"### {title}\n> 时间：{now}\n\n{text}",
            },
        }
        resp = requests.post(webhook, json=payload, timeout=12)
        resp.raise_for_status()
        data = resp.json()
        if data.get("errcode", 0) != 0:
            raise RuntimeError(f"errcode={data.get('errcode')} errmsg={data.get('errmsg')}")

    @staticmethod
    def _split_receivers(raw: str) -> List[str]:
        text = (raw or "").replace(";", ",").replace("\n", ",")
        return [item.strip() for item in text.split(",") if item.strip()]
