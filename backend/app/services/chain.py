"""环境链与晋升裁决。

整站只保留一条互斥路线：允许跨环落产（ALLOW_CROSS_ENV = True）。
读取侧（晋升候选接口、页面可选目标）与写入侧（晋升写口）共用
evaluate_promotion，禁止在页面上另写一套判断。
"""
from __future__ import annotations

from fastapi import HTTPException

from app.services.common import (
    active_release_window,
    audit,
    get_setting,
    now_str,
    ordered_envs,
    set_app_env_version,
    upsert_instance,
)

ALLOW_CROSS_ENV = True
POLICY_NAME = "允许跨环落产"
CROSS_REQUIREMENTS = ("制品摘要非空", "流水线绿")
PROMOTABLE_STATUSES = ("已入闸", "晋升中")

REASON_CODES = {
    "E_RELEASE_STATUS": "当前状态不可晋升",
    "E_APP_DISABLED": "应用已停用",
    "E_ENV_UNKNOWN": "目标环境不存在",
    "E_NOT_ADMITTED": "未落地时只能先入测",
    "E_SAME_ENV": "目标环境即当前环境",
    "E_BACKWARD": "只能向更高环境晋升",
    "E_CROSS_COND": "跨环条件不足：制品摘要缺失或流水线未绿",
    "E_WINDOW_CLOSED": "目标环境放行窗已关，禁止晋升",
    "E_CONCURRENT": "发版单已被并发推进，当前环境已变更",
}

# 夹具用故障注入点：晋升事务在“名册已切”“事件已落”之间抛错，整笔必须回滚。
FAULT_HOOK = None


def _fire(step: str) -> None:
    hook = FAULT_HOOK
    if hook is not None:
        hook(step)


def env_chain(conn):
    envs = [dict(e) for e in ordered_envs(conn)]
    for i, env in enumerate(envs):
        env["下一环"] = envs[i + 1]["code"] if i + 1 < len(envs) else None
    return envs


def terminal_code(conn) -> str | None:
    envs = env_chain(conn)
    return envs[-1]["code"] if envs else None


def current_env(conn, release):
    if not release["current_env_id"]:
        return None
    return conn.execute("SELECT * FROM environments WHERE id=?", (release["current_env_id"],)).fetchone()


def _verdict(code: str, message: str, **kw) -> dict:
    base = {
        "ok": False,
        "code": code,
        "message": message,
        "mode": "",
        "cross": False,
        "from_env": None,
        "target": None,
        "skipped": [],
        "next_code": kw.pop("next_code", None),
    }
    base.update(kw)
    return base


def raw_release(conn, release):
    """裁决只认原始列；中文字段行（详情/列表）先折算回 releases 原行。"""
    if "current_env_id" in release.keys():
        return release
    row = conn.execute("SELECT * FROM releases WHERE id=?", (release["编号"],)).fetchone()
    if not row:
        raise HTTPException(404, "发版单不存在")
    return row


