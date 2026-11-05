-- 网络设备巡检系统 MySQL 结构（可选）
-- 程序执行 `python inspect.py init-db` 时会自动建库建表；
-- 本文件供手工初始化或 DBA 审核使用。

CREATE DATABASE IF NOT EXISTS net_inspect
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE net_inspect;

CREATE TABLE IF NOT EXISTS inspection_runs (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  started_at  DATETIME NOT NULL,
  finished_at DATETIME NOT NULL,
  total       INT NOT NULL,
  reachable   INT NOT NULL,
  ssh_ok      INT NOT NULL,
  failed      INT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS device_results (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  run_id           INT NOT NULL,
  ip               VARCHAR(64) NOT NULL,
  name             VARCHAR(128) NOT NULL DEFAULT '',
  ping_ok          BOOL NOT NULL,
  latency_ms       FLOAT NULL,
  ttl              INT NULL,
  loss_pct         FLOAT NULL,
  ssh_attempted    BOOL NOT NULL DEFAULT 0,
  ssh_ok           BOOL NOT NULL DEFAULT 0,
  hostname         VARCHAR(128) NULL,
  vendor           VARCHAR(32) NULL,
  model            VARCHAR(128) NULL,
  os_version       VARCHAR(64) NULL,
  uptime           VARCHAR(128) NULL,
  cpu_pct          FLOAT NULL,
  mem_pct          FLOAT NULL,
  interfaces_up    INT NULL,
  interfaces_down  INT NULL,
  raw              JSON NULL,
  error            TEXT NULL,
  collected_at     DATETIME NOT NULL,
  CONSTRAINT fk_device_results_run
    FOREIGN KEY (run_id) REFERENCES inspection_runs (id) ON DELETE CASCADE,
  INDEX idx_device_results_run (run_id),
  INDEX idx_device_results_ip (ip)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
