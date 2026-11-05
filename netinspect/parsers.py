"""多厂商设备输出解析：版本、CPU、内存、接口状态。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 各厂商采集命令；检测到厂商后再选择对应命令。
CPU_COMMANDS: dict[str, str] = {
    "huawei": "display cpu-usage",
    "h3c": "display cpu-usage",
    "cisco": "show processes cpu | include CPU utilization",
    "ruijie": "show cpu",
    "juniper": "show chassis routing-engine",
}

MEM_COMMANDS: dict[str, str] = {
    "huawei": "display memory-usage",
    "h3c": "display memory",
    "cisco": "show memory statistics",
    "ruijie": "show memory",
    "juniper": "show chassis routing-engine",
}

INTERFACE_COMMANDS: dict[str, str] = {
    "huawei": "display interface brief",
    "h3c": "display interface brief",
    "cisco": "show ip interface brief",
    "ruijie": "show ip interface brief",
    "juniper": "show interfaces terse",
}

VERSION_COMMANDS: dict[str, tuple[str, ...]] = {
    "cisco": ("show version", "display version"),
    "ruijie": ("show version", "display version"),
    "juniper": ("show version",),
}
DEFAULT_VERSION_COMMANDS: tuple[str, ...] = ("display version", "show version")

ERROR_MARKERS = (
    "invalid input",
    "unrecognized",
    "incomplete command",
    "ambiguous command",
    "unknown command",
    "wrong parameter",
    "error:",
)

_IFACE_PREFIX = re.compile(
    r"^(?:GigabitEthernet|XGigabitEthernet|FastEthernet|TenGigabitEthernet|"
    r"Ethernet|Eth|GE|XGE|10GE|25GE|40GE|100GE|Serial|LoopBack|Loopback|"
    r"Vlanif|Vlan|MEth|mgmt|Management|M-GigabitEthernet|Port-channel|"
    r"Tunnel|NULL|Virtual-Template)",
    re.IGNORECASE,
)


@dataclass
class DeviceFacts:
    hostname: str = ""
    vendor: str = ""
    model: str = ""
    os_version: str = ""
    uptime: str = ""
    cpu_pct: float | None = None
    mem_pct: float | None = None
    interfaces_up: int | None = None
    interfaces_down: int | None = None
    raw: dict[str, str] = field(default_factory=dict)


def version_commands(vendor_hint: str = "") -> tuple[str, ...]:
    return VERSION_COMMANDS.get(vendor_hint, DEFAULT_VERSION_COMMANDS)


def is_error_output(text: str) -> bool:
    """判断命令输出是否为设备报错（用于在 show/display 之间回退）。"""

    stripped = text.strip()
    if not stripped:
        return True
    lowered = stripped.lower()
    return any(marker in lowered for marker in ERROR_MARKERS)


def detect_vendor(text: str, hint: str = "") -> str:
    lowered = text.lower()
    if "ruijie" in lowered or "rgos" in lowered:
        return "ruijie"
    if "comware" in lowered or "h3c" in lowered:
        return "h3c"
    if "versatile routing" in lowered or "huawei" in lowered:
        return "huawei"
    if "junos" in lowered or "juniper" in lowered:
        return "juniper"
    if "cisco" in lowered:
        return "cisco"
    return hint or "unknown"


def _search(text: str, pattern: str, flags: int = re.MULTILINE | re.IGNORECASE) -> str:
    match = re.search(pattern, text, flags)
    return match.group(1).strip() if match else ""


def parse_version(text: str, vendor: str) -> tuple[str, str, str, str]:
    """返回 (hostname, model, os_version, uptime)。"""

    host = model = os_version = uptime = ""
    if vendor == "huawei":
        model = _search(text, r"\(([A-Za-z0-9][A-Za-z0-9-]*)\s+V\d{3}")
        os_version = _search(text, r"Version\s+([\d.]+)")
        uptime = _search(text, r"uptime is\s+(.+)")
    elif vendor == "h3c":
        model = _search(text, r"^H3C\s+(\S+)\s+uptime is")
        os_version = _search(text, r"Version\s+([\d.]+)")
        uptime = _search(text, r"uptime is\s+(.+)")
    elif vendor == "cisco":
        model = _search(text, r"^cisco\s+(\S+)\s+\(")
        os_version = _search(text, r"Version\s+([^,\s]+)")
        uptime = _search(text, r"uptime is\s+(.+)")
    elif vendor == "ruijie":
        os_version = _search(text, r"(?:RGOS\s+)?Version\s+([\d.()A-Za-z]+)")
        uptime = _search(text, r"uptime is\s+(.+)")
    elif vendor == "juniper":
        host = _search(text, r"^Hostname:\s*(\S+)")
        model = _search(text, r"^Model:\s*(\S+)")
        os_version = _search(text, r"^Junos:\s*(\S+)")
    else:
        os_version = _search(text, r"Version\s+([\d.]+)")
        uptime = _search(text, r"uptime is\s+(.+)")
    return host, model, os_version, uptime


def _percent(used: int, total: int) -> float | None:
    if total <= 0:
        return None
    return round(used / total * 100, 1)


def parse_cpu(text: str, vendor: str) -> float | None:
    if not text.strip():
        return None
    patterns: list[str] = []
    if vendor in ("cisco", "ruijie"):
        patterns.append(r"five seconds:\s*(\d+(?:\.\d+)?)%")
    if vendor == "juniper":
        patterns.append(r"CPU utilization\s*:\s*(\d+(?:\.\d+)?)\s*%")
    patterns.append(r"CPU usage[^\n]*?:\s*(\d+(?:\.\d+)?)%")
    patterns.append(r"(\d+(?:\.\d+)?)%\s+in last 5 seconds")
    patterns.append(r"CPU utilization[^\n]*?:\s*(\d+(?:\.\d+)?)%")
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def _table_memory(text: str) -> tuple[int, int]:
    """解析 H3C ``display memory`` 的 Total/Used 表格。"""

    lines = text.splitlines()
    for index, line in enumerate(lines):
        if re.search(r"\bTotal\b.*\bUsed\b", line, re.IGNORECASE):
            for candidate in lines[index + 1 : index + 6]:
                if re.match(r"^\s*Slot\s", candidate, re.IGNORECASE):
                    continue
                numbers = re.findall(r"\d+", candidate)
                if len(numbers) >= 2:
                    return int(numbers[0]), int(numbers[1])
    return 0, 0


def parse_mem(text: str, vendor: str) -> float | None:
    if not text.strip():
        return None
    if vendor == "cisco":
        match = re.search(
            r"^\s*Processor\s+\S+\s+(\d+)\s+(\d+)\s+(\d+)", text, re.MULTILINE
        )
        if match:
            return _percent(int(match.group(2)), int(match.group(1)))
        match = re.search(r"Processor Pool Total:\s*(\d+)\s+Used:\s*(\d+)", text, re.IGNORECASE)
        if match:
            return _percent(int(match.group(2)), int(match.group(1)))
    match = re.search(
        r"Memory utilization(?:\s+is)?\s*[:=]?\s*(\d+(?:\.\d+)?)\s*%", text, re.IGNORECASE
    )
    if match:
        return float(match.group(1))
    match = re.search(
        r"(?:Mem|Memory):\s*(\d+)\s*K?\s+total,\s*(\d+)\s*K?\s+used", text, re.IGNORECASE
    )
    if match:
        return _percent(int(match.group(2)), int(match.group(1)))
    total, used = _table_memory(text)
    if total:
        return _percent(used, total)
    return None


def parse_interfaces(text: str, vendor: str) -> tuple[int, int] | None:
    """统计物理/逻辑接口的 up/down 数量；无匹配返回 None。"""

    if not text.strip() or vendor == "juniper":
        return None
    up = down = 0
    total = 0
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3 or not _IFACE_PREFIX.match(parts[0]):
            continue
        if vendor == "cisco":
            if len(parts) < 6:
                continue
            status, protocol = parts[4].lower(), parts[5].lower()
        else:
            status, protocol = parts[1].lower().lstrip("*"), parts[2].lower().lstrip("*")
        if status not in ("up", "down", "administratively", "admin"):
            continue
        total += 1
        if status == "up" and protocol == "up":
            up += 1
        else:
            down += 1
    if total == 0:
        return None
    return up, down


def parse_facts(
    version_text: str,
    cpu_text: str,
    mem_text: str,
    interfaces_text: str,
    vendor: str,
    prompt_hostname: str = "",
) -> DeviceFacts:
    vendor = vendor or "unknown"
    host, model, os_version, uptime = parse_version(version_text, vendor)
    facts = DeviceFacts(
        hostname=host or prompt_hostname,
        vendor=vendor,
        model=model,
        os_version=os_version,
        uptime=uptime,
        cpu_pct=parse_cpu(cpu_text, vendor),
        mem_pct=parse_mem(mem_text, vendor),
    )
    counts = parse_interfaces(interfaces_text, vendor)
    if counts is not None:
        facts.interfaces_up, facts.interfaces_down = counts
    return facts
