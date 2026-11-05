from netinspect.parsers import (
    detect_vendor,
    is_error_output,
    parse_cpu,
    parse_facts,
    parse_interfaces,
    parse_mem,
    parse_version,
    version_commands,
)

HUAWEI_VERSION = """Huawei Versatile Routing Platform Software
VRP (R) software, Version 5.170 (S5735-L24T4S-A1 V200R019C10SPC500)
Copyright (C) 2012-2020 Huawei Technologies Co., Ltd.
HUAWEI S5735-L24T4S-A1 uptime is 12 weeks, 3 days, 4 hours, 5 minutes
"""

HUAWEI_CPU = """CPU Usage Stat. Cycle: 10 (Second)
CPU Usage            : 7%  Max: 23%
"""

HUAWEI_MEM = "Memory utilization is 32% (used 1048576 KB of 3276800 KB).\n"

HUAWEI_INTERFACES = """Interface                   PHY   Protocol InUti OutUti   inErrors  outErrors
GigabitEthernet0/0/1        up    up        0%     0%          0          0
GigabitEthernet0/0/2        up    up        0%     0%          0          0
GigabitEthernet0/0/3        down  down      0%     0%          0          0
Vlanif1                     up    up        --     --          0          0
"""

CISCO_VERSION = """Cisco IOS Software, C2960 Software (C2960-LANBASEK9-M), Version 15.0(2)SE11, RELEASE SOFTWARE (fc3)
Copyright (c) 1986-2017 by Cisco Systems, Inc.
cisco WS-C2960-24TT-L (PowerPC405) processor (revision B0) with 65536K bytes of memory.
Switch uptime is 2 years, 10 weeks, 3 days, 4 hours, 5 minutes
"""

CISCO_CPU = "CPU utilization for five seconds: 5%/1%; one minute: 4%; five minutes: 3%\n"

CISCO_MEM = """                Head    Total(b)     Used(b)     Free(b)   Lowest(b)  Largest(b)
Processor   84D1C000    62409728    17391704    45018024    44348556    43815808
      I/O   8D800000     7471104      614216     6856888     6856888     6856680
"""

CISCO_INTERFACES = """Interface              IP-Address      OK? Method Status                Protocol
FastEthernet0/1        10.0.0.1        YES manual up                    up
FastEthernet0/2        unassigned      YES unset  down                  down
GigabitEthernet0/1     unassigned      YES unset  administratively down down
Vlan1                  10.0.0.254      YES manual up                    up
"""

H3C_VERSION = """H3C Comware Software, Version 7.1.070, Release 3506P05
Copyright (c) 2004-2020 New H3C Technologies Co., Ltd. All rights reserved.
H3C S5130S-28P-EI uptime is 0 weeks, 1 day, 2 hours, 3 minutes
"""

H3C_MEM = """Memory statistics are measured in KB:
Slot 1:
        Total    Used    Free    Shared   Buffers  Cached
        524288   209715  314573  0        0        0
"""


def test_detect_vendor() -> None:
    assert detect_vendor(HUAWEI_VERSION) == "huawei"
    assert detect_vendor(CISCO_VERSION) == "cisco"
    assert detect_vendor(H3C_VERSION) == "h3c"
    assert detect_vendor("Ruijie Networks RGOS", "cisco") == "ruijie"
    assert detect_vendor("unrecognized output", "huawei") == "huawei"
    assert detect_vendor("unrecognized output") == "unknown"


def test_version_commands_follow_hint() -> None:
    assert version_commands("cisco")[0] == "show version"
    assert version_commands("huawei")[0] == "display version"
    assert version_commands()[0] == "display version"


def test_is_error_output() -> None:
    assert is_error_output("")
    assert is_error_output("% Invalid input detected at '^' marker.")
    assert is_error_output("Error: Unrecognized command found at '^' position.")
    assert not is_error_output(HUAWEI_VERSION)


def test_parse_huawei() -> None:
    assert parse_version(HUAWEI_VERSION, "huawei") == (
        "",
        "S5735-L24T4S-A1",
        "5.170",
        "12 weeks, 3 days, 4 hours, 5 minutes",
    )
    assert parse_cpu(HUAWEI_CPU, "huawei") == 7.0
    assert parse_mem(HUAWEI_MEM, "huawei") == 32.0
    assert parse_interfaces(HUAWEI_INTERFACES, "huawei") == (3, 1)


def test_parse_cisco() -> None:
    assert parse_version(CISCO_VERSION, "cisco") == (
        "",
        "WS-C2960-24TT-L",
        "15.0(2)SE11",
        "2 years, 10 weeks, 3 days, 4 hours, 5 minutes",
    )
    assert parse_cpu(CISCO_CPU, "cisco") == 5.0
    assert parse_mem(CISCO_MEM, "cisco") == 27.9
    assert parse_interfaces(CISCO_INTERFACES, "cisco") == (2, 2)


def test_parse_h3c() -> None:
    assert parse_version(H3C_VERSION, "h3c") == (
        "",
        "S5130S-28P-EI",
        "7.1.070",
        "0 weeks, 1 day, 2 hours, 3 minutes",
    )
    assert parse_mem(H3C_MEM, "h3c") == 40.0


def test_parse_facts_uses_prompt_hostname() -> None:
    facts = parse_facts(
        HUAWEI_VERSION,
        HUAWEI_CPU,
        HUAWEI_MEM,
        HUAWEI_INTERFACES,
        "huawei",
        "CoreSW",
    )
    assert facts.hostname == "CoreSW"
    assert facts.vendor == "huawei"
    assert facts.model == "S5735-L24T4S-A1"
    assert facts.cpu_pct == 7.0
    assert facts.mem_pct == 32.0
    assert (facts.interfaces_up, facts.interfaces_down) == (3, 1)


def test_parse_missing_data_returns_none() -> None:
    facts = parse_facts("", "", "", "", "unknown")
    assert facts.cpu_pct is None
    assert facts.mem_pct is None
    assert facts.interfaces_up is None
    assert facts.hostname == ""
