from __future__ import annotations

import random
import re
import threading
import tkinter as tk
import webbrowser
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import time
from tkinter import messagebox, ttk
from typing import Dict, List, Optional, Tuple

from app_config import AppSettings, EmailConfig, RouteItem, WeComConfig, load_settings, save_settings
from notifier import NotificationSender
from ticket_client import QueryRequestError, RetryPolicy, TicketQueryClient, TicketRow

SEAT_OPTIONS = ["任意", "商务座", "一等座", "二等座", "软卧", "硬卧", "硬座", "无座"]
DEFAULT_TRANSFER_HUBS = ["北京南", "上海虹桥", "南京南", "杭州东", "武汉", "郑州东", "西安北", "长沙南"]


@dataclass
class DisplayRow:
    route: RouteItem
    row: TicketRow


@dataclass
class TransferPlan:
    route: RouteItem
    via_station: str
    first_row: TicketRow
    second_row: TicketRow
    first_depart_at: datetime
    first_arrive_at: datetime
    second_depart_at: datetime
    second_arrive_at: datetime
    wait_minutes: int
    total_minutes: int
    seat_hint: str
    seat_score: int


def has_ticket(value: str) -> bool:
    value = (value or "").strip()
    if not value or value in {"--", "无", "*"}:
        return False
    if value == "有":
        return True
    if value.isdigit():
        return int(value) > 0
    return True


def is_candidate_ticket(value: str) -> bool:
    text = (value or "").strip()
    return bool(text) and "候补" in text


