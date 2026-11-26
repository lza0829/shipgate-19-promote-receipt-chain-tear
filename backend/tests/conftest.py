from __future__ import annotations
import os
import tempfile
from pathlib import Path

import pytest

# 导入 app.main 时会自动建库并播种；先把它指向一次性目录，避免污染仓库内 data/。
os.environ.setdefault(
    "SHIPGATE_DB", str(Path(tempfile.mkdtemp(prefix="shipgate-import-")) / "boot.db")
)

import app.db as app_db  # noqa: E402
from app import seed as app_seed  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """每个用例一套独立库，避免种子/事件互相污染。"""
    db_path = Path(tmp_path) / "shipgate.db"
    monkeypatch.setattr(app_db, "DB_PATH", db_path)
    app_seed.seed_if_empty()
    with TestClient(fastapi_app) as c:
        yield c


def release(client, release_id=1):
    return client.get(f"/api/发版单/{release_id}").json()


def overview(client):
    return client.get("/api/总览").json()


def events(client, release_id=1):
    return client.get(f"/api/发版单/{release_id}/晋升事件").json()


def audit(client, **params):
    return client.get("/api/审计", params=params).json()


def promote(client, env, release_id=1, note=""):
    return client.post(f"/api/发版单/{release_id}/晋升", json={"目标环境": env, "备注": note})


def make_release(client, app_id=1, version="9.9.9", digest="sha256:fixture", admit=True):
    r = client.post("/api/发版单", json={"应用编号": app_id, "目标版本": version, "制品摘要": digest})
    assert r.status_code == 200, r.text
    rid = r.json()["编号"]
    client.post(f"/api/发版单/{rid}/流水线", json={"流水线": "绿"})
    if admit:
        a = client.post(f"/api/发版单/{rid}/入闸")
        assert a.status_code == 200, a.text
    return rid


def nameplate(client, app_name="结算网关"):
    """名册三列版本戳：测/预/产。"""
    rows = client.get("/api/总览").json()["环境版本"]
    return {r["环境"]: r["版本"] for r in rows if r["应用"] == app_name}
