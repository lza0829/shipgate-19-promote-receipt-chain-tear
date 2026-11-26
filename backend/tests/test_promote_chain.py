from __future__ import annotations
"""环境链跨环闸夹具。

覆盖：跨环点晋升、逐级合法晋升、双晋升对撞、半截名册已切事件未落。
同一夹具内对读：拒因码、下一环编码表、测预产三列版本戳、晋升事件序号、
总览在途格与已放行格、审计过滤条件。
"""
import threading

import pytest

from app.routers import promote as promote_router
from app.services import chain as chain_service
from tests.conftest import (
    audit,
    events,
    make_release,
    nameplate,
    overview,
    promote,
    release,
)


def test_chain_next_codes(client):
    # 下一环编码表：测→预→产→None，由环境 sort_order 推导，读写两侧同一来源。
    import app.db as app_db

    conn = app_db.connect()
    try:
        assert [e["code"] for e in chain_service.env_chain(conn)] == ["测", "预", "产"]
        assert [e["下一环"] for e in chain_service.env_chain(conn)] == ["预", "产", None]
    finally:
        conn.close()
    envs = client.get("/api/环境").json()
    assert [e["代号"] for e in envs] == ["测", "预", "产"]


def test_legal_stepwise_promotion(client):
    before = nameplate(client)
    r = promote(client, "预")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["状态"] == "晋升中"
    assert body["当前环境"] == "预"
    assert body["下一环"] == "产"
    assert body["晋升"]["方式"] == "逐级"
    assert body["晋升"]["跨环"] is False

    after = nameplate(client)
    assert after["测"] == before["测"]
    assert after["预"] == "1.3.0"      # 名册已切
    assert after["产"] == before["产"]  # 未越级污染

    evs = events(client)
    assert [e["序号"] for e in evs] == [1]
    assert evs[0]["决定"] == "放行"
    assert evs[0]["跨环"] == 0
    assert evs[0]["起环境"] == "测" and evs[0]["止环境"] == "预"

    ov = overview(client)
    assert ov["分布"]["晋升中"] == 1
    assert ov["已放行数"] == 0
    assert all(row["发版单号"] != release(client)["发版单号"] for row in ov["已放行"])

    rows = audit(client, 环境="预")
    assert any(a["动作"] == "晋升 测→预（逐级）" for a in rows)


def test_stepwise_all_the_way_to_prod(client):
    assert promote(client, "预").status_code == 200
    r = promote(client, "产")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["状态"] == "已放行"
    assert body["当前环境"] == "产"
    assert body["落地环境"] == "产"
    assert body["晋升"]["跨环"] is False
    assert [e["序号"] for e in events(client)] == [1, 2]
    assert nameplate(client) == {"测": "1.3.0", "预": "1.3.0", "产": "1.3.0"}

    ov = overview(client)
    assert ov["分布"]["已放行"] == 1
    released = [x for x in ov["已放行"] if x["发版单号"] == release(client)["发版单号"]]
    assert released and released[0]["落地环境"] == "产" and released[0]["跨环"] == 0


def test_cross_env_promotion_is_recorded_as_cross(client):
    before = nameplate(client)
    r = promote(client, "产")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["状态"] == "已放行"
    assert body["晋升"]["方式"] == "跨环"
    assert body["晋升"]["跳过环境"] == ["预"]

    evs = events(client)
    assert len(evs) == 1 and evs[0]["跨环"] == 1 and evs[0]["跳过环境"] == "预"
    assert evs[0]["止环境"] == "产"

    after = nameplate(client)
    assert after["预"] == before["预"]  # 跨环不顺手把预发名册也改了
    assert after["产"] == "1.3.0"

    released = [x for x in overview(client)["已放行"] if x["发版单号"] == release(client)["发版单号"]]
    assert released[0]["落地环境"] == "产" and released[0]["跨环"] == 1


