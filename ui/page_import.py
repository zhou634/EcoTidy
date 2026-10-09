# -*- coding: utf-8 -*-
"""
数据导入页面（开发方案 第六章 阶段 2 + 第五章易用性规范）

页面职责（副标题已写明"做什么 / 需要什么 / 产出什么"）：
    把 csv / xlsx / txt 文件读进来，识别数据类型、必要时让用户确认列映射，
    标准化后写入 app_state["raw"]，供后续清洗、计算、绘图、导出使用。

易用性要点（方案第五章，逐条对应）：
    5.1 「载入示例数据」按钮常驻在醒目位置，非仅首次启动时出现；
    5.3 无数据时显示空状态（图标 + 一句话 + 行动按钮），禁止空白页；
    5.4 数据类型识别结果预填，用户可随时手动改写；
    5.5 解析失败弹窗回答"哪个文件 / 哪一列 / 为什么 / 怎么办"，并直接给修复入口；
    5.6 整窗拖拽导入、一键完成全部分析；
    5.7 页面右上角「?」由主窗口统一提供。

线程约定：
    解析在后台线程执行（QThread + 工作对象），避免大文件把界面卡成假死；
    解析结果经队列信号回到主线程，再由主线程写入 AppState —— 绝不跨线程碰界面对象。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import data_parse, settings
from core.app_state import AppState
from ui import style as ui_style
from ui.widgets.column_mapper import ColumnMapperDialog
from ui.widgets.empty_state import EmptyState
from ui.widgets.pandas_model import PandasModel, column_tips_from_names
from utils.i18n import tr

# 文件列表的列定义
_LIST_HEADERS = ["文件", "识别出的数据类型", "编码 / 格式", "状态"]
_STATUS_PENDING = "待确认"
_STATUS_OK = "已识别"
_STATUS_NEED_MAPPING = "需手工指定列"
_STATUS_ERROR = "无法读取"

# 文件选择对话框的过滤器（离线软件，只支持这三种格式）
_FILE_FILTER = "支持的数据文件 (*.csv *.xlsx *.txt);;CSV 文件 (*.csv);;Excel 工作簿 (*.xlsx);;文本表格 (*.txt);;所有文件 (*)"


class _ParseWorker(QObject):
    """后台解析工作对象。

    纪律：本对象只调用 core 里的纯函数，不读写任何界面对象、不碰 AppState。
    结果通过信号发回主线程，由页面在主线程里落库。
    """

    finished = Signal(object)   # 参数：data_parse.ParseResult
    failed = Signal(object)     # 参数：BaseException（通常是 ParseError）

    def __init__(self, paths: List[Path], data_type: Optional[str],
                 mapping: Optional[Dict[str, Any]]) -> None:
        super().__init__()
        self._paths = list(paths)
        self._data_type = data_type
        self._mapping = mapping

    def run(self) -> None:
        """执行解析并把结果发回主线程。任何异常都转成 failed 信号，绝不吞掉。"""
        try:
            result = data_parse.parse_files(self._paths, data_type=self._data_type, mapping=self._mapping)
        except BaseException as exc:  # noqa: BLE001 - 统一交给主线程翻译成中文提示
            self.failed.emit(exc)
            return
        self.finished.emit(result)


class ImportPage(QWidget):
    """【数据导入】页面。

    对外接口（供主窗口调用）：
        handle_dropped_files(paths)  接收整窗拖拽进来的文件
        load_demo()                  载入内置示例数据（Ctrl+L 与菜单）
        confirm_import()             确认导入（Ctrl+R 在本页的主动作）
        has_imported_data()          是否已成功导入数据
    """

    def __init__(self, app_state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("import_page")
        self.app_state = app_state

        self._files: List[Path] = []          # 已选择的文件
        self._last_result: Optional[data_parse.ParseResult] = None
        self._last_mapping: Optional[Dict[str, Any]] = None
        self._thread: Optional[QThread] = None
        self._worker: Optional[_ParseWorker] = None

        self._build_ui()
        self._refresh_buttons()
        self._show_empty_preview()

    # =====================================================================
    # 一、界面搭建
    # =====================================================================
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # ---- 操作区：所有按钮 + 一句"先点哪个" ----
        root.addWidget(self._build_action_bar())

        # ---- 文件列表 ----
        root.addWidget(self._build_file_list())

        # ---- 预览 / 空状态 二选一 ----
        self._preview_stack = QStackedWidget(self)
        self._preview_stack.setObjectName("import_preview_stack")

        self._empty = EmptyState(self)
        self._empty.set_icon("folder")
        self._empty.set_description(
            "还没有数据。可以把 csv / xlsx / txt 文件直接拖到这里，或点击下方按钮选择文件。")
        self._empty.set_action("选择文件")
        self._empty.set_secondary_action(tr("载入示例数据"))
        self._empty.action_clicked.connect(self.choose_files)
        self._empty.secondary_clicked.connect(self.load_demo)
        self._preview_stack.addWidget(self._empty)

        self._preview_stack.addWidget(self._build_preview_panel())
        root.addWidget(self._preview_stack, 1)

        # ---- 底部状态提示 ----
        self._hint = ui_style.HintQLabel("", self)
        self._hint.setObjectName("import_hint")
        self._hint.setWordWrap(True)
        root.addWidget(self._hint)

    def _build_action_bar(self) -> QWidget:
        """按钮条：选择文件 / 载入示例数据 / 清空列表 / 列映射 / 确认导入 / 一键完成全部分析。"""
        bar = QFrame(self)
        bar.setObjectName("import_action_bar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._choose_button = QPushButton(tr("选择文件…"), bar)
        self._choose_button.setObjectName("primary_button")
        self._choose_button.setMinimumSize(120, 34)
        self._choose_button.setToolTip(tr("可多选同类型文件（Ctrl+O）"))
        self._choose_button.clicked.connect(self.choose_files)
        layout.addWidget(self._choose_button)

        self._demo_button = QPushButton(tr("载入示例数据"), bar)
        self._demo_button.setObjectName("secondary_button")
        self._demo_button.setMinimumSize(130, 34)
        self._demo_button.setToolTip(tr("用内置示例数据试跑（Ctrl+L）"))
        self._demo_button.clicked.connect(self.load_demo)
        layout.addWidget(self._demo_button)

        self._clear_button = QPushButton(tr("清空列表"), bar)
        self._clear_button.setObjectName("secondary_button")
        self._clear_button.setMinimumSize(100, 34)
        self._clear_button.setToolTip(tr("清空已选文件"))
        self._clear_button.clicked.connect(self.clear_files)
        layout.addWidget(self._clear_button)

        layout.addSpacing(12)

        self._map_button = QPushButton(tr("列映射…"), bar)
        self._map_button.setObjectName("secondary_button")
        self._map_button.setMinimumSize(100, 34)
        self._map_button.setToolTip(tr("手工指定列名对应关系"))
        self._map_button.clicked.connect(self.open_column_mapper)
        layout.addWidget(self._map_button)

        self._import_button = QPushButton(tr("确认导入"), bar)
        self._import_button.setObjectName("primary_button")
        self._import_button.setMinimumSize(120, 34)
        self._import_button.setToolTip(tr("解析并生成标准化数据表（Ctrl+R）"))
        self._import_button.clicked.connect(self.confirm_import)
        layout.addWidget(self._import_button)

        layout.addStretch(1)

        self._auto_button = QPushButton(tr("一键完成全部分析"), bar)
        self._auto_button.setObjectName("primary_button")
        self._auto_button.setMinimumSize(160, 34)
        self._auto_button.setToolTip(tr("自动跑完清洗 → 计算 → 出图 → 导出"))
        self._auto_button.clicked.connect(self.run_auto_flow)
        layout.addWidget(self._auto_button)
        return bar

    def _build_file_list(self) -> QWidget:
        """文件列表：每个文件一行，显示识别类型与状态，类型可手工改写。"""
        holder = QFrame(self)
        holder.setObjectName("import_file_holder")
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        caption = QLabel(tr("待导入的文件（数据类型已自动识别，若不对可以直接在下拉框里改）"), holder)
        caption.setObjectName("import_caption")
        layout.addWidget(caption)

        self._file_tree = QTreeWidget(holder)
        self._file_tree.setObjectName("import_file_tree")
        self._file_tree.setColumnCount(len(_LIST_HEADERS))
        self._file_tree.setHeaderLabels(_LIST_HEADERS)
        self._file_tree.setRootIsDecorated(False)
        self._file_tree.setAlternatingRowColors(True)
        self._file_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._file_tree.setMinimumHeight(120)
        self._file_tree.setMaximumHeight(190)
        header = self._file_tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._file_tree.setColumnWidth(1, 150)
        layout.addWidget(self._file_tree)
        return holder

    def _build_preview_panel(self) -> QWidget:
        """预览区：数据表 + 一行"共 N 行，当前预览前 5000 行"的说明。"""
        panel = QFrame(self)
        panel.setObjectName("import_preview_panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._table_view = QTableView(panel)
        self._table_view.setObjectName("preview_table")
        self._table_view.setAlternatingRowColors(True)
        self._table_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table_view.setSortingEnabled(False)
        self._table_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._table_model = PandasModel()
        self._table_view.setModel(self._table_model)
        # 表格行高不小于 26px（方案第二章可读性要求）
        self._table_view.verticalHeader().setDefaultSectionSize(26)
        layout.addWidget(self._table_view, 1)

        self._table_caption = QLabel("", panel)
        self._table_caption.setObjectName("import_caption")
        layout.addWidget(self._table_caption)
        return panel

    # =====================================================================
    # 二、文件选择与列表维护
    # =====================================================================
    def choose_files(self) -> None:
        """弹出文件选择对话框（支持多选），并把选中的文件加入列表。"""
        start_dir = settings.instance().last_dir or str(Path.home())
        paths, _filter = QFileDialog.getOpenFileNames(
            self, "选择要导入的数据文件", start_dir, _FILE_FILTER)
        if not paths:
            return
        self.add_files(paths)

    def add_files(self, paths: List[str]) -> None:
        """把文件加入待导入列表（拖拽与文件选择共用）。

        重复文件自动跳过；每个文件立即做一次"只看表头"的快速探测，把识别结果
        预填进下拉框，让用户在导入前就能看到软件认成了什么。
        """
        added = 0
        skipped = 0
        for path_str in paths:
            path = Path(path_str)
            if path.is_dir():
                # 拖入文件夹时，把它下面的支持格式文件全部收进来
                for child in sorted(path.iterdir()):
                    if child.suffix.lower() in data_parse.SUPPORTED_SUFFIXES:
                        if child not in self._files:
                            self._append_file_row(child)
                            added += 1
                        else:
                            skipped += 1
                continue
            if path in self._files:
                skipped += 1
                continue
            if path.suffix.lower() not in data_parse.SUPPORTED_SUFFIXES:
                QMessageBox.warning(
                    self, "文件格式不支持",
                    "无法导入 {0}。\n\n"
                    "本软件支持：csv（逗号或制表符分隔）、xlsx（Excel 工作簿）、txt（文本表格）。\n\n"
                    "怎么办：用 Excel 打开后另存为「xlsx」，或另存为「CSV UTF-8（逗号分隔）」再拖进来。".format(
                        path.name))
                continue
            self._append_file_row(path)
            added += 1

        if added:
            settings.instance().last_dir = str(Path(paths[0]).parent)
            settings.instance().save_last_open_files([str(p) for p in self._files])
            self._set_hint("已加入 {0} 个文件{1}。确认无误后点【确认导入】。".format(
                added, "，跳过 {0} 个重复或不支持的文件".format(skipped) if skipped else ""))
        self._refresh_buttons()

    def _append_file_row(self, path: Path) -> None:
        """向文件列表追加一行，并做一次轻量类型探测。"""
        self._files.append(path)
        probe = data_parse.probe_file(path)

        item = QTreeWidgetItem(self._file_tree)
        item.setData(0, Qt.ItemDataRole.UserRole, str(path))
        item.setText(0, path.name)
        item.setToolTip(0, str(path))

        # 数据类型下拉框（识别结果预填，允许改写）
        combo = QComboBox(self._file_tree)
        for name in data_parse.DATA_TYPES:
            combo.addItem(name)
        if probe.get("data_type"):
            combo.setCurrentText(probe["data_type"])
        combo.setToolTip(tr("不对可在此改"))
        combo.currentTextChanged.connect(lambda _text, p=path: self._on_type_changed(p))
        self._file_tree.setItemWidget(item, 1, combo)

        encoding = probe.get("encoding") or "Excel 工作簿"
        delimiter = probe.get("delimiter") or ""
        if delimiter == "\t":
            delimiter = "制表符分隔"
        item.setText(2, "{0}{1}".format(encoding, " · " + delimiter if delimiter else ""))
        item.setText(2, item.text(2) or "—")

        if probe.get("error"):
            item.setText(3, _STATUS_ERROR)
            item.setForeground(3, Qt.GlobalColor.red)
            item.setToolTip(3, probe.get("error", ""))
            combo.setCurrentText(data_parse.TYPE_PLAIN)
        elif probe.get("needs_mapping"):
            item.setText(3, _STATUS_NEED_MAPPING)
            item.setForeground(3, Qt.GlobalColor.darkYellow)
            item.setToolTip(3, "软件没能认全字段，导入时会请你确认列映射；也可以现在就点【列映射…】")
        else:
            item.setText(3, _STATUS_OK)
            item.setToolTip(3, "已识别，可以直接导入")
        item.setToolTip(1, probe.get("suggestion", "") or combo.toolTip())

    def clear_files(self) -> None:
        """清空待导入文件列表（不影响已经导入到 raw 的数据）。"""
        self._files.clear()
        self._file_tree.clear()
        self._last_result = None
        self._last_mapping = None
        self._set_hint("已清空待导入文件列表。已导入的数据仍然保留，可在下方预览中查看。")
        self._refresh_buttons()
        if not self.app_state.has("raw"):
            self._show_empty_preview()

    def remove_selected_files(self) -> None:
        """移除列表中选中的文件（右键或 Delete 键触发）。"""
        selected = self._file_tree.selectedItems()
        if not selected:
            return
        for item in selected:
            path_str = item.data(0, Qt.ItemDataRole.UserRole)
            index = self._file_tree.indexOfTopLevelItem(item)
            self._file_tree.takeTopLevelItem(index)
            if path_str:
                try:
                    self._files.remove(Path(path_str))
                except ValueError:
                    pass
        self._set_hint("已从列表中移除 {0} 个文件。".format(len(selected)))
        self._refresh_buttons()

    def _on_type_changed(self, path: Path) -> None:
        """用户手工改了某个文件的数据类型：清掉旧的解析结果，避免用旧类型继续。"""
        if self._last_result is not None and path in self._files:
            self._last_result = None
        self._set_hint("已修改 {0} 的数据类型，请重新点【确认导入】。".format(path.name))

    def current_type_for(self, path: Path) -> str:
        """读取某个文件当前选中的数据类型。"""
        for index in range(self._file_tree.topLevelItemCount()):
            item = self._file_tree.topLevelItem(index)
            if item.data(0, Qt.ItemDataRole.UserRole) == str(path):
                widget = self._file_tree.itemWidget(item, 1)
                if isinstance(widget, QComboBox):
                    return widget.currentText()
        return data_parse.TYPE_PLAIN

    def selected_types(self) -> Dict[str, str]:
        """返回 {文件路径: 用户选定的数据类型}。"""
        return {str(path): self.current_type_for(path) for path in self._files}

    # =====================================================================
    # 三、列映射与导入
    # =====================================================================
    def open_column_mapper(self) -> None:
        """打开列映射对话框。

        列名来源：优先用当前预览数据表的列；没有预览时读第一个文件的表头。
        """
        columns = self._available_columns()
        if not columns:
            QMessageBox.information(
                self, "请先选择文件",
                "还没有可选的文件。\n\n怎么办：先点【选择文件…】或【载入示例数据】，"
                "软件读到表头之后才能做列映射。")
            return

        data_type = self._current_data_type()
        dialog = ColumnMapperDialog(columns=columns, data_type=data_type,
                                    mapping=self._last_mapping, parent=self)
        if dialog.exec() != ColumnMapperDialog.DialogCode.Accepted:
            return

        self._last_mapping = dialog.selected_mapping()
        chosen_type = dialog.selected_data_type()
        # 把用户选定的类型同步回文件列表，保证两处显示一致
        for index in range(self._file_tree.topLevelItemCount()):
            item = self._file_tree.topLevelItem(index)
            widget = self._file_tree.itemWidget(item, 1)
            if isinstance(widget, QComboBox):
                widget.setCurrentText(chosen_type)
        self._set_hint("已记录列映射：{0}。点【确认导入】按该映射解析。".format(
            "、".join("{0}←{1}".format(k, v) for k, v in self._last_mapping.items()) or "（未指定任何字段）"))
        self._refresh_buttons()

    def _available_columns(self) -> List[Any]:
        """取可用于列映射的列名列表。"""
        if self._last_result is not None and len(self._last_result.df.columns):
            return list(self._last_result.df.columns)
        if self._files:
            probe = data_parse.probe_file(self._files[0])
            return list(probe.get("columns", []))
        return []

    def _current_data_type(self) -> str:
        """当前下拉框选定的数据类型（取第一个文件）。"""
        if self._files:
            return self.current_type_for(self._files[0])
        return data_parse.TYPE_QUADRAT

    def confirm_import(self) -> None:
        """解析并导入：按文件分组类型 → 后台解析 → 写入 AppState。"""
        if not self._files:
            QMessageBox.information(
                self, "还没有选择文件",
                "请先点【选择文件…】选择要导入的数据文件，或点【载入示例数据】直接使用示例。")
            return

        # ---- 多文件类型一致性检查（方案避坑 11）----
        types = {self.current_type_for(path) for path in self._files}
        if len(types) > 1:
            detail = "\n".join("· {0} → {1}".format(path.name, self.current_type_for(path))
                               for path in self._files)
            box = QMessageBox(self)
            box.setWindowTitle("选中的文件类型不一致")
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText("不同数据类型的文件不能合并成一张表。\n\n" + detail)
            box.setInformativeText(
                "原因：样方群落数据（记录某样方有哪些物种）与传感器时序数据（按时间连续记录）"
                "结构完全不同，强行拼接会得到一张无法分析的表。\n\n"
                "怎么办：请二选一 —— ① 只保留同一类型的文件；② 分两批导入，先分析一类。")
            box.setStandardButtons(QMessageBox.StandardButton.Ok)
            box.button(QMessageBox.StandardButton.Ok).setText("知道了")
            box.exec()
            return

        data_type = self._current_data_type()
        self._start_parse(self._files, data_type, self._last_mapping)

    def has_running_task(self) -> bool:
        """是否有正在后台解析的任务（主窗口退出前需要询问/等待）。"""
        thread = self._thread
        return thread is not None and thread.isRunning()

    def stop_background_task(self) -> bool:
        """停止后台解析线程，返回是否已安全停下。

        为什么必须这么做（实测踩坑，阶段 8 大数据量回归发现）：
            解析在 QThread 里进行时如果直接退出程序，进程会以
            0xC0000409（STATUS_STACK_BUFFER_OVERRUN）异常中止 ——
            界面已经关掉、用户以为正常退出，Windows 却弹出"程序已停止工作"。
            原因是 QApplication 析构时后台线程仍在运行，Qt 内部状态被破坏。
            因此退出前一定要把线程停下来（请求中断 + 等待结束）。
        """
        thread = self._thread
        if thread is None:
            return True
        if thread.isRunning():
            thread.requestInterruption()
            # 解析通常是"读文件 + pandas 处理"，给足时间收尾；实在不停就等它自然结束
            if not thread.wait(8000):
                return False
        # 线程已确认结束，此时释放引用是安全的（见 _release_background_task）
        self._thread = None
        self._worker = None
        return True

    def _start_parse(self, paths: List[Path], data_type: str,
                     mapping: Optional[Dict[str, Any]]) -> None:
        """在后台线程里解析，并把界面切到"正在解析"状态。"""
        self._set_busy(True, tr("正在解析 {0} 个文件，请稍候…").format(len(paths)))

        # 父对象留空：线程会先于应用安全结束（见 stop_background_task），
        # 若挂在界面上，QApplication 析构时容易和线程的回收顺序打架。
        thread = QThread()
        worker = _ParseWorker(paths, data_type, mapping)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_parse_finished)
        worker.failed.connect(self._on_parse_failed)
        # 两个信号发完都要退出线程并回收，避免线程泄漏
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        # 线程真正结束之后再释放引用（见 _release_background_task 的说明）
        thread.finished.connect(self._release_background_task)
        # 保留引用：局部变量被回收会导致线程对象提前析构（界面直接卡住）
        self._thread = thread
        self._worker = worker
        thread.start()

    def _release_background_task(self) -> None:
        """线程结束信号到达后才释放对线程对象的引用。

        为什么必须等这一刻（实测踩坑，阶段 8 大数据量回归定位）：
            解析结果信号先到、线程的 quit() 后到。若在结果回调里立刻
            `self._thread = None`，Python 侧对 QThread 的最后引用就消失了，
            而此时线程尚未真正结束 —— 包装对象被回收会让 Qt 直接
            0xC0000409（STATUS_STACK_BUFFER_OVERRUN）中止进程。
            最小复现：QThread + worker + finished→thread.quit，
            只在槽里清空引用就必崩；保留引用则一切正常。
            因此这里把清空引用挂到 thread.finished 上，确保线程已收尾。
        """
        self._thread = None
        self._worker = None

    # ---------------------------------------------------------------------
    # 解析回调（均在主线程执行）
    # ---------------------------------------------------------------------
    def _on_parse_finished(self, result: data_parse.ParseResult) -> None:
        """解析成功：写入 AppState、刷新预览与状态提示。"""
        self._set_busy(False)
        # 注意：这里**不能**清空 self._thread / self._worker。
        # 结果信号比线程真正结束更早到达，此时丢掉最后一个 Python 引用
        # 会让 QThread 包装对象被回收，进程随即 0xC0000409 中止。
        # 引用统一由 _release_background_task() 在 thread.finished 后释放。

        if result.df is None or len(result.df) == 0:
            QMessageBox.warning(
                self, "没有读到数据",
                "文件解析完成，但没有得到任何数据行。\n\n"
                "原因：文件可能只有表头，或所有行都是空行。\n"
                "怎么办：用 Excel 打开确认数据是否存在后重新导入。")
            return

        self._last_result = result
        source_names = "、".join(item.path.name for item in result.files)
        self.app_state.set_table(
            "raw", result.df,
            meta={
                "source_files": [str(item.path) for item in result.files],
                "source_names": source_names,
                "encoding": sorted({item.encoding for item in result.files if item.encoding}),
                "rows_before": result.rows_raw_total,
            },
        )
        self.app_state.set_text("data_type", result.data_type)

        # ---- 预览与状态 ----
        self._table_model.set_dataframe(result.df)
        self._table_model.set_column_tips(column_tips_from_names(result.df))
        self._table_view.resizeColumnsToContents()
        self._preview_stack.setCurrentWidget(self._preview_panel_widget())
        self._table_caption.setText(self._build_caption(result))
        self._hint.setText(self._build_hint(result))
        self._hint.set_state("normal")

        # ---- 需要用户确认列映射时，主动询问一次（识别置信度不足绝不擅自猜测）----
        needs_mapping = any(item.needs_mapping for item in result.files)
        if needs_mapping:
            # 优先列"识别阶段建议补齐的字段"，其次列"最终类型下仍缺的必需字段"
            missing = sorted({field for item in result.files
                              for field in (list(item.suggested_missing) + list(item.missing_fields))})
            if missing:
                question = (
                    "数据已导入，但有字段没能自动对应上：{0}\n\n"
                    "这通常只是列名写法不同，不影响数据本身。\n\n"
                    "现在就手工指定这些字段对应的列吗？".format("、".join(missing)))
            else:
                # 完全认不出表头：没有具体缺哪个字段，但同样必须给用户一个明确的出口
                question = (
                    "数据已导入，但软件没能从表头认出这是哪一类数据。\n\n"
                    "这通常只是列名写法不同，数据本身没有问题。\n\n"
                    "现在就手工指定「数据类型」与各字段对应的列吗？")
            answer = QMessageBox.question(
                self, "部分字段没有自动识别出来", question,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.open_column_mapper()
                return

        QMessageBox.information(
            self, "导入成功",
            "已导入 {0} 个文件，共 {1:,} 行 × {2} 列。\n"
            "识别出的数据类型：{3}。\n\n"
            "下一步：到【智能清洗】页检查并修正异常值与缺失值。".format(
                len(result.files), len(result.df), len(result.df.columns), result.data_type))
        self._refresh_buttons()

    def _on_parse_failed(self, exc: BaseException) -> None:
        """解析失败：弹"哪个文件 / 哪一列 / 为什么 / 怎么办"的中文提示，并给修复入口。"""
        self._set_busy(False)
        # 同 _on_parse_finished：引用由 _release_background_task() 统一释放

        if isinstance(exc, data_parse.ParseMixedTypesError):
            QMessageBox.warning(self, "文件类型不一致", str(exc))
            return

        if isinstance(exc, data_parse.ParseError):
            box = QMessageBox(self)
            box.setWindowTitle("导入失败")
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText(str(exc))
            parts: List[str] = []
            if exc.reason:
                parts.append("原因：" + exc.reason)
            if exc.suggestion:
                parts.append("怎么办：" + exc.suggestion)
            if parts:
                box.setInformativeText("\n\n".join(parts))
            map_text = tr("打开列映射手动指定")
            plain_text = tr("改为按普通统计数据导入")
            box.addButton(map_text, QMessageBox.ButtonRole.ActionRole)
            box.addButton(plain_text, QMessageBox.ButtonRole.ActionRole)
            box.addButton(tr("取消"), QMessageBox.ButtonRole.RejectRole)

            # 用按钮文字判断点了哪个：直接比较按钮对象在模态框返回后会失效
            # （`clickedButton() is btn` 恒为 False，点了没反应 —— 实测 bug）
            chosen = ui_style.ask_modal_result(box)
            if chosen == map_text:
                self.open_column_mapper()
            elif chosen == plain_text:
                for index in range(self._file_tree.topLevelItemCount()):
                    item = self._file_tree.topLevelItem(index)
                    widget = self._file_tree.itemWidget(item, 1)
                    if isinstance(widget, QComboBox):
                        widget.setCurrentText(data_parse.TYPE_PLAIN)
                self._set_hint(tr("已把数据类型改为「普通实验统计」，请重新点【确认导入】。"))
            return

        # 非预期异常：交给全局异常处理，保证不显示堆栈、不崩溃
        from utils.errors import report_exception

        report_exception(exc, context="导入数据文件")

    # =====================================================================
    # 四、示例数据与一键流程
    # =====================================================================
    def load_demo(self) -> None:
        """载入内置示例数据（方案 5.1：让用户 10 秒内看到完整效果）。"""
        self._set_busy(True, "正在载入内置示例数据…")
        try:
            result = data_parse.load_demo_data()
        except data_parse.ParseError as exc:
            self._set_busy(False)
            QMessageBox.warning(self, "示例数据不可用", "{0}\n\n{1}".format(exc, exc.suggestion))
            return
        except Exception as exc:  # noqa: BLE001
            self._set_busy(False)
            from utils.errors import report_exception

            report_exception(exc, context=tr("载入示例数据"))
            return

        self._set_busy(False)
        # 示例数据不进入"待导入列表"（它不是用户自己的文件），直接写入 AppState
        self._last_result = result
        self.app_state.set_table(
            "raw", result.df,
            meta={"source_files": [str(item.path) for item in result.files],
                  "source_names": "内置示例数据（样方群落）", "is_demo": True},
        )
        self.app_state.set_text("data_type", result.data_type)
        self._table_model.set_dataframe(result.df)
        self._table_model.set_column_tips(column_tips_from_names(result.df))
        self._table_view.resizeColumnsToContents()
        self._preview_stack.setCurrentWidget(self._preview_panel_widget())
        self._table_caption.setText(self._build_caption(result))
        self._hint.setText(
            "已载入内置示例数据：{0:,} 行 × {1} 列（{2}）。可以直接到【智能清洗】页继续，"
            "也可以点【一键完成全部分析】看完整效果。".format(
                len(result.df), len(result.df.columns), result.data_type))
        self._hint.set_state("info")
        self._refresh_buttons()

        # 让主窗口显示"当前为示例数据"提示条（方案 5.1）
        window = self.window()
        if hasattr(window, "set_banner_visible"):
            window.set_banner_visible(True)

    def run_auto_flow(self) -> None:
        """【一键完成全部分析】：由主窗口统一编排后续步骤（清洗 → 计算 → 出图 → 报告）。"""
        window = self.window()
        handler = getattr(window, "run_auto_analysis", None)
        if callable(handler):
            handler()
            return
        # 只有主窗口没加载成功时才会走到这里（正常情况由上面 return）
        QMessageBox.warning(
            self, "无法执行一键分析",
            "主窗口没有准备好，无法自动串联后续步骤。\n\n"
            "怎么办：重启软件后重试；若仍不行，请点菜单【帮助 → 打开日志文件夹】"
            "把日志反馈给开发者。\n\n"
            "也可以手动依次完成：【智能清洗】→【指标计算】→【可视化绘图】→【报告导出】。")

    # =====================================================================
    # 五、拖拽入口（主窗口转发）
    # =====================================================================
    def handle_dropped_files(self, paths: List[str]) -> None:
        """接收主窗口转发过来的拖拽文件。"""
        self.add_files(paths)

    # =====================================================================
    # 六、界面状态维护
    # =====================================================================
    def _preview_panel_widget(self) -> QWidget:
        """返回预览面板控件（stack 里的第二页）。"""
        return self._preview_stack.widget(1)

    def _show_empty_preview(self) -> None:
        """没有数据时显示空状态，不留空白页（方案 5.3）。"""
        self._preview_stack.setCurrentWidget(self._empty)
        self._table_caption.setText("")

    def _set_busy(self, busy: bool, message: str = "") -> None:
        """解析期间禁用触发按钮，并给出可见的进度反馈（方案第二章防假死）。"""
        for button in (self._choose_button, self._demo_button, self._clear_button,
                       self._map_button, self._import_button, self._auto_button):
            button.setEnabled(not busy)
        if busy:
            self._hint.setText(message)
            self._hint.set_state("info")
            self._choose_button.setText("正在解析…" if busy else tr("选择文件…"))
        else:
            self._choose_button.setText(tr("选择文件…"))
        if not busy:
            self._refresh_buttons()

    def _set_hint(self, text: str) -> None:
        """更新底部提示文字。"""
        self._hint.setText(text)

    def _refresh_buttons(self) -> None:
        """按当前状态启用/禁用按钮，并说明原因（方案第二章"按钮状态"）。"""
        has_files = bool(self._files)
        has_raw = self.app_state.has("raw")

        self._clear_button.setEnabled(has_files)
        self._map_button.setEnabled(has_files or has_raw)
        self._import_button.setEnabled(has_files)
        if not has_files:
            self._import_button.setToolTip(tr("请先选择文件，或点【载入示例数据】"))
        else:
            self._import_button.setToolTip("解析文件并生成标准化数据表（Ctrl+R）")

        # 一键完成全部分析：需要已有数据
        ready = has_raw or has_files
        self._auto_button.setEnabled(ready)
        if not ready:
            self._auto_button.setToolTip(tr("请先导入数据"))
        else:
            self._auto_button.setToolTip("用推荐参数自动完成清洗、计算、出图、生成报告，直接跳到导出页")

    def _build_caption(self, result: data_parse.ParseResult) -> str:
        """预览表下方的一行说明：行数列数 + 预览上限提示（方案第二章要求）。"""
        caption = self._table_model.summary_text()
        extras: List[str] = ["数据类型：" + result.data_type]
        if result.time_range:
            extras.append("时间跨度：{0} 至 {1}".format(result.time_range[0], result.time_range[1]))
        encodings = sorted({item.encoding for item in result.files if item.encoding})
        if encodings:
            extras.append("编码：" + "、".join(encodings))
        return "{0} ｜ {1}".format(caption, " ｜ ".join(extras))

    def _build_hint(self, result: data_parse.ParseResult) -> str:
        """底部提示：把解析过程中的中文提示汇总给用户。"""
        lines = ["已导入 {0:,} 行 × {1} 列，数据类型识别为「{2}」。".format(
            len(result.df), len(result.df.columns), result.data_type)]
        if result.warnings:
            lines.append("；".join(result.warnings))
        lines.append("下一步：到【智能清洗】页检查并修正异常值与缺失值。")
        return " ".join(lines)

    # =====================================================================
    # 七、供主窗口查询的状态
    # =====================================================================
    def has_imported_data(self) -> bool:
        """是否已经成功导入数据（主窗口据此更新步骤条与状态栏）。"""
        return self.app_state.has("raw")

    def last_result(self) -> Optional[data_parse.ParseResult]:
        """返回最近一次解析结果（供测试与后续阶段使用）。"""
        return self._last_result

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名约定
        """页面关闭时等待后台解析线程结束，避免线程在解释器退出时报错。"""
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
        super().closeEvent(event)


def create_page(app_state: AppState) -> QWidget:
    """页面工厂：主窗口用本函数挂载真实导入页（阶段 2 起的统一接入方式）。"""
    return ImportPage(app_state)
