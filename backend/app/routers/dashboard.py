from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list
from app.services.chain import policy_payload
from app.services.common import RELEASE_SELECT

router = APIRouter(tags=["总览"])

ACTIVE_STATUSES = ("已入闸", "晋升中", "已放行", "回滚中")


@router.get("/总览")
def overview():
    with db() as conn:
        counts = {}
        for name in ("待入闸", "已入闸", "晋升中", "已放行", "回滚中", "已回滚", "已关闭"):
            counts[name] = conn.execute(
                "SELECT COUNT(*) AS c FROM releases WHERE status=?", (name,)
            ).fetchone()["c"]

        # 在途格：与发版单状态机同一口径；跨环不会进“已放行”以外的格子。
        active = rows_to_list(
            conn.execute(
                RELEASE_SELECT
                + " WHERE r.status IN ('已入闸','晋升中','已放行','回滚中') ORDER BY r.id DESC"
            ).fetchall()
        )
        # 已放行格：只统计真正落产的单，跨环落产也在此格且带真实落地环境。
        released = rows_to_list(
            conn.execute(
                """
                SELECT r.id AS 编号, r.serial AS 发版单号, a.name AS 应用,
                       r.target_version AS 目标版本, e.code AS 落地环境,
                       COALESCE((
                         SELECT pe.cross_env FROM promote_events pe
                          WHERE pe.release_id=r.id AND pe.decision='放行'
                          ORDER BY pe.seq DESC, pe.id DESC LIMIT 1
                       ), 0) AS 跨环,
                       r.released_at AS 放行时间
                FROM releases r
                JOIN apps a ON a.id = r.app_id
                LEFT JOIN environments e ON e.id = r.current_env_id
                WHERE r.status='已放行'
                ORDER BY r.released_at DESC, r.id DESC
                """
            ).fetchall()
        )
        versions = rows_to_list(
            conn.execute(
                """
                SELECT a.name AS 应用, e.code AS 环境, e.name AS 环境名,
                       v.version AS 版本, v.updated_at AS 更新时间
                FROM app_env_versions v
                JOIN apps a ON a.id = v.app_id
                JOIN environments e ON e.id = v.env_id
                ORDER BY a.id, e.sort_order
                """
            ).fetchall()
        )
        health = rows_to_list(
            conn.execute(
                """
                SELECT i.id AS 编号, a.name AS 应用, e.code AS 环境, i.version AS 版本,
                       i.health AS 健康, i.release_id AS 发版单编号, i.updated_at AS 更新时间
                FROM instances i
                JOIN apps a ON a.id = i.app_id
                JOIN environments e ON e.id = i.env_id
                ORDER BY a.id, e.sort_order, i.id
                """
            ).fetchall()
        )
        return {
            "分布": counts,
            "在途": active,
            "已放行": released,
            "已放行数": len(released),
            "环境版本": versions,
            "实例": health,
            "晋升策略": policy_payload(),
        }

