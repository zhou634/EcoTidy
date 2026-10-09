# -*- coding: utf-8 -*-
"""
历史任务对接层（开发方案 第六章 阶段 7）

职责：
    把「本地数据库 + 任务快照」与「AppState」串起来：
        · 清洗/计算/导出后自动保存任务记录与快照；
        · 从快照恢复现场（数据表、日志、图片全部回到 AppState）；
        · 删除、重命名任务。

为什么单独一层：database/local_db.py 只认数据表和路径，不认识 AppState；
界面只认识 AppState。把"两者之间的翻译"集中在这里，好处是
    · 数据库层可以脱离界面单独测试；
    · 界面层不必知道快照里有哪些文件；
    · 自动保存的时机与去重逻辑只有一个地方需要维护。

自动保存的去重规则：
    同一次分析的多次操作（清洗 → 计算 → 导出）必须落在**同一条**任务记录上，
    否则用户会看到一串内容几乎一样的历史任务。
    因此任务 ID 记在 AppState 的 task_meta["task_id"] 里：
    已有 ID 就更新那条记录与快照，没有才新建。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from core.app_state import AppState
from database import local_db


class TaskError(Exception):
    """历史任务操作失败：信息为中文，可直接展示给用户。"""


# AppState 里保存当前任务 ID 与任务名的键（放在 task_meta 字典内，不新增数据槽）
META_TASK_ID = "task_id"
META_TASK_NAME = "task_name"


def current_task_id(state: AppState) -> str:
    """读取当前任务 ID（没有则返回空串）。"""
    return str(state.get_meta().get(META_TASK_ID, "") or "")


def _task_meta(state: AppState) -> Dict[str, Any]:
    """读取 task_meta 槽（该槽是元信息字典，经 AppState 的专用接口读写）。

    说明：task_meta 在设计上就是"元信息字典"槽，不是数据表也不是文本，
    因此 AppState 为它提供了 get_meta/set_meta/update_meta 三个接口。
    """
    return state.get_meta()


def _write_task_meta(state: AppState, **updates: Any) -> None:
    """更新 task_meta（不新增数据槽）。"""
    state.update_meta(**updates)


def current_task_name(state: AppState) -> str:
    """读取当前任务名（没有则给出一个基于时间的默认名）。"""
    meta = _task_meta(state)
    name = str(meta.get(META_TASK_NAME, "") or "")
    if name:
        return name
    data_type = state.get_text("data_type") or "分析"
    import time

    return "{0}_{1}".format(data_type, time.strftime("%Y%m%d_%H%M"))


def _row_count(state: AppState, key: str) -> int:
    frame = state.get_table(key)
    return int(len(frame)) if frame is not None else 0


def save_current_task(state: AppState, name: Optional[str] = None,
                      initialize: bool = False) -> Optional[local_db.TaskRecord]:
    """把当前 AppState 里的成果保存成任务记录 + 快照。

    参数：
        name       ：任务名（留空则沿用当前任务名或自动生成）
        initialize ：是否强制新建任务（"新建任务"入口用），默认更新当前任务
    返回：写入后的任务记录；当前没有任何数据时返回 None。
    """
    if not any(state.has(key) for key in ("raw", "clean", "index_row", "index_summary")):
        return None

    task_name = (name or "").strip() or current_task_name(state)
    task_id = "" if initialize else current_task_id(state)
    data_type = state.get_text("data_type")
    raw_rows = _row_count(state, "raw")
    clean_rows = _row_count(state, "clean")
    figures = state.get_list("figures")
    meta = _task_meta(state)
    source_files = meta.get("source_files") or []
    params = {key: meta.get(key) for key in ("clean_params",) if key in meta}

    try:
        if task_id:
            snapshot_dir = local_db.save_snapshot(
                task_id, raw=state.get_table("raw"), clean=state.get_table("clean"),
                row_index=state.get_table("index_row"),
                summary_index=state.get_table("index_summary"),
                clean_log=state.get_text("clean_log"), data_type=data_type,
                figures=figures, params=params, source_files=source_files)
            local_db.update_task(task_id, name=task_name, raw_rows=raw_rows,
                                 clean_rows=clean_rows, data_type=data_type,
                                 snapshot_dir=str(snapshot_dir))
            record = local_db.get_task(task_id)
        else:
            # 先建记录拿到 ID，再按该 ID 落盘快照
            record = local_db.create_task(task_name, raw_rows=raw_rows,
                                          clean_rows=clean_rows, data_type=data_type)
            snapshot_dir = local_db.save_snapshot(
                record.task_id, raw=state.get_table("raw"), clean=state.get_table("clean"),
                row_index=state.get_table("index_row"),
                summary_index=state.get_table("index_summary"),
                clean_log=state.get_text("clean_log"), data_type=data_type,
                figures=figures, params=params, source_files=source_files)
            local_db.update_task(record.task_id, snapshot_dir=str(snapshot_dir))
            record = local_db.get_task(record.task_id)
    except local_db.DatabaseError as exc:
        # 保存历史任务失败不应影响用户当前的分析工作，只提示不中断
        raise TaskError(str(exc))

    if record is not None:
        _write_task_meta(state, **{META_TASK_ID: record.task_id, META_TASK_NAME: record.name})
    return record


def list_history(keyword: str = "") -> List[local_db.TaskRecord]:
    """查询历史任务列表（按时间倒序，可按关键词搜索）。"""
    try:
        return local_db.list_tasks(keyword=keyword)
    except local_db.DatabaseError as exc:
        raise TaskError(str(exc))


def load_task_into_state(state: AppState, task_id: str) -> local_db.TaskRecord:
    """把某个历史任务恢复到 AppState（数据表、日志、图片、任务名）。

    返回恢复后的任务记录；快照失效或损坏时抛 TaskError（信息可直接展示）。
    """
    try:
        record = local_db.get_task(task_id)
    except local_db.DatabaseError as exc:
        raise TaskError(str(exc))
    if record is None:
        raise TaskError("找不到这条历史任务（可能已被删除）。\n\n怎么办：刷新列表后重试。")

    try:
        snapshot = local_db.load_snapshot(record.snapshot_dir)
    except local_db.DatabaseError as exc:
        raise TaskError(str(exc))

    tables: Dict[str, Any] = snapshot["tables"]
    # 先清空再恢复，避免上一个任务的数据残留（例如上个任务有指标表、这个没有）
    state.reset()
    for key in ("raw", "clean", "index_row", "index_summary"):
        frame = tables.get(key)
        if frame is not None and len(frame):
            state.set_table(key, frame, meta={"from_task": record.task_id})
    state.set_text("clean_log", snapshot.get("clean_log", ""))
    state.set_text("data_type", record.data_type or snapshot.get("meta", {}).get("data_type", ""))
    figures = [str(path) for path in snapshot.get("figures", [])]
    if figures:
        state.set_list("figures", figures)

    _write_task_meta(state, **{META_TASK_ID: record.task_id, META_TASK_NAME: record.name,
                               "restored_at": snapshot.get("meta", {}).get("saved_at", "")})
    return record


def rename_task(task_id: str, new_name: str, state: Optional[AppState] = None) -> bool:
    """重命名历史任务；若改的正是当前任务，同步更新 task_meta 里的名字。"""
    try:
        changed = local_db.rename_task(task_id, new_name)
    except local_db.DatabaseError as exc:
        raise TaskError(str(exc))
    if changed and state is not None and current_task_id(state) == task_id:
        _write_task_meta(state, **{META_TASK_NAME: new_name.strip()})
    return changed


def delete_task(task_id: str, state: Optional[AppState] = None) -> bool:
    """删除历史任务（连同快照目录）。若删的是当前任务，清掉 AppState 里的任务标记。"""
    try:
        removed = local_db.delete_task(task_id)
    except local_db.DatabaseError as exc:
        raise TaskError(str(exc))
    if removed and state is not None and current_task_id(state) == task_id:
        meta = state.get_meta()
        meta.pop(META_TASK_ID, None)
        meta.pop(META_TASK_NAME, None)
        state.set_meta(meta)
    return removed


def ensure_database() -> Path:
    """确保数据库已建好（main.py 启动时调用）。"""
    try:
        return local_db.init_database()
    except local_db.DatabaseError as exc:
        raise TaskError(str(exc))


def snapshot_files(record: local_db.TaskRecord) -> List[Path]:
    """列出某个任务快照目录里的文件（历史任务详情展示用）。"""
    directory = Path(record.snapshot_dir)
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.rglob("*") if path.is_file())
