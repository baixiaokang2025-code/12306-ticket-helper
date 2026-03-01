from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

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

    def query(
        self,
        train_date: str,
        from_station: str,
        to_station: str,
        purpose_codes: str = "ADULT",
    ) -> List[TicketRow]:
        self.warm_up()
        from_code = self.resolve_station_code(from_station)
        to_code = self.resolve_station_code(to_station)

        params = {
            "leftTicketDTO.train_date": train_date,
            "leftTicketDTO.from_station": from_code,
            "leftTicketDTO.to_station": to_code,
            "purpose_codes": purpose_codes,
        }

        payload = None
        last_error: Optional[Exception] = None
        for _attempt in range(2):
            for endpoint in QUERY_ENDPOINTS:
                try:
                    resp = self.session.get(endpoint, params=params, timeout=10)
                    resp.raise_for_status()
                    text_head = resp.text.lstrip()[:32]
                    if "error.html" in resp.url or text_head.startswith("<!DOCTYPE html"):
                        raise RuntimeError("12306接口返回页面，可能触发了临时风控")
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
            self.warm_up()

        if payload is None:
            if last_error:
                raise RuntimeError(f"查询失败：{last_error}") from last_error
            raise RuntimeError("查询失败：12306接口暂时不可用")

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
