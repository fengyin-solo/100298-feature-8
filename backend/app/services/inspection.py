"""定期检验业务规则：批量安排检验、逐台录入结论、状态流转与筛选口径都收在这里。"""
from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

from app.store import store

MODULE = "inspection"
REQUIRED_FIELDS = ["检验编号", "被检设备", "检验类别"]
STATUS_ORDER = ["待检验", "检验中", "合格", "不合格"]

# 可安排检验的状态：待检验首检、不合格整改后复检；合格的不再进下一批。
SCHEDULABLE_STATUSES = {"待检验", "不合格"}

# 幂等台账：同一批重复提交只认第一次（安排检验 / 录入结论分开记）。
_idempotency_ledger: dict[tuple[str, str], dict[str, Any]] = {}


class InspectionAgencyUnavailable(RuntimeError):
    """检验机构暂时取不到：可原样重试，调用方不得把机构写成空值。"""


class InspectionAgencyProvider:
    """检验机构取数通道。

    对设备目录的首次取数可能抖动（网络/目录同步），按同一批次号重试即可取到；
    这里用“首次必失败、重试成功”模拟可恢复故障，方便前端重试链路联调。
    """

    AGENCY_BY_CATEGORY = {
        "锅炉": "市特种设备检验研究院（锅炉检验所）",
        "压力容器": "市特种设备检验研究院（压力容器检验所）",
        "压力管道": "省特种设备检验检测院（压力管道中心）",
        "电梯": "市电梯检验检测中心",
        "起重机械": "市特种设备检验研究院（起重机械所）",
        "场车": "市特种设备检验研究院（场内车辆所）",
    }
    DEFAULT_AGENCY = "市特种设备检验研究院"

    def __init__(self) -> None:
        # 已经抖动过一次、再取就成功的批次号
        self._warmed: set[str] = set()

    def reset(self) -> None:
        self._warmed.clear()

    def resolve_batch(self, batch_no: str, entries: list[dict[str, Any]]) -> dict[int, str]:
        """一次性为一批设备取检验机构；任一设备取不到就整体报错，不落半批数据。"""
        if batch_no not in self._warmed:
            self._warmed.add(batch_no)
            raise InspectionAgencyUnavailable("检验机构目录暂时取不到，请稍后重试本次安排")
        return {int(entry["id"]): self.resolve_one(entry) for entry in entries}

    def resolve_one(self, entry: dict[str, Any]) -> str:
        category = str(entry.get("检验类别") or "")
        for keyword, agency in self.AGENCY_BY_CATEGORY.items():
            if keyword in category:
                return agency
        return self.DEFAULT_AGENCY


agency_provider = InspectionAgencyProvider()


