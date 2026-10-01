"""定期检验批量办理的端到端测试，直接走 FastAPI 路由验证全部业务口径。"""
from __future__ import annotations

import importlib
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    # 每个用例拿到一份全新的内存仓库与机构目录开关
    monkeypatch.delenv("INSP_ORG_FAIL", raising=False)
    import app.store
    import app.seed
    import app.services.inspection
    import app.services.agency
    importlib.reload(app.seed)
    importlib.reload(app.store)
    importlib.reload(app.services.agency)
    importlib.reload(app.services.inspection)
    import app.routers.inspection as inspection_router
    importlib.reload(inspection_router)
    import app.main
    importlib.reload(app.main)
    with TestClient(app.main.app) as test_client:
        yield test_client


def _list(client, **params):
    return client.get("/api/inspection", params=params).json()


def _candidate_ids(client):
    payload = client.get("/api/inspection/candidates").json()
    return {item["设备编号"]: item for item in payload["items"]}


def test_candidates_exclude_passed_and_busy(client):
    """已合格设备不再自动排进下一批；检验中设备同样不重复带出。"""
    candidates = _candidate_ids(client)
    # 种子：REGI-0003/0004 检验中，REGI-0005 已合格
    assert "REGI-0003" not in candidates
    assert "REGI-0004" not in candidates
    assert "REGI-0005" not in candidates
    # 未安排过的设备可勾选，并带出被检设备与检验类别
    assert candidates["REGI-0001"]["被检设备"] == "1号燃气锅炉"
    assert candidates["REGI-0001"]["检验类别"] == "锅炉"


