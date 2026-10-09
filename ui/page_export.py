# -*- coding: utf-8 -*-
"""
报告导出页面（开发方案 第六章 阶段 6 + 第五章易用性规范）

页面结构：
    成果项勾选（默认全选）+ 输出文件夹选择 + 一键导出按钮
    成果预览（将导出什么、当前有哪些数据）
    进度条 + 导出结果说明

易用性要点（方案第五章）：
    5.3 暂无可导出的成果时显示空状态（"暂无可导出的成果，请先完成前面的步骤。"）；
    5.6 「导出即所得」：默认全选所有成果项，点【一键导出】即可；
        导出完成后弹窗展示**实际写入的文件数量、目标路径**，并提供【打开输出文件夹】按钮；
    5.1 输出文件夹记忆上次选择（core/settings.py）。

纪律：本页只读 AppState、只调 core/report_make 写文件，不修改任何数据表。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core import report_make, settings
from core.app_state import AppState
from ui import style as ui_style
from ui.widgets.empty_state import EmptyState
from utils.errors import reveal_in_explorer
from utils.i18n import tr

#: 默认导出目录被换位置时的提示模板。
#: 用 {0} 占位、把路径单独存起来，这样标签文字始终是一个"可查表的模板"，
#: 切换语言时能正确重译（直接拼中文路径进去会漏翻，实测踩坑）。
_DIR_NOTICE_TEMPLATE = ("提示：系统「我的文档」不可写，输出文件夹已自动设为 {0}，"
                        "可以点【选择文件夹…】改成其它位置。")

# 成果项的中文说明（鼠标悬浮即可看到"这项是什么"）
_ITEM_TIPS: Dict[str, str] = {
    report_make.ITEM_CLEAN_TABLE: "清洗后的数据表，写入 Excel 工作簿的一个工作表。",
    report_make.ITEM_ROW_INDEX: "行级指标表（每行 = 某样方中的某物种），含重要值等。",
    report_make.ITEM_SUMMARY_INDEX: "汇总指标表（每行 = 一个样方或一个分组），含多样性指数。",
    report_make.ITEM_CLEAN_LOG: "中文清洗日志：删了什么、改了什么、为什么改，写方法部分可直接引用。",
    report_make.ITEM_FIGURES: "把【可视化绘图】页导出的图片一并复制到成果文件夹。",
    report_make.ITEM_REPORT: "自动生成四章结构的中文分析报告（数据信息 / 清洗统计 / 指标汇总 / 质量评价）。",
}


class ExportPage(QWidget):
    """【报告导出】页面。

    对外接口（供主窗口调用）：
        export_all()   一键导出（Ctrl+R / Ctrl+E 在本页的主动作）
    """

    def __init__(self, app_state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("export_page")
        self.app_state = app_state

        self._item_boxes: Dict[str, QCheckBox] = {}
        self._last_result: Optional[report_make.ExportResult] = None
        self._history_note: str = ""

        self._build_ui()
        self._refresh_from_state()
        self._warn_if_default_dir_changed()
        self.app_state.data_changed.connect(self._on_data_changed)

    # =====================================================================
    # 一、界面搭建
    # =====================================================================
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        root.addWidget(self._build_option_area())

        self._stack = QStackedWidget(self)
        self._stack.setObjectName("export_stack")

        self._empty = EmptyState(self)
        self._empty.set_icon("document")
        self._empty.set_description("暂无可导出的成果，请先完成前面的步骤。")
        self._empty.set_action("前往【数据导入】")
        self._empty.secondary_clicked.connect(lambda: self._goto_page(2))
        self._empty.set_secondary_action("前往【指标计算】")
        self._empty.action_clicked.connect(lambda: self._goto_page(0))
        self._stack.addWidget(self._empty)

        self._stack.addWidget(self._build_preview_area())
        root.addWidget(self._stack, 1)

        self._hint = ui_style.HintQLabel("", self)
        self._hint.setObjectName("export_hint")
        self._hint.setWordWrap(True)
        root.addWidget(self._hint)

    def _build_option_area(self) -> QWidget:
        holder = QFrame(self)
        holder.setObjectName("export_option_area")
        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)

        # ---- 成果项勾选（默认全选）----
        grid.addWidget(QLabel(tr("要导出的成果："), holder), 0, 0)
        items_row = QHBoxLayout()
        items_row.setSpacing(14)
        for item in report_make.ALL_ITEMS:
            box = QCheckBox(item, holder)
            box.setChecked(True)          # 方案 5.6：默认全选
            box.setToolTip(report_make.ITEM_PURPOSE.get(item, ""))
            box.toggled.connect(lambda _checked: self._update_plan())
            items_row.addWidget(box)
            self._item_boxes[item] = box
        items_row.addStretch(1)
        items_widget = QWidget(holder)
        items_widget.setLayout(items_row)
        grid.addWidget(items_widget, 0, 1, 1, 3)

        # ---- 任务名 ----
        grid.addWidget(QLabel(tr("任务名称："), holder), 1, 0)
        self._task_edit = QLineEdit(holder)
        self._task_edit.setPlaceholderText("用于文件命名，例如「2024年6月样方调查」")
        self._task_edit.setMinimumWidth(280)
        self._task_edit.setToolTip(tr("用于文件命名"))
        self._task_edit.textChanged.connect(lambda _text: self._update_plan())
        grid.addWidget(self._task_edit, 1, 1)

        # ---- 输出文件夹 ----
        grid.addWidget(QLabel(tr("输出文件夹："), holder), 2, 0)
        self._dir_edit = QLineEdit(holder)
        self._dir_edit.setReadOnly(True)
        self._dir_edit.setMinimumWidth(360)
        self._dir_edit.setToolTip("默认「我的文档」下的 EcoTidy 文件夹")
        grid.addWidget(self._dir_edit, 2, 1)

        self._choose_dir_button = QPushButton(tr("选择文件夹…"), holder)
        self._choose_dir_button.setObjectName("secondary_button")
        self._choose_dir_button.setMinimumSize(120, 34)
        self._choose_dir_button.clicked.connect(self.choose_directory)
        grid.addWidget(self._choose_dir_button, 2, 2)

        self._export_button = QPushButton(tr("一键导出"), holder)
        self._export_button.setObjectName("primary_button")
        self._export_button.setMinimumSize(130, 34)
        self._export_button.setToolTip(tr("导出勾选的成果（Ctrl+E）"))
        self._export_button.clicked.connect(self.export_all)
        grid.addWidget(self._export_button, 2, 3)

        # ---- 进度条（长任务可见反馈）----
        self._progress = QProgressBar(holder)
        self._progress.setObjectName("export_progress")
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        self._progress.setVisible(False)
        grid.addWidget(self._progress, 3, 0, 1, 4)

        # ---- 实时计划提示 ----
        self._plan_label = ui_style.HintQLabel("", holder)
        self._plan_label.setObjectName("export_plan")
        self._plan_label.setWordWrap(True)
        grid.addWidget(self._plan_label, 4, 0, 1, 4)

        # 默认输出目录（记忆上次选择；没有记录时用可写的默认位置）
        prefs = settings.instance()
        default_dir = prefs.export_dir or str(self._default_export_dir())
        self._dir_edit.setText(default_dir)
        return holder

    def _warn_if_default_dir_changed(self) -> None:
        """默认导出目录被换到非「我的文档」位置时，说明一下原因。"""
        try:
            from utils import paths

            if paths.export_dir_is_default():
                return
            # 保存路径值与状态，供切换语言时重新拼装（set_state 会重刷文字）
            self._dir_notice_value = self._dir_edit.text()
            self._hint.set_state("warning")
            self._hint.setText(tr(_DIR_NOTICE_TEMPLATE, self._dir_notice_value))
        except Exception:  # noqa: BLE001 - 纯提示，失败不影响使用
            pass

    @staticmethod
    def _default_export_dir() -> Path:
        """默认导出位置：优先「我的文档/EcoTidy」，不可写时自动换到可写处。

        为什么不能无脑用「我的文档」：受管控的电脑上它常常不可写，
        用户点【一键导出】会直接撞上报错，而他什么都没做错。
        """
        try:
            from utils import paths

            return paths.default_export_dir()
        except Exception:  # pragma: no cover
            return Path.home() / "Documents" / paths.APP_NAME

    def _build_preview_area(self) -> QWidget:
        """成果预览：当前有哪些数据、将导出哪些文件。"""
        holder = QFrame(self)
        holder.setObjectName("export_preview_area")
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self._preview_label = QLabel("", holder)
        self._preview_label.setObjectName("export_preview")
        self._preview_label.setWordWrap(True)
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._preview_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._preview_label, 1)
        return holder

    # =====================================================================
    # 二、状态
    # =====================================================================
    def _on_data_changed(self, key: str) -> None:
        if key in ("clean", "index_row", "index_summary", "figures", "clean_log", "*"):
            self._refresh_from_state()

    def available_items(self) -> List[str]:
        """当前真正有内容可导出的成果项。"""
        available: List[str] = []
        if self.app_state.has("clean") or self.app_state.has("index_row") \
                or self.app_state.has("index_summary"):
            if self.app_state.has("clean"):
                available.append(report_make.ITEM_CLEAN_TABLE)
            if self.app_state.has("index_row"):
                available.append(report_make.ITEM_ROW_INDEX)
            if self.app_state.has("index_summary"):
                available.append(report_make.ITEM_SUMMARY_INDEX)
        if self.app_state.get_text("clean_log").strip():
            available.append(report_make.ITEM_CLEAN_LOG)
        if self.app_state.get_list("figures"):
            available.append(report_make.ITEM_FIGURES)
        # 只要有任一数据就能生成报告
        if available:
            available.append(report_make.ITEM_REPORT)
        return available

    def selected_items(self) -> List[str]:
        return [item for item, box in self._item_boxes.items() if box.isChecked()]

    def _refresh_from_state(self) -> None:
        """按当前数据情况切换空状态/预览区，并更新提示。"""
        available = self.available_items()
        has_any = bool(available)
        self._stack.setCurrentIndex(1 if has_any else 0)
        self._export_button.setEnabled(has_any)
        if not has_any:
            self._export_button.setToolTip(tr("请先导入并清洗数据"))
            self._preview_label.setText("")
            return
        self._export_button.setToolTip("把勾选的成果一次性导出到上面的文件夹（Ctrl+E）")
        self._render_preview()
        self._update_plan()

    def _render_preview(self) -> None:
        """列出"现在有哪些数据、各多少行"，让用户点导出之前心里有数。"""
        lines: List[str] = ["当前可导出的成果："]
        clean = self.app_state.get_table("clean")
        if clean is not None:
            lines.append("　· 清洗后数据：{0:,} 行 × {1} 列".format(len(clean), len(clean.columns)))
        row_index = self.app_state.get_table("index_row")
        if row_index is not None:
            lines.append("　· 行级指标表：{0:,} 行（每行 = 某样方中的某物种）".format(len(row_index)))
        summary_index = self.app_state.get_table("index_summary")
        if summary_index is not None:
            lines.append("　· 汇总指标表：{0:,} 行（每行 = 一个样方或分组）".format(len(summary_index)))
        log_text = self.app_state.get_text("clean_log")
        if log_text.strip():
            lines.append("　· 清洗日志：{0:,} 个字符".format(len(log_text)))
        figures = self.app_state.get_list("figures")
        if figures:
            lines.append("　· 绘图图片：{0} 个文件".format(len(figures)))
        data_type = self.app_state.get_text("data_type")
        if data_type:
            lines.append("")
            lines.append("数据类型：{0}".format(data_type))
        lines.append("")
        lines.append("导出后会在这个文件夹里生成：数据表 xlsx（多工作表）、清洗日志 txt、"
                     "分析报告 txt、图片文件，以及一份《成果说明.txt》逐条说明每个文件是什么。")
        self._preview_label.setText("\n".join(lines))

    def _update_plan(self) -> None:
        """实时说明"这次会导出哪些文件"。"""
        selected = self.selected_items()
        if not selected:
            self._plan_label.setText("还没有勾选任何成果项，请至少勾选一项。")
            self._plan_label.set_state("error")
            return
        available = set(self.available_items())
        pending = [item for item in selected if item not in available]
        text = "将导出：{0}".format("、".join(selected))
        if pending:
            text += "　（其中 {0} 目前没有内容，会被自动跳过并说明原因）".format("、".join(pending))
        task = self._task_edit.text().strip() or report_make.DEFAULT_TASK_NAME
        text += "　｜　文件命名：{0}_内容类型_时间戳".format(task)
        self._plan_label.setText(text)
        self._plan_label.set_state("normal")

    # =====================================================================
    # 三、选择目录与导出
    # =====================================================================
    def choose_directory(self) -> None:
        """选择输出文件夹（默认从上次导出的位置开始）。"""
        start = self._dir_edit.text() or str(self._default_export_dir())
        directory = QFileDialog.getExistingDirectory(self, "选择输出文件夹", start)
        if directory:
            self._dir_edit.setText(directory)

    def export_all(self) -> bool:
        """一键导出：把勾选的成果写入目标文件夹。"""
        if not self.available_items():
            QMessageBox.information(
                self, tr("暂无可导出的成果"),
                "请先完成数据导入与清洗。\n\n最快的办法：到【数据导入】页点【载入示例数据】，"
                "再依次执行清洗与指标计算。")
            return False

        selected = self.selected_items()
        if not selected:
            QMessageBox.information(self, tr("还没有勾选成果项"),
                                    "请至少勾选一项要导出的成果，例如「分析报告」。")
            return False

        directory_text = self._dir_edit.text().strip()
        if not directory_text:
            QMessageBox.information(self, tr("还没有选择输出文件夹"),
                                    "请点【选择文件夹…】指定一个保存位置。")
            return False
        target = Path(directory_text)

        task_name = self._task_edit.text().strip() or report_make.DEFAULT_TASK_NAME
        self._set_busy(True, "正在导出成果…")
        try:
            result = report_make.export_all(
                target, task_name=task_name, items=selected,
                raw=self.app_state.get_table("raw"),
                clean=self.app_state.get_table("clean"),
                row_index=self.app_state.get_table("index_row"),
                summary_index=self.app_state.get_table("index_summary"),
                clean_log=self.app_state.get_text("clean_log"),
                data_type=self.app_state.get_text("data_type"),
                figure_files=self.app_state.get_list("figures"),
            )
        except report_make.ReportError as exc:
            self._set_busy(False)
            QMessageBox.warning(self, tr("导出失败"), str(exc))
            return False
        except Exception as exc:  # noqa: BLE001 - 交给全局异常处理显示中文提示
            self._set_busy(False)
            from utils.errors import report_exception

            report_exception(exc, context="导出成果")
            return False
        finally:
            self._set_busy(False)
            self._progress.setVisible(False)
            self._progress.setValue(0)

        self._last_result = result
        settings.instance().export_dir = str(result.directory)
        self._dir_edit.setText(str(result.directory))

        # 自动保存为历史任务（方案阶段 7 第 5 条：每次分析完成后记录，可随时恢复现场）。
        # 失败不影响本次导出结果，只提示、不中断。
        self._save_to_history(task_name)
        self._show_success(result)
        return True

    def _save_to_history(self, task_name: str) -> None:
        """把本次成果保存成一条历史任务记录（失败只提示，不影响导出）。"""
        try:
            from core import task_store

            record = task_store.save_current_task(self.app_state, name=task_name)
        except Exception as exc:  # noqa: BLE001 - 历史任务失败不应影响导出
            self._history_note = "历史任务记录失败：{0}".format(exc)
            return
        if record is not None:
            self._history_note = "已记录到历史任务「{0}」，下次可在左侧【历史任务】里一键恢复。".format(
                record.name)
        else:
            self._history_note = ""

    def retranslate(self) -> None:
        """按当前语言重刷本页文案（切换语言时由主窗口调用）。

        QComboBox 的下拉项、按钮文字由主窗口的通用遍历负责；
        这里只处理"带实参、无法靠查表重译"的那条提示 —— 它的文字里
        嵌了实际路径，必须用模板 + 存下来的值重新拼一遍。
        """
        value = getattr(self, "_dir_notice_value", "")
        if value and self._hint.text():
            self._hint.setText(tr(_DIR_NOTICE_TEMPLATE, value))

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._export_button.setEnabled(not busy)
        self._choose_dir_button.setEnabled(not busy)
        self._progress.setVisible(busy)
        if busy:
            self._progress.setValue(35)
            self._hint.setText(message)
            self._hint.set_state("info")
        else:
            self._refresh_from_state()

    def _show_success(self, result: report_make.ExportResult) -> None:
        """导出完成弹窗：文件数量 + 目标路径 + 【打开输出文件夹】按钮（方案 5.6）。"""
        self._hint.setText(
            "导出完成：共写入 {0} 个文件到 {1}。".format(result.count(), result.directory))
        self._hint.set_state("warning")

        box = QMessageBox(self)
        box.setWindowTitle(tr("导出完成"))
        box.setIcon(QMessageBox.Icon.Information)
        box.setText("已成功导出 {0} 个文件。".format(result.count()))
        detail_lines = ["保存位置：", str(result.directory), "", "本次导出的文件："]
        for path in sorted(result.files):
            detail_lines.append("　· {0}".format(path.name))
        if result.notes:
            detail_lines.append("")
            detail_lines.append(tr("说明："))
            for note in result.notes:
                detail_lines.append("　· {0}".format(note))
        history_note = getattr(self, "_history_note", "")
        if history_note:
            detail_lines.append("")
            detail_lines.append(history_note)
        box.setInformativeText("\n".join(detail_lines))
        open_text = tr("打开输出文件夹")
        box.addButton(open_text, QMessageBox.ButtonRole.ActionRole)
        box.addButton(tr("关闭"), QMessageBox.ButtonRole.RejectRole)
        # 用 ui_style.ask_modal 判断点了哪个按钮：直接比较按钮对象在模态框
        # 返回后会失效，导致"点了打开文件夹却没反应"（实测 bug）
        if ui_style.ask_modal(box, open_text):
            # 避免用户找不到文件（方案 5.6「导出即所得」）
            reveal_in_explorer(result.directory)

    # =====================================================================
    # 四、供主窗口查询
    # =====================================================================
    def last_result(self) -> Optional[report_make.ExportResult]:
        return self._last_result

    def has_exportable(self) -> bool:
        return bool(self.available_items())

    def _goto_page(self, index: int) -> None:
        window = self.window()
        handler = getattr(window, "goto_page", None)
        if callable(handler):
            handler(index)


def create_page(app_state: AppState) -> QWidget:
    """页面工厂：主窗口用本函数挂载真实导出页。"""
    return ExportPage(app_state)
