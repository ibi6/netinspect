import paramiko
import pytest

from demo.fake_device import FakeDevice
from netinspect.collector import inspect_many
from netinspect.config import Config, Device, PingSettings, SshSettings


@pytest.fixture
def devices() -> tuple[list[Device], FakeDevice, FakeDevice]:
    key = paramiko.RSAKey.generate(2048)
    huawei = FakeDevice("huawei", "CoreSW", key)
    cisco = FakeDevice("cisco", "EdgeSW", key)
    huawei.start()
    cisco.start()
    items = [
        Device("127.0.0.1", "CoreSW", "huawei", port=huawei.port),
        Device("127.0.0.1", "EdgeSW", "cisco", port=cisco.port),
        Device("192.0.2.1", "Offline-Site"),
    ]
    try:
        yield items, huawei, cisco
    finally:
        huawei.stop()
        cisco.stop()


def test_inspect_many_keeps_order_and_isolates_failures(devices) -> None:
    items, _, _ = devices
    config = Config(
        ping=PingSettings(count=1, timeout_ms=500, workers=4),
        ssh=SshSettings(username="admin", password="demo", timeout=5, command_timeout=5),
    )
    results = inspect_many(items, config)

    assert [item.ip for item in results] == ["127.0.0.1", "127.0.0.1", "192.0.2.1"]
    assert results[0].healthy and results[0].facts is not None
    assert results[0].facts.model == "S5735-L24T4S-A1"
    assert results[1].healthy and results[1].facts is not None
    assert results[1].facts.model == "WS-C2960-24TT-L"
    assert not results[2].healthy
    assert results[2].error
