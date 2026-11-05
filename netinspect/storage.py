"""巡检结果存储：SQLAlchemy Core，兼容 MySQL 与 SQLite。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    func,
    insert,
    select,
    text,
)
from sqlalchemy.engine import Engine, make_url

from .collector import DeviceResult

metadata = MetaData()

runs_table = Table(
    "inspection_runs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("started_at", DateTime, nullable=False),
    Column("finished_at", DateTime, nullable=False),
    Column("total", Integer, nullable=False),
    Column("reachable", Integer, nullable=False),
    Column("ssh_ok", Integer, nullable=False),
    Column("failed", Integer, nullable=False),
)

results_table = Table(
    "device_results",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "run_id",
        Integer,
        ForeignKey("inspection_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("ip", String(64), nullable=False, index=True),
    Column("name", String(128), nullable=False, default=""),
    Column("ping_ok", Boolean, nullable=False),
    Column("latency_ms", Float),
    Column("ttl", Integer),
    Column("loss_pct", Float),
    Column("ssh_attempted", Boolean, nullable=False, default=False),
    Column("ssh_ok", Boolean, nullable=False, default=False),
    Column("hostname", String(128)),
    Column("vendor", String(32)),
    Column("model", String(128)),
    Column("os_version", String(64)),
    Column("uptime", String(128)),
    Column("cpu_pct", Float),
    Column("mem_pct", Float),
    Column("interfaces_up", Integer),
    Column("interfaces_down", Integer),
    Column("raw", JSON),
    Column("error", Text),
    Column("collected_at", DateTime, nullable=False),
)


class Store:
    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.url = url
        kwargs: dict[str, Any] = {"echo": echo, "pool_pre_ping": True}
        if make_url(url).get_backend_name() == "sqlite":
            kwargs["connect_args"] = {"check_same_thread": False}
        self.engine: Engine = create_engine(url, **kwargs)

    def ensure_database(self) -> None:
        """MySQL 下自动创建数据库（表由 ``create_all`` 创建）。"""

        parsed = make_url(self.url)
        if parsed.get_backend_name() != "mysql" or not parsed.database:
            return
        database = parsed.database
        safe_database = database.replace("`", "``")
        server_url = parsed.set(database=None)
        server = create_engine(server_url)
        try:
            with server.begin() as connection:
                connection.execute(
                    text(
                        f"CREATE DATABASE IF NOT EXISTS `{safe_database}` "
                        "DEFAULT CHARACTER SET utf8mb4"
                    )
                )
        finally:
            server.dispose()

    def init_schema(self) -> None:
        self.ensure_database()
        metadata.create_all(self.engine)

    def save_run(
        self,
        started_at: datetime,
        finished_at: datetime,
        results: Sequence[DeviceResult],
    ) -> int:
        total = len(results)
        reachable = sum(1 for item in results if item.ping.reachable)
        ssh_ok = sum(1 for item in results if item.ssh_ok)
        failed = sum(1 for item in results if not item.healthy)
        rows = [self._result_row(item, finished_at) for item in results]
        with self.engine.begin() as connection:
            run_id = connection.execute(
                insert(runs_table).values(
                    started_at=started_at,
                    finished_at=finished_at,
                    total=total,
                    reachable=reachable,
                    ssh_ok=ssh_ok,
                    failed=failed,
                )
            ).inserted_primary_key[0]
            if rows:
                for row in rows:
                    row["run_id"] = run_id
                connection.execute(insert(results_table), rows)
        return int(run_id)

    @staticmethod
    def _result_row(result: DeviceResult, collected_at: datetime) -> dict[str, Any]:
        facts = result.facts
        error = result.error or (result.ping.error if not result.ping.reachable else "")
        return {
            "ip": result.ip,
            "name": result.name,
            "ping_ok": result.ping.reachable,
            "latency_ms": result.ping.latency_ms,
            "ttl": result.ping.ttl,
            "loss_pct": result.ping.loss_pct,
            "ssh_attempted": result.ssh_attempted,
            "ssh_ok": result.ssh_ok,
            "hostname": facts.hostname if facts else None,
            "vendor": facts.vendor if facts else None,
            "model": facts.model if facts else None,
            "os_version": facts.os_version if facts else None,
            "uptime": facts.uptime if facts else None,
            "cpu_pct": facts.cpu_pct if facts else None,
            "mem_pct": facts.mem_pct if facts else None,
            "interfaces_up": facts.interfaces_up if facts else None,
            "interfaces_down": facts.interfaces_down if facts else None,
            "raw": facts.raw if facts else None,
            "error": error or None,
            "collected_at": collected_at,
        }

    def latest_run_id(self) -> int | None:
        with self.engine.connect() as connection:
            return connection.execute(select(func.max(runs_table.c.id))).scalar_one_or_none()

    def fetch_run(self, run_id: int | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """读取一次巡检及其明细；``run_id`` 为 None 时取最近一次。"""

        with self.engine.connect() as connection:
            if run_id is None:
                run_id = connection.execute(select(func.max(runs_table.c.id))).scalar_one_or_none()
            if run_id is None:
                raise LookupError("数据库中还没有巡检记录")
            run = connection.execute(
                select(runs_table).where(runs_table.c.id == run_id)
            ).mappings().first()
            if run is None:
                raise LookupError(f"巡检记录不存在: {run_id}")
            rows = (
                connection.execute(
                    select(results_table)
                    .where(results_table.c.run_id == run_id)
                    .order_by(results_table.c.id)
                )
                .mappings()
                .all()
            )
        return dict(run), [dict(row) for row in rows]

    def fetch_history(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(runs_table).order_by(runs_table.c.id.desc()).limit(limit)
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows]

    def fetch_trends(self, limit: int = 12) -> list[dict[str, Any]]:
        """按巡检批次汇总 CPU / 内存 / 延迟均值与异常数。"""

        statement = (
            select(
                runs_table.c.id,
                runs_table.c.started_at,
                runs_table.c.finished_at,
                runs_table.c.total,
                runs_table.c.reachable,
                runs_table.c.failed,
                func.avg(results_table.c.cpu_pct).label("cpu_avg"),
                func.avg(results_table.c.mem_pct).label("mem_avg"),
                func.avg(results_table.c.latency_ms).label("latency_avg"),
            )
            .select_from(
                runs_table.outerjoin(
                    results_table, results_table.c.run_id == runs_table.c.id
                )
            )
            .group_by(
                runs_table.c.id,
                runs_table.c.started_at,
                runs_table.c.finished_at,
                runs_table.c.total,
                runs_table.c.reachable,
                runs_table.c.failed,
            )
            .order_by(runs_table.c.id.desc())
            .limit(limit)
        )
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [dict(row) for row in reversed(rows)]

    def fetch_device_history(
        self, ip: str, name: str | None = None, limit: int = 12
    ) -> list[dict[str, Any]]:
        statement = select(
            results_table.c.run_id,
            results_table.c.collected_at,
            results_table.c.cpu_pct,
            results_table.c.mem_pct,
            results_table.c.latency_ms,
        ).where(results_table.c.ip == ip)
        if name:
            statement = statement.where(results_table.c.name == name)
        statement = statement.order_by(results_table.c.id.desc()).limit(limit)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [dict(row) for row in reversed(rows)]

    def fetch_result(self, result_id: int) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(results_table).where(results_table.c.id == result_id)
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    def close(self) -> None:
        self.engine.dispose()
