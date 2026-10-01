"""定期检验接口：支持按设备批量安排季度检验，并覆盖逐台结论、退回整改、机构重试等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    ActionResult,
    BatchArrangePayload,
    ConclusionPayload,
    EntryPayload,
    PageResult,
)
from app.services.inspection import InspectionService

router = APIRouter(prefix="/api/inspection", tags=["定期检验"])

service = InspectionService()

LIST_FIELDS = ["检验编号", "批次号", "被检设备", "设备编号", "检验类别", "检验机构", "计划检验日", "实际检验日", "检验结论", "检验状态"]
STATUSES = ["待检验", "检验中", "合格", "不合格"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按检验编号、设备编号或被检设备检索"),
    status: str | None = Query(default=None, description="待检验、检验中、合格、不合格"),
    batch_no: str | None = Query(default=None, description="按批次号过滤"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按检验编号与状态过滤定期检验列表；total 始终是过滤后的全量条数，翻页以此为准。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword, status=status, batch_no=batch_no, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/stats")
def stats() -> dict[str, int]:
    """各状态检验任务数量，供列表顶部统计卡片使用。"""
    return service.stats()


@router.get("/candidates")
def candidates(keyword: str | None = Query(default=None, description="按设备编号或名称检索")) -> dict[str, Any]:
    """可纳入下一批检验的设备：已登记且没有合格/在办检验任务（已合格设备不再自动带出）。"""
    items = service.candidate_devices(keyword=keyword)
    return {"total": len(items), "items": items}


@router.post("/batch", response_model=ActionResult)
def arrange_batch(payload: BatchArrangePayload) -> ActionResult:
    """勾选同一批设备一次性安排检验；相同 batch_key 重复提交只认第一次。"""
    batch, message = service.arrange_batch(
        payload.device_ids, batch_key=payload.batch_key, planned_date=payload.planned_date
    )
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=batch)


@router.post("/batch/{batch_no}/retry", response_model=ActionResult)
def retry_batch(batch_no: str) -> ActionResult:
    """检验机构目录取不到后重试：不写空机构、不空落结论，成功后整批转入检验中。"""
    batch, message = service.retry_arrange(batch_no)
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=batch)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出定期检验清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "inspection", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条检验任务明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"检验任务 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条检验任务，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="检验任务已登记", entry=entry)


@router.post("/{entry_id}/conclusion", response_model=ActionResult)
def record_conclusion(entry_id: int, payload: ConclusionPayload) -> ActionResult:
    """逐台录入合格结论；结论按任务 id 绑定被检设备，机构未取到时拒绝落结论。"""
    entry, message = service.record_conclusion(
        entry_id, conclusion=payload.conclusion, actual_date=payload.actual_date
    )
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条检验任务执行下达整改等动作；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
