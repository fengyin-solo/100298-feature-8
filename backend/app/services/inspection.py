"""定期检验业务规则：批量安排检验、状态流转、结论录入与候选设备口径都收在这里。

核心约定：
- 一次勾选多台设备 → 一个批次（batch_key 幂等，重复提交只认第一次）；
- 每台设备生成一条独立检验任务，逐台带出「被检设备 / 设备编号 / 检验类别」；
- 合格设备不再进入后续批次的候选范围；
- 检验机构取不到时整批保持待安排，可重试，绝不把空机构带进结论；
- 结论逐台录入，按任务 id 绑定，不合格的那台可单独退回整改。
"""
from __future__ import annotations

from typing import Any

from app.services import agency
from app.store import store

MODULE = "inspection"
REGISTER_MODULE = "register"
REQUIRED_FIELDS = ["检验编号", "被检设备", "检验类别"]
STATUS_ORDER = ["待检验", "检验中", "合格", "不合格"]
ACTION_RULES = {"安排检验": "检验中", "录入结论": "合格", "下达整改": "不合格"}
NEGATIVE_ACTIONS = ["下达整改"]

# 已经合格的设备不再自动排进下一批；其余在办状态也不允许重复开批
_BUSY_STATUSES = {"待检验", "检验中", "不合格"}
_PASSED_STATUS = "合格"


