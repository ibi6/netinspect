"""UI 管理的真实设备清单（默认 devices.yaml）。

config.yaml 里的 static devices 仍然有效；这里存放页面「添加设备」录入的设备，
密码仅保存在本机文件中，接口不会回传。
"""

from __future__ import annotations

import ipaddress
import os
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import ConfigError, Device

VENDORS = ("", "huawei", "h3c", "cisco", "ruijie", "juniper")


@dataclass(frozen=True)
class ManagedDevice:
    id: str
    ip: str
    name: str = ""
    vendor: str = ""
    port: int = 22
    username: str = ""
    password: str = ""
    ssh: bool = True

    def to_device(self) -> Device:
        return Device(
            ip=self.ip,
            name=self.name,
            vendor=self.vendor,
            port=self.port,
            username=self.username or None,
            password=self.password or None,
            ssh=self.ssh,
        )


class Inventory:
    def __init__(self, path: str | Path = "devices.yaml") -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    # ---- 读取 -----------------------------------------------------
    def load(self) -> list[ManagedDevice]:
        with self._lock:
            return self._read_unlocked()

    def devices(self) -> list[Device]:
        return [item.to_device() for item in self.load()]

    def get(self, device_id: str) -> ManagedDevice | None:
        for item in self.load():
            if item.id == device_id:
                return item
        return None

    @staticmethod
    def view(item: ManagedDevice) -> dict[str, Any]:
        return {
            "id": item.id,
            "ip": item.ip,
            "name": item.name,
            "vendor": item.vendor or "自动识别",
            "port": item.port,
            "username": item.username,
            "ssh": item.ssh,
            "managed": True,
        }

    # ---- 写入 -----------------------------------------------------
    def add(self, payload: dict[str, Any]) -> ManagedDevice:
        with self._lock:
            items = self._read_unlocked()
            item = self._from_payload(payload, uuid.uuid4().hex[:8])
            self._ensure_unique(items, item)
            items.append(item)
            self._write_unlocked(items)
            return item

    def update(self, device_id: str, payload: dict[str, Any]) -> ManagedDevice:
        with self._lock:
            items = self._read_unlocked()
            index = next((i for i, item in enumerate(items) if item.id == device_id), -1)
            if index < 0:
                raise ConfigError("设备不存在")
            existing = items[index]
            item = self._from_payload(payload, device_id, existing)
            others = [entry for entry in items if entry.id != device_id]
            self._ensure_unique(others, item)
            items[index] = item
            self._write_unlocked(items)
            return item

    def delete(self, device_id: str) -> None:
        with self._lock:
            items = self._read_unlocked()
            remaining = [item for item in items if item.id != device_id]
            if len(remaining) == len(items):
                raise ConfigError("设备不存在")
            self._write_unlocked(remaining)

    # ---- 内部 -----------------------------------------------------
    def _read_unlocked(self) -> list[ManagedDevice]:
        if not self.path.exists():
            return []
        try:
            data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"设备清单解析失败: {exc}") from exc
        raw_items = data.get("devices") if isinstance(data, dict) else None
        if raw_items is None:
            return []
        if not isinstance(raw_items, list):
            raise ConfigError("devices.yaml 的 devices 必须是列表")
        items: list[ManagedDevice] = []
        for index, raw in enumerate(raw_items, start=1):
            if not isinstance(raw, dict):
                raise ConfigError(f"devices.yaml 第 {index} 项格式错误")
            item = self._from_payload(raw, str(raw.get("id") or uuid.uuid4().hex[:8]))
            items.append(item)
        return items

    @staticmethod
    def _from_payload(
        payload: dict[str, Any],
        device_id: str,
        existing: ManagedDevice | None = None,
    ) -> ManagedDevice:
        ip = str(payload.get("ip") or "").strip()
        try:
            ipaddress.ip_address(ip)
        except ValueError as exc:
            raise ConfigError(f"IP 地址无效: {ip or '空'}") from exc
        try:
            port = int(payload.get("port") or 22)
        except (TypeError, ValueError) as exc:
            raise ConfigError("SSH 端口必须是整数") from exc
        if not 1 <= port <= 65535:
            raise ConfigError("SSH 端口必须在 1-65535 之间")
        vendor = str(payload.get("vendor") or "").lower().strip()
        if vendor not in VENDORS:
            raise ConfigError(f"不支持的厂商: {vendor}")
        password = payload.get("password")
        if password is None or password == "":
            password_value = existing.password if existing else ""
        else:
            password_value = str(password)
        return ManagedDevice(
            id=device_id,
            ip=ip,
            name=str(payload.get("name") or "").strip()[:128],
            vendor=vendor,
            port=port,
            username=str(payload.get("username") or "").strip()[:128],
            password=password_value,
            ssh=bool(payload.get("ssh", True)),
        )

    @staticmethod
    def _ensure_unique(items: list[ManagedDevice], candidate: ManagedDevice) -> None:
        for item in items:
            if item.ip == candidate.ip and item.port == candidate.port:
                raise ConfigError(f"设备已存在: {candidate.ip}:{candidate.port}")

    def _write_unlocked(self, items: list[ManagedDevice]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "devices": [
                {
                    "id": item.id,
                    "ip": item.ip,
                    "name": item.name,
                    "vendor": item.vendor,
                    "port": item.port,
                    "username": item.username,
                    "password": item.password,
                    "ssh": item.ssh,
                }
                for item in items
            ]
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
