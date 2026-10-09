# -*- coding: utf-8 -*-
"""
可视化绘图页面（开发方案 第六章 阶段 5 + 第五章易用性规范）

页面结构：
    数据源选择（先选表 → 再选字段）+ 图表设置 + 按钮
    matplotlib 画布预览（嵌入 PySide）
    绘图说明（字体来源、聚合方式、跳过的列等）

易用性要点（方案第五章）：
    5.3 没有可绘制数据时显示空状态（"还没有可绘制的数据…"+【前往【指标计算】】）；
    5.6 **进入页面即自动推荐并预选合适的图表类型与字段**，用户直接点【绘制图表】就能看到图；
        图表标题与坐标轴标签全部自动生成为中文，不出现英文列名；
        「先选表、再选字段」的设计解决了行级表与汇总表的粒度歧义。
    导出文件名 = 中文图表名 + 时间戳，不静默覆盖同名文件；导出后提供【打开所在文件夹】。

纪律：只读 AppState 里的数据表，绝不修改它们；每次绘图释放上一张图对象，避免内存增长。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core import plot_draw, settings
from core.app_state import AppState
from ui import style as ui_style
from ui.widgets.empty_state import EmptyState
from utils.errors import reveal_in_explorer
from utils.i18n import tr

# matplotlib 的 Qt 画布：优先用 matplotlib 3.5+ 的统一后端 backend_qtagg（方案阶段 5 第 2 条）
#
# 延迟加载（启动性能优化）：
#     画布后端会连带把 matplotlib 的绘图栈拉起来，实测约 400 ms。
#     而用户打开软件时还没画任何图，没必要为此拖慢启动。
#     因此改为"第一次真要放画布时才导入"，见 _figure_canvas_class()。
_CANVAS_CLASS: Any = None
_CANVAS_READY = False


def _figure_canvas_class() -> Any:
    """返回 matplotlib 的 Qt 画布类；环境不支持时返回 None。

    首次调用会触发一次性导入（约 400 ms），之后直接返回缓存结果。
    """
    global _CANVAS_CLASS, _CANVAS_READY
    if _CANVAS_READY:
        return _CANVAS_CLASS
    _CANVAS_READY = True
    try:  # pragma: no cover - 取决于环境
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as canvas_class
    except Exception:  # noqa: BLE001
        try:
            from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as canvas_class
        except Exception:  # noqa: BLE001
            canvas_class = None
    _CANVAS_CLASS = canvas_class
    return _CANVAS_CLASS

# 数据源（先选表，再选字段）
SOURCE_CLEAN = "清洗数据"
SOURCE_ROW = "行级指标"
SOURCE_SUMMARY = "汇总指标"
_SOURCE_TO_KEY = {
    SOURCE_CLEAN: "clean",
    SOURCE_ROW: "index_row",
    SOURCE_SUMMARY: "index_summary",
}
_SOURCE_ORDER = (SOURCE_CLEAN, SOURCE_ROW, SOURCE_SUMMARY)


class PlotPage(QWidget):
    """【可视化绘图】页面。

    对外接口（供主窗口调用）：
        draw_chart()      按当前设置绘图（Ctrl+R 在本页的主动作）
        export_images()   按所选格式导出图片（PNG / SVG / 两者）
        has_figures()     是否已经生成过图片
    """

    #: 输出格式选项（界面文字 -> 实际格式元组）。
    #: 用中文说明而不是只写 "PNG/SVG"，用户不必猜各是干什么的。
    _FORMAT_OPTIONS: Dict[str, Tuple[str, ...]] = {
        "PNG + SVG（推荐）": ("png", "svg"),
        "仅 PNG 位图（300dpi）": ("png",),
        "仅 SVG 矢量图": ("svg",),
    }
    _FORMAT_DEFAULT = "PNG + SVG（推荐）"

    def __init__(self, app_state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("plot_page")
        self.app_state = app_state

        self._canvas_widget: Optional[QWidget] = None
        self._canvas: Any = None
        self._result: Optional[plot_draw.ChartResult] = None
        self._exported: List[Path] = []
        self._building = False
        # 用户是否手动选过数据源（决定刷新时是保留他的选择还是用默认优先级）
        self._source_touched = False

        self._build_ui()
        self._restore_choices()
        self._refresh_from_state()
        self.app_state.data_changed.connect(self._on_data_changed)

    # =====================================================================
    # 一、界面搭建
    # =====================================================================
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        root.addWidget(self._build_control_bar())

        self._stack = QStackedWidget(self)
        self._stack.setObjectName("plot_stack")

        self._empty = EmptyState(self)
        self._empty.set_icon("chart")
        self._empty.set_description("还没有可绘制的数据，请先完成指标计算；也可以直接用清洗后的数据绘图。")
        self._empty.set_action("前往【指标计算】")
        self._empty.set_secondary_action("前往【智能清洗】")
        self._empty.action_clicked.connect(lambda: self._goto_page(2))
        self._empty.secondary_clicked.connect(lambda: self._goto_page(1))
        self._stack.addWidget(self._empty)

        self._stack.addWidget(self._build_canvas_area())
        root.addWidget(self._stack, 1)

        self._hint = ui_style.HintQLabel("", self)
        self._hint.setObjectName("plot_hint")
        self._hint.setWordWrap(True)
        root.addWidget(self._hint)

    def _build_control_bar(self) -> QWidget:
        holder = QFrame(self)
        holder.setObjectName("plot_control_bar")
        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)

        # ---- 第一行：数据源 → 图表类型 → 分类字段 ----
        grid.addWidget(QLabel(tr("① 数据来源："), holder), 0, 0)
        self._source_combo = QComboBox(holder)
        self._source_combo.setMinimumWidth(150)
        self._source_combo.setToolTip(
            tr("先选表，再选字段"))
        self._source_combo.currentTextChanged.connect(self._on_source_changed)
        grid.addWidget(self._source_combo, 0, 1)

        grid.addWidget(QLabel(tr("② 图表类型："), holder), 0, 2)
        self._chart_combo = QComboBox(holder)
        self._chart_combo.setMinimumWidth(170)
        self._chart_combo.setToolTip(tr("默认已按数据类型推荐"))
        self._chart_combo.currentTextChanged.connect(self._on_chart_type_changed)
        grid.addWidget(self._chart_combo, 0, 3)

        grid.addWidget(QLabel(tr("③ 分类 / X 轴："), holder), 0, 4)
        self._category_combo = QComboBox(holder)
        self._category_combo.setMinimumWidth(140)
        self._category_combo.setToolTip(tr("横轴分类，如样方号、物种、日期"))
        self._category_combo.currentTextChanged.connect(lambda _t: self._update_plan())
        grid.addWidget(self._category_combo, 0, 5)

        # ---- 第二行：数值字段（多选）----
        # 标签做成动态的：每个数据源可画的列不一样（例如树高/基径/冠幅只存在于
        # 清洗数据与行级指标，汇总指标里没有）。不写清"这是哪张表的哪些列"，
        # 用户会以为软件把列弄丢了（实测反馈）。
        value_label = QLabel(tr("④ 数值 / Y 轴（可多选）："), holder)
        value_label.setObjectName("plot_value_label")
        self._value_label = value_label
        grid.addWidget(value_label, 1, 0, Qt.AlignmentFlag.AlignTop)
        self._value_list = QListWidget(holder)
        self._value_list.setObjectName("plot_value_list")
        self._value_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._value_list.setMaximumHeight(78)
        self._value_list.setMinimumWidth(360)
        self._value_list.setToolTip(tr("可多选"))
        self._value_list.itemChanged.connect(self._on_value_changed)
        grid.addWidget(self._value_list, 1, 1, 1, 3)

        # ---- 第二行右侧：按钮 ----
        self._draw_button = QPushButton(tr("绘制图表"), holder)
        self._draw_button.setObjectName("primary_button")
        self._draw_button.setMinimumSize(120, 34)
        self._draw_button.setToolTip(tr("绘制图表（Ctrl+R）"))
        self._draw_button.clicked.connect(self.draw_chart)
        grid.addWidget(self._draw_button, 1, 4)

        self._export_button = QPushButton(tr("导出图片"), holder)
        self._export_button.setObjectName("secondary_button")
        self._export_button.setMinimumSize(110, 34)
        self._export_button.setToolTip(tr("按所选格式导出，自动带时间戳、不覆盖"))
        self._export_button.clicked.connect(self.export_images)
        grid.addWidget(self._export_button, 1, 5)

        # ---- 第三行：配色方案与输出格式（阶段 8：彩色/黑白可选、矢量图可选）----
        grid.addWidget(QLabel(tr("⑤ 配色方案："), holder), 2, 0)
        self._palette_combo = QComboBox(holder)
        self._palette_combo.setMinimumWidth(150)
        self._palette_combo.setToolTip(
            tr("灰阶（默认）／彩色／纯黑白（底纹区分）"))
        for mode in plot_draw.ALL_PALETTE_MODES:
            self._palette_combo.addItem(mode)
        self._palette_combo.currentTextChanged.connect(self._on_palette_changed)
        grid.addWidget(self._palette_combo, 2, 1)

        grid.addWidget(QLabel(tr("⑥ 输出格式："), holder), 2, 2)
        self._format_combo = QComboBox(holder)
        self._format_combo.setMinimumWidth(190)
        self._format_combo.setToolTip(
            tr("PNG 位图 300dpi／SVG 矢量图／两者都要"))
        for label in self._FORMAT_OPTIONS:
            self._format_combo.addItem(label)
        self._format_combo.currentTextChanged.connect(self._on_format_changed)
        grid.addWidget(self._format_combo, 2, 3, 1, 3)

        # ---- 第四行：实时提示 ----
        self._plan_label = ui_style.HintQLabel("", holder)
        self._plan_label.setObjectName("plot_plan")
        self._plan_label.setWordWrap(True)
        grid.addWidget(self._plan_label, 3, 0, 1, 6)
        return holder

    def _build_canvas_area(self) -> QWidget:
        """画布区域：先放一个占位提示，真正的画布等第一次显示本页时才创建。

        为什么要延迟（启动性能，实测）：
            matplotlib 的 Qt 后端约 400 ms，是本页最贵的一步。
            而五个页面在启动时全部预挂载（架构要求：都监听 data_changed），
            若在这里直接建画布，用户即便只想看看导入页，也要先等 matplotlib 加载完。
            改成鼠标悬停延迟：占位框先显示"打开本页即加载绘图组件"，
            等真正切到本页（showEvent）再建画布 —— 用户完全感觉不到差别。
        """
        holder = QFrame(self)
        holder.setObjectName("plot_canvas_area")
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        placeholder = QLabel(tr("绘图组件将在打开本页时加载…"), holder)
        placeholder.setObjectName("plot_fallback")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setWordWrap(True)
        layout.addWidget(placeholder, 1)

        self._canvas_holder = holder
        self._canvas_layout = layout
        self._canvas_placeholder = placeholder
        self._canvas_widget = placeholder
        self._canvas = None
        self._canvas_ready = False
        return holder

    def showEvent(self, event: Any) -> None:  # noqa: N802 - Qt 命名约定
        """第一次显示本页时才真正加载绘图组件。"""
        super().showEvent(event)
        self._ensure_canvas()

    def _ensure_canvas(self) -> None:
        """建立真正的 matplotlib 画布（幂等，只做一次）。"""
        if self._canvas_ready:
            return
        self._canvas_ready = True

        canvas_class = _figure_canvas_class()
        layout = self._canvas_layout
        if canvas_class is None:
            # matplotlib 的 Qt 后端不可用：给出可操作的中文提示，而不是空白页
            self._canvas_placeholder.setText(tr(
                "无法加载绘图组件（matplotlib 的 Qt 后端不可用）。\n\n"
                "怎么办：请确认已安装 matplotlib（pip install -r requirements.txt），"
                "然后重启软件。数据本身没有问题，其它页面仍可正常使用。"))
            self._canvas_widget = self._canvas_placeholder
            return

        figure = plot_draw.create_figure(width=9.0, height=5.2, dpi=100)
        canvas = canvas_class(figure)
        canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # 用真画布替换占位控件
        layout.removeWidget(self._canvas_placeholder)
        self._canvas_placeholder.hide()
        layout.addWidget(canvas, 1)

        self._canvas_caption = QLabel("", self._canvas_holder)
        self._canvas_caption.setObjectName("plot_caption")
        self._canvas_caption.setWordWrap(True)
        layout.addWidget(self._canvas_caption)

        self._canvas = canvas
        self._canvas_widget = canvas

    # =====================================================================
    # 二、状态与数据源
    # =====================================================================
    def _on_data_changed(self, key: str) -> None:
        """任一数据槽变化后刷新可用的数据源与字段（方案第三章纪律 3）。"""
        if key in ("clean", "index_row", "index_summary", "*"):
            self._result = None
            self._refresh_from_state()

    def _refresh_from_state(self) -> None:
        """按当前数据情况重建数据源列表，并自动推荐图表。"""
        available = [name for name in _SOURCE_ORDER
                     if self.app_state.has(_SOURCE_TO_KEY[name])]
        has_any = bool(available)
        self._stack.setCurrentIndex(1 if has_any else 0)
        self._draw_button.setEnabled(has_any)
        self._export_button.setEnabled(len(self._exported) > 0)

        if not has_any:
            self._draw_button.setToolTip(tr("请先准备数据"))
            self._source_combo.clear()
            self._value_list.clear()
            return

        self._building = True
        try:
            current = self._source_combo.currentText()
            self._source_combo.clear()
            self._source_combo.addItems(available)
            # 选择当前数据源的规则：
            #   · 用户手动选过 → 保留他的选择（刷新不该把用户挑好的表换掉）；
            #   · 否则用默认优先级：汇总指标 > 行级指标 > 清洗数据。
            # 注意：不能用 currentText() 判断"用户是否选过"——addItems 之后
            # currentText() 已经变成第一项了，那会让默认优先级永远不生效（实测踩坑）。
            if self._source_touched and current in available:
                preferred = current
            elif SOURCE_SUMMARY in available:
                preferred = SOURCE_SUMMARY
            elif SOURCE_ROW in available:
                preferred = SOURCE_ROW
            else:
                preferred = available[0]
            index = self._source_combo.findText(preferred)
            if index >= 0:
                self._source_combo.setCurrentIndex(index)
        finally:
            self._building = False
        self._rebuild_fields(auto_recommend=True)

    def current_source_key(self) -> str:
        return _SOURCE_TO_KEY.get(self._source_combo.currentText(), "clean")

    def current_table(self):
        return self.app_state.get_table(self.current_source_key())

    def _on_source_changed(self, _text: str) -> None:
        if self._building:
            return
        # 用户手动改过数据源：之后刷新时就保留他的选择，不再跳回默认表
        self._source_touched = True
        self._result = None
        self._rebuild_fields(auto_recommend=True)

    def _on_chart_type_changed(self, _text: str) -> None:
        if self._building:
            return
        self._update_plan()

    def _on_value_changed(self, _item: QListWidgetItem) -> None:
        if self._building:
            return
        self._save_choices()
        self._update_plan()

    def _on_palette_changed(self, _text: str) -> None:
        """切换配色方案：立即用新配色重画，让用户马上看到区别。"""
        if self._building:
            return
        self._save_choices()
        self._update_plan()
        if self._result is not None:
            self.draw_chart()

    def _on_format_changed(self, _text: str) -> None:
        if self._building:
            return
        self._save_choices()
        self._update_plan()

    def current_palette(self) -> str:
        """当前配色方案（未知值回退到默认）。"""
        mode = self._palette_combo.currentText()
        return mode if mode in plot_draw.ALL_PALETTE_MODES else plot_draw.DEFAULT_PALETTE_MODE

    def current_formats(self) -> Tuple[str, ...]:
        """当前选择的输出格式（png / svg / 两者）。"""
        return plot_draw.normalize_formats(self._FORMAT_OPTIONS.get(
            self._format_combo.currentText(), ("png", "svg")))

    def _rebuild_fields(self, auto_recommend: bool = True) -> None:
        """重建分类字段下拉框与数值字段复选列表。

        auto_recommend=True 时按数据类型自动预选图表类型与字段（方案 5.6），
        让用户进页面就能直接点【绘制图表】看到结果。
        """
        table = self.current_table()
        if table is None or len(table.columns) == 0:
            return

        import pandas as pd

        columns = [str(name) for name in table.columns]
        # 统一走 plot_draw 的筛选：它会排除样方号/编号这类"看着是数值、其实是标识"
        # 的列。这里若自己写一遍 is_numeric_dtype，样方号又会混进 Y 轴列表
        # （实测踩坑：改好了 core 却没改界面，等于没改）。
        numeric = plot_draw.plottable_numeric(table)

        self._building = True
        try:
            # ---- 图表类型：只列出适用的 ----
            types = plot_draw.chart_types_for(table) or [plot_draw.CHART_QUADRAT_BAR]
            self._chart_combo.clear()
            self._chart_combo.addItems(types)

            # ---- 分类字段 ----
            self._category_combo.clear()
            self._category_combo.addItems(columns)

            # ---- 数值字段 ----
            self._value_list.clear()
            for name in numeric:
                item = QListWidgetItem(plot_draw.column_label(name), self._value_list)
                item.setData(Qt.ItemDataRole.UserRole, name)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)

            # 标签里带上"当前是哪个数据源、共几个可画列"。
            # 换数据源时可画列会变（测量列只在清洗数据/行级指标里），
            # 不标出来用户会以为软件把列弄丢了（实测反馈）。
            source_name = self._source_combo.currentText() or ""
            self._value_label.setText(
                tr("④ 数值 / Y 轴（可多选）：") + "\n{0} · {1}".format(
                    source_name, tr("{0} 列可选", len(numeric))))
            self._value_list.setToolTip(
                tr("可多选") + "\n" + tr(
                    "「清洗数据」与「行级指标」含树高、基径、冠幅等原始测量列；"
                    "「汇总指标」是按样方聚合的结果，只有统计指标。"))
        finally:
            self._building = False

        if auto_recommend:
            self._apply_recommendation(table)
        self._update_plan()

    def _apply_recommendation(self, table) -> None:
        """按数据内容自动选好图表类型、分类字段与数值字段。"""
        spec = plot_draw.recommend_spec(table, self.current_source_key())
        self._building = True
        try:
            if self._chart_combo.findText(spec.chart_type) >= 0:
                self._chart_combo.setCurrentText(spec.chart_type)
            if spec.category and self._category_combo.findText(spec.category) >= 0:
                self._category_combo.setCurrentText(spec.category)
            wanted = set(spec.values)
            for index in range(self._value_list.count()):
                item = self._value_list.item(index)
                item.setCheckState(Qt.CheckState.Checked if item.data(Qt.ItemDataRole.UserRole) in wanted
                                   else Qt.CheckState.Unchecked)
        finally:
            self._building = False

    def selected_values(self) -> List[str]:
        chosen: List[str] = []
        for index in range(self._value_list.count()):
            item = self._value_list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                chosen.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return chosen

    def _save_choices(self) -> None:
        """记住用户选择，下次进入页面直接沿用（方案 5.1）。"""
        settings.instance().save_plot_choices({
            "source": self._source_combo.currentText(),
            "chart_type": self._chart_combo.currentText(),
            "category": self._category_combo.currentText(),
            "values": self.selected_values(),
            "palette": self.current_palette(),
            "format": self._format_combo.currentText(),
        })

    def _restore_choices(self) -> None:
        """恢复上次的配色与输出格式（图表类型/字段由推荐逻辑决定，不在此处理）。"""
        choices = settings.instance().load_plot_choices()
        self._building = True
        try:
            palette = choices.get("palette")
            if palette in plot_draw.ALL_PALETTE_MODES:
                self._palette_combo.setCurrentText(palette)
            fmt = choices.get("format")
            if fmt in self._FORMAT_OPTIONS:
                self._format_combo.setCurrentText(fmt)
        finally:
            self._building = False

    def _update_plan(self) -> None:
        """实时说明"当前设置会画出什么图"。"""
        table = self.current_table()
        if table is None:
            self._plan_label.setText("")
            return
        values = self.selected_values()
        category = self._category_combo.currentText() or None
        chart_type = self._chart_combo.currentText()
        if not values:
            self._plan_label.setText("还没有勾选数值列，请在④里至少勾选一列要画的指标。")
            self._plan_label.set_state("error")
            return
        title = plot_draw.describe_chart(chart_type, category,
                                         values, group_by=category)
        self._plan_label.setText(
            "将绘制：{0}　｜　图题：{1}　｜　X 轴：{2}　｜　Y 轴：{3}"
            "　｜　配色：{4}　｜　输出：{5}".format(
                chart_type, title, plot_draw.column_label(category) or "—",
                "、".join(plot_draw.column_label(v) for v in values),
                self.current_palette(),
                "＋".join(fmt.upper() for fmt in self.current_formats()) or "未选择"))
        self._plan_label.set_state("normal")

    # =====================================================================
    # 三、绘图
    # =====================================================================
    def draw_chart(self) -> bool:
        """按当前设置绘图并显示在画布上。"""
        if _figure_canvas_class() is None:
            QMessageBox.warning(
                self, tr("无法绘图"),
                tr("绘图组件不可用（matplotlib 的 Qt 后端未加载）。\n\n"
                   "怎么办：确认已安装 matplotlib 后重启软件。"))
            return False

        table = self.current_table()
        if table is None or len(table) == 0:
            QMessageBox.information(
                self, "没有可绘制的数据",
                "请先在【数据导入】与【智能清洗】页准备好数据。\n\n"
                "最快的办法：到【数据导入】页点【载入示例数据】。")
            return False

        values = self.selected_values()
        if not values:
            QMessageBox.information(
                self, "还没有勾选数值列",
                "请在「④ 数值 / Y 轴」里至少勾选一列要画的指标，例如香农指数或气温。")
            return False

        spec = plot_draw.ChartSpec(
            table_key=self.current_source_key(),
            chart_type=self._chart_combo.currentText(),
            category=self._category_combo.currentText() or None,
            values=values,
            group_by=self._category_combo.currentText() or None,
            aggregate=True,
            palette=self.current_palette(),
        )

        self._set_busy(True)
        try:
            result = plot_draw.draw_chart(table, spec)
        except plot_draw.PlotError as exc:
            self._set_busy(False)
            QMessageBox.warning(self, "无法绘制图表", str(exc))
            return False
        except Exception as exc:  # noqa: BLE001 - 交给全局异常处理显示中文提示
            self._set_busy(False)
            from utils.errors import report_exception

            report_exception(exc, context=tr("绘制图表"))
            return False
        finally:
            self._set_busy(False)

        self._show_result(result)
        self._save_choices()
        return True

    def _show_result(self, result: plot_draw.ChartResult) -> None:
        """把新图换到画布上，并释放上一张图占用的内存。"""
        # 画布可能还没建（用户直接调了绘图而没先切到本页，例如测试或一键流程）
        self._ensure_canvas()
        old = self._result
        self._result = result
        if self._canvas is not None:
            self._canvas.figure = result.figure
            self._canvas.draw_idle()
        if old is not None and old.figure is not result.figure:
            plot_draw.close_figure(old)

        caption = "图题：{0}".format(result.title)
        if result.font_source:
            caption += "　｜　中文字体：{0}".format(result.font_source)
        caption_label = getattr(self, "_canvas_caption", None)
        if caption_label is not None:
            caption_label.setText(caption)

        text = "绘图完成：{0}".format(result.title)
        if result.notes:
            text += "　" + "；".join(result.notes)
        text += "　可以点【导出图片】保存为 300dpi PNG 与 SVG。"
        self._hint.setText(text)
        self._hint.set_state("normal")
        self._export_button.setEnabled(True)
        # 标记"已经画过图"，供步骤条第 4 步打勾（阶段 8 走查发现：
        # 原来只有导出图片才算完成，一键流程跑完后步骤条会中间断一格）
        self.app_state.update_meta(chart_drawn=True)

        # 把导出过的旧图清掉：新图还没导出
        self._exported = []

    def _set_busy(self, busy: bool) -> None:
        self._draw_button.setEnabled(not busy)
        if busy:
            self._hint.setText("正在绘制图表，请稍候…")
            self._hint.set_state("info")

    # =====================================================================
    # 四、导出
    # =====================================================================
    def export_images(self) -> None:
        """按所选格式导出当前图表，并把路径写入 AppState 的 figures 槽。

        输出格式由「⑥ 输出格式」决定：PNG 位图（300dpi）/ SVG 矢量图 / 两者都要。
        """
        if self._result is None:
            QMessageBox.information(
                self, tr("还没有图可以导出"),
                tr("请先点【绘制图表】，看到图之后再导出。"))
            return

        formats = self.current_formats()
        if not formats:
            QMessageBox.information(
                self, tr("还没有选择输出格式"),
                tr("请在「⑥ 输出格式」里选择要导出的格式（PNG、SVG 或两者都要）。"))
            return

        prefs = settings.instance()
        start_dir = prefs.export_dir or prefs.last_dir or str(Path.home())
        directory = QFileDialog.getExistingDirectory(self, tr("选择图片保存位置"), start_dir)
        if not directory:
            return

        try:
            written = plot_draw.save_figure(self._result, Path(directory),
                                            formats=formats, png_dpi=300)
        except PermissionError:
            QMessageBox.warning(
                self, "导出失败",
                "无法写入目录：\n{0}\n\n原因：该目录不可写，或同名文件正在被其它程序打开。\n\n"
                "怎么办：换一个目录重试。".format(directory))
            return
        except Exception as exc:  # noqa: BLE001
            from utils.errors import report_exception

            report_exception(exc, context=tr("导出图片"))
            return

        self._exported = written
        prefs.export_dir = directory

        # 写入 AppState（方案 3.1：figures 槽存放已生成图片的路径列表）
        existing = self.app_state.get_list("figures")
        self.app_state.set_list("figures", existing + [str(path) for path in written])

        box = QMessageBox(self)
        box.setWindowTitle(tr("导出成功"))
        box.setIcon(QMessageBox.Icon.Information)
        box.setText("已导出 {0} 个图片文件（{1}）。".format(
            len(written), "＋".join(fmt.upper() for fmt in formats)))
        box.setInformativeText("保存位置：\n{0}\n\n{1}".format(
            directory, "\n".join("· " + path.name for path in written)))
        open_text = tr("打开输出文件夹")
        box.addButton(open_text, QMessageBox.ButtonRole.ActionRole)
        box.addButton(tr("关闭"), QMessageBox.ButtonRole.RejectRole)
        # 用 ask_modal 判断点了哪个按钮：直接比较按钮对象在模态框返回后会失效，
        # 表现为"点了打开文件夹却没反应"（实测 bug）
        if ui_style.ask_modal(box, open_text):
            # 避免用户找不到文件（方案 5.6 导出即所得）
            reveal_in_explorer(directory)

    # =====================================================================
    # 五、供主窗口查询
    # =====================================================================
    def has_figures(self) -> bool:
        return self.app_state.has("figures")

    def last_result(self) -> Optional[plot_draw.ChartResult]:
        return self._result

    def exported_files(self) -> List[Path]:
        return list(self._exported)

    def _goto_page(self, index: int) -> None:
        window = self.window()
        handler = getattr(window, "goto_page", None)
        if callable(handler):
            handler(index)


def create_page(app_state: AppState) -> QWidget:
    """页面工厂：主窗口用本函数挂载真实绘图页。"""
    return PlotPage(app_state)
