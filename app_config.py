from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class RouteItem:
    train_date: str
    from_station: str
    to_station: str

    def key(self) -> str:
        return f"{self.train_date}|{self.from_station}|{self.to_station}"


@dataclass
class EmailConfig:
    enabled: bool = False
    smtp_host: str = "smtp.qq.com"
    smtp_port: int = 465
    username: str = ""
    password: str = ""
    from_addr: str = ""
    to_addrs: str = ""
    use_ssl: bool = True


@dataclass
class WeComConfig:
    enabled: bool = False
    webhook: str = ""


@dataclass
class AppSettings:
    interval_sec: int = 5
    train_filter: str = ""
    seat_filter: str = "任意"
    routes: List[RouteItem] = field(default_factory=list)
    email: EmailConfig = field(default_factory=EmailConfig)
    wecom: WeComConfig = field(default_factory=WeComConfig)


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load_settings(path: Path) -> AppSettings:
    if not path.exists():
        return AppSettings()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return AppSettings()

    routes_raw = data.get("routes", [])
    routes: List[RouteItem] = []
    for item in routes_raw:
        if not isinstance(item, dict):
            continue
        train_date = str(item.get("train_date", "")).strip()
        from_station = str(item.get("from_station", "")).strip()
        to_station = str(item.get("to_station", "")).strip()
        if train_date and from_station and to_station:
            routes.append(RouteItem(train_date=train_date, from_station=from_station, to_station=to_station))

    email_raw: Dict[str, Any] = data.get("email", {}) if isinstance(data.get("email", {}), dict) else {}
    wecom_raw: Dict[str, Any] = data.get("wecom", {}) if isinstance(data.get("wecom", {}), dict) else {}

    email = EmailConfig(
        enabled=_to_bool(email_raw.get("enabled"), False),
        smtp_host=str(email_raw.get("smtp_host", "smtp.qq.com")),
        smtp_port=_to_int(email_raw.get("smtp_port"), 465),
        username=str(email_raw.get("username", "")),
        password=str(email_raw.get("password", "")),
        from_addr=str(email_raw.get("from_addr", "")),
        to_addrs=str(email_raw.get("to_addrs", "")),
        use_ssl=_to_bool(email_raw.get("use_ssl"), True),
    )

    wecom = WeComConfig(
        enabled=_to_bool(wecom_raw.get("enabled"), False),
        webhook=str(wecom_raw.get("webhook", "")),
    )

    return AppSettings(
        interval_sec=max(2, _to_int(data.get("interval_sec"), 5)),
        train_filter=str(data.get("train_filter", "")),
        seat_filter=str(data.get("seat_filter", "任意")),
        routes=routes,
        email=email,
        wecom=wecom,
    )


def save_settings(path: Path, settings: AppSettings) -> None:
    payload = asdict(settings)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
