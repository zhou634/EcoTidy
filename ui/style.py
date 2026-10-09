# -*- coding: utf-8 -*-
"""
界面风格常量（白色单色调 + 直角）

集中放置配色与内联样式，避免各页面各写一套颜色字符串
（改风格时只改这一处，不会再出现"漏改某个页面"的情况）。

设计约定：
    · 底色纯白，界面不出现彩色；层级靠灰度与 1px 描边拉开；
    · 所有圆角为 0（直角界面）；
    · 语义提示不用红绿橙，改用「符号 + 加粗 + 灰度深浅」表达：
          normal  正文色，普通说明
          info    深灰，进行中
          success 最深 + ✓，已完成
          warning 最深 + ⚠，需要留意
          error   最深 + ✕，出错了
      这是去彩色后最关键的一步 —— 否则"出错了"和"一切正常"长得一模一样。
"""

from __future__ import annotations

from typing import Optional


def ask_modal(box, action_button_text: str) -> bool:
    """弹出模态对话框，返回"用户是否点了那个自定义操作按钮"。

    为什么需要这个函数（实测 bug，用户报了"点打开输出文件夹没反应"）：
        常见写法是
            ok = box.addButton("打开输出文件夹", …)
            box.exec()
            if box.clickedButton() is ok:      # ← 这里会失败
        模态框返回后，其按钮的 Python 包装对象往往已经失效，
        `clickedButton() is ok` 恒为 False —— 界面看着正常，功能静默失效。

    做法：exec() 之后立刻取 clickedButton()（此时仍有效），
          用按钮**文字**比较，并兼容拿不到按钮对象的情况。
    """
    box.exec()
    clicked = box.clickedButton()
    if clicked is None:
        return False
    try:
        return clicked.text() == action_button_text
    except RuntimeError:
        # 对象已销毁：退化为"对比内部指针"，能比出来更好，比不出就当没点
        return False


def ask_modal_result(box) -> Optional[str]:
    """弹出模态对话框，返回被点击按钮的文字（拿不到时返回 None）。"""
    box.exec()
    clicked = box.clickedButton()
    if clicked is None:
        return None
    try:
        return clicked.text()
    except RuntimeError:
        return None

from typing import Dict

# ---------------------------------------------------------------------------
# 一、色板
# ---------------------------------------------------------------------------
TEXT = "#1F2329"          # 正文
TEXT_SUB = "#5A5F66"      # 次要文字
TEXT_WEAK = "#8C9096"     # 弱化文字
LINE = "#D8DADF"          # 常规描边
LINE_SOFT = "#EDEEF0"     # 更浅的分隔线
BG = "#FFFFFF"            # 纯白底
BG_SOFT = "#F6F7F8"       # 浅灰底（表头、选中项）
BG_HOVER = "#EFEFF1"      # 悬浮底
PRIMARY = "#1F2329"       # 主色（实心黑）
PRIMARY_HOVER = "#000000"
DISABLED = "#C4C7CC"

# 表格高亮（清洗时被修改过的单元格）：单色下用浅灰底 + 左侧深色边线
HIGHLIGHT_BG = "#EDEEF0"

# ---------------------------------------------------------------------------
# 二、语义提示样式
# ---------------------------------------------------------------------------
# 每个状态给 (颜色, 是否加粗, 前缀符号)
_SEMANTIC: Dict[str, tuple] = {
    "normal": (TEXT, False, ""),
    "info": (TEXT_SUB, False, ""),
    "success": (TEXT, True, "✓ "),
    "warning": (TEXT, True, "⚠ "),
    "error": (TEXT, True, "✕ "),
}

# 提示标签的 QSS。
# 注意：三种"强调"状态（success/warning/error）在单色下样式**完全相同** ——
# 区别只体现在文字前缀符号上。因此千万不要试图从 QSS 反推状态（会互相混淆，
# 实测踩坑：success 被识别成 error，文字前缀一直是 ✕）。
# 状态一律通过 set_hint_state() 显式指定。
HINT_QSS: Dict[str, str] = {
    state: "color:{0};{1}".format(color, "font-weight:bold;" if bold else "")
    for state, (color, bold, _prefix) in _SEMANTIC.items()
}

# 语义提示的左侧色条：单色下用边框粗细再强化一次区分
HINT_BORDER: Dict[str, str] = {
    "normal": "none",
    "info": "none",
    "success": "2px solid #1F2329",
    "warning": "2px solid #1F2329",
    "error": "3px solid #1F2329",
}


