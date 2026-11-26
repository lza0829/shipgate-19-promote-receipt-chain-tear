from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.db import db
from app.services.common import audit, now_str, set_app_env_version

router = APIRouter(tags=["应用"])


class AppIn(BaseModel):
    名称: str = Field(min_length=1)
    负责人: str = ""
    状态: str = "启用"


@router.get("/应用")
def list_apps():
    """应用名册：带测/预/产三列版本戳，跨环落产时对应列必须与晋升事件一致。"""
    with db() as conn:
        envs = conn.execute("SELECT * FROM environments ORDER BY sort_order").fetchall()
        rows = conn.execute(
            "SELECT id AS 编号, name AS 名称, owner AS 负责人, status AS 状态 FROM apps ORDER BY id"
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            versions = {
                r["env_id"]: r["version"]
                for r in conn.execute(
                    "SELECT env_id, version FROM app_env_versions WHERE app_id=?", (row["编号"],)
                ).fetchall()
            }
            for env in envs:
                item[env["code"]] = versions.get(env["id"], "")
            item["环境链"] = [env["code"] for env in envs]
            result.append(item)
        return result


@router.post("/应用")
def create_app(body: AppIn):
    if body.状态 not in ("启用", "停用"):
        raise HTTPException(400, "状态仅允许启用或停用")
    with db() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO apps(name, owner, status) VALUES (?, ?, ?)",
                (body.名称, body.负责人, body.状态),
            )
        except Exception:
            raise HTTPException(400, "应用名已存在")
        aid = cur.lastrowid
        for env in conn.execute("SELECT id FROM environments").fetchall():
            set_app_env_version(conn, aid, env["id"], "")
        audit(conn, "应用", aid, "新建应用", body.名称)
        return {"编号": aid}


@router.patch("/应用/{app_id}")
def patch_app(app_id: int, body: AppIn):
    with db() as conn:
        row = conn.execute("SELECT id FROM apps WHERE id=?", (app_id,)).fetchone()
        if not row:
            raise HTTPException(404, "应用不存在")
        if body.状态 not in ("启用", "停用"):
            raise HTTPException(400, "状态仅允许启用或停用")
        conn.execute(
            "UPDATE apps SET name=?, owner=?, status=? WHERE id=?",
            (body.名称, body.负责人, body.状态, app_id),
        )
        audit(conn, "应用", app_id, "更新应用", body.状态)
        return {"编号": app_id, "更新时间": now_str()}
