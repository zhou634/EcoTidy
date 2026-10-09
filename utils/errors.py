# -*- coding: utf-8 -*-
"""
全局错误处理与友好提示（开发方案 5.5、第六章 阶段 1）

为什么单独成一个模块，而不是写在 main.py 里：
    各个页面在阶段 2 起都要在自己的 try/except 里报告错误。如果这些函数留在
    main.py，页面就得写 `import main`——而 main 又 import 了 ui.main_window，
    立刻形成循环导入（方案第九章避坑 13 明令禁止的情形）。
    放在 utils/ 下，页面与 core 都能安全引用，且不依赖任何界面模块。

界面纪律（方案 5.5）：
    1. 绝不把 Python 堆栈直接显示给用户；
    2. 弹窗必须回答四件事：哪个文件 / 哪一列 / 为什么 / 怎么办；
    3. 堆栈写入 TEMP_DIR 日志，弹窗里只给"打开日志文件夹"入口。
"""

from __future__ import annotations

import cgitb
import datetime
import sys
import traceback
from types import TracebackType
from typing import Any, List, Optional

from PySide6.QtCore import QtMsgType, QUrl, qInstallMessageHandler
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMessageBox

from utils import paths

# 软件名（写进日志抬头，便于区分用户是运行 EXE 还是在开发环境里跑）
APP_NAME = paths.APP_NAME

# 全局持有 QApplication 与主窗口引用（仅用于异常弹窗定位父窗口，不承载业务数据）
_app: Optional[QApplication] = None
_main_window: Optional[Any] = None

# Qt 消息处理器里保存最近若干条 Qt 警告，异常兜底时一并写进日志，便于定位
_qt_warnings: List[str] = []
_MAX_QT_WARNINGS = 50


# ===========================================================================
# 一、通俗中文说明：把异常翻译成用户能看懂的话
# ===========================================================================
def friendly_hint(exc: BaseException, context: str = "") -> str:
    """把异常翻译成"哪个对象 / 为什么 / 怎么办"三段式中文说明。

    覆盖最常见的 7 类失败，其余归入通用兜底，绝不把 Python 堆栈原文丢给用户。
    参数 context 用于补充场景，例如 context="导入文件 sample_quadrat.csv"。
    """
    lead = "操作中断" if not context else "在{0}时中断".format(context)

    if isinstance(exc, ModuleNotFoundError):
        missing = getattr(exc, "name", "") or "某个依赖库"
        return (
            "{0}：缺少运行所需的组件「{1}」。\n\n"
            "原因：当前 Python 环境里没有安装完整依赖。\n"
            "怎么办：在项目目录执行 pip install -r requirements.txt 后重新启动软件；"
            "若在 Visual Studio 中运行，请确认「Python 环境」窗口里选中的解释器装好了这些组件。".format(lead, missing)
        )

    if isinstance(exc, FileNotFoundError):
        path = getattr(exc, "filename", "") or "（未提供文件名）"
        return (
            "{0}：找不到文件 {1}。\n\n"
            "原因：文件被移动、重命名或删除，也可能是路径里的中文或空格导致解析失败。\n"
            "怎么办：确认该文件仍然存在；若已移动，请重新选择文件；"
            "若这是历史任务中的文件，可用「重新指定文件」重新关联。".format(lead, path)
        )

    if isinstance(exc, PermissionError):
        path = getattr(exc, "filename", "") or "（未提供文件名）"
        return (
            "{0}：没有权限访问 {1}。\n\n"
            "原因：文件正被 Excel 等程序占用，或该目录不允许写入。\n"
            "怎么办：先关闭正在占用该文件的程序；或换一个输出目录（例如「我的文档」下的文件夹）重试。".format(lead, path)
        )

    if isinstance(exc, UnicodeDecodeError):
        return (
            "{0}：文件编码无法识别。\n\n"
            "原因：文件不是 UTF-8 或 GB18030 编码。软件会依次尝试 UTF-8 带 BOM、UTF-8、GB18030。\n"
            "怎么办：用记事本打开该文件 → 另存为 → 编码选择「UTF-8」→ 保存后重新导入。".format(lead)
        )

    if isinstance(exc, KeyError):
        key = str(exc.args[0]) if exc.args else ""
        return (
            "{0}：数据表中缺少需要的列（{1}）。\n\n"
            "原因：表头写法与软件预期的标准字段不一致。\n"
            "怎么办：点【列映射】按钮，手工把标准字段对应到你的列；"
            "或按 F1 查看「数据准备要求」，确认这类数据需要哪些列。".format(lead, key)
        )

    if isinstance(exc, MemoryError):
        return (
            "{0}：内存不足。\n\n"
            "原因：一次导入的数据量过大，或同时打开了多个大文件。\n"
            "怎么办：分批导入数据；关闭其他占用内存的程序后重试。".format(lead)
        )

    if isinstance(exc, ZeroDivisionError):
        return (
            "{0}：计算时出现了除以零。\n\n"
            "原因：某个分组内没有任何个体（例如该样方株数合计为 0），"
            "或需要输入的参数（样方面积、供试种子总数等）填了 0。\n"
            "怎么办：回到【智能清洗】页检查该分组是否为空；"
            "若为参数问题，请在参数区填入大于 0 的数值。".format(lead)
        )

    if isinstance(exc, (ValueError, TypeError, IndexError)):
        return (
            "{0}：数据内容不符合预期，无法继续计算。\n\n"
            "原因：可能存在空数据表、整列都是空白、或该列不是数值格式。\n"
            "怎么办：回到【智能清洗】页检查该列数据；若整列为空，请从数据表中删除该列后重新导入。".format(lead)
        )

    return (
        "{0}：软件遇到了未预料到的问题。\n\n"
        "原因：详细信息已记录到日志文件，界面不便直接展示。\n"
        "怎么办：可点击「打开日志文件夹」查看日志；若问题反复出现，请把日志文件反馈给开发者。".format(lead)
    )


