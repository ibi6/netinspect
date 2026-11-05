"""Ping 连通性检测：跨平台封装、结果解析与批量并发。"""

from __future__ import annotations

import ipaddress
import locale
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"

_LATENCY_RE = re.compile(r"(?:time|时间)[=<]\s*([\d.]+)\s*ms", re.IGNORECASE)
_TTL_RE = re.compile(r"ttl[=:](\d+)", re.IGNORECASE)
_ADDRESS_RE = re.compile(r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s*-\s*(\d{1,3}(?:\.\d{1,3}){3}|\d{1,3})\s*$")
MAX_SCAN_HOSTS = 65536


@dataclass(frozen=True)
class PingResult:
    ip: str
    reachable: bool
    latency_ms: float | None = None
    ttl: int | None = None
    loss_pct: float | None = None
    error: str = ""


def _build_command(ip: str, count: int, timeout_ms: int) -> list[str]:
    if IS_WINDOWS:
        return ["ping", "-n", str(count), "-w", str(timeout_ms), ip]
    if IS_MACOS:
        return ["ping", "-n", "-c", str(count), "-W", str(timeout_ms), ip]
    return [
        "ping",
        "-n",
        "-c",
        str(count),
        "-W",
        str(max(1, round(timeout_ms / 1000))),
        ip,
    ]


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8", locale.getpreferredencoding(False), "gbk"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def ping_host(ip: str, count: int = 3, timeout_ms: int = 1000) -> PingResult:
    """执行一次 Ping，返回可达性、平均延迟、TTL 与丢包率。"""

    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return PingResult(ip, False, error="无效的 IP 地址")

    command = _build_command(ip, count, timeout_ms)
    timeout = count * (timeout_ms / 1000.0) + 5
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            timeout=timeout,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0,
        )
    except FileNotFoundError:
        return PingResult(ip, False, error="系统未找到 ping 命令")
    except subprocess.TimeoutExpired:
        return PingResult(ip, False, loss_pct=100.0, error="Ping 执行超时")
    except OSError as exc:
        return PingResult(ip, False, error=f"Ping 执行失败: {exc}")

    text = _decode(proc.stdout) + _decode(proc.stderr)
    latencies = [float(value) for value in _LATENCY_RE.findall(text)]
    ttls = [int(value) for value in _TTL_RE.findall(text)]
    replies = len(ttls) or len(latencies)
    reachable = proc.returncode == 0 and replies > 0
    loss = max(0.0, (count - replies) / count * 100) if count else None

    if not reachable:
        lowered = text.lower()
        if "无法访问目标主机" in text or "destination host unreachable" in lowered:
            reason = "目标不可达"
        elif "请求超时" in text or "timed out" in lowered or "100% packet loss" in lowered:
            reason = "请求超时"
        else:
            reason = "无响应"
        return PingResult(ip, False, loss_pct=loss, error=reason)

    return PingResult(
        ip=ip,
        reachable=True,
        latency_ms=round(sum(latencies) / len(latencies), 2) if latencies else None,
        ttl=max(ttls) if ttls else None,
        loss_pct=round(loss, 1) if loss is not None else None,
    )


def ping_many(
    targets: list[str], *, count: int = 3, timeout_ms: int = 1000, workers: int = 32
) -> list[PingResult]:
    """并发 Ping，返回顺序与输入一致。"""

    results: list[PingResult | None] = [None] * len(targets)

    def work(index: int, ip: str) -> None:
        try:
            results[index] = ping_host(ip, count, timeout_ms)
        except Exception as exc:  # noqa: BLE001 - 单个目标失败不应中断整批
            results[index] = PingResult(ip, False, error=f"检测异常: {exc}")

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(targets) or 1))) as pool:
        for index, ip in enumerate(targets):
            pool.submit(work, index, ip)
    return [item for item in results if item is not None]


def expand_targets(spec: str) -> list[str]:
    """展开单个 IP、CIDR 或末段范围，如 ``192.168.1.0/24``、``192.168.1.10-20``。"""

    spec = spec.strip()
    if not spec:
        raise ValueError("目标地址为空")

    if "/" in spec:
        try:
            network = ipaddress.ip_network(spec, strict=False)
        except ValueError as exc:
            raise ValueError(f"无效网段: {spec}") from exc
        if network.num_addresses > MAX_SCAN_HOSTS:
            raise ValueError(f"网段过大（超过 {MAX_SCAN_HOSTS} 个地址）: {spec}")
        hosts = list(network.hosts()) if network.num_addresses > 2 else list(network)
        return [str(host) for host in hosts]

    match = _ADDRESS_RE.match(spec)
    if match:
        start = ipaddress.ip_address(match.group(1))
        end_text = match.group(2)
        if "." in end_text:
            end = ipaddress.ip_address(end_text)
        else:
            end = ipaddress.ip_address(int(start) & 0xFFFFFF00 | int(end_text))
        if int(end) < int(start):
            raise ValueError(f"地址范围起点大于终点: {spec}")
        if int(end) - int(start) + 1 > MAX_SCAN_HOSTS:
            raise ValueError(f"地址范围过大（超过 {MAX_SCAN_HOSTS} 个地址）: {spec}")
        return [str(ipaddress.ip_address(value)) for value in range(int(start), int(end) + 1)]

    try:
        return [str(ipaddress.ip_address(spec))]
    except ValueError as exc:
        raise ValueError(f"无效目标: {spec}") from exc

