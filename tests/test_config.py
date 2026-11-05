from netinspect.config import ConfigError, parse_devices


def test_parse_full_line() -> None:
    devices = parse_devices("192.168.1.1,核心交换机,huawei,2222\n")
    assert len(devices) == 1
    device = devices[0]
    assert (device.ip, device.name, device.vendor, device.port) == (
        "192.168.1.1",
        "核心交换机",
        "huawei",
        2222,
    )


def test_parse_space_separated_and_comments() -> None:
    text = "# comment\n10.0.0.1 core cisco\n\n10.0.0.2\n"
    devices = parse_devices(text)
    assert [d.ip for d in devices] == ["10.0.0.1", "10.0.0.2"]
    assert devices[0].name == "core"
    assert devices[0].vendor == "cisco"
    assert devices[1].name == ""


def test_same_ip_different_ports_allowed() -> None:
    devices = parse_devices("127.0.0.1,a,huawei,2201\n127.0.0.1,b,cisco,2202\n")
    assert len(devices) == 2


def test_duplicate_device_rejected() -> None:
    try:
        parse_devices("10.0.0.1\n10.0.0.1\n")
    except ConfigError as exc:
        assert "重复" in str(exc)
    else:
        raise AssertionError("重复设备未被拒绝")


def test_invalid_ip_rejected() -> None:
    try:
        parse_devices("999.1.1.1\n")
    except ConfigError as exc:
        assert "无效" in str(exc)
    else:
        raise AssertionError("非法 IP 未被拒绝")