def test_batch_arrange_creates_one_task_per_device(client):
    """勾选多台设备一次性安排：逐台生成任务，被检设备与检验类别逐台带出。"""
    before = _list(client, size=200)["total"]
    resp = client.post(
        "/api/inspection/batch",
        json={"device_ids": [1, 2, 8], "batch_key": "q4-2026", "planned_date": "2026-10-15"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    batch = data["entry"]
    assert batch["总数"] == 3
    assert batch["检验中"] == 3

    after = _list(client, size=200)
    assert after["total"] == before + 3
    tasks = [row for row in after["items"] if row["批次号"] == batch["批次号"]]
    assert {row["设备编号"] for row in tasks} == {"REGI-0001", "REGI-0002", "REGI-0008"}
    assert {row["被检设备"] for row in tasks} == {"1号燃气锅炉", "2号燃气锅炉", "3吨平衡重叉车"}
    assert all(row["检验机构"] for row in tasks)
    assert all(row["status"] == "检验中" for row in tasks)


def test_duplicate_batch_submission_only_first_counts(client):
    """同一批重复提交只认第一次：不新建任务，返回同一个批次。"""
    first = client.post("/api/inspection/batch", json={"device_ids": [1, 2], "batch_key": "dup"}).json()
    second = client.post("/api/inspection/batch", json={"device_ids": [1, 2], "batch_key": "dup"}).json()
    assert first["entry"]["批次号"] == second["entry"]["批次号"]
    assert "第一次" in second["message"]
    rows = [row for row in _list(client, size=200)["items"] if row["批次号"] == first["entry"]["批次号"]]
    assert len(rows) == 2


def test_batch_rejects_passed_or_busy_devices(client):
    """把已合格/在办设备混进勾选名单时整批拦下，避免重复排期。"""
    resp = client.post("/api/inspection/batch", json={"device_ids": [1, 5, 3]}).json()
    assert resp["ok"] is False
    assert "REGI-0005" in resp["message"]  # 已合格
    assert "REGI-0003" in resp["message"]  # 检验中
    # 被拦下的设备 1 也没有被偷偷建任务
    assert all(row["设备编号"] != "REGI-0001" for row in _list(client, size=200)["items"])


def test_conclusion_bound_to_device_and_pass_excludes_next_batch(client):
    """结论按任务绑定设备录入；合格后该设备从下一批候选中消失。"""
    batch = client.post("/api/inspection/batch", json={"device_ids": [1], "batch_key": "c1"}).json()["entry"]
    task = batch["items"][0]

    # 空结论拒绝
    bad = client.post(f"/api/inspection/{task['id']}/conclusion", json={"conclusion": "  "}).json()
    assert bad["ok"] is False
    assert "不能为空" in bad["message"]

    ok = client.post(
        f"/api/inspection/{task['id']}/conclusion",
        json={"conclusion": "符合定期检验要求", "actual_date": "2026-10-16"},
    ).json()
    assert ok["ok"] is True
    assert ok["entry"]["被检设备"] == "1号燃气锅炉"
    assert ok["entry"]["检验结论"] == "符合定期检验要求"
    assert ok["entry"]["status"] == "合格"

    candidates = _candidate_ids(client)
    assert "REGI-0001" not in candidates


def test_failed_device_returned_rectify_independently(client):
    """同批里不合格的那台单独退回整改，其余合格的照常落结论。"""
    batch = client.post(
        "/api/inspection/batch", json={"device_ids": [1, 2], "batch_key": "mix"}
    ).json()["entry"]
    first, second = batch["items"]

    # 第一台合格
    client.post(f"/api/inspection/{first['id']}/conclusion", json={"conclusion": "合格"})
    # 第二台单独退回整改
    rectified = client.post(
        f"/api/inspection/{second['id']}/actions", json={"values": {"action": "下达整改"}}
    ).json()
    assert rectified["ok"] is True
    assert rectified["entry"]["status"] == "不合格"
    assert rectified["entry"]["abnormal"] is True
    assert rectified["entry"]["被检设备"] == "2号燃气锅炉"

    view = client.post("/api/inspection/batch/" + batch["批次号"] + "/retry", json={}).json()
    # 第一台结论未受影响
    first_now = next(row for row in view["entry"]["items"] if row["id"] == first["id"])
    assert first_now["status"] == "合格"
    assert first_now["检验结论"] == "合格"


def test_agency_unavailable_can_retry_without_empty_conclusion(client, monkeypatch):
    """机构目录取不到：批次保持待检验、机构为空；可重试；机构未落位前禁止录结论。"""
    monkeypatch.setenv("INSP_ORG_FAIL", "1")
    failed = client.post("/api/inspection/batch", json={"device_ids": [1], "batch_key": "retry1"}).json()
    assert failed["ok"] is True
    assert "重试" in failed["message"]
    batch_no = failed["entry"]["批次号"]
    assert failed["entry"]["待检验"] == 1
    task = failed["entry"]["items"][0]
    assert task["检验机构"] == ""
    assert task["检验结论"] == ""

    # 机构为空时不能落结论
    blocked = client.post(f"/api/inspection/{task['id']}/conclusion", json={"conclusion": "合格"}).json()
    assert blocked["ok"] is False
    assert "检验机构" in blocked["message"]

    # 恢复机构目录后重试
    monkeypatch.delenv("INSP_ORG_FAIL")
    retried = client.post(f"/api/inspection/batch/{batch_no}/retry", json={}).json()
    assert retried["ok"] is True
    assert retried["entry"]["检验中"] == 1
    ready = retried["entry"]["items"][0]
    assert ready["检验机构"] == "市特种设备检验研究院"

    # 重试后才能录结论
    ok = client.post(f"/api/inspection/{ready['id']}/conclusion", json={"conclusion": "合格"}).json()
    assert ok["ok"] is True


def test_pagination_total_matches_across_pages(client):
    """列表条数与 total 翻页后对得上：服务端过滤后计数，分页只是切片。"""
    client.post("/api/inspection/batch", json={"device_ids": [1, 2, 6, 7, 8], "batch_key": "page"})
    page1 = _list(client, page=1, size=5)
    page2 = _list(client, page=2, size=5)
    assert page1["total"] == 8  # 3 条种子 + 5 条新批
    assert len(page1["items"]) == 5
    assert len(page2["items"]) == 3
    ids_page1 = {row["id"] for row in page1["items"]}
    ids_page2 = {row["id"] for row in page2["items"]}
    assert not ids_page1 & ids_page2

    # 状态过滤下 total 与翻完所有页后的条数之和一致
    seen = 0
    page_no = 1
    while True:
        part = _list(client, status="检验中", page=page_no, size=2)
        seen += len(part["items"])
        if page_no == 1:
            inspecting_total = part["total"]
            assert all(row["status"] == "检验中" for row in part["items"])
        if not part["items"]:
            break
        page_no += 1
    assert seen == inspecting_total == 7
