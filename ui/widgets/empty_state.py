# -*- coding: utf-8 -*-
"""
空状态占位组件（开发方案 第五章 5.3，阶段 1 交付）

解决什么问题：
    页面缺少前置数据时如果只显示一片空白，用户完全不知道下一步该做什么，
    只能到处乱点甚至直接关掉软件。本组件强制回答两个问题：
        "现在为什么没有内容？" + "我该点哪里？"

结构固定为三件套（方案原文：图标 + 一句话说明 + 一个行动按钮）：
    icon_label   图标（占位图形）
    desc_label   一句话说明
    action_button 行动按钮（点击后由宿主页面跳转到正确的页面）
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from utils.i18n import tr


class _WrappingLabel(QLabel):
    """会按换行结果自动长高的标签。

    为什么必须自定义（实测踩坑）：
        空状态说明是长句，窄窗口下会折成 2~4 行。QLabel 虽然开启 wordWrap，
        Qt 的布局却只按"一行的高度"（实测 18px）分配空间，文字直接溢出到
        标签框外 —— 截图中能看到折行文字压到按钮上甚至被裁掉。

    做法（两步缺一不可）：
        1. sizePolicy 打开 heightForWidth，并实现 heightForWidth()；
        2. **在宽度真正确定后把 minimumHeight 钉死**为换行所需高度。
           只做第 1 步不够：空间紧张时布局会退回 minimumSizeHint()，
           而那时还不知道宽度，只能按一行算，于是又被压扁（实测复现）。
    """

    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setWordWrap(True)
        self._sync_min_height()

    def _wrapped_height(self, width: int) -> int:
        """给定宽度下，文字换行后需要的高度。"""
        available = max(60, int(width) - 4)
        flags = int(self.alignment()) | 0x1000      # Qt.TextWordWrap
        rect = self.fontMetrics().boundingRect(0, 0, available, 10000, flags, self.text())
        return max(self.fontMetrics().height(), rect.height()) + 4

    def _sync_min_height(self) -> None:
        """按当前宽度重新钉住最小高度（宽度未知时用一个稳妥的估计值）。"""
        width = self.width()
        if width <= 0:
            width = min(self.maximumWidth(), 420)
        self.setMinimumHeight(self._wrapped_height(width))

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt 命名约定
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt 命名约定
        return self._wrapped_height(width)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名约定
        width = self.width() if self.width() > 0 else 420
        return QSize(width, self._wrapped_height(width))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt 命名约定
        # 宽度给一个可读下限（280），否则布局会把文字挤成 120px 宽的窄条，
        # 虽然不再溢出，但每行只有两三个字，非常难看（实测截图确认）。
        return QSize(min(280, max(120, self.maximumWidth())), self.minimumHeight())

    def setText(self, text: str) -> None:  # noqa: N802 - Qt 命名约定
        super().setText(text)
        self._sync_min_height()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名约定
        super().resizeEvent(event)
        self._sync_min_height()


class EmptyState(QFrame):
    """空状态占位控件。

    典型用法（宿主页面）：
        self.empty = EmptyState(parent=self)
        self.empty.set_description("请先导入数据。")
        self.empty.set_action("前往【数据导入】")
        self.empty.action_clicked.connect(self._goto_import)

    信号：
        action_clicked()  用户点了行动按钮
        secondary_clicked() 用户点了次要按钮（可选）
    """

    action_clicked = Signal()
    secondary_clicked = Signal()

    #: 文案的"中文原文"（语言切换时据此重译）
    _desc_key: str = ""
    _action_key: str = ""
    _secondary_key: str = ""
    _tooltip_key: str = ""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("empty_state")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        # 让空状态在预览区域里尽量占满，视觉上不会缩成一小块
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(240)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(1)

        # ---- 图标（黑白线稿，不用彩色 Emoji）----
        self._icon_label = QLabel("", self)
        self._icon_label.setObjectName("empty_icon")
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setFixedHeight(56)
        self._icon_size = 48
        outer.addWidget(self._icon_label)

        # ---- 说明文字 ----
        # 用 _WrappingLabel：普通 QLabel 在窄窗口下只按一行高度分配空间，
        # 折行后的文字会溢出框外（实测：需要 46px，只给了 14px）。
        self._desc_label = _WrappingLabel("", self)
        self._desc_label.setObjectName("empty_desc")
        self._desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._desc_label.setMinimumWidth(0)
        self._desc_label.setMaximumWidth(560)
        outer.addSpacing(12)
        outer.addWidget(self._desc_label, 0, Qt.AlignmentFlag.AlignHCenter)

        # ---- 行动按钮（至少一个，可再加一个次要按钮）----
        button_row = QHBoxLayout()
        button_row.setSpacing(12)
        button_row.addStretch(1)

        self._action_button = QPushButton("", self)
        self._action_button.setObjectName("primary_button")
        self._action_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._action_button.setMinimumSize(120, 34)
        self._action_button.clicked.connect(self.action_clicked.emit)
        button_row.addWidget(self._action_button)

        self._secondary_button = QPushButton("", self)
        self._secondary_button.setObjectName("secondary_button")
        self._secondary_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._secondary_button.setMinimumSize(120, 34)
        self._secondary_button.clicked.connect(self.secondary_clicked.emit)
        self._secondary_button.hide()   # 默认不显示，由宿主页面决定是否启用
        button_row.addWidget(self._secondary_button)

        button_row.addStretch(1)
        outer.addSpacing(18)
        outer.addLayout(button_row)
        outer.addStretch(1)

    # ---------------------------------------------------------------------
    # 对外接口
    # ---------------------------------------------------------------------
    def set_icon(self, text: str) -> None:
        """设置图标。

        兼容两种用法：
            · 传 ALL_ICONS 里的类型名（"folder"/"broom"…）→ 画黑白线稿；
            · 传其它任意短字符串 → 按文字显示（保留旧行为，便于临时占位）。
        这样旧代码里的 Emoji 调用不会报错，但推荐改用类型名。
        """
        from ui.widgets.icons import ALL_ICONS, icon_pixmap

        key = (text or "").strip()
        self._icon_key = key
        if key in ALL_ICONS:
            self._icon_label.setPixmap(icon_pixmap(key, self._icon_size))
            self._icon_label.setText("")
        else:
            self._icon_label.setPixmap(QPixmap())     # 清掉上次的图形
            self._icon_label.setText(key)

    def set_description(self, text: str) -> None:
        """设置说明文字。方案对各页文案有固定要求，见第五章 5.3 表格。"""
        # 记住中文原文，切换语言时据此重译（英文再译回中文是做不到的）
        self._desc_key = text or ""
        self._desc_label.setText(tr(self._desc_key))

    def set_action(self, text: str) -> None:
        """设置行动按钮文字，例如"前往【数据导入】"。"""
        self._action_key = text or ""
        self._action_button.setText(tr(self._action_key))
        self._action_button.setVisible(bool(text))

    def set_secondary_action(self, text: str) -> None:
        """设置次要按钮文字（如数据导入页的"选择文件 ／ 载入示例数据"）。传空串则隐藏。"""
        self._secondary_key = text or ""
        self._secondary_button.setText(tr(self._secondary_key))
        self._secondary_button.setVisible(bool(text))

    def set_action_enabled(self, enabled: bool, reason: str = "") -> None:
        """置灰 / 启用行动按钮，并在按钮上说明原因（方案第二章"按钮状态"要求）。"""
        self._action_button.setEnabled(bool(enabled))
        self._tooltip_key = reason if not enabled else ""
        if not enabled and reason:
            self._action_button.setToolTip(tr(reason))
        else:
            self._action_button.setToolTip("")

    def retranslate(self) -> None:
        """按当前语言重刷说明与按钮文字（切换语言时由主窗口调用）。

        注意：挂载真实页面后，这个占位控件会被 Qt 销毁，但 Python 侧
        可能仍留有引用。此时访问其子控件会抛 RuntimeError
        （"Internal C++ object already deleted"）—— 必须吞掉，
        否则切换语言时整个界面会崩（实测踩坑）。
        """
        try:
            self._desc_label.setText(tr(self._desc_key))
            self._action_button.setText(tr(self._action_key))
            self._secondary_button.setText(tr(self._secondary_key))
            if self._tooltip_key:
                self._action_button.setToolTip(tr(self._tooltip_key))
        except RuntimeError:
            # 控件已被销毁：说明这一页已经装上了真实页面，占位控件不再需要重译
            return

    def description(self) -> str:
        """返回当前说明文字（供测试与调试使用）。"""
        return self._desc_label.text()

    def action_text(self) -> str:
        """返回当前行动按钮文字（供测试与调试使用）。"""
        return self._action_button.text()
