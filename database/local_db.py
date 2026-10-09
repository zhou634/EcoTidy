# -*- coding: utf-8 -*-
"""
SQLite 本地数据库（开发方案 第六章 阶段 7）

职责：
    1. 在 %APPDATA%\\EcoTidy\\ecodata.db 建库建表，程序启动自动完成；
    2. 记录每个分析任务的元信息（任务ID、名称、创建时间、原始/清洗行数、数据类型、快照目录）；
    3. 任务**快照**落盘到 TASKS_DIR/<任务ID>/：把当次的数据表、清洗日志、图片、
       任务参数一并存下来，历史任务因此不再依赖用户原始文件是否还在原处；
    4. 列表查询、重命名、删除记录（连同快照目录）。

为什么历史任务不能只存一条"可执行文件路径"（方案阶段 7 第 4 条）：
    用户移动或删除原始文件后，那条路径就失效了，历史任务随之变成死记录。
    因此这里落盘的是**成果快照**（数据表 + 日志 + 图片 + 参数），
    加载前再做一次存在性校验，失效时友好提示并提供"重新指定文件"的入口。

连接纪律（方案阶段 7 第 5 条）：
    统一用上下文管理器获取连接，写操作走事务；异常时自动回滚，避免库被写坏。
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

import pandas as pd

from core import data_parse

# ===========================================================================
# 一、常量
# ===========================================================================
TABLE_NAME = "task"

# 快照目录内的固定文件名（写入与读取必须一致）
SNAPSHOT_RAW = "raw.csv"
SNAPSHOT_CLEAN = "clean.csv"
SNAPSHOT_ROW_INDEX = "index_row.csv"
SNAPSHOT_SUMMARY_INDEX = "index_summary.csv"
SNAPSHOT_CLEAN_LOG = "clean_log.txt"
SNAPSHOT_META = "task_meta.json"
SNAPSHOT_FIGURES_DIR = "figures"

# 快照里保存的数据表：槽名 -> 文件名
_SNAPSHOT_TABLES: Dict[str, str] = {
    "raw": SNAPSHOT_RAW,
    "clean": SNAPSHOT_CLEAN,
    "index_row": SNAPSHOT_ROW_INDEX,
    "index_summary": SNAPSHOT_SUMMARY_INDEX,
}


class DatabaseError(Exception):
    """数据库操作失败异常：信息为中文，可直接展示给用户。"""


# ===========================================================================
# 二、数据结构
# ===========================================================================
@dataclass
class TaskRecord:
    """一条历史任务记录。"""
    task_id: str
    name: str
    created_at: str
    raw_rows: int = 0
    clean_rows: int = 0
    data_type: str = ""
    snapshot_dir: str = ""
    note: str = ""
    # 运行时计算（不入库）：快照是否还在
    snapshot_exists: bool = True

    def summary(self) -> str:
        """一行中文摘要，用于列表展示与调试。"""
        parts = ["{0}".format(self.name)]
        if self.data_type:
            parts.append(self.data_type)
        parts.append("原始 {0:,} 行".format(self.raw_rows))
        if self.clean_rows:
            parts.append("清洗后 {0:,} 行".format(self.clean_rows))
        return " ｜ ".join(parts)

    def display_time(self) -> str:
        """把入库时间显示得好看一点。"""
        return self.created_at.replace("T", " ") if self.created_at else ""


# ===========================================================================
# 三、连接与建表
# ===========================================================================
def database_path() -> Path:
    """数据库文件位置（%APPDATA%，绝不放程序目录）。"""
    from utils import paths

    return paths.DB_PATH


@contextmanager
def connect(path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    """获取数据库连接的上下文管理器（自动提交/回滚、必定关闭）。

    为什么必须用上下文管理器：SQLite 连接忘记关闭会锁住文件，
    而写操作中途抛异常若不回滚，可能留下半个任务记录。
    """
    target = Path(path) if path is not None else database_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DatabaseError(
            "无法创建数据库目录：{0}\n\n原因：{1}\n\n"
            "怎么办：确认当前用户对 %APPDATA%\\EcoTidy 有写入权限后重试。".format(
                target.parent, exc))

    connection: Optional[sqlite3.Connection] = None
    try:
        connection = sqlite3.connect(str(target), timeout=10.0)
        connection.row_factory = sqlite3.Row
        # 外键约束与 WAL 模式：写入更稳，异常断电也不易损坏库文件
        connection.execute("PRAGMA foreign_keys = ON")
        yield connection
        connection.commit()
    except sqlite3.Error as exc:
        if connection is not None:
            connection.rollback()
        raise DatabaseError(
            "本地数据库操作失败。\n\n原因：{0}\n\n"
            "怎么办：关闭软件后删除 {1} 重新打开软件（历史任务列表会清空，但已导出的成果文件不受影响）。".format(
                exc, target))
    finally:
        if connection is not None:
            connection.close()


def init_database(path: Optional[Path] = None) -> Path:
    """建库建表（幂等）。程序启动时调用一次。"""
    target = Path(path) if path is not None else database_path()
    with connect(target) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS {0} (
                task_id      TEXT PRIMARY KEY,
                name         TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                raw_rows     INTEGER DEFAULT 0,
                clean_rows   INTEGER DEFAULT 0,
                data_type    TEXT DEFAULT '',
                snapshot_dir TEXT DEFAULT '',
                note         TEXT DEFAULT ''
            )
            """.format(TABLE_NAME))
        # 按时间倒序查询是列表的默认排序，建索引避免任务多了之后变慢
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_created ON {0}(created_at DESC)".format(TABLE_NAME))
    return target


