from __future__ import annotations

import re
from dataclasses import dataclass
import random
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlencode

import requests


STATION_JS_URL = "https://kyfw.12306.cn/otn/resources/js/framework/station_name.js"
QUERY_ENDPOINTS = [
    "https://kyfw.12306.cn/otn/leftTicket/queryU",
    "https://kyfw.12306.cn/otn/leftTicket/query",
    "https://kyfw.12306.cn/otn/leftTicket/queryA",
    "https://kyfw.12306.cn/otn/leftTicket/queryZ",
]


@dataclass
class TicketRow:
    train_no: str
    from_station: str
    to_station: str
    start_time: str
    arrive_time: str
    duration: str
    bookable: bool
    seats: Dict[str, str]


@dataclass
class RetryPolicy:
    attempts: int = 3
    base_delay_sec: float = 0.6
    max_delay_sec: float = 4.0
    timeout_sec: float = 10.0


class QueryRequestError(RuntimeError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


class TicketQueryClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/123.0.0.0 Safari/537.36"
                ),
                "Referer": "https://kyfw.12306.cn/otn/leftTicket/init",
            }
        )
        self._name_to_code: Dict[str, str] = {}
        self._code_to_name: Dict[str, str] = {}
        self._warmed_up = False

    def set_cookie_header(self, cookie_text: str) -> None:
        value = (cookie_text or "").strip()
        if value:
            self.session.headers["Cookie"] = value
        else:
            self.clear_cookie_header()

    def clear_cookie_header(self) -> None:
        if "Cookie" in self.session.headers:
            del self.session.headers["Cookie"]

    def warm_up(self) -> None:
        if self._warmed_up:
            return
        resp = self.session.get("https://kyfw.12306.cn/otn/leftTicket/init", timeout=10)
        resp.raise_for_status()
        self._warmed_up = True

    def load_stations(self) -> None:
        if self._name_to_code:
            return
        resp = self.session.get(STATION_JS_URL, timeout=10)
        resp.raise_for_status()

        # Example fragment: @bjb|北京北|VAP|beijingbei|bjb|0
        pairs = re.findall(r"@[^|]*\|([^|]+)\|([A-Z]+)\|", resp.text)
        if not pairs:
            raise RuntimeError("无法解析车站编码，请稍后重试")

        for name, code in pairs:
            self._name_to_code.setdefault(name, code)
            self._name_to_code.setdefault(code, code)
            self._code_to_name.setdefault(code, name)

    def resolve_station_code(self, station: str) -> str:
        self.load_stations()
        station = station.strip()
        if not station:
            raise ValueError("车站不能为空")

        if station in self._name_to_code:
            return self._name_to_code[station]

        # Fuzzy fallback for partial Chinese names.
        candidates = [name for name in self._name_to_code if station in name and len(name) > 1]
        if len(candidates) == 1:
            return self._name_to_code[candidates[0]]

        if candidates:
            hint = "、".join(candidates[:5])
            raise ValueError(f"未找到精确站名“{station}”，你可以试试：{hint}")
        raise ValueError(f"未找到站名“{station}”")

    @staticmethod
    def _seat_value(raw: Optional[str]) -> str:
        if raw is None or raw == "":
            return "--"
        return str(raw)

    @staticmethod
    def classify_error(exc: Exception) -> str:
        text = str(exc)
        low = text.lower()
        if isinstance(exc, requests.Timeout) or "timed out" in low:
            return "超时"
        if isinstance(exc, requests.ConnectionError) or "failed to connect" in low:
            return "网络"
        if "风控" in text or "error.html" in low:
            return "风控"
        if "未找到站名" in text or "未找到精确站名" in text or "车站不能为空" in text:
            return "参数"
        if "json" in low or "接口" in text:
            return "接口"
        return "未知"

    @staticmethod
    def _build_backoff(policy: RetryPolicy, attempt: int) -> float:
        delay = min(policy.max_delay_sec, policy.base_delay_sec * (2 ** attempt))
        return delay + random.uniform(0, max(0.15, policy.base_delay_sec * 0.35))

    def query(
        self,
        train_date: str,
        from_station: str,
        to_station: str,
        purpose_codes: str = "ADULT",
        retry_policy: Optional[RetryPolicy] = None,
    ) -> List[TicketRow]:
        policy = retry_policy or RetryPolicy()
        self.warm_up()
        try:
            from_code = self.resolve_station_code(from_station)
            to_code = self.resolve_station_code(to_station)
        except Exception as exc:  # noqa: BLE001
            category = self.classify_error(exc)
            raise QueryRequestError(category, f"查询失败：{exc}") from exc

        params = {
            "leftTicketDTO.train_date": train_date,
            "leftTicketDTO.from_station": from_code,
            "leftTicketDTO.to_station": to_code,
            "purpose_codes": purpose_codes,
        }

        payload = None
        last_error: Optional[Exception] = None
        for attempt in range(max(1, int(policy.attempts))):
            for endpoint in QUERY_ENDPOINTS:
                try:
                    resp = self.session.get(endpoint, params=params, timeout=policy.timeout_sec)
                    resp.raise_for_status()
                    text_head = resp.text.lstrip()[:32]
                    if "error.html" in resp.url or text_head.startswith("<!DOCTYPE html"):
                        raise QueryRequestError("风控", "12306接口返回页面，可能触发了临时风控")
                    data = resp.json()
                    ok = data.get("status") is True or data.get("httpstatus") == 200
                    if ok and data.get("data") is not None:
                        payload = data["data"]
                        break
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
            if payload is not None:
                break
            # Try refreshing cookies once when API returns HTML/error pages.
            self._warmed_up = False
            try:
                self.warm_up()
            except Exception as exc:  # noqa: BLE001
                last_error = exc
            if attempt < max(1, int(policy.attempts)) - 1:
                time.sleep(self._build_backoff(policy, attempt))

        if payload is None:
            if last_error:
                category = self.classify_error(last_error)
                raise QueryRequestError(category, f"查询失败：{last_error}") from last_error
            raise QueryRequestError("接口", "查询失败：12306接口暂时不可用")

        code_to_name = dict(self._code_to_name)
        code_to_name.update(payload.get("map", {}))

        rows: List[TicketRow] = []
        for item in payload.get("result", []):
            fields = item.split("|")
            if len(fields) < 33:
                continue

            seat_map = {
                "商务座": self._seat_value(fields[32] if len(fields) > 32 else None),
                "一等座": self._seat_value(fields[31] if len(fields) > 31 else None),
                "二等座": self._seat_value(fields[30] if len(fields) > 30 else None),
                "软卧": self._seat_value(fields[23] if len(fields) > 23 else None),
                "硬卧": self._seat_value(fields[28] if len(fields) > 28 else None),
                "硬座": self._seat_value(fields[29] if len(fields) > 29 else None),
                "无座": self._seat_value(fields[26] if len(fields) > 26 else None),
            }

            from_code_i = fields[6] if len(fields) > 6 else ""
            to_code_i = fields[7] if len(fields) > 7 else ""

            rows.append(
                TicketRow(
                    train_no=fields[3],
                    from_station=code_to_name.get(from_code_i, from_station),
                    to_station=code_to_name.get(to_code_i, to_station),
                    start_time=fields[8],
                    arrive_time=fields[9],
                    duration=fields[10],
                    bookable=(fields[11] == "Y" or fields[1] in {"预订", "可预订"}),
                    seats=seat_map,
                )
            )
        return rows

    def build_left_ticket_url(
        self,
        train_date: str,
        from_station: str,
        to_station: str,
        train_no: Optional[str] = None,
    ) -> str:
        from_code = self.resolve_station_code(from_station)
        to_code = self.resolve_station_code(to_station)
        params = {
            "linktypeid": "dc",
            "fs": f"{from_station},{from_code}",
            "ts": f"{to_station},{to_code}",
            "date": train_date,
            "flag": "N,N,Y",
        }
        if train_no:
            params["train_no"] = train_no
        return f"https://kyfw.12306.cn/otn/leftTicket/init?{urlencode(params)}"

    def check_login_status(self) -> Tuple[bool, str]:
        """
        Check whether current session cookie appears to be logged in.
        Note: this only validates cookies in this app session, not browser session.
        """
        self.warm_up()
        url = "https://kyfw.12306.cn/otn/login/checkUser"
        try:
            resp = self.session.post(url, data={"_json_att": ""}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            return False, f"登录状态检测失败：{exc}"

        flag = bool(data.get("data", {}).get("flag"))
        message = data.get("messages") or data.get("validateMessages") or data.get("result_message") or ""
        if isinstance(message, list):
            message = "；".join([str(item) for item in message if item])
        if not message:
            message = "已登录" if flag else "未登录或Cookie无效"
        return flag, str(message)
