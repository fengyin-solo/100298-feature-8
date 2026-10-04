"""定期检验接口：维护检验任务，覆盖批量安排检验、逐台录入结论、下达整改等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    ActionResult,
    BatchConclusionPayload,
    BatchSchedulePayload,
    EntryPayload,
    PageResult,
)
from app.services.inspection import InspectionService

router = APIRouter(prefix="/api/inspection", tags=["定期检验"])

service = InspectionService()

LIST_FIELDS = ["检验编号", "被检设备", "检验类别", "检验机构", "计划检验日", "实际检验日", "检验结论", "检验状态"]
STATUSES = ["待检验", "检验中", "合格", "不合格"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按检验编号检索"),
    status: str | None = Query(default=None, description="待检验、检验中、合格、不合格"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按检验编号与状态过滤定期检验列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    if size < 1:
        raise HTTPException(status_code=400, detail="每页条数至少为 1")
    if page < 1:
        page = 1
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


# 静态路由放在 /{entry_id} 之前，避免被路径参数吞掉。
@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出定期检验清单：返回当前全量数据，供前端复核条数与统计卡片。"""
    items = service.list_all()
    return {"module": "inspection", "total": len(items), "items": items}


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


@router.post("/batch/schedule", response_model=ActionResult)
def schedule_batch(payload: BatchSchedulePayload) -> ActionResult:
    """勾选同一批设备一次性安排检验；已合格设备自动跳过，重复批次只认第一次。"""
    if not payload.entry_ids:
        return ActionResult(ok=False, message="请先勾选需要安排检验的设备")
    result = service.schedule_batch(
        payload.entry_ids,
        batch_no=payload.batch_no,
        plan_date=payload.plan_date,
    )
    return ActionResult(**result)


@router.post("/batch/conclusions", response_model=ActionResult)
def submit_conclusions(payload: BatchConclusionPayload) -> ActionResult:
    """逐台录入检验结论：合格照常落结论，不合格单独退回整改。"""
    if not payload.conclusions:
        return ActionResult(ok=False, message="没有需要录入的检验结论")
    result = service.submit_conclusions(
        payload.conclusions,
        batch_no=payload.batch_no,
        actual_date=payload.actual_date,
    )
    return ActionResult(**result)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条检验任务执行安排检验、录入结论、下达整改；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
