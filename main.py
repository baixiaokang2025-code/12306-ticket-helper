from __future__ import annotations

import random
import threading
import tkinter as tk
import webbrowser
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import time
from tkinter import messagebox, ttk
from typing import List

from app_config import AppSettings, EmailConfig, RouteItem, WeComConfig, load_settings, save_settings
from notifier import NotificationSender
from ticket_client import TicketQueryClient, TicketRow

SEAT_OPTIONS = ["任意", "商务座", "一等座", "二等座", "软卧", "硬卧", "硬座", "无座"]


@dataclass
class DisplayRow:
    route: RouteItem
    row: TicketRow


def has_ticket(value: str) -> bool:
    value = (value or "").strip()
    if not value or value in {"--", "无", "*"}:
        return False
    if value == "有":
        return True
    if value.isdigit():
        return int(value) > 0
    return True


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("12306 余票助手（合法版）")
        self.root.geometry("1260x760")

        self.client = TicketQueryClient()
        self.notifier = NotificationSender()

        self.querying = False
        self.monitoring = False
        self.after_job: str | None = None
        self.alerted_keys: set[str] = set()

        self.settings_path = Path(__file__).with_name("settings.json")
        self.settings = load_settings(self.settings_path)
        self.routes: List[RouteItem] = list(self.settings.routes)

        self.route_date_var = tk.StringVar(value=(date.today() + timedelta(days=1)).strftime("%Y-%m-%d"))
        self.route_from_var = tk.StringVar(value="")
        self.route_to_var = tk.StringVar(value="")

        self.interval_var = tk.IntVar(value=max(2, self.settings.interval_sec))
        self.train_filter_var = tk.StringVar(value=self.settings.train_filter)
        self.seat_var = tk.StringVar(value=self.settings.seat_filter if self.settings.seat_filter in SEAT_OPTIONS else "任意")
        self.status_var = tk.StringVar(value="就绪")
        self.route_count_var = tk.StringVar(value="线路数：0")

        self.email_enabled_var = tk.BooleanVar(value=self.settings.email.enabled)
        self.smtp_host_var = tk.StringVar(value=self.settings.email.smtp_host)
        self.smtp_port_var = tk.IntVar(value=self.settings.email.smtp_port)
        self.smtp_user_var = tk.StringVar(value=self.settings.email.username)
        self.smtp_pass_var = tk.StringVar(value=self.settings.email.password)
        self.smtp_from_var = tk.StringVar(value=self.settings.email.from_addr)
        self.smtp_to_var = tk.StringVar(value=self.settings.email.to_addrs)
        self.smtp_ssl_var = tk.BooleanVar(value=self.settings.email.use_ssl)

        self.wecom_enabled_var = tk.BooleanVar(value=self.settings.wecom.enabled)
        self.wecom_webhook_var = tk.StringVar(value=self.settings.wecom.webhook)

        self._build_ui()
        self._refresh_route_tree()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self) -> None:
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        tab_monitor = ttk.Frame(notebook)
        tab_notify = ttk.Frame(notebook)
        notebook.add(tab_monitor, text="监控")
        notebook.add(tab_notify, text="通知设置")

        self._build_monitor_tab(tab_monitor)
        self._build_notify_tab(tab_notify)

        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

    def _build_monitor_tab(self, parent: ttk.Frame) -> None:
        ctrl = ttk.LabelFrame(parent, text="监控设置", padding=10)
        ctrl.pack(fill=tk.X, padx=4, pady=4)

        ttk.Label(ctrl, text="刷新间隔(秒)").grid(row=0, column=0, sticky=tk.W, padx=(0, 4), pady=4)
        ttk.Spinbox(ctrl, from_=2, to=600, textvariable=self.interval_var, width=8).grid(row=0, column=1, pady=4)

        ttk.Label(ctrl, text="车次过滤").grid(row=0, column=2, sticky=tk.W, padx=(14, 4), pady=4)
        ttk.Entry(ctrl, textvariable=self.train_filter_var, width=14).grid(row=0, column=3, pady=4)

        ttk.Label(ctrl, text="提醒座位").grid(row=0, column=4, sticky=tk.W, padx=(14, 4), pady=4)
        ttk.Combobox(ctrl, textvariable=self.seat_var, values=SEAT_OPTIONS, width=10, state="readonly").grid(
            row=0, column=5, pady=4
        )

        btns = ttk.Frame(ctrl)
        btns.grid(row=0, column=6, padx=(16, 0), sticky=tk.E)
        self.query_btn = ttk.Button(btns, text="立即查询", command=self.trigger_query)
        self.query_btn.pack(side=tk.LEFT, padx=3)
        self.start_btn = ttk.Button(btns, text="开始监控", command=self.start_monitor)
        self.start_btn.pack(side=tk.LEFT, padx=3)
        self.stop_btn = ttk.Button(btns, text="停止监控", command=self.stop_monitor, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=3)
        ttk.Button(btns, text="打开12306官网", command=self.open_web).pack(side=tk.LEFT, padx=3)
        ttk.Button(btns, text="保存设置", command=self.save_current_settings).pack(side=tk.LEFT, padx=3)

        routes_frame = ttk.LabelFrame(parent, text="线路管理（支持多线路）", padding=10)
        routes_frame.pack(fill=tk.X, padx=4, pady=4)

        ttk.Label(routes_frame, text="日期").grid(row=0, column=0, sticky=tk.W, padx=(0, 4), pady=4)
        ttk.Entry(routes_frame, textvariable=self.route_date_var, width=12).grid(row=0, column=1, pady=4)

        ttk.Label(routes_frame, text="出发站").grid(row=0, column=2, sticky=tk.W, padx=(12, 4), pady=4)
        ttk.Entry(routes_frame, textvariable=self.route_from_var, width=14).grid(row=0, column=3, pady=4)

        ttk.Label(routes_frame, text="到达站").grid(row=0, column=4, sticky=tk.W, padx=(12, 4), pady=4)
        ttk.Entry(routes_frame, textvariable=self.route_to_var, width=14).grid(row=0, column=5, pady=4)

        ttk.Button(routes_frame, text="添加线路", command=self.add_route).grid(row=0, column=6, padx=(12, 4), pady=4)
        ttk.Button(routes_frame, text="删除选中", command=self.remove_selected_routes).grid(row=0, column=7, padx=4, pady=4)
        ttk.Button(routes_frame, text="清空线路", command=self.clear_routes).grid(row=0, column=8, padx=4, pady=4)
        ttk.Label(routes_frame, textvariable=self.route_count_var).grid(row=0, column=9, padx=(10, 0), sticky=tk.E)

        route_table_frame = ttk.Frame(routes_frame)
        route_table_frame.grid(row=1, column=0, columnspan=10, sticky=tk.EW, pady=(6, 0))
        routes_frame.columnconfigure(9, weight=1)

        self.route_tree = ttk.Treeview(route_table_frame, columns=("date", "from", "to"), show="headings", height=4)
        self.route_tree.heading("date", text="日期")
        self.route_tree.heading("from", text="出发站")
        self.route_tree.heading("to", text="到达站")
        self.route_tree.column("date", width=120, anchor=tk.CENTER)
        self.route_tree.column("from", width=140, anchor=tk.CENTER)
        self.route_tree.column("to", width=140, anchor=tk.CENTER)

        route_scroll = ttk.Scrollbar(route_table_frame, orient=tk.VERTICAL, command=self.route_tree.yview)
        self.route_tree.configure(yscrollcommand=route_scroll.set)
        self.route_tree.pack(side=tk.LEFT, fill=tk.X, expand=True)
        route_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        tip = ttk.Label(parent, text="说明：仅做余票查询与提醒，不自动下单。", padding=(8, 4, 8, 0))
        tip.pack(anchor=tk.W)

        result_frame = ttk.LabelFrame(parent, text="余票结果", padding=8)
        result_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        columns = (
            "route_date",
            "route_pair",
            "train",
            "from",
            "to",
            "duration",
            "swz",
            "ydz",
            "edz",
            "rw",
            "yw",
            "yz",
            "wz",
        )
        self.result_tree = ttk.Treeview(result_frame, columns=columns, show="headings", height=16)

        headers = {
            "route_date": "线路日期",
            "route_pair": "线路",
            "train": "车次",
            "from": "出发",
            "to": "到达",
            "duration": "历时",
            "swz": "商务座",
            "ydz": "一等座",
            "edz": "二等座",
            "rw": "软卧",
            "yw": "硬卧",
            "yz": "硬座",
            "wz": "无座",
        }
        widths = {
            "route_date": 95,
            "route_pair": 170,
            "train": 78,
            "from": 135,
            "to": 135,
            "duration": 80,
            "swz": 65,
            "ydz": 65,
            "edz": 65,
            "rw": 65,
            "yw": 65,
            "yz": 65,
            "wz": 65,
        }
        for key in columns:
            self.result_tree.heading(key, text=headers[key])
            self.result_tree.column(key, width=widths[key], anchor=tk.CENTER)

        result_scroll_y = ttk.Scrollbar(result_frame, orient=tk.VERTICAL, command=self.result_tree.yview)
        self.result_tree.configure(yscrollcommand=result_scroll_y.set)

        self.result_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        result_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_notify_tab(self, parent: ttk.Frame) -> None:
        mail_frame = ttk.LabelFrame(parent, text="邮件通知", padding=10)
        mail_frame.pack(fill=tk.X, padx=6, pady=(6, 4))

        ttk.Checkbutton(mail_frame, text="启用邮件通知", variable=self.email_enabled_var).grid(
            row=0, column=0, columnspan=2, sticky=tk.W, pady=2
        )

        ttk.Label(mail_frame, text="SMTP 主机").grid(row=1, column=0, sticky=tk.W, padx=(0, 4), pady=3)
        ttk.Entry(mail_frame, textvariable=self.smtp_host_var, width=28).grid(row=1, column=1, sticky=tk.W, pady=3)

        ttk.Label(mail_frame, text="端口").grid(row=1, column=2, sticky=tk.W, padx=(16, 4), pady=3)
        ttk.Spinbox(mail_frame, from_=1, to=65535, textvariable=self.smtp_port_var, width=8).grid(
            row=1, column=3, sticky=tk.W, pady=3
        )

        ttk.Label(mail_frame, text="用户名").grid(row=2, column=0, sticky=tk.W, padx=(0, 4), pady=3)
        ttk.Entry(mail_frame, textvariable=self.smtp_user_var, width=28).grid(row=2, column=1, sticky=tk.W, pady=3)

        ttk.Label(mail_frame, text="密码/授权码").grid(row=2, column=2, sticky=tk.W, padx=(16, 4), pady=3)
        ttk.Entry(mail_frame, textvariable=self.smtp_pass_var, width=28, show="*").grid(row=2, column=3, sticky=tk.W, pady=3)

        ttk.Label(mail_frame, text="发件人(可空)").grid(row=3, column=0, sticky=tk.W, padx=(0, 4), pady=3)
        ttk.Entry(mail_frame, textvariable=self.smtp_from_var, width=28).grid(row=3, column=1, sticky=tk.W, pady=3)

        ttk.Label(mail_frame, text="收件人(逗号分隔)").grid(row=3, column=2, sticky=tk.W, padx=(16, 4), pady=3)
        ttk.Entry(mail_frame, textvariable=self.smtp_to_var, width=36).grid(row=3, column=3, sticky=tk.W, pady=3)

        ttk.Checkbutton(mail_frame, text="使用 SSL", variable=self.smtp_ssl_var).grid(row=4, column=0, sticky=tk.W, pady=2)

        wecom_frame = ttk.LabelFrame(parent, text="企业微信机器人通知", padding=10)
        wecom_frame.pack(fill=tk.X, padx=6, pady=4)

        ttk.Checkbutton(wecom_frame, text="启用企业微信通知", variable=self.wecom_enabled_var).grid(
            row=0, column=0, sticky=tk.W, pady=2
        )
        ttk.Label(wecom_frame, text="Webhook").grid(row=1, column=0, sticky=tk.W, padx=(0, 4), pady=3)
        ttk.Entry(wecom_frame, textvariable=self.wecom_webhook_var, width=100).grid(row=1, column=1, sticky=tk.W, pady=3)

        action_frame = ttk.Frame(parent)
        action_frame.pack(fill=tk.X, padx=6, pady=(4, 6))
        ttk.Button(action_frame, text="发送测试通知", command=self.send_test_notification).pack(side=tk.LEFT)
        ttk.Button(action_frame, text="保存设置", command=self.save_current_settings).pack(side=tk.LEFT, padx=6)

        note = ttk.Label(
            parent,
            text=(
                "提示：\n"
                "1) 邮件通常需填写 SMTP 授权码，而不是登录密码。\n"
                "2) 企业微信机器人 webhook 可在群机器人设置中获取。"
            ),
            justify=tk.LEFT,
            padding=(6, 0, 6, 0),
        )
        note.pack(anchor=tk.W)

    def _refresh_route_tree(self) -> None:
        for item in self.route_tree.get_children():
            self.route_tree.delete(item)

        unique: List[RouteItem] = []
        seen = set()
        for route in self.routes:
            if route.key() in seen:
                continue
            seen.add(route.key())
            unique.append(route)
        self.routes = unique

        for route in self.routes:
            self.route_tree.insert("", tk.END, iid=route.key(), values=(route.train_date, route.from_station, route.to_station))

        self.route_count_var.set(f"线路数：{len(self.routes)}")

    def add_route(self) -> None:
        train_date = self.route_date_var.get().strip()
        from_station = self.route_from_var.get().strip()
        to_station = self.route_to_var.get().strip()

        if not train_date or not from_station or not to_station:
            messagebox.showwarning("提示", "请填写完整的日期、出发站和到达站")
            return

        if not self._valid_date(train_date):
            messagebox.showwarning("提示", "日期格式应为 YYYY-MM-DD")
            return

        route = RouteItem(train_date=train_date, from_station=from_station, to_station=to_station)
        if route.key() in {item.key() for item in self.routes}:
            messagebox.showinfo("提示", "该线路已存在")
            return

        self.routes.append(route)
        self._refresh_route_tree()

    def remove_selected_routes(self) -> None:
        selected = self.route_tree.selection()
        if not selected:
            return

        selected_keys = set(selected)
        self.routes = [route for route in self.routes if route.key() not in selected_keys]
        self._refresh_route_tree()

    def clear_routes(self) -> None:
        if not self.routes:
            return
        if not messagebox.askyesno("确认", "确定清空所有线路吗？"):
            return
        self.routes = []
        self._refresh_route_tree()

    def open_web(self) -> None:
        webbrowser.open("https://kyfw.12306.cn/otn/leftTicket/init")

    def start_monitor(self) -> None:
        if not self._get_routes_for_query():
            return
        self.monitoring = True
        self.alerted_keys.clear()
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.status_var.set("监控已启动")
        self.trigger_query()

    def stop_monitor(self) -> None:
        self.monitoring = False
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        if self.after_job:
            self.root.after_cancel(self.after_job)
            self.after_job = None
        self.status_var.set("监控已停止")

    def schedule_next(self) -> None:
        if not self.monitoring:
            return
        interval_ms = max(2, int(self.interval_var.get())) * 1000
        self.after_job = self.root.after(interval_ms, self.trigger_query)

    def trigger_query(self) -> None:
        if self.querying:
            return

        routes = self._get_routes_for_query()
        if not routes:
            messagebox.showwarning("提示", "请先添加至少一条线路，或填写线路输入框后再查询")
            return

        self.querying = True
        self.query_btn.config(state=tk.DISABLED)
        self.status_var.set(f"查询中...（{len(routes)} 条线路）")

        threading.Thread(target=self._query_in_thread, args=(routes,), daemon=True).start()

    def _query_in_thread(self, routes: List[RouteItem]) -> None:
        rows: List[DisplayRow] = []
        errors: List[str] = []

        for index, route in enumerate(routes):
            try:
                route_rows = self.client.query(route.train_date, route.from_station, route.to_station)
                rows.extend([DisplayRow(route=route, row=item) for item in route_rows])
            except Exception as exc:  # noqa: BLE001
                errors.append(f"[{route.train_date} {route.from_station}->{route.to_station}] {exc}")
            if index < len(routes) - 1:
                time.sleep(random.uniform(0.4, 1.0))

        self.root.after(0, lambda: self.on_query_done(rows, errors))

    def on_query_done(self, rows: List[DisplayRow], errors: List[str]) -> None:
        self.querying = False
        self.query_btn.config(state=tk.NORMAL)

        filtered = self._apply_filters(rows)
        self._render_rows(filtered)
        alert_lines = self._collect_new_alerts(filtered)
        if alert_lines:
            self.root.bell()
            preview = "\n".join(alert_lines[:8])
            messagebox.showinfo("有票提醒", f"发现可购车次：\n{preview}\n\n请尽快前往12306官网下单。")
            self._send_notifications_async(alert_lines, title="12306到票提醒")

        now = datetime.now().strftime("%H:%M:%S")
        if errors:
            short_err = "；".join(errors[:2])
            self.status_var.set(f"查询完成：{len(filtered)} 条（失败{len(errors)}条，{now}）{short_err}")
        else:
            self.status_var.set(f"查询完成：{len(filtered)} 条（{now}）")

        self.schedule_next()

    def _apply_filters(self, rows: List[DisplayRow]) -> List[DisplayRow]:
        train_kw = self.train_filter_var.get().strip().upper()
        seat = self.seat_var.get()

        out: List[DisplayRow] = []
        for item in rows:
            if train_kw and train_kw not in item.row.train_no.upper():
                continue
            if seat != "任意" and not has_ticket(item.row.seats.get(seat, "--")):
                continue
            out.append(item)
        return out

    def _render_rows(self, rows: List[DisplayRow]) -> None:
        for item in self.result_tree.get_children():
            self.result_tree.delete(item)

        for item in rows:
            row = item.row
            route = item.route
            values = (
                route.train_date,
                f"{route.from_station}->{route.to_station}",
                row.train_no,
                f"{row.from_station} {row.start_time}",
                f"{row.to_station} {row.arrive_time}",
                row.duration,
                row.seats.get("商务座", "--"),
                row.seats.get("一等座", "--"),
                row.seats.get("二等座", "--"),
                row.seats.get("软卧", "--"),
                row.seats.get("硬卧", "--"),
                row.seats.get("硬座", "--"),
                row.seats.get("无座", "--"),
            )
            self.result_tree.insert("", tk.END, values=values)

    def _collect_new_alerts(self, rows: List[DisplayRow]) -> List[str]:
        seat = self.seat_var.get()
        lines: List[str] = []

        for item in rows:
            route = item.route
            row = item.row
            seats_to_check = [seat] if seat != "任意" else ["商务座", "一等座", "二等座", "软卧", "硬卧", "硬座", "无座"]
            for seat_name in seats_to_check:
                value = row.seats.get(seat_name, "--")
                if not has_ticket(value):
                    continue
                key = f"{route.key()}:{row.train_no}:{seat_name}"
                if key in self.alerted_keys:
                    continue
                self.alerted_keys.add(key)
                lines.append(f"[{route.train_date} {route.from_station}->{route.to_station}] {row.train_no} {seat_name}：{value}")

        return lines

    def _get_routes_for_query(self) -> List[RouteItem]:
        if self.routes:
            return list(self.routes)

        train_date = self.route_date_var.get().strip()
        from_station = self.route_from_var.get().strip()
        to_station = self.route_to_var.get().strip()
        if train_date and from_station and to_station and self._valid_date(train_date):
            return [RouteItem(train_date=train_date, from_station=from_station, to_station=to_station)]
        return []

    def _valid_date(self, text: str) -> bool:
        try:
            datetime.strptime(text, "%Y-%m-%d")
            return True
        except ValueError:
            return False

    def _build_email_config(self) -> EmailConfig:
        return EmailConfig(
            enabled=self.email_enabled_var.get(),
            smtp_host=self.smtp_host_var.get().strip(),
            smtp_port=max(1, int(self.smtp_port_var.get())),
            username=self.smtp_user_var.get().strip(),
            password=self.smtp_pass_var.get(),
            from_addr=self.smtp_from_var.get().strip(),
            to_addrs=self.smtp_to_var.get().strip(),
            use_ssl=self.smtp_ssl_var.get(),
        )

    def _build_wecom_config(self) -> WeComConfig:
        return WeComConfig(
            enabled=self.wecom_enabled_var.get(),
            webhook=self.wecom_webhook_var.get().strip(),
        )

    def _send_notifications_async(self, lines: List[str], *, title: str) -> None:
        email_cfg = self._build_email_config()
        wecom_cfg = self._build_wecom_config()

        if not email_cfg.enabled and not wecom_cfg.enabled:
            return

        threading.Thread(
            target=self._notify_worker,
            args=(lines, email_cfg, wecom_cfg, title),
            daemon=True,
        ).start()

    def _notify_worker(self, lines: List[str], email_cfg: EmailConfig, wecom_cfg: WeComConfig, title: str) -> None:
        errors = self.notifier.send(lines, email_cfg, wecom_cfg, title=title)
        self.root.after(0, lambda: self._on_notify_done(errors))

    def _on_notify_done(self, errors: List[str]) -> None:
        if errors:
            messagebox.showwarning("通知结果", "\n".join(errors))
        else:
            self.status_var.set(f"{self.status_var.get()} | 已发送通知")

    def send_test_notification(self) -> None:
        email_cfg = self._build_email_config()
        wecom_cfg = self._build_wecom_config()
        if not email_cfg.enabled and not wecom_cfg.enabled:
            messagebox.showwarning("提示", "请先启用至少一种通知方式")
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [f"测试消息：当前时间 {now}", "若你收到此消息，说明通知配置可用。"]
        self._send_notifications_async(lines, title="12306测试通知")
        self.status_var.set("测试通知发送中...")

    def _collect_settings(self) -> AppSettings:
        return AppSettings(
            interval_sec=max(2, int(self.interval_var.get())),
            train_filter=self.train_filter_var.get().strip(),
            seat_filter=self.seat_var.get().strip(),
            routes=list(self.routes),
            email=self._build_email_config(),
            wecom=self._build_wecom_config(),
        )

    def save_current_settings(self, silent: bool = False) -> None:
        settings = self._collect_settings()
        try:
            save_settings(self.settings_path, settings)
        except Exception as exc:  # noqa: BLE001
            if not silent:
                messagebox.showerror("保存失败", str(exc))
            return
        if not silent:
            messagebox.showinfo("提示", f"设置已保存：{self.settings_path}")
        self.status_var.set("设置已保存")

    def on_close(self) -> None:
        self.save_current_settings(silent=True)
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
