"""命令行入口：run / scan / report / init-db。"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, OperationalError

from .collector import DeviceResult, inspect_many
from .config import Config, ConfigError, load_config, parse_devices
from .inventory import Inventory
from .ping import expand_targets, ping_many
from .report import console_summary, write_report
from .storage import Store


def _verbose_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    return parser


def _common_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="配置文件路径（默认 config.yaml）")
    common.add_argument("--db-url", help="覆盖数据库连接串")
    common.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    return common


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="网络设备自动巡检系统：Ping 连通性 + SSH 信息采集 + MySQL 存储 + 报告",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    common = _common_parser()

    run = subparsers.add_parser("run", parents=[common], help="执行一次巡检")
    run.add_argument("-d", "--devices", help="设备清单文件；指定后覆盖配置中的 devices")
    run.add_argument("-w", "--workers", type=int, help="并发数，默认取 ping.workers")
    run.add_argument("--no-ssh", action="store_true", help="只做 Ping 连通性检测")
    run.add_argument("--report-dir", default="reports", help="报告输出目录")
    run.add_argument("--inventory", default="devices.yaml", help="页面添加设备清单")
    run.set_defaults(func=cmd_run)

    scan = subparsers.add_parser(
        "scan", parents=[_verbose_parser()], help="批量 IP 连通性扫描"
    )
    scan.add_argument("targets", nargs="+", help="IP / CIDR / 范围，如 192.168.1.1、10.0.0.0/24、192.168.1.10-20")
    scan.add_argument("-c", "--count", type=int, default=1, help="每个地址的 Ping 次数，默认 1")
    scan.add_argument("--timeout-ms", type=int, default=1000, help="单次 Ping 超时（毫秒），默认 1000")
    scan.add_argument("-w", "--workers", type=int, default=64, help="并发数，默认 64")
    scan.add_argument("--save", help="把可达地址追加到设备清单文件")
    scan.set_defaults(func=cmd_scan)

    report = subparsers.add_parser("report", parents=[common], help="重新生成历史巡检报告")
    report.add_argument("-r", "--run", default="latest", help="巡检编号或 latest")
    report.add_argument("-o", "--out", help="HTML 输出路径")
    report.set_defaults(func=cmd_report)

    init_db = subparsers.add_parser("init-db", parents=[common], help="创建数据库与表")
    init_db.set_defaults(func=cmd_init_db)

    web = subparsers.add_parser("web", parents=[common], help="启动 Web 巡检仪表盘")
    web.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    web.add_argument("--port", type=int, default=8777, help="监听端口，默认 8777")
    web.add_argument("--report-dir", default="reports", help="报告输出目录")
    web.add_argument("--demo", action="store_true", help="同时启动本机模拟设备（22201/22202）")
    web.add_argument("--inventory", default="devices.yaml", help="页面添加设备清单")
    web.set_defaults(func=cmd_web)

    return parser


def _load_config(args: argparse.Namespace) -> Config:
    config = (
        load_config(args.config)
        if args.config
        else load_config("config.yaml", missing_ok=True)
    )
    if args.db_url:
        config = replace(config, database_url=args.db_url)
    return config


def _store(config: Config) -> Store:
    return Store(config.database_url, echo=config.db_echo)


def _masked_url(url: str) -> str:
    try:
        return make_url(url).render_as_string(hide_password=True)
    except (ValueError, ArgumentError):
        return url


def _progress(result: DeviceResult) -> None:
    if not result.ping.reachable:
        print(f"[不可达] {result.ip} {result.name}".rstrip(), flush=True)
        return
    if result.error:
        print(f"[异常] {result.ip} {result.name} - {result.error}".rstrip(), flush=True)
        return
    details = [f"延迟 {result.ping.latency_ms:.1f}ms" if result.ping.latency_ms is not None else ""]
    if result.facts:
        if result.facts.hostname:
            details.append(result.facts.hostname)
        if result.facts.cpu_pct is not None:
            details.append(f"CPU {result.facts.cpu_pct:.0f}%")
    detail_text = "，".join(item for item in details if item)
    print(f"[正常] {result.ip} {result.name} {detail_text}".rstrip(), flush=True)


def cmd_run(args: argparse.Namespace) -> int:
    try:
        config = _load_config(args)
        if args.devices:
            text = Path(args.devices).read_text(encoding="utf-8")
            config = replace(config, devices=tuple(parse_devices(text)))
    except (ConfigError, OSError) as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2

    if args.workers is not None:
        if args.workers <= 0:
            print("并发数必须大于 0", file=sys.stderr)
            return 2
        config = replace(config, ping=replace(config.ping, workers=args.workers))
    if not args.devices:
        try:
            inventory = Inventory(args.inventory)
            existing = {(device.ip, device.port or 0) for device in config.devices}
            extra = [
                device
                for device in inventory.devices()
                if (device.ip, device.port or 0) not in existing
            ]
            if extra:
                config = replace(config, devices=tuple(list(config.devices) + extra))
        except ConfigError as exc:
            print(f"设备清单错误: {exc}", file=sys.stderr)
            return 2
    if args.no_ssh:
        config = replace(
            config, devices=tuple(replace(device, ssh=False) for device in config.devices)
        )
    if not config.devices:
        print("没有待巡检设备：请在 config.yaml 或 --devices 清单中配置", file=sys.stderr)
        return 2

    print(
        f"开始巡检 {len(config.devices)} 台设备，"
        f"并发 {config.ping.workers}，数据库 {_masked_url(config.database_url)}"
    )
    started_at = datetime.now()
    results = inspect_many(list(config.devices), config, progress=_progress)
    finished_at = datetime.now()

    store = _store(config)
    try:
        store.init_schema()
        run_id = store.save_run(started_at, finished_at, results)
        run, rows = store.fetch_run(run_id)
    except LookupError as exc:
        print(f"读取巡检结果失败: {exc}", file=sys.stderr)
        return 3
    except OperationalError as exc:
        print(f"数据库不可用: {exc}", file=sys.stderr)
        print(
            "请检查 config.yaml 的 database.url，或使用 --db-url "
            "mysql+pymysql://用户:密码@主机:3306/net_inspect",
            file=sys.stderr,
        )
        return 3
    finally:
        store.close()

    report_dir = Path(args.report_dir)
    target = report_dir / f"inspection_{run_id}_{finished_at:%Y%m%d_%H%M%S}.html"
    write_report(target, run, rows)

    print()
    print(console_summary(run, rows))
    print(f"\n报告已生成: {target.resolve()}")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    targets: list[str] = []
    seen: set[str] = set()
    for spec in args.targets:
        try:
            expanded = expand_targets(spec)
        except ValueError as exc:
            print(f"目标无效: {exc}", file=sys.stderr)
            return 2
        for ip in expanded:
            if ip not in seen:
                seen.add(ip)
                targets.append(ip)

    if not targets:
        print("没有待检测地址", file=sys.stderr)
        return 2
    print(f"开始 Ping 检测 {len(targets)} 个地址 ...")

    results = ping_many(
        targets, count=max(1, args.count), timeout_ms=max(1, args.timeout_ms), workers=max(1, args.workers)
    )
    alive = [result for result in results if result.reachable]
    print(f"\n{'IP':<16}{'状态':<8}{'延迟':<12}{'TTL':<6}丢包")
    print("-" * 48)
    for result in results:
        status = "可达" if result.reachable else "不可达"
        latency = f"{result.latency_ms:.1f} ms" if result.latency_ms is not None else "-"
        ttl = str(result.ttl) if result.ttl is not None else "-"
        loss = f"{result.loss_pct:.0f}%" if result.loss_pct is not None else "-"
        print(f"{result.ip:<16}{status:<8}{latency:<12}{ttl:<6}{loss}")
    print(f"\n可达 {len(alive)} / {len(results)}")

    if args.save:
        _save_alive(Path(args.save), [result.ip for result in alive])
    return 0


def _save_alive(path: Path, ips: list[str]) -> None:
    existing: set[str] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            entry = line.split("#", 1)[0].strip()
            if entry:
                existing.add(entry.split(",", 1)[0].split()[0])
    new_ips = [ip for ip in ips if ip not in existing]
    if not new_ips:
        print(f"清单已包含全部可达地址: {path}")
        return
    with path.open("a", encoding="utf-8") as handle:
        for ip in new_ips:
            handle.write(ip + "\n")
    print(f"已追加 {len(new_ips)} 个地址到 {path}")


def cmd_report(args: argparse.Namespace) -> int:
    try:
        config = _load_config(args)
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2
    run_id: int | None
    if args.run.lower() == "latest":
        run_id = None
    else:
        try:
            run_id = int(args.run)
        except ValueError:
            print("--run 必须是巡检编号或 latest", file=sys.stderr)
            return 2

    store = _store(config)
    try:
        run, rows = store.fetch_run(run_id)
    except LookupError as exc:
        print(f"读取巡检记录失败: {exc}", file=sys.stderr)
        return 2
    except OperationalError as exc:
        print(f"数据库不可用: {exc}", file=sys.stderr)
        return 3
    finally:
        store.close()

    if args.out:
        target = Path(args.out)
    else:
        target = Path("reports") / f"inspection_{run['id']}_{run['finished_at']:%Y%m%d_%H%M%S}.html"
    write_report(target, run, rows)
    print(console_summary(run, rows))
    print(f"\n报告已生成: {target.resolve()}")
    return 0


def cmd_init_db(args: argparse.Namespace) -> int:
    try:
        config = _load_config(args)
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2
    store = _store(config)
    try:
        store.init_schema()
    except OperationalError as exc:
        print(f"数据库不可用: {exc}", file=sys.stderr)
        return 3
    finally:
        store.close()
    print(f"数据库表已就绪: {_masked_url(config.database_url)}")
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    try:
        config = _load_config(args)
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2

    from .web import create_app

    app = create_app(
        config,
        report_dir=Path(args.report_dir),
        inventory_path=args.inventory,
        demo=args.demo,
    )
    print(f"Web 巡检仪表盘: http://{args.host}:{args.port}")
    print(f"数据库: {_masked_url(config.database_url)}")
    if args.demo:
        print("模拟设备: 127.0.0.1:22201 (Huawei) / 127.0.0.1:22202 (Cisco)")
    try:
        app.run(host=args.host, port=args.port, threaded=True, debug=False)
    except OSError as exc:
        print(f"启动失败: {exc}", file=sys.stderr)
        return 3
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("paramiko").setLevel(logging.WARNING)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
