"""异常重试管理业务规则：状态流转、字段校验与筛选口径都收在这里。

与代理池管理同一套约定：人工停用规则只表示规则不再参与新请求，
不会被标记成异常；状态只能按顺序流转，重复停用/重复恢复只生效一次。
"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "retry"
REQUIRED_FIELDS = ["规则编号", "异常类型", "重试上限"]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
PENDING_STATUSES = {"草稿", "待校验"}

# 生命周期流转：动作 -> (允许的起始状态集合, 目标状态)
TRANSITIONS: dict[str, tuple[set[str], str]] = {
    "提交校验": ({"草稿", "已停用"}, "待校验"),
    "启用规则": ({"待校验"}, "已启用"),
    "停用规则": ({"已启用"}, "已停用"),
}

# 起始状态不允许时的可读提示；未列出的组合走默认提示
BLOCKED_HINTS = {
    ("提交校验", "待校验"): "重试规则 {code} 已在待校验状态，无需重复提交",
    ("提交校验", "已启用"): "重试规则 {code} 当前已启用，无需重复提交校验",
    ("启用规则", "草稿"): "重试规则 {code} 尚未通过校验，请先提交校验",
    ("启用规则", "已停用"): "重试规则 {code} 已停用，恢复启用前请先提交校验重新校验",
    ("停用规则", "草稿"): "重试规则 {code} 当前为草稿，只有已启用规则才能停用",
    ("停用规则", "待校验"): "重试规则 {code} 尚未启用，只有已启用规则才能停用",
}


class RetryService:
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
            rows = [row for row in rows if keyword in str(row.get("规则编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"重试规则 {entry_id} 不存在或已归档"
        code = str(entry.get("规则编号") or entry_id)
        if action not in TRANSITIONS:
            return None, f"动作「{action}」不属于异常重试管理可执行范围"
        sources, target = TRANSITIONS[action]
        current = str(entry.get("status") or "")
        if current == target:
            return None, f"重试规则 {code} 已处于{target}状态，重复{action}不再生效"
        if current in sources:
            entry["status"] = target
            entry["pending"] = target in PENDING_STATUSES
            if action == "提交校验":
                return entry, f"重试规则 {code} 已提交校验"
            if action == "启用规则":
                return entry, f"重试规则 {code} 已启用"
            # 停用规则只是退出新请求，不代表规则异常，健康结论保持原样
            return entry, f"重试规则 {code} 已停用，不再参与新请求"
        hint = BLOCKED_HINTS.get((action, current))
        if hint is not None:
            return None, hint.format(code=code)
        return None, f"重试规则 {code} 当前为「{current}」，不能执行「{action}」"
