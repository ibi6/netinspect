# Python 网络设备自动巡检与监控系统

输入设备 IP 列表，自动完成 **Ping 连通性检测 → SSH 信息采集 → MySQL 存储 → 巡检报告生成** 的完整链路。

支持华为（VRP）、H3C（Comware）、Cisco（IOS）设备，Ruijie / Juniper 按通用规则采集；无真机时可使用内置的模拟设备完成演示。

## 功能

| 能力 | 说明 |
| --- | --- |
| 批量 IP 检测 | 单个 IP、CIDR 网段、末段范围（`192.168.1.10-20`），多线程并发 |
| Ping 检测 | 可达性、平均延迟、TTL、丢包率，跨 Windows / Linux / macOS |
| SSH 采集 | Paramiko 交互式 shell，自动识别命令提示符、关闭分页 |
| 厂商识别 | 根据 `display version` / `show version` 输出自动识别 |
| 设备信息 | 主机名、厂商、型号、系统版本、运行时长、CPU、内存、接口 up/down |
| 结果存储 | MySQL（SQLAlchemy Core），一次巡检对应一组主表 + 明细；SQLite 可用于本地演示 |
| 巡检报告 | 自包含 HTML（汇总卡片 + 明细表）与终端表格 |
| Web 仪表盘 | Flask + 原生前端：实时进度、趋势图、自动巡检、导出、资源环图、连通性扫描 |
| 模拟设备 | 内置华为 / Cisco 模拟 SSH 设备，便于无真机联调与答辩演示 |

## 目录结构

```text
python巡检/
├── main.py                     # 命令行入口
├── config.yaml                 # 数据库、Ping、SSH、设备配置
├── devices.txt                 # 设备清单示例
├── devices.yaml                # 页面添加的真实设备（运行时生成，已忽略）
├── inspect.db                  # SQLite 演示库（运行时生成，已忽略）
├── netinspect/
│   ├── cli.py                  # run / scan / report / init-db / web
│   ├── config.py               # 配置与设备清单解析
│   ├── ping.py                 # Ping 检测、CIDR/范围展开、批量并发
│   ├── ssh.py                  # Paramiko 会话、提示符与分页处理
│   ├── parsers.py              # 多厂商输出解析
│   ├── collector.py            # 单设备巡检与并发调度
│   ├── storage.py              # SQLAlchemy 存储层（MySQL/SQLite）
│   ├── inventory.py            # 页面添加的真实设备（devices.yaml）
│   ├── report.py               # HTML 报告与终端摘要
│   ├── web.py                  # Flask 仪表盘与实时巡检 API
│   └── templates/
│       ├── report.html         # 静态报告模板
│       └── dashboard.html      # Web 仪表盘（含样式与交互）
├── sql/schema.sql              # MySQL 建表脚本（可选，与程序一致）
├── demo/
│   ├── fake_device.py          # 模拟 SSH 网络设备
│   └── devices_demo.txt        # 演示用设备清单
├── tests/                      # pytest 测试
├── reports/                    # 报告输出目录
├── requirements.txt
└── requirements-dev.txt
```

## 架构

```mermaid
flowchart LR
    A[设备清单<br/>IP/CIDR] --> B[并发调度]
    B --> C[Ping 检测]
    B --> D[SSH 采集]
    D --> E[厂商识别与解析]
    C --> F[DeviceResult]
    E --> F
    F --> G[(MySQL)]
    F --> H[HTML 报告]
    G --> H
    G --> I[Web 仪表盘<br/>实时进度 / 历史记录]
    J[浏览器] --> I
```

## 环境要求

- Python 3.11+
- 能访问设备的 ICMP（Ping 被防火墙禁止时，可只用 SSH 采集）
- MySQL 5.7+ / 8.0（仅用 SQLite 演示时不需要）

## 安装

```bash
pip install -r requirements.txt
```

## 配置

### 1. 数据库

创建库和账号（或让程序自动建库）：

