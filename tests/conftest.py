# -*- coding: utf-8 -*-
"""
pytest 共享夹具

为什么需要它（实测踩坑）：
    夹具定义在某个测试模块里时，**只有该模块**能用。test_i18n 想复用
    test_ui_behaviour 里的 qt_app 会报 "fixture 'qt_app' not found"。
    放到 conftest.py 后，同目录下所有测试文件都能直接使用。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 界面测试必须在离屏平台下跑，否则 CI/无桌面环境会直接崩
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "temp" / "mplconfig_test"))


@pytest.fixture(scope="session")
def qt_app():
    """整个测试会话共用一个 QApplication（Qt 不允许重复创建）。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("EcoTidy")
    app.setApplicationName("EcoTidy")
    return app


@pytest.fixture()
def app_sandbox(tmp_path, monkeypatch):
    """把数据目录与配置目录都指到临时目录，避免污染真实用户数据。

    返回数据目录路径；同时重置 settings 单例，保证偏好互不干扰。
    """
    from PySide6.QtCore import QSettings

    from core import settings as settings_module
    from utils import paths

    data_dir = tmp_path / "appdata"
    monkeypatch.setattr(paths, "DATA_DIR", data_dir)
    monkeypatch.setattr(paths, "DB_PATH", data_dir / "ecodata.db")
    monkeypatch.setattr(paths, "TASKS_DIR", data_dir / "tasks")
    monkeypatch.setattr(paths, "TEMP_DIR", data_dir / "temp")
    paths.ensure_dirs()
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(data_dir))
    settings_module.Settings._instance = None
    yield data_dir
    settings_module.Settings._instance = None


@pytest.fixture()
def window(qt_app, app_sandbox, monkeypatch):
    """建好主窗口并把弹窗替换成非阻塞记录器。

    放在 conftest 里是为了让多个测试文件共用：夹具定义在某个测试模块内时，
    只有那个模块能用（实测踩坑：别的模块引用会报 fixture not found）。
    """
    from PySide6.QtWidgets import QMessageBox

    from core.app_state import AppState

    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "critical",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))

    import main as entry

    AppState.instance().reset()
    win = entry.MainWindow(app_state=AppState.instance())
    win.show()
    qt_app.processEvents()
    yield win
    win.close()
    qt_app.processEvents()
