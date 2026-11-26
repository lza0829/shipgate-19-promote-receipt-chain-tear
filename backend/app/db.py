from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS apps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  owner TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT '启用' CHECK(status IN ('启用','停用'))
);
CREATE TABLE IF NOT EXISTS environments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  sort_order INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS app_env_versions (
  app_id INTEGER NOT NULL,
  env_id INTEGER NOT NULL,
  version TEXT NOT NULL DEFAULT '',
  updated_at TEXT,
  PRIMARY KEY(app_id, env_id),
  FOREIGN KEY(app_id) REFERENCES apps(id),
  FOREIGN KEY(env_id) REFERENCES environments(id)
);
CREATE TABLE IF NOT EXISTS releases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  serial TEXT NOT NULL UNIQUE,
  app_id INTEGER NOT NULL,
  target_version TEXT NOT NULL,
  artifact_digest TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT '待入闸'
    CHECK(status IN ('待入闸','已入闸','晋升中','已放行','回滚中','已回滚','已关闭')),
  pipeline_status TEXT NOT NULL DEFAULT '未回执'
    CHECK(pipeline_status IN ('未回执','绿','红')),
  current_env_id INTEGER,
  canary_ratio INTEGER NOT NULL DEFAULT 10,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  admitted_at TEXT,
  released_at TEXT,
  rolled_back_at TEXT,
  FOREIGN KEY(app_id) REFERENCES apps(id),
  FOREIGN KEY(current_env_id) REFERENCES environments(id)
);
CREATE TABLE IF NOT EXISTS promote_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  release_id INTEGER NOT NULL,
  from_env_id INTEGER,
  to_env_id INTEGER,
  decision TEXT NOT NULL CHECK(decision IN ('放行','拒绝')),
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  seq INTEGER NOT NULL DEFAULT 0,
  cross_env INTEGER NOT NULL DEFAULT 0,
  skipped_envs TEXT NOT NULL DEFAULT '',
  reason_code TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(release_id) REFERENCES releases(id),
  FOREIGN KEY(from_env_id) REFERENCES environments(id),
  FOREIGN KEY(to_env_id) REFERENCES environments(id)
);
CREATE TABLE IF NOT EXISTS release_windows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  env_id INTEGER NOT NULL,
  open_at TEXT NOT NULL,
  close_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '窗开' CHECK(status IN ('窗开','窗关')),
  FOREIGN KEY(env_id) REFERENCES environments(id)
);
CREATE TABLE IF NOT EXISTS rollback_windows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  env_id INTEGER NOT NULL,
  open_at TEXT NOT NULL,
  close_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '窗开' CHECK(status IN ('窗开','窗关')),
  FOREIGN KEY(env_id) REFERENCES environments(id)
);
CREATE TABLE IF NOT EXISTS instances (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  app_id INTEGER NOT NULL,
  env_id INTEGER NOT NULL,
  version TEXT NOT NULL,
  health TEXT NOT NULL DEFAULT '探针绿' CHECK(health IN ('探针绿','探针红')),
  release_id INTEGER,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(app_id) REFERENCES apps(id),
  FOREIGN KEY(env_id) REFERENCES environments(id),
  FOREIGN KEY(release_id) REFERENCES releases(id)
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  ref_id INTEGER,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  env_code TEXT NOT NULL DEFAULT ''
);
"""

RELEASE_STATUSES = ("待入闸", "已入闸", "晋升中", "已放行", "回滚中", "已回滚", "已关闭")
PIPELINE_STATUSES = ("未回执", "绿", "红")
HEALTH_STATUSES = ("探针绿", "探针红")
WINDOW_STATUSES = ("窗开", "窗关")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None：连接处于自动提交，事务由 db() 显式 BEGIN IMMEDIATE 控制，
    # 让并发晋升在数据库层串行化，而不是各改各的。
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


@contextmanager
def db(*, immediate: bool = False):
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


PROMOTE_EVENT_COLUMNS = {
    "seq": "INTEGER NOT NULL DEFAULT 0",
    "cross_env": "INTEGER NOT NULL DEFAULT 0",
    "skipped_envs": "TEXT NOT NULL DEFAULT ''",
    "reason_code": "TEXT NOT NULL DEFAULT ''",
}


def _migrate(conn: sqlite3.Connection) -> None:
    """旧库升级：补晋升事件附加列/放开 to_env_id，并给审计补 env_code。"""
    # 清掉“假晋升成功灯”时代残留在设置表里的涂刷计数，避免旧库继续被旁读成已放行。
    conn.execute("DELETE FROM settings WHERE key IN ('surface_last', 'surface_ok')")

    audit_cols = {r["name"] for r in conn.execute("PRAGMA table_info(audit_events)").fetchall()}
    if audit_cols and "env_code" not in audit_cols:
        conn.execute("ALTER TABLE audit_events ADD COLUMN env_code TEXT NOT NULL DEFAULT ''")

    cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(promote_events)").fetchall()}
    if not cols:
        return
    to_env = cols.get("to_env_id")
    missing = [c for c in PROMOTE_EVENT_COLUMNS if c not in cols]
    needs_rebuild = bool(missing) or (to_env is not None and to_env["notnull"])
    if not needs_rebuild:
        return
    conn.execute("ALTER TABLE promote_events RENAME TO promote_events_old")
    conn.executescript(SCHEMA)
    target_cols = [c for c in PROMOTE_EVENT_COLUMNS if c in cols]
    copy_cols = ["id", "release_id", "from_env_id", "to_env_id", "decision", "created_at", "note"]
    conn.execute(
        f"""
        INSERT INTO promote_events({', '.join(copy_cols + target_cols)})
        SELECT {', '.join(copy_cols + target_cols)} FROM promote_events_old
        """
    )
    conn.execute("DROP TABLE promote_events_old")


def init_db() -> None:
    # 初始化在进程启动时单线程执行；executescript 会隐式提交，故不套显式事务。
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def rows_to_list(rows) -> list[dict]:
    return [dict(r) for r in rows]
