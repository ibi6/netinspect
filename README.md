<div align="center">

# NetInspect

**Python 网络设备自动巡检与监控系统**

从一份 IP 清单出发，完成连通性检测、SSH 采集、结果入库与可视化报告。

[![CI](https://github.com/ibi6/netinspect/actions/workflows/ci.yml/badge.svg)](https://github.com/ibi6/netinspect/actions/workflows/ci.yml)
[![Live Demo](https://img.shields.io/badge/LIVE_DEMO-GitHub_Pages-ff5c93?style=flat-square)](https://ibi6.github.io/netinspect/)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.x-000000?style=flat-square&logo=flask)](https://flask.palletsprojects.com/)
[![MySQL](https://img.shields.io/badge/MySQL-5.7%2B-4479A1?style=flat-square&logo=mysql&logoColor=white)](https://www.mysql.com/)
[![Paramiko](https://img.shields.io/badge/SSH-Paramiko-2f6f4e?style=flat-square)](https://www.paramiko.org/)

[在线演示](https://ibi6.github.io/netinspect/) · [快速开始](#快速开始) · [架构](#架构) · [API](#api-一览)

</div>

![NetInspect 仪表盘](docs/assets/dashboard-dark.webp)

## 项目简介

NetInspect 面向网络运维场景：输入设备 IP 清单后，自动完成 **Ping 连通性检测 → Paramiko SSH 登录 → 多厂商命令采集 → MySQL 存储 → HTML 报告 / Web 仪表盘展示**。

项目同时提供三个入口：

| 入口 | 用途 |
| --- | --- |
| `python main.py run` | 一次性巡检，输出终端表格和静态 HTML 报告，适合脚本/计划任务 |
| `python main.py web` | Web 仪表盘，实时进度、趋势分析、设备管理、自动巡检 |
| GitHub Pages | 纯前端静态演示，不需要后端即可体验完整界面 |

## 界面预览

<table>
  <tr>
    <td width="50%"><img src="docs/assets/dashboard-light.webp" alt="浅色仪表盘"></td>
    <td width="50%"><img src="docs/assets/trends-dark.webp" alt="趋势分析"></td>
  </tr>
  <tr>
    <td align="center"><sub>浅色主题 · 总览 / 环图 / 设备明细</sub></td>
    <td align="center"><sub>深色主题 · CPU、内存、延迟、异常趋势</sub></td>
  </tr>
</table>

<table>
  <tr>
    <td width="52%"><img src="docs/assets/device-dialog.webp" alt="添加真实设备"></td>
    <td width="48%">
      <h3>不是只有 Mock</h3>
      <p>设备清单页可以直接添加真实交换机 / 路由器：IP、名称、厂商、SSH 端口、账号、密码、是否采集。</p>
      <p>页面添加的设备保存在本机 <code>devices.yaml</code>，密码不会回传到浏览器；CLI 与 Web 共用这份清单。</p>
      <p>内置华为 / Cisco 模拟设备，方便无真机时演示完整 SSH 采集链路。</p>
    </td>
  </tr>
</table>

## 核心能力

| 模块 | 说明 |
| --- | --- |
| 批量 IP 检测 | 单个 IP、CIDR、末段范围（`192.168.1.10-20`），多线程并发 |
| Ping | 可达性、平均延迟、TTL、丢包率，兼容 Windows / Linux / macOS |
| SSH 采集 | Paramiko 交互式 shell，自动识别提示符、关闭分页、失败重试 |
| 厂商适配 | 华为 VRP、H3C Comware、Cisco IOS；Ruijie / Juniper 按通用规则解析 |
| 设备信息 | 主机名、厂商、型号、系统版本、运行时长、CPU、内存、接口 up/down |
| 数据存储 | MySQL（SQLAlchemy Core）为主；SQLite 用于本地演示 |
| 报告 | 自包含 HTML 报告 + 终端等宽表格 |
| Web 仪表盘 | 实时巡检进度、趋势图、设备增删改、自动巡检、CSV/JSON 导出、浅/深主题 |
| 工程化 | 35 个 pytest 用例、ruff、GitHub Actions（Python 3.11 / 3.13） |

## 在线演示

**[https://ibi6.github.io/netinspect/](https://ibi6.github.io/netinspect/)**

演示站是纯静态页面，通过前端 mock 拦截 `/api/*`，可以实际点击：

- 开始巡检，观察 4 台设备的实时进度、正常/不可达切换；
- 设备清单添加、编辑、删除设备；
- 趋势分析、设备详情、历史记录、连通性扫描；
- 浅色 / 深色主题切换。

数据为模拟数据，不会发起真实网络请求。

## 架构

```mermaid
flowchart LR
    U[浏览器] --> W[Flask Web API]
    C[CLI] --> S[巡检调度]
    W --> S
    S --> P[Ping 检测]
    S --> H[Paramiko SSH]
    H --> R[厂商识别 / 输出解析]
    S --> D[(MySQL / SQLite)]
    D --> W
    D --> T[HTML 报告]
    R --> D
    M[静态演示 mock] -.-> U
```

设计要点：

- **采集与展示分离**：`collector` 只产出 `DeviceResult`，CLI 和 Web 复用同一套采集、存储、报告代码。
- **单设备失败隔离**：并发巡检中任意设备超时/登录失败，不影响整批任务。
- **交互式 SSH**：网络设备通常没有 exec channel，统一使用 `invoke_shell` + 提示符结束标记。
- **连接池复用**：Web 进程内复用 SQLAlchemy Engine，实时轮询不会反复创建连接池。

## 快速开始

### 1. 安装

```bash
git clone https://github.com/ibi6/netinspect.git
cd netinspect
pip install -r requirements.txt
```

要求 Python 3.11+。

### 2. 本地演示（无需 MySQL、无需真机）

```bash
python main.py web --demo --db-url sqlite:///demo.db
```

打开 <http://127.0.0.1:8777>，点击「开始巡检 → 演示设备」。

### 3. 接入真实设备

修改 `config.yaml` 的 SSH 默认账号、密码和数据库连接，或直接运行 Web 后在「设备清单」添加设备。

```bash
python main.py init-db          # 创建 MySQL 库表
python main.py web              # http://127.0.0.1:8777
```

也可以在命令行执行一次巡检：

```bash
python main.py run --devices devices.txt
python main.py scan 192.168.1.0/24 -c 1 --timeout-ms 500
python main.py report --run latest --out reports/latest.html
```

`devices.txt` 每行格式：`IP[,名称[,厂商[,端口]]]`。

### 4. MySQL

```sql
CREATE DATABASE net_inspect DEFAULT CHARACTER SET utf8mb4;
CREATE USER 'netinspect'@'%' IDENTIFIED BY 'your-password';
GRANT ALL PRIVILEGES ON net_inspect.* TO 'netinspect'@'%';
```

```yaml
database:
  url: "mysql+pymysql://netinspect:your-password@127.0.0.1:3306/net_inspect?charset=utf8mb4"
```

也可以直接执行 `sql/schema.sql`。

## 设备管理

- Web「设备清单 → 添加设备」支持 **添加 / 编辑 / 删除**，保存到 `devices.yaml`。
- `config.yaml` 中的设备为只读配置，同样会显示在列表中。
- 密码字段留空表示沿用 `config.yaml` 的全局 SSH 密码。
- 页面添加的密码仅保存在本机 `devices.yaml`（已加入 `.gitignore`），API 不会返回密码。
- CLI `run` 会自动合并 `config.yaml` 与 `devices.yaml`；显式 `--devices` 时以指定文件为准。

## API 一览

| Method | Path | 说明 |
| --- | --- | --- |
| `GET` | `/api/overview` | 最近巡检、统计、历史、自动巡检状态 |
| `POST` | `/api/inspect` | 启动巡检：`{source, ssh}` |
| `GET` | `/api/inspect/status` | 实时进度与逐台结果 |
| `GET` | `/api/devices` | 配置设备、页面添加设备、演示设备 |
| `POST` | `/api/devices` | 添加真实设备 |
| `PUT` | `/api/devices/{id}` | 编辑设备 |
| `DELETE` | `/api/devices/{id}` | 删除设备 |
| `GET` | `/api/trends` | 最近 N 次巡检的 CPU / 内存 / 延迟 / 异常趋势 |
| `GET` | `/api/device/history` | 单台设备历史：`?ip=&name=` |
| `GET` | `/api/result/{id}` | 单次采集详情与原始输出 |
| `POST` | `/api/scan` | 批量 Ping：`{targets, timeout_ms}` |
| `GET` | `/api/auto` · `POST` | 自动巡检状态 / 开关 |
| `GET` | `/api/report/{id}` | 渲染 HTML 报告 |
| `GET` | `/api/export/{id}?format=csv\|json` | 导出巡检结果 |

## 数据模型

| 表 | 作用 |
| --- | --- |
| `inspection_runs` | 一次巡检：起止时间、设备总数、可达数、SSH 成功数、异常数 |
| `device_results` | 每台设备每次巡检：Ping 指标、SSH 状态、厂商/型号/版本、CPU/内存、接口、错误、原始输出 JSON |

## 支持设备

| 厂商 | 版本命令 | 状态 |
| --- | --- | --- |
| Huawei VRP | `display version` / `display cpu-usage` / `display memory-usage` / `display interface brief` | 已适配 |
| H3C Comware | `display version` / `display cpu-usage` / `display memory` / `display interface brief` | 已适配 |
| Cisco IOS | `show version` / `show processes cpu` / `show memory statistics` / `show ip interface brief` | 已适配 |
| Ruijie | `show version` / `show cpu` / `show memory` | 通用规则 |
| Juniper | `show version` / `show chassis routing-engine` | 基础适配 |

> 不同型号/软件版本的输出存在差异，接入时可在 `netinspect/parsers.py` 补充正则。

## 测试与 CI

```bash
pip install -r requirements-dev.txt
ruff check .
python -m pytest -q
```

当前 **35 个测试通过**，覆盖目标展开、Ping、多厂商解析、配置、SQLite 存储、报告、模拟 SSH 采集、并发调度、Web 巡检流程、设备增删改、趋势与导出 API。

GitHub Actions 在每次 push / PR 时对 Python 3.11 和 3.13 执行 lint + 测试。

## 部署

- Web 仪表盘使用 Flask 自带服务器，仅适合本机演示；生产环境建议使用 waitress / gunicorn 托管 `create_app()`。
- 静态演示位于 `docs/`，由 GitHub Pages 直接托管，无需服务器。
- 定时巡检：Windows 任务计划程序或 Linux `crontab` 调用 `python main.py run`，也可以直接开启 Web 的「自动巡检」。

## 项目结构

```text
netinspect/
├── main.py                     # CLI 入口
├── config.yaml                 # 数据库 / Ping / SSH / 设备配置
├── devices.txt                 # 设备清单示例
├── netinspect/
│   ├── cli.py                  # run / scan / report / init-db / web
│   ├── config.py               # 配置与设备清单解析
│   ├── inventory.py            # 页面添加的真实设备（devices.yaml）
│   ├── ping.py                 # Ping、CIDR/范围、并发
│   ├── ssh.py                  # Paramiko 会话、提示符、分页
│   ├── parsers.py              # 多厂商输出解析
│   ├── collector.py            # 单设备巡检与并发调度
│   ├── storage.py              # MySQL / SQLite 存储
│   ├── report.py               # HTML 报告 / 终端摘要
│   ├── web.py                  # Flask API、实时巡检、自动巡检
│   └── templates/              # 报告与仪表盘模板
├── demo/                       # 模拟华为 / Cisco SSH 设备
├── docs/                       # GitHub Pages 静态演示
├── sql/schema.sql              # MySQL DDL
├── tests/                      # pytest
└── .github/workflows/ci.yml    # CI
```

## 已知限制

- Web 仪表盘没有账号体系，默认仅监听 `127.0.0.1`；不要直接暴露到公网。
- `devices.yaml` 中密码为明文，仅适合本机/答辩环境；生产应接入凭据库或环境变量。
- SSH 使用 `AutoAddPolicy`，生产环境应维护 `known_hosts` 并拒绝未知主机。
- MySQL 路径的代码与 DDL 已完成，但当前开发环境没有 MySQL 服务，未做真机联调；SQLite 路径已完整验证。
- 趋势与自动巡检基于当前进程状态，服务重启后自动巡检开关会重置。

## Roadmap

- 告警通知（邮件 / Webhook）与阈值规则
- SNMP 采集与接口流量趋势
- 巡检报告定时生成与归档
- Web 登录、角色权限与操作审计
- 设备配置备份与差异对比
