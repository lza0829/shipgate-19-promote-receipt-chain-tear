from __future__ import annotations
"""旧库升级夹具：晋级前的 promote_events 没有 seq/cross_env/skipped_envs/reason_code，
audit_events 没有 env_code，且 to_env_id 原本 NOT NULL。升级后既有事件不得丢失。"""
import sqlite3

import app.db as app_db

OLD_SCHEMA = """
CREATE TABLE environments(id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE,
                          name TEXT NOT NULL, sort_order INTEGER NOT NULL);
CREATE TABLE apps(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
                  owner TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '启用');
CREATE TABLE releases(id INTEGER PRIMARY KEY AUTOINCREMENT, serial TEXT NOT NULL UNIQUE,
                      app_id INTEGER NOT NULL, target_version TEXT NOT NULL,
                      status TEXT NOT NULL DEFAULT '待入闸', current_env_id INTEGER);
CREATE TABLE promote_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  release_id INTEGER NOT NULL,
  from_env_id INTEGER,
  to_env_id INTEGER NOT NULL,
  decision TEXT NOT NULL CHECK(decision IN ('放行','拒绝')),
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE audit_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL, ref_id INTEGER, action TEXT NOT NULL, created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT ''
);
"""


def test_migrate_legacy_promote_events(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(path)
    raw.executescript(OLD_SCHEMA)
    raw.execute("INSERT INTO environments(code,name,sort_order) VALUES ('测','测试',1),('预','预发',2),('产','生产',3)")
    raw.execute("INSERT INTO apps(name) VALUES ('旧应用')")
    raw.execute("INSERT INTO releases(serial,app_id,target_version,current_env_id) VALUES ('发旧0001',1,'0.1.0',1)")
    raw.execute(
        "INSERT INTO promote_events(release_id,from_env_id,to_env_id,decision,created_at,note)"
        " VALUES (1,1,2,'放行','2026-01-01 00:00:00','历史事件')"
    )
    raw.execute(
        "INSERT INTO audit_events(kind,ref_id,action,created_at,note)"
        " VALUES ('晋升',1,'晋升到预','2026-01-01 00:00:00','历史审计')"
    )
    raw.commit()
    raw.close()

    monkeypatch.setattr(app_db, "DB_PATH", path)
    app_db.init_db()

    conn = app_db.connect()
    try:
        ev = conn.execute("SELECT * FROM promote_events").fetchone()
        assert ev["note"] == "历史事件"
        assert ev["seq"] == 0 and ev["cross_env"] == 0
        assert ev["skipped_envs"] == "" and ev["reason_code"] == ""
        # to_env_id 已放开为可空（拒绝事件可无目标环境）
        info = {r["name"]: r for r in conn.execute("PRAGMA table_info(promote_events)")}
        assert info["to_env_id"]["notnull"] == 0
        au = conn.execute("SELECT * FROM audit_events").fetchone()
        assert au["env_code"] == ""
        # 迁移后仍可写入拒绝事件
        conn.execute(
            "INSERT INTO promote_events(release_id,from_env_id,to_env_id,decision,created_at,note,seq,reason_code)"
            " VALUES (1,1,NULL,'拒绝','2026-01-01 00:00:01','','1','E_SAME_ENV')"
        )
    finally:
        conn.close()