class InspectionService:
    # ------------------------------------------------------------------ 查询
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("检验编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        # total 先按过滤后的全集算，再切片：翻页后条数与总数始终对得上。
        page = max(page, 1)
        start = (page - 1) * max(size, 1)
        return [self._serialize(row) for row in rows[start:start + size]], total

    def list_all(self) -> list[dict[str, Any]]:
        return [self._serialize(row) for row in store.rows(MODULE)]

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return self._serialize(row) if row else None

    # ------------------------------------------------------------------ 登记
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in ["检验编号", "被检设备", "检验类别", "检验机构", "计划检验日", "实际检验日", "检验结论"]:
            value = values.get(field)
            if value not in (None, ""):
                entry[field] = value
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return self._serialize(entry), []

    # -------------------------------------------------------- 单台动作（保留）
    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"检验任务 {entry_id} 不存在或已归档"
        values = values or {}
        if action == "安排检验":
            if entry["status"] not in SCHEDULABLE_STATUSES:
                return None, f"设备「{entry.get('被检设备')}」当前为{entry['status']}，无需重复安排检验"
            agency = str(values.get("检验机构") or "").strip() or agency_provider.resolve_one(entry)
            self._apply_schedule(entry, agency, str(values.get("计划检验日") or "").strip())
            return self._serialize(entry), f"设备「{entry.get('被检设备')}」已安排检验"
        if action == "录入结论":
            message = self._apply_conclusion(entry, str(values.get("检验结论") or "").strip())
            if message:
                return None, message
            return self._serialize(entry), f"设备「{entry.get('被检设备')}」检验结论已录入"
        if action == "下达整改":
            if entry["status"] != "检验中":
                return None, f"设备「{entry.get('被检设备')}」当前为{entry['status']}，不能下达整改"
            self._mark_unqualified(entry)
            return self._serialize(entry), f"设备「{entry.get('被检设备')}」已退回整改"
        return None, f"动作「{action}」不属于定期检验可执行范围"

    # -------------------------------------------------------- 批量安排检验
    def schedule_batch(
        self,
        entry_ids: list[int],
        *,
        batch_no: str | None = None,
        plan_date: str | None = None,
    ) -> dict[str, Any]:
        """勾选同一批设备一次性安排检验。

        - 同一批次号重复提交只认第一次（幂等）；
        - 逐台带出被检设备与检验类别，机构取不到时整体可重试，不写空机构；
        - 已合格的设备自动跳过，不再排进下一批。
        """
        batch_no = (batch_no or f"BATCH-{date.today().isoformat()}-{uuid4().hex[:8]}").strip()
        ledger_key = ("schedule", batch_no)
        if ledger_key in _idempotency_ledger:
            cached = dict(_idempotency_ledger[ledger_key])
            cached["duplicated"] = True
            return cached

        entries: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        seen: set[int] = set()
        for raw_id in entry_ids:
            entry_id = int(raw_id)
            if entry_id in seen:
                continue
            seen.add(entry_id)
            entry = store.find(MODULE, entry_id)
            if entry is None:
                skipped.append({"id": entry_id, "reason": "检验任务不存在或已归档"})
            elif entry["status"] not in SCHEDULABLE_STATUSES:
                skipped.append({
                    "id": entry_id,
                    "被检设备": entry.get("被检设备"),
                    "reason": f"当前为{entry['status']}，不再排入检验批次",
                })
            else:
                entries.append(entry)

        result: dict[str, Any] = {
            "ok": False,
            "retryable": False,
            "batch_no": batch_no,
            "message": "",
            "entries": [],
            "skipped": skipped,
            "scheduled": 0,
        }
        if not entries:
            result["message"] = "勾选的设备均无需安排检验（合格设备不会重复入批）"
            return result

        # 先把整批检验机构取齐再落库：取不到时不产生半批数据，也不写空机构。
        try:
            agencies = agency_provider.resolve_batch(batch_no, entries)
        except InspectionAgencyUnavailable as exc:
            result["message"] = str(exc)
            result["retryable"] = True
            return result

        plan_date = (plan_date or date.today().isoformat()).strip()
        for entry in entries:
            self._apply_schedule(entry, agencies[int(entry["id"])], plan_date, batch_no=batch_no)

        result["ok"] = True
        result["retryable"] = False
        result["entries"] = [self._serialize(entry) for entry in entries]
        result["scheduled"] = len(entries)
        result["message"] = (
            f"批次 {batch_no} 已安排 {len(entries)} 台设备检验"
            + (f"，跳过 {len(skipped)} 台" if skipped else "")
        )
        _idempotency_ledger[ledger_key] = dict(result)
        return result

    # -------------------------------------------------------- 批量录入结论
    def submit_conclusions(
        self,
        conclusions: dict[str, Any],
        *,
        batch_no: str | None = None,
        actual_date: str | None = None,
    ) -> dict[str, Any]:
        """逐台录入检验结论。

        - 结论按检验任务 id 对号入座，防止设备和结论错位；
        - 合格的照常落合格结论；不合格的单独退回整改；
        - 校验不通过时整批不落库，避免录串台。
        """
        batch_no = (batch_no or f"RESULT-{date.today().isoformat()}-{uuid4().hex[:8]}").strip()
        ledger_key = ("conclude", batch_no)
        if ledger_key in _idempotency_ledger:
            cached = dict(_idempotency_ledger[ledger_key])
            cached["duplicated"] = True
            return cached

        actual_date = (actual_date or date.today().isoformat()).strip()
        pairs: list[tuple[dict[str, Any], str]] = []
        for raw_id, raw_result in conclusions.items():
            entry_id = int(raw_id)
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return self._fail(batch_no, f"检验任务 {entry_id} 不存在或已归档，结论无法对号入座")
            if entry["status"] != "检验中":
                return self._fail(
                    batch_no,
                    f"设备「{entry.get('被检设备')}」当前为{entry['status']}，不是检验中，不能录入结论",
                )
            result_text = str(raw_result or "").strip()
            if not result_text:
                return self._fail(batch_no, f"设备「{entry.get('被检设备')}」的检验结论为空，请补充后再提交")
            pairs.append((entry, result_text))

        if not pairs:
            return self._fail(batch_no, "没有需要录入结论的设备")

        passed: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for entry, result_text in pairs:
            entry["实际检验日"] = actual_date
            entry["检验结论"] = result_text
            if "不合格" in result_text:
                self._mark_unqualified(entry)
                rejected.append(self._serialize(entry))
            else:
                entry["status"] = "合格"
                entry["pending"] = False
                entry["abnormal"] = False
                entry["结果批次号"] = batch_no
                passed.append(self._serialize(entry))

        result = {
            "ok": True,
            "retryable": False,
            "batch_no": batch_no,
            "message": (
                f"已录入 {len(passed) + len(rejected)} 台结论：合格 {len(passed)} 台"
                + (f"，{len(rejected)} 台不合格已单独退回整改" if rejected else "")
            ),
            "entries": passed + rejected,
            "passed": passed,
            "rejected": rejected,
        }
        _idempotency_ledger[ledger_key] = dict(result)
        return result

    # ------------------------------------------------------------------ 内部
    @staticmethod
    def _apply_schedule(
        entry: dict[str, Any],
        agency: str,
        plan_date: str,
        *,
        batch_no: str | None = None,
    ) -> None:
        # 机构必须有值：取不到时上层会整体报错重试，不会走到这里写空。
        entry["检验机构"] = agency
        if plan_date:
            entry["计划检验日"] = plan_date
        entry["status"] = "检验中"
        entry["pending"] = True
        entry["abnormal"] = False
        entry["检验结论"] = ""
        if batch_no:
            entry["安排批次号"] = batch_no

    @staticmethod
    def _apply_conclusion(entry: dict[str, Any], result_text: str) -> str:
        if entry["status"] != "检验中":
            return f"设备「{entry.get('被检设备')}」当前为{entry['status']}，不能录入结论"
        if not result_text:
            return f"设备「{entry.get('被检设备')}」的检验结论为空"
        entry["实际检验日"] = date.today().isoformat()
        entry["检验结论"] = result_text
        if "不合格" in result_text:
            InspectionService._mark_unqualified(entry)
        else:
            entry["status"] = "合格"
            entry["pending"] = False
            entry["abnormal"] = False
        return ""

    @staticmethod
    def _mark_unqualified(entry: dict[str, Any]) -> None:
        entry["status"] = "不合格"
        entry["pending"] = True
        entry["abnormal"] = True

    @staticmethod
    def _fail(batch_no: str, message: str) -> dict[str, Any]:
        return {"ok": False, "retryable": False, "batch_no": batch_no, "message": message, "entries": []}

    @staticmethod
    def _serialize(row: dict[str, Any]) -> dict[str, Any]:
        """对外统一补齐 8 个展示字段；检验状态与内部 status 始终同源。"""
        item = dict(row)
        for field in ["检验编号", "被检设备", "检验类别", "检验机构", "计划检验日", "实际检验日", "检验结论"]:
            item.setdefault(field, "")
        item["检验状态"] = row.get("status", STATUS_ORDER[0])
        return item
