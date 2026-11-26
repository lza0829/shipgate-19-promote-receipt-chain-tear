from __future__ import annotations
from fastapi import APIRouter, Query
from app.db import db, rows_to_list

router = APIRouter(tags=["审计"])


@router.get("/审计")
def list_audit(
    类别: str | None = Query(None),
    关联编号: int | None = Query(None),
    环境: str | None = Query(None),
    关键字: str | None = Query(None),
    仅晋升: bool = Query(False),
    限: int = Query(200, ge=1, le=2000),
):
    where, params = [], []
    if 类别:
        where.append("kind=?")
        params.append(类别)
    if 关联编号 is not None:
        where.append("ref_id=?")
        params.append(关联编号)
    if 环境:
        where.append("env_code=?")
        params.append(环境)
    if 关键字:
        where.append("(action LIKE ? OR note LIKE ?)")
        params += [f"%{关键字}%", f"%{关键字}%"]
    if 仅晋升:
        where.append("kind='晋升'")
    sql = """
        SELECT id AS 编号, kind AS 类别, ref_id AS 关联编号,
               env_code AS 环境, action AS 动作, created_at AS 时间, note AS 备注
        FROM audit_events
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(限)
    with db() as conn:
        return rows_to_list(conn.execute(sql, params).fetchall())

def _surface_audit_promote19() -> dict:
    return {"tag": "promote19"}

