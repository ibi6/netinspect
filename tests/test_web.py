import time
from pathlib import Path

import paramiko
import pytest

from demo.fake_device import FakeDevice
from netinspect.config import Config, PingSettings, SshSettings
from netinspect.web import create_app


def _config(tmp_path: Path) -> Config:
    return Config(
        database_url=f"sqlite:///{(tmp_path / 'web.db').as_posix()}",
        ping=PingSettings(count=1, timeout_ms=500, workers=4),
        ssh=SshSettings(
            username="admin",
            password="demo",
            timeout=5,
            command_timeout=5,
        ),
    )


@pytest.fixture
def demo_hosts():
    key = paramiko.RSAKey.generate(2048)
    huawei = FakeDevice("huawei", "CoreSW", key)
    cisco = FakeDevice("cisco", "EdgeSW", key)
    huawei.start()
    cisco.start()
    try:
        yield huawei, cisco
    finally:
        huawei.stop()
        cisco.stop()


def test_dashboard_pages_and_empty_overview(tmp_path: Path) -> None:
    app = create_app(
        _config(tmp_path),
        report_dir=tmp_path / "reports",
        inventory_path=tmp_path / "devices.yaml",
    )
    client = app.test_client()

    page = client.get("/")
    assert page.status_code == 200
    assert "网络巡检中心" in page.get_data(as_text=True)

    overview = client.get("/api/overview").get_json()
    assert overview["latest"] is None
    assert overview["live"]["status"] == "idle"

    devices = client.get("/api/devices").get_json()
    assert devices["config"] == []
    assert isinstance(devices["demo"], list)


def test_web_inspection_flow(tmp_path: Path, demo_hosts) -> None:
    huawei, cisco = demo_hosts
    app = create_app(
        _config(tmp_path),
        report_dir=tmp_path / "reports",
        inventory_path=tmp_path / "devices.yaml",
    )
    client = app.test_client()

    device_text = (
        f"127.0.0.1,CoreSW,huawei,{huawei.port}\n"
        f"127.0.0.1,EdgeSW,cisco,{cisco.port}\n"
    )
    response = client.post(
        "/api/inspect",
        json={"source": "custom", "devices": device_text, "ssh": True},
    )
    assert response.status_code == 202

    live = client.get("/api/inspect/status").get_json()
    deadline = time.time() + 30
    while live["status"] == "running" and time.time() < deadline:
        time.sleep(0.05)
        live = client.get("/api/inspect/status").get_json()

    assert live["status"] == "done", live
    assert live["completed"] == 2
    assert {item["vendor"] for item in live["results"]} == {"huawei", "cisco"}

    overview = client.get("/api/overview").get_json()
    latest = overview["latest"]
    assert latest["stats"] == {"total": 2, "reachable": 2, "ssh_ok": 2, "failed": 0}
    assert [row["ip"] for row in latest["rows"]] == ["127.0.0.1", "127.0.0.1"]

    report = client.get(latest["report_url"])
    assert report.status_code == 200
    assert "CoreSW" in report.get_data(as_text=True)

    result_id = latest["rows"][0]["id"]
    detail = client.get(f"/api/result/{result_id}").get_json()
    assert detail["raw"]["version"]

    trends = client.get("/api/trends").get_json()
    assert trends and trends[-1]["id"] == latest["run"]["id"]
    assert trends[-1]["cpu_avg"] is not None

    history = client.get(
        "/api/device/history", query_string={"ip": "127.0.0.1", "name": "CoreSW"}
    ).get_json()
    assert history and history[-1]["run_id"] == latest["run"]["id"]

    csv_export = client.get(f"/api/export/{latest['run']['id']}?format=csv")
    assert csv_export.status_code == 200
    assert "CoreSW" in csv_export.get_data(as_text=True)

    json_export = client.get(f"/api/export/{latest['run']['id']}?format=json")
    assert json_export.status_code == 200
    assert json_export.get_json()["results"][0]["ip"] == "127.0.0.1"

    auto = client.post(
        "/api/auto", json={"enabled": True, "interval_minutes": 5}
    ).get_json()["auto"]
    assert auto["enabled"] is True
    assert auto["next_run"]
    assert client.get("/api/auto").get_json()["enabled"] is True
    client.post("/api/auto", json={"enabled": False})


def test_scan_api(tmp_path: Path) -> None:
    app = create_app(
        _config(tmp_path),
        report_dir=tmp_path / "reports",
        inventory_path=tmp_path / "devices.yaml",
    )
    client = app.test_client()

    response = client.post(
        "/api/scan", json={"targets": "127.0.0.1", "timeout_ms": 500}
    )
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["alive"] == 1
    assert payload["results"][0]["reachable"] is True


def test_device_management_and_managed_run(tmp_path: Path, demo_hosts) -> None:
    huawei, _ = demo_hosts
    inventory = tmp_path / "devices.yaml"
    app = create_app(
        _config(tmp_path),
        report_dir=tmp_path / "reports",
        inventory_path=inventory,
    )
    client = app.test_client()

    payload = {
        "ip": "127.0.0.1",
        "name": "CoreSW-real",
        "vendor": "huawei",
        "port": huawei.port,
        "username": "admin",
        "password": "demo",
        "ssh": True,
    }
    created = client.post("/api/devices", json=payload)
    assert created.status_code == 201
    device = created.get_json()["device"]
    assert device["managed"] is True
    assert "password" not in device

    devices = client.get("/api/devices").get_json()
    assert len(devices["real"]) == 1
    assert devices["real"][0]["name"] == "CoreSW-real"

    duplicate = client.post("/api/devices", json=payload)
    assert duplicate.status_code == 400

    updated = client.put(
        f"/api/devices/{device['id']}", json={**payload, "name": "CoreSW-edited"}
    )
    assert updated.status_code == 200
    assert updated.get_json()["device"]["name"] == "CoreSW-edited"

    # devices.yaml 中的设备可以直接用于巡检（不传 --devices）
    response = client.post("/api/inspect", json={"source": "config", "ssh": True})
    assert response.status_code == 202
    live = client.get("/api/inspect/status").get_json()
    deadline = time.time() + 30
    while live["status"] == "running" and time.time() < deadline:
        time.sleep(0.05)
        live = client.get("/api/inspect/status").get_json()
    assert live["status"] == "done", live
    assert live["results"][0]["vendor"] == "huawei"
    assert live["results"][0]["ssh_ok"] is True

    removed = client.delete(f"/api/devices/{device['id']}")
    assert removed.status_code == 200
    assert client.get("/api/devices").get_json()["real"] == []
