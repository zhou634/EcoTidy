# -*- coding: utf-8 -*-
"""
顶部流程步骤条（开发方案 第五章 5.2，阶段 1 交付）

解决什么问题：
    用户任何时候都必须能一眼看出"我在哪一步、还差什么"。
    没有步骤条时，新手在 5 个页面之间点来点去，完全不知道下一步该做什么。

三种状态（方案原文）：
    已完成   ✓ 绿色   —— 该步骤的产出数据已经存在
    当前     高亮主色 —— 用户此刻停留的页面
    未开始   灰色     —— 还没做到这一步

两个关键设计：
    1. "当前"状态由 current_index 唯一决定（不额外存储），
       避免"数据状态"与"页面位置"两个来源打架。
    2. 用户可以直接点任意一步跳转（不发信号给兄弟页面，只发信号给主窗口）。
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from utils.i18n import tr

# 三个状态常量（对外公开，方便主窗口与测试引用，避免散落魔法数字）
STATE_NOT_STARTED = 0   # 未开始：灰色
STATE_DONE = 1          # 已完成：绿色 + 打勾（且不是当前步）
STATE_CURRENT = 2       # 当前：高亮主色（且未完成）
"""
"当前且已完成"（STATE_DONE_CURRENT）的存在理由（实测踩坑）：
    用户导入数据后通常会停留在【数据导入】页，此时第 ① 步既是"当前"又是"已完成"。
    如果按"当前优先"渲染，按钮就只剩高亮、丢掉 ✓ —— 用户会以为数据没导入成功。
    因此增加第 4 种状态：既有绿勾说明已完成，又用蓝色描边表示当前所在步骤。
