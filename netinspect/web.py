"""Web 仪表盘：实时巡检、概览、历史记录与报告浏览。"""

from __future__ import annotations

import atexit
import copy
import csv
import io
import json
import logging
import re
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template, request
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from .collector import DeviceResult, inspect_many
from .config import Config, ConfigError, Device, parse_devices
from .inventory import Inventory, ManagedDevice
from .ping import expand_targets, ping_many
from .report import build_view, render_html, write_report
from .storage import Store

log = logging.getLogger(__name__)

MAX_UI_SCAN = 1024
DEMO_DEVICES = (
    ("huawei", 22201, "CoreSW"),
    ("cisco", 22202, "EdgeSW"),
)


def _fmt(value: Any) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if isinstance(value, datetime) else str(value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return _fmt(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _pct(value: float | None) -> str:
    return f"{value:.0f}%" if value is not None else "-"


def _count(value: int | None) -> str:
    return str(value) if value is not None else "-"


def result_to_row(result: DeviceResult) -> dict[str, Any]:
    """把单台设备结果转换为表格可直接渲染的行。"""

    facts = result.facts
    if not result.ping.reachable:
        status = "不可达"
    elif result.healthy:
        status = "正常"
    else:
        status = "异常"
    vendor_model = " ".join(
        item for item in (facts.vendor if facts else "", facts.model if facts else "") if item
    ) or "-"
    return {
        "id": None,
        "ip": result.ip,
        "name": result.name,
        "ok": result.healthy,
        "status": status,
        "host": (facts.hostname if facts else "") or "-",
        "vendor_model": vendor_model,
        "os_display": (facts.os_version if facts else "") or "-",
        "uptime_display": (facts.uptime if facts else "") or "-",
        "cpu_display": _pct(facts.cpu_pct if facts else None),
        "mem_display": _pct(facts.mem_pct if facts else None),
        "interfaces_display": (
            f"{_count(facts.interfaces_up)} / {_count(facts.interfaces_down)}" if facts else "-"
        ),
        "latency_display": (
            f"{result.ping.latency_ms:.1f} ms" if result.ping.latency_ms is not None else "-"
        ),
        "loss_display": (
            f"{result.ping.loss_pct:.0f}%" if result.ping.loss_pct is not None else "-"
        ),
        "error_display": result.error or (result.ping.error if not result.ping.reachable else ""),
        "ping_ok": result.ping.reachable,
        "ssh_ok": result.ssh_ok,
        "ssh_attempted": result.ssh_attempted,
        "hostname": facts.hostname if facts else "",
        "vendor": facts.vendor if facts else "",
        "model": facts.model if facts else "",
        "os_version": facts.os_version if facts else "",
        "uptime": facts.uptime if facts else "",
        "cpu_pct": facts.cpu_pct if facts else None,
        "mem_pct": facts.mem_pct if facts else None,
        "interfaces_up": facts.interfaces_up if facts else None,
        "interfaces_down": facts.interfaces_down if facts else None,
        "latency_ms": result.ping.latency_ms,
    }


def _device_dict(device: Device) -> dict[str, Any]:
    return {
        "id": None,
        "ip": device.ip,
        "name": device.name,
        "vendor": device.vendor or "自动识别",
        "port": device.port or 22,
        "username": device.username or "继承全局",
        "ssh": device.ssh,
        "managed": False,
    }


class DashboardService:
    def __init__(
        self,
        config: Config,
        report_dir: Path,
        demo_path: Path,
        inventory: Inventory | None = None,
    ) -> None:
        self.config = config
        self.report_dir = report_dir
        self.demo_path = demo_path
        self.inventory = inventory or Inventory()
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()
        self.live: dict[str, Any] = {
            "status": "idle",
            "started_at": None,
            "finished_at": None,
            "total": 0,
            "completed": 0,
            "results": [],
            "pending": [],
            "error": "",
            "run_id": None,
            "report_url": None,
            "source": "",
            "ssh": True,
        }
        self.auto: dict[str, Any] = {
            "enabled": False,
            "interval_minutes": 15,
            "next_run": None,
            "last_error": "",
            "source": "config" if self.config_devices() else "demo",
        }
        self._scheduler_started = False
        self._store_instance: Store | None = None
        self._store_lock = threading.Lock()

    # ---- 数据访问 -------------------------------------------------
    def _store(self) -> Store:
        """复用引擎与连接池；页面轮询不应为每个请求重建连接池。"""

        if self._store_instance is None:
            with self._store_lock:
                if self._store_instance is None:
                    self._store_instance = Store(
                        self.config.database_url, echo=self.config.db_echo
                    )
        return self._store_instance

    def close(self) -> None:
        with self._store_lock:
            if self._store_instance is not None:
                self._store_instance.close()
                self._store_instance = None

    def init_db(self) -> None:
        self._store().init_schema()

    def db_backend(self) -> str:
        try:
            return make_url(self.config.database_url).get_backend_name()
        except (ValueError, ArgumentError):
            return "unknown"

    def config_devices(self) -> list[Device]:
        """config.yaml 与 devices.yaml 合并后的真实设备。"""

        devices = list(self.config.devices)
        seen = {(item.ip, item.port or 0) for item in devices}
        for device in self.inventory.devices():
            key = (device.ip, device.port or 0)
            if key not in seen:
                devices.append(device)
                seen.add(key)
        return devices

    def demo_devices(self) -> list[Device]:
        try:
            return parse_devices(self.demo_path.read_text(encoding="utf-8"))
        except (OSError, ConfigError):
            return []

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self.live)

    def auto_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self.auto)

    def configure_auto(
        self, enabled: bool, interval_minutes: int, source: str = ""
    ) -> dict[str, Any]:
        interval = max(1, min(int(interval_minutes), 1440))
        if source not in ("config", "demo"):
            source = "config" if self.config_devices() else "demo"
        if source == "config" and not self.config_devices():
            source = "demo" if self.demo_devices() else "config"
        if enabled and not self._resolve_devices(source, ""):
            raise ConfigError("没有可巡检设备，无法开启自动巡检")
        with self._lock:
            self.auto.update(
                enabled=bool(enabled),
                interval_minutes=interval,
                source=source,
                last_error="",
                next_run=_fmt(datetime.now() + timedelta(minutes=interval))
                if enabled
                else None,
            )
            return copy.deepcopy(self.auto)

    def start_scheduler(self) -> None:
        if self._scheduler_started:
            return
        self._scheduler_started = True
        threading.Thread(target=self._scheduler_loop, daemon=True).start()

    def _scheduler_loop(self) -> None:
        while True:
            time.sleep(5)
            auto = self.auto_snapshot()
            if not auto["enabled"] or not auto["next_run"]:
                continue
            try:
                due = datetime.strptime(auto["next_run"], "%Y-%m-%d %H:%M:%S") <= datetime.now()
            except ValueError:
                continue
            if not due:
                continue
            if self.snapshot()["status"] == "running":
                with self._lock:
                    self.auto["next_run"] = _fmt(datetime.now() + timedelta(seconds=20))
                continue
            try:
                self.start(auto["source"] or "config", True)
                with self._lock:
                    self.auto["last_error"] = ""
                    self.auto["next_run"] = _fmt(
                        datetime.now() + timedelta(minutes=int(self.auto["interval_minutes"]))
                    )
            except Exception as exc:  # noqa: BLE001 - 定时任务边界，错误保留给页面
                log.warning("自动巡检启动失败: %s", exc)
                with self._lock:
                    self.auto["last_error"] = str(exc)
                    self.auto["next_run"] = _fmt(datetime.now() + timedelta(minutes=1))

    def overview(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "config": {
                "db": self.db_backend(),
                "devices": len(self.config_devices()),
                "demo_devices": len(self.demo_devices()),
                "ssh_user": self.config.ssh.username or "-",
            },
            "latest": None,
            "history": [],
            "live": self.snapshot(),
            "auto": self.auto_snapshot(),
            "error": "",
        }
        try:
            store = self._store()
            history = store.fetch_history(8)
            try:
                run, rows = store.fetch_run(None)
            except LookupError:
                run, rows = None, []
        except Exception as exc:  # noqa: BLE001 - 数据库故障统一展示在页面上
            payload["error"] = f"数据库不可用: {exc}"
            return payload

        payload["history"] = _jsonable(history)
        if run is not None:
            run_view, row_views, stats = build_view(run, rows)
            for row in row_views:
                row.pop("raw", None)
            payload["latest"] = {
                "run": _jsonable(run_view),
                "rows": _jsonable(row_views),
                "stats": stats,
                "report_url": f"/api/report/{run['id']}",
            }
        return payload

    def devices_payload(self) -> dict[str, Any]:
        config_devices = [_device_dict(device) for device in self.config.devices]
        managed = [Inventory.view(item) for item in self.inventory.load()]
        return {
            "real": config_devices + managed,
            "config": config_devices,
            "managed": managed,
            "demo": [_device_dict(device) for device in self.demo_devices()],
        }

    def add_device(self, payload: dict[str, Any]) -> ManagedDevice:
        self._check_device_conflict(payload)
        return self.inventory.add(payload)

    def update_device(self, device_id: str, payload: dict[str, Any]) -> ManagedDevice:
        self._check_device_conflict(payload, exclude_id=device_id)
        return self.inventory.update(device_id, payload)

    def delete_device(self, device_id: str) -> None:
        self.inventory.delete(device_id)

    def _check_device_conflict(
        self, payload: dict[str, Any], exclude_id: str | None = None
    ) -> None:
        ip = str(payload.get("ip") or "").strip()
        port = str(payload.get("port") or 22)
        for device in self.config.devices:
            if device.ip == ip and str(device.port or 22) == port:
                raise ConfigError(f"设备已存在于 config.yaml: {ip}:{port}")
        for item in self.inventory.load():
            if item.id == exclude_id:
                continue
            if item.ip == ip and str(item.port) == port:
                raise ConfigError(f"设备已存在: {ip}:{port}")

    # ---- 巡检 -----------------------------------------------------
    def start(self, source: str, use_ssh: bool, custom_text: str = "") -> None:
        with self._start_lock:
            if self.snapshot()["status"] == "running":
                raise RuntimeError("已有巡检正在进行")
            devices = self._resolve_devices(source, custom_text)
            if not devices:
                raise ConfigError("没有可巡检设备，请先在 config.yaml 或设备清单中配置")
            config = replace(self.config, devices=tuple(devices))
            if not use_ssh:
                config = replace(
                    config, devices=tuple(replace(d, ssh=False) for d in devices)
                )
            with self._lock:
                self.live = {
                    "status": "running",
                    "started_at": _fmt(datetime.now()),
                    "finished_at": None,
                    "total": len(devices),
                    "completed": 0,
                    "results": [],
                    "pending": [
                        {"ip": device.ip, "name": device.name} for device in devices
                    ],
                    "error": "",
                    "run_id": None,
                    "report_url": None,
                    "source": source,
                    "ssh": use_ssh,
                }
            thread = threading.Thread(target=self._execute, args=(config,), daemon=True)
            thread.start()

    def _resolve_devices(self, source: str, custom_text: str) -> list[Device]:
        if source == "demo":
            return self.demo_devices()
        if source == "custom":
            return parse_devices(custom_text)
        return self.config_devices()

    def _execute(self, config: Config) -> None:
        started = datetime.now()
        try:
            results = inspect_many(list(config.devices), config, progress=self._on_progress)
            finished = datetime.now()
            store = self._store()
            store.init_schema()
            run_id = store.save_run(started, finished, results)
            run, rows = store.fetch_run(run_id)
            self.report_dir.mkdir(parents=True, exist_ok=True)
            target = self.report_dir / f"inspection_{run_id}_{finished:%Y%m%d_%H%M%S}.html"
            write_report(target, run, rows)
            with self._lock:
                self.live.update(
                    status="done",
                    finished_at=_fmt(finished),
                    run_id=run_id,
                    report_url=f"/api/report/{run_id}",
                    report_file=str(target),
                )
        except Exception as exc:  # 后台任务边界，错误要反馈到 UI
            log.exception("Web 巡检执行失败")
            with self._lock:
                self.live.update(
                    status="error",
                    finished_at=_fmt(datetime.now()),
                    error=str(exc),
                )

    def _on_progress(self, result: DeviceResult) -> None:
        row = result_to_row(result)
        with self._lock:
            self.live["results"].append(row)
            self.live["completed"] = len(self.live["results"])
            for index, item in enumerate(self.live["pending"]):
                if item["ip"] == result.ip and item["name"] == result.name:
                    self.live["pending"].pop(index)
                    break

    # ---- 扫描 / 报告 ----------------------------------------------
    def scan(self, raw: str | list[str], timeout_ms: int, count: int) -> list[dict[str, Any]]:
        tokens = raw if isinstance(raw, list) else re.split(r"[\s,]+", raw)
        targets: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            token = str(token).strip()
            if not token:
                continue
            for ip in expand_targets(token):
                if ip not in seen:
                    seen.add(ip)
                    targets.append(ip)
        if not targets:
            raise ValueError("请输入 IP、CIDR 或地址范围")
        if len(targets) > MAX_UI_SCAN:
            raise ValueError(f"单次扫描最多 {MAX_UI_SCAN} 个地址")
        results = ping_many(
            targets,
            count=max(1, min(count, 10)),
            timeout_ms=max(100, min(timeout_ms, 5000)),
            workers=64,
        )
        return [
            {
                "ip": result.ip,
                "reachable": result.reachable,
                "latency": f"{result.latency_ms:.1f} ms" if result.latency_ms is not None else "-",
                "ttl": result.ttl,
                "loss": f"{result.loss_pct:.0f}%" if result.loss_pct is not None else "-",
                "error": result.error,
            }
            for result in results
        ]

    def report(self, run_id: int) -> str:
        run, rows = self._store().fetch_run(run_id)
        return render_html(run, rows)

    def result_detail(self, result_id: int) -> dict[str, Any] | None:
        return self._store().fetch_result(result_id)

    def trends(self, limit: int = 12) -> list[dict[str, Any]]:
        return self._store().fetch_trends(limit)

    def device_history(
        self, ip: str, name: str | None = None, limit: int = 12
    ) -> list[dict[str, Any]]:
        return self._store().fetch_device_history(ip, name, limit)

    def export_run(self, run_id: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return self._store().fetch_run(run_id)


def _start_demo_servers() -> list[Any]:
    try:
        import paramiko

        from demo.fake_device import FakeDevice
    except Exception as exc:  # noqa: BLE001 - 可选演示依赖，缺失时继续启动
        log.warning("无法加载模拟设备: %s", exc)
        return []
    key = paramiko.RSAKey.generate(2048)
    servers: list[Any] = []
    for vendor, port, hostname in DEMO_DEVICES:
        device = FakeDevice(vendor, hostname, key)
        try:
            device.start(port=port)
        except OSError as exc:
            log.warning("模拟设备端口 %s 不可用: %s", port, exc)
        else:
            servers.append(device)
            log.info("模拟 %s 设备: 127.0.0.1:%s", vendor, port)
    return servers


def create_app(
    config: Config,
    *,
    report_dir: str | Path = "reports",
    demo_path: str | Path = "demo/devices_demo.txt",
    inventory_path: str | Path = "devices.yaml",
    demo: bool = False,
) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.json.ensure_ascii = False
    service = DashboardService(
        config, Path(report_dir), Path(demo_path), Inventory(inventory_path)
    )
    atexit.register(service.close)
    app.extensions["dashboard"] = service
    if demo:
        app.extensions["demo_servers"] = _start_demo_servers()
    service.start_scheduler()
    try:
        service.init_db()
    except Exception as exc:  # noqa: BLE001 - 启动阶段尽力初始化，失败不影响页面
        log.warning("数据库初始化失败（页面会展示错误）: %s", exc)

    @app.after_request
    def no_cache(response: Response) -> Response:
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def index() -> str:
        return render_template("dashboard.html")

    @app.get("/api/overview")
    def api_overview():
        return jsonify(service.overview())

    @app.get("/api/devices")
    def api_devices():
        return jsonify(service.devices_payload())

    @app.post("/api/devices")
    def api_add_device():
        try:
            device = service.add_device(request.get_json(silent=True) or {})
        except ConfigError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"device": Inventory.view(device)}), 201

    @app.put("/api/devices/<device_id>")
    def api_update_device(device_id: str):
        try:
            device = service.update_device(
                device_id, request.get_json(silent=True) or {}
            )
        except ConfigError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"device": Inventory.view(device)})

    @app.delete("/api/devices/<device_id>")
    def api_delete_device(device_id: str):
        try:
            service.delete_device(device_id)
        except ConfigError as exc:
            return jsonify({"error": str(exc)}), 404
        return jsonify({"ok": True})

    @app.get("/api/inspect/status")
    def api_status():
        return jsonify(service.snapshot())

    @app.post("/api/inspect")
    def api_inspect():
        data = request.get_json(silent=True) or {}
        try:
            service.start(
                str(data.get("source") or "config"),
                bool(data.get("ssh", True)),
                str(data.get("devices") or ""),
            )
        except ConfigError as exc:
            return jsonify({"error": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify({"ok": True}), 202

    @app.post("/api/scan")
    def api_scan():
        data = request.get_json(silent=True) or {}
        try:
            results = service.scan(
                data.get("targets") or "",
                int(data.get("timeout_ms", 1000)),
                int(data.get("count", 1)),
            )
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(
            {
                "results": results,
                "total": len(results),
                "alive": sum(1 for item in results if item["reachable"]),
            }
        )

    @app.get("/api/report/<int:run_id>")
    def api_report(run_id: int):
        try:
            html = service.report(run_id)
        except LookupError as exc:
            return jsonify({"error": str(exc)}), 404
        return Response(html, mimetype="text/html")

    @app.get("/api/result/<int:result_id>")
    def api_result(result_id: int):
        row = service.result_detail(result_id)
        if row is None:
            return jsonify({"error": "结果不存在"}), 404
        return jsonify(_jsonable(row))

    @app.get("/api/trends")
    def api_trends():
        try:
            limit = int(request.args.get("limit", 12))
        except ValueError:
            limit = 12
        return jsonify(_jsonable(service.trends(max(2, min(limit, 50)))))

    @app.get("/api/device/history")
    def api_device_history():
        ip = request.args.get("ip", "").strip()
        if not ip:
            return jsonify({"error": "缺少 ip 参数"}), 400
        name = request.args.get("name", "").strip() or None
        return jsonify(_jsonable(service.device_history(ip, name)))

    @app.get("/api/auto")
    def api_auto():
        return jsonify(service.auto_snapshot())

    @app.post("/api/auto")
    def api_auto_set():
        data = request.get_json(silent=True) or {}
        try:
            auto = service.configure_auto(
                bool(data.get("enabled", True)),
                int(data.get("interval_minutes", 15)),
                str(data.get("source") or ""),
            )
        except (ConfigError, ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"auto": auto})

    @app.get("/api/export/<int:run_id>")
    def api_export(run_id: int):
        try:
            run, rows = service.export_run(run_id)
        except LookupError as exc:
            return jsonify({"error": str(exc)}), 404
        if request.args.get("format", "json").lower() == "csv":
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow([
                "IP", "名称", "状态", "Ping", "延迟(ms)", "丢包(%)", "SSH",
                "主机名", "厂商", "型号", "系统版本", "运行时长",
                "CPU(%)", "内存(%)", "接口up", "接口down", "错误",
            ])
            for row in rows:
                status = (
                    "不可达"
                    if not row["ping_ok"]
                    else ("正常" if (row["ssh_ok"] or not row["ssh_attempted"]) else "异常")
                )
                writer.writerow([
                    row["ip"], row["name"], status,
                    "可达" if row["ping_ok"] else "不可达",
                    row["latency_ms"] if row["latency_ms"] is not None else "",
                    row["loss_pct"] if row["loss_pct"] is not None else "",
                    "成功" if row["ssh_ok"] else ("未启用" if not row["ssh_attempted"] else "失败"),
                    row["hostname"] or "", row["vendor"] or "", row["model"] or "",
                    row["os_version"] or "", row["uptime"] or "",
                    row["cpu_pct"] if row["cpu_pct"] is not None else "",
                    row["mem_pct"] if row["mem_pct"] is not None else "",
                    row["interfaces_up"] if row["interfaces_up"] is not None else "",
                    row["interfaces_down"] if row["interfaces_down"] is not None else "",
                    row["error"] or "",
                ])
            return Response(
                "\ufeff" + buffer.getvalue(),
                mimetype="text/csv; charset=utf-8",
                headers={
                    "Content-Disposition": f'attachment; filename="inspection_{run_id}.csv"'
                },
            )
        payload = {"run": _jsonable(run), "results": [_jsonable(row) for row in rows]}
        return Response(
            json.dumps(payload, ensure_ascii=False, indent=2),
            mimetype="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="inspection_{run_id}.json"'
            },
        )

    return app
