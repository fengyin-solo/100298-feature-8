"""检验机构查询：模拟对接特种设备检验机构目录。

真实项目里这里会换成外部接口/统一认证目录调用；当前实现用内存映射演示，
并通过环境变量 INSP_ORG_FAIL 模拟"机构目录暂时取不到"的故障，便于演示重试口径：
取不到时返回 None，由调用方决定重试，绝不允许把空机构写进检验结论。
"""
from __future__ import annotations

import os
import time

# 设备种类 -> 具备相应检验资质的机构
_ORG_DIRECTORY: dict[str, str] = {
    "锅炉": "市特种设备检验研究院",
    "压力容器": "市特种设备检验研究院",
    "压力管道": "市特种设备检验研究院",
    "电梯": "省特种设备检验检测中心",
    "起重机械": "省特种设备检验检测中心",
    "场(厂)内专用机动车辆": "市特种设备检验研究院",
}

# 机构目录偶发抖动时的内部重试参数
_MAX_ATTEMPTS = 2
_RETRY_INTERVAL = 0.2


class AgencyUnavailable(RuntimeError):
    """机构目录取不到时抛出，供上层决定是否让用户手动重试。"""


def _directory_failing() -> bool:
    return os.getenv("INSP_ORG_FAIL", "").strip() in {"1", "true", "TRUE", "yes"}


def resolve_agency(category: str) -> str | None:
    """按检验类别查机构；目录本身故障时抛 AgencyUnavailable，类别未配置时返回 None。"""
    for _ in range(_MAX_ATTEMPTS):
        if not _directory_failing():
            return _ORG_DIRECTORY.get(str(category or "").strip())
        time.sleep(_RETRY_INTERVAL)
    raise AgencyUnavailable("检验机构目录暂时取不到，请稍后重试")
