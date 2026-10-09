# -*- coding: utf-8 -*-
"""
历史任务对话框（开发方案 第六章 阶段 7 + 第五章易用性规范）

界面结构：
    顶部：搜索框 + 任务数量说明
    中部：任务列表（任务名 / 创建时间 / 数据类型 / 原始行数 / 清洗后行数 / 存档状态）
    底部：【载入选中任务】【重命名】【删除】【关闭】

易用性要点（方案第五章）：
    5.3 没有任何历史任务时显示空状态（tr("还没有历史任务。完成一次分析后会自动记录在这里。")）；
    5.5 双击即可恢复现场；存档失效时给出"原因 + 怎么办"，并提供【重新指定文件】入口；
    所有破坏性操作（删除）都要二次确认，并说明"只删本软件自己的存档，不动你的原始文件"。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import task_store
from core.app_state import AppState
from database import local_db
from ui import style as ui_style
from ui.widgets.empty_state import EmptyState
from utils.i18n import tr

# 列表列定义
_HEADERS = [tr("任务名"), tr("创建时间"), tr("数据类型"), tr("原始行数"), tr("清洗后行数"), tr("存档状态")]
_STATUS_OK = tr("可恢复")
_STATUS_MISSING = tr("存档已丢失")


class HistoryDialog(QDialog):
    """历史任务列表对话框。

    对外接口：
        selected_task()  当前选中的任务记录（没有则 None）
    信号：
        task_load_requested(str)  用户要求载入某个任务（由主窗口执行恢复）
    """

    task_load_requested = Signal(str)

    def __init__(self, app_state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("历史任务"))
        self.setObjectName("history_dialog")
        self.setModal(True)
        self.setMinimumSize(820, 520)
        self.app_state = app_state
        self._records: List[local_db.TaskRecord] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(10)

        # ---- 顶部：说明 + 搜索 ----
        intro = QLabel(
            "这里保存着以前的分析任务。<b>双击</b>某一行即可把当时的数据与结果重新载入软件，"
            "继续分析或重新导出。", self)
        intro.setWordWrap(True)
        intro.setObjectName("history_intro")
        outer.addWidget(intro)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        search_row.addWidget(QLabel(tr("搜索："), self))
        self._search = QLineEdit(self)
        self._search.setPlaceholderText(tr("输入任务名或数据类型的关键词…"))
        self._search.setClearButtonEnabled(True)
        self._search.setMinimumHeight(30)
        self._search.textChanged.connect(lambda _text: self.refresh())
        search_row.addWidget(self._search, 1)
        self._count_label = QLabel("", self)
        self._count_label.setObjectName("history_count")
        search_row.addWidget(self._count_label)
        outer.addLayout(search_row)

        # ---- 中部：列表 / 空状态 ----
        self._stack = QStackedWidget(self)

        self._empty = EmptyState(self)
        self._empty.set_icon("history")
        self._empty.set_description(tr("还没有历史任务。完成一次分析后会自动记录在这里。"))
        self._empty.set_action(tr("关闭"))
        self._empty.action_clicked.connect(self.reject)
        self._stack.addWidget(self._empty)

        self._tree = QTreeWidget(self)
        self._tree.setObjectName("history_tree")
        self._tree.setColumnCount(len(_HEADERS))
        self._tree.setHeaderLabels(_HEADERS)
        self._tree.setRootIsDecorated(False)
        self._tree.setAlternatingRowColors(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.setSortingEnabled(True)
        self._tree.itemDoubleClicked.connect(lambda _item, _col: self._load_selected())
        self._tree.itemSelectionChanged.connect(self._update_buttons)
        header = self._tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(_HEADERS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self._stack.addWidget(self._tree)

        outer.addWidget(self._stack, 1)

        # ---- 底部：操作按钮 ----
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self._load_button = QPushButton(tr("载入选中任务"), self)
        self._load_button.setObjectName("primary_button")
        self._load_button.setMinimumSize(130, 34)
        self._load_button.setToolTip(tr("载入该任务（也可双击列表）"))
        self._load_button.clicked.connect(self._load_selected)
        buttons.addWidget(self._load_button)

        self._rename_button = QPushButton(tr("重命名"), self)
        self._rename_button.setObjectName("secondary_button")
        self._rename_button.setMinimumSize(90, 34)
        self._rename_button.clicked.connect(self._rename_selected)
        buttons.addWidget(self._rename_button)

        self._delete_button = QPushButton(tr("删除"), self)
        self._delete_button.setObjectName("secondary_button")
        self._delete_button.setMinimumSize(90, 34)
        self._delete_button.setToolTip(tr("删除记录与存档，不动原始文件"))
        self._delete_button.clicked.connect(self._delete_selected)
        buttons.addWidget(self._delete_button)

        self._relink_button = QPushButton(tr("重新指定文件…"), self)
        self._relink_button.setObjectName("secondary_button")
        self._relink_button.setMinimumSize(140, 34)
        self._relink_button.setToolTip(tr("存档丢失时重新选择数据文件"))
        self._relink_button.clicked.connect(self._relink_selected)
        buttons.addWidget(self._relink_button)

        buttons.addStretch(1)
        close_button = QPushButton(tr("关闭"), self)
        close_button.setObjectName("secondary_button")
        close_button.setMinimumSize(90, 34)
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        outer.addLayout(buttons)

        self._hint = ui_style.HintQLabel("", self)
        self._hint.setObjectName("history_hint")
        self._hint.setWordWrap(True)
        outer.addWidget(self._hint)

        self.refresh()

    # =====================================================================
    # 列表维护
    # =====================================================================
    def refresh(self) -> None:
        """按搜索框内容重新加载任务列表。"""
        keyword = self._search.text().strip()
        try:
            self._records = task_store.list_history(keyword)
        except task_store.TaskError as exc:
            self._records = []
            self._hint.setText(str(exc))
            self._hint.set_state("warning")

        self._stack.setCurrentIndex(1 if self._records else 0)
        self._tree.setSortingEnabled(False)
        self._tree.clear()
        for record in self._records:
            item = QTreeWidgetItem(self._tree)
            item.setData(0, Qt.ItemDataRole.UserRole, record.task_id)
            item.setText(0, record.name)
            item.setText(1, record.display_time())
            item.setText(2, record.data_type or "—")
            item.setText(3, "{0:,}".format(record.raw_rows))
            item.setText(4, "{0:,}".format(record.clean_rows) if record.clean_rows else "—")
            item.setText(5, _STATUS_OK if record.snapshot_exists else _STATUS_MISSING)
            if not record.snapshot_exists:
                item.setForeground(5, Qt.GlobalColor.darkYellow)
                item.setToolTip(5, "该任务的存档目录已被移动或删除，无法直接恢复；"
                                   "可用【重新指定文件…】重新选择原始数据")
            item.setToolTip(0, "双击即可载入这个任务")
        self._tree.setSortingEnabled(True)

        total = len(self._records)
        if keyword:
            self._count_label.setText("找到 {0} 条匹配的任务".format(total))
        else:
            self._count_label.setText("共 {0} 条历史任务".format(total))
        if total and not any(record.snapshot_exists for record in self._records):
            self._hint.setText("列表里的任务存档都已丢失，可用【重新指定文件…】重新选择原始数据。")
            self._hint.set_state("warning")
        self._update_buttons()

    def selected_task(self) -> Optional[local_db.TaskRecord]:
        """返回当前选中的任务记录（未选中返回 None）。"""
        items = self._tree.selectedItems()
        if not items:
            return None
        task_id = items[0].data(0, Qt.ItemDataRole.UserRole)
        for record in self._records:
            if record.task_id == task_id:
                return record
        return None

    def _update_buttons(self) -> None:
        """按选中状态启用/禁用按钮，并说明原因。"""
        record = self.selected_task()
        has_selection = record is not None
        self._load_button.setEnabled(has_selection)
        self._rename_button.setEnabled(has_selection)
        self._delete_button.setEnabled(has_selection)
        self._relink_button.setEnabled(has_selection)
        if not has_selection:
            reason = tr("请先在列表里选择一条任务")
            for button in (self._load_button, self._rename_button,
                           self._delete_button, self._relink_button):
                button.setToolTip(reason)
            return
        if record.snapshot_exists:
            self._load_button.setToolTip("把该任务的数据与结果重新载入软件（也可以直接双击列表）")
        else:
            self._load_button.setToolTip(tr("存档已丢失，请用【重新指定文件…】"))

    # =====================================================================
    # 操作
    # =====================================================================
    def _load_selected(self) -> None:
        """请求主窗口载入选中的任务。"""
        record = self.selected_task()
        if record is None:
            QMessageBox.information(self, tr("请先选择任务"), "请先在列表里点击要载入的任务。")
            return
        if not record.snapshot_exists:
            self._explain_missing(record)
            return
        self.task_load_requested.emit(record.task_id)
        self.accept()

    def _explain_missing(self, record: local_db.TaskRecord) -> None:
        """存档失效时的友好提示：原因 + 怎么办 + 直接给修复入口。"""
        box = QMessageBox(self)
        box.setWindowTitle("这个任务的存档已丢失")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("无法恢复「{0}」。".format(record.name))
        box.setInformativeText(
            "原因：任务存档目录已经不在了。\n"
            "　{0}\n\n"
            "常见情况：手动清理过 %APPDATA%\\EcoTidy，或换了一台电脑。\n\n"
            "怎么办：\n"
            "　① 用【重新指定文件…】重新选择当时的数据文件，即可重新分析一次；\n"
            "　② 或直接删除这条已经没有用的历史记录。\n\n"
            "注意：这个任务以前导出的成果文件仍然在你当初保存的位置，不受影响。".format(
                record.snapshot_dir or "（未记录存档路径）"))
        relink_text = tr("重新指定文件…")
        box.addButton(relink_text, QMessageBox.ButtonRole.ActionRole)
        box.addButton(tr("知道了"), QMessageBox.ButtonRole.RejectRole)
        if ui_style.ask_modal(box, relink_text):
            self._relink_selected()

    def _rename_selected(self) -> None:
        """重命名任务。"""
        record = self.selected_task()
        if record is None:
            return
        new_name, ok = QInputDialog.getText(self, tr("重命名任务"), tr("新的任务名："),
                                            text=record.name)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name:
            QMessageBox.information(self, tr("任务名不能为空"), tr("请输入一个名字后再确定。"))
            return
        try:
            task_store.rename_task(record.task_id, new_name, state=self.app_state)
        except task_store.TaskError as exc:
            QMessageBox.warning(self, tr("重命名失败"), str(exc))
            return
        self._hint.setText("已重命名为「{0}」。".format(new_name))
        self._hint.set_state("warning")
        self.refresh()

    def _delete_selected(self) -> None:
        """删除任务（二次确认，并说明不会动原始文件）。"""
        record = self.selected_task()
        if record is None:
            return
        answer = QMessageBox.question(
            self, tr("确认删除这条历史任务？"),
            "将删除历史任务「{0}」及其存档。\n\n"
            "不会删除你的任何原始数据文件，也不会删除以前导出的成果文件。\n\n"
            "确定要删除吗？".format(record.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            task_store.delete_task(record.task_id, state=self.app_state)
        except task_store.TaskError as exc:
            QMessageBox.warning(self, tr("删除失败"), str(exc))
            return
        self._hint.setText("已删除「{0}」。".format(record.name))
        self._hint.set_state("normal")
        self.refresh()

    def _relink_selected(self) -> None:
        """存档失效时的修复入口：让主窗口走一遍导入流程。"""
        self._hint.setText(
            "已关闭历史任务列表。请到【数据导入】页选择当时的数据文件重新导入，"
            "然后按需要重新执行清洗与计算。")
        self._hint.set_state("warning")
        self.done(2)          # 自定义返回码：主窗口据此跳到数据导入页