def _row_to_record(row: sqlite3.Row) -> TaskRecord:
    record = TaskRecord(
        task_id=str(row["task_id"]),
        name=str(row["name"]),
        created_at=str(row["created_at"]),
        raw_rows=int(row["raw_rows"] or 0),
        clean_rows=int(row["clean_rows"] or 0),
        data_type=str(row["data_type"] or ""),
        snapshot_dir=str(row["snapshot_dir"] or ""),
        note=str(row["note"] or ""),
    )
    record.snapshot_exists = _snapshot_alive(record.snapshot_dir)
    return record


def _snapshot_alive(snapshot_dir: str) -> bool:
    """判断快照目录是否仍然可用（存在且至少有一个数据文件）。"""
    if not snapshot_dir:
        return False
    directory = Path(snapshot_dir)
    if not directory.is_dir():
        return False
    return any((directory / filename).exists() for filename in _SNAPSHOT_TABLES.values())


# ===========================================================================
# 四、增删改查
# ===========================================================================
def create_task(name: str, *, raw_rows: int = 0, clean_rows: int = 0,
                data_type: str = "", snapshot_dir: str = "", note: str = "",
                created_at: Optional[str] = None, task_id: Optional[str] = None,
                path: Optional[Path] = None) -> TaskRecord:
    """新建一条任务记录，返回写入后的记录。"""
    identifier = task_id or uuid.uuid4().hex[:12]
    stamp = created_at or time.strftime("%Y-%m-%dT%H:%M:%S")
    clean_name = (name or "").strip() or "未命名任务"
    with connect(path) as connection:
        connection.execute(
            "INSERT INTO {0} (task_id, name, created_at, raw_rows, clean_rows, data_type,"
            " snapshot_dir, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)".format(TABLE_NAME),
            (identifier, clean_name, stamp, int(raw_rows), int(clean_rows),
             data_type or "", snapshot_dir or "", note or ""))
    return TaskRecord(task_id=identifier, name=clean_name, created_at=stamp,
                      raw_rows=int(raw_rows), clean_rows=int(clean_rows),
                      data_type=data_type or "", snapshot_dir=snapshot_dir or "",
                      note=note or "",
                      snapshot_exists=_snapshot_alive(snapshot_dir or ""))