class HintLabel:
    """给提示标签补上"单色语义"的混入类（class MyLabel(HintLabel, QLabel)）。

    为什么需要它：
        原来的界面用红/绿/橙区分"出错 / 成功 / 提醒"。改成单色之后，
        三种状态的文字样式变得一样，若只改颜色，用户就分不清
        "出错了"和"一切正常"了。
        本类把状态显示为「符号（✕/✓/⚠）+ 加粗 + 左侧粗边条」，
        状态由 set_state() / set_hint_state() 显式给出，不靠猜。

    纪律：页面里设置提示文字请统一走 ui_style.set_hint_state(label, 文字, 状态)。
    """

    _hint_state: str = "normal"

    def set_state(self, state: str) -> None:
        """设置语义状态并刷新样式与文字前缀。"""
        self._hint_state = state if state in _SEMANTIC else "normal"
        style = hint_style(self._hint_state)
        border = HINT_BORDER.get(self._hint_state, "none")
        if border != "none":
            style = "{0}border-left:{1};padding-left:8px;".format(style, border)
        super().setStyleSheet(style)
        self._apply_prefix()

    def setStyleSheet(self, qss: str) -> None:  # noqa: N802 - Qt 命名约定
        """兼容老调用：只改外观，状态保持不变（状态请用 set_state）。"""
        self._hint_state = getattr(self, "_hint_state", "normal")
        border = HINT_BORDER.get(self._hint_state, "none")
        style = qss or ""
        if border != "none":
            style = "{0}border-left:{1};padding-left:8px;".format(style, border)
        super().setStyleSheet(style)

    def _apply_prefix(self) -> None:
        """按当前状态重建文字前缀（先剥掉上一次的符号，避免叠加）。"""
        try:
            text = super().text()
        except AttributeError:  # pragma: no cover - 非 QLabel 时静默跳过
            return
        for _state, (_color, _bold, prefix) in _SEMANTIC.items():
            if prefix and text.startswith(prefix):
                text = text[len(prefix):]
                break
        prefix = _SEMANTIC.get(getattr(self, "_hint_state", "normal"),
                               _SEMANTIC["normal"])[2]
        super().setText("{0}{1}".format(prefix, text))

    def setText(self, text: str) -> None:  # noqa: N802 - Qt 命名约定
        """设置文字时自动带上当前状态符号。"""
        super().setText(text or "")
        self._apply_prefix()


def hint_style(state: str) -> str:
    """取某个语义状态的提示样式（未知状态按 normal 处理）。"""
    return HINT_QSS.get(state, HINT_QSS["normal"])


def hint_prefix(state: str) -> str:
    """取某个语义状态的前缀符号（单色界面靠它区分状态）。"""
    return _SEMANTIC.get(state, _SEMANTIC["normal"])[2]


def set_hint_state(label, text: str, state: str = "normal") -> None:
    """给提示标签设置文字与语义状态（推荐统一用它，别手写 setStyleSheet）。

    用法：ui_style.set_hint_state(self._hint, "已导出 6 个文件", "success")
    效果：文字前自动加 ✓/⚠/✕，并附上相应粗细的左侧边条。
    """
    if label is None:
        return
    setter = getattr(label, "set_state", None)
    if callable(setter):
        setter(state)
    else:  # 普通 QLabel 兜底：只能改颜色
        label.setStyleSheet(hint_style(state))
    label.setText(text or "")


# ---------------------------------------------------------------------------
# 三、可实例化的提示标签
# ---------------------------------------------------------------------------
# 说明：HintLabel 只是混入类，不能直接实例化（HintLabel("", self) 会抛 TypeError）。
#       这里用工厂函数生成 "HintLabel + QLabel" 的组合类，避免每个页面各写一遍
#       `class _Hint(HintLabel, QLabel): pass`。
from PySide6.QtWidgets import QLabel  # noqa: E402


def _compose(base):
    """生成 HintLabel + base 的组合类（多重继承顺序：混入类在前）。"""
    return type("Hint" + base.__name__, (HintLabel, base), {})


HintQLabel = _compose(QLabel)


def help_span(text: str) -> str:
    """帮助/说明性小问号的富文本片段（单色：深灰下划线，不用蓝色）。"""
    return "<span style='color:{0};font-weight:bold'>{1}</span>".format(TEXT_SUB, text)


def required_span(text: str = "＊") -> str:
    """必填标记（单色：黑色粗体，而不是红色）。"""
    return "<span style='color:{0};font-weight:bold'>{1}</span>".format(TEXT, text)


def muted_span(text: str) -> str:
    """弱化说明文字片段。"""
    return "<span style='color:{0}'>{1}</span>".format(TEXT_WEAK, text)
