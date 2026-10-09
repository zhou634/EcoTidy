# -*- coding: utf-8 -*-
"""
智能清洗页面（开发方案 第六章 阶段 3 + 第五章易用性规范）

页面结构（自上而下）：
    选项区：三个复选框（默认全选）+「高级设置」折叠面板 + 动作按钮
    对比区：清洗前 / 清洗后 切换查看，被修改的单元格高亮
    结果区：清洗日志（中文分条）+ 修改明细表（可排序、可导出）

易用性要点（方案第五章逐条对应）：
    5.4 高级参数默认收在「高级设置」折叠面板里，并标注"不确定就用默认值"；
        每个参数右侧给出通俗解释与推荐值；调整参数后即时显示预计影响；
    5.5 清洗完成后提供前后对比视图并高亮被修改的单元格；日志为中文分条摘要；
        明细以表格列出「行号 / 列名 / 原值 / 处理方式」，可排序、可导出；
    5.3 没有数据时显示空状态（tr("请先导入数据。")+【前往【数据导入】】按钮）。

强制纪律（方案 3.3 第 4 条）：
    每次执行都从 app_state["raw"] 重新计算，绝不在上一次结果上二次清洗。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableView,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core import data_clean, data_parse, settings
from core.app_state import AppState
from ui import style as ui_style
from ui.widgets.empty_state import EmptyState
from ui.widgets.pandas_model import PandasModel, column_tips_from_names
from utils.i18n import tr

# 对比视图的三种模式
VIEW_BEFORE = tr("清洗前")
VIEW_AFTER = tr("清洗后")
VIEW_DIFF = "只看被修改的单元格"

# 被修改单元格的高亮色（浅橙，与主色蓝不冲突，且打印出来仍可辨）
HIGHLIGHT_BACKGROUND = "#EDEEF0"
HIGHLIGHT_TOOLTIP_PREFIX = "本单元格已被清洗修改："


class CleanPage(QWidget):
    """【智能清洗】页面。

    对外接口（供主窗口调用）：
        run_clean()          执行清洗（Ctrl+R 在本页的主动作）
        has_clean_data()     是否已经清洗出结果
    """

    def __init__(self, app_state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("clean_page")
        self.app_state = app_state

        self._result: Optional[data_clean.CleanResult] = None
        self._raw_snapshot: Optional[Any] = None       # 本次清洗所用的输入快照
        self._change_map: Dict[tuple, str] = {}        # (行号, 列名) -> 处理方式
        self._building = False                         # 防止程序化改动控件触发回调

        self._build_ui()
        self._load_params_from_settings()
        self._refresh_from_state()
        self.app_state.data_changed.connect(self._on_data_changed)

    # =====================================================================
    # 一、界面搭建
    # =====================================================================
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        root.addWidget(self._build_option_bar())

        # 预览 / 空状态 二选一
        self._stack = QStackedWidget(self)
        self._stack.setObjectName("clean_stack")

        self._empty = EmptyState(self)
        self._empty.set_icon("broom")
        self._empty.set_description(tr("请先导入数据。"))
        self._empty.set_action("前往【数据导入】")
        self._empty.action_clicked.connect(lambda: self._goto_page(0))
        self._stack.addWidget(self._empty)

        self._stack.addWidget(self._build_result_area())
        root.addWidget(self._stack, 1)

        self._hint = ui_style.HintQLabel("", self)
        self._hint.setObjectName("clean_hint")
        self._hint.setWordWrap(True)
        root.addWidget(self._hint)

    def _build_option_bar(self) -> QWidget:
        """选项区：复选框 + 高级设置 + 动作按钮。"""
        holder = QFrame(self)
        holder.setObjectName("clean_option_bar")
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # ---- 第一行：三项清洗开关（默认全选）+ 动作按钮 ----
        row = QHBoxLayout()
        row.setSpacing(12)

        self._check_outlier = QCheckBox(tr("启用生态异常值检测"), holder)
        self._check_outlier.setChecked(True)
        self._check_outlier.setToolTip(
            tr("负值、超范围值，以及时序单点突变"))
        row.addWidget(self._check_outlier)

        self._check_fill = QCheckBox(tr("启用缺失值填充"), holder)
        self._check_fill.setChecked(True)
        self._check_fill.setToolTip(
            tr("时序插值，样方按分组中位数填补"))
        row.addWidget(self._check_fill)

        self._check_dedup = QCheckBox(tr("启用自动去重"), holder)
        self._check_dedup.setChecked(True)
        self._check_dedup.setToolTip(tr("删除完全重复的行，保留首条"))
        row.addWidget(self._check_dedup)

        for box in (self._check_outlier, self._check_fill, self._check_dedup):
            box.toggled.connect(self._on_option_changed)

        row.addStretch(1)

        self._advanced_toggle = QPushButton(tr("高级设置 ▸"), holder)
        self._advanced_toggle.setObjectName("secondary_button")
        self._advanced_toggle.setMinimumSize(120, 34)
        self._advanced_toggle.setCheckable(True)
        self._advanced_toggle.setToolTip(tr("突变阈值、最大插值间隙等（默认即可）"))
        self._advanced_toggle.toggled.connect(self._on_advanced_toggled)
        row.addWidget(self._advanced_toggle)

        self._run_button = QPushButton(tr("一键执行清洗"), holder)
        self._run_button.setObjectName("primary_button")
        self._run_button.setMinimumSize(130, 34)
        self._run_button.setToolTip(tr("按当前选项清洗（Ctrl+R）"))
        self._run_button.clicked.connect(self.run_clean)
        row.addWidget(self._run_button)

        self._log_button = QPushButton(tr("查看清洗日志"), holder)
        self._log_button.setObjectName("secondary_button")
        self._log_button.setMinimumSize(120, 34)
        self._log_button.setToolTip(tr("查看清洗说明与明细"))
        self._log_button.clicked.connect(self._show_log_tab)
        row.addWidget(self._log_button)

        self._save_button = QPushButton(tr("保存清洗后数据"), holder)
        self._save_button.setObjectName("secondary_button")
        self._save_button.setMinimumSize(140, 34)
        self._save_button.setToolTip(tr("另存为 csv 或 xlsx"))
        self._save_button.clicked.connect(self.save_cleaned_data)
        row.addWidget(self._save_button)

        layout.addLayout(row)

        # ---- 第二行：高级设置折叠面板（默认收起，方案 5.4）----
        self._advanced_panel = QGroupBox("高级设置（不确定就用默认值）", holder)
        self._advanced_panel.setObjectName("advanced_panel")
        form = QFormLayout(self._advanced_panel)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(8)

        self._mad_spin = QDoubleSpinBox(self._advanced_panel)
        self._mad_spin.setRange(1.0, 20.0)
        self._mad_spin.setSingleStep(0.5)
        self._mad_spin.setDecimals(1)
        self._mad_spin.setValue(data_clean.DEFAULT_MAD_K)
        self._mad_spin.setMinimumWidth(90)
        self._mad_spin.valueChanged.connect(self._on_option_changed)
        form.addRow(self._label_with_tip(
            "突变检测阈值（倍 MAD）",
            "偏离当日中位数超过几倍 MAD 才算异常。默认 3。\n"
            "调小会更严格（可能把真实极值也改掉），调大会更宽松。\n"
            "软件用 MAD 而不是标准差，因为生态数据不正态，用标准差会误杀季节性峰值。"),
            self._mad_spin)

        self._gap_spin = QSpinBox(self._advanced_panel)
        self._gap_spin.setRange(0, 100)
        self._gap_spin.setValue(data_clean.DEFAULT_MAX_GAP)
        self._gap_spin.setMinimumWidth(90)
        self._gap_spin.setToolTip(tr("0 = 不插值"))
        self._gap_spin.valueChanged.connect(self._on_option_changed)
        form.addRow(self._label_with_tip(
            "最大插值间隙（个点）",
            "数据中断不超过几个点时才自动补值。默认 3。\n"
            "缺口更长时保留空白，避免凭空造数据。设为 0 表示完全不插值。"),
            self._gap_spin)

        self._advanced_panel.setVisible(False)
        layout.addWidget(self._advanced_panel)

        # ---- 第三行：参数实时反馈（方案 5.4）----
        self._impact = ui_style.HintQLabel("", holder)
        self._impact.setObjectName("clean_impact")
        self._impact.setWordWrap(True)
        layout.addWidget(self._impact)

        return holder

    @staticmethod
    def _label_with_tip(title: str, tip: str) -> QLabel:
        """构造带问号说明的参数标签（参数控件右侧必须给出通俗解释）。"""
        label = QLabel("{0} <span style='color:#5A5F66;font-weight:bold'>?</span>".format(title))
        label.setToolTip(tip)
        label.setWordWrap(True)
        label.setMinimumWidth(190)
        return label

    def _build_result_area(self) -> QWidget:
        """结果区：左边前后对比、右边日志与明细。"""
        holder = QFrame(self)
        holder.setObjectName("clean_result_area")
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._tabs = QTabWidget(holder)
        self._tabs.setObjectName("clean_tabs")

        # ---- 标签页 1：清洗前后对比 ----
        compare = QWidget(self._tabs)
        compare_layout = QVBoxLayout(compare)
        compare_layout.setContentsMargins(8, 8, 8, 8)
        compare_layout.setSpacing(6)

        switch_row = QHBoxLayout()
        switch_row.setSpacing(8)
        switch_row.addWidget(QLabel("查看：", compare))
        self._view_buttons: List[QPushButton] = []
        for index, mode in enumerate((VIEW_BEFORE, VIEW_AFTER, VIEW_DIFF)):
            button = QPushButton(mode, compare)
            button.setObjectName("view_switch_button")
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setMinimumSize(150, 32)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip({
                VIEW_BEFORE: "显示进入清洗前的原始数据",
                VIEW_AFTER: "显示清洗后的数据，被修改的单元格用浅橙色标出",
                VIEW_DIFF: "只列出被修改过的单元格，便于逐条核对",
            }[mode])
            button.clicked.connect(lambda _checked=False, m=mode: self._switch_view(m))
            switch_row.addWidget(button)
            self._view_buttons.append(button)
        self._view_buttons[0].setChecked(True)
        switch_row.addStretch(1)

        self._compare_caption = QLabel("", compare)
        self._compare_caption.setObjectName("clean_caption")
        switch_row.addWidget(self._compare_caption)
        compare_layout.addLayout(switch_row)

        self._compare_model = PandasModel()
        self._compare_view = QTableView(compare)
        self._compare_view.setObjectName("clean_table")
        self._compare_view.setModel(self._compare_model)
        self._compare_view.setAlternatingRowColors(True)
        self._compare_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._compare_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._compare_view.setSortingEnabled(True)
        self._compare_view.verticalHeader().setDefaultSectionSize(26)
        compare_layout.addWidget(self._compare_view, 1)
        self._tabs.addTab(compare, "清洗前后对比")

        # ---- 标签页 2：清洗日志 ----
        log_page = QWidget(self._tabs)
        log_layout = QVBoxLayout(log_page)
        log_layout.setContentsMargins(8, 8, 8, 8)
        log_layout.setSpacing(6)

        self._log_view = QTextEdit(log_page)
        self._log_view.setObjectName("clean_log_view")
        self._log_view.setReadOnly(True)
        self._log_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self._log_view.setToolTip(tr("逐条说明改了什么、为什么"))
        log_layout.addWidget(self._log_view, 1)

        export_row = QHBoxLayout()
        self._export_changes_button = QPushButton("导出修改明细…", log_page)
        self._export_changes_button.setObjectName("secondary_button")
        self._export_changes_button.setMinimumSize(150, 34)
        self._export_changes_button.setToolTip(tr("导出修改明细为 csv"))
        self._export_changes_button.clicked.connect(self.export_changes)
        export_row.addWidget(self._export_changes_button)
        export_row.addStretch(1)
        log_layout.addLayout(export_row)
        self._tabs.addTab(log_page, "清洗日志与明细")

        # ---- 标签页 3：修改明细表 ----
        detail_page = QWidget(self._tabs)
        detail_layout = QVBoxLayout(detail_page)
        detail_layout.setContentsMargins(8, 8, 8, 8)
        detail_layout.setSpacing(6)
        detail_layout.addWidget(QLabel("逐条列出被修改的单元格（点击表头可排序）：", detail_page))
        self._changes_model = PandasModel()
        self._changes_view = QTableView(detail_page)
        self._changes_view.setObjectName("changes_table")
        self._changes_view.setModel(self._changes_model)
        self._changes_view.setAlternatingRowColors(True)
        self._changes_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._changes_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._changes_view.setSortingEnabled(True)
        self._changes_view.verticalHeader().setDefaultSectionSize(26)
        detail_layout.addWidget(self._changes_view, 1)
        self._tabs.addTab(detail_page, "修改明细表")

        layout.addWidget(self._tabs, 1)
        return holder

    # =====================================================================
    # 二、状态读取与参数
    # =====================================================================
    def _on_data_changed(self, key: str) -> None:
        """raw 变化后回到"未清洗"状态，避免显示过期结果（方案第三章纪律 3）。"""
        if key in ("raw", "*"):
            self._result = None
            self._raw_snapshot = None
            self._change_map.clear()
            self._refresh_from_state()
            self._update_impact()

    def _refresh_from_state(self) -> None:
        """按当前数据状态切换空状态 / 结果区，并同步按钮可用性。"""
        has_raw = self.app_state.has("raw")
        self._stack.setCurrentIndex(1 if has_raw else 0)
        self._run_button.setEnabled(has_raw)
        self._run_button.setToolTip(
            "按当前选项清洗数据（Ctrl+R）。每次都会从原始数据重新计算。"
            if has_raw else "请先在【数据导入】页载入数据")
        self._save_button.setEnabled(self._result is not None)
        self._log_button.setEnabled(has_raw)
        if has_raw and self._result is None:
            self._hint.setText("已读取到原始数据。确认选项后点【一键执行清洗】。")
            self._hint.set_state("normal")
            self._show_raw_preview()

    def _show_raw_preview(self) -> None:
        """未清洗时，对比区先展示原始数据（用户至少能看到自己的数据长什么样）。"""
        raw = self.app_state.get_table("raw")
        if raw is None:
            return
        self._compare_model.set_dataframe(raw)
        self._compare_model.set_column_tips(column_tips_from_names(raw))
        self._compare_caption.setText("原始数据：" + self._compare_model.summary_text())
        self._view_buttons[1].setEnabled(False)
        self._view_buttons[2].setEnabled(False)
        self._view_buttons[0].setChecked(True)
        self._tabs.setTabText(0, "清洗前后对比（尚未清洗）")

    def _load_params_from_settings(self) -> None:
        """恢复上次使用的参数（方案 5.1：第二次使用无需重新设置）。"""
        params = settings.instance().load_clean_params()
        for key, value, widget in (
            ("mad_k", params.get("mad_k", data_clean.DEFAULT_MAD_K), self._mad_spin),
            ("max_gap", params.get("max_gap", data_clean.DEFAULT_MAX_GAP), self._gap_spin),
        ):
            try:
                widget.setValue(float(value) if isinstance(widget, QDoubleSpinBox) else int(value))
            except (TypeError, ValueError):
                continue

    def _current_params(self) -> Dict[str, Any]:
        """收集当前选项与参数。"""
        return {
            "check_outlier": self._check_outlier.isChecked(),
            "fill_missing": self._check_fill.isChecked(),
            "drop_duplicate": self._check_dedup.isChecked(),
            "mad_k": float(self._mad_spin.value()),
            "max_gap": int(self._gap_spin.value()),
        }

    def _save_params(self) -> None:
        """记住本次参数，下次打开软件仍然生效。"""
        settings.instance().save_clean_params(self._current_params())

    # =====================================================================
    # 三、交互
    # =====================================================================
    def _on_advanced_toggled(self, expanded: bool) -> None:
        """展开/收起高级设置面板，并同步按钮文字上的箭头。"""
        self._advanced_panel.setVisible(bool(expanded))
        self._advanced_toggle.setText("高级设置 ▾" if expanded else tr("高级设置 ▸"))

    def _on_option_changed(self, *_args: Any) -> None:
        """选项或参数变化：立即更新预计影响（方案 5.4 参数实时反馈）。"""
        if self._building:
            return
        self._save_params()
        self._update_impact()

    def _update_impact(self) -> None:
        """在真正执行之前，先把"预计会改多少"告诉用户。"""
        raw = self.app_state.get_table("raw")
        if raw is None:
            self._impact.setText("")
            return
        info = data_clean.preview_impact(
            raw, data_type=self.app_state.get_text("data_type"),
            **self._current_params())
        if not info.get("ok"):
            self._impact.setText("无法预估影响：" + str(info.get("message", "")))
            self._impact.set_state("warning")
            return
        text = "{0}（清洗前 {1:,} 行 → 清洗后 {2:,} 行）".format(
            info["message"], info["rows_in"], info["rows_out"])
        self._impact.setText(text)
        # 有改动时用主色提示，完全没有改动时用绿色说明数据本来就干净
        self._impact.set_state("info" if info["stats"].total_changes else "success")

    # =====================================================================
    # 四、执行清洗
    # =====================================================================
    def run_clean(self) -> bool:
        """执行清洗：始终以 app_state["raw"] 为输入，产出写入 clean 与 clean_log。"""
        raw = self.app_state.get_table("raw")
        if raw is None:
            QMessageBox.information(
                self, "还没有数据",
                "清洗需要先有原始数据。\n\n怎么办：请先到【数据导入】页导入数据，"
                "或点该页的【载入示例数据】直接体验。")
            return False

        params = self._current_params()
        self._set_busy(True)
        try:
            result = data_clean.clean_data(
                raw, data_type=self.app_state.get_text("data_type"), **params)
        except data_clean.CleanError as exc:
            self._set_busy(False)
            QMessageBox.warning(self, "无法清洗", str(exc))
            return False
        except Exception as exc:  # noqa: BLE001 - 交给全局异常处理，显示中文提示
            self._set_busy(False)
            from utils.errors import report_exception

            report_exception(exc, context="清洗数据")
            return False
        finally:
            self._set_busy(False)

        # ---- 写入 AppState（只经 set_table / set_text，禁止页面互相操作）----
        self._result = result
        self._raw_snapshot = raw.copy(deep=True)
        self._change_map = self._build_change_map(result)
        self.app_state.set_table(
            "clean", result.df,
            meta={"cleaned_from_rows": len(raw), "clean_params": params})
        self.app_state.set_text("clean_log", result.log)

        self._render_result()
        self._save_params()
        self._report_result(result)
        return True

    def _set_busy(self, busy: bool) -> None:
        """清洗期间禁用按钮并给出可见反馈（方案第二章防假死）。"""
        for widget in (self._run_button, self._save_button, self._export_changes_button):
            widget.setEnabled(not busy)
        if busy:
            self._hint.setText("正在清洗数据，请稍候…")
            self._hint.set_state("info")
        else:
            self._refresh_from_state()

    def _build_change_map(self, result: data_clean.CleanResult) -> Dict[tuple, str]:
        """把明细表转成 {(行号, 列名): 处理方式}，供单元格高亮与悬浮提示使用。"""
        mapping: Dict[tuple, str] = {}
        changes = result.changes
        if changes is None or len(changes) == 0:
            return mapping
        for _index, row in changes.iterrows():
            try:
                row_number = int(row["行号"])
            except (TypeError, ValueError):
                continue
            mapping[(row_number, str(row["列名"]))] = str(row["处理方式"])
        return mapping

    # =====================================================================
    # 五、结果展示
    # =====================================================================
    def _render_result(self) -> None:
        """把清洗结果铺到界面上：对比视图、日志、明细表。"""
        result = self._result
        if result is None:
            return

        self._log_view.setPlainText(result.log)
        self._changes_model.set_dataframe(result.changes)
        self._changes_model.set_column_tips({
            "行号": "该单元格在原始数据中的行号（与清洗前表格的左侧行号一致）。",
            "列名": "被修改的列。整行删除时显示「（整行）」。",
            "原值": "清洗前的原始值。",
            "处理后的值": "清洗后的值；显示「（空）」表示该处保留为空白。",
            "处理方式": "这条修改属于哪一类处理。",
        })

        self._view_buttons[1].setEnabled(True)
        self._view_buttons[2].setEnabled(True)
        self._tabs.setTabText(0, "清洗前后对比")
        # 默认切到"清洗后"，让用户立刻看到结果，再自行切回对比
        self._view_buttons[1].setChecked(True)
        self._switch_view(VIEW_AFTER)

        self._compare_caption.setText(
            "清洗后：{0} ｜ 共 {1} 处修改".format(
                self._compare_model.summary_text(), len(self._change_map)))

    def _switch_view(self, mode: str) -> None:
        """切换 清洗前 / 清洗后 / 只看被修改的单元格。"""
        result = self._result
        raw = self._raw_snapshot if self._raw_snapshot is not None else self.app_state.get_table("raw")
        if result is None or raw is None:
            return

        if mode == VIEW_BEFORE:
            self._compare_model.set_dataframe(raw)
            self._compare_model.set_column_tips(column_tips_from_names(raw))
            self._compare_model.set_highlight({})
            self._compare_caption.setText("清洗前：{0}".format(self._compare_model.summary_text()))
            return

        if mode == VIEW_AFTER:
            self._compare_model.set_dataframe(result.df)
            self._compare_model.set_column_tips(column_tips_from_names(result.df))
            # 行号与列名组成的键恰好与明细表一致，据此高亮被改过的单元格
            self._compare_model.set_highlight(self._change_map)
            self._compare_caption.setText(
                "清洗后：{0} ｜ 浅橙色 = 被修改过的单元格".format(self._compare_model.summary_text()))
            return

        # 只看被修改的单元格：把明细表当作数据源展示
        self._compare_model.set_dataframe(result.changes)
        self._compare_model.set_column_tips({
            "行号": "原始数据中的行号。",
            "列名": "被修改的列。",
            "原值": "清洗前的值。",
            "处理后的值": "清洗后的值。",
            "处理方式": "处理类别。",
        })
        self._compare_model.set_highlight({})
        self._compare_caption.setText(
            "被修改的单元格：{0}（完整信息见「修改明细表」标签页）".format(
                self._compare_model.summary_text()))

    def _show_log_tab(self) -> None:
        """跳到日志标签页（若尚未清洗，先提示）。"""
        self._tabs.setCurrentIndex(1)
        if self._result is None:
            QMessageBox.information(
                self, "还没有清洗日志",
                "清洗日志会在执行清洗后生成。\n\n怎么办：确认选项后点【一键执行清洗】。")

    def _report_result(self, result: data_clean.CleanResult) -> None:
        """清洗完成后的结果摘要（方案 5.5：任务完成后显示结果摘要）。

        文案分三种情况，避免"数据本来很干净"却被说成"改了一大堆"：
            三项检查都没发现问题 → 明确告诉用户数据是干净的；
            只是补齐了空白（例如灌木草本本来就没有胸径）→ 说明这是按规范补齐，不是修正错误；
            确实改动了数据 → 报出三类改动的具体数量，并引导用户逐条核对。
        """
        stats = result.stats
        if stats.total_changes == 0 and stats.duplicates_removed == 0:
            self._hint.setText(
                "清洗完成：异常值、重复记录、缺失值三项检查都没有发现问题，数据本身是干净的，"
                "可以直接前往【指标计算】页。")
            self._hint.set_state("success")
            return

        if stats.outliers_fixed == 0 and stats.duplicates_removed == 0:
            self._hint.setText(
                "清洗完成：没有发现异常值，也没有重复记录；"
                "按规范补齐了 {0} 个空白（时序插值 {1} 个、分组中位数 {2} 个、按众数 {3} 个），"
                "数据仍是 {4:,} 行。可切换上方按钮核对每一个被修改的单元格。".format(
                    stats.missing_filled, stats.interpolated,
                    stats.median_filled + stats.column_median_filled, stats.mode_filled,
                    stats.rows_out))
            self._hint.set_state("success")
            return

        self._hint.setText(
            "清洗完成：共修改 {0} 处（修正异常值 {1}、补齐空缺 {2}、删除重复 {3} 行），"
            "数据从 {4:,} 行变为 {5:,} 行。可切换上方按钮核对每一个被修改的单元格。".format(
                stats.total_changes + stats.duplicates_removed, stats.outliers_fixed,
                stats.missing_filled, stats.duplicates_removed, stats.rows_in, stats.rows_out))
        self._hint.set_state("normal")

    # =====================================================================
    # 六、保存与导出
    # =====================================================================
    def save_cleaned_data(self) -> None:
        """把清洗后的数据表另存为文件（方案阶段 3：保存清洗后数据）。"""
        if self._result is None:
            QMessageBox.information(
                self, "还没有清洗结果",
                "请先点【一键执行清洗】生成清洗后的数据。")
            return

        prefs = settings.instance()
        start_dir = prefs.export_dir or prefs.last_dir or str(Path.home())
        path, _filter = QFileDialog.getSaveFileName(
            self, "保存清洗后的数据", str(Path(start_dir) / "清洗后数据.csv"),
            "CSV 文件 (*.csv);;Excel 工作簿 (*.xlsx)")
        if not path:
            return
        target = Path(path)
        try:
            if target.suffix.lower() == ".xlsx":
                self._result.df.to_excel(target, index=False, engine="openpyxl")
            else:
                # utf-8-sig：保证 Excel 打开时中文不乱码
                self._result.df.to_csv(target, index=False, encoding="utf-8-sig")
        except PermissionError:
            QMessageBox.warning(
                self, "保存失败",
                "文件 {0} 无法写入。\n\n原因：该文件可能正在 Excel 中打开。\n\n"
                "怎么办：关闭 Excel 后重试，或换一个文件名。".format(target.name))
            return
        except Exception as exc:  # noqa: BLE001
            from utils.errors import report_exception

            report_exception(exc, context=tr("保存清洗后数据"))
            return

        settings.instance().export_dir = str(target.parent)
        QMessageBox.information(
            self, "保存成功",
            "清洗后的数据已保存到：\n{0}\n\n共 {1:,} 行 × {2} 列。".format(
                target, len(self._result.df), len(self._result.df.columns)))

    def export_changes(self) -> None:
        """导出修改明细（方案 5.5：明细可导出）。"""
        if self._result is None or len(self._result.changes) == 0:
            QMessageBox.information(
                self, "没有可导出的明细",
                "本次清洗没有产生任何修改，因此没有明细可以导出。")
            return

        prefs = settings.instance()
        start_dir = prefs.export_dir or str(Path.home())
        path, _filter = QFileDialog.getSaveFileName(
            self, "导出修改明细", str(Path(start_dir) / "清洗修改明细.csv"), "CSV 文件 (*.csv)")
        if not path:
            return
        target = Path(path)
        try:
            self._result.changes.to_csv(target, index=False, encoding="utf-8-sig")
        except PermissionError:
            QMessageBox.warning(
                self, "导出失败",
                "文件 {0} 无法写入。\n\n原因：该文件可能正在 Excel 中打开。\n\n"
                "怎么办：关闭 Excel 后重试，或换一个文件名。".format(target.name))
            return
        except Exception as exc:  # noqa: BLE001
            from utils.errors import report_exception

            report_exception(exc, context="导出修改明细")
            return
        QMessageBox.information(
            self, "导出成功",
            "修改明细已导出到：\n{0}\n\n共 {1:,} 条记录。".format(target, len(self._result.changes)))

    # =====================================================================
    # 七、供主窗口查询
    # =====================================================================
    def has_clean_data(self) -> bool:
        """是否已经产生清洗结果（主窗口据此推进步骤条）。"""
        return self.app_state.has("clean")

    def last_result(self) -> Optional[data_clean.CleanResult]:
        """返回最近一次清洗结果（供测试与后续阶段使用）。"""
        return self._result

    def _goto_page(self, index: int) -> None:
        """跳转到指定页面（空状态按钮用，经主窗口统一切换）。"""
        window = self.window()
        handler = getattr(window, "goto_page", None)
        if callable(handler):
            handler(index)


def create_page(app_state: AppState) -> QWidget:
    """页面工厂：主窗口用本函数挂载真实清洗页。"""
    return CleanPage(app_state)
