"""配置加载：数据库、Ping、SSH 默认值以及设备清单。"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

DEFAULT_DB_URL = "mysql+pymysql://root@127.0.0.1:3306/net_inspect?charset=utf8mb4"


class ConfigError(Exception):
    """配置文件或设备清单不合法。"""


@dataclass(frozen=True)
class SshSettings:
    port: int = 22
    username: str = ""
    password: str = ""
    timeout: float = 10.0
    command_timeout: float = 8.0
    retries: int = 1


@dataclass(frozen=True)
class PingSettings:
    count: int = 3
    timeout_ms: int = 1000
    workers: int = 32


@dataclass(frozen=True)
class Device:
    ip: str
    name: str = ""
    vendor: str = ""
    port: int | None = None
    username: str | None = None
    password: str | None = None
    ssh: bool = True

    def ssh_settings(self, defaults: SshSettings) -> SshSettings:
        return replace(
            defaults,
            port=self.port or defaults.port,
            username=self.username if self.username is not None else defaults.username,
            password=self.password if self.password is not None else defaults.password,
        )


@dataclass(frozen=True)
class Config:
    database_url: str = DEFAULT_DB_URL
    db_echo: bool = False
    ping: PingSettings = PingSettings()
    ssh: SshSettings = SshSettings()
    devices: tuple[Device, ...] = ()


def _positive_int(value: object, field: str) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field} 必须是整数") from exc
    if number <= 0:
        raise ConfigError(f"{field} 必须大于 0")
    return number


def _non_negative_int(value: object, field: str) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field} 必须是整数") from exc
    if number < 0:
        raise ConfigError(f"{field} 不能为负数")
    return number


def _positive_float(value: object, field: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field} 必须是数字") from exc
    if number <= 0:
        raise ConfigError(f"{field} 必须大于 0")
    return number


def parse_devices(text: str) -> list[Device]:
    """解析设备清单。

    每行格式：``IP[,名称[,厂商[,端口]]]``，也支持空格分隔；``#`` 之后为注释。
    名称、厂商、端口均可省略，缺省值由全局 SSH 配置补齐。
    """

    devices: list[Device] = []
    seen: set[tuple[str, int]] = set()
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [p.strip() for p in (line.split(",") if "," in line else line.split())]
        ip = parts[0]
        try:
            ipaddress.ip_address(ip)
        except ValueError as exc:
            raise ConfigError(f"第 {lineno} 行 IP 地址无效: {ip!r}") from exc
        name = parts[1] if len(parts) > 1 else ""
        vendor = (parts[2] if len(parts) > 2 else "").lower()
        port: int | None = None
        if len(parts) > 3 and parts[3]:
            port = _positive_int(parts[3], f"第 {lineno} 行端口")
            if port > 65535:
                raise ConfigError(f"第 {lineno} 行端口超出范围: {port}")
        key = (ip, port or 0)
        if key in seen:
            raise ConfigError(f"第 {lineno} 行设备重复: {ip}" + (f":{port}" if port else ""))
        seen.add(key)
        devices.append(Device(ip=ip, name=name, vendor=vendor, port=port))
    return devices


def load_config(path: str | Path = "config.yaml", *, missing_ok: bool = False) -> Config:
    """读取 YAML 配置，环境变量优先于文件内容。"""

    config_path = Path(path)
    if not config_path.exists():
        if missing_ok:
            return _build_config({})
        raise ConfigError(f"配置文件不存在: {config_path}")
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("配置文件顶层必须是映射")
    return _build_config(data)


def _build_config(data: dict) -> Config:
    db = data.get("database") or {}
    ping = data.get("ping") or {}
    ssh = data.get("ssh") or {}
    if not all(isinstance(section, dict) for section in (db, ping, ssh)):
        raise ConfigError("database/ping/ssh 配置段必须是映射")

    database_url = os.environ.get("NETINSPECT_DB_URL") or db.get("url") or DEFAULT_DB_URL
    ping_settings = PingSettings(
        count=_positive_int(ping.get("count", 3), "ping.count"),
        timeout_ms=_positive_int(ping.get("timeout_ms", 1000), "ping.timeout_ms"),
        workers=_positive_int(ping.get("workers", 32), "ping.workers"),
    )
    ssh_settings = SshSettings(
        port=_positive_int(ssh.get("port", 22), "ssh.port"),
        username=os.environ.get("NETINSPECT_SSH_USER") or str(ssh.get("username") or ""),
        password=os.environ.get("NETINSPECT_SSH_PASSWORD") or str(ssh.get("password") or ""),
        timeout=_positive_float(ssh.get("timeout", 10), "ssh.timeout"),
        command_timeout=_positive_float(ssh.get("command_timeout", 8), "ssh.command_timeout"),
        retries=_non_negative_int(ssh.get("retries", 1), "ssh.retries"),
    )

    raw_devices = data.get("devices") or []
    if not isinstance(raw_devices, list):
        raise ConfigError("devices 必须是列表")
    devices: list[Device] = []
    for index, item in enumerate(raw_devices, start=1):
        if not isinstance(item, dict) or not item.get("ip"):
            raise ConfigError(f"devices 第 {index} 项缺少 ip")
        ip = str(item["ip"])
        try:
            ipaddress.ip_address(ip)
        except ValueError as exc:
            raise ConfigError(f"devices 第 {index} 项 IP 无效: {ip}") from exc
        devices.append(
            Device(
                ip=ip,
                name=str(item.get("name") or ""),
                vendor=str(item.get("vendor") or "").lower(),
                port=_positive_int(item["port"], f"devices 第 {index} 项 port") if item.get("port") else None,
                username=str(item["username"]) if item.get("username") is not None else None,
                password=str(item["password"]) if item.get("password") is not None else None,
                ssh=bool(item.get("ssh", True)),
            )
        )

    return Config(
        database_url=str(database_url),
        db_echo=bool(db.get("echo", False)),
        ping=ping_settings,
        ssh=ssh_settings,
        devices=tuple(devices),
    )