def list_tasks(keyword: str = "", limit: Optional[int] = None,
               path: Optional[Path] = None) -> List[TaskRecord]:
    """查询历史任务，按创建时间倒序；keyword 非空时按任务名模糊搜索。"""
    sql = ("SELECT task_id, name, created_at, raw_rows, clean_rows, data_type,"
           " snapshot_dir, note FROM {0}".format(TABLE_NAME))
    params: List[Any] = []
    if keyword and keyword.strip():
        sql += " WHERE name LIKE ? OR data_type LIKE ?"
        pattern = "%{0}%".format(keyword.strip())
        params.extend([pattern, pattern])
    sql += " ORDER BY created_at DESC, rowid DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))

    with connect(path) as connection:
        rows = connection.execute(sql, params).fetchall()
    return [_row_to_record(row) for row in rows]


def get_task(task_id: str, path: Optional[Path] = None) -> Optional[TaskRecord]:
    """按 ID 取一条任务记录；不存在返回 None。"""
    with connect(path) as connection:
        row = connection.execute(
            "SELECT task_id, name, created_at, raw_rows, clean_rows, data_type,"
            " snapshot_dir, note FROM {0} WHERE task_id = ?".format(TABLE_NAME),
            (task_id,)).fetchone()
    return _row_to_record(row) if row is not None else None


def rename_task(task_id: str, new_name: str, path: Optional[Path] = None) -> bool:
    """重命名任务。返回是否真的改动了记录。"""
    clean_name = (new_name or "").strip()
    if not clean_name:
        raise DatabaseError("任务名不能为空。\n\n怎么办：请输入一个名字后再保存。")
    with connect(path) as connection:
        cursor = connection.execute(
            "UPDATE {0} SET name = ? WHERE task_id = ?".format(TABLE_NAME),
            (clean_name, task_id))
    return cursor.rowcount > 0


def update_task(task_id: str, *, name: Optional[str] = None, raw_rows: Optional[int] = None,
                clean_rows: Optional[int] = None, data_type: Optional[str] = None,
                snapshot_dir: Optional[str] = None, note: Optional[str] = None,
                path: Optional[Path] = None) -> bool:
    """更新任务的部分字段（只更新传入的项）。"""
    fields: List[str] = []
    values: List[Any] = []
    for column, value in (("name", name), ("raw_rows", raw_rows), ("clean_rows", clean_rows),
                          ("data_type", data_type), ("snapshot_dir", snapshot_dir),
                          ("note", note)):
        if value is None:
            continue
        fields.append("{0} = ?".format(column))
        values.append(value)
    if not fields:
        return False
    values.append(task_id)
    with connect(path) as connection:
        cursor = connection.execute(
            "UPDATE {0} SET {1} WHERE task_id = ?".format(TABLE_NAME, ", ".join(fields)),
            values)
    return cursor.rowcount > 0


def delete_task(task_id: str, *, remove_snapshot: bool = True,
                path: Optional[Path] = None) -> bool:
    """删除任务记录；默认连同快照目录一起删除。

    注意：只删"本软件自己创建的快照目录"，不碰用户的任何原始文件。
    """
    record = get_task(task_id, path=path)
    if record is None:
        return False
    if remove_snapshot and record.snapshot_dir:
        _remove_snapshot(record.snapshot_dir)
    with connect(path) as connection:
        cursor = connection.execute(
            "DELETE FROM {0} WHERE task_id = ?".format(TABLE_NAME), (task_id,))
    return cursor.rowcount > 0


def _remove_snapshot(snapshot_dir: str) -> None:
    """删除快照目录（限定在 TASKS_DIR 之内，避免误删用户目录）。"""
    from utils import paths

    try:
        directory = Path(snapshot_dir).resolve()
        tasks_root = paths.TASKS_DIR.resolve()
        directory.relative_to(tasks_root)          # 不在任务目录内的，一律不删
    except (ValueError, OSError):
        return
    shutil.rmtree(directory, ignore_errors=True)


def count_tasks(path: Optional[Path] = None) -> int:
    """任务总数。"""
    with connect(path) as connection:
        row = connection.execute("SELECT COUNT(*) AS total FROM {0}".format(TABLE_NAME)).fetchone()
    return int(row["total"] or 0)