"""
STATE_DONE_CURRENT = 3  # 当前且已完成：绿色打勾 + 蓝色描边

# 固定 5 步（开发方案第二章：5 个页面顺序固定）
STEP_TITLES: List[str] = [
    "① 导入数据",
    "② 清洗数据",
    "③ 计算指标",
    "④ 绘制图表",
    "⑤ 导出报告",
]

# 各步的短名（语言菜单与状态栏用；与 STEP_TITLES 一一对应）
STEP_SHORT_NAMES: List[str] = ["导入", "清洗", "计算", "绘图", "导出"]

# 每一步的悬浮说明：一句话说清"这一步产出什么"
STEP_TOOLTIPS: List[str] = [
    "导入 csv / xlsx / txt 文件。产出：标准化数据表。",
    "修正异常值与缺失值。产出：可计算的干净数据。",
    "计算群落结构、多样性、时序指标。产出：指标表。",
    "选图表并导出图片。产出：PNG / SVG 文件。",
    "一次性导出全部成果。产出：成果文件夹。",
]


class StepBar(QFrame):
    """顶部流程步骤条控件。

    对外接口：
        set_current(index)   设置当前步（高亮），页面切换时由主窗口调用
        set_done(index, ok)  设置某一步是否已完成（数据状态变化时由主窗口调用）
        is_done(index)       查询某一步是否已完成
        done_states()        返回 5 个步骤的完成状态，供恢复历史任务时使用
        set_hint(text)       设置右侧提示文字（例如"下一步：导入数据"）
    信号：
        step_clicked(int)    用户点击了第 index 步（0~4）
    """

    step_clicked = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("step_bar")
        self.setFrameShape(QFrame.Shape.NoFrame)
        # 行高要求（方案第二章：关键操作按钮不小于 100×34，表格行高不小于 26）
        self.setMinimumHeight(56)

        self._buttons: List[QPushButton] = []
        self._arrows: List[QLabel] = []
        self._done: List[bool] = [False] * len(STEP_TITLES)
        self._current: int = 0

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(6)

        for index, title in enumerate(STEP_TITLES):
            button = QPushButton(tr(title), self)
            button.setObjectName("step_button")
            button.setCheckable(False)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMinimumHeight(34)
            button.setMinimumWidth(112)
            button.setToolTip(tr(STEP_TOOLTIPS[index]))
            # 用默认参数绑定 index，避免闭包late binding 让所有按钮都跳到最后一步
            button.clicked.connect(lambda _checked=False, i=index: self._on_clicked(i))
            layout.addWidget(button)
            self._buttons.append(button)

            if index < len(STEP_TITLES) - 1:
                arrow = QLabel("›", self)
                arrow.setObjectName("step_arrow")
                arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
                arrow.setFixedWidth(14)
                layout.addWidget(arrow)
                self._arrows.append(arrow)

        layout.addSpacing(10)
        self._hint = QLabel("", self)
        self._hint.setObjectName("step_hint")
        self._hint.setWordWrap(False)
        layout.addWidget(self._hint)
        layout.addStretch(1)

        self._refresh()

    # ---------------------------------------------------------------------
    # 对外接口
    # ---------------------------------------------------------------------
    def set_current(self, index: int) -> None:
        """设置当前步（0~4）。越界值会被忽略，不会抛异常。"""
        if not 0 <= index < len(STEP_TITLES):
            return
        self._current = index
        self._refresh()

    def retranslate(self) -> None:
        """按当前语言刷新 5 个步骤按钮的文字与提示（切换语言时调用）。"""
        for index, button in enumerate(self._buttons):
            button.setText(tr(STEP_TITLES[index]))
            button.setToolTip(tr(STEP_TOOLTIPS[index]))
        self._refresh()

    def current_index(self) -> int:
        """返回当前步序号。"""
        return self._current

    def set_done(self, index: int, done: bool = True) -> None:
        """设置某一步是否已完成（由主窗口根据数据状态调用）。"""
        if not 0 <= index < len(self._done):
            return
        if self._done[index] != bool(done):
            self._done[index] = bool(done)
            self._refresh()

    def is_done(self, index: int) -> bool:
        """查询某一步是否已完成。"""
        if not 0 <= index < len(self._done):
            return False
        return self._done[index]

    def done_states(self) -> List[bool]:
        """返回 5 个步骤的完成状态副本（恢复历史任务时用）。"""
        return list(self._done)

    def set_hint(self, text: str) -> None:
        """设置右侧提示文字，例如"下一步：导入数据"或"已全部完成"。"""
        self._hint.setText(text or "")

    # ---------------------------------------------------------------------
    # 内部实现
    # ---------------------------------------------------------------------
    def _on_clicked(self, index: int) -> None:
        """按钮点击：先把自己设为当前步（即时反馈），再通知主窗口切换页面。"""
        self.set_current(index)
        self.step_clicked.emit(index)

    def _refresh(self) -> None:
        """按 done/current 两个维度重绘所有按钮的样式类与文字。

        判定顺序：先看"是否已完成"，再看"是否当前"，两者可叠加（见 STATE_DONE_CURRENT）。
        文字上的 ✓ 只由"已完成"决定 —— 完成状态必须永远可见，不能被"当前"高亮吃掉。
        """
        for index, button in enumerate(self._buttons):
            done = self._done[index]
            current = index == self._current
            if done and current:
                state = STATE_DONE_CURRENT
            elif done:
                state = STATE_DONE
            elif current:
                state = STATE_CURRENT
            else:
                state = STATE_NOT_STARTED

            # 样式通过 Qt 属性选择器生效，具体配色写在主窗口的全局样式表里
            button.setProperty("stepState", state)
            # 已完成打勾；未完成显示序号。
            # 注意：这里必须走 tr()，否则 _refresh()（每次切页都会调用）
            # 会把手上的译文又覆盖回中文常量（实测踩坑）。
            title = tr(STEP_TITLES[index])
            if done:
                # 去掉开头的序号符号（①②…），换成 ✓
                button.setText("✓ " + title[1:].strip())
            else:
                button.setText(title)

            # 四态文字说明，鼠标停一下就知道这一步现在是什么情况
            tip = tr(STEP_TOOLTIPS[index])
            if done and current:
                button.setToolTip(tip + tr("\n\n· 已完成，当前停在这一步"))
            elif done:
                button.setToolTip(tip + tr("\n\n· 已完成，点击可回看"))
            elif current:
                button.setToolTip(tip + tr("\n\n· 当前所在步骤"))
            else:
                button.setToolTip(tip + tr("\n\n· 尚未开始，点击可直接前往"))

            # 改了动态属性后必须重新应用样式表，否则颜色不刷新。
            # 注意：控件尚未 polish（例如窗口还没显示）时 style() 可能返回 None，
            #       必须判空，否则会在构造阶段直接抛 AttributeError。
            style = button.style()
            if style is not None:
                style.unpolish(button)
                style.polish(button)
