# -*- coding: utf-8 -*-
"""
中英文切换（全局国际化）

设计要点：
    1. **中文原文即键**：代码里写 `tr("载入示例数据")`，词典把中文映射到英文。
       好处是源码始终可读，漏翻时自动回落中文而不是显示乱码或键名。
    2. **不翻译数据契约**：列名（"相对密度"、"H 香农指数"…）是数据表头与
       计算逻辑的约定，翻译后会与导出结果对不上，因此一律不翻。
       这类字符串不调用 tr()，天然不会被翻译。
    3. **运行时即时生效**：切换语言后发 language_changed 信号，
       主窗口与各页面收到后重建文案，不需要重启软件。
    4. **漏翻可见**：debug 模式下把未翻译的字符串记下来，便于补齐。
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QObject, Signal

# 语言标识
LANG_ZH = "zh"
LANG_EN = "en"
ALL_LANGUAGES = (LANG_ZH, LANG_EN)
DEFAULT_LANGUAGE = LANG_ZH

#: 语言显示名（语言切换菜单里用；两种语言都写成"母语 + 英文"形式，便于识别）
LANGUAGE_NAMES: Dict[str, str] = {
    LANG_ZH: "中文",
    LANG_EN: "English",
}


class _LanguageBus(QObject):
    """语言变化的广播中心。"""

    language_changed = Signal(str)


_bus: Optional[_LanguageBus] = None
_current: str = DEFAULT_LANGUAGE
#: 调试用：记录没翻译到的中文，便于补齐词典
_missing: List[str] = []

#: 原文登记表：英文 -> 中文原文列表。
#: 用途：切换语言时，界面上已经显示的是**英文**，要变回中文就必须反查出原文。
#: tr() 每次调用都会把"中文原文 -> 英文"登记进来，这样界面重建文案时
#: 不必给每个控件额外保存一份键，通用重译成为可能。
_back: Dict[str, List[str]] = {}

#: 提示标签的状态前缀（与 ui/style.py 的语义符号一致）。
#: 重译时要先摘掉，否则 "⚠ 提示：…" 查不到词条。
_STATE_PREFIXES: tuple = ("✕ ", "✓ ", "⚠ ")


def _get_bus() -> _LanguageBus:
    global _bus
    if _bus is None:
        _bus = _LanguageBus()
    return _bus


def language_bus() -> _LanguageBus:
    """返回语言广播对象（界面连接它的 language_changed 信号）。"""
    return _get_bus()


def current_language() -> str:
    """当前语言标识（zh / en）。"""
    return _current


def is_english() -> bool:
    """当前是否为英文界面。"""
    return _current == LANG_EN


def language_name(code: Optional[str] = None) -> str:
    """语言显示名。"""
    return LANGUAGE_NAMES.get(code or _current, str(code))


def set_language(code: str, *, notify: bool = True) -> bool:
    """切换语言；返回是否真的发生了变化。

    notify=False 用于启动时按偏好恢复语言，此时界面还没建好，不需要广播。
    """
    global _current
    if code not in ALL_LANGUAGES:
        return False
    if code == _current:
        return False
    _current = code
    if notify:
        _get_bus().language_changed.emit(code)
    return True


def tr(text: str, *args: object, **kwargs: object) -> str:
    """翻译一条文案，并可同时格式化。

    用法：
        tr("载入示例数据")                      -> "Load sample data"
        tr("第 {0} 步：{1}", 2, "清洗")          -> "Step 2: Clean"
        tr("共 {n} 行", n=5)                    -> "5 rows"

    关键点：格式化参数里如果也是中文文案，会**先被翻译**再填进去。
    否则会出现 "Step 2: 清洗" 这种半截英文（实测踩坑）。

    中文模式下不做任何替换，直接返回原文并格式化，性能最好。
    """
    result = text
    if _current == LANG_EN:
        translated = _EN.get(text)
        if translated is None:
            if text and any("\u4e00" <= ch <= "\u9fff" for ch in text):
                _record_missing(text)
            translated = text
        result = translated
        # 登记反查表：英文 -> 原文，供 retranslate_widgets 使用
        if translated != text:
            bucket = _back.setdefault(translated, [])
            if text not in bucket:
                bucket.append(text)

    # 参数里的中文同样要翻（例如 tr("第 {0} 步：{1}", 2, "清洗")）
    if args:
        args = tuple(tr(a) if isinstance(a, str) else a for a in args)
    if kwargs:
        kwargs = {key: (tr(value) if isinstance(value, str) else value)
                  for key, value in kwargs.items()}

    if args or kwargs:
        try:
            result = result.format(*args, **kwargs)
        except (KeyError, IndexError, ValueError):
            pass
    return result


def trf(text: str, *args: object) -> str:
    """带位置参数的翻译（tr 的别名，保留以便语义清晰）。"""
    return tr(text, *args)


def _record_missing(text: str) -> None:
    """记录一条"可能漏翻"的文案。

    会先排掉两类**不是界面文案**的字符串，避免误报（实测踩坑）：
      · 已经是译文的内容：切换语言时会把英文再喂回来一次；
      · 文件路径：用户名或目录名里可能含汉字，但那是数据，不该翻译。
    """
    if text in _back:
        return
    if _looks_like_path(text):
        return
    if text not in _missing:
        _missing.append(text)


def _looks_like_path(text: str) -> bool:
    """粗略判断是否是文件路径（含盘符或路径分隔符、且很长）。"""
    if len(text) < 12:
        return False
    has_drive = len(text) > 2 and text[1] == ":" and text[2] in ("\\", "/")
    has_sep = ("\\" in text or "/" in text) and text.count("\\") + text.count("/") >= 2
    return has_drive or has_sep


def missing_translations() -> List[str]:
    """返回本次运行中未翻译到的中文（用于补齐词典）。"""
    return list(_missing)


def clear_missing() -> None:
    _missing.clear()


def language_menu_entries() -> List[tuple]:
    """语言切换菜单项：[(语言代码, 菜单文字), …]（当前语言带勾选标记）。"""
    entries = []
    for code in ALL_LANGUAGES:
        name = LANGUAGE_NAMES[code]
        mark = "✓ " if code == _current else "   "
        entries.append((code, mark + name))
    return entries


# ===========================================================================
# 中英词典
# ===========================================================================
# 词条表放在 utils/i18n_strings.py，便于集中审阅与后续补充；
# 本模块只负责查表与格式化。
from utils.i18n_strings import STRINGS as _EN  # noqa: E402


def register(mapping: Dict[str, str]) -> None:
    """批量登记词条（模块可把自己新增的词条注册进来）。"""
    _EN.update(mapping)


def translation_count() -> int:
    """当前词条总数（测试与自检用）。"""
    return len(_EN)


def has_translation(text: str) -> bool:
    """某条中文是否已有英文词条。"""
    return text in _EN


def retranslate_text(displayed: str) -> str:
    """把当前显示的文字翻到目标语言。

    · 目标语言是英文时：显示的应是中文 → 直接查词典；
    · 目标语言是中文时：显示的可能是英文 → 先反查原文再返回原文；
      查不到就原样返回（例如用户数据里的文字，不该被改动）。

    两处容易踩的坑（都实测遇到过）：
      1. 提示标签带状态前缀（✕/✓/⚠，见 ui/style.py 的 HintLabel）——
         查表前要先摘掉，否则永远查不到。
      2. 带参数的文案在控件里**已经格式化过**（"{0}" 已被替换成实际值），
         直接查表必然落空。这时改用"模板匹配"兜底。
    """
    if not displayed:
        return displayed

    prefix = ""
    body = displayed
    for mark in _STATE_PREFIXES:
        if displayed.startswith(mark):
            prefix = mark
            body = displayed[len(mark):]
            break

    if _current == LANG_EN:
        return prefix + tr(body)
    originals = _back.get(body) or _back.get(displayed)
    if originals:
        return prefix + originals[0]
    return displayed


# 各控件类型对应的"文字 setter"（通用重译时按顺序尝试）
def retranslate_widgets(root: object) -> int:
    """遍历控件树，把可见文字重译为当前语言，返回改动的处数。

    为什么需要这个通用实现（而不是给每个页面写一遍 retranslate）：
        页面里有几十个标签/按钮/复选框，逐个手写重译既啰嗦又必然漏掉几个。
        这里统一按类型取文字并重译，新增控件自动被覆盖。

    只处理"文案来源明确"的属性（text / placeholder / tooltip），
    绝不碰表格模型里的数据 —— 列名与单元格属于数据，不能翻译。
    """
    from PySide6.QtWidgets import (  # 延迟导入，避免非 Qt 环境导入本模块失败
        QAbstractButton, QComboBox, QGroupBox, QLabel, QLineEdit, QWidget,
    )

    changed = 0

    def apply(getter, setter) -> None:
        nonlocal changed
        try:
            current = getter()
        except Exception:  # noqa: BLE001 - 取不到就跳过
            return
        if not current:
            return
        target = retranslate_text(current)
        if target != current:
            try:
                setter(target)
                changed += 1
            except Exception:  # noqa: BLE001
                pass

    widgets = []
    try:
        widgets.append(root)
        find_children = getattr(root, "findChildren", None)
        if callable(find_children):
            # 注意：findChildren(object) 连布局（QObject）都会返回，
            # 它们没有 toolTip()，因此下面统一先判类型再取属性（实测踩坑）。
            widgets.extend(find_children(QWidget))
    except Exception:  # noqa: BLE001
        pass

    for widget in widgets:
        # 注意：QGroupBox 用的是 title()，不是 text()（实测踩坑）
        if isinstance(widget, (QLabel, QAbstractButton)):
            apply(widget.text, widget.setText)
        elif isinstance(widget, QGroupBox):
            apply(widget.title, widget.setTitle)
        if isinstance(widget, QLineEdit):
            apply(widget.placeholderText, widget.setPlaceholderText)
        if isinstance(widget, QComboBox):
            # 下拉项文字也要跟着换（例如配色方案、输出格式）
            for index in range(widget.count()):
                current = widget.itemText(index)
                target = retranslate_text(current)
                if target != current:
                    widget.setItemText(index, target)
                    changed += 1
        if isinstance(widget, QWidget):
            apply(widget.toolTip, widget.setToolTip)
    return changed
