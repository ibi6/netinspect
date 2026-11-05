/* 静态演示数据：拦截 /api/*，让仪表盘在 GitHub Pages 上完整可交互。 */
(() => {
  const pad = (value) => String(value).padStart(2, "0");
  const stamp = (value) => {
    const date = new Date(value);
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  };
  const json = (data, status = 200) =>
    new Response(JSON.stringify(data), {
      status,
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });

  const base = Date.now();
  let runId = 12;
  let simulation = null;

  const realDevices = [
    {
      id: "core-sw",
      ip: "10.10.1.1",
      name: "Core-SW-01",
      vendor: "huawei",
      port: 22,
      username: "netops",
      ssh: true,
      managed: true,
    },
    {
      id: "agg-sw",
      ip: "10.10.1.2",
      name: "Agg-SW-02",
      vendor: "h3c",
      port: 22,
      username: "netops",
      ssh: true,
      managed: true,
    },
    {
      id: "edge-rt",
      ip: "10.10.1.254",
      name: "Edge-RT-01",
      vendor: "cisco",
      port: 22,
      username: "netops",
      ssh: true,
      managed: false,
    },
    {
      id: null,
      ip: "10.10.1.77",
      name: "Old-SW-03",
      vendor: "",
      port: 22,
      username: "netops",
      ssh: false,
      managed: false,
    },
  ];

  const demoDevices = [
    { id: null, ip: "192.168.56.10", name: "CoreSW", vendor: "huawei", port: 22, username: "demo", ssh: true, managed: false },
    { id: null, ip: "192.168.56.11", name: "EdgeSW", vendor: "cisco", port: 22, username: "demo", ssh: true, managed: false },
    { id: null, ip: "192.168.56.99", name: "Offline-Site", vendor: "", port: 22, username: "demo", ssh: false, managed: false },
  ];

  const specs = {
    "10.10.1.1": { host: "Core-SW-01", vendor: "huawei", model: "S5735-L24T4S-A1", os: "5.170", uptime: "12 weeks, 3 days, 4 hours", cpu: 7, mem: 32, up: 24, down: 2, latency: 1.2 },
    "10.10.1.2": { host: "Agg-SW-02", vendor: "h3c", model: "S5130S-28P-EI", os: "7.1.070", uptime: "5 weeks, 1 day, 6 hours", cpu: 12, mem: 41, up: 12, down: 1, latency: 2.4 },
    "10.10.1.254": { host: "Edge-RT-01", vendor: "cisco", model: "ISR4331/K9", os: "16.09.05", uptime: "1 year, 2 weeks, 3 days", cpu: 23, mem: 55, up: 4, down: 0, latency: 4.8 },
    "192.168.56.10": { host: "CoreSW", vendor: "huawei", model: "S5735-L24T4S-A1", os: "5.170", uptime: "12 weeks, 3 days", cpu: 7, mem: 32, up: 3, down: 1, latency: 1.0 },
    "192.168.56.11": { host: "EdgeSW", vendor: "cisco", model: "WS-C2960-24TT-L", os: "15.0(2)SE11", uptime: "2 years, 10 weeks", cpu: 5, mem: 28, up: 2, down: 2, latency: 1.0 },
    "10.10.1.77": { host: "Old-SW-03", vendor: "", model: "-", os: "-", uptime: "-", cpu: 0, mem: 0, up: 0, down: 0, latency: 0 },
  };

  function buildRow(device, offline = false) {
    const spec = specs[device.ip] || { host: device.name, vendor: device.vendor || "unknown", model: "-", os: "-", uptime: "-", cpu: 8, mem: 30, up: 1, down: 0, latency: 2.0 };
    const ssh = Boolean(device.ssh);
    const pct = (value) => `${value}%`;
    return {
      id: device.id && device.id.length <= 12 ? Number(String(device.id).replace(/\D/g, "").slice(0, 6)) || 8800 : null,
      ip: device.ip,
      name: device.name || spec.host,
      ok: !offline,
      status: offline ? "不可达" : "正常",
      host: spec.host,
      vendor_model: `${spec.vendor} ${spec.model}`,
      os_display: spec.os,
      uptime_display: spec.uptime,
      cpu_display: offline ? "-" : pct(spec.cpu),
      mem_display: offline ? "-" : pct(spec.mem),
      interfaces_display: offline ? "-" : `${spec.up} / ${spec.down}`,
      latency_display: offline ? "-" : `${spec.latency.toFixed(1)} ms`,
      loss_display: offline ? "100%" : "0%",
      error_display: offline ? "请求超时" : "",
      ping_ok: !offline,
      ssh_ok: !offline && ssh,
      ssh_attempted: !offline && ssh,
      hostname: spec.host,
      vendor: spec.vendor,
      model: spec.model,
      os_version: spec.os,
      uptime: spec.uptime,
      cpu_pct: offline ? null : spec.cpu,
      mem_pct: offline ? null : spec.mem,
      interfaces_up: offline ? null : spec.up,
      interfaces_down: offline ? null : spec.down,
      latency_ms: offline ? null : spec.latency,
    };
  }

  let rows = [
    buildRow(realDevices[0]),
    buildRow(realDevices[1]),
    buildRow(realDevices[2]),
    {
      ...buildRow({ id: null, ip: "10.10.1.77", name: "Old-SW-03", vendor: "", ssh: false }, true),
      host: "Old-SW-03",
      vendor_model: "-",
      hostname: "Old-SW-03",
    },
  ];
  rows = rows.map((row) => ({ ...row, id: row.ip === "10.10.1.1" ? 8801 : row.ip === "10.10.1.2" ? 8802 : row.ip === "10.10.1.254" ? 8803 : 8804 }));

  const history = Array.from({ length: 8 }, (_, index) => {
    const started = base - (8 - index) * 3600 * 1000;
    const failed = index === 4 ? 0 : index % 3 === 0 ? 2 : 1;
    const total = 4;
    return {
      id: index + 5,
      started_at: stamp(started),
      finished_at: stamp(started + 4200),
      total,
      reachable: total - (failed > 1 ? 2 : 1),
      ssh_ok: total - failed,
      failed,
    };
  });
  history[history.length - 1] = {
    id: 12,
    started_at: stamp(base - 280000),
    finished_at: stamp(base - 276000),
    total: 4,
    reachable: 3,
    ssh_ok: 3,
    failed: 1,
  };

  const trends = Array.from({ length: 8 }, (_, index) => {
    const cpu = [6, 6, 9, 8, 7, 8, 12, 10][index];
    const mem = [27, 28, 32, 30, 29, 31, 36, 33][index];
    const latency = [1.1, 1.2, 1.6, 1.4, 1.3, 1.5, 2.0, 1.7][index];
    return {
      id: index + 5,
      started_at: stamp(base - (8 - index) * 3600 * 1000),
      finished_at: stamp(base - (8 - index) * 3600 * 1000 + 4200),
      total: 4,
      reachable: index === 6 ? 2 : 3,
      failed: index === 6 ? 2 : 1,
      cpu_avg: cpu,
      mem_avg: mem,
      latency_avg: latency,
    };
  });

  const initialRun = {
    id: 12,
    started_at: stamp(base - 280000),
    finished_at: stamp(base - 276000),
    total: 4,
    reachable: 3,
    ssh_ok: 3,
    failed: 1,
  };

  let auto = {
    enabled: false,
    interval_minutes: 15,
    next_run: null,
    last_error: "",
    source: "config",
  };

  let live = {
    status: "idle",
    started_at: null,
    finished_at: null,
    total: 0,
    completed: 0,
    results: [],
    pending: [],
    error: "",
    run_id: null,
    report_url: null,
    source: "",
    ssh: true,
  };

  function statsFor(items) {
    return {
      total: items.length,
      reachable: items.filter((row) => row.ping_ok).length,
      ssh_ok: items.filter((row) => row.ssh_ok).length,
      failed: items.filter((row) => !row.ok).length,
    };
  }

  function overview() {
    return {
      config: { db: "sqlite", devices: realDevices.length, demo_devices: demoDevices.length, ssh_user: "netops" },
      latest: {
        run: initialRun,
        rows,
        stats: statsFor(rows),
        report_url: "report.html",
      },
      history,
      live,
      auto,
      error: "",
    };
  }

  function devicesPayload() {
    return {
      real: realDevices,
      config: realDevices.filter((item) => !item.managed),
      managed: realDevices.filter((item) => item.managed),
      demo: demoDevices,
    };
  }

  function finishSimulation() {
    const started = live.started_at;
    const id = ++runId;
    const finished = stamp(Date.now());
    const run = {
      id,
      started_at: started,
      finished_at: finished,
      total: live.results.length,
      reachable: live.results.filter((row) => row.ping_ok).length,
      ssh_ok: live.results.filter((row) => row.ssh_ok).length,
      failed: live.results.filter((row) => !row.ok).length,
    };
    rows = live.results.map((row, index) => ({ ...row, id: 9000 + id * 10 + index }));
    history.unshift({ ...run });
    history.length = 8;
    initialRun.id = run.id;
    initialRun.started_at = run.started_at;
    initialRun.finished_at = run.finished_at;
    initialRun.total = run.total;
    initialRun.reachable = run.reachable;
    initialRun.ssh_ok = run.ssh_ok;
    initialRun.failed = run.failed;
    live = {
      ...live,
      status: "done",
      finished_at: finished,
      run_id: id,
      report_url: "report.html",
      results: [],
      pending: [],
    };
    if (!trends.some((item) => item.id === id)) {
      trends.push({
        id,
        started_at: run.started_at,
        finished_at: run.finished_at,
        total: run.total,
        reachable: run.reachable,
        failed: run.failed,
        cpu_avg: run.reachable ? 10.5 : null,
        mem_avg: run.reachable ? 38 : null,
        latency_avg: run.reachable ? 1.8 : null,
      });
      trends.shift();
    }
    simulation = null;
  }

  function startSimulation(payload) {
    if (live.status === "running") return json({ error: "已有巡检正在进行" }, 409);
    const select = payload.source === "demo" ? demoDevices : realDevices;
    const targets = select.length ? select : demoDevices;
    if (!targets.length) return json({ error: "没有可巡检设备" }, 400);
    live = {
      status: "running",
      started_at: stamp(Date.now()),
      finished_at: null,
      total: targets.length,
      completed: 0,
      results: [],
      pending: targets.map((item) => ({ ip: item.ip, name: item.name })),
      error: "",
      run_id: null,
      report_url: null,
      source: payload.source || "config",
      ssh: payload.ssh !== false,
    };
    let index = 0;
    const tick = () => {
      if (index >= targets.length) {
        finishSimulation();
        return;
      }
      const device = targets[index];
      index += 1;
      const offline = device.name === "Offline-Site" || device.name === "Old-SW-03" || device.ip.endsWith(".99");
      const row = buildRow(device, offline);
      live.results.push(row);
      live.completed = live.results.length;
      live.pending = live.pending.filter((item) => !(item.ip === device.ip && item.name === device.name));
      simulation = setTimeout(tick, 850);
    };
    simulation = setTimeout(tick, 700);
    return json({ ok: true }, 202);
  }

  function apiRoute(pathname) {
    return pathname.replace(/^\/netinspect/, "").replace(/\/$/, "") || "/";
  }

  const realFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const url = typeof input === "string" ? input : input.url;
    const parsed = new URL(url, window.location.href);
    const route = apiRoute(parsed.pathname);
    const method = String(init.method || (input && input.method) || "GET").toUpperCase();

    if (!route.startsWith("/api/")) {
      return realFetch(input, init);
    }

    if (route === "/api/overview" && method === "GET") return json(overview());
    if (route === "/api/devices" && method === "GET") return json(devicesPayload());
    if (route === "/api/inspect/status" && method === "GET") return json(live);
    if (route === "/api/inspect" && method === "POST") {
      const payload = JSON.parse(init.body || "{}");
      return startSimulation(payload);
    }
    if (route === "/api/auto" && method === "GET") return json(auto);
    if (route === "/api/auto" && method === "POST") {
      const payload = JSON.parse(init.body || "{}");
      auto = {
        enabled: Boolean(payload.enabled),
        interval_minutes: Number(payload.interval_minutes) || 15,
        next_run: payload.enabled
          ? stamp(Date.now() + (Number(payload.interval_minutes) || 15) * 60000)
          : null,
        last_error: "",
        source: realDevices.length ? "config" : "demo",
      };
      return json({ auto });
    }
    if (route === "/api/trends" && method === "GET") return json(trends);
    if (route === "/api/scan" && method === "POST") {
      const payload = JSON.parse(init.body || "{}");
      const tokens = String(payload.targets || "").split(/[\s,]+/).filter(Boolean);
      const targets = [];
      tokens.forEach((token) => {
        if (token.includes("/") || token.includes("-")) {
          targets.push("10.10.1.1", "10.10.1.2", "10.10.1.254");
        } else {
          targets.push(token);
        }
      });
      return json({
        results: targets.map((ip, index) => ({
          ip,
          reachable: ip.startsWith("10.10.1.") || ip === "127.0.0.1",
          latency: ip === "127.0.0.1" ? "1.0 ms" : `${(index + 1) * 1.3} ms`,
          ttl: 128,
          loss: "0%",
          error: "",
        })),
        total: targets.length,
        alive: targets.filter((ip) => ip.startsWith("10.10.1.") || ip === "127.0.0.1").length,
      });
    }
    if (route.startsWith("/api/device/history") && method === "GET") {
      const ip = parsed.searchParams.get("ip");
      const name = parsed.searchParams.get("name");
      const source = rows.find((row) => row.ip === ip && (!name || row.name === name)) || rows[0];
      return json(
        Array.from({ length: 8 }, (_, index) => ({
          run_id: 5 + index,
          collected_at: stamp(base - (8 - index) * 3600 * 1000),
          cpu_pct: Math.max(3, (source.cpu_pct || 8) + (index % 4) - 1),
          mem_pct: Math.max(10, (source.mem_pct || 30) + (index % 3) - 1),
          latency_ms: Math.max(0.4, (source.latency_ms || 1.5) + (index % 3) * 0.2),
        }))
      );
    }
    if (route.startsWith("/api/result/") && method === "GET") {
      const id = Number(route.split("/").pop());
      const row = rows.find((item) => item.id === id) || rows[0];
      return json({
        ...row,
        raw: {
          version: `Hostname: ${row.hostname}\nVendor: ${row.vendor}\nModel: ${row.model}\nVersion: ${row.os_version}\nUptime: ${row.uptime}`,
          cpu: `CPU utilization: ${row.cpu_display}\nFive seconds: 7% / one minute: 6% / five minutes: 5%`,
          memory: `Memory utilization: ${row.mem_display}`,
          interfaces: `Interfaces up: ${row.interfaces_up}\nInterfaces down: ${row.interfaces_down}`,
        },
      });
    }
    if (route === "/api/devices" && method === "POST") {
      const payload = JSON.parse(init.body || "{}");
      if (!payload.ip) return json({ error: "IP 地址无效" }, 400);
      if (realDevices.some((item) => item.ip === payload.ip && item.port === (Number(payload.port) || 22))) {
        return json({ error: `设备已存在: ${payload.ip}:${payload.port || 22}` }, 400);
      }
      const device = {
        id: `m${Date.now().toString(36)}`,
        ip: payload.ip,
        name: payload.name || "",
        vendor: payload.vendor || "",
        port: Number(payload.port) || 22,
        username: payload.username || "",
        ssh: payload.ssh !== false,
        managed: true,
      };
      realDevices.push(device);
      return json({ device }, 201);
    }
    if (route.startsWith("/api/devices/") && (method === "PUT" || method === "DELETE")) {
      const id = route.split("/").pop();
      const index = realDevices.findIndex((item) => item.id === id);
      if (index < 0) return json({ error: "设备不存在" }, 404);
      if (method === "DELETE") {
        realDevices.splice(index, 1);
        return json({ ok: true });
      }
      const payload = JSON.parse(init.body || "{}");
      realDevices[index] = { ...realDevices[index], ...payload, id, managed: true, port: Number(payload.port) || 22 };
      return json({ device: realDevices[index] });
    }
    return json({ error: `演示站点未实现该接口: ${route}` }, 404);
  };

  /* 演示标识 */
  const style = document.createElement("style");
  style.textContent =
    ".demo-pill{color:var(--accent);border-color:rgba(255,92,147,.3);background:var(--accent-soft);font-weight:600;letter-spacing:.02em}";
  document.head.append(style);
  const badge = document.createElement("a");
  badge.className = "pill demo-pill";
  badge.href = "https://github.com/ibi6/netinspect";
  badge.target = "_blank";
  badge.rel = "noopener";
  badge.textContent = "DEMO · 模拟数据";
  document.querySelector(".top-actions")?.prepend(badge);

  /* 报告与外链：演示站点没有后端，统一落到静态报告页 */
  document.addEventListener(
    "click",
    (event) => {
      const reportLink = event.target.closest('a[href^="/api/report/"]');
      if (reportLink) {
        event.preventDefault();
        window.open("report.html", "_blank");
        return;
      }
      const csv = event.target.closest("#export-csv");
      if (csv) {
        event.preventDefault();
        download("csv");
        return;
      }
      const jsonLink = event.target.closest("#export-json");
      if (jsonLink) {
        event.preventDefault();
        download("json");
      }
    },
    true
  );

  function download(format) {
    let blob;
    if (format === "csv") {
      const header = ["IP", "名称", "状态", "厂商", "型号", "CPU(%)", "内存(%)", "延迟(ms)"];
      const lines = rows.map((row) => [row.ip, row.name, row.status, row.vendor, row.model, row.cpu_pct ?? "", row.mem_pct ?? "", row.latency_ms ?? ""]);
      blob = new Blob(["\ufeff" + [header, ...lines].map((line) => line.join(",")).join("\n")], { type: "text/csv;charset=utf-8" });
    } else {
      blob = new Blob([JSON.stringify({ run: initialRun, results: rows }, null, 2)], { type: "application/json" });
    }
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `netinspect-demo.${format}`;
    link.click();
    URL.revokeObjectURL(link.href);
  }
})();