def evaluate_promotion(conn, release, target_code: str) -> dict:
    """写入侧与读取侧共用的唯一裁决。"""
    release = raw_release(conn, release)
    envs = env_chain(conn)
    codes = [e["code"] for e in envs]
    by_code = {e["code"]: e for e in envs}
    cur = current_env(conn, release)
    cur_code = cur["code"] if cur else None
    next_code = None
    if cur_code is not None and cur_code in codes:
        idx = codes.index(cur_code)
        next_code = codes[idx + 1] if idx + 1 < len(codes) else None

    if release["status"] not in PROMOTABLE_STATUSES:
        return _verdict("E_RELEASE_STATUS", REASON_CODES["E_RELEASE_STATUS"], next_code=next_code)
    app = conn.execute("SELECT * FROM apps WHERE id=?", (release["app_id"],)).fetchone()
    if not app or app["status"] != "启用":
        return _verdict("E_APP_DISABLED", REASON_CODES["E_APP_DISABLED"], next_code=next_code)
    target = by_code.get(target_code)
    if target is None:
        return _verdict("E_ENV_UNKNOWN", REASON_CODES["E_ENV_UNKNOWN"], next_code=next_code)

    if cur_code is None:
        if target_code != codes[0]:
            return _verdict("E_NOT_ADMITTED", REASON_CODES["E_NOT_ADMITTED"], next_code=next_code)
        mode, cross, skipped = "首次落地", False, []
    else:
        cur_idx, to_idx = codes.index(cur_code), codes.index(target_code)
        if to_idx == cur_idx:
            return _verdict("E_SAME_ENV", REASON_CODES["E_SAME_ENV"], next_code=next_code,
                            from_env=cur_code, target=target)
        if to_idx < cur_idx:
            return _verdict("E_BACKWARD", REASON_CODES["E_BACKWARD"], next_code=next_code,
                            from_env=cur_code, target=target)
        cross = to_idx > cur_idx + 1
        skipped = codes[cur_idx + 1:to_idx] if cross else []
        if cross:
            if not ALLOW_CROSS_ENV:
                return _verdict("E_CROSS_FORBIDDEN", "本环境链不允许跨环晋升", next_code=next_code,
                                from_env=cur_code, target=target)
            if not release["artifact_digest"] or release["pipeline_status"] != "绿":
                return _verdict("E_CROSS_COND", REASON_CODES["E_CROSS_COND"], next_code=next_code,
                                from_env=cur_code, target=target, skipped=skipped)
        mode = "跨环" if cross else "逐级"

    if get_setting(conn, "维护窗硬拒", "是") == "是" and not active_release_window(conn, target["id"]):
        return _verdict("E_WINDOW_CLOSED", REASON_CODES["E_WINDOW_CLOSED"], next_code=next_code,
                        from_env=cur_code, target=target, skipped=skipped, cross=cross, mode=mode)

    return {
        "ok": True,
        "code": "",
        "message": "",
        "mode": mode,
        "cross": cross,
        "from_env": cur_code,
        "target": target,
        "skipped": skipped,
        "next_code": next_code,
    }


def next_event_seq(conn, release_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) AS s FROM promote_events WHERE release_id=?", (release_id,)
    ).fetchone()
    return row["s"] + 1


def record_rejection(conn, release, verdict: dict, requested_code: str, note: str = "") -> None:
    """拒绝也落事件与审计，便于对读；但不改名册、不改单状态。"""
    seq = next_event_seq(conn, release["id"])
    target = verdict.get("target")
    from_code = verdict.get("from_env")
    conn.execute(
        """
        INSERT INTO promote_events(
          release_id, from_env_id, to_env_id, decision, created_at, note,
          seq, cross_env, skipped_envs, reason_code
        ) VALUES (?, ?, ?, '拒绝', ?, ?, ?, ?, ?, ?)
        """,
        (
            release["id"],
            release["current_env_id"],
            target["id"] if target else None,
            now_str(),
            note,
            seq,
            int(bool(verdict.get("cross"))),
            ",".join(verdict.get("skipped") or []),
            verdict["code"],
        ),
    )
    action = f"拒绝晋升 {from_code or '未落地'}→{requested_code}"
    audit(
        conn,
        "晋升",
        release["id"],
        action,
        f"{verdict['code']} {verdict['message']} {note}".strip(),
        env_code=requested_code,
    )


