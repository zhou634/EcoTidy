# -*- coding: utf-8 -*-
"""
utils/paths.py 路径解析单元测试（阶段 7 回归防线）

背景（实测踩坑）：
    原来 _resolve_data_dir() 直接返回 %APPDATA%\\EcoTidy，
    不检查是否可写。本机 %APPDATA% 建目录直接 [WinError 5] 拒绝访问，
    于是 paths.ensure_dirs() 在 main() 最开始就抛异常 —— 此时异常钩子与界面
    都还没建立，软件直接闪退并甩出一堆英文堆栈。

本文件锁定修复后的行为：
    1. 数据目录按候选顺序**真实探测可写性**，第一个可写的胜出；
    2. 首选不可写时必须回退，绝不硬失败；
    3. 全部不可写时模块导入仍不抛异常（界面还要靠这些常量拼路径）；
    4. 发生回退时能向用户说明"数据存到哪里了、为什么"。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils import paths  # noqa: E402


# ===========================================================================
# 一、当前环境的基本不变量
# ===========================================================================
class TestResolvedState:
    def test_data_dir_is_absolute(self):
        assert paths.DATA_DIR.is_absolute()

    def test_data_dir_matches_writability_flag(self):
        """DATA_DIR_WRITABLE 必须如实反映真实可写性。"""
        assert paths.DATA_DIR_WRITABLE == paths._is_dir_writable(paths.DATA_DIR), \
            (paths.DATA_DIR, paths.DATA_DIR_WRITABLE)

    def test_subdirs_live_under_data_dir(self):
        for directory in (paths.TEMP_DIR, paths.TASKS_DIR, paths.MPL_CONFIG_DIR):
            assert paths.DATA_DIR in directory.parents, directory
        assert paths.DB_PATH.parent == paths.DATA_DIR

    def test_ensure_dirs_creates_required_dirs(self):
        created = paths.ensure_dirs()
        assert paths.DATA_DIR in created
        assert paths.TEMP_DIR in created
        assert paths.TASKS_DIR in created
        for directory in (paths.DATA_DIR, paths.TEMP_DIR, paths.TASKS_DIR):
            assert directory.is_dir()

    def test_ensure_dirs_never_raises(self):
        """即使某个目录建不出来，ensure_dirs 也只跳过、不抛异常。"""
        assert isinstance(paths.ensure_dirs(), list)

    def test_resource_path_under_static(self):
        assert paths.resource_path("demo", "x.csv") == paths.STATIC_DIR / "demo" / "x.csv"

    def test_base_dir_is_project_root_in_dev(self):
        assert not paths.IS_FROZEN
        assert (paths.BASE_DIR / "main.py").exists()


# ===========================================================================
# 二、候选目录与可写性探测
# ===========================================================================
class TestCandidates:
    def test_first_candidate_is_appdata(self):
        candidates = paths._data_dir_candidates()
        appdata = os.environ.get("APPDATA")
        if appdata:
            assert candidates[0] == Path(appdata) / paths.APP_NAME

    def test_candidates_include_program_dir_fallback(self):
        """程序目录下的 .ecodata 必须在候选里（便携部署/受限电脑的兜底）。"""
        assert paths.BASE_DIR / ".ecodata" in paths._data_dir_candidates()

    def test_candidates_include_temp(self):
        import tempfile

        assert Path(tempfile.gettempdir()) / paths.APP_NAME in paths._data_dir_candidates()

    def test_candidates_are_unique(self):
        candidates = paths._data_dir_candidates()
        assert len(candidates) == len(set(candidates))

    def test_is_dir_writable_true_for_tmp(self, tmp_path):
        assert paths._is_dir_writable(tmp_path) is True

    def test_is_dir_writable_does_not_leave_probe_file(self, tmp_path):
        paths._is_dir_writable(tmp_path)
        assert not (tmp_path / ".write_probe").exists()

    def test_is_dir_writable_false_for_nonexistent(self, tmp_path):
        assert paths._is_dir_writable(tmp_path / "不存在") is False


# ===========================================================================
# 三、回退逻辑（本缺陷的核心）
# ===========================================================================
class TestFallback:
    def test_prefers_first_writable_candidate(self, monkeypatch, tmp_path):
        first = tmp_path / "首选"
        monkeypatch.setattr(paths, "_data_dir_candidates", lambda: [first, tmp_path / "备选"])
        assert paths._resolve_data_dir() == first
        assert first.is_dir()

    def test_falls_back_when_first_is_unwritable(self, monkeypatch, tmp_path):
        """首选目录不可写时必须回退到下一个可写候选，而不是硬失败。"""
        first = tmp_path / "不可写"
        second = tmp_path / "可写"
        monkeypatch.setattr(paths, "_data_dir_candidates", lambda: [first, second])
        monkeypatch.setattr(paths, "_is_dir_writable", lambda d: Path(d) == second)
        assert paths._resolve_data_dir() == second

    def test_falls_back_when_first_cannot_even_be_created(self, monkeypatch, tmp_path):
        """首选目录连 mkdir 都失败（本机 %APPDATA% 就是这个情况）时也要回退。"""
        first = tmp_path / "建不出来"
        second = tmp_path / "能建出来"

        original_mkdir = Path.mkdir

        def fake_mkdir(self, *args, **kwargs):
            if self == first:
                raise PermissionError("[WinError 5] 拒绝访问。")
            return original_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(paths, "_data_dir_candidates", lambda: [first, second])
        monkeypatch.setattr(Path, "mkdir", fake_mkdir)
        assert paths._resolve_data_dir() == second

    def test_returns_first_candidate_when_none_writable(self, monkeypatch, tmp_path):
        """全部不可写时返回首选目录（不抛异常），由 DATA_DIR_WRITABLE 反映真实情况。"""
        first = tmp_path / "全都不可写"
        second = tmp_path / "也不可写"
        monkeypatch.setattr(paths, "_data_dir_candidates", lambda: [first, second])
        monkeypatch.setattr(paths, "_is_dir_writable", lambda d: False)
        assert paths._resolve_data_dir() == first

    def test_fallback_records_reason(self, monkeypatch, tmp_path):
        """发生回退时要能说清原因，界面横幅靠它提示用户。"""
        first = tmp_path / "首选"
        second = tmp_path / "备选"
        monkeypatch.setattr(paths, "_data_dir_candidates", lambda: [first, second])
        monkeypatch.setattr(paths, "_is_dir_writable", lambda d: Path(d) == second)
        monkeypatch.setattr(paths, "_DATA_DIR_FALLBACK_REASON", "")
        # 直接验证设置原因的那段逻辑
        paths._resolve_data_dir()
        assert paths._DATA_DIR_FALLBACK_REASON != ""

    def test_describe_data_dir_is_chinese(self):
        text = paths.describe_data_dir()
        assert "数据保存在" in text
        assert str(paths.DATA_DIR) in text

    def test_describe_mentions_reason_on_fallback(self, monkeypatch, tmp_path):
        monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "别处")
        monkeypatch.setattr(paths, "PREFERRED_DATA_DIR", tmp_path / "标准位置")
        text = paths.describe_data_dir()
        assert "已自动改存到这里" in text

    def test_data_dir_is_default_flag(self, monkeypatch, tmp_path):
        monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "a")
        monkeypatch.setattr(paths, "PREFERRED_DATA_DIR", tmp_path / "a")
        assert paths.data_dir_is_default() is True
        monkeypatch.setattr(paths, "PREFERRED_DATA_DIR", tmp_path / "b")
        assert paths.data_dir_is_default() is False


# ===========================================================================
# 四、数据落盘真的能成功（端到端）
# ===========================================================================
class TestActuallyUsable:
    def test_can_write_and_read_a_file_in_data_dir(self):
        paths.ensure_dirs()
        probe = paths.TEMP_DIR / "写读探测.txt"
        probe.write_text("中文内容正常", encoding="utf-8")
        try:
            assert probe.read_text(encoding="utf-8") == "中文内容正常"
        finally:
            probe.unlink()

    def test_database_path_parent_is_usable(self):
        """数据库父目录必须能建可写，否则启动建库就会失败。"""
        paths.ensure_dirs()
        assert paths.DB_PATH.parent.is_dir()
        assert paths._is_dir_writable(paths.DB_PATH.parent)

    def test_tasks_dir_is_usable(self):
        paths.ensure_dirs()
        assert paths._is_dir_writable(paths.TASKS_DIR)

    def test_mpl_config_dir_resolves_writable(self):
        """matplotlib 缓冲目录也必须可写（否则 import pyplot 直接崩）。"""
        directory = paths.resolve_mpl_config_dir()
        assert directory.is_dir()
        assert paths._is_dir_writable(directory), directory

    def test_export_dir_can_be_created(self, tmp_path):
        target = tmp_path / "导出"
        assert paths.ensure_export_dir(target) == target
        assert target.is_dir()


# ===========================================================================
# 五、matplotlib 缓冲目录（同一类问题的另一处防线）
# ===========================================================================
class TestMplConfigDir:
    def test_resolved_dir_is_writable(self, monkeypatch):
        """解析出来的 matplotlib 缓冲目录必须可写，否则 import pyplot 直接崩。"""
        monkeypatch.delenv("MPLCONFIGDIR", raising=False)
        directory = paths.resolve_mpl_config_dir()
        assert paths._is_dir_writable(directory), directory

    def test_prefers_dir_under_data_dir(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MPLCONFIGDIR", raising=False)
        target = tmp_path / "数据" / "matplotlib"
        monkeypatch.setattr(paths, "MPL_CONFIG_DIR", target)
        assert paths.resolve_mpl_config_dir() == target

    def test_falls_back_when_data_dir_unwritable(self, monkeypatch, tmp_path):
        """数据目录不可写时要退到临时目录，而不是继续用不可写目录。"""
        import tempfile

        monkeypatch.delenv("MPLCONFIGDIR", raising=False)
        monkeypatch.setattr(paths, "MPL_CONFIG_DIR", tmp_path / "不可写")
        home_cache = Path.home() / ".matplotlib"
        monkeypatch.setattr(paths, "_is_dir_writable",
                            lambda d: Path(d).name == "matplotlib"
                            and Path(d).parent.name == paths.APP_NAME)
        directory = paths.resolve_mpl_config_dir()
        temp_root = Path(tempfile.gettempdir()).resolve()
        assert directory == home_cache or str(directory).startswith(str(temp_root)), directory

    def test_explicit_env_is_respected(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path))
        assert paths.resolve_mpl_config_dir() == tmp_path

    def test_ensure_is_idempotent(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path))
        assert paths.ensure_mpl_config_dir() == tmp_path
        assert paths.ensure_mpl_config_dir() == tmp_path

    def test_ensure_sets_env_when_absent(self, monkeypatch):
        monkeypatch.delenv("MPLCONFIGDIR", raising=False)
        directory = paths.ensure_mpl_config_dir()
        assert os.environ["MPLCONFIGDIR"] == str(directory)