class InspectionService:
    # ---------- 列表与统计 ----------
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        batch_no: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._filter_rows(keyword=keyword, status=status, batch_no=batch_no)
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def stats(self) -> dict[str, int]:
        rows = store.rows(MODULE)
        return {
            "待检验设备": sum(1 for row in rows if row.get("status") == "待检验"),
            "检验中设备": sum(1 for row in rows if row.get("status") == "检验中"),
            "合格设备": sum(1 for row in rows if row.get("status") == "合格"),
            "不合格设备": sum(1 for row in rows if row.get("status") == "不合格"),
        }

    def _filter_rows(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        batch_no: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [
                row
                for row in rows
                if keyword in str(row.get("检验编号", ""))
                or keyword in str(row.get("被检设备", ""))
                or keyword in str(row.get("设备编号", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if batch_no:
            rows = [row for row in rows if row.get("批次号") == batch_no]
        return rows

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    # ---------- 候选设备 ----------
    def candidate_devices(self, keyword: str | None = None) -> list[dict[str, Any]]:
        """可纳入新批次的设备：已登记、且没有合格/在办的检验任务。"""
        occupied: dict[int, str] = {}
        for task in store.rows(MODULE):
            device_id = task.get("设备登记id")
            if device_id is None:
                continue
            status = str(task.get("status") or "")
            if status == _PASSED_STATUS or status in _BUSY_STATUSES:
                occupied[int(device_id)] = status

        candidates: list[dict[str, Any]] = []
        for device in store.rows(REGISTER_MODULE):
            if str(device.get("status") or "") != "已登记":
                continue
            device_id = int(device.get("id", 0))
            if device_id in occupied:
                continue
            item = {
                "设备登记id": device_id,
                "设备编号": device.get("设备编号", ""),
                "被检设备": device.get("设备名称", ""),
                "检验类别": device.get("设备种类", ""),
                "使用单位": device.get("使用单位", ""),
                "安装地点": device.get("安装地点", ""),
            }
            if keyword and keyword not in str(item["设备编号"]) and keyword not in str(item["被检设备"]):
                continue
            candidates.append(item)
        return candidates

    # ---------- 批量安排 ----------
    def arrange_batch(
        self,
        device_ids: list[int],
        *,
        batch_key: str | None = None,
        planned_date: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """按勾选的设备一次性安排检验；同一 batch_key 重复提交只认第一次。"""
        ids = self._dedupe_ids(device_ids)
        if not ids:
            return None, "请至少勾选一台设备后再安排检验"
        if len(ids) > 100:
            return None, "单次最多安排 100 台设备，请分批提交"

        rows = store.rows(MODULE)

        # 幂等：同一批次键已提交过，直接回第一次的批次，不再重复建任务
        if batch_key:
            existing = next((row for row in rows if row.get("批次键") == batch_key), None)
            if existing is not None:
                return self._batch_view(str(existing["批次号"])), "该批次已提交过，已按第一次提交处理"

        devices = self._load_devices(ids)
        if len(devices) != len(ids):
            found = {int(device["id"]) for device in devices}
            missing = [str(device_id) for device_id in ids if device_id not in found]
            return None, f"设备台账中找不到或不可检：{('、'.join(missing))}"

        blocked = self._blocked_devices(devices)
        if blocked:
            return None, "以下设备已合格或已有在办检验，未重复安排：" + "、".join(blocked)

        batch_no = self._next_batch_no()
        next_id = store.next_id(MODULE)

        # 先把整批任务落为「待检验」，再解析机构；机构目录抖动时可整批重试，不留半成品结论
        created: list[dict[str, Any]] = []
        for index, device in enumerate(devices, start=1):
            entry = {
                "id": next_id + index - 1,
                "检验编号": f"{batch_no}-{index:03d}",
                "批次号": batch_no,
                "批次键": batch_key or batch_no,
                "设备登记id": int(device["id"]),
                "被检设备": device.get("设备名称", ""),
                "设备编号": device.get("设备编号", ""),
                "检验类别": device.get("设备种类", ""),
                "检验机构": "",
                "计划检验日": planned_date or "",
                "实际检验日": "",
                "检验结论": "",
                "检验状态": "待检验",
                "status": "待检验",
                "pending": True,
                "abnormal": False,
                "agency_pending": True,
            }
            rows.append(entry)
            created.append(entry)

        return self._activate_batch(batch_no)

    def retry_arrange(self, batch_no: str) -> tuple[dict[str, Any] | None, str]:
        """机构目录取不到后重试：把待安排批次重新解析机构并转入检验中。"""
        rows = store.rows(MODULE)
        members = [row for row in rows if row.get("批次号") == batch_no]
        if not members:
            return None, f"批次 {batch_no} 不存在或已归档"
        if not any(row.get("agency_pending") for row in members):
            return self._batch_view(batch_no), "该批次已安排检验，无需重试"
        return self._activate_batch(batch_no)

    def _activate_batch(self, batch_no: str) -> tuple[dict[str, Any], str]:
        members = [row for row in store.rows(MODULE) if row.get("批次号") == batch_no]
        try:
            for entry in members:
                agency_name = agency.resolve_agency(str(entry.get("检验类别") or ""))
                if not agency_name:
                    return self._batch_view(batch_no), (
                        f"检验类别「{entry.get('检验类别')}」未配置承检机构，请补充机构目录后重试"
                    )
                entry["检验机构"] = agency_name
        except agency.AgencyUnavailable as exc:
            # 整批保持待检验、机构留空但不落任何结论，等用户点重试
            for entry in members:
                entry["检验机构"] = ""
                entry["agency_pending"] = True
            return self._batch_view(batch_no), f"{exc}；本批尚未安排，可直接重试"

        for entry in members:
            entry["status"] = "检验中"
            entry["检验状态"] = "检验中"
            entry["pending"] = True
            entry["abnormal"] = False
            entry["agency_pending"] = False
        return self._batch_view(batch_no), f"批次 {batch_no} 已安排 {len(members)} 台设备检验"

    def _batch_view(self, batch_no: str) -> dict[str, Any]:
        members = [row for row in store.rows(MODULE) if row.get("批次号") == batch_no]
        return {
            "批次号": batch_no,
            "总数": len(members),
            "待检验": sum(1 for row in members if row.get("status") == "待检验"),
            "检验中": sum(1 for row in members if row.get("status") == "检验中"),
            "合格": sum(1 for row in members if row.get("status") == "合格"),
            "不合格": sum(1 for row in members if row.get("status") == "不合格"),
            "items": members,
        }

    # ---------- 逐台结论与整改 ----------
    def record_conclusion(
        self,
        entry_id: int,
        *,
        conclusion: str,
        actual_date: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"检验任务 {entry_id} 不存在或已归档"
        conclusion = str(conclusion or "").strip()
        if not conclusion:
            return None, "检验结论不能为空，请录入实际检验结果"
        if entry.get("status") == "合格":
            return None, "该设备已录入合格结论，无需重复录入"
        if entry.get("status") == "不合格":
            return None, "该设备已退回整改，请走整改复查流程"
        if not str(entry.get("检验机构") or "").strip():
            return None, "检验机构尚未取到，请先重试安排检验，不能在机构为空时落结论"

        entry["检验结论"] = conclusion
        entry["status"] = "合格"
        entry["检验状态"] = "合格"
        entry["pending"] = False
        entry["abnormal"] = False
        if actual_date:
            entry["实际检验日"] = actual_date
        return entry, f"设备 {entry.get('被检设备')} 的检验结论已录入（合格）"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        # 「下达整改」只作用于单台设备；其余历史动作沿用原状态机
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"检验任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于定期检验可执行范围"
        if action == "下达整改":
            if entry.get("status") != "检验中":
                return None, "只有检验中的设备可以退回整改"
            entry["status"] = "不合格"
            entry["检验状态"] = "不合格"
            entry["pending"] = True
            entry["abnormal"] = True
            return entry, f"设备 {entry.get('被检设备')} 已单独退回整改"

        target = ACTION_RULES[action]
        entry["status"] = target
        entry["检验状态"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"检验任务已{action}"

    # ---------- 兼容旧的单条登记 ----------
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": store.next_id(MODULE)}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["设备编号"] = values.get("设备编号", "")
        entry["检验机构"] = values.get("检验机构", "")
        entry["计划检验日"] = values.get("计划检验日", "")
        entry["实际检验日"] = ""
        entry["检验结论"] = ""
        entry["检验状态"] = STATUS_ORDER[0]
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    # ---------- 内部工具 ----------
    @staticmethod
    def _dedupe_ids(device_ids: list[Any]) -> list[int]:
        result: list[int] = []
        for raw in device_ids or []:
            try:
                device_id = int(raw)
            except (TypeError, ValueError):
                continue
            if device_id > 0 and device_id not in result:
                result.append(device_id)
        return result

    def _load_devices(self, ids: list[int]) -> list[dict[str, Any]]:
        devices = [store.find(REGISTER_MODULE, device_id) for device_id in ids]
        return [device for device in devices if device is not None]

    @staticmethod
    def _blocked_devices(devices: list[dict[str, Any]]) -> list[str]:
        occupied: dict[int, str] = {}
        for task in store.rows(MODULE):
            device_id = task.get("设备登记id")
            if device_id is None:
                continue
            status = str(task.get("status") or "")
            if status == _PASSED_STATUS or status in _BUSY_STATUSES:
                occupied[int(device_id)] = status
        blocked: list[str] = []
        for device in devices:
            status = occupied.get(int(device["id"]))
            if status:
                blocked.append(f"{device.get('设备编号')}（{status}）")
        return blocked

    @staticmethod
    def _next_batch_no() -> str:
        existing = [
            str(row.get("批次号") or "")
            for row in store.rows(MODULE)
            if str(row.get("批次号") or "").startswith("BATCH-")
        ]
        max_seq = 0
        for batch_no in existing:
            tail = batch_no.rsplit("-", 1)[-1]
            if tail.isdigit():
                max_seq = max(max_seq, int(tail))
        return f"BATCH-{max_seq + 1:04d}"
