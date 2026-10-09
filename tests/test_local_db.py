# -*- coding: utf-8 -*-
"""
database/local_db.py 与 core/task_store.py 单元测试（阶段 7 交付）

覆盖重点（对应方案阶段 7 的验收标准）：
    1. 建库建表幂等；数据库落在用户数据目录，不在程序目录
    2. 任务增删改查、按时间倒序、按关键词搜索
    3. 快照落盘与读取：数据表、清洗日志、图片、参数
    4. 存档失效时的判定与友好提示（方案阶段 7 第 3 条）
    5. 恢复现场：从快照把数据与日志装回 AppState
    6. 自动保存去重：同一次分析的多次操作只产生一条记录
    7. 删除只动本软件自己的存档，绝不碰用户原始文件
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "temp" / "mplconfig_test"))

from core import data_clean as dc  # noqa: E402
from core import data_parse as dp  # noqa: E402
from core import task_store as ts  # noqa: E402
from core.app_state import AppState  # noqa: E402
from database import local_db  # noqa: E402
from utils import paths  # noqa: E402

SAMPLES = ROOT / "tests" / "samples"


# ===========================================================================
# 测试夹具：把数据目录整体重定向到临时目录
# ===========================================================================
@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """把数据库、任务目录、导出目录都指到临时目录，避免污染真实用户数据。"""
    data_dir = tmp_path / "appdata"
    monkeypatch.setattr(paths, "DATA_DIR", data_dir)
    monkeypatch.setattr(paths, "DB_PATH", data_dir / "ecodata.db")
    monkeypatch.setattr(paths, "TASKS_DIR", data_dir / "tasks")
    monkeypatch.setattr(paths, "TEMP_DIR", data_dir / "temp")
    local_db.init_database()
    return data_dir


@pytest.fixture()
def state(sandbox):
    """干净的单例状态（每个用例重置）。"""
    instance = AppState.instance()
    instance.reset()
    return instance


def _quadrat_state(state, rows: int = 0):
    """把样方数据装进 AppState（模拟导入+清洗+计算完成）。"""
    raw = dp.parse_files([SAMPLES / "sample_quadrat.csv"]).df
    cleaned = dc.clean_data(raw)
    state.set_table("raw", raw)
    state.set_table("clean", cleaned.df)
    state.set_text("clean_log", cleaned.log)
    state.set_text("data_type", dp.TYPE_QUADRAT)
    return raw, cleaned


# ===========================================================================
# 一、建库
# ===========================================================================
class TestInitDatabase:
    def test_creates_file_and_table(self, sandbox):
        assert local_db.database_path().exists()
        assert local_db.count_tasks() == 0

    def test_init_is_idempotent(self, sandbox):
        local_db.init_database()
        local_db.init_database()
        assert local_db.database_path().exists()

    def test_database_is_not_in_program_directory(self, sandbox):
        """数据库必须放在用户数据目录，绝不放在程序目录（方案避坑 2）。"""
        assert local_db.database_path().parent == paths.DATA_DIR
        assert ROOT not in local_db.database_path().parents

    def test_connect_creates_missing_parent(self, tmp_path):
        target = tmp_path / "深" / "层" / "目录" / "x.db"
        local_db.init_database(target)
        assert target.exists()


# ===========================================================================
# 二、增删改查
# ===========================================================================
class TestCrud:
    def test_create_and_get(self, sandbox):
        record = local_db.create_task("任务甲", raw_rows=38, clean_rows=36,
                                      data_type=dp.TYPE_QUADRAT)
        assert record.task_id
        fetched = local_db.get_task(record.task_id)
        assert fetched is not None
        assert fetched.name == "任务甲"
        assert fetched.raw_rows == 38 and fetched.clean_rows == 36
        assert fetched.data_type == dp.TYPE_QUADRAT

    def test_empty_name_falls_back(self, sandbox):
        record = local_db.create_task("   ")
        assert record.name == "未命名任务"

    def test_get_missing_returns_none(self, sandbox):
        assert local_db.get_task("不存在") is None

    def test_list_orders_by_time_desc(self, sandbox):
        local_db.create_task("早", created_at="2024-01-01T10:00:00")
        local_db.create_task("晚", created_at="2024-06-01T10:00:00")
        local_db.create_task("中", created_at="2024-03-01T10:00:00")
        assert [record.name for record in local_db.list_tasks()] == ["晚", "中", "早"]

    def test_list_search_by_name_and_type(self, sandbox):
        local_db.create_task("六月样方", data_type=dp.TYPE_QUADRAT)
        local_db.create_task("七月传感器", data_type=dp.TYPE_SENSOR)
        assert len(local_db.list_tasks(keyword="样方")) == 1
        assert len(local_db.list_tasks(keyword="传感器")) == 1
        assert len(local_db.list_tasks(keyword="不存在")) == 0

    def test_list_limit(self, sandbox):
        for index in range(5):
            local_db.create_task("任务{0}".format(index))
        assert len(local_db.list_tasks(limit=2)) == 2

    def test_rename(self, sandbox):
        record = local_db.create_task("旧名")
        assert local_db.rename_task(record.task_id, "新名") is True
        assert local_db.get_task(record.task_id).name == "新名"

    def test_rename_missing_returns_false(self, sandbox):
        assert local_db.rename_task("不存在", "新名") is False

    def test_rename_empty_raises_chinese_error(self, sandbox):
        record = local_db.create_task("任务")
        with pytest.raises(local_db.DatabaseError) as excinfo:
            local_db.rename_task(record.task_id, "   ")
        assert "不能为空" in str(excinfo.value)

    def test_update_partial_fields(self, sandbox):
        record = local_db.create_task("任务", raw_rows=10)
        local_db.update_task(record.task_id, clean_rows=8, data_type=dp.TYPE_SENSOR)
        fetched = local_db.get_task(record.task_id)
        assert fetched.raw_rows == 10          # 未传的字段保持不变
        assert fetched.clean_rows == 8
        assert fetched.data_type == dp.TYPE_SENSOR

    def test_update_with_no_fields_returns_false(self, sandbox):
        record = local_db.create_task("任务")
        assert local_db.update_task(record.task_id) is False

    def test_delete_removes_record(self, sandbox):
        record = local_db.create_task("任务")
        assert local_db.delete_task(record.task_id) is True
        assert local_db.get_task(record.task_id) is None
        assert local_db.count_tasks() == 0

    def test_delete_missing_returns_false(self, sandbox):
        assert local_db.delete_task("不存在") is False


# ===========================================================================
# 三、快照
# ===========================================================================
class TestSnapshot:
    def test_save_and_load(self, sandbox):
        record = local_db.create_task("任务")
        raw, cleaned = None, None
        raw = dp.parse_files([SAMPLES / "sample_quadrat.csv"]).df
        cleaned = dc.clean_data(raw)
        directory = local_db.save_snapshot(
            record.task_id, raw=raw, clean=cleaned.df, clean_log=cleaned.log,
            data_type=dp.TYPE_QUADRAT, params={"mad_k": 3.0})

        assert directory.is_dir()
        assert (directory / local_db.SNAPSHOT_RAW).exists()
        assert (directory / local_db.SNAPSHOT_CLEAN).exists()
        assert (directory / local_db.SNAPSHOT_CLEAN_LOG).exists()
        assert (directory / local_db.SNAPSHOT_META).exists()

        loaded = local_db.load_snapshot(str(directory))
        assert len(loaded["tables"]["raw"]) == 38
        assert len(loaded["tables"]["clean"]) == 36
        assert "共读取 38 行数据" in loaded["clean_log"]
        assert loaded["meta"]["params"]["mad_k"] == 3.0
        assert loaded["meta"]["rows"]["clean"] == 36

    def test_snapshot_preserves_chinese(self, sandbox):
        record = local_db.create_task("中文任务")
        raw = dp.parse_files([SAMPLES / "sample_quadrat.csv"]).df
        local_db.save_snapshot(record.task_id, raw=raw, data_type=dp.TYPE_QUADRAT)
        loaded = local_db.load_snapshot(str(paths.TASKS_DIR / record.task_id))
        frame = loaded["tables"]["raw"]
        assert list(frame.columns)[0] == "样方号"
        assert frame.iloc[0]["物种"] == "油松"

    def test_snapshot_only_stores_present_tables(self, sandbox):
        record = local_db.create_task("任务")
        local_db.save_snapshot(record.task_id, clean=pd.DataFrame({"a": [1]}))
        directory = paths.TASKS_DIR / record.task_id
        assert (directory / local_db.SNAPSHOT_CLEAN).exists()
        assert not (directory / local_db.SNAPSHOT_RAW).exists()

    def test_stale_table_is_removed_on_resave(self, sandbox):
        """重存快照时，本次没有的表必须删掉旧文件，避免加载到过期数据。"""
        record = local_db.create_task("任务")
        local_db.save_snapshot(record.task_id, raw=pd.DataFrame({"a": [1, 2]}))
        assert (paths.TASKS_DIR / record.task_id / local_db.SNAPSHOT_RAW).exists()
        local_db.save_snapshot(record.task_id, clean=pd.DataFrame({"b": [3]}))
        assert not (paths.TASKS_DIR / record.task_id / local_db.SNAPSHOT_RAW).exists()

    def test_figures_are_copied(self, sandbox, tmp_path):
        record = local_db.create_task("任务")
        source = tmp_path / "图.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 50)
        local_db.save_snapshot(record.task_id, raw=pd.DataFrame({"a": [1]}),
                               figures=[source])
        loaded = local_db.load_snapshot(str(paths.TASKS_DIR / record.task_id))
        assert len(loaded["figures"]) == 1
        assert loaded["figures"][0].exists()
        assert loaded["figures"][0].read_bytes() == source.read_bytes()

    def test_missing_figure_is_skipped(self, sandbox, tmp_path):
        record = local_db.create_task("任务")
        local_db.save_snapshot(record.task_id, raw=pd.DataFrame({"a": [1]}),
                               figures=[tmp_path / "不存在.png"])
        loaded = local_db.load_snapshot(str(paths.TASKS_DIR / record.task_id))
        assert loaded["figures"] == []

    def test_load_missing_snapshot_gives_actionable_message(self, sandbox):
        with pytest.raises(local_db.DatabaseError) as excinfo:
            local_db.load_snapshot(str(paths.TASKS_DIR / "不存在"))
        message = str(excinfo.value)
        assert "存档" in message
        assert "怎么办" in message
        assert "重新指定文件" in message

    def test_snapshot_exists_flag(self, sandbox):
        record = local_db.create_task("任务")
        directory = local_db.save_snapshot(record.task_id, raw=pd.DataFrame({"a": [1]}))
        local_db.update_task(record.task_id, snapshot_dir=str(directory))
        assert local_db.get_task(record.task_id).snapshot_exists is True

        shutil.rmtree(directory)
        assert local_db.get_task(record.task_id).snapshot_exists is False

    def test_load_snapshot_with_empty_directory(self, sandbox):
        directory = paths.TASKS_DIR / "空目录"
        directory.mkdir(parents=True, exist_ok=True)
        with pytest.raises(local_db.DatabaseError) as excinfo:
            local_db.load_snapshot(str(directory))
        assert "没有可用的数据表" in str(excinfo.value)

    def test_corrupted_table_gives_actionable_message(self, sandbox):
        record = local_db.create_task("任务")
        directory = local_db.save_snapshot(record.task_id, raw=pd.DataFrame({"a": [1]}))
        (directory / local_db.SNAPSHOT_RAW).write_bytes(b"\x00\x01\x02 not a csv \xff")
        # pandas 对纯二进制可能仍能读出内容，这里只要"要么读出来、要么给出中文错误"
        try:
            local_db.load_snapshot(str(directory))
        except local_db.DatabaseError as exc:
            assert "怎么办" in str(exc)


# ===========================================================================
# 四、删除的边界：绝不碰用户文件
# ===========================================================================
class TestDeleteSafety:
    def test_delete_removes_snapshot_directory(self, sandbox):
        record = local_db.create_task("任务")
        directory = local_db.save_snapshot(record.task_id, raw=pd.DataFrame({"a": [1]}))
        local_db.update_task(record.task_id, snapshot_dir=str(directory))
        local_db.delete_task(record.task_id)
        assert not directory.exists()

    def test_delete_keeps_record_when_snapshot_disabled(self, sandbox):
        record = local_db.create_task("任务")
        directory = local_db.save_snapshot(record.task_id, raw=pd.DataFrame({"a": [1]}))
        local_db.update_task(record.task_id, snapshot_dir=str(directory))
        local_db.delete_task(record.task_id, remove_snapshot=False)
        assert directory.exists()
        assert local_db.get_task(record.task_id) is None

    def test_refuses_to_delete_directory_outside_tasks_root(self, sandbox, tmp_path):
        """snapshot_dir 被改成任务目录之外的路径时，必须拒绝删除。"""
        outsider = tmp_path / "用户自己的文件夹"
        outsider.mkdir()
        (outsider / "重要文件.txt").write_text("别删我", encoding="utf-8")
        record = local_db.create_task("任务", snapshot_dir=str(outsider))
        local_db.delete_task(record.task_id)
        assert outsider.exists()
        assert (outsider / "重要文件.txt").exists()

    def test_refuses_to_delete_user_data_directory(self, sandbox, tmp_path):
        user_dir = tmp_path / "我的原始数据"
        user_dir.mkdir()
        raw = dp.parse_files([SAMPLES / "sample_quadrat.csv"]).df
        raw.to_csv(user_dir / "sample.csv", index=False, encoding="utf-8-sig")
        record = local_db.create_task("任务", snapshot_dir=str(user_dir))
        local_db.delete_task(record.task_id)
        assert (user_dir / "sample.csv").exists()


# ===========================================================================
# 五、task_store：与 AppState 的对接
# ===========================================================================
class TestTaskStore:
    def test_save_current_task_creates_record_and_snapshot(self, state):
        _quadrat_state(state)
        record = ts.save_current_task(state, name="第一次分析")
        assert record is not None
        assert record.name == "第一次分析"
        assert record.raw_rows == 38 and record.clean_rows == 36
        assert record.snapshot_exists is True
        assert (paths.TASKS_DIR / record.task_id / local_db.SNAPSHOT_CLEAN).exists()

    def test_save_without_data_returns_none(self, state):
        assert ts.save_current_task(state) is None

    def test_second_save_updates_same_record(self, state):
        """同一次分析的多次保存必须落在同一条记录上，不能产生重复条目。"""
        _quadrat_state(state)
        first = ts.save_current_task(state, name="分析")
        second = ts.save_current_task(state, name="分析")
        assert first.task_id == second.task_id
        assert local_db.count_tasks() == 1

    def test_initialize_forces_new_record(self, state):
        _quadrat_state(state)
        first = ts.save_current_task(state, name="分析一")
        second = ts.save_current_task(state, name="分析二", initialize=True)
        assert first.task_id != second.task_id
        assert local_db.count_tasks() == 2

    def test_save_records_task_id_in_state(self, state):
        _quadrat_state(state)
        record = ts.save_current_task(state, name="分析")
        assert ts.current_task_id(state) == record.task_id
        assert ts.current_task_name(state) == "分析"

    def test_current_task_name_default_when_empty(self, state):
        _quadrat_state(state)
        name = ts.current_task_name(state)
        assert name and "样方群落" in name

    def test_load_task_into_state_restores_everything(self, state):
        raw, cleaned = _quadrat_state(state)
        record = ts.save_current_task(state, name="分析")
        state.reset()
        assert state.has("clean") is False

        restored = ts.load_task_into_state(state, record.task_id)
        assert restored.task_id == record.task_id
        assert state.has("raw") and state.has("clean")
        assert len(state.get_table("clean")) == 36
        assert "共读取 38 行数据" in state.get_text("clean_log")
        assert state.get_text("data_type") == dp.TYPE_QUADRAT
        assert ts.current_task_id(state) == record.task_id

    def test_load_clears_previous_task_data(self, state):
        """恢复任务前必须清空上一个任务的数据，避免两张表混在一起。"""
        _quadrat_state(state)
        from core import eco_index as ei

        index = ei.compute_indicators(state.get_table("clean"), data_type=dp.TYPE_QUADRAT)
        state.set_table("index_row", index.row_table)
        first = ts.save_current_task(state, name="有指标的任务")

        # 第二个任务：只有原始数据，没有指标
        state.reset()
        state.set_table("raw", pd.DataFrame({"a": [1, 2]}))
        second = ts.save_current_task(state, name="只有原始数据")

        ts.load_task_into_state(state, second.task_id)
        assert state.has("index_row") is False, "上一个任务的指标表不该残留"

    def test_load_missing_task_gives_actionable_message(self, state):
        with pytest.raises(ts.TaskError) as excinfo:
            ts.load_task_into_state(state, "不存在")
        assert "找不到" in str(excinfo.value)

    def test_load_task_with_lost_snapshot(self, state):
        _quadrat_state(state)
        record = ts.save_current_task(state, name="分析")
        shutil.rmtree(paths.TASKS_DIR / record.task_id)
        state.reset()
        with pytest.raises(ts.TaskError) as excinfo:
            ts.load_task_into_state(state, record.task_id)
        message = str(excinfo.value)
        assert "存档" in message and "怎么办" in message

    def test_list_history_and_search(self, state):
        _quadrat_state(state)
        ts.save_current_task(state, name="六月样方")
        state.reset()
        state.set_table("raw", pd.DataFrame({"a": [1]}))
        state.set_text("data_type", dp.TYPE_SENSOR)
        ts.save_current_task(state, name="七月传感器", initialize=True)

        assert len(ts.list_history()) == 2
        assert len(ts.list_history(keyword="样方")) == 1
        assert len(ts.list_history(keyword="传感器")) == 1

    def test_rename_and_delete_sync_state(self, state):
        _quadrat_state(state)
        record = ts.save_current_task(state, name="旧名")
        ts.rename_task(record.task_id, "新名", state=state)
        assert ts.current_task_name(state) == "新名"

        ts.delete_task(record.task_id, state=state)
        assert ts.current_task_id(state) == ""
        assert local_db.count_tasks() == 0

    def test_snapshot_files_listing(self, state):
        _quadrat_state(state)
        record = ts.save_current_task(state, name="分析")
        files = ts.snapshot_files(record)
        assert any(path.name == local_db.SNAPSHOT_CLEAN for path in files)
        assert any(path.name == local_db.SNAPSHOT_CLEAN_LOG for path in files)
        assert any(path.name == local_db.SNAPSHOT_META for path in files)

    def test_snapshot_files_for_missing_dir(self, state):
        record = local_db.TaskRecord(task_id="x", name="n", created_at="t",
                                     snapshot_dir=str(paths.TASKS_DIR / "无"))
        assert ts.snapshot_files(record) == []

    def test_meta_json_is_readable(self, state):
        _quadrat_state(state)
        record = ts.save_current_task(state, name="分析")
        meta_path = paths.TASKS_DIR / record.task_id / local_db.SNAPSHOT_META
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["task_id"] == record.task_id
        assert meta["data_type"] == dp.TYPE_QUADRAT
        assert meta["rows"]["raw"] == 38

    def test_round_trip_preserves_index_tables(self, state):
        """带指标的任务恢复后，指标表必须原样回来（数值不丢精度）。"""
        from core import eco_index as ei

        _quadrat_state(state)
        index = ei.compute_indicators(state.get_table("clean"), data_type=dp.TYPE_QUADRAT)
        state.set_table("index_row", index.row_table)
        state.set_table("index_summary", index.summary_table)
        record = ts.save_current_task(state, name="完整任务")

        state.reset()
        ts.load_task_into_state(state, record.task_id)
        restored = state.get_table("index_summary")
        assert restored is not None
        assert list(restored.columns) == list(index.summary_table.columns)
        assert len(restored) == len(index.summary_table)
        original_h = float(index.summary_table["H 香农指数"].iloc[0])
        restored_h = float(restored["H 香农指数"].iloc[0])
        assert abs(original_h - restored_h) < 1e-9
