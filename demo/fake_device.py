"""本地模拟网络设备：用于无真机时演示/联调 SSH 采集链路。

用法：
    python demo/fake_device.py --vendor huawei --port 22201 --hostname CoreSW
    python demo/fake_device.py --vendor cisco  --port 22202 --hostname EdgeSW

模拟器只实现巡检所需命令，密码校验被省略（任意用户名/密码均可登录）。
"""

from __future__ import annotations

import argparse
import logging
import socket
import threading

import paramiko

log = logging.getLogger(__name__)


class _FakeServer(paramiko.ServerInterface):
    def check_auth_password(self, username: str, password: str) -> int:
        return paramiko.AUTH_SUCCESSFUL

    def check_auth_publickey(self, username: str, key: paramiko.PKey) -> int:
        return paramiko.AUTH_SUCCESSFUL

    def get_allowed_auths(self, username: str) -> str:
        return "password,publickey"

    def check_channel_request(self, kind: str, chanid: int) -> int:
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_shell_request(self, channel: paramiko.Channel) -> bool:
        return True

    def check_channel_pty_request(
        self,
        channel: paramiko.Channel,
        term: str,
        width: int,
        height: int,
        pixelwidth: int,
        pixelheight: int,
        modes: bytes,
    ) -> bool:
        return True


HUAWEI_SAMPLES: dict[str, str] = {
    "display version": """Huawei Versatile Routing Platform Software
VRP (R) software, Version 5.170 (S5735-L24T4S-A1 V200R019C10SPC500)
Copyright (C) 2012-2020 Huawei Technologies Co., Ltd.
HUAWEI S5735-L24T4S-A1 uptime is 12 weeks, 3 days, 4 hours, 5 minutes
""",
    "display cpu-usage": """CPU Usage Stat. Cycle: 10 (Second)
CPU Usage            : 7%  Max: 23%
CPU Usage Stat. Time : 2026-11-05 10:00:00
""",
    "display memory-usage": "Memory utilization is 32% (used 1048576 KB of 3276800 KB).\n",
    "display interface brief": """Interface                   PHY   Protocol InUti OutUti   inErrors  outErrors
GigabitEthernet0/0/1        up    up        0%     0%          0          0
GigabitEthernet0/0/2        up    up        0%     0%          0          0
GigabitEthernet0/0/3        down  down      0%     0%          0          0
Vlanif1                     up    up        --     --          0          0
""",
}

CISCO_SAMPLES: dict[str, str] = {
    "show version": """Cisco IOS Software, C2960 Software (C2960-LANBASEK9-M), Version 15.0(2)SE11, RELEASE SOFTWARE (fc3)
Copyright (c) 1986-2017 by Cisco Systems, Inc.
Compiled Sat 12-Aug-17 02:01 by prod_rel_team

cisco WS-C2960-24TT-L (PowerPC405) processor (revision B0) with 65536K bytes of memory.
Processor board ID FOC1234X5YZ
Switch uptime is 2 years, 10 weeks, 3 days, 4 hours, 5 minutes
""",
    "show processes cpu | include CPU utilization": "CPU utilization for five seconds: 5%/1%; one minute: 4%; five minutes: 3%\n",
    "show memory statistics": """                Head    Total(b)     Used(b)     Free(b)   Lowest(b)  Largest(b)
Processor   84D1C000    62409728    17391704    45018024    44348556    43815808
      I/O   8D800000     7471104      614216     6856888     6856888     6856680
""",
    "show ip interface brief": """Interface              IP-Address      OK? Method Status                Protocol
FastEthernet0/1        10.0.0.1        YES manual up                    up
FastEthernet0/2        unassigned      YES unset  down                  down
GigabitEthernet0/1     unassigned      YES unset  administratively down down
Vlan1                  10.0.0.254      YES manual up                    up
""",
}

VENDORS: dict[str, tuple[dict[str, str], str, str]] = {
    "huawei": (HUAWEI_SAMPLES, "<{hostname}>", "Error: Unrecognized command found at '^' position."),
    "cisco": (CISCO_SAMPLES, "{hostname}#", "% Invalid input detected at '^' marker."),
}


class FakeDevice:
    def __init__(self, vendor: str, hostname: str, host_key: paramiko.PKey) -> None:
        if vendor not in VENDORS:
            raise ValueError(f"不支持的模拟厂商: {vendor}")
        self.vendor = vendor
        self.hostname = hostname
        self.host_key = host_key
        self.samples, self.prompt_template, self.error_text = VENDORS[vendor]
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    @property
    def prompt(self) -> str:
        return self.prompt_template.format(hostname=self.hostname)

    def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen(8)
        self._socket = server
        self.port = server.getsockname()[1]
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        return self.port

    def _accept_loop(self) -> None:
        assert self._socket is not None
        while True:
            try:
                client, _ = self._socket.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client: socket.socket) -> None:
        transport = paramiko.Transport(client)
        transport.add_server_key(self.host_key)
        try:
            transport.start_server(server=_FakeServer())
            channel = transport.accept(20)
            if channel is None:
                return
            channel.send(f"Welcome to simulated {self.vendor} device\r\n{self.prompt}")
            buffer = ""
            while True:
                data = channel.recv(4096)
                if not data:
                    return
                buffer += data.decode("utf-8", "replace")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    command = line.strip()
                    if not command:
                        continue
                    if command in ("exit", "quit"):
                        channel.send("Goodbye\r\n")
                        return
                    output = self.samples.get(command, self.error_text)
                    channel.send(f"{command}\r\n{output}\r\n{self.prompt}")
        except (OSError, paramiko.SSHException):
            return
        finally:
            try:
                transport.close()
            except (OSError, paramiko.SSHException):
                log.debug("关闭模拟设备连接失败", exc_info=True)

    def stop(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None


def main() -> int:
    parser = argparse.ArgumentParser(description="启动一个模拟 SSH 网络设备")
    parser.add_argument("--vendor", choices=sorted(VENDORS), default="huawei")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="监听端口，0 表示随机")
    parser.add_argument("--hostname", default="CoreSW")
    args = parser.parse_args()

    device = FakeDevice(args.vendor, args.hostname, paramiko.RSAKey.generate(2048))
    port = device.start(args.host, args.port)
    print(
        f"模拟 {args.vendor} 设备已启动: {args.host}:{port} "
        f"(提示符 {device.prompt})，Ctrl+C 退出"
    )
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        device.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
