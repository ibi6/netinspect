"""基于 Paramiko 的交互式 SSH 采集：登录、提示符识别、分页处理与命令执行。"""

from __future__ import annotations

import logging
import re
import time
from typing import Self

import paramiko

from .config import SshSettings
from .parsers import (
    CPU_COMMANDS,
    INTERFACE_COMMANDS,
    MEM_COMMANDS,
    DeviceFacts,
    detect_vendor,
    is_error_output,
    parse_facts,
    version_commands,
)

log = logging.getLogger(__name__)

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
PROMPT_RE = re.compile(r"(?:^|\r?\n)\s*([^\r\n]{1,120}[>#])\s*$")
MORE_RE = re.compile(r"(?:-{2,}\s*More\s*-{2,}|<---\s*More\s*--->)")
RAW_LIMIT = 8000


class SshError(RuntimeError):
    """SSH 连接或采集失败。"""


class DeviceSession:
    """网络设备交互式命令会话。

    网络设备通常只提供交互式 shell，因此这里使用 ``invoke_shell``，
    以设备返回的命令提示符作为每条命令的结束标记。
    """

    def __init__(self, settings: SshSettings) -> None:
        self.settings = settings
        self.prompt = ""
        self._client: paramiko.SSHClient | None = None
        self._channel: paramiko.Channel | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def connect(self, host: str, port: int | None = None) -> None:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        channel: paramiko.Channel | None = None
        try:
            client.connect(
                hostname=host,
                port=port or self.settings.port,
                username=self.settings.username,
                password=self.settings.password,
                timeout=self.settings.timeout,
                banner_timeout=self.settings.timeout,
                auth_timeout=self.settings.timeout,
                look_for_keys=False,
                allow_agent=False,
            )
            channel = client.invoke_shell()
        except (OSError, paramiko.SSHException, EOFError) as exc:
            raise SshError(f"SSH 登录失败: {exc}") from exc
        finally:
            if channel is None:
                client.close()
        channel.settimeout(0.2)
        self._client, self._channel = client, channel
        try:
            channel.send("\n")
            self.prompt = self._read_initial_prompt()
        except SshError:
            self.close()
            raise
        log.debug("已连接 %s，提示符 %r", host, self.prompt)

    def close(self) -> None:
        if self._channel is not None:
            try:
                self._channel.close()
            except (OSError, paramiko.SSHException):
                log.debug("关闭 SSH 通道失败", exc_info=True)
            self._channel = None
        if self._client is not None:
            try:
                self._client.close()
            except (OSError, paramiko.SSHException):
                log.debug("关闭 SSH 客户端失败", exc_info=True)
            self._client = None

    def run(self, command: str, timeout: float | None = None) -> str:
        """执行单条命令并返回去掉回显与提示符的输出。"""

        if self._channel is None:
            raise SshError("SSH 会话未建立")
        self._drain()
        try:
            self._channel.send(command + "\n")
        except (OSError, EOFError) as exc:
            raise SshError(f"发送命令失败: {exc}") from exc
        output = self._read_until_prompt(timeout or self.settings.command_timeout, command)
        return self._clean(output, command)

    def collect(self, vendor_hint: str = "") -> DeviceFacts:
        """采集版本、CPU、内存与接口状态。"""

        self._disable_paging()
        version_text, vendor = self._read_version(vendor_hint)
        raw: dict[str, str] = {"version": _clip(version_text)}
        cpu_text = mem_text = interfaces_text = ""

        if vendor in CPU_COMMANDS:
            cpu_text = self._safe_run(CPU_COMMANDS[vendor])
            raw["cpu"] = _clip(cpu_text)
        if vendor in MEM_COMMANDS:
            mem_text = self._safe_run(MEM_COMMANDS[vendor])
            raw["memory"] = _clip(mem_text)
        if vendor in INTERFACE_COMMANDS:
            interfaces_text = self._safe_run(INTERFACE_COMMANDS[vendor])
            raw["interfaces"] = _clip(interfaces_text)

        facts = parse_facts(
            version_text,
            cpu_text,
            mem_text,
            interfaces_text,
            vendor,
            self.prompt_hostname,
        )
        facts.raw = raw
        return facts

    @property
    def prompt_hostname(self) -> str:
        return re.sub(r"[>#]\s*$", "", self.prompt).strip().strip("<>[]() ")

    def _disable_paging(self) -> None:
        """关闭分页；不同厂商命令不同，失败不影响后续采集。"""

        for command in ("screen-length 0 temporary", "terminal length 0"):
            self._safe_run(command, timeout=5)

    def _read_version(self, hint: str = "") -> tuple[str, str]:
        for command in version_commands(hint):
            text = self._safe_run(command, timeout=15)
            if text and not is_error_output(text):
                return text, detect_vendor(text, hint)
        raise SshError("无法获取设备版本信息")

    def _safe_run(self, command: str, timeout: float | None = None) -> str:
        try:
            return self.run(command, timeout=timeout)
        except SshError as exc:
            log.debug("命令 %s 执行失败: %s", command, exc)
            return ""

    def _drain(self) -> None:
        if self._channel is None:
            return
        while self._channel.recv_ready():
            self._channel.recv(65535)

    def _recv_text(self) -> str:
        assert self._channel is not None
        try:
            data = self._channel.recv(65535)
        except TimeoutError:
            return ""
        except (OSError, EOFError) as exc:
            raise SshError(f"SSH 连接中断: {exc}") from exc
        if not data:
            if not self._channel.active:
                raise SshError("SSH 连接已断开")
            return ""
        return ANSI_RE.sub("", data.decode("utf-8", "replace"))

    def _read_initial_prompt(self) -> str:
        deadline = time.monotonic() + max(self.settings.timeout, 5.0)
        buffer = ""
        while time.monotonic() < deadline:
            buffer += self._recv_text()
            if _handle_more(self._channel, buffer):
                buffer = _strip_more(buffer)
                continue
            match = PROMPT_RE.search(buffer)
            if match:
                return match.group(1).strip()
            if not buffer:
                time.sleep(0.05)
        raise SshError("等待设备命令提示符超时")

    def _read_until_prompt(self, timeout: float, command: str) -> str:
        assert self._channel is not None
        deadline = time.monotonic() + timeout
        buffer = ""
        while True:
            buffer += self._recv_text()
            if _handle_more(self._channel, buffer):
                buffer = _strip_more(buffer)
                continue
            if buffer.rstrip().endswith(self.prompt):
                return buffer
            if time.monotonic() >= deadline:
                raise SshError(f"命令执行超时: {command}")
            time.sleep(0.05)

    def _clean(self, output: str, command: str) -> str:
        lines = output.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        while lines and not lines[-1].strip():
            lines.pop()
        if lines and lines[-1].strip().endswith(self.prompt):
            lines.pop()
        while lines:
            first = lines[0].strip()
            if not first or first == command or first.endswith(command):
                lines.pop(0)
            else:
                break
        return "\n".join(line.rstrip() for line in lines).strip("\n")


def _handle_more(channel: paramiko.Channel | None, buffer: str) -> bool:
    if channel is None:
        return False
    if MORE_RE.search(buffer[-400:]):
        channel.send(" ")
        return True
    return False


def _strip_more(buffer: str) -> str:
    return MORE_RE.sub("", buffer).replace("--More--", "")


def _clip(text: str) -> str:
    return text[:RAW_LIMIT]