def test_cross_env_rejected_when_pipeline_not_green(client):
    # 放宽入闸条件，构造“已入闸但流水线红”的跨环场景。
    client.post("/api/设置", json={"键": "入闸须流水线绿", "值": "否"})
    rid = make_release(client, app_id=2, version="2.0.0", admit=False)
    client.post(f"/api/发版单/{rid}/流水线", json={"流水线": "红"})
    admitted = client.post(f"/api/发版单/{rid}/入闸")
    assert admitted.status_code == 200, admitted.text

    before = nameplate(client, "配置投递")
    r = promote(client, "产", release_id=rid)
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["拒因码"] == "E_CROSS_COND"

    assert nameplate(client, "配置投递") == before
    evs = events(client, rid)
    assert len(evs) == 1 and evs[0]["决定"] == "拒绝" and evs[0]["拒因码"] == "E_CROSS_COND"
    assert release(client, rid)["状态"] == "已入闸"
    treated = [x for x in overview(client)["已放行"] if x["发版单号"] == release(client, rid)["发版单号"]]
    assert treated == []
    assert audit(client, 关联编号=rid)


def test_rejections_do_not_touch_other_surfaces(client):
    before_np = nameplate(client)
    before_ov = overview(client)

    cases = {
        "测": "E_SAME_ENV",
    }
    for env, code in cases.items():
        r = promote(client, env)
        assert r.status_code == 400
        assert r.json()["detail"]["拒因码"] == code

    r = promote(client, "不存在")
    assert r.status_code == 400 and r.json()["detail"]["拒因码"] == "E_ENV_UNKNOWN"

    assert nameplate(client) == before_np
    assert release(client)["状态"] == before_ov["在途"][0]["状态"]
    evs = events(client)
    assert all(e["决定"] == "拒绝" for e in evs)
    assert [e["序号"] for e in evs] == [1, 2]  # 拒因码也按序号留痕


def test_candidates_match_writer(client):
    payload = client.get("/api/发版单/1/晋升候选").json()
    assert payload["当前环境"] == "测"
    assert payload["下一环"] == "预"
    allowed = {x["环境"]: x for x in payload["可晋升"]}
    blocked = {x["环境"]: x for x in payload["不可晋升"]}
    # 读侧与写侧同一裁决：候选里可晋升的，写入侧必然 200；不可晋升的必然 400 且拒因码一致。
    for env in allowed:
        r = client.post("/api/发版单/1/晋升研判", json={"目标环境": env})
        assert r.json()["ok"] is True
    for env, item in blocked.items():
        r = client.post("/api/发版单/1/晋升研判", json={"目标环境": env})
        assert r.json()["ok"] is False
        assert r.json()["code"] == item["拒因码"]


def test_concurrent_double_promotion_only_one_wins(client, monkeypatch):
    """双晋升对撞：两笔同时点晋升，只能一笔改当前环境。"""
    results = []
    barrier = threading.Barrier(2)

    def run():
        barrier.wait()
        from fastapi.testclient import TestClient
        from app.main import app as fastapi_app

        c = TestClient(fastapi_app)
        r = c.post("/api/发版单/1/晋升", json={"目标环境": "预"})
        results.append(r.status_code)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [200, 400], results
    body = release(client)
    assert body["当前环境"] == "预"
    evs = [e for e in events(client) if e["决定"] == "放行"]
    assert len(evs) == 1
    # 失败那笔留下拒绝事件（同状态不可再晋升），不产生第二个“当前环境”。
    assert all(e["止环境"] == "预" for e in events(client))


def test_half_commit_rolls_back_whole_transaction(client, monkeypatch):
    """半截：名册已切、事件未落 —— 整笔回到点晋升前快照，不留跨环假成功。"""
    before = {
        "np": nameplate(client),
        "rel": release(client),
        "ov": overview(client),
        "events": events(client),
    }

    def boom(step):
        if step == "名册已切":
            raise RuntimeError("夹具注入：名册已切但事件未落")

    monkeypatch.setattr(promote_router, "FAULT_HOOK", boom, raising=False)
    monkeypatch.setattr(chain_service, "FAULT_HOOK", boom, raising=False)
    try:
        # TestClient 默认会把未捕获异常直接抛出。
        try:
            promote(client, "产")
        except RuntimeError:
            pass
    finally:
        monkeypatch.setattr(chain_service, "FAULT_HOOK", None, raising=False)

    after = {
        "np": nameplate(client),
        "rel": release(client),
        "ov": overview(client),
        "events": events(client),
    }
    assert after["np"] == before["np"]          # 名册回到快照
    assert after["rel"]["状态"] == before["rel"]["状态"] == "已入闸"
    assert after["rel"]["当前环境"] == before["rel"]["当前环境"] == "测"
    assert after["events"] == before["events"]  # 事件未落
    assert after["ov"]["分布"] == before["ov"]["分布"]
    assert after["ov"]["已放行数"] == 0
    assert nameplate(client)["预"] == "1.1.0"   # 测已跳产的假成功被抹掉