# ===========================================================================
# 二、异常日志落盘
# ===========================================================================
def write_error_log(exc_type: type, exc_value: BaseException,
                    exc_tb: Optional[TracebackType]) -> Optional[str]:
    """把完整堆栈写入 TEMP_DIR\\error.log，返回日志文件路径字符串。

    写日志失败（磁盘满、目录被占用）不抛异常：此时宁可丢日志，也不能让软件崩掉。
    """
    try:
        paths.ensure_dirs()
        with open(paths.ERROR_LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write("=" * 78 + "\n")
            handle.write("发生时间：{0}\n".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            handle.write("软件版本：{0}（根目录 {1}）\n".format(APP_NAME, paths.BASE_DIR))
            handle.write("运行形态：{0}\n".format("打包 EXE" if paths.IS_FROZEN else "开发环境"))
            handle.write("-" * 78 + "\n")
            # cgitb 输出带变量值的堆栈，比裸 traceback 更利于定位（只写日志，不显示给用户）
            handle.write(cgitb.text((exc_type, exc_value, exc_tb)))
            if _qt_warnings:
                handle.write("-" * 78 + "\n最近 Qt 警告：\n")
                handle.write("\n".join(_qt_warnings[-_MAX_QT_WARNINGS:]) + "\n")
            handle.write("\n")
        return str(paths.ERROR_LOG_PATH)
    except Exception:
        return None


# ===========================================================================
# 三、友好错误弹窗
# ===========================================================================
def show_friendly_error(exc_type: type, exc_value: BaseException,
                        exc_tb: Optional[TracebackType], context: str = "") -> None:
    """弹出通俗中文错误提示，并提供查看日志的入口。

    堆栈不放在提示正文里，而是放进 Qt 自带的「显示详细信息」折叠区，
    用户不想看技术细节时可以只看中文说明。
    """
    log_path = write_error_log(exc_type, exc_value, exc_tb)
    hint = friendly_hint(exc_value, context)

    if _app is None:
        # 连 QApplication 都没起来（例如依赖缺失），只能打印到控制台，绝不静默退出
        print("[启动失败] {0}".format(hint), file=sys.stderr)
        if log_path:
            print("[详细日志] {0}".format(log_path), file=sys.stderr)
        return

    parent = _main_window if _main_window is not None else None
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("操作遇到问题")
    box.setText(hint)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.button(QMessageBox.StandardButton.Ok).setText("知道了")

    open_folder_text = ""
    if log_path:
        # Qt 会据此自动显示「显示详细信息」按钮，默认折叠
        box.setDetailedText(
            "日志文件：{0}\n\n{1}".format(
                log_path, "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
        )
        open_folder_text = "打开日志文件夹"
        box.addButton(open_folder_text, QMessageBox.ButtonRole.ActionRole)

    # 用按钮文字判断：直接比较按钮对象在模态框返回后会失效（实测 bug）
    clicked_text = None
    box.exec()
    clicked = box.clickedButton()
    if clicked is not None:
        try:
            clicked_text = clicked.text()
        except RuntimeError:
            clicked_text = None

    if open_folder_text and clicked_text == open_folder_text:
        # 直接打开日志目录，省去用户自己找 %APPDATA% 的麻烦
        open_path_in_explorer(paths.TEMP_DIR)


def report_exception(exc: BaseException, context: str = "") -> None:
    """供各页面在 try/except 里主动调用：把异常转成友好提示。

    页面里的标准写法：
        from utils.errors import report_exception
        try:
            ...
        except Exception as exc:
            report_exception(exc, context="导入文件")
    """
    show_friendly_error(type(exc), exc, exc.__traceback__, context)


# ===========================================================================
# 四、在文件管理器里打开路径
# ===========================================================================
def _log_line(message: str) -> None:
    """把一行诊断信息追加到错误日志（写日志失败也静默，绝不因此再抛异常）。"""
    try:
        paths.ensure_dirs()
        with open(paths.ERROR_LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write("[{0}] {1}\n".format(
                datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message))
    except Exception:  # noqa: BLE001
        pass


def open_path_in_explorer(target: Any) -> bool:
    """在系统文件管理器里打开一个目录，返回是否成功。

    为什么单独封装（实测 bug）：
        直接 QDesktopServices.openUrl(QUrl.fromLocalFile(目录)) 失败时
        只返回 False 或被静默忽略，用户看到的就是"点了没反应"。
        这里先确认路径存在，再检查返回值，失败写日志，便于定位。
    """
    from pathlib import Path as _Path

    path = _Path(str(target))
    if not path.exists():
        _log_line("打开目录失败：路径不存在 - {0}".format(path))
        return False
    try:
        ok = QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
    except Exception as exc:  # noqa: BLE001 - 打不开目录不是致命错误
        _log_line("打开目录异常：{0} - {1}".format(path, exc))
        return False
    if not ok:
        _log_line("打开目录被系统拒绝：{0}".format(path))
    return bool(ok)


def reveal_in_explorer(target: Any) -> bool:
    """打开目录；传入文件时打开它所在的目录。"""
    from pathlib import Path as _Path

    path = _Path(str(target))
    return open_path_in_explorer(path if path.is_dir() else path.parent)


# ===========================================================================
# 五、钩子安装
# ===========================================================================
def install_exception_hook(app: QApplication) -> None:
    """安装全局异常钩子（sys.excepthook）与 Qt 消息处理器。

    为什么必须装：
        未捕获的异常会让 PySide6 直接终止进程——用户看到的是"软件闪退"，
        没有任何可操作信息，这是最不可接受的失败方式。
    """
    global _app
    _app = app

    def _hook(exc_type: type, exc_value: BaseException, exc_tb: Optional[TracebackType]) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            # 用户主动 Ctrl+C 中断，不属于错误
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        show_friendly_error(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook
    qInstallMessageHandler(_qt_message_handler)


def register_main_window(window: Any) -> None:
    """登记主窗口，作为错误弹窗的父窗口（避免弹窗跑到屏幕角落或主窗口后面）。"""
    global _main_window
    _main_window = window


def _qt_message_handler(mode: "QtMsgType", context: Any, message: str) -> None:
    """Qt 内部消息处理器：把 Qt 的警告与错误记入日志，不在界面弹窗。

    原因：Qt 内部警告（例如样式表属性不支持）对用户毫无意义，
          但排查问题时非常关键，因此只记日志，绝不弹窗打断用户操作。
    """
    mode_text = {
        QtMsgType.QtDebugMsg: "DEBUG",
        QtMsgType.QtInfoMsg: "INFO",
        QtMsgType.QtWarningMsg: "WARNING",
        QtMsgType.QtCriticalMsg: "CRITICAL",
        QtMsgType.QtFatalMsg: "FATAL",
    }.get(mode, "MESSAGE")

    line = "[Qt {0}] {1}".format(mode_text, message)
    # 控制台仍然输出，方便在 Visual Studio 输出窗口里调试
    print(line, file=sys.stderr)

    if mode in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
        _qt_warnings.append(line)
        if len(_qt_warnings) > _MAX_QT_WARNINGS:
            del _qt_warnings[0:len(_qt_warnings) - _MAX_QT_WARNINGS]