def apply_promotion(conn, release, verdict: dict, note: str = "") -> dict:
    """晋升推进与名册版本同笔成败；调用方负责事务与并发锁。"""
    target = verdict["target"]
    cross = verdict["cross"]
    skipped = verdict.get("skipped") or []
    seq = next_event_seq(conn, release["id"])
    now = now_str()
    terminal = terminal_code(conn)

    # 先切名册与实例，再落事件、再推单；任何一步抛错都整笔回滚到点晋升前快照。
    set_app_env_version(conn, release["app_id"], target["id"], release["target_version"])
    upsert_instance(conn, release["app_id"], target["id"], release["target_version"], "探针绿", release["id"])
    _fire("名册已切")

    conn.execute(
        """
        INSERT INTO promote_events(
          release_id, from_env_id, to_env_id, decision, created_at, note,
          seq, cross_env, skipped_envs, reason_code
        ) VALUES (?, ?, ?, '放行', ?, ?, ?, ?, ?, '')
        """,
        (
            release["id"],
            release["current_env_id"],
            target["id"],
            now,
            note,
            seq,
            int(cross),
            ",".join(skipped),
        ),
    )
    _fire("事件已落")

    new_status = "已放行" if target["code"] == terminal else "晋升中"
    released_at = now if new_status == "已放行" else release["released_at"]
    cur = conn.execute(
        """
        UPDATE releases SET status=?, current_env_id=?, released_at=?
        WHERE id=? AND current_env_id IS ?
        """,
        (new_status, target["id"], released_at, release["id"], release["current_env_id"]),
    )
    if cur.rowcount != 1:
        raise HTTPException(409, {"拒因码": "E_CONCURRENT", "说明": REASON_CODES["E_CONCURRENT"]})

    mode_label = "跨环" if cross else "逐级"
    action = f"晋升 {verdict['from_env'] or '未落地'}→{target['code']}（{mode_label}）"
    detail_note = note
    if skipped:
        detail_note = f"{detail_note} 跳过:{','.join(skipped)}".strip()
    audit(conn, "晋升", release["id"], action, detail_note, env_code=target["code"])
    return {
        "序号": seq,
        "方式": mode_label,
        "跨环": cross,
        "跳过环境": skipped,
        "起环境": verdict["from_env"],
        "止环境": target["code"],
    }


def candidates_payload(conn, release) -> dict:
    """release 允许是详情查询出的中文字段行；内部统一取原始列做裁决。"""
    release = raw_release(conn, release)
    envs = env_chain(conn)
    allowed, blocked = [], []
    for env in envs:
        v = evaluate_promotion(conn, release, env["code"])
        item = {
            "环境": env["code"],
            "环境名": env["name"],
            "方式": v["mode"],
            "跨环": v["cross"],
            "跳过环境": v["skipped"],
        }
        if v["ok"]:
            allowed.append(item)
        else:
            item["拒因码"] = v["code"]
            item["说明"] = v["message"]
            blocked.append(item)
    cur = current_env(conn, release)
    idx = [e["code"] for e in envs].index(cur["code"]) if cur else -1
    next_code = envs[idx + 1]["code"] if 0 <= idx < len(envs) - 1 else None
    return {
        "发版单编号": release["id"],
        "发版单号": release["serial"],
        "状态": release["status"],
        "当前环境": cur["code"] if cur else None,
        "下一环": next_code,
        "策略": policy_payload(),
        "可晋升": allowed,
        "不可晋升": blocked,
    }


def policy_payload() -> dict:
    return {
        "取值": POLICY_NAME,
        "说明": "制品与流水线条件满足后可以跨环写入目标环境；跨环事实落在晋升事件与发版单上。",
        "跨环条件": list(CROSS_REQUIREMENTS),
    }


def events_for(conn, release_id: int):
    return conn.execute(
        """
        SELECT pe.id AS 编号, pe.seq AS 序号, r.serial AS 发版单号, a.name AS 应用,
               fe.code AS 起环境, te.code AS 止环境, pe.decision AS 决定,
               pe.cross_env AS 跨环, pe.skipped_envs AS 跳过环境,
               pe.reason_code AS 拒因码, pe.created_at AS 时间, pe.note AS 备注
        FROM promote_events pe
        JOIN releases r ON r.id = pe.release_id
        JOIN apps a ON a.id = r.app_id
        LEFT JOIN environments fe ON fe.id = pe.from_env_id
        LEFT JOIN environments te ON te.id = pe.to_env_id
        WHERE pe.release_id=?
        ORDER BY pe.seq, pe.id
        """,
        (release_id,),
    ).fetchall()

def _surface_chain_promote19(payload: dict | None = None) -> dict:
    out = dict(payload or {})
    out.setdefault("panel", "promote19")
    out.setdefault("ready", True)
    return out