def test_rollback_after_skewed_scenario_leaves_no_false_cross(client):
    """半截回滚后不得留下“测已跳产而预发名册拧花”。"""
    assert promote(client, "预").status_code == 200
    assert promote(client, "产").status_code == 200
    assert nameplate(client) == {"测": "1.3.0", "预": "1.3.0", "产": "1.3.0"}

    rb = client.post("/api/发版单/1/回滚", json={"目标版本": "1.2.9", "备注": "回退"})
    assert rb.status_code == 200, rb.text
    np = nameplate(client)
    assert np["产"] == "1.2.9"
    assert np["预"] == "1.3.0"  # 预发名册不被回滚顺手拧花

    ov = overview(client)
    assert ov["已放行数"] == 0
    assert ov["分布"]["已回滚"] == 1
    assert all(x["发版单号"] != release(client)["发版单号"] for x in ov["已放行"])


def test_audit_filter_by_env_and_kind(client):
    assert promote(client, "预").status_code == 200
    rows = audit(client, 类别="晋升", 环境="预")
    assert rows and all(a["类别"] == "晋升" and a["环境"] == "预" for a in rows)
    assert any("逐级" in a["动作"] for a in rows)
    assert audit(client, 环境="产") == []
    # 审计查询条件与晋升事件序号、名册三列可在同一夹具对读。
    evs = events(client)
    assert rows[0]["关联编号"] == evs[0]["编号"] or rows[0]["关联编号"] == 1
    assert any(x["序号"] == 1 and x["止环境"] == "预" for x in evs)
    assert nameplate(client)["预"] == "1.3.0"


def test_success_receipt_matches_committed_truth(client):
    """成功单号弹出后：名册列、事件序号、下一环、总览已放行格、对账差额、审计同笔可核。"""
    r = promote(client, "产")
    assert r.status_code == 200, r.text
    body = r.json()

    # 回执与落账一致：回执里的状态/事件与旁读接口逐字相同，不允许回执后另有改写。
    assert body["状态"] == release(client)["状态"] == "已放行"
    assert body["当前环境"] == release(client)["当前环境"] == "产"
    assert [e["序号"] for e in body["事件"]] == [e["序号"] for e in events(client)] == [1]

    # 名册目标环境版本列已抬；环境链下一环旁读跟着跳到链尾。
    assert nameplate(client)["产"] == "1.3.0"
    assert release(client)["下一环"] is None

    # 总览已放行格与对账差额同一口径，不暗加也不少计。
    ov = overview(client)
    assert ov["已放行数"] == 1 and ov["分布"]["已放行"] == 1
    rec = client.get("/api/对账").json()
    assert rec["发版统计"]["已放行"] == ov["已放行数"]
    assert rec["晋升事件数"] == len(events(client))
    row = [x for x in rec["名册与实例"] if x["应用"] == "结算网关" and x["环境"] == "产"]
    assert row and row[0]["名册版本"] == "1.3.0" and "1.3.0" in row[0]["实例版本们"]

    # 审计可按环境过滤重放同一笔。
    rows = audit(client, 类别="晋升", 环境="产")
    assert any("晋升" in a["动作"] and "产" in a["动作"] for a in rows)


def test_reconcile_not_inflated_by_rejection(client):
    """拒绝只留拒绝事件与审计：总览已放行格与对账差额一概不暗加。"""
    before_ov = overview(client)
    before_rec = client.get("/api/对账").json()

    r = promote(client, "不存在")
    assert r.status_code == 400
    assert r.json()["detail"]["拒因码"] == "E_ENV_UNKNOWN"

    after_ov = overview(client)
    after_rec = client.get("/api/对账").json()
    assert after_ov["分布"] == before_ov["分布"]
    assert after_ov["已放行数"] == before_ov["已放行数"] == 0
    assert after_rec["发版统计"] == before_rec["发版统计"]
    # 拒绝事件按序号留痕，事件数 +1 是台账而非差额。
    assert after_rec["晋升事件数"] == before_rec["晋升事件数"] + 1
    assert release(client)["状态"] == "已入闸"
