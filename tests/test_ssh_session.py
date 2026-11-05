import paramiko
import pytest

from demo.fake_device import FakeDevice
from netinspect.config import SshSettings
from netinspect.ssh import DeviceSession, SshError


@pytest.fixture
def host_key() -> paramiko.RSAKey:
    return paramiko.RSAKey.generate(2048)


@pytest.fixture
def fake_huawei(host_key: paramiko.RSAKey):
    device = FakeDevice("huawei", "CoreSW", host_key)
    device.start()
    try:
        yield device.port
    finally:
        device.stop()


@pytest.fixture
def fake_cisco(host_key: paramiko.RSAKey):
    device = FakeDevice("cisco", "EdgeSW", host_key)
    device.start()
    try:
        yield device.port
    finally:
        device.stop()


def _settings(port: int) -> SshSettings:
    return SshSettings(
        port=port,
        username="admin",
        password="demo",
        timeout=5,
        command_timeout=5,
    )


def test_huawei_collection(fake_huawei: int) -> None:
    with DeviceSession(_settings(fake_huawei)) as session:
        session.connect("127.0.0.1")
        assert session.prompt == "<CoreSW>"
        facts = session.collect("huawei")

    assert facts.hostname == "CoreSW"
    assert facts.vendor == "huawei"
    assert facts.model == "S5735-L24T4S-A1"
    assert facts.os_version == "5.170"
    assert facts.cpu_pct == 7.0
    assert facts.mem_pct == 32.0
    assert (facts.interfaces_up, facts.interfaces_down) == (3, 1)
    assert "version" in facts.raw


def test_cisco_collection(fake_cisco: int) -> None:
    with DeviceSession(_settings(fake_cisco)) as session:
        session.connect("127.0.0.1")
        assert session.prompt == "EdgeSW#"
        facts = session.collect("cisco")

    assert facts.hostname == "EdgeSW"
    assert facts.vendor == "cisco"
    assert facts.model == "WS-C2960-24TT-L"
    assert facts.os_version == "15.0(2)SE11"
    assert facts.cpu_pct == 5.0
    assert facts.mem_pct == 27.9
    assert (facts.interfaces_up, facts.interfaces_down) == (2, 2)


def test_login_failure(host_key: paramiko.RSAKey) -> None:
    with pytest.raises(SshError), DeviceSession(_settings(1)) as session:
        session.connect("127.0.0.1")