```sql
CREATE DATABASE net_inspect DEFAULT CHARACTER SET utf8mb4;
CREATE USER 'netinspect'@'%' IDENTIFIED BY '你的密码';
GRANT ALL PRIVILEGES ON net_inspect.* TO 'netinspect'@'%';
FLUSH PRIVILEGES;
```

修改 `config.yaml`：

```yaml
database:
  url: "mysql+pymysql://netinspect:你的密码@127.0.0.1:3306/net_inspect?charset=utf8mb4"
  echo: false
```

初始化表结构：

```bash
python main.py init-db
```

也可以直接执行 `sql/schema.sql` 手工建表。

连接串优先级的覆盖顺序：`--db-url` > 环境变量 `NETINSPECT_DB_URL` > `config.yaml`。

### 2. 设备与 SSH

```yaml
ssh:
  port: 22
  username: admin
  password: "设备登录密码"
  timeout: 10          # 建立连接超时（秒）
  command_timeout: 8   # 单条命令超时（秒）
  retries: 1           # 失败重试次数
```

SSH 用户名/密码可用环境变量 `NETINSPECT_SSH_USER`、`NETINSPECT_SSH_PASSWORD` 覆盖，避免明文写入配置。

设备清单文件每行格式：`IP[,名称[,厂商[,端口]]]`，`#` 之后为注释：

```text
192.168.1.1,核心交换机,huawei,22
192.168.1.2,汇聚交换机,h3c
192.168.1.254,出口路由器,cisco
```

厂商字段只作为识别提示，最终厂商以设备版本输出为准。

## 使用

### 批量连通性扫描

```bash
python main.py scan 192.168.1.0/24 -c 1 --timeout-ms 500
python main.py scan 192.168.1.10-20 10.0.0.1
```

只做 Ping、不写数据库。`--save devices.txt` 可把可达地址追加到设备清单。

`python main.py run` 会自动合并 `devices.yaml`（页面添加的设备）与 `config.yaml` 中的设备；显式指定 `--devices` 时以该文件为准。

### 执行巡检

```bash
# 使用配置内 devices 或 --devices 清单，结果写入 MySQL 并生成 HTML
python main.py run --devices devices.txt --report-dir reports

# 只做 Ping（跳过 SSH）
python main.py run --devices devices.txt --no-ssh

# 覆盖数据库
python main.py run --devices devices.txt --db-url "mysql+pymysql://root:密码@127.0.0.1:3306/net_inspect?charset=utf8mb4"
```

### 重新生成历史报告

```bash
python main.py report --run latest
python main.py report --run 3 --out reports/run3.html
```

### 本地无 MySQL 演示

```bash
python main.py run --devices demo/devices_demo.txt --db-url sqlite:///demo.db
```


## Web 仪表盘

```bash
# 一条命令启动：自动拉起华为 / Cisco 模拟设备，使用 SQLite 演示库
python main.py web --demo --db-url sqlite:///demo.db

# 接真实设备 / MySQL（先配置好 config.yaml）
python main.py web
```

浏览器访问 <http://127.0.0.1:8777>。

页面能力：

- **总览**：巡检快照、系统信息、CPU/内存环图、设备总数/可达/SSH/异常/平均延迟、设备明细表。
- **巡检**：右上角「开始巡检」可选择配置清单或演示设备、开关 SSH 采集；巡检期间实时显示完成进度与每台设备状态。
- **设备清单**：页面直接添加 / 编辑 / 删除真实设备（IP、名称、厂商、端口、账号、密码、SSH 开关），保存在 `devices.yaml`；`config.yaml` 中的设备也会列出。密码不会回传到前端，留空则继承 `config.yaml` 的全局 SSH 密码。
- **巡检记录**：最近 8 次巡检，直接打开对应的 HTML 报告。
- **趋势分析**：最近 12 次巡检的 CPU / 内存 / 延迟折线与异常设备柱状图，带悬浮详情。
- **连通性扫描**：输入 IP / CIDR / 范围，Ping 结果直接显示在页面。
- **自动巡检**：服务端定时任务，可设 5 / 15 / 30 / 60 分钟间隔，关闭页面仍继续执行。
- **导出**：最新巡检一键下载 CSV（Excel 兼容）或 JSON。
- 设备详情：点击「详情」查看厂商、型号、版本、CPU/内存/接口，以及版本/CPU/内存/接口原始输出。
- 支持浅色 / 深色主题切换（记忆到浏览器本地）；页面切换、数字滚动、环图、图表绘制、进度条均有动效，并尊重 `prefers-reduced-motion`。

