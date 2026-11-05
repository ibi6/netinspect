from netinspect.ping import ping_host


def test_localhost_reachable() -> None:
    result = ping_host("127.0.0.1", count=2, timeout_ms=1000)
    assert result.reachable is True
    assert result.ttl is not None
    assert result.loss_pct == 0.0


def test_invalid_ip() -> None:
    result = ping_host("not-an-ip")
    assert result.reachable is False
    assert "无效" in result.error


def test_unreachable_address() -> None:
    # TEST-NET-1，保留地址，正常网络不可达
    result = ping_host("192.0.2.1", count=1, timeout_ms=300)
    assert result.reachable is False
    assert result.error
