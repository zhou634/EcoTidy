# -*- coding: utf-8 -*-
"""
帮助与术语速查面板（开发方案 第五章 5.7，阶段 1 交付）

解决什么问题：
    用户遇到不懂的术语（香农指数、重要值、Pielou 均匀度…）时，
    如果必须去翻外部文档，就违反了"三分钟免文档"的硬性要求。
    因此帮助必须**就地可达**：F1 随时唤起，页面右上角的"?"直接跳到该页对应章节。

设计要点：
    1. 非模态对话框（show 而不是 exec）：用户可以一边看图一边查术语，不会被打断。
    2. 显示前先做 isMinimized 检查并 activateWindow，避免第二次按 F1 时
       窗口"看起来没反应"（实际是藏在别的窗口后面）。
    3. 尺寸经 core/settings.py 记忆，用户调大过一次就不用再调。
    4. 文案全部来自 ui/widgets/help_content.py，本文件只管界面与跳转逻辑。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core import settings
from ui.widgets import help_content
from utils.i18n import tr

# 帮助内容区的排版样式（只影响 QTextBrowser 内部，不污染全局样式表）
_CONTENT_QSS = """
body { font-size: 13px; line-height: 1.7; color: #1F2329; }
h3 { color: #1F2329; font-size: 15px; margin: 4px 0 10px 0; }
h4 { color: #1F2329; font-size: 13px; margin: 14px 0 6px 0; }
p  { margin: 6px 0; }
li { margin: 4px 0; }
code { background: #F6F7F8; padding: 1px 4px; }
table { border-collapse: collapse; margin: 8px 0; }
th { background: #F6F7F8; color: #1F2329; text-align: left; padding: 6px 8px; border: 1px solid #D8DADF; }
td { padding: 5px 8px; border: 1px solid #D8DADF; }
.tip { color: #1F2329; background: #F6F7F8; border-left: 4px solid #1F2329; padding: 8px 10px; }
/* 「必需」小标签：界面是纯灰阶，用深浅区分而不是用颜色 */
.req { background: #1F2329; color: #FFFFFF; font-size: 11px; font-weight: bold;
       padding: 1px 5px; margin-left: 4px; }
"""


class HelpPanel(QDialog):
    """帮助与术语速查面板。

    对外接口：
        show_section(section_key)  打开并跳到指定章节
        filter_terms(keyword)      在术语章节里按关键词筛选
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("帮助与术语速查")
        self.setObjectName("help_panel")
        # 非模态：用户可一边操作主界面一边查术语
        self.setModal(False)
        self.setMinimumSize(720, 480)
        self.resize(880, 620)

        # 恢复上次的面板尺寸（第一次使用时没有记录，用上面的默认值）
        saved = settings.instance().load_help_geometry()
        if saved is not None:
            self.restoreGeometry(saved)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        # ---- 顶部：标题 + 关闭按钮 ----
        header = QHBoxLayout()
        title_label = QLabel("帮助与术语速查", self)
        title_label.setObjectName("help_title")
        header.addWidget(title_label)
        header.addStretch(1)
        close_button = QPushButton("关闭（Esc）", self)
        close_button.setObjectName("secondary_button")
        close_button.setMinimumSize(110, 34)
        close_button.clicked.connect(self.close)
        header.addWidget(close_button)
        outer.addLayout(header)

        # ---- 主体：左目录 + 右内容 ----
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        left = QWidget(splitter)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        self._search = QLineEdit(left)
        self._search.setPlaceholderText("搜索术语关键词…")
        self._search.setClearButtonEnabled(True)
        self._search.setMinimumHeight(30)
        self._search.textChanged.connect(self._on_search_changed)
        left_layout.addWidget(self._search)

        self._section_list = QListWidget(left)
        self._section_list.setObjectName("help_section_list")
        for section_key, section_title in help_content.SECTIONS:
            item = QListWidgetItem(section_title, self._section_list)
            # 把章节键名挂在 item 上，避免用中文标题反查键名（改文案就失效）
            item.setData(Qt.ItemDataRole.UserRole, section_key)
            # 必须显式 addItem：只构造 QListWidgetItem 不会进入列表，
            # 漏掉这一行会让左侧目录整片空白（用户按 F1 后无章节可选）。
            self._section_list.addItem(item)
        self._section_list.currentItemChanged.connect(self._on_section_changed)
        left_layout.addWidget(self._section_list, 1)
        splitter.addWidget(left)

        self._browser = QTextBrowser(splitter)
        self._browser.setObjectName("help_browser")
        self._browser.setOpenExternalLinks(False)   # 离线软件：不允许跳出浏览器
        self._browser.document().setDefaultStyleSheet(_CONTENT_QSS)
        splitter.addWidget(self._browser)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([200, 660])
        outer.addWidget(splitter, 1)

        # ---- 底部提示 ----
        footer = QLabel(tr("提示：每个页面右上角的「?」可以直接打开该页对应的帮助章节。"), self)
        footer.setObjectName("help_footer")
        outer.addWidget(footer)

        # Esc 关闭（QDialog 默认行为，这里显式绑定一次，保证焦点在列表里也生效）
        # 注意：必须用 Qt.Key_Escape。PySide6 中并不存在 Qt.Key.Key_Escape 这种写法，
        #       写成后者会在构造面板时直接抛 AttributeError，F1 一按就崩。
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.close)

        # 默认显示第一章
        self.show_section("quickstart")

    # ---------------------------------------------------------------------
    # 对外接口
    # ---------------------------------------------------------------------
    def show_section(self, section_key: str) -> None:
        """切换到指定章节（键名见 help_content.SECTIONS）。

        为什么写完列表选中行还要显式刷新内容：
            Qt 只在"选中行真的发生变化"时才发 currentItemChanged 信号。
            构造时已把选中行设为第 1 章，用户再跳回第 1 章（或首次显示时）
            不会触发信号，右侧就会是一片空白 —— 因此这里必须主动刷新一次内容区。
        """
        if section_key not in help_content.CONTENT:
            section_key = "quickstart"
        for row in range(self._section_list.count()):
            item = self._section_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == section_key:
                self._section_list.setCurrentRow(row)
                # 不依赖信号，直接刷新，保证任何调用路径下内容区都正确
                self._on_section_changed(item, None)
                break

    def filter_terms(self, keyword: str) -> None:
        """在术语章节内按关键词筛选（顶部搜索框输入时自动调用）。"""
        self._search.setText(keyword or "")

    # ---------------------------------------------------------------------
    # 内部槽函数
    # ---------------------------------------------------------------------
    def _on_section_changed(self, current: Optional[QListWidgetItem], _previous: Optional[QListWidgetItem]) -> None:
        """左侧目录切换：把右侧内容换成对应章节。"""
        if current is None:
            return
        section_key = current.data(Qt.ItemDataRole.UserRole) or "quickstart"
        self._browser.setHtml(help_content.section(section_key))
        self._browser.verticalScrollBar().setValue(0)

    def _on_search_changed(self, text: str) -> None:
        """把搜索框的内容透传给内容区（QTextBrowser 自带 find 高亮）。"""
        keyword = (text or "").strip()
        if not keyword:
            return
        # 先跳到术语速查章节，再在其中定位关键词，符合"搜术语"的使用预期
        self.show_section("terms")
        self._browser.find(keyword)

    # ---------------------------------------------------------------------
    # 关闭时保存尺寸
    # ---------------------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名约定
        """关闭前记住面板尺寸，下次打开保持用户调好的大小。"""
        try:
            settings.instance().save_help_geometry(self.saveGeometry())
        except Exception:
            # 保存偏好失败不应阻止面板关闭
            pass
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# 全局唯一实例与便捷入口
# ---------------------------------------------------------------------------
# 说明：本模块持有唯一的帮助面板实例，因此引入一个模块级变量。
#       这**不违反**"禁止模块级全局变量传递业务数据"的纪律：
#       该变量是界面资源句柄，不承载任何业务数据，且业务数据依旧只经 AppState 传递。
#       这样做的好处是 5 个页面与菜单栏共用同一个面板，尺寸与位置保持一致。
_panel: Optional[HelpPanel] = None


def get_panel(parent: Optional[QWidget] = None) -> HelpPanel:
    """获取（必要时创建）帮助面板实例。"""
    global _panel
    if _panel is None:
        _panel = HelpPanel(parent)
    return _panel


def show_help(section_key: str = "quickstart", parent: Optional[QWidget] = None) -> HelpPanel:
    """打开帮助面板并跳到指定章节（F1 与各页「?」按钮的统一入口）。

    注意 show() 之前的三个动作：
        showNormal()    从最小化状态恢复
        raise_()        提到同级窗口最前
        activateWindow() 取得键盘焦点
    少了任何一步，用户第二次按 F1 都可能"看不到反应"。
    """
    panel = get_panel(parent)
    panel.show_section(section_key)
    panel.showNormal()
    panel.raise_()
    panel.activateWindow()
    return panel
