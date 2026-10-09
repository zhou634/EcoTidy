# -*- coding: utf-8 -*-
"""
界面行为回归测试（阶段 8 交付）

覆盖本阶段走查发现并修复的缺陷，以及"点了没反应"这类最容易漏掉的体验问题：
    1. 步骤条：画出图就算完成第 4 步（原来只有导出图片才算，一键流程后中间断一格）
    2. 步骤条提示语与下一步一致
    3. 禁用按钮必须说明为什么不能点（方案第五章：绝不允许"点了没反应"）
    4. 后台解析线程的引用必须在**线程结束后**才释放
       —— 原来在结果回调里立刻清空引用，进程会以 0xC0000409 异常中止
    5. 关窗/退出时会先停后台任务
    6. 全局不残留"开发中/后续阶段交付"这类把责任推给用户的文案
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "temp" / "mplconfig_test"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractButton, QApplication, QLabel, QMessageBox,
)

from core import data_parse as dp  # noqa: E402
from core.app_state import AppState  # noqa: E402
from utils import paths  # noqa: E402

SAMPLES = ROOT / "tests" / "samples"


@pytest.fixture(scope="session")
def qt_app():
    """整个测试会话共用一个 QApplication（Qt 不允许重复创建）。"""
    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("EcoTidy")
    app.setApplicationName("EcoTidy")
    return app


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """把数据目录与配置目录都指到临时目录，避免污染真实用户数据。"""
    data_dir = tmp_path / "appdata"
    monkeypatch.setattr(paths, "DATA_DIR", data_dir)
    monkeypatch.setattr(paths, "DB_PATH", data_dir / "ecodata.db")
    monkeypatch.setattr(paths, "TASKS_DIR", data_dir / "tasks")
    monkeypatch.setattr(paths, "TEMP_DIR", data_dir / "temp")
    paths.ensure_dirs()
    # 偏好也写到临时目录，避免测试互相干扰
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(data_dir))
    from core import settings as settings_module

    settings_module.Settings._instance = None
    yield data_dir
    settings_module.Settings._instance = None


@pytest.fixture()
def window(qt_app, sandbox, monkeypatch):
    """建好主窗口，并把弹窗替换成非阻塞记录器。"""
    dialogs = []

    def record(answer=None):
        def _inner(*args, **kwargs):
            text = args[2] if len(args) > 2 else kwargs.get("text", "")
            dialogs.append(str(text))
            return answer if answer is not None else QMessageBox.StandardButton.Ok
        return staticmethod(_inner)

    monkeypatch.setattr(QMessageBox, "information", record())
    monkeypatch.setattr(QMessageBox, "warning", record())
    monkeypatch.setattr(QMessageBox, "critical", record())
    monkeypatch.setattr(QMessageBox, "question", record(QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)

    import main as entry

    AppState.instance().reset()
    win = entry.MainWindow(app_state=AppState.instance())
    win.show()
    qt_app.processEvents()
    yield win
    win.close()
    qt_app.processEvents()


# ===========================================================================
# 一、步骤条（阶段 8 修复项）
# ===========================================================================
class TestStepBar:
    def test_all_steps_unticked_without_data(self, window):
        assert [window.step_bar.is_done(i) for i in range(5)] == [False] * 5

    def test_step_one_ticks_after_import(self, window, qt_app):
        state = AppState.instance()
        state.set_table("raw", dp.parse_files([SAMPLES / "sample_quadrat.csv"]).df)
        qt_app.processEvents()
        assert window.step_bar.is_done(0) is True

    def test_drawing_chart_ticks_step_four(self, window, qt_app):
        """阶段 8 缺陷回归：画出图就应该算完成第 4 步，不必先导出图片。"""
        state = AppState.instance()
        state.set_table("raw", dp.parse_files([SAMPLES / "sample_quadrat.csv"]).df)
        window.page(1).run_clean()
        window.page(2).run_calc()
        window.plot_page._on_data_changed("clean")
        qt_app.processEvents()
        assert window.step_bar.is_done(3) is False

        assert window.plot_page.draw_chart() is True
        qt_app.processEvents()
        assert window.step_bar.is_done(3) is True, "绘图后第 4 步应打勾"
        assert state.get_meta().get("chart_drawn") is True

    def test_no_gap_after_automatic_flow(self, window, qt_app, tmp_path):
        """一键流程跑完后，步骤条不应出现「✓✓✓·✓」这种中间断格。"""
        # 输出目录设到临时目录：默认位置（我的文档）在受管控的电脑上可能不可写，
        # 那样导出会如实失败，第 5 步本就不该打勾（这是正确行为，另有用例覆盖）。
        window.export_page._dir_edit.setText(str(tmp_path / "成果"))
        window.import_page.load_demo()
        qt_app.processEvents()
        window.import_page.run_auto_flow()
        qt_app.processEvents()
        marks = [window.step_bar.is_done(i) for i in range(5)]
        assert marks == [True] * 5, marks

    def test_step_five_stays_unticked_when_export_fails(self, window, qt_app):
        """导出没成功时第 5 步不能打勾 —— 否则就是"还没导出就说完成了"的误导。"""
        state = AppState.instance()
        state.set_table("raw", dp.parse_files([SAMPLES / "sample_quadrat.csv"]).df)
        window.page(1).run_clean()
        window.page(2).run_calc()
        window.plot_page._on_data_changed("clean")
        window.plot_page.draw_chart()
        qt_app.processEvents()
        # 指向一个不可能创建成功的路径（路径过长，系统会直接拒绝）
        window.export_page._dir_edit.setText(str(ROOT / "temp" / ("x" * 300)))
        assert window.export_page.export_all() is False
        qt_app.processEvents()
        assert window.step_bar.is_done(4) is False
        assert window.step_bar.is_done(3) is True, "画过图，第 4 步仍应打勾"

    def test_hint_matches_next_step(self, window, qt_app):
        assert window.step_bar._hint.text() == "下一步：导入数据"
        state = AppState.instance()
        state.set_table("raw", dp.parse_files([SAMPLES / "sample_quadrat.csv"]).df)
        qt_app.processEvents()
        assert window.step_bar._hint.text() == "下一步：清洗数据"
        window.page(1).run_clean()
        qt_app.processEvents()
        assert window.step_bar._hint.text() == "下一步：计算指标"


# ===========================================================================
# 二、禁用按钮必须说明原因
# ===========================================================================
class TestDisabledButtons:
    def test_every_disabled_button_explains_why(self, window, qt_app):
        """方案第五章：按钮置灰时必须写清"为什么不能点、先做什么"。"""
        window.goto_page(0)
        qt_app.processEvents()
        offenders = []
        for index in range(5):
            window.goto_page(index)
            qt_app.processEvents()
            page = window.page(index)
            if page is None:
                continue
            for button in page.findChildren(QAbstractButton):
                if button.isVisible() and not button.isEnabled():
                    if not button.toolTip().strip():
                        offenders.append((index, button.text()))
        assert not offenders, "这些禁用按钮没有说明原因：{0}".format(offenders)

    def test_import_page_actions_disabled_before_import(self, window, qt_app):
        window.goto_page(0)
        qt_app.processEvents()
        page = window.import_page
        assert page._import_button.isEnabled() is False
        assert "载入示例数据" in page._import_button.toolTip()
        assert page._clear_button.isEnabled() is False
        assert page._clear_button.toolTip().strip(), "禁用按钮必须说明原因"


# ===========================================================================
# 三、后台线程引用与退出（阶段 8 缺陷回归）
# ===========================================================================
class TestBackgroundThread:
    def test_import_page_exposes_running_flag(self, window):
        assert window.import_page.has_running_task() is False
        assert window.has_running_task() is False

    def test_stop_background_task_is_safe_when_idle(self, window):
        assert window.import_page.stop_background_task() is True
        assert window.stop_background_tasks() is True

    def test_shutdown_returns_true_when_idle(self, window):
        assert window.shutdown() is True

    def test_thread_reference_released_only_by_thread_finished(self):
        """锁定修复方式：结果回调里**不得**清空线程引用。

        原缺陷：在 _on_parse_finished 里 `self._thread = None`，
        此时线程尚未真正结束，QThread 包装对象被回收导致进程
        0xC0000409（STATUS_STACK_BUFFER_OVERRUN）中止。
        正确做法是挂到 thread.finished 上，由 _release_background_task 释放。
        """
        source = (ROOT / "ui" / "page_import.py").read_text(encoding="utf-8")
        assert "thread.finished.connect(self._release_background_task)" in source
        # 取出两个回调的函数体，确认里面没有清空引用
        for name in ("_on_parse_finished", "_on_parse_failed"):
            start = source.index("def {0}".format(name))
            body = source[start:start + 1500]
            assert "self._thread = None" not in body, \
                "{0} 里清空线程引用会让进程崩溃，应交给 _release_background_task".format(name)


# ===========================================================================
# 四、真实进程退出（子进程验证，锁定 0xC0000409 回归）
# ===========================================================================
class TestCleanExit:
    def test_process_exits_cleanly_after_threaded_import(self, tmp_path):
        """子进程里跑"事件循环 + 线程导入 + 关窗退出"，进程必须正常退出。

        这是本阶段最严重缺陷的回归测试：修复前退出码为 3221226505
        （0xC0000409，栈缓冲溢出），修复后为 0。
        """
        script = tmp_path / "exit_probe.py"
        script.write_text(textwrap.dedent('''
            import os, sys
            from pathlib import Path
            ROOT = Path(r"{root}")
            sys.path.insert(0, str(ROOT))
            os.environ["QT_QPA_PLATFORM"] = "offscreen"
            os.environ["MPLCONFIGDIR"] = str(ROOT / "temp" / "mplconfig_test")

            from PySide6.QtCore import QSettings, QTimer, QEventLoop
            from PySide6.QtWidgets import QApplication, QMessageBox

            cfg = Path(r"{cfg}")
            cfg.mkdir(parents=True, exist_ok=True)
            QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(cfg))

            from utils import paths
            paths.DATA_DIR = Path(r"{cfg}") / "appdata"
            paths.DB_PATH = paths.DATA_DIR / "ecodata.db"
            paths.TASKS_DIR = paths.DATA_DIR / "tasks"
            paths.TEMP_DIR = paths.DATA_DIR / "temp"
            paths.ensure_dirs()

            import main as entry
            from core.app_state import AppState

            QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
            QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
            QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
            QMessageBox.exec = lambda self: 0

            app = QApplication([])
            app.setOrganizationName(entry.ORG_NAME)
            app.setApplicationName(entry.APP_NAME)
            state = AppState.instance()
            state.reset()
            window = entry.MainWindow(app_state=state)
            window.show()

            sample = ROOT / "tests" / "samples" / "sample_quadrat.csv"

            def start():
                window.import_page.add_files([str(sample)])
                window.import_page.confirm_import()
                QTimer.singleShot(50, poll)

            def poll():
                if window.has_running_task():
                    QTimer.singleShot(50, poll)
                    return
                window.close()
                QTimer.singleShot(50, app.quit)

            QTimer.singleShot(100, start)
            QTimer.singleShot(30000, app.quit)
            app.exec()
            window.shutdown()
            app.processEvents()
            print("EXIT_OK")
        ''').format(root=ROOT, cfg=tmp_path / "cfg"), encoding="utf-8")

        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "offscreen"
        env["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            [sys.executable, str(script)], capture_output=True, timeout=180, env=env)
        stdout = completed.stdout.decode("utf-8", "replace")
        stderr = completed.stderr.decode("utf-8", "replace")
        assert completed.returncode == 0, (
            "子进程退出码 {0}（0xC0000409 = 3221226505 表示后台线程引用被提前释放）。\n"
            "stdout: {1}\nstderr: {2}".format(completed.returncode, stdout[-800:], stderr[-800:]))
        assert "EXIT_OK" in stdout


# ===========================================================================
# 四之二、绘图页的配色与输出格式选项
# ===========================================================================
class TestPlotPaletteUi:
    """绘图页必须能选彩色/黑白，也能选矢量图输出（本阶段新增需求）。"""

    def test_palette_options_present(self, window, qt_app):
        from core import plot_draw

        combo = window.plot_page._palette_combo
        options = [combo.itemText(i) for i in range(combo.count())]
        assert options == list(plot_draw.ALL_PALETTE_MODES), options
        assert "彩色" in options and "纯黑白" in options and "灰阶" in options
        assert combo.toolTip().strip(), "配色下拉框必须有说明"

    def test_format_options_present(self, window, qt_app):
        combo = window.plot_page._format_combo
        options = [combo.itemText(i) for i in range(combo.count())]
        assert len(options) == 3, options
        assert any("PNG" in item and "SVG" in item for item in options), options
        assert any("仅 SVG" in item for item in options), options
        assert combo.toolTip().strip(), "输出格式下拉框必须有说明"

    def test_current_palette_and_formats_reflect_selection(self, window, qt_app):
        page = window.plot_page
        page._palette_combo.setCurrentText("彩色")
        assert page.current_palette() == "彩色"
        page._format_combo.setCurrentText("仅 SVG 矢量图")
        assert page.current_formats() == ("svg",)
        page._format_combo.setCurrentText("PNG + SVG（推荐）")
        assert page.current_formats() == ("png", "svg")

    def test_drawing_uses_selected_palette(self, window, qt_app):
        """切换配色后画出来的图必须真的换了配色。"""
        state = AppState.instance()
        state.reset()
        state.set_table("raw", dp.parse_files([SAMPLES / "sample_quadrat.csv"]).df)
        window.page(1).run_clean()
        window.page(2).run_calc()
        qt_app.processEvents()
        page = window.plot_page
        page._on_data_changed("clean")
        page._source_combo.setCurrentText("汇总指标")
        qt_app.processEvents()

        page._palette_combo.setCurrentText("彩色")
        assert page.draw_chart() is True
        color_first = page.last_result().figure.axes[0].patches[0].get_facecolor()
        page._palette_combo.setCurrentText("纯黑白")
        qt_app.processEvents()
        bw_first = page.last_result().figure.axes[0].patches[0].get_facecolor()
        assert color_first != bw_first, "切换配色后图形没有变化"

    def test_palette_choice_is_remembered(self, window, qt_app, sandbox):
        from core import settings as settings_mod

        page = window.plot_page
        page._palette_combo.setCurrentText("纯黑白")
        page._format_combo.setCurrentText("仅 PNG 位图（300dpi）")
        page._save_choices()
        choices = settings_mod.instance().load_plot_choices()
        assert choices.get("palette") == "纯黑白"
        assert choices.get("format") == "仅 PNG 位图（300dpi）"

    def test_plan_label_mentions_palette_and_format(self, window, qt_app):
        state = AppState.instance()
        state.reset()
        state.set_table("raw", dp.parse_files([SAMPLES / "sample_quadrat.csv"]).df)
        window.page(1).run_clean()
        window.page(2).run_calc()
        qt_app.processEvents()
        page = window.plot_page
        page._on_data_changed("clean")
        page._source_combo.setCurrentText("汇总指标")
        qt_app.processEvents()
        text = page._plan_label.text()
        assert "配色" in text and "输出" in text, text


# ===========================================================================
# 四之三、空状态文字不溢出（用户报告：字超出显示范围）
# ===========================================================================
class TestEmptyStateLayout:
    """空状态说明在窄窗口下必须折行并长高，不能溢出到框外。

    实测缺陷：QLabel 开了 wordWrap，但布局只按"一行的高度"（18px）分配空间，
    而折行后的文字需要 46px —— 文字压到按钮上甚至被裁掉。
    """

    def test_description_has_room_for_wrapped_text(self, window, qt_app):
        from PySide6.QtGui import QFontMetrics

        window.resize(538, 285)
        qt_app.processEvents()
        for index in range(5):
            window.goto_page(index)
            qt_app.processEvents()
            page = window.page(index)
            for label in page.findChildren(QLabel):
                if label.objectName() != "empty_desc" or not label.text():
                    continue
                metrics = QFontMetrics(label.font())
                flags = int(label.alignment()) | 0x1000      # Qt.TextWordWrap
                needed = metrics.boundingRect(0, 0, label.width(), 10000,
                                              flags, label.text())
                assert label.height() >= needed.height(), (
                    "第 {0} 页说明文字溢出：标签高 {1}px，文字需要 {2}px".format(
                        index + 1, label.height(), needed.height()))

    def test_label_grows_with_longer_text(self, window, qt_app):
        """文字更长时必须更高（证明高度是按内容算的，不是写死的）。"""
        window.goto_page(4)
        qt_app.processEvents()
        labels = [lb for lb in window.page(4).findChildren(QLabel)
                  if lb.objectName() == "empty_desc"]
        assert labels, "报告导出页应有空状态说明"
        label = labels[0]
        before = label.minimumHeight()
        original = label.text()
        try:
            label.setText(original * 4)
            assert label.minimumHeight() > before, (before, label.minimumHeight())
        finally:
            label.setText(original)

    def test_layout_gives_description_enough_height(self, window, qt_app):
        """布局给说明标签的高度必须 >= 其最小高度（否则会被压扁）。"""
        window.resize(538, 420)
        qt_app.processEvents()
        window.goto_page(4)
        qt_app.processEvents()
        state = window._empty_states[4]
        try:
            label = state._desc_label
            assert label.height() >= label.minimumHeight(), (
                label.height(), label.minimumHeight())
        except RuntimeError:
            pass          # 该页已挂载真实页面，占位控件被销毁


# ===========================================================================
# 四之四、界面图标（黑白线稿，不用彩色 Emoji）
# ===========================================================================
class TestIcons:
    def test_all_icons_render(self, qt_app):
        from ui.widgets.icons import ALL_ICONS, icon_pixmap

        for name in ALL_ICONS:
            pixmap = icon_pixmap(name, 48)
            assert not pixmap.isNull(), name
            assert pixmap.width() == 48

    def test_icons_are_monochrome(self, qt_app):
        """图标必须是单色深色线稿：彩色 Emoji 与白色单色界面冲突。

        判定口径：每个不透明像素都必须是"深灰/黑"（三通道都低且接近）。
        只比较通道极差是不够的 —— 抗锯齿边缘会有 ±10 的轻微色差，
        肉眼完全看不出，但那不是"彩色图标"（实测踩坑，误报过一次）。
        """
        from ui.widgets.icons import ALL_ICONS, icon_pixmap

        for name in ALL_ICONS:
            image = icon_pixmap(name, 64).toImage()
            for y in range(image.height()):
                for x in range(image.width()):
                    color = image.pixelColor(x, y)
                    if color.alpha() < 40:          # 几乎透明，忽略
                        continue
                    highest = max(color.red(), color.green(), color.blue())
                    spread = highest - min(color.red(), color.green(), color.blue())
                    assert highest <= 90, (
                        "{0} 在 ({1},{2}) 太亮，不是深色线稿：{3}".format(
                            name, x, y, color.name()))
                    assert spread <= 24, (
                        "{0} 在 ({1},{2}) 明显偏色：{3}".format(
                            name, x, y, color.name()))

    def test_empty_state_uses_drawn_bitmap_not_emoji(self, qt_app):
        """空状态控件收到图标名后应显示画出来的位图，而不是 Emoji 文字。"""
        from ui.widgets.empty_state import EmptyState

        state = EmptyState()
        state.set_icon("folder")
        label = state._icon_label
        assert not label.pixmap().isNull(), "图标应是画出来的位图"
        assert label.text() == "", "用位图时不应再显示文字"

        # 传非图标名时退回文字显示（兼容旧用法，也便于临时占位）
        state.set_icon("X")
        assert label.text() == "X"
        assert label.pixmap().isNull()

    def test_page_icon_names_are_valid(self, qt_app):
        from ui import main_window
        from ui.widgets.icons import ALL_ICONS

        for name in main_window.EMPTY_ICONS:
            assert name in ALL_ICONS, name

    def test_pages_do_not_override_icon_with_emoji(self):
        """回归防线：五个页面与历史对话框不许再用 Emoji 设置空状态图标。

        实测缺陷：主窗口已经设成了黑白线稿图标，但各页面在构造时又调用
        `self._empty.set_icon("🗂")` 把它盖回彩色 Emoji —— 界面上看到的
        仍然是黄色文件夹，看起来"改了没生效"。
        """
        import re

        from ui.widgets.icons import ALL_ICONS

        offenders = []
        watched = ["ui/page_import.py", "ui/page_clean.py", "ui/page_calc.py",
                   "ui/page_plot.py", "ui/page_export.py",
                   "ui/widgets/history_dialog.py"]
        for name in watched:
            path = ROOT / name
            for number, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), 1):
                match = re.search(r'set_icon\("([^"]*)"\)', line)
                if match and match.group(1) not in ALL_ICONS:
                    offenders.append("{0}:{1} -> {2}".format(
                        name, number, match.group(1)))
        assert not offenders, (
            "这些地方用非图标名（多半是 Emoji）覆盖了线稿图标：{0}".format(offenders))


# ===========================================================================
# 四之五、模态框按钮（用户报告：点"打开输出文件夹"没反应）
# ===========================================================================
class TestModalButtonResult:
    def test_ask_modal_detects_action_button(self, qt_app, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        from ui import style as ui_style

        box = QMessageBox()
        box.addButton("确定", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
        monkeypatch.setattr(QMessageBox, "clickedButton",
                            lambda self: self.buttons()[0])
        assert ui_style.ask_modal(box, "确定") is True
        assert ui_style.ask_modal(box, "取消") is False

    def test_ask_modal_survives_missing_button(self, qt_app, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        from ui import style as ui_style

        box = QMessageBox()
        monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
        monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: None)
        assert ui_style.ask_modal(box, "打开输出文件夹") is False
        assert ui_style.ask_modal_result(box) is None

    def test_no_fragile_clicked_button_comparison(self):
        """回归防线：不许再写 `box.clickedButton() is some_button`。

        模态框返回后按钮的 Python 包装对象会失效，这种比较恒为 False，
        表现为"点了按钮却没反应"（用户实际报过的 bug）。
        """
        offenders = []
        for path in sorted(ROOT.rglob("*.py")):
            rel = path.relative_to(ROOT)
            if rel.parts[0] in (".vs", "env", "env1", "env2", "temp", "release",
                                ".ecodata", "tests"):
                continue
            if "__pycache__" in rel.parts:
                continue
            for number, line in _code_lines(path):
                if "clickedButton() is " in line:
                    offenders.append("{0}:{1}".format(rel.as_posix(), number))
        assert not offenders, "这些地方仍在用不可靠的按钮比较：{0}".format(offenders)


# ===========================================================================
# 四之六、打开文件夹
# ===========================================================================
class TestOpenFolder:
    def test_missing_path_returns_false_without_crash(self, tmp_path):
        from utils.errors import open_path_in_explorer, reveal_in_explorer

        missing = tmp_path / "不存在的目录"
        assert open_path_in_explorer(missing) is False
        assert reveal_in_explorer(missing) is False

    def test_existing_dir_is_attempted(self, tmp_path, monkeypatch):
        from utils import errors

        calls = []
        monkeypatch.setattr(errors.QDesktopServices, "openUrl",
                            staticmethod(lambda url: calls.append(url) or True))
        assert errors.open_path_in_explorer(tmp_path) is True
        assert len(calls) == 1

    def test_reveal_file_opens_parent_dir(self, tmp_path, monkeypatch):
        from utils import errors

        calls = []
        monkeypatch.setattr(errors.QDesktopServices, "openUrl",
                            staticmethod(lambda url: calls.append(url) or True))
        target = tmp_path / "结果.xlsx"
        target.write_text("x", encoding="utf-8")
        assert errors.reveal_in_explorer(target) is True
        from pathlib import Path as _P
        assert _P(calls[0].toLocalFile()) == tmp_path


class TestHelpAppendixIsDynamic:
    """帮助附录里的版本号与路径必须是运行时真实值。

    实测隐患：附录原先写死 "%APPDATA%\\EcoTidy"，但 %APPDATA% 不可写时
    程序会自动回退到别的目录，用户照着说明去找文件会找不到。
    """

    def test_version_is_filled_in(self):
        from utils import paths
        from ui.widgets import help_content

        text = help_content.section("appendix")
        assert paths.VERSION in text
        assert paths.APP_NAME in text
        assert "{version}" not in text
        assert "{app_name}" not in text

    def test_paths_match_runtime_values(self):
        from utils import paths
        from ui.widgets import help_content

        text = help_content.section("appendix")
        # 注意 TASKS_DIR 在附录里是 "…\tasks\" + "<任务ID>"，所以只断言目录本身
        for value in (paths.DATA_DIR, paths.TASKS_DIR, paths.EXPORT_DIR,
                      paths.ERROR_LOG_PATH):
            assert str(value) in text, value

    def test_no_hardcoded_appdata_path(self):
        """不许再写死 %APPDATA% 这种"看起来对、实际可能不对"的路径。"""
        from ui.widgets import help_content

        text = help_content.section("appendix")
        assert "%APPDATA%" not in text, "附录里不该出现写死的 %APPDATA% 路径"
        assert "我的文档\\\\EcoTidy" not in text


# ===========================================================================
# 四之八、Qt 告警看门狗
# ===========================================================================
class TestQtWarnings:
    """Qt 运行期告警的看门狗。

    为什么要测：Qt 的告警只在 stderr 打印一行，功能照常、界面照常，
    极容易被忽略；而新出现的告警往往意味着真的画错了东西。
    这里把所有 Qt 告警收集起来，只放行"已查明是 Qt 自身缺陷"的那一条，
    其余一律判失败 —— 这样将来冒出新告警时会被立刻发现。

    已放行的告警（实测结论，见 ui/main_window.py 中该样式规则的注释）：
        QPainter::end: Painter ended with N saved states
        —— 给 QSpinBox 做样式后，Qt 的 QStyleSheetStyle 绘制微调按钮箭头时
           save/restore 未配平。已验证：同样规则加在 QComboBox / QLineEdit
           上不出现；换 objectName 选择器、拆开 padding 都无效。
           属 Qt 内部缺陷，我们无法修复，且不影响显示与功能。
    """

    ALLOWED = ("QPainter::end: Painter ended with",)

    def test_no_unexpected_qt_warnings(self, window, qt_app):
        from PySide6.QtCore import qInstallMessageHandler

        collected = []
        previous = qInstallMessageHandler(
            lambda mode, context, message: collected.append(message))
        try:
            # 触发最容易出告警的交互：展开高级设置、切页、重绘
            window.goto_page(2)
            qt_app.processEvents()
            page = window.calc_page
            toggle = getattr(page, "_advanced_toggle", None)
            if toggle is not None:
                toggle.setChecked(True)
                qt_app.processEvents()
                toggle.setChecked(False)
                qt_app.processEvents()
            for index in range(5):
                window.goto_page(index)
                qt_app.processEvents()
                window.repaint()
                qt_app.processEvents()
        finally:
            qInstallMessageHandler(previous)

        unexpected = [message for message in collected
                      if not any(allowed in message for allowed in self.ALLOWED)]
        assert not unexpected, "出现未预期的 Qt 告警：\n" + "\n".join(unexpected)


# ===========================================================================
# 五、不留"开发中"这类推责文案
# ===========================================================================
class TestNoPlaceholderWording:
    def test_no_undefined_names_in_source(self):
        """回归防线：源码里不许有"用了但没导入"的名字。

        为什么要专门测这个（实测踩坑，栽过三次）：
            · main_window 里调用 ui_style.ask_modal 却没导入 ui_style；
            · settings 里用 Path 却没导入；
            · page_import 里引用了不存在的 map_text。
        这三处都恰好落在 `except Exception` 的兜底分支里，
        出错只写日志、界面看着一切正常，功能却静默失效 ——
        只有静态检查才能把它们揪出来。

        实现：先用 compile 保证语法正确，再用 pyflakes（若已安装）查未定义名字。
        没装 pyflakes 时跳过，不让测试变成对可选依赖的硬要求。
        """
        import io
        from contextlib import redirect_stdout

        try:
            from pyflakes import api as pyflakes_api
        except ImportError:      # pragma: no cover - 未安装则跳过
            pytest.skip("未安装 pyflakes，跳过静态检查")

        targets = [ROOT / "main.py"]
        for folder in ("core", "ui", "utils", "database"):
            targets.extend(sorted((ROOT / folder).rglob("*.py")))
        targets = [path for path in targets if "__pycache__" not in path.parts]

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            for path in targets:
                text = path.read_text(encoding="utf-8")
                pyflakes_api.check(text, str(path))
        report = buffer.getvalue()

        offenders = [line.strip() for line in report.splitlines()
                     if "undefined name" in line]
        assert not offenders, "源码里有未定义的名字：\n" + "\n".join(offenders)

    def test_no_user_visible_placeholder_wording(self):
        """用户可见文案里不得出现"开发中/后续阶段交付"这类推责说法。

        做法：先剥掉注释与文档字符串，只检查**真正的字符串字面量与代码**，
        避免把"我们绝不写『敬请期待』"这种策略说明本身误判成违规。
        """
        forbidden = ("正在开发中", "正在按阶段开发", "将在后续阶段交付",
                     "敬请期待", "暂未实现", "后续阶段交付", "功能开发中")
        offenders = []
        for path in sorted(ROOT.rglob("*.py")):
            rel = path.relative_to(ROOT)
            if rel.parts[0] in (".vs", "env", "temp", "release", ".ecodata", "tests"):
                continue
            if "__pycache__" in rel.parts:
                continue
            import io
            import tokenize

            with open(path, "r", encoding="utf-8") as handle:
                try:
                    tokens = list(tokenize.generate_tokens(handle.readline))
                except tokenize.TokenError:
                    continue
            # 只保留字符串字面量：注释与文档字符串都会以 STRING 出现，
            # 但文档字符串是模块/函数的第一条语句，这里用"是否含中文祈使语气"无法区分，
            # 因此改为检查所有 STRING，但把"否定式说明"（含 绝不/不得/禁止/避免）豁免。
            for token in tokens:
                if token.type != tokenize.STRING:
                    continue
                text = token.string
                if any(word in text for word in forbidden):
                    window_before = _line_text(path, token.start[0])
                    if any(mark in text for mark in ("绝不", "不得", "禁止", "避免", "不要")):
                        continue
                    if any(mark in window_before for mark in ('"""', "'''")):
                        continue
                    offenders.append("{0}:{1}".format(rel.as_posix(), token.start[0]))
        assert not offenders, "残留的用户可见占位文案：{0}".format(offenders)

    def test_all_five_pages_mounted(self, window):
        assert window.import_page is not None
        assert window.clean_page is not None
        assert window.calc_page is not None
        assert window.plot_page is not None
        assert window.export_page is not None


def _line_text(path: Path, number: int) -> str:
    """取某一行的原文（用于判断该字符串是否处在文档字符串里）。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    if 1 <= number <= len(lines):
        return lines[number - 1]
    return ""

def _code_lines(path: Path):
    """逐行产出源码，但跳过注释与文档字符串。

    为什么需要：`box.clickedButton() is ok` 这句话本身会出现在
    ui/style.py 的说明文字里，不剥掉就会把自己判成违规（实测踩坑）。
    """
    import io
    import tokenize

    try:
        with open(path, "r", encoding="utf-8") as handle:
            tokens = list(tokenize.generate_tokens(handle.readline))
    except (tokenize.TokenError, OSError):
        return
    skip_lines = set()
    for token in tokens:
        if token.type in (tokenize.COMMENT, tokenize.STRING):
            for number in range(token.start[0], token.end[0] + 1):
                skip_lines.add(number)
    lines = path.read_text(encoding="utf-8").splitlines()
    for number, line in enumerate(lines, 1):
        if number in skip_lines:
            continue
        yield number, line
