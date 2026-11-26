from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import db, rows_to_list, row_to_dict
from app.services.chain import (
    apply_promotion,
    candidates_payload,
    evaluate_promotion,
    events_for,
    policy_payload,
    record_rejection,
)
from app.services.common import get_release

router = APIRouter(tags=["晋升"])


class PromoteIn(BaseModel):
    目标环境: str
    备注: str = ""


class EvaluateIn(BaseModel):
    目标环境: str


@router.get("/晋升/策略")
def promotion_policy():
    return policy_payload()


@router.get("/晋升/事件")
def promotion_events():
    with db() as conn:
        return rows_to_list(
            conn.execute(
                """
                SELECT pe.id AS 编号, pe.seq AS 序号, pe.release_id AS 发版单编号,
                       r.serial AS 发版单号, a.name AS 应用,
                       fe.code AS 起环境, te.code AS 止环境, pe.decision AS 决定,
                       pe.cross_env AS 跨环, pe.skipped_envs AS 跳过环境,
                       pe.reason_code AS 拒因码, pe.created_at AS 时间, pe.note AS 备注
                FROM promote_events pe
                JOIN releases r ON r.id = pe.release_id
                JOIN apps a ON a.id = r.app_id
                LEFT JOIN environments fe ON fe.id = pe.from_env_id
                LEFT JOIN environments te ON te.id = pe.to_env_id
                ORDER BY pe.id DESC
                """
            ).fetchall()
        )


@router.get("/发版单/{release_id}/晋升事件")
def release_events(release_id: int):
    with db() as conn:
        get_release(conn, release_id)
        return rows_to_list(events_for(conn, release_id))


@router.get("/发版单/{release_id}/晋升候选")
def promotion_candidates(release_id: int):
    """页面能不能跳到目标环境，完全由写入侧同一套 evaluate_promotion 重放。"""
    with db() as conn:
        return candidates_payload(conn, get_release(conn, release_id))


@router.post("/发版单/{release_id}/晋升研判")
def evaluate(release_id: int, body: EvaluateIn):
    with db() as conn:
        v = evaluate_promotion(conn, get_release(conn, release_id), body.目标环境)
    target = v.get("target")
    return {
        "ok": v["ok"],
        "code": v["code"],
        "message": v["message"],
        "mode": v["mode"],
        "cross": v["cross"],
        "from_env": v["from_env"],
        "target": target["code"] if target else None,
        "skipped": v["skipped"],
        "next_code": v["next_code"],
    }


@router.post("/发版单/{release_id}/晋升")
def promote(release_id: int, body: PromoteIn):
    rejection = None
    payload = None
    # BEGIN IMMEDIATE：同一时刻只允许一笔晋升推进，双晋升对撞时后来者读到新状态后失败。
    with db(immediate=True) as conn:
        release = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
        if not release:
            raise HTTPException(404, "发版单不存在")

        verdict = evaluate_promotion(conn, release, body.目标环境)
        if not verdict["ok"]:
            # 拒绝事件随事务提交，再在事务外抛错；名册、单状态、总览计数一概不动。
            record_rejection(conn, release, verdict, body.目标环境, body.备注)
            rejection = {
                "拒因码": verdict["code"],
                "说明": verdict["message"],
                "当前环境": verdict["from_env"],
                "目标环境": body.目标环境,
                "允许跨环": policy_payload()["取值"],
            }
        else:
            result = apply_promotion(conn, release, verdict, body.备注)
            payload = {
                **row_to_dict(get_release(conn, release_id)),
                "晋升": result,
                "事件": rows_to_list(events_for(conn, release_id)),
            }

    if rejection is not None:
        raise HTTPException(400, rejection)
    # 成功回执即事务内已提交的落账事实：名册版本、事件序号、单状态、审计同笔可核。
    # 回执发出后不再开第二笔事务改写任何一侧，杜绝“成功亮而名册与事件双账”。
    return payload

