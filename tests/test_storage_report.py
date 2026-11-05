from datetime import datetime
from pathlib import Path

from netinspect.collector import DeviceResult
from netinspect.parsers import DeviceFacts
from netinspect.ping import PingResult
from netinspect.report import console_summary, render_html, write_report
from netinspect.storage import Store


def _results() -> list[DeviceResult]:
    healthy = DeviceResult(
        "10.0.0.1",
        "core",
        PingResult("10.0.0.1", True, latency_ms=2.5, ttl=64, loss_pct=0.0),
        ssh_attempted=True,
        ssh_ok=True,
        facts=DeviceFacts(
            hostname="CoreSW",
            vendor="huawei",
            model="S5735-L24T4S-A1",
            os_version="5.170",
            uptime="12 weeks",
            cpu_pct=7.0,
            mem_pct=32.0,
            interfaces_up=3,
            interfaces_down=1,
            raw={"version": "Huawei VRP"},
        ),
    )
    offline = DeviceResult(
        "10.0.0.2",
        "offline",
        PingResult("10.0.0.2", False, loss_pct=100.0, error="请求超时"),
    )
    return [healthy, offline]


def test_store_roundtrip_and_report(tmp_path: Path) -> None:
    store = Store(f"sqlite:///{(tmp_path / 'inspect.db').as_posix()}")
    try:
        store.init_schema()
        started = datetime(2026, 11, 5, 10, 0, 0)
        finished = datetime(2026, 11, 5, 10, 0, 5)
        run_id = store.save_run(started, finished, _results())
        run, rows = store.fetch_run(run_id)
    finally:
        store.close()

    assert run["total"] == 2
    assert run["reachable"] == 1
    assert run["ssh_ok"] == 1
    assert run["failed"] == 1
    assert [row["ip"] for row in rows] == ["10.0.0.1", "10.0.0.2"]
    assert rows[0]["model"] == "S5735-L24T4S-A1"
    assert rows[0]["raw"] == {"version": "Huawei VRP"}
    assert rows[1]["error"] == "请求超时"

    html = render_html(run, rows)
    assert "网络设备巡检报告" in html
    assert "CoreSW" in html
    assert "不可达" in html
    assert "请求超时" in html

    summary = console_summary(run, rows)
    assert "core" in summary
    assert "S5735-L24T4S-A1" in summary
    assert "设备总数: 2" in summary

    target = write_report(tmp_path / "reports" / "report.html", run, rows)
    assert target.exists()
    assert "CoreSW" in target.read_text(encoding="utf-8")


def test_latest_run(tmp_path: Path) -> None:
    store = Store(f"sqlite:///{(tmp_path / 'inspect.db').as_posix()}")
    try:
        store.init_schema()
        first = store.save_run(datetime.now(), datetime.now(), _results())
        second = store.save_run(datetime.now(), datetime.now(), _results())
        assert store.latest_run_id() == second
        run, _ = store.fetch_run()
        assert run["id"] == second
        assert first != second
    finally:
        store.close()
