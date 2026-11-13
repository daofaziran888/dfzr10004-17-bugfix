"""代理池管理业务规则：状态流转、字段校验与筛选口径都收在这里。

关键约定：
- 生命周期状态（status）与健康结论（abnormal）是两条互不相干的线：
  人工停用只表示节点不再参与新请求，不会把节点标记成异常；
  只有网络失败（上报异常）才让节点进入异常并触发冷却。
- 状态只能按顺序流转：草稿 → 待校验 → 已启用 → 已停用；
  已停用节点恢复启用前必须重新提交校验，重复停用/重复恢复只生效一次。
- 健康校验与人工停用冲突时，以最近一次健康结论为准：
  提交校验代表一次新的健康校验（通过即清除异常与冷却），
  上报异常代表最近一次健康结论为网络失败。
"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "proxy"
REQUIRED_FIELDS = ["节点编号", "代理地址", "代理类型"]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
PENDING_STATUSES = {"草稿", "待校验"}

# 生命周期流转：动作 -> (允许的起始状态集合, 目标状态)
TRANSITIONS: dict[str, tuple[set[str], str]] = {
    "提交校验": ({"草稿", "已停用"}, "待校验"),
    "启用节点": ({"待校验"}, "已启用"),
    "停用节点": ({"已启用"}, "已停用"),
}
# 健康事件：不改变生命周期状态，只更新最近一次健康结论
HEALTH_ACTIONS = {"上报异常"}

# 起始状态不允许时的可读提示；未列出的组合走默认提示
BLOCKED_HINTS = {
    ("提交校验", "待校验"): "代理节点 {code} 已在待校验状态且最近一次健康结论为健康，无需重复提交",
    ("提交校验", "已启用"): "代理节点 {code} 当前已启用且最近一次健康结论为健康，无需重复提交校验",
    ("启用节点", "草稿"): "代理节点 {code} 尚未通过校验，请先提交校验",
    ("启用节点", "已停用"): "代理节点 {code} 已停用，恢复启用前请先提交校验重新校验",
    ("停用节点", "草稿"): "代理节点 {code} 当前为草稿，只有已启用节点才能停用",
    ("停用节点", "待校验"): "代理节点 {code} 尚未启用，只有已启用节点才能停用",
}


class ProxyService:
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
            rows = [row for row in rows if keyword in str(row.get("节点编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._decorate(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._decorate(entry) if entry is not None else None

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
        return self._decorate(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"代理节点 {entry_id} 不存在或已归档"
        code = str(entry.get("节点编号") or entry_id)
        if action in HEALTH_ACTIONS:
            return self._report_abnormal(entry, code)
        if action not in TRANSITIONS:
            return None, f"动作「{action}」不属于代理池管理可执行范围"
        sources, target = TRANSITIONS[action]
        current = str(entry.get("status") or "")
        abnormal = bool(entry.get("abnormal"))
        if action == "提交校验" and abnormal and current in {"待校验", "已启用"}:
            # 异常节点重新校验：生命周期状态不变，最近一次健康结论更新为健康
            entry["abnormal"] = False
            return self._decorate(entry), f"代理节点 {code} 已重新校验，最近一次健康结论：健康，异常冷却已解除"
        if current == target:
            return None, f"代理节点 {code} 已处于{target}状态，重复{action}不再生效"
        if action == "启用节点" and abnormal:
            return None, f"代理节点 {code} 最近一次健康结论为异常，需先提交校验重新校验"
        if current in sources:
            return self._apply_transition(entry, code, action, target)
        hint = BLOCKED_HINTS.get((action, current))
        if hint is not None:
            return None, hint.format(code=code)
        return None, f"代理节点 {code} 当前为「{current}」，不能执行「{action}」"

    def pool_summary(self) -> dict[str, Any]:
        """代理池选择口径：重试规则与采集计划都从这里看节点是否可参与请求。

        可参与请求 = 已启用且最近一次健康结论为健康；
        人工停用与网络异常（冷却中）的节点都不得被重试选中，
        其中异常节点构成待禁用清单，等待重新校验或人工处置。
        """
        rows = [self._decorate(row) for row in store.rows(MODULE)]
        selectable = [row for row in rows if row["selectable"]]
        abnormal = [row for row in rows if row["abnormal"]]
        disabled = [row for row in rows if row["status"] == "已停用"]
        total = len(rows)
        healthy = total - len(abnormal)
        availability = round(healthy / total * 100, 1) if total else 0.0
        return {
            "total": total,
            "by_status": {name: sum(1 for row in rows if row["status"] == name) for name in STATUS_ORDER},
            "abnormal": len(abnormal),
            "selectable": len(selectable),
            "availability": availability,
            "selectable_nodes": [self._brief(row) for row in selectable],
            "pending_disable": [self._brief(row, "网络异常（冷却中）") for row in abnormal],
            "disabled_nodes": [self._brief(row, "人工停用") for row in disabled],
        }

    def _apply_transition(
        self, entry: dict[str, Any], code: str, action: str, target: str
    ) -> tuple[dict[str, Any], str]:
        entry["status"] = target
        entry["pending"] = target in PENDING_STATUSES
        if action == "提交校验":
            # 提交校验代表一次新的健康校验：通过后最近一次健康结论为健康
            entry["abnormal"] = False
            return self._decorate(entry), f"代理节点 {code} 已提交校验，最近一次健康结论：健康"
        if action == "启用节点":
            return self._decorate(entry), f"代理节点 {code} 已启用，可参与新请求"
        # 停用节点：只改变生命周期，不触碰健康结论，停用不等于故障
        return self._decorate(entry), f"代理节点 {code} 已停用，不再参与新请求（健康结论不受影响）"

    def _report_abnormal(self, entry: dict[str, Any], code: str) -> tuple[dict[str, Any] | None, str]:
        if entry.get("status") == "草稿":
            return None, f"代理节点 {code} 尚未启用，不参与请求，无需上报异常"
        if entry.get("abnormal"):
            return None, f"代理节点 {code} 已处于异常冷却中，重复上报不再生效"
        entry["abnormal"] = True
        return self._decorate(entry), f"代理节点 {code} 网络失败，已标记异常并进入冷却"

    @staticmethod
    def _decorate(row: dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        abnormal = bool(item.get("abnormal"))
        item["cooldown"] = abnormal
        item["selectable"] = item.get("status") == "已启用" and not abnormal
        return item

    @staticmethod
    def _brief(row: dict[str, Any], reason: str | None = None) -> dict[str, Any]:
        brief = {"id": row.get("id"), "节点编号": row.get("节点编号"), "status": row.get("status")}
        if reason is not None:
            brief["原因"] = reason
        return brief
