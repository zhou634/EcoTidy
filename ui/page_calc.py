# -*- coding: utf-8 -*-
"""
指标计算页面（开发方案 第六章 阶段 4 + 第五章易用性规范）

页面结构：
    指标组勾选区（默认按数据类型自动勾选）+「高级设置」折叠面板 + 执行按钮
    结果区：两个标签页 ——「行级指标结果」「汇总指标结果」，各自标注粒度

易用性要点（方案第五章）：
    5.3 没有数据时显示空状态（tr("请先完成数据清洗。")+【前往【智能清洗】】）；
    5.4 参数（样方面积、昼夜划分、萌发天数、分组维度）全部收在「高级设置」里，
        每项附通俗说明与推荐值，调整后即时显示会影响什么；
    5.6 鼠标悬浮任一指标列，显示中文全称、计算公式与生态学含义；
    粒度提示：两个结果表都明确写出"每行 = 什么"，避免用户误读。

纪律：每次执行都以 app_state["clean"] 为输入，结果写入 index_row 与 index_summary。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core import data_parse, eco_index, settings
from core.app_state import AppState
from ui import style as ui_style
from ui.widgets.empty_state import EmptyState
from ui.widgets.pandas_model import PandasModel
from utils.i18n import tr

# 各指标组的一句话说明（鼠标悬浮在复选框上即可看到）
# 原则：只说"算什么 + 需要什么列"，具体公式放在 F1 帮助里，不在这里堆。
_GROUP_TIPS: Dict[str, str] = {
    eco_index.GROUP_STRUCTURE: "密度、频度、优势度、重要值（每行 = 某样方中的某物种）",
    eco_index.GROUP_DIVERSITY: "物种数、香农指数、辛普森指数、均匀度（每样方一行）",
    eco_index.GROUP_ENV: "均值、极值、昼夜均值差（需时间列）",
    eco_index.GROUP_SOIL: "质量／体积含水率（需鲜重、干重列）",
    eco_index.GROUP_EXPERIMENT: "萌发率、萌发势、凋落物分解速率（需相应实验字段）",
}


class CalcPage(QWidget):
    """【指标计算】页面。

    对外接口（供主窗口调用）：
        run_calc()        执行指标计算（Ctrl+R 在本页的主动作）
        has_index_data()  是否已经算出指标
    """

    def __init__(self, app_state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("calc_page")
        self.app_state = app_state

        self._result: Optional[eco_index.IndexResult] = None
        self._group_boxes: Dict[str, QCheckBox] = {}
        self._building = False

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

        self._stack = QStackedWidget(self)
        self._stack.setObjectName("calc_stack")

        self._empty = EmptyState(self)
        self._empty.set_icon("ruler")
        self._empty.set_description(tr("请先完成数据清洗。"))
        self._empty.set_action("前往【智能清洗】")
        self._empty.action_clicked.connect(lambda: self._goto_page(1))
        self._stack.addWidget(self._empty)

        self._stack.addWidget(self._build_result_area())
        root.addWidget(self._stack, 1)

        self._hint = ui_style.HintQLabel("", self)
        self._hint.setObjectName("calc_hint")
        self._hint.setWordWrap(True)
        root.addWidget(self._hint)

    def _build_option_bar(self) -> QWidget:
        holder = QFrame(self)
        holder.setObjectName("calc_option_bar")
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # ---- 指标组勾选 ----
        group_row = QHBoxLayout()
        group_row.setSpacing(12)
        group_row.addWidget(QLabel(tr("要计算哪些指标："), holder))
        for name in eco_index.ALL_GROUPS:
            box = QCheckBox(name, holder)
            box.setToolTip(_GROUP_TIPS.get(name, ""))
            box.toggled.connect(self._on_selection_changed)
            group_row.addWidget(box)
            self._group_boxes[name] = box
        group_row.addStretch(1)

        self._advanced_toggle = QPushButton("高级设置 ▸", holder)
        self._advanced_toggle.setObjectName("secondary_button")
        self._advanced_toggle.setMinimumSize(120, 34)
        self._advanced_toggle.setCheckable(True)
        self._advanced_toggle.setToolTip(tr("样方面积、昼夜划分等（默认即可）"))
        self._advanced_toggle.toggled.connect(self._on_advanced_toggled)
        group_row.addWidget(self._advanced_toggle)

        self._run_button = QPushButton(tr("执行指标计算"), holder)
        self._run_button.setObjectName("primary_button")
        self._run_button.setMinimumSize(130, 34)
        self._run_button.setToolTip(tr("按勾选的指标组计算（Ctrl+R）"))
        self._run_button.clicked.connect(self.run_calc)
        group_row.addWidget(self._run_button)
        layout.addLayout(group_row)

        # ---- 高级设置折叠面板 ----
        self._advanced_panel = QGroupBox("高级设置（不确定就用默认值）", holder)
        form = QFormLayout(self._advanced_panel)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(8)

        self._area_spin = QDoubleSpinBox(self._advanced_panel)
        self._area_spin.setRange(0.01, 100000.0)
        self._area_spin.setDecimals(2)
        self._area_spin.setValue(eco_index.DEFAULT_QUADRAT_AREA)
        self._area_spin.setMinimumWidth(110)
        self._area_spin.setToolTip(tr("仅影响密度指标"))
        self._area_spin.valueChanged.connect(self._on_selection_changed)
        form.addRow(self._tip_label(
            "样方面积（m²）",
            "每个样方的实际面积，只用于计算密度 = 株数 ÷ 样方面积。默认 1。\n"
            "例如 10 m × 10 m 的样方请填 100。填多少不影响其它指标。"),
            self._area_spin)

        self._day_start_spin = QSpinBox(self._advanced_panel)
        self._day_start_spin.setRange(0, 23)
        self._day_start_spin.setValue(eco_index.DEFAULT_DAY_START)
        self._day_start_spin.setMinimumWidth(110)
        self._day_start_spin.valueChanged.connect(self._on_selection_changed)
        form.addRow(self._tip_label("白天开始时刻", "昼夜均值差按这里的划分计算。默认 6 点。"),
                    self._day_start_spin)

        self._day_end_spin = QSpinBox(self._advanced_panel)
        self._day_end_spin.setRange(0, 23)
        self._day_end_spin.setValue(eco_index.DEFAULT_DAY_END)
        self._day_end_spin.setMinimumWidth(110)
        self._day_end_spin.valueChanged.connect(self._on_selection_changed)
        form.addRow(self._tip_label("白天结束时刻", "默认 18 点（即 06:00–18:00 为白天，其余为夜间）。"),
                    self._day_end_spin)

        self._group_combo = QComboBox(self._advanced_panel)
        self._group_combo.addItems(["日", "时"])
        self._group_combo.setMinimumWidth(110)
        self._group_combo.currentTextChanged.connect(self._on_selection_changed)
        form.addRow(self._tip_label("时序统计分组", "环境时序统计按「日」还是「时」汇总。默认按日。"),
                    self._group_combo)

        self._germination_spin = QSpinBox(self._advanced_panel)
        self._germination_spin.setRange(1, 365)
        self._germination_spin.setValue(eco_index.DEFAULT_GERMINATION_DAY)
        self._germination_spin.setMinimumWidth(110)
        self._germination_spin.valueChanged.connect(self._on_selection_changed)
        form.addRow(self._tip_label(
            "萌发势统计天数",
            "萌发势取第几天的累计萌发数。默认 7 天。\n"
            "只影响实验生态指标里的「萌发势」一项。"),
            self._germination_spin)

        self._advanced_panel.setVisible(False)
        layout.addWidget(self._advanced_panel)

        # ---- 实时提示：将计算什么、产出几张表 ----
        self._plan_label = ui_style.HintQLabel("", holder)
        self._plan_label.setObjectName("calc_plan")
        self._plan_label.setWordWrap(True)
        layout.addWidget(self._plan_label)
        return holder

    @staticmethod
    def _tip_label(title: str, tip: str) -> QLabel:
        label = QLabel("{0} <span style='color:#5A5F66;font-weight:bold'>?</span>".format(title))
        label.setToolTip(tip)
        label.setWordWrap(True)
        label.setMinimumWidth(150)
        return label

    def _build_result_area(self) -> QWidget:
        holder = QFrame(self)
        holder.setObjectName("calc_result_area")
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._tabs = QTabWidget(holder)
        self._tabs.setObjectName("calc_tabs")

        # ---- 行级指标结果 ----
        row_page = QWidget(self._tabs)
        row_layout = QVBoxLayout(row_page)
        row_layout.setContentsMargins(8, 8, 8, 8)
        row_layout.setSpacing(6)
        self._row_caption = QLabel("每行 = 某样方中的某物种", row_page)
        self._row_caption.setObjectName("calc_caption")
        self._row_caption.setWordWrap(True)
        row_layout.addWidget(self._row_caption)
        self._row_model = PandasModel()
        self._row_view = QTableView(row_page)
        self._row_view.setObjectName("row_index_table")
        self._row_view.setModel(self._row_model)
        self._row_view.setAlternatingRowColors(True)
        self._row_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._row_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._row_view.setSortingEnabled(True)
        self._row_view.verticalHeader().setDefaultSectionSize(26)
        row_layout.addWidget(self._row_view, 1)
        self._tabs.addTab(row_page, "行级指标结果（样方 × 物种）")

        # ---- 汇总指标结果 ----
        summary_page = QWidget(self._tabs)
        summary_layout = QVBoxLayout(summary_page)
        summary_layout.setContentsMargins(8, 8, 8, 8)
        summary_layout.setSpacing(6)
        self._summary_caption = QLabel("每行 = 一个样方", summary_page)
        self._summary_caption.setObjectName("calc_caption")
        self._summary_caption.setWordWrap(True)
        summary_layout.addWidget(self._summary_caption)
        self._summary_model = PandasModel()
        self._summary_view = QTableView(summary_page)
        self._summary_view.setObjectName("summary_index_table")
        self._summary_view.setModel(self._summary_model)
        self._summary_view.setAlternatingRowColors(True)
        self._summary_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._summary_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._summary_view.setSortingEnabled(True)
        self._summary_view.verticalHeader().setDefaultSectionSize(26)
        summary_layout.addWidget(self._summary_view, 1)
        self._tabs.addTab(summary_page, "汇总指标结果（按样方）")

        # ---- 计算说明 ----
        notes_page = QWidget(self._tabs)
        notes_layout = QVBoxLayout(notes_page)
        notes_layout.setContentsMargins(8, 8, 8, 8)
        self._notes_view = QLabel("", notes_page)
        self._notes_view.setObjectName("calc_notes")
        self._notes_view.setWordWrap(True)
        self._notes_view.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._notes_view.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        notes_layout.addWidget(self._notes_view, 1)
        self._tabs.addTab(notes_page, "计算说明")

        layout.addWidget(self._tabs, 1)
        return holder

    # =====================================================================
    # 二、状态与参数
    # =====================================================================
    def _on_data_changed(self, key: str) -> None:
        """clean 变化后回到"未计算"状态，并按新数据类型调整默认勾选。"""
        if key in ("clean", "data_type", "*"):
            self._result = None
            self._refresh_from_state()
            self._apply_default_groups()

    def _refresh_from_state(self) -> None:
        has_clean = self.app_state.has("clean")
        self._stack.setCurrentIndex(1 if has_clean else 0)
        self._run_button.setEnabled(has_clean)
        self._run_button.setToolTip(tr("按勾选的指标组计算（Ctrl+R）") if has_clean
                                    else "请先在【智能清洗】页完成数据清洗")
        if has_clean and self._result is None:
            self._hint.setText("已读取到清洗后的数据。确认指标组后点【执行指标计算】。")
            self._hint.set_state("normal")
        self._update_plan()

    def _apply_default_groups(self) -> None:
        """按数据类型勾选默认指标组（方案阶段 4 第 2 条）。"""
        data_type = self.app_state.get_text("data_type") or data_parse.TYPE_QUADRAT
        defaults = set(eco_index.default_groups_for(data_type))
        self._building = True
        try:
            for name, box in self._group_boxes.items():
                box.setChecked(name in defaults)
        finally:
            self._building = False
        self._update_plan()

    def _load_params_from_settings(self) -> None:
        params = settings.instance().load_calc_params()
        try:
            if params.get("quadrat_area"):
                self._area_spin.setValue(float(params["quadrat_area"]))
            if params.get("day_start") is not None:
                self._day_start_spin.setValue(int(params["day_start"]))
            if params.get("day_end") is not None:
                self._day_end_spin.setValue(int(params["day_end"]))
            if params.get("germination_day"):
                self._germination_spin.setValue(int(params["germination_day"]))
            if params.get("group_by") in ("日", "时"):
                self._group_combo.setCurrentText(str(params["group_by"]))
        except (TypeError, ValueError):
            pass

    def _current_params(self) -> Dict[str, Any]:
        return {
            "quadrat_area": float(self._area_spin.value()),
            "day_start": int(self._day_start_spin.value()),
            "day_end": int(self._day_end_spin.value()),
            "germination_day": int(self._germination_spin.value()),
            "group_by": self._group_combo.currentText(),
        }

    def selected_groups(self) -> List[str]:
        return [name for name, box in self._group_boxes.items() if box.isChecked()]

    # =====================================================================
    # 三、交互
    # =====================================================================
    def _on_advanced_toggled(self, expanded: bool) -> None:
        self._advanced_panel.setVisible(bool(expanded))
        self._advanced_toggle.setText("高级设置 ▾" if expanded else "高级设置 ▸")

    def _on_selection_changed(self, *_args: Any) -> None:
        if self._building:
            return
        settings.instance().save_calc_params(self._current_params())
        self._update_plan()

    def _update_plan(self) -> None:
        """实时告诉用户"这次会算出什么、产出几张表"。"""
        groups = self.selected_groups()
        if not self.app_state.has("clean"):
            self._plan_label.setText("")
            return
        if not groups:
            self._plan_label.setText("还没有勾选任何指标组，点上面的复选框选择要计算的内容。")
            self._plan_label.set_state("error")
            return

        produces_row = eco_index.GROUP_STRUCTURE in groups
        produces_summary = any(name in groups for name in
                               (eco_index.GROUP_DIVERSITY, eco_index.GROUP_ENV,
                                eco_index.GROUP_SOIL, eco_index.GROUP_EXPERIMENT))
        cleaned = self.app_state.get_table("clean")
        rows = len(cleaned) if cleaned is not None else 0
        parts = ["将计算：{0}".format("、".join(groups))]
        tables: List[str] = []
        if produces_row:
            tables.append("行级指标表（{0:,} 行）".format(rows))
        if produces_summary:
            if eco_index.GROUP_DIVERSITY in groups and "样方号" in getattr(cleaned, "columns", []):
                tables.append("汇总指标表（{0} 个样方）".format(cleaned["样方号"].nunique()))
            elif eco_index.GROUP_ENV in groups:
                tables.append("汇总指标表（按{0}分组）".format(self._group_combo.currentText()))
            else:
                tables.append("汇总指标表")
        if tables:
            parts.append("产出：" + "、".join(tables))
            parts.append("样方面积 {0:g} m²".format(self._area_spin.value()))
            out_of_range = self._day_start_spin.value() >= self._day_end_spin.value()
            if eco_index.GROUP_ENV in groups and out_of_range:
                parts.append("⚠ 白天的开始时刻不早于结束时刻，昼夜均值差将无法计算")
        self._plan_label.setText(" ｜ ".join(parts))
        self._plan_label.set_state("normal")

    # =====================================================================
    # 四、执行计算
    # =====================================================================
    def run_calc(self) -> bool:
        """执行指标计算并把结果写入 index_row / index_summary。"""
        cleaned = self.app_state.get_table("clean")
        if cleaned is None:
            QMessageBox.information(
                self, "还没有清洗后的数据",
                "指标计算需要先完成数据清洗。\n\n怎么办：请先到【智能清洗】页执行清洗，"
                "或点【数据导入】页的【载入示例数据】直接体验完整流程。")
            return False

        groups = self.selected_groups()
        if not groups:
            QMessageBox.information(
                self, "还没有选择指标组",
                "请至少勾选一个指标组，例如「群落结构指标」或「多样性指标」。")
            return False

        self._set_busy(True)
        try:
            result = eco_index.compute_indicators(
                cleaned,
                data_type=self.app_state.get_text("data_type"),
                groups=groups,
                **self._current_params())
        except eco_index.IndexError_ as exc:
            self._set_busy(False)
            QMessageBox.warning(self, "无法计算指标", str(exc))
            return False
        except Exception as exc:  # noqa: BLE001 - 交给全局异常处理显示中文提示
            self._set_busy(False)
            from utils.errors import report_exception

            report_exception(exc, context="计算生态指标")
            return False
        finally:
            self._set_busy(False)

        # ---- 写入 AppState ----
        self._result = result
        if result.has_row_table():
            self.app_state.set_table("index_row", result.row_table,
                                     meta={"granularity": result.row_granularity})
        if result.has_summary_table():
            self.app_state.set_table("index_summary", result.summary_table,
                                     meta={"granularity": result.summary_granularity})

        self._render_result(result)
        settings.instance().save_calc_params(self._current_params())
        self._report_result(result)
        return True

    def _set_busy(self, busy: bool) -> None:
        for widget in (self._run_button,):
            widget.setEnabled(not busy)
        if busy:
            self._hint.setText("正在计算指标，请稍候…")
            self._hint.set_state("info")
        else:
            self._refresh_from_state()

    # =====================================================================
    # 五、结果展示
    # =====================================================================
    def _render_result(self, result: eco_index.IndexResult) -> None:
        tips = eco_index.metric_tips()

        if result.has_row_table():
            self._row_model.set_dataframe(result.row_table)
            self._row_model.set_column_tips(tips)
            self._row_view.resizeColumnsToContents()
            self._row_caption.setText(
                "{0} ｜ 粒度：{1}。鼠标停在列名上可看到公式与生态学含义。".format(
                    self._row_model.summary_text(), result.row_granularity))
            self._tabs.setTabText(0, "行级指标结果（{0} 行）".format(len(result.row_table)))
        else:
            self._row_model.set_dataframe(None)
            self._row_caption.setText("本次没有勾选产出「行级指标」的指标组（群落结构指标）。")
            self._tabs.setTabText(0, "行级指标结果（无）")

        if result.has_summary_table():
            self._summary_model.set_dataframe(result.summary_table)
            self._summary_model.set_column_tips(tips)
            self._summary_view.resizeColumnsToContents()
            self._summary_caption.setText(
                "{0} ｜ 粒度：{1}。".format(self._summary_model.summary_text(),
                                          result.summary_granularity))
            self._tabs.setTabText(1, "汇总指标结果（{0} 行）".format(len(result.summary_table)))
        else:
            self._summary_model.set_dataframe(None)
            self._summary_caption.setText("本次没有勾选产出「汇总指标」的指标组。")
            self._tabs.setTabText(1, "汇总指标结果（无）")

        notes = result.notes or ["本次计算没有需要特别说明的情况。"]
        self._notes_view.setText("\n\n".join("· " + note for note in notes))

        # 默认停在有内容的那一页
        self._tabs.setCurrentIndex(0 if result.has_row_table() else 1)

    def _report_result(self, result: eco_index.IndexResult) -> None:
        """计算完成后的摘要（方案 5.5）。"""
        parts: List[str] = []
        if result.has_row_table():
            parts.append("行级指标表 {0:,} 行".format(len(result.row_table)))
        if result.has_summary_table():
            parts.append("汇总指标表 {0:,} 行".format(len(result.summary_table)))
        text = "计算完成：{0}（{1}）。".format("、".join(parts), "、".join(result.groups))
        if result.notes:
            text += " 有 {0} 条说明需要留意，见「计算说明」标签页。".format(len(result.notes))
        text += " 下一步：到【可视化绘图】页选择图表。"
        self._hint.setText(text)
        self._hint.set_state("normal")

    # =====================================================================
    # 六、供主窗口查询
    # =====================================================================
    def has_index_data(self) -> bool:
        return self.app_state.has("index_row") or self.app_state.has("index_summary")

    def last_result(self) -> Optional[eco_index.IndexResult]:
        return self._result

    def _goto_page(self, index: int) -> None:
        window = self.window()
        handler = getattr(window, "goto_page", None)
        if callable(handler):
            handler(index)


def create_page(app_state: AppState) -> QWidget:
    """页面工厂：主窗口用本函数挂载真实指标计算页。"""
    return CalcPage(app_state)