def extract_cookie_expiration(cookie_text: str) -> Optional[datetime]:
    text = (cookie_text or "").strip()
    if not text:
        return None
    match = re.search(r"RAIL_EXPIRATION=([^;\\s]+)", text)
    if not match:
        return None
    raw = match.group(1).strip()
    if not raw.isdigit():
        return None
    value = int(raw)
    if value > 10**12:
        ts = value / 1000.0
    else:
        ts = float(value)
    try:
        return datetime.fromtimestamp(ts)
    except (ValueError, OSError):
        return None


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
        self.result_row_map: Dict[str, DisplayRow] = {}
        self.transfer_row_map: Dict[str, TransferPlan] = {}
        self.error_stats: Dict[str, Dict[str, str]] = {}

        self.settings_path = Path(__file__).with_name("settings.json")
        self.settings = load_settings(self.settings_path)
        self.routes: List[RouteItem] = list(self.settings.routes)

        self.route_date_var = tk.StringVar(value=(date.today() + timedelta(days=1)).strftime("%Y-%m-%d"))
        self.route_from_var = tk.StringVar(value="")
        self.route_to_var = tk.StringVar(value="")
        self.route_group_var = tk.StringVar(value="默认")
        self.route_enabled_var = tk.BooleanVar(value=True)
        self.batch_group_var = tk.StringVar(value="默认")

        self.interval_var = tk.IntVar(value=max(2, self.settings.interval_sec))
        self.train_filter_var = tk.StringVar(value=self.settings.train_filter)
        self.seat_var = tk.StringVar(value=self.settings.seat_filter if self.settings.seat_filter in SEAT_OPTIONS else "任意")
        self.retry_attempts_var = tk.IntVar(value=max(1, self.settings.retry_attempts))
        self.retry_base_delay_var = tk.DoubleVar(value=max(0.1, self.settings.retry_base_delay_sec))
        self.retry_max_delay_var = tk.DoubleVar(value=max(0.2, self.settings.retry_max_delay_sec))
        self.request_timeout_var = tk.DoubleVar(value=max(2.0, self.settings.request_timeout_sec))
        self.transfer_enabled_var = tk.BooleanVar(value=self.settings.transfer_enabled)
        self.transfer_hubs_var = tk.StringVar(value=self.settings.transfer_hubs)
        self.transfer_min_layover_var = tk.IntVar(value=max(5, self.settings.transfer_min_layover_min))
        self.transfer_max_layover_var = tk.IntVar(value=max(30, self.settings.transfer_max_layover_min))
        self.transfer_max_plans_var = tk.IntVar(value=max(1, self.settings.transfer_max_plans))
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
        self.cookie_var = tk.StringVar()
        self.login_state_var = tk.StringVar(value="登录状态：未检测")
        self.cookie_expire_var = tk.StringVar(value="Cookie到期：未提供")

        self._build_ui()
        self._refresh_route_tree()
        self._update_cookie_expiration_tip()
        self.cookie_var.trace_add("write", lambda *_: self._update_cookie_expiration_tip())

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

        ttk.Label(ctrl, text="重试次数").grid(row=1, column=0, sticky=tk.W, padx=(0, 4), pady=4)
        ttk.Spinbox(ctrl, from_=1, to=8, textvariable=self.retry_attempts_var, width=8).grid(row=1, column=1, pady=4)

        ttk.Label(ctrl, text="基础退避(s)").grid(row=1, column=2, sticky=tk.W, padx=(14, 4), pady=4)
        ttk.Spinbox(ctrl, from_=0.1, to=5.0, increment=0.1, textvariable=self.retry_base_delay_var, width=8).grid(
            row=1, column=3, pady=4
        )

        ttk.Label(ctrl, text="最大退避(s)").grid(row=1, column=4, sticky=tk.W, padx=(14, 4), pady=4)
        ttk.Spinbox(ctrl, from_=0.2, to=15.0, increment=0.2, textvariable=self.retry_max_delay_var, width=8).grid(
            row=1, column=5, pady=4
        )

        ttk.Label(ctrl, text="请求超时(s)").grid(row=1, column=6, sticky=tk.W, padx=(14, 4), pady=4)
        ttk.Spinbox(ctrl, from_=2.0, to=30.0, increment=0.5, textvariable=self.request_timeout_var, width=8).grid(
            row=1, column=7, pady=4
        )

        ttk.Checkbutton(ctrl, text="启用智能中转", variable=self.transfer_enabled_var).grid(
            row=2, column=0, columnspan=2, sticky=tk.W, padx=(0, 4), pady=4
        )
        ttk.Label(ctrl, text="中转站(逗号分隔)").grid(row=2, column=2, sticky=tk.W, padx=(14, 4), pady=4)
        ttk.Entry(ctrl, textvariable=self.transfer_hubs_var, width=20).grid(row=2, column=3, pady=4, sticky=tk.W)
        ttk.Label(ctrl, text="最短换乘(分)").grid(row=2, column=4, sticky=tk.W, padx=(14, 4), pady=4)
        ttk.Spinbox(ctrl, from_=5, to=360, textvariable=self.transfer_min_layover_var, width=8).grid(row=2, column=5, pady=4)
        ttk.Label(ctrl, text="最长换乘(分)").grid(row=2, column=6, sticky=tk.W, padx=(14, 4), pady=4)
        ttk.Spinbox(ctrl, from_=30, to=1440, textvariable=self.transfer_max_layover_var, width=8).grid(row=2, column=7, pady=4)
        ttk.Label(ctrl, text="最多方案").grid(row=2, column=8, sticky=tk.W, padx=(14, 4), pady=4)
        ttk.Spinbox(ctrl, from_=1, to=20, textvariable=self.transfer_max_plans_var, width=6).grid(row=2, column=9, pady=4)

        btns = ttk.Frame(ctrl)
        btns.grid(row=0, column=10, rowspan=3, padx=(16, 0), sticky=tk.E)
        self.query_btn = ttk.Button(btns, text="立即查询", command=self.trigger_query)
        self.query_btn.pack(side=tk.LEFT, padx=3)
        self.start_btn = ttk.Button(btns, text="开始监控", command=self.start_monitor)
        self.start_btn.pack(side=tk.LEFT, padx=3)
        self.stop_btn = ttk.Button(btns, text="停止监控", command=self.stop_monitor, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=3)
        ttk.Button(btns, text="打开12306官网", command=self.open_web).pack(side=tk.LEFT, padx=3)
        ttk.Button(btns, text="打开选中下单页", command=self.open_selected_order_page).pack(side=tk.LEFT, padx=3)
        ttk.Button(btns, text="保存设置", command=self.save_current_settings).pack(side=tk.LEFT, padx=3)

        routes_frame = ttk.LabelFrame(parent, text="线路管理（支持多线路）", padding=10)
        routes_frame.pack(fill=tk.X, padx=4, pady=4)

        ttk.Label(routes_frame, text="日期").grid(row=0, column=0, sticky=tk.W, padx=(0, 4), pady=4)
        ttk.Entry(routes_frame, textvariable=self.route_date_var, width=12).grid(row=0, column=1, pady=4)

        ttk.Label(routes_frame, text="出发站").grid(row=0, column=2, sticky=tk.W, padx=(12, 4), pady=4)
        ttk.Entry(routes_frame, textvariable=self.route_from_var, width=14).grid(row=0, column=3, pady=4)

        ttk.Label(routes_frame, text="到达站").grid(row=0, column=4, sticky=tk.W, padx=(12, 4), pady=4)
        ttk.Entry(routes_frame, textvariable=self.route_to_var, width=14).grid(row=0, column=5, pady=4)

        ttk.Label(routes_frame, text="分组").grid(row=0, column=6, sticky=tk.W, padx=(12, 4), pady=4)
        ttk.Entry(routes_frame, textvariable=self.route_group_var, width=10).grid(row=0, column=7, pady=4)
        ttk.Checkbutton(routes_frame, text="启用", variable=self.route_enabled_var).grid(row=0, column=8, pady=4, padx=4)

        ttk.Button(routes_frame, text="添加线路", command=self.add_route).grid(row=0, column=9, padx=(8, 4), pady=4)
        ttk.Button(routes_frame, text="删除选中", command=self.remove_selected_routes).grid(row=0, column=10, padx=4, pady=4)
        ttk.Button(routes_frame, text="清空线路", command=self.clear_routes).grid(row=0, column=11, padx=4, pady=4)
        ttk.Label(routes_frame, text="批量分组").grid(row=1, column=0, sticky=tk.W, padx=(0, 4), pady=4)
        ttk.Entry(routes_frame, textvariable=self.batch_group_var, width=12).grid(row=1, column=1, pady=4)
        ttk.Button(routes_frame, text="按分组启用", command=self.enable_routes_by_group).grid(row=1, column=2, padx=4, pady=4)
        ttk.Button(routes_frame, text="按分组停用", command=self.disable_routes_by_group).grid(row=1, column=3, padx=4, pady=4)
        ttk.Button(routes_frame, text="启用选中", command=self.enable_selected_routes).grid(row=1, column=4, padx=4, pady=4)
        ttk.Button(routes_frame, text="停用选中", command=self.disable_selected_routes).grid(row=1, column=5, padx=4, pady=4)
        ttk.Label(routes_frame, textvariable=self.route_count_var).grid(row=1, column=11, padx=(10, 0), sticky=tk.E)

        route_table_frame = ttk.Frame(routes_frame)
        route_table_frame.grid(row=2, column=0, columnspan=12, sticky=tk.EW, pady=(6, 0))
        routes_frame.columnconfigure(11, weight=1)

        self.route_tree = ttk.Treeview(route_table_frame, columns=("date", "from", "to", "group", "enabled"), show="headings", height=5)
        self.route_tree.heading("date", text="日期")
        self.route_tree.heading("from", text="出发站")
        self.route_tree.heading("to", text="到达站")
        self.route_tree.heading("group", text="分组")
        self.route_tree.heading("enabled", text="状态")
        self.route_tree.column("date", width=120, anchor=tk.CENTER)
        self.route_tree.column("from", width=140, anchor=tk.CENTER)
        self.route_tree.column("to", width=140, anchor=tk.CENTER)
        self.route_tree.column("group", width=100, anchor=tk.CENTER)
        self.route_tree.column("enabled", width=80, anchor=tk.CENTER)

        route_scroll = ttk.Scrollbar(route_table_frame, orient=tk.VERTICAL, command=self.route_tree.yview)
        self.route_tree.configure(yscrollcommand=route_scroll.set)
        self.route_tree.pack(side=tk.LEFT, fill=tk.X, expand=True)
        route_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        error_frame = ttk.LabelFrame(parent, text="错误分类面板", padding=8)
        error_frame.pack(fill=tk.X, padx=4, pady=(0, 4))
        ttk.Button(error_frame, text="清空统计", command=self.clear_error_stats).pack(anchor=tk.E, pady=(0, 4))
        self.error_tree = ttk.Treeview(error_frame, columns=("category", "count", "last"), show="headings", height=4)
        self.error_tree.heading("category", text="分类")
        self.error_tree.heading("count", text="次数")
        self.error_tree.heading("last", text="最近错误")
        self.error_tree.column("category", width=110, anchor=tk.CENTER)
        self.error_tree.column("count", width=80, anchor=tk.CENTER)
        self.error_tree.column("last", width=900, anchor=tk.W)
        err_scroll = ttk.Scrollbar(error_frame, orient=tk.VERTICAL, command=self.error_tree.yview)
        self.error_tree.configure(yscrollcommand=err_scroll.set)
        self.error_tree.pack(side=tk.LEFT, fill=tk.X, expand=True)
        err_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        tip = ttk.Label(parent, text="说明：仅做余票查询/候补提醒与跳转，不自动下单。", padding=(8, 4, 8, 0))
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

        transfer_frame = ttk.LabelFrame(parent, text="智能中转方案（1次中转）", padding=8)
        transfer_frame.pack(fill=tk.BOTH, expand=False, padx=4, pady=(0, 4))
        action_row = ttk.Frame(transfer_frame)
        action_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(action_row, text="打开选中中转首段", command=self.open_selected_transfer_first_leg).pack(side=tk.LEFT)
        ttk.Button(action_row, text="打开选中中转次段", command=self.open_selected_transfer_second_leg).pack(side=tk.LEFT, padx=6)
        self.transfer_summary_var = tk.StringVar(value="暂无中转方案")
        ttk.Label(action_row, textvariable=self.transfer_summary_var).pack(side=tk.LEFT, padx=(12, 0))

        transfer_cols = ("route_date", "route_pair", "plan", "first", "second", "wait", "total", "seat")
        self.transfer_tree = ttk.Treeview(transfer_frame, columns=transfer_cols, show="headings", height=6)
        transfer_headers = {
            "route_date": "日期",
            "route_pair": "线路",
            "plan": "中转站",
            "first": "第一程",
            "second": "第二程",
            "wait": "换乘等待",
            "total": "总耗时",
            "seat": "座位建议",
        }
        transfer_widths = {
            "route_date": 95,
            "route_pair": 150,
            "plan": 110,
            "first": 220,
            "second": 220,
            "wait": 90,
            "total": 90,
            "seat": 250,
        }
        for key in transfer_cols:
            self.transfer_tree.heading(key, text=transfer_headers[key])
            self.transfer_tree.column(key, width=transfer_widths[key], anchor=tk.CENTER)

        transfer_scroll_y = ttk.Scrollbar(transfer_frame, orient=tk.VERTICAL, command=self.transfer_tree.yview)
        self.transfer_tree.configure(yscrollcommand=transfer_scroll_y.set)
        self.transfer_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        transfer_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)

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

        login_frame = ttk.LabelFrame(parent, text="12306登录状态检测（可选）", padding=10)
        login_frame.pack(fill=tk.X, padx=6, pady=4)
        ttk.Label(login_frame, text="Cookie").grid(row=0, column=0, sticky=tk.W, padx=(0, 4), pady=3)
        ttk.Entry(login_frame, textvariable=self.cookie_var, width=100).grid(row=0, column=1, sticky=tk.W, pady=3)
        ttk.Button(login_frame, text="检测登录状态", command=self.check_login_status).grid(row=0, column=2, padx=6, pady=3)
        ttk.Button(login_frame, text="清空Cookie", command=self.clear_cookie_text).grid(row=0, column=3, padx=4, pady=3)
        ttk.Label(login_frame, textvariable=self.login_state_var).grid(row=1, column=0, columnspan=4, sticky=tk.W, pady=(2, 0))
        ttk.Label(login_frame, textvariable=self.cookie_expire_var).grid(row=2, column=0, columnspan=4, sticky=tk.W, pady=(2, 0))

        action_frame = ttk.Frame(parent)
        action_frame.pack(fill=tk.X, padx=6, pady=(4, 6))
        ttk.Button(action_frame, text="发送测试通知", command=self.send_test_notification).pack(side=tk.LEFT)
        ttk.Button(action_frame, text="保存设置", command=self.save_current_settings).pack(side=tk.LEFT, padx=6)

        note = ttk.Label(
            parent,
            text=(
                "提示：\n"
                "1) 邮件通常需填写 SMTP 授权码，而不是登录密码。\n"
                "2) 企业微信机器人 webhook 可在群机器人设置中获取。\n"
                "3) 登录状态检测只校验你粘贴的 Cookie，不会读取浏览器登录态。"
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
            self.route_tree.insert(
                "",
                tk.END,
                iid=route.key(),
                values=(
                    route.train_date,
                    route.from_station,
                    route.to_station,
                    route.group,
                    "启用" if route.enabled else "停用",
                ),
            )

        enabled_count = len([route for route in self.routes if route.enabled])
        self.route_count_var.set(f"线路数：{len(self.routes)}（启用 {enabled_count}）")

    def add_route(self) -> None:
        train_date = self.route_date_var.get().strip()
        from_station = self.route_from_var.get().strip()
        to_station = self.route_to_var.get().strip()
        group = self.route_group_var.get().strip() or "默认"
        enabled = self.route_enabled_var.get()

        if not train_date or not from_station or not to_station:
            messagebox.showwarning("提示", "请填写完整的日期、出发站和到达站")
            return

        if not self._valid_date(train_date):
            messagebox.showwarning("提示", "日期格式应为 YYYY-MM-DD")
            return

        route = RouteItem(
            train_date=train_date,
            from_station=from_station,
            to_station=to_station,
            group=group,
            enabled=enabled,
        )
        if route.key() in {item.key() for item in self.routes}:
            messagebox.showinfo("提示", "该线路已存在")
            return

        self.routes.append(route)
        self.batch_group_var.set(group)
        self._refresh_route_tree()

    def remove_selected_routes(self) -> None:
        selected = self.route_tree.selection()
        if not selected:
            return

        selected_keys = set(selected)
        self.routes = [route for route in self.routes if route.key() not in selected_keys]
        self._refresh_route_tree()

    def enable_selected_routes(self) -> None:
        self._set_selected_routes_enabled(True)

    def disable_selected_routes(self) -> None:
        self._set_selected_routes_enabled(False)

    def _set_selected_routes_enabled(self, enabled: bool) -> None:
        selected = set(self.route_tree.selection())
        if not selected:
            messagebox.showinfo("提示", "请先选择要操作的线路")
            return
        changed = 0
        for route in self.routes:
            if route.key() in selected and route.enabled != enabled:
                route.enabled = enabled
                changed += 1
        self._refresh_route_tree()
        self.status_var.set(f"已{('启用' if enabled else '停用')}选中线路 {changed} 条")

    def enable_routes_by_group(self) -> None:
        self._set_routes_by_group_enabled(True)

    def disable_routes_by_group(self) -> None:
        self._set_routes_by_group_enabled(False)

    def _set_routes_by_group_enabled(self, enabled: bool) -> None:
        group = self.batch_group_var.get().strip()
        if not group:
            messagebox.showwarning("提示", "请先填写批量分组名")
            return
        changed = 0
        for route in self.routes:
            if route.group == group and route.enabled != enabled:
                route.enabled = enabled
                changed += 1
        self._refresh_route_tree()
        self.status_var.set(f"分组[{group}]已{('启用' if enabled else '停用')} {changed} 条线路")

    def clear_routes(self) -> None:
        if not self.routes:
            return
        if not messagebox.askyesno("确认", "确定清空所有线路吗？"):
            return
        self.routes = []
        self._refresh_route_tree()

    def open_web(self) -> None:
        webbrowser.open("https://kyfw.12306.cn/otn/leftTicket/init")

    def open_selected_transfer_first_leg(self) -> None:
        self._open_selected_transfer_leg(is_first_leg=True)

    def open_selected_transfer_second_leg(self) -> None:
        self._open_selected_transfer_leg(is_first_leg=False)

    def _open_selected_transfer_leg(self, *, is_first_leg: bool) -> None:
        selected = self.transfer_tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请先在中转方案表格中选择一行")
            return
        plan = self.transfer_row_map.get(selected[0])
        if not plan:
            messagebox.showwarning("提示", "未找到选中中转记录，请重新查询后再试")
            return
        from_station = plan.route.from_station if is_first_leg else plan.via_station
        to_station = plan.via_station if is_first_leg else plan.route.to_station
        train_date = plan.route.train_date if is_first_leg else plan.second_depart_at.strftime("%Y-%m-%d")
        try:
            url = self.client.build_left_ticket_url(
                train_date=train_date,
                from_station=from_station,
                to_station=to_station,
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("打开失败", f"无法生成下单链接：{exc}")
            return
        webbrowser.open(url)
        leg_text = "首段" if is_first_leg else "次段"
        self.status_var.set(f"已打开选中中转{leg_text}下单页（需你手动确认下单与支付）")

    def open_selected_order_page(self) -> None:
        selected = self.result_tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请先在结果表格中选择一行")
            return

        item_id = selected[0]
        display = self.result_row_map.get(item_id)
        if not display:
            messagebox.showwarning("提示", "未找到选中记录，请重新查询后再试")
            return

        try:
            url = self.client.build_left_ticket_url(
                train_date=display.route.train_date,
                from_station=display.route.from_station,
                to_station=display.route.to_station,
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("打开失败", f"无法生成下单链接：{exc}")
            return

        webbrowser.open(url)
        self.status_var.set("已打开选中线路下单页（需你手动确认下单与支付）")

    def clear_cookie_text(self) -> None:
        self.cookie_var.set("")
        self.client.clear_cookie_header()
        self.login_state_var.set("登录状态：未检测")
        self.cookie_expire_var.set("Cookie到期：未提供")
        self.status_var.set("已清空Cookie")

    def _update_cookie_expiration_tip(self) -> None:
        raw_cookie = self.cookie_var.get().strip()
        if not raw_cookie:
            self.cookie_expire_var.set("Cookie到期：未提供")
            return
        dt = extract_cookie_expiration(raw_cookie)
        if not dt:
            self.cookie_expire_var.set("Cookie到期：未识别 RAIL_EXPIRATION")
            return
        now = datetime.now()
        remain = dt - now
        if remain.total_seconds() <= 0:
            self.cookie_expire_var.set(f"Cookie到期：已过期（{dt.strftime('%Y-%m-%d %H:%M:%S')}）")
            return
        hours = remain.total_seconds() / 3600
        self.cookie_expire_var.set(
            f"Cookie到期：{dt.strftime('%Y-%m-%d %H:%M:%S')}（剩余约 {hours:.1f} 小时）"
        )

    def _cookie_expire_warning_text(self) -> Optional[str]:
        dt = extract_cookie_expiration(self.cookie_var.get())
        if not dt:
            return None
        remain = dt - datetime.now()
        if remain.total_seconds() <= 0:
            return "检测到 Cookie 已过期，建议重新获取后再监控。"
        if remain.total_seconds() <= 24 * 3600:
            hours = remain.total_seconds() / 3600
            return f"Cookie 将在约 {hours:.1f} 小时后过期，建议提前更新。"
        return None

    def check_login_status(self) -> None:
        cookie = self.cookie_var.get().strip()
        self.client.set_cookie_header(cookie)
        self._update_cookie_expiration_tip()
        ok, message = self.client.check_login_status()
        expire_warn = self._cookie_expire_warning_text()
        if ok:
            self.login_state_var.set(f"登录状态：已登录（{message}）")
            self.status_var.set("12306登录状态检测：已登录")
            if expire_warn:
                messagebox.showwarning("Cookie即将到期", expire_warn)
        else:
            self.login_state_var.set(f"登录状态：未登录/无效（{message}）")
            self.status_var.set("12306登录状态检测：未登录或Cookie无效")
            if expire_warn:
                messagebox.showwarning("Cookie状态提醒", expire_warn)

    def start_monitor(self) -> None:
        if not self._get_routes_for_query():
            messagebox.showwarning("提示", "没有可用线路（请添加并启用至少一条线路）")
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
            messagebox.showwarning("提示", "请先添加并启用至少一条线路，或填写线路输入框后再查询")
            return

        self.querying = True
        self.query_btn.config(state=tk.DISABLED)
        self.status_var.set(f"查询中...（{len(routes)} 条线路）")

        threading.Thread(target=self._query_in_thread, args=(routes,), daemon=True).start()

    def _query_in_thread(self, routes: List[RouteItem]) -> None:
        rows: List[DisplayRow] = []
        errors: List[Tuple[str, str, str]] = []
        transfer_plans: List[TransferPlan] = []
        policy = self._build_retry_policy()
        query_cache: Dict[Tuple[str, str, str], List[TicketRow]] = {}

        for index, route in enumerate(routes):
            route_text = f"[{route.group}] {route.train_date} {route.from_station}->{route.to_station}"
            route_rows = self._query_route_rows_with_cache(
                train_date=route.train_date,
                from_station=route.from_station,
                to_station=route.to_station,
                route_text=route_text,
                retry_policy=policy,
                query_cache=query_cache,
                errors=errors,
            )
            rows.extend([DisplayRow(route=route, row=item) for item in route_rows])
            if self.transfer_enabled_var.get():
                transfer_plans.extend(
                    self._build_transfer_plans_for_route(
                        route=route,
                        retry_policy=policy,
                        query_cache=query_cache,
                        errors=errors,
                    )
                )
            if index < len(routes) - 1:
                time.sleep(random.uniform(0.4, 1.0))

        self.root.after(0, lambda: self.on_query_done(rows, errors, transfer_plans))

    def on_query_done(
        self,
        rows: List[DisplayRow],
        errors: List[Tuple[str, str, str]],
        transfer_plans: List[TransferPlan],
    ) -> None:
        self.querying = False
        self.query_btn.config(state=tk.NORMAL)

        filtered = self._apply_filters(rows)
        self._render_rows(filtered)
        self._render_transfer_plans(transfer_plans)
        ticket_lines, candidate_lines = self._collect_new_alerts(filtered)
        transfer_lines = self._collect_transfer_alerts(transfer_plans)
        all_lines = ticket_lines + candidate_lines + transfer_lines
        if all_lines:
            self.root.bell()
            preview = "\n".join(all_lines[:8])
            if ticket_lines and candidate_lines:
                title = "有票+候补提醒"
            elif ticket_lines:
                title = "有票提醒"
            elif candidate_lines:
                title = "候补提醒"
            else:
                title = "中转提醒"
            messagebox.showinfo(title, f"发现可用车次：\n{preview}\n\n请尽快前往12306官网下单。")
            self._send_notifications_async(all_lines, title=f"12306{title}")

        now = datetime.now().strftime("%H:%M:%S")
        reminder_text = ""
        if ticket_lines or candidate_lines:
            reminder_text = f" | 新提醒 有票{len(ticket_lines)} 候补{len(candidate_lines)}"
        if transfer_lines:
            reminder_text += f" 中转{len(transfer_lines)}"
        if errors:
            self._record_errors(errors)
            short_err = "；".join([f"{item[0]} {item[2]}" for item in errors[:2]])
            self.status_var.set(f"查询完成：{len(filtered)} 条（失败{len(errors)}条，{now}）{short_err}{reminder_text}")
        else:
            self.status_var.set(f"查询完成：{len(filtered)} 条（{now}）{reminder_text}")

        self.schedule_next()

    def _build_retry_policy(self) -> RetryPolicy:
        attempts = max(1, int(self.retry_attempts_var.get()))
        base_delay = max(0.1, float(self.retry_base_delay_var.get()))
        max_delay = max(base_delay, float(self.retry_max_delay_var.get()))
        timeout_sec = max(2.0, float(self.request_timeout_var.get()))
        return RetryPolicy(
            attempts=attempts,
            base_delay_sec=base_delay,
            max_delay_sec=max_delay,
            timeout_sec=timeout_sec,
        )

    def _query_route_rows_with_cache(
        self,
        *,
        train_date: str,
        from_station: str,
        to_station: str,
        route_text: str,
        retry_policy: RetryPolicy,
        query_cache: Dict[Tuple[str, str, str], List[TicketRow]],
        errors: List[Tuple[str, str, str]],
    ) -> List[TicketRow]:
        cache_key = (train_date, from_station, to_station)
        if cache_key in query_cache:
            return query_cache[cache_key]
        try:
            rows = self.client.query(
                train_date=train_date,
                from_station=from_station,
                to_station=to_station,
                retry_policy=retry_policy,
            )
            query_cache[cache_key] = rows
            return rows
        except QueryRequestError as exc:
            errors.append((route_text, exc.category, str(exc)))
        except Exception as exc:  # noqa: BLE001
            errors.append((route_text, "未知", str(exc)))
        query_cache[cache_key] = []
        return []

    def _build_transfer_plans_for_route(
        self,
        *,
        route: RouteItem,
        retry_policy: RetryPolicy,
        query_cache: Dict[Tuple[str, str, str], List[TicketRow]],
        errors: List[Tuple[str, str, str]],
    ) -> List[TransferPlan]:
        min_wait = max(5, int(self.transfer_min_layover_var.get()))
        max_wait = max(min_wait + 5, int(self.transfer_max_layover_var.get()))
        max_plans = max(1, int(self.transfer_max_plans_var.get()))
        hubs = self._resolve_transfer_hubs(route)
        if not hubs:
            return []

        next_day = self._next_date_text(route.train_date)
        if not next_day:
            return []

        plans: List[TransferPlan] = []
        for hub in hubs:
            route_label = f"[{route.group}] {route.train_date} {route.from_station}->{route.to_station} 经 {hub}"
            first_rows = self._query_route_rows_with_cache(
                train_date=route.train_date,
                from_station=route.from_station,
                to_station=hub,
                route_text=f"{route_label}（第一程）",
                retry_policy=retry_policy,
                query_cache=query_cache,
                errors=errors,
            )
            if not first_rows:
                continue
            second_rows_today = self._query_route_rows_with_cache(
                train_date=route.train_date,
                from_station=hub,
                to_station=route.to_station,
                route_text=f"{route_label}（第二程当日）",
                retry_policy=retry_policy,
                query_cache=query_cache,
                errors=errors,
            )
            second_rows_next = self._query_route_rows_with_cache(
                train_date=next_day,
                from_station=hub,
                to_station=route.to_station,
                route_text=f"{route_label}（第二程次日）",
                retry_policy=retry_policy,
                query_cache=query_cache,
                errors=errors,
            )
            if not second_rows_today and not second_rows_next:
                continue
            for first_row in first_rows:
                if not self._row_passes_seat_filter(first_row):
                    continue
                depart_1 = self._to_datetime(route.train_date, first_row.start_time)
                duration_1 = self._duration_minutes(first_row.duration)
                if depart_1 is None or duration_1 is None:
                    continue
                arrive_1 = depart_1 + timedelta(minutes=duration_1)
                plans.extend(
                    self._build_transfer_plans_with_second_rows(
                        route=route,
                        via_station=hub,
                        first_row=first_row,
                        first_depart_at=depart_1,
                        first_arrive_at=arrive_1,
                        second_rows=second_rows_today,
                        second_train_date=route.train_date,
                        min_wait=min_wait,
                        max_wait=max_wait,
                    )
                )
                plans.extend(
                    self._build_transfer_plans_with_second_rows(
                        route=route,
                        via_station=hub,
                        first_row=first_row,
                        first_depart_at=depart_1,
                        first_arrive_at=arrive_1,
                        second_rows=second_rows_next,
                        second_train_date=next_day,
                        min_wait=min_wait,
                        max_wait=max_wait,
                    )
                )

        plans = self._dedupe_transfer_plans(plans)
        plans.sort(key=lambda item: (-item.seat_score, item.total_minutes, item.wait_minutes))
        return plans[:max_plans]

    def _build_transfer_plans_with_second_rows(
        self,
        *,
        route: RouteItem,
        via_station: str,
        first_row: TicketRow,
        first_depart_at: datetime,
        first_arrive_at: datetime,
        second_rows: List[TicketRow],
        second_train_date: str,
        min_wait: int,
        max_wait: int,
    ) -> List[TransferPlan]:
        plans: List[TransferPlan] = []
        for second_row in second_rows:
            if not self._row_passes_seat_filter(second_row):
                continue
            depart_2 = self._to_datetime(second_train_date, second_row.start_time)
            duration_2 = self._duration_minutes(second_row.duration)
            if depart_2 is None or duration_2 is None:
                continue
            if depart_2 <= first_arrive_at:
                continue
            wait_minutes = int((depart_2 - first_arrive_at).total_seconds() / 60)
            if wait_minutes < min_wait or wait_minutes > max_wait:
                continue
            arrive_2 = depart_2 + timedelta(minutes=duration_2)
            total_minutes = int((arrive_2 - first_depart_at).total_seconds() / 60)
            seat_hint, seat_score = self._build_transfer_seat_hint(first_row, second_row)
            plans.append(
                TransferPlan(
                    route=route,
                    via_station=via_station,
                    first_row=first_row,
                    second_row=second_row,
                    first_depart_at=first_depart_at,
                    first_arrive_at=first_arrive_at,
                    second_depart_at=depart_2,
                    second_arrive_at=arrive_2,
                    wait_minutes=wait_minutes,
                    total_minutes=total_minutes,
                    seat_hint=seat_hint,
                    seat_score=seat_score,
                )
            )
        return plans

    def _resolve_transfer_hubs(self, route: RouteItem) -> List[str]:
        text = self.transfer_hubs_var.get().strip()
        if not text:
            raw_list = list(DEFAULT_TRANSFER_HUBS)
        else:
            raw_list = [item.strip() for item in re.split(r"[，,\s]+", text) if item.strip()]
        out: List[str] = []
        seen = set()
        for station in raw_list:
            if station in {route.from_station, route.to_station}:
                continue
            if station in seen:
                continue
            seen.add(station)
            out.append(station)
            if len(out) >= 8:
                break
        return out

    def _next_date_text(self, train_date: str) -> Optional[str]:
        try:
            dt = datetime.strptime(train_date, "%Y-%m-%d")
        except ValueError:
            return None
        return (dt + timedelta(days=1)).strftime("%Y-%m-%d")

    def _to_datetime(self, train_date: str, hhmm: str) -> Optional[datetime]:
        text = (hhmm or "").strip()
        try:
            return datetime.strptime(f"{train_date} {text}", "%Y-%m-%d %H:%M")
        except ValueError:
            return None

    def _duration_minutes(self, duration_text: str) -> Optional[int]:
        text = (duration_text or "").strip()
        if not text:
            return None
        match = re.match(r"^(\d{1,3}):(\d{2})$", text)
        if not match:
            return None
        hours = int(match.group(1))
        minutes = int(match.group(2))
        return hours * 60 + minutes

    def _row_passes_seat_filter(self, row: TicketRow) -> bool:
        seat = self.seat_var.get()
        if seat != "任意":
            return has_ticket(row.seats.get(seat, "--"))
        for seat_name in SEAT_OPTIONS:
            if seat_name == "任意":
                continue
            if has_ticket(row.seats.get(seat_name, "--")):
                return True
        return False

    def _seat_value_score(self, value: str) -> int:
        text = (value or "").strip()
        if not text or text in {"--", "无", "*"}:
            return 0
        if "候补" in text:
            return 8
        if text == "有":
            return 60
        if text.isdigit():
            return min(80, 30 + int(text))
        return 20

    def _build_transfer_seat_hint(self, first_row: TicketRow, second_row: TicketRow) -> Tuple[str, int]:
        seat = self.seat_var.get()
        if seat != "任意":
            first_v = first_row.seats.get(seat, "--")
            second_v = second_row.seats.get(seat, "--")
            score = self._seat_value_score(first_v) + self._seat_value_score(second_v)
            return f"{seat} {first_v} / {second_v}", score
        best_name = "二等座"
        best_text = "无可用"
        best_score = -1
        for seat_name in SEAT_OPTIONS:
            if seat_name == "任意":
                continue
            first_v = first_row.seats.get(seat_name, "--")
            second_v = second_row.seats.get(seat_name, "--")
            score = self._seat_value_score(first_v) + self._seat_value_score(second_v)
            if score > best_score:
                best_score = score
                best_name = seat_name
                best_text = f"{seat_name} {first_v} / {second_v}"
        return best_text if best_score > 0 else f"{best_name} 无可用", max(0, best_score)

    def _dedupe_transfer_plans(self, plans: List[TransferPlan]) -> List[TransferPlan]:
        unique: List[TransferPlan] = []
        seen = set()
        for plan in plans:
            key = (
                plan.route.key(),
                plan.via_station,
                plan.first_row.train_no,
                plan.second_row.train_no,
                plan.first_depart_at.strftime("%Y-%m-%d %H:%M"),
                plan.second_depart_at.strftime("%Y-%m-%d %H:%M"),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(plan)
        return unique

    def _format_minutes(self, mins: int) -> str:
        hours = mins // 60
        minutes = mins % 60
        return f"{hours}h{minutes:02d}m"

    def _render_transfer_plans(self, plans: List[TransferPlan]) -> None:
        self.transfer_row_map.clear()
        for item in self.transfer_tree.get_children():
            self.transfer_tree.delete(item)
        if not self.transfer_enabled_var.get():
            self.transfer_summary_var.set("智能中转已关闭")
            return
        if not plans:
            self.transfer_summary_var.set("未发现满足条件的中转方案")
            return
        for plan in plans:
            values = (
                plan.route.train_date,
                f"{plan.route.from_station}->{plan.route.to_station}",
                plan.via_station,
                f"{plan.first_row.train_no} {plan.first_row.start_time}-{plan.first_row.arrive_time}",
                f"{plan.second_row.train_no} {plan.second_row.start_time}-{plan.second_row.arrive_time}",
                self._format_minutes(plan.wait_minutes),
                self._format_minutes(plan.total_minutes),
                plan.seat_hint,
            )
            iid = self.transfer_tree.insert("", tk.END, values=values)
            self.transfer_row_map[iid] = plan
        self.transfer_summary_var.set(f"已生成 {len(plans)} 条中转方案")

    def _collect_transfer_alerts(self, plans: List[TransferPlan]) -> List[str]:
        lines: List[str] = []
        for plan in plans:
            key = (
                f"{plan.route.key()}:{plan.via_station}:{plan.first_row.train_no}:{plan.second_row.train_no}:"
                f"{plan.first_depart_at.strftime('%Y%m%d%H%M')}:{plan.second_depart_at.strftime('%Y%m%d%H%M')}"
            )
            if key in self.alerted_keys:
                continue
            self.alerted_keys.add(key)
            lines.append(
                "[中转] "
                f"[{plan.route.train_date} {plan.route.from_station}->{plan.route.to_station}] "
                f"经 {plan.via_station}：{plan.first_row.train_no}->{plan.second_row.train_no}，"
                f"等待{plan.wait_minutes}分，{plan.seat_hint}"
            )
        return lines

    def _record_errors(self, errors: List[Tuple[str, str, str]]) -> None:
        for route_text, category, msg in errors:
            key = category or "未知"
            item = self.error_stats.get(key, {"count": "0", "last": ""})
            count = int(item.get("count", "0")) + 1
            item["count"] = str(count)
            item["last"] = f"{route_text} {msg}"
            self.error_stats[key] = item
        self._render_error_stats()

    def _render_error_stats(self) -> None:
        for iid in self.error_tree.get_children():
            self.error_tree.delete(iid)
        for category in sorted(self.error_stats.keys()):
            item = self.error_stats[category]
            self.error_tree.insert(
                "",
                tk.END,
                values=(category, item.get("count", "0"), item.get("last", "")),
            )

    def clear_error_stats(self) -> None:
        self.error_stats = {}
        self._render_error_stats()
        self.status_var.set("已清空错误分类统计")

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
        self.result_row_map.clear()
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
            item_id = self.result_tree.insert("", tk.END, values=values)
            self.result_row_map[item_id] = item

    def _collect_new_alerts(self, rows: List[DisplayRow]) -> Tuple[List[str], List[str]]:
        seat = self.seat_var.get()
        ticket_lines: List[str] = []
        candidate_lines: List[str] = []

        for item in rows:
            route = item.route
            row = item.row
            seats_to_check = [seat] if seat != "任意" else ["商务座", "一等座", "二等座", "软卧", "硬卧", "硬座", "无座"]
            for seat_name in seats_to_check:
                value = row.seats.get(seat_name, "--")
                if not has_ticket(value):
                    continue
                alert_type = "candidate" if is_candidate_ticket(value) else "ticket"
                key = f"{route.key()}:{row.train_no}:{seat_name}:{alert_type}"
                if key in self.alerted_keys:
                    continue
                self.alerted_keys.add(key)
                content = f"[{route.train_date} {route.from_station}->{route.to_station}] {row.train_no} {seat_name}：{value}"
                if is_candidate_ticket(value):
                    candidate_lines.append(f"[候补] {content}")
                else:
                    ticket_lines.append(f"[有票] {content}")

        return ticket_lines, candidate_lines

    def _get_routes_for_query(self) -> List[RouteItem]:
        if self.routes:
            return [route for route in self.routes if route.enabled]

        train_date = self.route_date_var.get().strip()
        from_station = self.route_from_var.get().strip()
        to_station = self.route_to_var.get().strip()
        if train_date and from_station and to_station and self._valid_date(train_date):
            return [
                RouteItem(
                    train_date=train_date,
                    from_station=from_station,
                    to_station=to_station,
                    group=self.route_group_var.get().strip() or "默认",
                    enabled=True,
                )
            ]
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
        min_layover = max(5, int(self.transfer_min_layover_var.get()))
        max_layover = max(min_layover + 5, int(self.transfer_max_layover_var.get()))
        return AppSettings(
            interval_sec=max(2, int(self.interval_var.get())),
            train_filter=self.train_filter_var.get().strip(),
            seat_filter=self.seat_var.get().strip(),
            routes=list(self.routes),
            retry_attempts=max(1, int(self.retry_attempts_var.get())),
            retry_base_delay_sec=max(0.1, float(self.retry_base_delay_var.get())),
            retry_max_delay_sec=max(0.2, float(self.retry_max_delay_var.get())),
            request_timeout_sec=max(2.0, float(self.request_timeout_var.get())),
            transfer_enabled=self.transfer_enabled_var.get(),
            transfer_hubs=self.transfer_hubs_var.get().strip(),
            transfer_min_layover_min=min_layover,
            transfer_max_layover_min=max_layover,
            transfer_max_plans=max(1, int(self.transfer_max_plans_var.get())),
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
