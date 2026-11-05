"""巡检调度：并发执行 Ping 与 SSH 采集，汇总为设备结果。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from .config import Config, Device
from .parsers import DeviceFacts
from .ping import PingResult, ping_host
from .ssh import DeviceSession, SshError

log = logging.getLogger(__name__)


@dataclass
class DeviceResult:
    ip: str
    name: str
    ping: PingResult
    ssh_attempted: bool = False
    ssh_ok: bool = False
    facts: DeviceFacts | None = None
    error: str = ""

    @property
    def healthy(self) -> bool:
        return self.ping.reachable and (self.ssh_ok or not self.ssh_attempted)


def inspect_device(device: Device, config: Config) -> DeviceResult:
    """Ping + 可选 SSH 采集单个设备。任何单设备失败都不会抛出。"""

    ping = ping_host(device.ip, config.ping.count, config.ping.timeout_ms)
    result = DeviceResult(device.ip, device.name, ping)
    if not ping.reachable:
        result.error = ping.error or "网络不可达"
        return result
    if not device.ssh:
        return result

    result.ssh_attempted = True
    settings = device.ssh_settings(config.ssh)
    last_error = ""
    for attempt in range(settings.retries + 1):
        if attempt:
            log.debug("%s 第 %d 次重试 SSH", device.ip, attempt + 1)
        session = DeviceSession(settings)
        try:
            session.connect(device.ip)
            result.facts = session.collect(device.vendor)
            result.ssh_ok = True
            result.error = ""
            return result
        except SshError as exc:
            last_error = str(exc)
            log.debug("%s SSH 采集失败: %s", device.ip, last_error)
        finally:
            session.close()

    result.error = f"SSH 采集失败: {last_error}" if last_error else "SSH 采集失败"
    return result


def inspect_many(
    devices: list[Device] | tuple[Device, ...],
    config: Config,
    progress: Callable[[DeviceResult], None] | None = None,
) -> list[DeviceResult]:
    """并发巡检，返回顺序与输入设备一致。"""

    ordered: list[DeviceResult | None] = [None] * len(devices)
    workers = max(1, min(config.ping.workers, len(devices) or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(inspect_device, device, config): index
            for index, device in enumerate(devices)
        }
        for future in as_completed(futures):
            index = futures[future]
            device = devices[index]
            try:
                result = future.result()
            except Exception as exc:  # 巡检边界兜底，保证整批继续
                log.exception("巡检 %s 时发生未预期错误", device.ip)
                result = DeviceResult(
                    device.ip,
                    device.name,
                    PingResult(device.ip, False, error=f"巡检异常: {exc}"),
                )
            ordered[index] = result
            if progress is not None:
                progress(result)
    return [item for item in ordered if item is not None]