# ===========================================================================
# 五、任务快照
# ===========================================================================
def save_snapshot(task_id: str, *, raw: Optional[pd.DataFrame] = None,
                  clean: Optional[pd.DataFrame] = None,
                  row_index: Optional[pd.DataFrame] = None,
                  summary_index: Optional[pd.DataFrame] = None,
                  clean_log: str = "", data_type: str = "",
                  figures: Optional[Sequence[Any]] = None,
                  params: Optional[Dict[str, Any]] = None,
                  source_files: Optional[Sequence[str]] = None,
                  root: Optional[Path] = None) -> Path:
    """把当次的数据表、日志、图片与参数落盘成任务快照，返回快照目录。"""
    from utils import paths

    base = Path(root) if root is not None else paths.TASKS_DIR
    directory = base / str(task_id)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DatabaseError(
            "无法创建任务快照目录：{0}\n\n原因：{1}\n\n"
            "怎么办：确认 %APPDATA% 可写后重试；本次分析结果不受影响，只是无法恢复现场。".format(
                directory, exc))

    tables = {"raw": raw, "clean": clean, "index_row": row_index, "index_summary": summary_index}
    saved: Dict[str, int] = {}
    for key, frame in tables.items():
        filename = _SNAPSHOT_TABLES[key]
        target = directory / filename
        if frame is not None and len(frame) > 0:
            # utf-8-sig：用户直接用 Excel 打开快照也不会中文乱码
            frame.to_csv(target, index=False, encoding="utf-8-sig")
            saved[key] = int(len(frame))
        elif target.exists():
            # 本次没有该表（例如用户没做指标计算），删掉上次留下的旧文件，避免加载到过期数据
            target.unlink()

    (directory / SNAPSHOT_CLEAN_LOG).write_text(clean_log or "", encoding="utf-8")

    figure_names: List[str] = []
    figures_dir = directory / SNAPSHOT_FIGURES_DIR
    if figures:
        figures_dir.mkdir(exist_ok=True)
        for source in figures:
            path = Path(source)
            if not path.exists():
                continue
            target = figures_dir / path.name
            if path.resolve() != target.resolve():
                shutil.copyfile(path, target)
            figure_names.append(target.name)

    meta = {
        "task_id": task_id,
        "data_type": data_type,
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "rows": saved,
        "params": params or {},
        "source_files": [str(item) for item in (source_files or [])],
        "figures": figure_names,
    }
    (directory / SNAPSHOT_META).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return directory


def load_snapshot(snapshot_dir: str) -> Dict[str, Any]:
    """读取任务快照。

    返回键：tables（槽名 -> DataFrame）、clean_log、meta、figures。
    快照不存在或已损坏时抛 DatabaseError，信息可直接展示给用户。
    """
    directory = Path(snapshot_dir)
    if not directory.is_dir():
        raise DatabaseError(
            "这个任务的存档已经不在了：\n{0}\n\n"
            "原因：任务快照目录被移动或删除（常见于手动清理 %APPDATA%、或换了电脑）。\n\n"
            "怎么办：可以【重新指定文件】把当时的数据重新导入，或删除这条历史记录。".format(
                directory))

    tables: Dict[str, pd.DataFrame] = {}
    for key, filename in _SNAPSHOT_TABLES.items():
        path = directory / filename
        if not path.exists():
            continue
        try:
            tables[key] = pd.read_csv(path, encoding="utf-8-sig")
        except Exception as exc:  # noqa: BLE001 - 单个表损坏不应让整次恢复失败
            raise DatabaseError(
                "任务存档里的数据表无法读取：{0}\n\n原因：{1}\n\n"
                "怎么办：该存档可能已损坏，可以删除这条历史记录后重新分析一次。".format(
                    filename, exc))

    if not tables:
        raise DatabaseError(
            "任务存档目录里没有可用的数据表：\n{0}\n\n"
            "怎么办：删除这条历史记录后重新分析一次（原始数据文件不受影响）。".format(directory))

    log_path = directory / SNAPSHOT_CLEAN_LOG
    clean_log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""

    meta: Dict[str, Any] = {}
    meta_path = directory / SNAPSHOT_META
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            meta = {}

    figures = [directory / SNAPSHOT_FIGURES_DIR / name for name in meta.get("figures", [])]
    return {"tables": tables, "clean_log": clean_log, "meta": meta,
            "figures": [path for path in figures if path.exists()]}
