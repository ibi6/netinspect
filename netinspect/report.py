"""巡检报告：HTML 文件与终端摘要。"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATE_DIR = Path(__file__).with_name("templates")


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def build_view(
    run: dict[str, Any], results: Sequence[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, int]]:
    """把数据库行转换为报告展示模型。"""

    rows: list[dict[str, Any]] = []
    for item in results:
        ssh_attempted = bool(item.get("ssh_attempted"))
        ping_ok = bool(item.get("ping_ok"))
        ssh_ok = bool(item.get("ssh_ok"))
        ok = ping_ok and (ssh_ok or not ssh_attempted)
        if not ping_ok:
            status = "不可达"
        elif ok:
            status = "正常"
        else:
            status = "异常"
        rows.append(
            {
                **item,
                "ok": ok,
                "status": status,
                "host": item.get("hostname") or "-",
                "vendor_model": " ".join(
                    value for value in (item.get("vendor"), item.get("model")) if value
                )
                or "-",
                "os_display": item.get("os_version") or "-",
                "uptime_display": item.get("uptime") or "-",
                "cpu_display": _percent_text(item.get("cpu_pct")),
                "mem_display": _percent_text(item.get("mem_pct")),
                "interfaces_display": (
                    f"{item.get('interfaces_up')} / {item.get('interfaces_down')}"
                    if item.get("interfaces_up") is not None
                    else "-"
                ),
                "latency_display": (
                    f"{item['latency_ms']:.1f} ms"
                    if item.get("latency_ms") is not None
                    else "-"
                ),
                "loss_display": (
                    f"{item['loss_pct']:.0f}%"
                    if item.get("loss_pct") is not None
                    else "-"
                ),
                "error_display": item.get("error") or "",
            }
        )

    stats = {
        "total": len(rows),
        "reachable": sum(1 for row in rows if row["ping_ok"]),
        "ssh_ok": sum(1 for row in rows if row["ssh_ok"]),
        "failed": sum(1 for row in rows if not row["ok"]),
    }
    return run, rows, stats


def _percent_text(value: Any) -> str:
    return f"{float(value):.0f}%" if value is not None else "-"


def render_html(run: dict[str, Any], results: Sequence[dict[str, Any]]) -> str:
    run_view, rows, stats = build_view(run, results)
    template = _environment().get_template("report.html")
    return template.render(run=run_view, rows=rows, stats=stats)


def write_report(
    path: str | Path, run: dict[str, Any], results: Sequence[dict[str, Any]]
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_html(run, results), encoding="utf-8")
    return target


def console_summary(run: dict[str, Any], results: Sequence[dict[str, Any]]) -> str:
    run_view, rows, stats = build_view(run, results)
    started = run_view["started_at"].strftime("%Y-%m-%d %H:%M:%S")
    finished = run_view["finished_at"].strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"巡检编号: #{run_view['id']}  时间: {started} ~ {finished}",
        (
            f"设备总数: {stats['total']}  可达: {stats['reachable']}  "
            f"SSH 成功: {stats['ssh_ok']}  异常: {stats['failed']}"
        ),
        "",
    ]
    headers = ["名称", "IP", "状态", "厂商/型号", "CPU", "内存", "接口", "延迟", "备注"]
    widths = [12, 15, 6, 24, 6, 6, 9, 10, 30]
    lines.append("  ".join(_pad(header, width) for header, width in zip(headers, widths)))
    lines.append("  ".join("-" * width for width in widths))
    for row in rows:
        values = [
            row.get("name") or "-",
            row.get("ip") or "-",
            row["status"],
            row["vendor_model"],
            row["cpu_display"],
            row["mem_display"],
            row["interfaces_display"],
            row["latency_display"],
            row["error_display"] or "-",
        ]
        lines.append(
            "  ".join(
                _pad(_truncate(value, width), width) for value, width in zip(values, widths)
            ).rstrip()
        )
    return "\n".join(lines)


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in ("W", "F") else 1 for char in text)


def _truncate(text: str, width: int) -> str:
    if _display_width(text) <= width:
        return text
    result = ""
    used = 0
    for char in text:
        char_width = 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        if used + char_width > width - 1:
            break
        result += char
        used += char_width
    return result + "…"


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))
