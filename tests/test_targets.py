import pytest

from netinspect.ping import expand_targets


def test_single_address() -> None:
    assert expand_targets("10.0.0.1") == ["10.0.0.1"]


def test_cidr_excludes_network_and_broadcast() -> None:
    assert expand_targets("192.168.1.0/30") == ["192.168.1.1", "192.168.1.2"]


def test_cidr_single_host() -> None:
    assert expand_targets("192.168.1.1/32") == ["192.168.1.1"]


def test_last_octet_range() -> None:
    assert expand_targets("172.16.0.10-12") == ["172.16.0.10", "172.16.0.11", "172.16.0.12"]


def test_full_range() -> None:
    assert expand_targets("10.1.1.1-10.1.1.2") == ["10.1.1.1", "10.1.1.2"]


@pytest.mark.parametrize("spec", ["", "not-an-ip", "10.0.0.0/33", "10.0.0.5-1"])
def test_invalid_targets(spec: str) -> None:
    with pytest.raises(ValueError):
        expand_targets(spec)
