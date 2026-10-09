# -*- coding: utf-8 -*-
"""
主窗口（开发方案 第二章 全局 UI 规范 + 第五章 5.2 流程导航，阶段 1 交付）

自上而下的固定结构（方案原文）：
    菜单栏 → 流程步骤条 → 主体区（左侧导航 200px + 右侧 QStackedWidget）→ 状态栏

本阶段（阶段 1）的交付边界：
    · 5 个页面在这里**实例挂载 + 空状态占位**，业务功能留空；
    · 页面构造函数只接收 app_state，不接收兄弟页面引用（方案第三章纪律）；
    · 步骤条与数据状态联动、状态栏概况、快捷键、拖拽导入入口全部到位；
    · 需要后续阶段实现的按钮，点击时给出"说明 + 可用时间 + 现在能做什么"的提示，
      不允许出现点了没反应、或抛异常堆栈的情况。

后续各阶段接入方式（不改动本文件结构）：
    阶段 2 起，页面模块提供 create_page(app_state) -> QWidget，
    把返回的控件交给 register_page_widget(页码, 控件) 即可替换空状态占位。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QIcon, QKeySequence,
                           QPainter, QPixmap)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QScrollArea,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from core import settings
from core.app_state import AppState
from ui import style as ui_style
from ui.widgets.empty_state import EmptyState
from ui.widgets.help_content import page_section
from ui.widgets.help_panel import show_help
from ui.widgets.step_bar import StepBar
from utils import i18n, paths
from utils.errors import reveal_in_explorer
from utils.i18n import tr

# ---------------------------------------------------------------------------
# 页面与步骤定义（顺序固定在方案第二章，禁止调整）
# ---------------------------------------------------------------------------
PAGE_TITLES: List[str] = [
    "数据导入",
    "智能清洗",
    "指标计算",
    "可视化绘图",
    "报告导出",
]

# 页面副标题（方案 5.2：每页标题下方固定一行浅灰小字，回答"做什么/需要什么/产出什么"）
PAGE_SUBTITLES: List[str] = [
    "支持 csv / xlsx / txt 文件，可多选、也可直接把文件拖进窗口。需要：你的原始数据文件（或点【载入示例数据】直接用软件自带的例子）。产出：标准化后的原始数据表。",
    "检查并修正数据中的异常值与缺失值。需要：已导入的原始数据。产出：可直接用于指标计算的干净数据表。",
    "计算群落结构、多样性与环境时序统计指标。需要：清洗后的数据。产出：行级指标表与汇总指标表。",
    "按数据类型选择图表并导出高清图片。需要：指标计算结果或清洗后的数据。产出：PNG / SVG 图片。",
    "把数据表、指标表、清洗日志、图片与报告一次性导出。需要：至少完成前面任一步骤。产出：完整成果文件夹与成果说明。",
]

# 各页空状态文案与行动按钮（方案 5.3 表格原文，禁止改写）
EMPTY_STATES: List[Dict[str, str]] = [
    {
        "description": "还没有数据。可以把 csv / xlsx / txt 文件直接拖到这里，或点击下方按钮选择文件。",
        "action": "选择文件",
        "secondary": "载入示例数据",
    },
    {
        "description": "请先导入数据。",
        "action": "前往【数据导入】",
    },
    {
        "description": "请先完成数据清洗。",
        "action": "前往【智能清洗】",
    },
    {
        "description": "还没有可绘制的数据，请先完成指标计算；也可以直接用清洗后的数据绘图。",
        "action": "前往【指标计算】",
    },
    {
        "description": "暂无可导出的成果，请先完成前面的步骤。",
        "action": "前往【数据导入】",
    },
]

# 空状态行动按钮的目标页面（-1 表示留在本页，由本页自己处理）
EMPTY_ACTION_TARGET: List[int] = [0, 0, 1, 2, 0]

# 各页对应的空状态图标类型（由 ui/widgets/icons.py 画成黑白线稿，不用彩色 Emoji）
EMPTY_ICONS: List[str] = ["folder", "broom", "ruler", "chart", "document"]


class MainWindow(QMainWindow):
    """主窗口。

    对外接口：
        goto_page(index)                 切换到第 index 个页面（0~4）
        register_page_widget(index, w)   用真实页面控件替换空状态占位
    """

    def __init__(self, app_state: Optional[AppState] = None) -> None:
        super().__init__()
        # ---- 数据共享由 AppState 负责，主窗口本身不持有业务数据 ----
        self.app_state: AppState = app_state if app_state is not None else AppState.instance()
        # 各阶段接入的真实页面（未接入时为 None），见 _mount_available_pages()
        self.import_page: Optional[Any] = None

        # 标题栏只显示软件名（按要求不出现中文）
        self.setWindowTitle(paths.APP_TITLE)
        self.resize(1200, 800)                      # 方案：初始尺寸 1200×800
        self.setMinimumSize(1000, 660)              # 支持缩放，但保证排版不挤坏
        self.setAcceptDrops(True)                   # 支持整窗拖拽导入
        self.setStyleSheet(_GLOBAL_QSS)
        self.setWindowIcon(build_app_icon())

        # ---- 页面容器与占位控件 ----
        self._page_widgets: List[QWidget] = []
        self._content_slots: List[QWidget] = []
        self._empty_states: List[EmptyState] = []
        self._nav_buttons: List[QPushButton] = []
        self._current_page: int = 0
        # 真实页面实例（阶段 2 起逐页接入）：页码 -> 页面控件
        self._real_pages: Dict[int, QWidget] = {}
        # 需要随语言切换更新的控件引用（retranslate 用）
        self._header_title: Optional[QLabel] = None
        self._header_subtitle: Optional[QLabel] = None
        self._help_button: Optional[QPushButton] = None
        self._history_button: Optional[QPushButton] = None
        self._page_titles: Dict[int, QLabel] = {}
        self._page_subtitles: Dict[int, QLabel] = {}

        # ---- 自上而下搭建界面 ----
        self._build_menu_bar()
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        self._banner = self._build_banner()
        root.addWidget(self._banner)
        self.step_bar = StepBar(central)
        root.addWidget(self.step_bar)
        root.addWidget(self._build_body(), 1)

        self._build_status_bar()
        self._bind_shortcuts()

        # 步骤条点击 -> 切换页面；两条路径共用 goto_page，保证状态永远同步
        self.step_bar.step_clicked.connect(self.goto_page)

        # 任何数据槽发生变化，步骤条与状态栏自动刷新（方案第三章纪律 3）
        self.app_state.data_changed.connect(self._on_data_changed)

        # 保存/恢复窗口尺寸位置（方案：窗口尺寸与位置须记忆）
        self._restore_window_state()

        # 初始状态：第 1 页 + 空的界面，并立刻同步一次步骤条与状态栏
        self.goto_page(self._safe_start_page())
        self._refresh_status_bar()

        # 挂载已交付的真实页面（阶段 2：数据导入页）
        self._mount_available_pages()

        # 首屏同步一次步骤条与状态栏。
        # 为什么必须在挂载页面之后再调一次（实测踩坑，阶段 8 走查发现）：
        #   _refresh_steps() 只在 data_changed 时被触发，而刚启动时没有任何数据变化，
        #   于是步骤条右侧的"下一步：…"提示一直是空的 ——
        #   新用户进来看不到"该先干什么"这句最关键的引导。
        self._refresh_steps()
        self._refresh_status_bar()

        # 数据目录若发生回退（例如 %APPDATA% 不可写），启动就明确告知
        self._warn_if_data_dir_changed()

        # 首次启动引导（方案 5.1）：仅在没有"引导已完成"标记时弹出
        QTimer.singleShot(0, self._maybe_show_welcome)
        # 更名提示：旧版数据目录仍在时告知一次
        QTimer.singleShot(400, self._maybe_warn_legacy_data)

        # 语言变化时重刷主窗口文案（各页面自己也连着这个信号）
        i18n.language_bus().language_changed.connect(lambda _code: self.retranslate())

    # =====================================================================
    # 页面挂载
    # =====================================================================
    def _mount_available_pages(self) -> None:
        """挂载当前阶段已经交付的真实页面。

        为什么要做异常保护：某一页初始化失败不应该让整个主窗口打不开——
        用户可以继续用其它页面，同时错误被记入日志并给出中文提示。
        """
        self.import_page: Optional[Any] = None
        self.clean_page: Optional[Any] = None
        self.calc_page: Optional[Any] = None
        self.plot_page: Optional[Any] = None
        self.export_page: Optional[Any] = None

        try:
            from ui.page_import import ImportPage

            self.import_page = ImportPage(self.app_state, self)
            self.register_page_widget(0, self.import_page)
        except Exception as exc:  # noqa: BLE001 - 保证主窗口一定能起来
            from utils.errors import report_exception

            report_exception(exc, context="加载【数据导入】页面")

        try:
            from ui.page_clean import CleanPage

            self.clean_page = CleanPage(self.app_state, self)
            self.register_page_widget(1, self.clean_page)
        except Exception as exc:  # noqa: BLE001
            from utils.errors import report_exception

            report_exception(exc, context="加载【智能清洗】页面")

        try:
            from ui.page_calc import CalcPage

            self.calc_page = CalcPage(self.app_state, self)
            self.register_page_widget(2, self.calc_page)
        except Exception as exc:  # noqa: BLE001
            from utils.errors import report_exception

            report_exception(exc, context="加载【指标计算】页面")

        try:
            from ui.page_plot import PlotPage

            self.plot_page = PlotPage(self.app_state, self)
            self.register_page_widget(3, self.plot_page)
        except Exception as exc:  # noqa: BLE001
            from utils.errors import report_exception

            report_exception(exc, context="加载【可视化绘图】页面")

        try:
            from ui.page_export import ExportPage

            self.export_page = ExportPage(self.app_state, self)
            self.register_page_widget(4, self.export_page)
        except Exception as exc:  # noqa: BLE001
            from utils.errors import report_exception

            report_exception(exc, context="加载【报告导出】页面")

    # =====================================================================
    # 首次启动引导（方案 5.1）
    # =====================================================================
    def _maybe_show_welcome(self) -> None:
        """首次运行时弹出欢迎对话框，两个按钮：先看示例 / 我自己的数据。"""
        prefs = settings.instance()
        if prefs.welcome_done:
            return
        prefs.welcome_done = True

        box = QMessageBox(self)
        box.setWindowTitle("欢迎使用 {0}".format(paths.APP_TITLE))
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(
            "<b>离线生态数据清洗与分析工具</b><br><br>"
            "五步完成一份分析成果：<br>"
            "① 导入　② 清洗　③ 计算指标　④ 出图　⑤ 导出报告")
        box.setInformativeText("不确定从哪开始？点【载入示例数据】试试。")
        demo_text = tr("载入示例数据，先看效果")
        demo_button = box.addButton(demo_text, QMessageBox.ButtonRole.AcceptRole)
        box.addButton(tr("我自己有数据，直接开始"), QMessageBox.ButtonRole.ActionRole)
        box.setDefaultButton(demo_button)

        if ui_style.ask_modal(box, demo_text) and self.import_page is not None:
            self.import_page.load_demo()
            self.set_banner_visible(True)
        else:
            self.goto_page(0)

    def _maybe_warn_legacy_data(self) -> None:
        """更名后一次性提示：旧目录里还留着历史任务与设置。

        为什么必须提示：软件从 EcoDataAnalysis 改名为 EcoTidy 后数据目录也随之改变，
        老用户打开新版本会发现"历史任务全没了"。这里明确告诉他旧数据在哪、怎么搬，
        而不是让他自己猜。只提示一次，不自动搬移（避免动用户文件）。
        """
        prefs = settings.instance()
        if prefs.migration_notice_done:
            return
        try:
            legacy = paths.legacy_data_dirs()
        except Exception:  # noqa: BLE001 - 提示功能失败不影响使用
            return
        if not legacy:
            prefs.migration_notice_done = True
            return
        prefs.migration_notice_done = True

        old = legacy[0]
        box = QMessageBox(self)
        box.setWindowTitle("软件已更名为 {0}".format(paths.APP_TITLE))
        box.setIcon(QMessageBox.Icon.Information)
        box.setText("检测到旧版本的数据目录仍然存在：\n{0}".format(old))
        box.setInformativeText(
            "新版本的数据保存在：\n{0}\n\n"
            "旧目录里的历史任务与设置不会自动搬过来。"
            "如需继续使用，把它们复制到新目录即可。".format(paths.DATA_DIR))
        open_text = tr("打开旧目录")
        box.addButton(open_text, QMessageBox.ButtonRole.ActionRole)
        box.addButton(tr("知道了"), QMessageBox.ButtonRole.RejectRole)
        if ui_style.ask_modal(box, open_text):
            reveal_in_explorer(old)

    # =====================================================================
    # 一、界面搭建
    # =====================================================================
    def _build_menu_bar(self) -> None:
        """菜单栏：文件 / 视图 / 帮助（方案 5.7）。

        只建一次。切换语言时走 _retranslate_menu()，不重建菜单 ——
        实测 menuBar().clear() 并不能可靠移除菜单，重建会出现两套菜单并排。
        """
        menu_bar = self.menuBar()

        # ---- 文件 ----
        self._menu_file = menu_bar.addMenu("")
        self._action_open = self._add_action(
            self._menu_file, "", "Ctrl+O", self._act_open_file)
        self._action_demo = self._add_action(
            self._menu_file, "", "Ctrl+L", self._act_load_demo)
        self._menu_file.addSeparator()
        self._action_export = self._add_action(
            self._menu_file, "", "Ctrl+E", self._act_export)
        self._menu_file.addSeparator()
        self._action_quit = self._add_action(
            self._menu_file, "", "Ctrl+Q", self.close)

        # ---- 视图 ----
        self._menu_view = menu_bar.addMenu("")
        self._page_actions: List[QAction] = []
        for index in range(len(PAGE_TITLES)):
            self._page_actions.append(self._add_action(
                self._menu_view, "", "Ctrl+{0}".format(index + 1),
                lambda _checked=False, i=index: self.goto_page(i)))
        self._menu_view.addSeparator()
        self._action_prev = self._add_action(
            self._menu_view, "", "Alt+Left", lambda: self.goto_page(self._current_page - 1))
        self._action_next = self._add_action(
            self._menu_view, "", "Alt+Right", lambda: self.goto_page(self._current_page + 1))
        self._menu_view.addSeparator()

        # ---- 语言（全局中英文切换）----
        self._menu_language = self._menu_view.addMenu("")
        self._language_actions: List[QAction] = []
        for code in i18n.ALL_LANGUAGES:
            action = QAction("", self)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, c=code: self.change_language(c))
            self._menu_language.addAction(action)
            self._language_actions.append(action)

        # ---- 帮助 ----
        self._menu_help = menu_bar.addMenu("")
        self._action_manual = self._add_action(
            self._menu_help, "", "F1", lambda: self._show_help("quickstart"))
        self._action_terms = self._add_action(
            self._menu_help, "", "", lambda: self._show_help("terms"))
        self._action_glossary = self._add_action(
            self._menu_help, "", "", lambda: self._show_help("glossary"))
        self._menu_help.addSeparator()
        self._action_about = self._add_action(self._menu_help, "", "", self._act_about)

        self._retranslate_menu()

    def _retranslate_menu(self) -> None:
        """按当前语言刷新菜单文字与提示（可反复调用）。"""
        def set_action(action: QAction, text: str, tip: str = "") -> None:
            action.setText(text)
            action.setStatusTip(tip)
            action.setToolTip(tip)

        self._menu_file.setTitle(tr("文件(&F)"))
        set_action(self._action_open, tr("打开文件…"), tr("选择要导入的数据文件"))
        set_action(self._action_demo, tr("载入示例数据"), tr("使用软件自带的示例数据"))
        set_action(self._action_export, tr("导出成果"), tr("批量导出图表、数据表与分析报告"))
        set_action(self._action_quit, tr("退出"), tr("退出软件（自动记住窗口大小与设置）"))

        self._menu_view.setTitle(tr("视图(&V)"))
        for index, action in enumerate(self._page_actions):
            set_action(action, tr("第 {0} 步：{1}", index + 1, tr(PAGE_TITLES[index])),
                       tr("跳转到【{0}】页面", tr(PAGE_TITLES[index])))
        set_action(self._action_prev, tr("上一个页面"), tr("回到上一个流程页面"))
        set_action(self._action_next, tr("下一个页面"), tr("前往下一个流程页面"))

        self._menu_language.setTitle(tr("语言"))
        for action, (code, label) in zip(self._language_actions,
                                         i18n.language_menu_entries()):
            action.setText(label)
            action.setChecked(code == i18n.current_language())

        self._menu_help.setTitle(tr("帮助(&H)"))
        set_action(self._action_manual, tr("使用手册（F1）"),
                   tr("打开帮助面板：快速上手、数据要求、常见问题"))
        set_action(self._action_terms, tr("术语表"), tr("生态学术语速查"))
        set_action(self._action_glossary, tr("指标公式速查"), tr("全部指标公式一览"))
        set_action(self._action_about, tr("关于"), tr("软件用途与版本信息"))

    # =====================================================================
    # 语言切换
    # =====================================================================
    def change_language(self, code: str) -> None:
        """切换界面语言：立即重建全部文案，不需要重启软件。"""
        if not i18n.set_language(code):
            return
        settings.instance().language = code
        self.retranslate()
        # 通知各页面刷新自己的文案（它们各自实现 retranslate）
        i18n.language_bus().language_changed.emit(code)

    def retranslate(self) -> None:
        """按当前语言重建主窗口自身的所有文案。"""
        self.setWindowTitle(paths.APP_TITLE)
        if self._header_title is not None:
            self._header_title.setText(paths.APP_TITLE)
        if self._header_subtitle is not None:
            self._header_subtitle.setText(tr("导入 → 清洗 → 计算 → 出图 → 导出"))
        if self._help_button is not None:
            self._help_button.setToolTip(tr("帮助与术语速查（F1）"))

        # 菜单整体刷新（标签、提示、语言勾选都会跟着变）
        self._retranslate_menu()

        # 步骤条
        self.step_bar.retranslate()
        self._refresh_steps()

        # 左侧导航与页面标题
        for index, button in enumerate(self._nav_buttons):
            button.setText("{0}. {1}".format(index + 1, tr(PAGE_TITLES[index])))
            button.setToolTip(tr("第 {0} 步：{1}", index + 1, PAGE_SUBTITLES[index]))
        for index in range(len(PAGE_TITLES)):
            if index in self._page_titles:
                self._page_titles[index].setText(tr(PAGE_TITLES[index]))
            if index in self._page_subtitles:
                self._page_subtitles[index].setText(tr(PAGE_SUBTITLES[index]))

        if self._history_button is not None:
            self._history_button.setText(tr("历史任务…"))
            self._history_button.setToolTip(tr("查看以前的分析任务并恢复现场"))
        if self._status_detail is not None:
            self._status_detail.setToolTip(tr("各步骤数据量"))

        # 数据目录提示条：用模板 + 值重新拼装，保证英文界面也翻得到
        if getattr(self, "_data_dir_value", None) is not None and self._banner.isVisible():
            self._banner_label.setText(
                tr("提示：数据保存在 {0}，历史任务与设置也在这里。", self._data_dir_value))

        # 各页空状态（说明文字与行动按钮）。
        # 已经装了真实页面的那些空状态控件已被 Qt 销毁，retranslate 内部会自行跳过。
        for state in self._empty_states:
            try:
                state.retranslate()
            except RuntimeError:
                continue

        # 五个页面：通用遍历式重译。
        # 页内控件几十个，逐个手写重译必然漏，这里统一按类型取文字再翻。
        # 只处理文案属性，不碰表格数据（列名与单元格属于数据契约，不能翻）。
        for widget in self._real_pages.values():
            try:
                i18n.retranslate_widgets(widget)
                # 页面自己也能重译"带实参、查表翻不到"的提示（例如嵌了实际路径的那条）
                own = getattr(widget, "retranslate", None)
                if callable(own):
                    own()
            except RuntimeError:
                # 页面控件已被销毁（例如窗口正在关闭）：跳过而不是让切换语言崩掉
                continue

        # 状态栏提示与页面联动
        self.goto_page(self._current_page)
        self._refresh_status_bar()
        self.set_status_hint(tr("界面语言已切换为 {0}", i18n.language_name()))

    def _add_action(self, menu, text: str, shortcut: str, slot, tip: str = "") -> QAction:
        """向菜单添加一个动作（统一处理快捷键、状态提示、绑定）。"""
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
            action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        if tip:
            action.setStatusTip(tip)
            action.setToolTip(tip)
        action.triggered.connect(lambda _checked=False, fn=slot: fn())
        menu.addAction(action)
        return action

    def _build_header(self) -> QWidget:
        """顶部标题区：软件名 + 一行副标题 + 右上角「?」帮助按钮。"""
        header = QFrame(self)
        header.setObjectName("app_header")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(18, 12, 14, 12)
        layout.setSpacing(10)

        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        # 顶部只显示软件名（与标题栏一致，不出现中文全称）
        title = QLabel(paths.APP_TITLE, header)
        title.setObjectName("app_title")
        subtitle = QLabel(tr("导入 → 清洗 → 计算 → 出图 → 导出"), header)
        subtitle.setObjectName("app_subtitle")
        text_box.addWidget(title)
        text_box.addWidget(subtitle)
        self._header_title = title
        self._header_subtitle = subtitle
        layout.addLayout(text_box)
        layout.addStretch(1)

        help_button = QPushButton("?", header)
        help_button.setObjectName("help_button")
        help_button.setToolTip(tr("帮助与术语速查（F1）"))
        help_button.setCursor(Qt.CursorShape.PointingHandCursor)
        help_button.setFixedSize(34, 34)
        help_button.clicked.connect(lambda: self._show_help("quickstart"))
        self._help_button = help_button
        layout.addWidget(help_button)
        return header

    def _build_banner(self) -> QWidget:
        """示例数据提示条（方案 5.1：载入示例后顶部显示浅色提示条，可关闭）。

        本阶段先建好控件，阶段 2 载入示例数据时调用 set_banner_visible(True) 显示。
        """
        banner = QFrame(self)
        banner.setObjectName("demo_banner")
        layout = QHBoxLayout(banner)
        layout.setContentsMargins(18, 6, 12, 6)
        layout.setSpacing(8)

        label = QLabel(tr("当前为示例数据，可随时切换为自己的文件"), banner)
        label.setObjectName("demo_banner_text")
        layout.addWidget(label)
        self._banner_label = label
        layout.addStretch(1)

        close_button = QPushButton("关闭", banner)
        close_button.setObjectName("banner_close")
        close_button.setFixedHeight(26)
        close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        close_button.clicked.connect(lambda: self.set_banner_visible(False))
        layout.addWidget(close_button)

        banner.setVisible(False)
        return banner

    def _build_body(self) -> QWidget:
        """主体区：左侧导航面板（约 200px）+ 右侧页面堆叠容器。"""
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setObjectName("body_splitter")
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(1)

        # ---- 左侧导航 ----
        nav_panel = QFrame(splitter)
        nav_panel.setObjectName("nav_panel")
        nav_panel.setMinimumWidth(170)
        nav_panel.setMaximumWidth(260)
        nav_layout = QVBoxLayout(nav_panel)
        nav_layout.setContentsMargins(10, 12, 10, 12)
        nav_layout.setSpacing(6)

        nav_title = QLabel(tr("分析流程"), nav_panel)
        nav_title.setObjectName("nav_title")
        nav_layout.addWidget(nav_title)

        for index, page_title in enumerate(PAGE_TITLES):
            button = QPushButton("{0}. {1}".format(index + 1, tr(page_title)), nav_panel)
            button.setObjectName("nav_button")
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMinimumHeight(40)
            button.setToolTip(tr("第 {0} 步：{1}", index + 1, PAGE_SUBTITLES[index]))
            button.clicked.connect(lambda _checked=False, i=index: self.goto_page(i))
            nav_layout.addWidget(button)
            self._nav_buttons.append(button)

        nav_layout.addStretch(1)

        # 历史任务入口：打开任务列表，双击即可恢复现场
        history_button = QPushButton(tr("历史任务…"), nav_panel)
        history_button.setObjectName("nav_secondary_button")
        history_button.setCursor(Qt.CursorShape.PointingHandCursor)
        history_button.setMinimumHeight(34)
        history_button.setToolTip(tr("查看以前的分析任务并恢复现场"))
        history_button.clicked.connect(self._act_history)
        self._history_button = history_button
        nav_layout.addWidget(history_button)

        version_label = QLabel(tr("离线单机版"), nav_panel)
        version_label.setObjectName("nav_version")
        nav_layout.addWidget(version_label)

        splitter.addWidget(nav_panel)

        # ---- 右侧页面容器 ----
        self.page_stack = QStackedWidget(splitter)
        self.page_stack.setObjectName("page_stack")
        for index in range(len(PAGE_TITLES)):
            self.page_stack.addWidget(self._build_placeholder_page(index))
        splitter.addWidget(self.page_stack)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        # 左栏初始宽度 200px；英文界面文案更长，这里留 220px 避免导航文字被截断
        splitter.setSizes([220, 1000])
        return splitter

    def _build_placeholder_page(self, index: int) -> QWidget:
        """构建某一页的容器：标题 + 副标题 + 内容区（占位空状态或真实页面）。

        阶段 2 起，各页用 register_page_widget() 把真实页面控件塞进 _content_slots[index]，
        标题、副标题与该页专属「?」按钮保持由主窗口统一提供，避免每页重复实现导致文案不一致。
        """
        page = QWidget(self.page_stack)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        # ---- 标题行：标题 + 右上角该页专属「?」 ----
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title = QLabel(tr(PAGE_TITLES[index]), page)
        title.setObjectName("page_title")
        title_row.addWidget(title)
        self._page_titles[index] = title
        title_row.addStretch(1)

        page_help_button = QPushButton("?", page)
        page_help_button.setObjectName("help_button")
        page_help_button.setToolTip(tr("查看本页相关帮助"))
        page_help_button.setCursor(Qt.CursorShape.PointingHandCursor)
        page_help_button.setFixedSize(30, 30)
        # 用默认参数固化 index，避免闭包在循环里取到最后一次的值
        page_help_button.clicked.connect(lambda _checked=False, i=index: self._help_for_page(i))
        title_row.addWidget(page_help_button)
        layout.addLayout(title_row)

        # ---- 副标题：这一步做什么、需要什么、产出什么 ----
        subtitle = QLabel(tr(PAGE_SUBTITLES[index]), page)
        subtitle.setObjectName("page_subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        self._page_subtitles[index] = subtitle

        # ---- 内容区（可被真实页面替换）----
        # 外面套一层滚动区：窗口被拉得很小时，宁可出现滚动条，
        # 也不要把内容压扁成"文字叠在一起"（实测：538×285 下空状态被挤到重叠）。
        content_scroll = QScrollArea(page)
        content_scroll.setObjectName("page_scroll")
        content_scroll.setWidgetResizable(True)
        content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        content_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        content_slot = QWidget(content_scroll)
        slot_layout = QVBoxLayout(content_slot)
        slot_layout.setContentsMargins(0, 0, 0, 0)
        slot_layout.setSpacing(0)
        content_scroll.setWidget(content_slot)

        # 空状态引导（禁止空白页，方案 5.3）
        empty = EmptyState(content_slot)
        spec = EMPTY_STATES[index]
        empty.set_icon(EMPTY_ICONS[index])
        empty.set_description(tr(spec["description"]))
        empty.set_action(tr(spec["action"]))
        if spec.get("secondary"):
            empty.set_secondary_action(tr(spec["secondary"]))
        empty.action_clicked.connect(lambda i=index: self._on_empty_action(i))
        empty.secondary_clicked.connect(lambda i=index: self._on_empty_secondary(i, spec.get("secondary", "")))
        slot_layout.addWidget(empty, 1)

        layout.addWidget(content_scroll, 1)

        self._empty_states.append(empty)
        self._content_slots.append(content_slot)
        self._page_widgets.append(page)
        return page

    def _build_status_bar(self) -> None:
        """状态栏：左侧当前动作提示，右侧数据集概况（方案 5.2）。"""
        bar = QStatusBar(self)
        bar.setObjectName("status_bar")
        self.setStatusBar(bar)

        self._status_hint = QLabel(tr("准备就绪"), bar)
        self._status_hint.setObjectName("status_hint")
        bar.addWidget(self._status_hint, 1)

        self._status_detail = QLabel("", bar)
        self._status_detail.setObjectName("status_detail")
        self._status_detail.setToolTip(tr("各步骤数据量"))
        bar.addPermanentWidget(self._status_detail)

    def _bind_shortcuts(self) -> None:
        """绑定方案 5.7 规定的快捷键。

        路由说明：
            Ctrl+O / Ctrl+L / Ctrl+E 由「文件」菜单动作绑定；
            Ctrl+1~5 由「视图」菜单动作绑定；
            Alt+←/→ 由「视图」菜单动作绑定；
            F1 由「帮助 → 使用手册」动作绑定（ApplicationShortcut，任意焦点下都生效）。
        因此本方法只补一个不在菜单里展示的动作：Ctrl+R（执行当前页主要动作）。
        """
        run_action = QAction("执行当前页面的主要动作", self)
        run_action.setShortcut(QKeySequence("Ctrl+R"))
        run_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        run_action.setStatusTip("对当前页面执行主要动作（清洗／计算／绘图）")
        run_action.triggered.connect(lambda _checked=False: self._act_run_current())
        self.addAction(run_action)

    # =====================================================================
    # 二、页面切换与对外接口
    # =====================================================================
    def goto_page(self, index: int) -> None:
        """切换到第 index 个页面（0~4），并同步导航按钮、步骤条与菜单勾选状态。

        越界时自动收敛到合法范围而不是报错：首尾之外按"到头停住"处理，
        这样 Alt+←/→ 连按不会踩到异常。
        """
        index = max(0, min(index, len(PAGE_TITLES) - 1))
        self._current_page = index
        self.page_stack.setCurrentIndex(index)

        # 左侧导航按钮选中态（两块控件状态必须一致）
        for button_index, button in enumerate(self._nav_buttons):
            button.setChecked(button_index == index)

        # 步骤条"当前步"高亮
        self.step_bar.set_current(index)

        # 状态栏左侧提示当前页该做什么
        self._status_hint.setText(tr("第 {0} 步 / 共 5 步：{1}", index + 1, tr(PAGE_TITLES[index])))

    def register_page_widget(self, index: int, widget: QWidget) -> None:
        """把真实页面控件挂进第 index 页的内容区，替换原来的空状态占位。

        参数：
            index  ：页面序号 0~4
            widget ：该页真实内容控件（由 ui/page_*.py 的 create_page(app_state) 返回）
        说明：
            标题、副标题与「?」按钮仍由主窗口提供，页内只负责业务内容，
            这样 5 个页面的排版与文案风格必然一致。
        """
        if not 0 <= index < len(self._content_slots):
            return
        slot = self._content_slots[index]
        slot_layout = slot.layout()
        if slot_layout is not None:
            while slot_layout.count():
                item = slot_layout.takeAt(0)
                old = item.widget()
                if old is not None:
                    old.setParent(None)
                    old.deleteLater()
            slot_layout.addWidget(widget)
        self._real_pages[index] = widget

    def page_widget(self, index: int) -> Optional[QWidget]:
        """返回某页当前的控件（真实页面优先，未接入时返回占位页）。"""
        if index in self._real_pages:
            return self._real_pages[index]
        if 0 <= index < len(self._page_widgets):
            return self._page_widgets[index]
        return None

    def page(self, index: int) -> Optional[QWidget]:
        """返回某页的**真实页面**控件；尚未接入时返回 None（供测试与阶段 2 起调用）。"""
        return self._real_pages.get(index)

    def current_page_index(self) -> int:
        """返回当前页面序号（供测试与调试）。"""
        return self._current_page

    def set_banner_visible(self, visible: bool, text: str = "") -> None:
        """显示/隐藏顶部提示条（载入示例数据、或数据目录发生回退时使用）。

        传进来的 text 视为**已翻译好**的成品文字；不传则保持当前文字。
        （不要把中文模板传进来指望这里现翻：`retranslate_widgets`
          只会对"当前文字"做查表，带实参的模板查不到，会漏翻。）
        """
        if text:
            self._banner_label.setText(text)
        self._banner.setVisible(bool(visible))

    def _warn_if_data_dir_changed(self) -> None:
        """数据目录不是标准 %APPDATA% 位置时，启动即告知用户（阶段 7 易用性）。

        为什么必须提示：数据目录换成别处后，用户换一台电脑或清理程序目录时
        会"莫名其妙"丢掉历史任务。提前说清楚位置，比事后报错友好得多。
        """
        try:
            from utils import paths

            if paths.data_dir_is_default():
                return
            # 用 {0} 占位，让标签文字本身就是一个"可查表的模板"：
            # 切换语言时 retranslate_widgets 能查到它并正确重译。
            # 若在这里就把中文路径拼进去，那条提示在英文界面下会漏翻（实测踩坑）。
            self._data_dir_value = paths.DATA_DIR
            self.set_banner_visible(
                True,
                tr("提示：数据保存在 {0}，历史任务与设置也在这里。", paths.DATA_DIR))
        except Exception:  # noqa: BLE001 - 纯提示功能，失败不影响使用
            pass

    def set_status_hint(self, text: str) -> None:
        """更新状态栏左侧提示文字（长任务显示"正在…"用）。"""
        self._status_hint.setText(text or "")

    # =====================================================================
    # 三、数据状态联动
    # =====================================================================
    def _on_data_changed(self, key: str) -> None:
        """任何数据槽变化后统一刷新步骤条与状态栏。

        方案第三章纪律：任何页面收到 data_changed 信号后自行刷新自身预览表，
        不由发送方去操作别人的控件。主窗口作为"步骤条与状态栏"的宿主，
        在这里刷新自己负责的那部分显示。
        """
        self._refresh_steps()
        self._refresh_status_bar()

    def _refresh_steps(self) -> None:
        """按数据槽状态更新 5 个步骤的完成标记与右侧提示语。"""
        raw_ready = self.app_state.has("raw")
        clean_ready = self.app_state.has("clean")
        index_ready = self.app_state.has("index_row") or self.app_state.has("index_summary")
        # 第 4 步是"绘制图表"：画出图就算完成，不要求已经导出成图片文件。
        # （阶段 8 走查发现：原来只认 figures 槽，导致一键流程跑完后
        #   步骤条出现「✓✓✓·✓」这种中间断一格的怪显示。）
        chart_drawn = bool(self.app_state.get_meta().get("chart_drawn"))
        figures_ready = bool(self.app_state.has("figures")) or chart_drawn

        # 注意：第 5 步"导出报告"的完成状态由阶段 6 在导出成功后显式置位，
        #       这里不根据数据猜测，避免"还没导出就打了勾"的误导。
        self.step_bar.set_done(0, raw_ready)
        self.step_bar.set_done(1, clean_ready)
        self.step_bar.set_done(2, index_ready)
        self.step_bar.set_done(3, figures_ready)

        # 右侧提示语：告诉用户"下一步做什么"
        if not raw_ready:
            self.step_bar.set_hint(tr("下一步：导入数据"))
        elif not clean_ready:
            self.step_bar.set_hint(tr("下一步：清洗数据"))
        elif not index_ready:
            self.step_bar.set_hint(tr("下一步：计算指标"))
        elif not figures_ready:
            self.step_bar.set_hint(tr("下一步：绘制图表"))
        else:
            self.step_bar.set_hint(tr("下一步：导出报告"))

    def mark_export_done(self, done: bool = True) -> None:
        """置位/取消第 5 步"导出报告"的完成标记（阶段 6 导出成功后调用）。"""
        self.step_bar.set_done(4, done)

    def _refresh_status_bar(self) -> None:
        """状态栏右侧：实时显示当前数据集概况。

        文字严格按方案 5.2 的样式，例如：
            原始数据 1,240 行 × 8 列 ｜ 清洗后 1,197 行
        """
        parts: List[str] = []

        raw = self.app_state.get_table("raw")
        if raw is not None:
            parts.append("原始数据 {0:,} 行 × {1} 列".format(len(raw), len(raw.columns)))
        else:
            parts.append("尚无数据")

        clean = self.app_state.get_table("clean")
        if clean is not None:
            parts.append("清洗后 {0:,} 行".format(len(clean)))

        index_row = self.app_state.get_table("index_row")
        index_summary = self.app_state.get_table("index_summary")
        if index_row is not None or index_summary is not None:
            column_count = 0
            if index_row is not None:
                column_count += len(index_row.columns)
            if index_summary is not None:
                column_count += len(index_summary.columns)
            parts.append("已计算 {0} 项指标".format(column_count))

        figures = self.app_state.has("figures")
        if figures:
            parts.append("已生成图片")

        self._status_detail.setText(" ｜ ".join(parts))

    # =====================================================================
    # 四、拖拽导入（方案 5.6）
    # =====================================================================
    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt 命名约定
        """拖入文件时：只接受 csv / xlsx / txt，并给出即时视觉反馈。"""
        if event.mimeData().hasUrls():
            paths = [url.toLocalFile() for url in event.mimeData().urls()]
            if any(self._is_supported_file(path) for path in paths):
                event.acceptProposedAction()
                self.set_status_hint("松开鼠标即可导入这些文件")
                return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 - Qt 命名约定
        """拖拽离开窗口：恢复状态栏提示。"""
        self.goto_page(self._current_page)
        event.accept()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt 命名约定
        """放下文件：交给数据导入页处理（阶段 2 接入），并切到导入页让用户看到结果。"""
        paths: List[str] = []
        if event.mimeData().hasUrls():
            paths = [url.toLocalFile() for url in event.mimeData().urls()]
        supported = [path for path in paths if self._is_supported_file(path)]
        unsupported = [path for path in paths if path and not self._is_supported_file(path)]

        if unsupported:
            QMessageBox.warning(
                self,
                "有文件无法导入",
                "以下文件格式暂不支持：\n{0}\n\n本软件支持：csv（逗号分隔）、xlsx（Excel 工作簿）、"
                "txt（制表符分隔的文本）。\n\n怎么办：用 Excel 另存为 xlsx，或另存为「CSV UTF-8」后重试。".format(
                    "\n".join(unsupported)
                ),
            )

        if not supported:
            event.ignore()
            return

        self.goto_page(0)
        if self.import_page is not None:
            # 已接入真实导入页：交给它完成解析（它自己会给出进度与结果提示）
            self.import_page.handle_dropped_files(supported)
            self.set_status_hint("已接收 {0} 个文件，请在【数据导入】页确认后点【确认导入】。".format(len(supported)))
        else:
            handler = getattr(self._page_widgets[0], "handle_dropped_files", None)
            if callable(handler):
                handler(supported)
            else:
                self._show_not_ready(
                    "数据导入功能",
                    "已经收到你拖入的 {0} 个文件：\n{1}".format(len(supported), "\n".join(supported)),
                    "【数据导入】页面尚未加载成功。",
                    "现在可以：重启软件后重试。",
                )
        event.acceptProposedAction()

    @staticmethod
    def _is_supported_file(path: str) -> bool:
        """判断文件扩展名是否属于支持的导入格式。"""
        if not path:
            return False
        lower = path.lower()
        return lower.endswith(".csv") or lower.endswith(".xlsx") or lower.endswith(".txt")

    # =====================================================================
    # 五、动作处理（未实现的功能统一给出"说明 + 可用时间 + 现在能做什么"）
    # =====================================================================
    def _act_open_file(self) -> None:
        """文件 → 打开文件：直接调用导入页的选择文件功能。"""
        if self.import_page is not None:
            self.import_page.choose_files()
            return
        self._show_not_ready(
            "打开文件",
            "选择数据文件（支持 csv / xlsx / txt，可多选）。",
            "【数据导入】页面尚未加载成功。",
            "现在可以：重启软件重试；若仍不行，请把日志文件反馈给开发者。",
        )

    def _act_load_demo(self) -> None:
        """文件 → 载入示例数据：直接调用导入页的示例数据功能。"""
        if self.import_page is not None:
            self.import_page.load_demo()
            self.set_banner_visible(True)
            return
        self._show_not_ready(
            "载入示例数据",
            "两套示例数据已经随软件提供，载入后可立即看到完整效果。",
            "【数据导入】页面尚未加载成功。",
            "现在可以：重启软件重试。",
        )

    def _act_export(self) -> None:
        """文件 → 导出成果：切到【报告导出】页并执行一键导出。"""
        if self.export_page is not None:
            self.goto_page(4)
            self.export_page.export_all()
            return
        self._show_not_ready(
            "导出成果",
            "把数据表、指标表、清洗日志、图片和分析报告一次性导出到指定文件夹。",
            "【报告导出】页面没有加载成功（通常是该模块导入出错）。",
            "现在可以：重启软件重试；若仍不行，请点【帮助 → 打开日志文件夹】把日志反馈给开发者。",
        )

    def _act_run_current(self) -> None:
        """Ctrl+R：执行当前页面的主要动作。

        五个页面都已交付，正常情况下一定能找到对应动作；
        找不到说明该页模块导入失败，此时给出"原因 + 怎么办"，绝不允许"点了没反应"。
        """
        page = self.page_widget(self._current_page)
        for method_name in ("confirm_import", "run_clean", "run_calc", "draw_chart", "export_all"):
            handler = getattr(page, method_name, None)
            if callable(handler):
                handler()
                return
        self._act_open_file() if self._current_page == 0 else self._show_not_ready(
            ["开始导入", "开始清洗", "开始计算", "绘制图表", "一键导出"][self._current_page],
            "对【{0}】页执行主要动作。".format(PAGE_TITLES[self._current_page]),
            "该页面没有加载成功（通常是模块导入出错）。",
            "现在可以：重启软件重试；也可以直接点页面上的按钮；按 F1 可查看流程图解。",
        )

    # =====================================================================
    # 一键完成全部分析（方案 5.6）
    # =====================================================================
    def run_auto_analysis(self) -> None:
        """自动模式：按推荐参数依次执行 清洗 → 计算 → 出图 → 生成报告。

        实现策略：**只执行已经交付的环节**，遇到尚未开发的环节就停下并如实说明，
        绝不假装跑完。这样按钮在任何阶段都可用、且不会误导用户。
        """
        if not self.app_state.has("raw"):
            QMessageBox.information(
                self, "还没有数据",
                "自动分析需要先有数据。\n\n怎么办：先点【选择文件…】导入自己的数据，"
                "或点【载入示例数据】直接使用软件自带的示例。")
            return

        # 已交付的自动环节按顺序登记在这里；后续阶段把新环节追加进来即可
        auto_steps = [
            ("清洗数据", "run_clean", 1),
            ("计算指标", "run_calc", 2),
            ("绘制图表", "draw_chart", 3),
            ("生成报告", "export_all", 4),
        ]
        executed: List[str] = []
        for label, method_name, page_index in auto_steps:
            page = self.page_widget(page_index)
            handler = getattr(page, method_name, None)
            if not callable(handler):
                self._report_auto_stopped(label, page_index, executed)
                return
            # 绘图与导出会返回 True/False 表示成功与否；清洗/计算则看数据槽。
            succeeded = handler()
            if succeeded is False:
                self.set_status_hint("自动分析已停止：{0}未完成。".format(label))
                return
            if page_index == 1 and not self.app_state.has("clean"):
                self.set_status_hint("自动分析已停止：{0}未完成。".format(label))
                return
            if page_index == 2 and not (self.app_state.has("index_row")
                                        or self.app_state.has("index_summary")):
                self.set_status_hint("自动分析已停止：{0}未完成。".format(label))
                return
            executed.append(label)

        # 五步全部完成：把步骤条第⑤步打勾并给出完成提示
        if len(executed) == len(auto_steps):
            self.mark_export_done(True)
            self.goto_page(4)
            self.set_status_hint("全部分析已完成，成果已导出到指定文件夹。")
            QMessageBox.information(
                self, "全部分析已完成",
                "已按推荐参数完成：{0}。\n\n"
                "成果保存位置可在【报告导出】页看到，点该页的【一键导出】可再次导出到其它位置。".format(
                    " → ".join(executed)))
            return

        # 走到这里说明某个环节没能完成（正常路径会在上面 return）
        # 找到第一个没做完的环节，如实告知用户停在哪里、为什么、现在能做什么
        page_index = len(executed) + 1
        self._report_auto_stopped(auto_steps[len(executed)][0], page_index, executed)

    def _report_auto_stopped(self, next_step: str, page_index: int, executed: List[str]) -> None:
        """自动模式中途停下时，如实告知用户当前进度与后续该怎么做。"""
        done_text = "、".join(executed) if executed else "（无）"
        box = QMessageBox(self)
        box.setWindowTitle("自动分析已停止")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText("已完成：{0}".format(done_text))
        box.setInformativeText(
            "下一步「{0}」没有完成，通常是因为数据不满足该环节的要求"
            "（例如缺少时间列、没有可计算的数值字段）。\n\n"
            "现在可以：到【{1}】页看提示并按建议调整；也可以按 F1 查看每一步的说明。".format(
                next_step, PAGE_TITLES[page_index]))
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.button(QMessageBox.StandardButton.Ok).setText("知道了")
        box.exec()
        self.goto_page(page_index)

    def _act_history(self) -> None:
        """左侧导航 → 历史任务：打开历史任务列表，双击即可恢复现场（阶段 7）。"""
        dialog = None
        code = 1
        try:
            from ui.widgets.history_dialog import HistoryDialog

            dialog = HistoryDialog(self.app_state, self)
            dialog.task_load_requested.connect(self.load_history_task)
            code = dialog.exec()
        except Exception as exc:  # noqa: BLE001 - 历史任务不可用不应影响其它功能
            from utils.errors import report_exception

            report_exception(exc, context="打开历史任务")
            return
        finally:
            # 显式释放对话框：QDialog 虽然会被垃圾回收，但这里持有 Python 引用，
            # 不释放会让每次打开历史任务都留下一份控件树（反复打开会持续占内存）。
            if dialog is not None:
                dialog.deleteLater()
        if code == 2:
            # 用户选择了"重新指定文件"：直接把他送到数据导入页
            self.goto_page(0)
            if self.import_page is not None:
                self.import_page.choose_files()

    def load_history_task(self, task_id: str) -> None:
        """把历史任务恢复进 AppState，并同步界面（步骤条、状态栏、各页刷新）。"""
        from core import task_store

        try:
            record = task_store.load_task_into_state(self.app_state, task_id)
        except task_store.TaskError as exc:
            QMessageBox.warning(self, "无法恢复这个任务", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            from utils.errors import report_exception

            report_exception(exc, context="恢复历史任务")
            return

        self._sync_steps_after_restore()
        self._refresh_status_bar()
        self.set_banner_visible(False)
        # 先切页再写状态栏提示：goto_page 会刷新左侧"第 N 步"提示，
        # 若先写提示就会被它覆盖掉，用户看不到"已恢复历史任务"这句话。
        self.goto_page(self._first_incomplete_page())
        self.set_status_hint("已恢复历史任务「{0}」（{1}）。".format(
            record.name, record.display_time()))

        QMessageBox.information(
            self, "已恢复任务",
            "历史任务「{0}」已载入。\n\n"
            "包含：原始数据 {1:,} 行、清洗后数据 {2:,} 行{3}。\n\n"
            "接下来可以继续分析，或到【报告导出】页重新导出成果。".format(
                record.name, record.raw_rows, record.clean_rows,
                "、已计算指标" if self.app_state.has("index_row")
                or self.app_state.has("index_summary") else ""))

    def _sync_steps_after_restore(self) -> None:
        """恢复任务后按数据槽重算步骤条：有数据的步骤一律打勾。"""
        self._refresh_steps()
        # 第 ⑤ 步（导出报告）只有在本次会话真的导出过才算完成；
        # 恢复任务时若快照里已有图片，说明当时画过图，但导出仍需用户确认。
        if self.app_state.has("figures"):
            self.step_bar.set_done(3, True)

    def _first_incomplete_page(self) -> int:
        """返回"第一个还没做完"的页面序号，让用户接着往下走。"""
        if not self.app_state.has("raw"):
            return 0
        if not self.app_state.has("clean"):
            return 1
        if not (self.app_state.has("index_row") or self.app_state.has("index_summary")):
            return 2
        if not self.app_state.has("figures"):
            return 3
        return 4

    def _act_about(self) -> None:
        """帮助 → 关于。"""
        self._show_help("appendix")

    def _on_empty_action(self, index: int) -> None:
        """空状态主按钮：按方案 5.3 的规定跳转到正确的页面或触发本页动作。"""
        if index == 0:
            # 数据导入页的"选择文件"：走真实导入页；未接入时退回说明性提示
            if self.import_page is not None:
                self.import_page.choose_files()
            else:
                self._act_open_file()
            return
        target = EMPTY_ACTION_TARGET[index]
        self.goto_page(target)

    def _on_empty_secondary(self, index: int, label: str) -> None:
        """空状态次要按钮：目前只有数据导入页的"载入示例数据"。"""
        if index == 0 and "示例" in label:
            self._act_load_demo()
            return
        self._act_run_current()

    def _help_for_page(self, index: int) -> None:
        """页面右上角「?」：打开帮助面板中与该页对应的章节。"""
        self._show_help(page_section(index))

    def _show_help(self, section_key: str) -> None:
        """打开帮助面板（非模态，不阻塞操作）。"""
        show_help(section_key, self)

    def _show_not_ready(self, title: str, what: str, why: str, how: str) -> None:
        """统一提示"这个功能现在用不了"。

        用途：某个页面/模块意外加载失败时的兜底提示（正常路径不会走到这里）。
        弹窗必须回答四件事：这是什么 / 为什么现在用不了 / 现在能做什么。
        绝不出现"开发中/敬请期待"这类把责任推给用户的说法。
        """
        box = QMessageBox(self)
        box.setWindowTitle("暂时用不了 · {0}".format(title))
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("<b>{0}</b>".format(what))
        if why:
            box.setInformativeText("{0}\n\n{1}".format(why, how))
        else:
            box.setInformativeText(how)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.button(QMessageBox.StandardButton.Ok).setText("知道了")
        box.exec()

    # =====================================================================
    # 六、窗口状态记忆
    # =====================================================================
    def _restore_window_state(self) -> None:
        """恢复上次的窗口尺寸与位置（第一次运行时保持默认 1200×800 居中）。"""
        prefs = settings.instance()
        geometry = prefs.load_geometry()
        if geometry is not None and self.restoreGeometry(geometry):
            return
        self._center_on_screen()

    def _center_on_screen(self) -> None:
        """把窗口移到屏幕中央（首次启动的观感要求）。"""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())

    def _safe_start_page(self) -> int:
        """读取上次停留的页面，已完成的步骤优先（让用户回到最靠后的进度）。"""
        last_page = settings.instance().last_page
        return max(0, min(last_page, len(PAGE_TITLES) - 1))

    def has_running_task(self) -> bool:
        """是否有后台任务正在跑（目前只有导入页的解析线程）。"""
        page = self.import_page
        checker = getattr(page, "has_running_task", None)
        return bool(checker()) if callable(checker) else False

    def stop_background_tasks(self) -> bool:
        """停止全部后台任务，返回是否都已安全停下。

        必须在程序退出前调用（实测踩坑，阶段 8 大数据量回归发现）：
            解析线程还在跑时直接退出，进程会以 0xC0000409
            （STATUS_STACK_BUFFER_OVERRUN）异常中止 ——
            界面已经关掉、用户以为正常退出，Windows 却弹"程序已停止工作"。
        """
        stopped = True
        page = self.import_page
        stopper = getattr(page, "stop_background_task", None)
        if callable(stopper):
            stopped = bool(stopper()) and stopped
        return stopped

    def shutdown(self) -> bool:
        """退出前的收尾：停后台任务 → 存窗口状态。返回是否一切正常。"""
        stopped = self.stop_background_tasks()
        try:
            prefs = settings.instance()
            prefs.save_geometry(self.saveGeometry())
            prefs.save_window_state(self.saveState())
            prefs.last_page = self._current_page
        except Exception:
            # 偏好保存失败不应阻止用户关闭软件
            pass
        return stopped

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名约定
        """关闭前：先把后台任务停下来，再保存窗口尺寸、位置与当前页面。

        为什么要先停后台任务：解析线程仍在运行时退出会让进程异常中止
        （见 stop_background_tasks 的说明）。等待期间界面已不可交互，
        因此先询问用户，避免"点了关闭却像卡住"。
        """
        if self.has_running_task():
            answer = QMessageBox.question(
                self, "还在读取数据",
                "正在读取数据文件，现在关闭会中断这次读取（已导入的数据不受影响）。\n\n"
                "要关闭软件吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.shutdown()
        super().closeEvent(event)


# ===========================================================================
# 全局样式表：白色单色调 + 直角界面
# ===========================================================================
# 设计约定（全工程统一，页面内联样式也必须取自这里）：
#   · 底色纯白，不出现任何彩色；层级只靠灰度与 1px 描边拉开；
#   · 所有控件圆角为 0（方案原稿的圆角主题已按要求改为直角）；
#   · 主按钮 = 实心黑，次按钮 = 白底黑边，禁用 = 浅灰不可点；
#   · 文字三级：正文 #1F2329 / 次要 #5A5F66 / 弱化 #8C9096。
# 语义提示（错误、成功、提醒）不再用红绿橙，改为"加粗 + 符号 + 深灰"，
# 保证单色下依然醒目 —— 这是去彩色后最容易丢失的可读性。
# 色板定义在 ui/style.py（单一来源，页面与主窗口共用同一套值）。
from ui.style import (  # noqa: E402  - 放在这里是为了紧邻样式表，便于对照
    BG as COLOR_BG,
    BG_HOVER as COLOR_BG_HOVER,
    BG_SOFT as COLOR_BG_SOFT,
    DISABLED as COLOR_DISABLED,
    LINE as COLOR_LINE,
    LINE_SOFT as COLOR_LINE_SOFT,
    PRIMARY as COLOR_PRIMARY,
    PRIMARY_HOVER as COLOR_PRIMARY_HOVER,
    TEXT as COLOR_TEXT,
    TEXT_SUB as COLOR_TEXT_SUB,
    TEXT_WEAK as COLOR_TEXT_WEAK,
)

_GLOBAL_QSS = """
QMainWindow, QDialog { background: #FFFFFF; }
QWidget { font-size: 13px; color: #1F2329; }

QMenuBar { background: #FFFFFF; border-bottom: 1px solid #D8DADF; }
QMenuBar::item { padding: 6px 12px; background: transparent; }
QMenuBar::item:selected { background: #EFEFF1; color: #1F2329; }
QMenu { background: #FFFFFF; border: 1px solid #D8DADF; padding: 4px; }
QMenu::item { padding: 6px 22px 6px 14px; }
QMenu::item:selected { background: #EFEFF1; color: #1F2329; }

#app_header { background: #FFFFFF; border-bottom: 1px solid #D8DADF; }
#app_title { font-size: 17px; font-weight: bold; color: #1F2329; }
#app_subtitle { font-size: 12px; color: #8C9096; }

/* 顶部提示条：单色下用左侧粗边条 + 加粗文字表示"这是一条提示" */
#demo_banner { background: #F6F7F8; border-bottom: 1px solid #D8DADF;
               border-left: 4px solid #1F2329; }
#demo_banner_text { color: #1F2329; font-size: 13px; }
#banner_close { background: #FFFFFF; border: 1px solid #1F2329; border-radius: 0px;
                color: #1F2329; padding: 0 10px; font-size: 12px; }
#banner_close:hover { background: #1F2329; color: #FFFFFF; }

#step_bar { background: #FFFFFF; border-bottom: 1px solid #D8DADF; }
#step_button { background: #FFFFFF; border: 1px solid #D8DADF; border-radius: 0px;
               color: #5A5F66; padding: 0 14px; font-size: 13px; text-align: center; }
#step_button:hover { border-color: #1F2329; color: #1F2329; }
/* 当前步骤：实心黑 */
#step_button[stepState="2"] { background: #1F2329; border-color: #1F2329; color: #FFFFFF;
                              font-weight: bold; }
/* 已完成：浅灰底 + 深色文字 + ✓ */
#step_button[stepState="1"] { background: #F6F7F8; border-color: #D8DADF; color: #1F2329; }
#step_button[stepState="1"]:hover { border-color: #1F2329; }
/* 当前且已完成：浅灰底 + 2px 黑框，兼顾"已完成"与"你正停在这一步" */
#step_button[stepState="3"] { background: #F6F7F8; border: 2px solid #1F2329;
                              color: #1F2329; font-weight: bold; }
#step_arrow { color: #C4C7CC; font-size: 16px; }
#step_hint { color: #5A5F66; font-size: 12px; }

#nav_panel { background: #FFFFFF; border-right: 1px solid #D8DADF; }
#nav_title { color: #8C9096; font-size: 12px; padding: 2px 4px 6px 4px; }
#nav_button { background: transparent; border: none; border-left: 3px solid transparent;
              border-radius: 0px; text-align: left;
              padding: 0 12px; color: #5A5F66; font-size: 14px; }
#nav_button:hover { background: #F6F7F8; color: #1F2329; }
/* 选中项：左侧黑色粗边条 + 加粗，比整块灰底更清爽 */
#nav_button:checked { background: #F6F7F8; border-left: 3px solid #1F2329;
                      color: #1F2329; font-weight: bold; }
#nav_secondary_button { background: #FFFFFF; border: 1px solid #D8DADF; border-radius: 0px;
                        color: #5A5F66; font-size: 13px; }
#nav_secondary_button:hover { border-color: #1F2329; color: #1F2329; }
#nav_version { color: #C4C7CC; font-size: 11px; padding-left: 4px; }

#page_stack { background: #FFFFFF; }
/* 页面滚动区：透明底，不引入额外边框，视觉上与页面融为一体 */
#page_scroll { background: #FFFFFF; border: none; }
#page_title { font-size: 16px; font-weight: bold; color: #1F2329; }
#page_subtitle { color: #8C9096; font-size: 12px; }

#empty_state { background: #FFFFFF; border: 1px dashed #C4C7CC; border-radius: 0px; }
#empty_icon { font-size: 34px; }
/* 说明文字：宽度与高度由 _WrappingLabel 自己算，这里只管字体与颜色 */
#empty_desc { color: #5A5F66; font-size: 14px; }

#primary_button { background: #1F2329; border: 1px solid #1F2329; border-radius: 0px;
                  color: #FFFFFF; padding: 0 16px; font-size: 14px; }
#primary_button:hover { background: #000000; border-color: #000000; }
#primary_button:disabled { background: #C4C7CC; border-color: #C4C7CC; color: #FFFFFF; }
#secondary_button { background: #FFFFFF; border: 1px solid #1F2329; border-radius: 0px;
                    color: #1F2329; padding: 0 16px; font-size: 14px; }
#secondary_button:hover { background: #EFEFF1; }
#secondary_button:disabled { border-color: #C4C7CC; color: #C4C7CC; }

#help_button { background: #FFFFFF; border: 1px solid #1F2329; border-radius: 0px;
               color: #1F2329; font-size: 14px; font-weight: bold; }
#help_button:hover { background: #1F2329; color: #FFFFFF; }

#help_title { font-size: 15px; font-weight: bold; color: #1F2329; }
#help_footer { color: #8C9096; font-size: 12px; }
#help_section_list { background: #FFFFFF; border: 1px solid #D8DADF; border-radius: 0px;
                     font-size: 13px; outline: none; }
#help_section_list::item { padding: 8px 10px; }
#help_section_list::item:selected { background: #F6F7F8; color: #1F2329; font-weight: bold; }
#help_browser { background: #FFFFFF; border: 1px solid #D8DADF; border-radius: 0px; padding: 8px; }

QStatusBar { background: #FFFFFF; border-top: 1px solid #D8DADF; }
#status_hint { color: #5A5F66; font-size: 12px; padding-left: 6px; }
#status_detail { color: #8C9096; font-size: 12px; padding-right: 8px; }

QTableView { background: #FFFFFF; border: 1px solid #D8DADF; border-radius: 0px;
             gridline-color: #EDEEF0; selection-background-color: #EFEFF1;
             selection-color: #1F2329; }
QHeaderView::section { background: #F6F7F8; border: none; border-right: 1px solid #D8DADF;
                       border-bottom: 1px solid #D8DADF; padding: 6px 8px;
                       color: #1F2329; font-weight: bold; }
QProgressBar { border: 1px solid #D8DADF; border-radius: 0px; background: #FFFFFF;
               text-align: center; height: 20px; }
QProgressBar::chunk { background: #1F2329; border-radius: 0px; }
/* 输入类控件统一描边与内边距。
   已知无害告警（Qt 自身缺陷，不要试图"修好"它）：
       给 QSpinBox 做样式后，Qt 的 QStyleSheetStyle 在绘制微调按钮箭头时
       save/restore 没配平，控制台会打印
           QPainter::end: Painter ended with N saved states
       已实测确认：同样的规则只加在 QComboBox / QLineEdit 上完全没有告警，
       只有 QSpinBox 会；换成 objectName 选择器、拆开 padding 都无效。
       影响仅限于 stderr 多一行提示，界面与功能不受影响，打包后用户也看不到。
       （详见 tests/test_ui_behaviour.py::TestQtWarnings） */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: #FFFFFF; border: 1px solid #D8DADF;
               border-radius: 0px; padding: 4px 8px; min-height: 26px; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #1F2329; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
               background: #F6F7F8; color: #8C9096; }
/* 通用按钮兜底：给所有按钮一个可见的边框。
   为什么必须写这一条（实测问题）：弹窗里的按钮是 QMessageBox.addButton() /
   setStandardButtons() 创建的，没有 objectName，选不中 #primary_button /
   #secondary_button 这两条规则；而 Qt 默认按钮样式在部分 Windows 主题下
   是无边框的平按钮，看起来像一行纯文字，用户认不出那是按钮。
   这里统一给「白底 + 1px 描边 + 直角」，并按主/次角色区分。 */
QPushButton, QDialogButtonBox QPushButton {
    background: #FFFFFF; border: 1px solid #1F2329; border-radius: 0px;
    color: #1F2329; padding: 0 14px; min-height: 26px; min-width: 72px;
}
QPushButton:hover, QDialogButtonBox QPushButton:hover { background: #EFEFF1; }
QPushButton:pressed, QDialogButtonBox QPushButton:pressed { background: #E3E4E6; }
QPushButton:disabled, QDialogButtonBox QPushButton:disabled {
    background: #FFFFFF; border-color: #C4C7CC; color: #C4C7CC;
}
QPushButton:focus, QDialogButtonBox QPushButton:focus { border: 2px solid #1F2329; }
/* 弹窗里的默认按钮（回车触发的那个）用实心黑，一眼看出主次 */
QMessageBox QPushButton:default, QDialogButtonBox QPushButton:default {
    background: #1F2329; border-color: #1F2329; color: #FFFFFF; font-weight: bold;
}
QMessageBox QPushButton:default:hover { background: #000000; }
QMessageBox { background: #FFFFFF; }
QMessageBox QLabel { color: #1F2329; }
QPushButton { border-radius: 0px; }
QGroupBox { background: #FFFFFF; border: 1px solid #D8DADF; border-radius: 0px;
            margin-top: 14px; padding: 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; color: #5A5F66; }
QCheckBox, QRadioButton { color: #1F2329; spacing: 6px; }
QScrollBar:vertical { background: #FFFFFF; width: 12px; margin: 0px; border: none; }
QScrollBar::handle:vertical { background: #C4C7CC; border-radius: 0px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: #8C9096; }
QScrollBar:horizontal { background: #FFFFFF; height: 12px; margin: 0px; border: none; }
QScrollBar::handle:horizontal { background: #C4C7CC; border-radius: 0px; min-width: 28px; }
QScrollBar::handle:horizontal:hover { background: #8C9096; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0px; height: 0px; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: #FFFFFF; }
QSplitter::handle { background: #D8DADF; }
QSplitter::handle:hover { background: #1F2329; }
QToolTip { background: #1F2329; color: #FFFFFF; border: 1px solid #1F2329;
           border-radius: 0px; padding: 4px 6px; }
"""


def build_app_icon() -> QIcon:
    """生成窗口图标：黑底白色「E」（不依赖外部图片，也不依赖字体）。

    为什么用线段画 E 而不是 drawText("E")（实测踩坑）：
        个别环境（精简系统、Qt 找不到字体目录时）QFontDatabase.families() 返回空，
        drawText 什么也画不出来 —— 实测本机开发环境就是 0 个字体，
        drawText 版本渲染出来只有一块空方块。图标属于"门面"，
        不能依赖字体是否装好，因此这里用三条横杠 + 一条竖杠拼出 E。
    风格与界面一致：单色、直角。生成多档尺寸，
    保证任务栏、标题栏、Alt+Tab 切换器里都清晰。
    公开函数（供 main.py 设置应用级图标）。
    """
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pixmap)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            # 整块黑色方形底（直角）
            painter.setBrush(QColor(COLOR_PRIMARY))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(0, 0, size, size)

            # 白色「E」：一条竖杠 + 上中下三条横杠（全部直角）
            painter.setBrush(QColor("#FFFFFF"))
            left = round(size * 0.30)
            right = round(size * 0.70)
            top = round(size * 0.26)
            bottom = round(size * 0.74)
            stem = max(1, round(size * 0.10))          # 竖杠粗细
            bar = max(1, round(size * 0.085))          # 横杠粗细
            middle = round((top + bottom) / 2)         # 中间那横的位置

            painter.drawRect(left, top, stem, bottom - top + 1)      # 竖杠
            painter.drawRect(left, top, right - left + 1, bar)       # 上横
            painter.drawRect(left, middle - bar // 2, round((right - left) * 0.78), bar)  # 中横（略短）
            painter.drawRect(left, bottom - bar + 1, right - left + 1, bar)  # 下横
        finally:
            painter.end()
        icon.addPixmap(pixmap)
    return icon