> 默认只监听 `127.0.0.1`；需要局域网访问时用 `--host 0.0.0.0`，但项目没有登录鉴权，请勿直接暴露到公网。Flask 自带服务器仅适合演示，生产可用 waitress / gunicorn 托管 `netinspect.web:create_app`。
>
> 如果浏览器曾在该端口运行过其他项目，可能因旧 Service Worker 缓存看到别的页面。换一个端口（默认已改为 8777），或在 DevTools → Application → Service Workers 中注销旧站点后刷新。

## 无真机演示（模拟设备）

1. 启动两个模拟设备（华为 / Cisco，两个终端窗口）：

```bash
python demo/fake_device.py --vendor huawei --port 22201 --hostname CoreSW
python demo/fake_device.py --vendor cisco  --port 22202 --hostname EdgeSW
```

2. 直接使用演示清单巡检（含一台不可达设备 `192.0.2.1`）：

```bash
python main.py run --devices demo/devices_demo.txt --db-url sqlite:///demo.db
```

预期：2 台设备 SSH 采集成功，1 台显示不可达；控制台打印汇总表并生成 `reports/inspection_*.html`。

## 存储结构

- `inspection_runs`：每次巡检的编号、起止时间、设备总数、可达数、SSH 成功数、异常数。
- `device_results`：每台设备每次巡检的 Ping 指标、SSH 状态、厂商/型号/版本/运行时长、CPU/内存、接口数量、错误信息与原始命令输出（JSON）。

原始输出字段 `raw` 保留 `version/cpu/memory/interfaces` 命令结果（每个最多 8000 字符），便于回溯解析问题。

## 报告

`reports/inspection_<编号>_<时间>.html` 为自包含 HTML：

- 顶部汇总卡片：设备总数、Ping 可达、SSH 成功、异常设备；
- 明细表：名称、IP、状态、主机名、厂商/型号、系统版本、运行时长、CPU、内存、接口 up/down、延迟、丢包、备注；
- 异常行高亮，所有样式内联，可直接提交或打印。

终端同时输出等宽对齐的汇总表（按中文显示宽度对齐）。

## 支持与限制

- 完整适配：华为 VRP（`display version` / `display cpu-usage` / `display memory-usage` / `display interface brief`）、H3C Comware、Cisco IOS。
- Ruijie、Juniper 已配置对应命令并按通用规则解析，实际型号需按现场输出微调正则。
- SSH 采集假设账号登录后具备执行查询命令的权限；如设备要求 `enable` 或 `super`，需在设备侧预先提权。
- Ping 失败时不会继续建立 SSH 连接（先确认网络可达再采集），这是当前调度策略；仅需 Ping 结果时使用 `--no-ssh`。
- SSH 默认使用 `AutoAddPolicy`，不校验设备主机密钥；生产环境应改为维护 `known_hosts` 并拒绝未知主机。
- Web 仪表盘面向本机演示，没有账号体系；实时进度采用轮询（800ms），不是 WebSocket。
- 页面添加的设备保存在本机 `devices.yaml`（已加入 `.gitignore`），密码为明文存储；生产环境应改为凭据库 / 环境变量。

## 测试

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

测试覆盖：CIDR/范围展开、Ping、多厂商解析、配置解析、SQLite 存储往返、HTML/终端报告、模拟设备 SSH 采集与并发巡检调度，以及 Web API 的巡检流程、报告、扫描、设备增删改与趋势接口。

## 定时巡检

程序每次执行完成一次巡检后退出。生产环境可用系统计划任务驱动：

- Windows：任务计划程序，触发器每日定时，操作 `python G:\python巡检\main.py run --devices devices.txt`
- Linux：`crontab -e`，例如 `0 2 * * * cd /opt/netinspect && python main.py run --devices devices.txt`
