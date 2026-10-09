# -*- coding: utf-8 -*-
"""
程序入口（开发方案 第六章 阶段 1 交付）

职责（只做这四件事，不写任何业务逻辑）：
    1. 创建 QApplication，并设置组织名 / 应用名（QSettings 靠它决定配置存放位置）；
    2. 安装全局异常钩子（sys.excepthook + Qt 消息处理器），异常写日志、界面弹通俗中文提示；
    3. 加载主窗口；
    4. 启动消息循环。

全局异常捕获在本阶段完成并生效（方案第六章阶段 1）：
    此后任何阶段抛出的异常都会被兜住，用户看到的是"人人能看懂的中文说明 + 怎么办"，
    而 Python 堆栈只写进 %APPDATA%\\EcoTidy\\temp\\error.log，不弹给用户。

实现位置说明：
    异常钩子与友好提示的具体实现放在 utils/errors.py，而不是本文件。
    原因是阶段 2 起各页面都要报告自己的异常，若它们必须 `import main`，
    就会与 main → ui.main_window 形成循环导入（方案避坑 13）。
    本文件只负责"在正确的时机把它装上"。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import List, Optional

# ===========================================================================
# 必须最先执行：确定 matplotlib 的缓冲目录
# ===========================================================================
# 为什么放在所有 import 之前：
#     matplotlib 在**首次导入时**就会去自己的配置目录（默认 ~/.matplotlib）
#     建立 fontlist-*.json.matplotlib-lock 锁文件。若该目录不可写，
#     导入语句本身就抛 PermissionError，界面还没起来软件就已经死了。
#     实测本机（以及很多受管控的电脑）用户主目录不可写，因此必须先把
#     MPLCONFIGDIR 指到我们确定可写的数据目录。
# 说明：utils.paths 只依赖标准库，提前导入它是安全的。
from utils import paths

paths.ensure_mpl_config_dir()


# ===========================================================================
# 依赖自检：缺包时给出可操作的中文提示，而不是甩一段英文堆栈
# ===========================================================================
# 为什么需要（实测反复踩坑）：
#     Visual Studio 会为每个工作区**新建**一个虚拟环境（env2、env3…），
#     新建的环境里没有装依赖，于是点运行就抛
#         ModuleNotFoundError: No module named 'PySide6'
#     用户看到的是英文堆栈，不知道"环境没装包"还是"代码有问题"。
#     这里提前检查并直接说清：缺什么、装到哪个解释器、用什么命令装。
_REQUIRED_PACKAGES = (
    ("PySide6", "PySide6", "界面框架"),
    ("pandas", "pandas", "数据处理"),
    ("numpy", "numpy", "数值计算"),
    ("matplotlib", "matplotlib", "绘图"),
    ("openpyxl", "openpyxl", "Excel 读写"),
)


def _check_dependencies() -> None:
    """检查运行依赖；缺失时打印中文指引并退出（退出码 3）。

    关键：只用 find_spec **查在不在**，绝不真的 import（实测性能问题）。
        最初这里写的是 importlib.import_module(module_name)，本意是"试着导入一下"，
        结果把 openpyxl、matplotlib 这些**启动时根本用不到**的重库全拉了起来：
            pandas     203 ms 时被引入
            matplotlib 780 ms 时被引入
            openpyxl   939 ms 时被引入
        实测启动因此多花约 0.8 秒 —— 为了"检查一下在不在"而付出近一半的启动时间。
        find_spec 只做路径查找，不执行模块代码，开销可忽略，
        而且同样能准确判断"缺包"（返回 None）。
    """
    import importlib.util
    import sys

    missing: List[str] = []
    for module_name, pip_name, purpose in _REQUIRED_PACKAGES:
        try:
            spec = importlib.util.find_spec(module_name)
        except (ImportError, ValueError):
            spec = None
        if spec is None:
            missing.append("{0}（{1}，用于{2}）".format(pip_name, module_name, purpose))

    if not missing:
        return

    exe = sys.executable
    lines = [
        "",
        "=" * 68,
        "启动失败：当前 Python 环境缺少必需的程序库。",
        "=" * 68,
        "",
        "当前使用的解释器：",
        "    {0}".format(exe),
        "",
        "缺少以下库：",
    ]
    lines.extend("    · " + item for item in missing)
    lines.append("")

    requirements = paths.BASE_DIR / "requirements.txt"
    if requirements.exists():
        lines.extend([
            "怎么办（二选一）：",
            "",
            "  ① 给当前环境装依赖（推荐，复制下面这行到命令行执行）：",
            "",
            '     "{0}" -m pip install -r "{1}"'.format(exe, requirements),
            "",
            "     若下载慢，可加国内镜像：",
            "     -i https://pypi.tuna.tsinghua.edu.cn/simple",
            "",
            "  ② 改用已经装好依赖的环境：",
            "     在 Visual Studio 里右键项目 → Python 环境 → 选择已有的环境，",
            "     或点「添加环境」指向该目录下的 Scripts\\python.exe。",
            "",
            "提示：Visual Studio 每新建一个工作区环境都是**空的**，",
            "      换环境后需要重新安装一次依赖（requirements.txt 里已锁定版本）。",
        ])
    else:
        # 打包后 requirements.txt 不随包分发，此时给另一套指引
        lines.extend([
            "怎么办：",
            "",
            "  ① 这是打包版，依赖本应已随包提供 —— 通常是解压不完整，",
            "     或安全软件误删了程序目录里的文件。",
            "     建议重新解压一份完整压缩包（不要只单独复制 exe 一个文件）。",
            "",
            "  ② 若你在源码环境里运行：",
            "     请在项目目录下执行 pip install -r requirements.txt，",
            "     或改用已经装好依赖的解释器运行 main.py。",
        ])

    lines.extend(["=" * 68, ""])
    print("\n".join(lines), file=sys.stderr)
    raise SystemExit(3)


_check_dependencies()

from PySide6.QtWidgets import QApplication  # noqa: E402

from core import settings  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from utils.errors import install_exception_hook, register_main_window  # noqa: E402

# 软件标识统一来自 utils/paths.py，避免多处各写一份导致不一致
ORG_NAME = paths.APP_NAME
APP_NAME = paths.APP_NAME

# 主窗口引用：只在进程内保留一份，防止被垃圾回收导致窗口闪退
_main_window: Optional[MainWindow] = None


def main(argv: Optional[List[str]] = None) -> int:
    """创建应用、装好异常钩子、显示主窗口并进入消息循环。"""
    global _main_window

    # 启动前先确保用户数据目录存在（数据库、临时文件、任务快照都要用到）。
    # 注意：ensure_dirs() 内部已逐个目录容错，这里再兜一层，
    #       因为此时异常钩子与界面都还没建立，一旦抛出就是"闪退 + 一堆英文堆栈"。
    try:
        paths.ensure_dirs()
    except Exception:  # noqa: BLE001 - 数据目录不可用不应阻止软件打开
        pass

    app = QApplication(argv if argv is not None else sys.argv)
    # 组织名 / 应用名决定 QSettings 的存储位置，必须与 core/settings.py 保持一致
    app.setOrganizationName(ORG_NAME)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(paths.APP_TITLE)
    # 应用级图标：任务栏、Alt+Tab、窗口标题栏都用它（黑底白字 E）
    from ui.main_window import build_app_icon

    app.setWindowIcon(build_app_icon())

    # 异常钩子装在最前面：此后任何环节出问题都不会闪退
    install_exception_hook(app)

    # 首次启动标记读取（欢迎对话框与"载入示例数据"在阶段 2 一起交付；
    # 这里先读一次，确保配置读写链路从第一天就是通的）
    _ = settings.instance().welcome_done

    # 按上次选择恢复界面语言（必须在建窗口之前，否则会先出中文再跳英文）
    from utils import i18n

    i18n.set_language(settings.instance().language, notify=False)

    # 建库建表（幂等）：历史任务功能依赖它，失败时只提示、不阻止软件启动
    try:
        from core import task_store

        task_store.ensure_database()
    except Exception as exc:  # noqa: BLE001 - 数据库不可用不应导致软件打不开
        from utils.errors import report_exception

        report_exception(exc, context="初始化本地数据库")
    _main_window = MainWindow()
    # 登记主窗口，让错误弹窗以它作为父窗口，始终显示在软件正上方
    register_main_window(_main_window)
    _main_window.show()
    try:
        code = app.exec()
    finally:
        # 退出前必须收尾：停掉后台解析线程并保存窗口状态。
        # 否则关闭窗口时若还有后台任务在跑，进程会以 0xC0000409
        # （STATUS_STACK_BUFFER_OVERRUN）异常中止，Windows 会弹"程序已停止工作"。
        try:
            _main_window.shutdown()
        except Exception:  # noqa: BLE001 - 收尾失败不应改变退出码
            pass
        # 处理完剩余事件后再退出，让 deleteLater 真正执行
        try:
            app.processEvents()
        except Exception:  # noqa: BLE001
            pass
    return code


def run_selftest(report_path: Optional[str] = None) -> int:
    """自检模式：不显示界面，把核心链路跑一遍并输出结果。

    为什么需要（发布前必须能回答"装到别人电脑上能用吗"）：
        打包后的 exe 是 GUI 程序，没有控制台，出问题只会"闪一下就没"或
        在别人机器上静默失败。这里提供一个可复现的自检：
            EcoTidy.exe --selftest
        它会依次验证：依赖可用 → 内置示例数据能读 → 清洗 → 指标计算 →
        出图 → 导出文件 → 字体（中文不乱码），并把结果写到指定文件。

    参数：
        report_path：结果写到哪个文件；为空则写到桌面或数据目录下的
                     EcoTidy_自检结果.txt，便于用户在资源管理器里直接打开。
    返回：0 全部通过；非 0 表示有检查项失败（失败项会逐条列出）。
    """
    import traceback

    lines: List[str] = []
    failures: List[str] = []

    # 进度文件：自检卡住时能看出卡在哪一步。
    # 为什么必须单独写一个文件（实测踩坑）：自检是逐项追加结果的，
    # 如果只在最后一次性写报告，一旦中途卡死（例如打包后某个模块导入阻塞），
    # 用户拿到的就是"什么都没有"，完全无法定位。
    progress_path: Optional[Path] = None
    try:
        if report_path:
            progress_path = Path(str(report_path) + ".progress")
        else:
            progress_path = Path(paths.DATA_DIR) / "EcoTidy_自检进度.txt"
        if progress_path.parent and not progress_path.parent.exists():
            progress_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        progress_path = None

    def trace(message: str) -> None:
        """把当前进度立即落盘（每步都刷，卡住时能看到最后一步）。"""
        if progress_path is None:
            return
        try:
            with open(progress_path, "a", encoding="utf-8") as handle:
                handle.write("[{0:>7.0f} ms] {1}\n".format(
                    (time.perf_counter() - T0) * 1000, message))
        except Exception:  # noqa: BLE001 - 进度写入失败不影响自检本身
            pass

    def record(name: str, ok: bool, detail: str = "") -> None:
        lines.append("{0} {1}{2}".format("[通过]" if ok else "[失败]", name,
                                         "" if not detail else "  —— " + detail))
        trace("{0} {1}{2}".format("[通过]" if ok else "[失败]", name,
                                  "" if not detail else "  —— " + detail))
        if not ok:
            failures.append(name)

    T0 = time.perf_counter()
    trace("自检开始")
    lines.append("EcoTidy 自检报告")
    lines.append("=" * 60)
    lines.append("软件版本：{0}".format(paths.VERSION))
    lines.append("运行方式：{0}".format("打包版（exe）" if paths.IS_FROZEN else "源码运行"))
    lines.append("程序目录：{0}".format(paths.BASE_DIR))
    lines.append("数据目录：{0}".format(paths.DATA_DIR))
    lines.append("Python：{0}".format(sys.version.split()[0]))
    lines.append("")

    # ---- 1. 依赖 ----
    trace("检查依赖...")
    import importlib.util

    for module_name, pip_name, purpose in _REQUIRED_PACKAGES:
        try:
            found = importlib.util.find_spec(module_name) is not None
        except Exception:  # noqa: BLE001
            found = False
        record("依赖 {0}（{1}）".format(pip_name, purpose), found)

    # ---- 2. 资源 ----
    trace("检查随包资源...")
    demo = paths.DEMO_DIR / "demo_quadrat.csv"
    record("内置示例数据存在", demo.exists(), str(demo))
    fonts = list(paths.FONTS_DIR.glob("*.ttf")) + list(paths.FONTS_DIR.glob("*.otf"))
    record("随包中文字体存在", bool(fonts), fonts[0].name if fonts else "未找到")

    # ---- 3. 数据链路 ----
    try:
        trace("导入核心模块（data_parse 等）...")
        from core import data_clean, data_parse, eco_index, plot_draw, report_make
        trace("核心模块导入完成")

        trace("读取内置示例数据...")
        parsed = data_parse.load_demo_data()
        record("读取示例数据", parsed.df is not None and len(parsed.df) > 0,
               "{0} 行 × {1} 列".format(len(parsed.df), len(parsed.df.columns)))

        trace("清洗数据...")
        cleaned = data_clean.clean_data(parsed.df, data_type=parsed.data_type)
        clean_df = cleaned.df if hasattr(cleaned, "df") else cleaned
        record("清洗数据", clean_df is not None and len(clean_df) > 0,
               "{0} 行".format(len(clean_df)))

        trace("计算指标...")
        outcome = eco_index.compute_indicators(
            clean_df, data_type=parsed.data_type,
            groups=eco_index.default_groups_for(parsed.data_type))
        has_metrics = (outcome.row_table is not None) or (outcome.summary_table is not None)
        record("计算指标", has_metrics,
               "行级 {0} 行".format(0 if outcome.row_table is None else len(outcome.row_table)))

        trace("绘图（首次会加载 matplotlib）...")
        spec = plot_draw.recommend_spec(clean_df, "clean")
        chart = plot_draw.draw_chart(clean_df, spec)
        record("绘制图表", chart is not None, spec.chart_type)

        # 中文是否正常渲染：图题里应出现中文字符
        title_ok = any("\u4e00" <= ch <= "\u9fff" for ch in (chart.title or ""))
        record("图表标题为中文", title_ok, chart.title or "(空)")

        import tempfile

        out_dir = Path(tempfile.mkdtemp(prefix="ecotidy_selftest_"))
        written = plot_draw.save_figure(chart, out_dir, formats=("png", "svg"))
        record("导出图片", len(written) == 2,
               "、".join(p.name for p in written))
        plot_draw.close_figure(chart)

        trace("导出 Excel...")
        tables = [(name, frame) for name, frame in
                  (("清洗后数据", clean_df),
                   ("行级指标", outcome.row_table),
                   ("汇总指标", outcome.summary_table)) if frame is not None]
        excel = out_dir / "自检_数据表.xlsx"
        report_make.write_tables_to_excel(tables, excel, [])
        record("导出 Excel", excel.exists(), "{0:.0f} KB".format(
            excel.stat().st_size / 1024) if excel.exists() else "未生成")

        text_report = out_dir / "自检_报告.txt"
        text_report.write_text("自检用报告\n" + "=" * 40 + "\n" + "\n".join(lines),
                               encoding="utf-8")
        record("导出文本报告", text_report.exists())
        lines.append("")
        lines.append("导出目录：{0}".format(out_dir))
    except Exception as exc:  # noqa: BLE001 - 自检要抓住一切异常并报告
        record("数据链路", False, "{0}: {1}".format(type(exc).__name__, exc))
        lines.append("")
        lines.append(traceback.format_exc())

    # ---- 4. 界面组件（不显示窗口）----
    try:
        trace("导入 QtWidgets...")
        from PySide6.QtWidgets import QApplication
        trace("QtWidgets 就绪")

        app = QApplication.instance() or QApplication([])
        trace("导入界面模块...")
        from ui.main_window import build_app_icon
        trace("界面模块就绪")

        icon = build_app_icon()
        record("生成应用图标", not icon.isNull(),
               "尺寸 " + ",".join(str(s) for s in icon.availableSizes()[:3]))

        from core.app_state import AppState

        trace("构建主窗口...")
        window = MainWindow(app_state=AppState.instance())
        trace("主窗口构建完成")
        pages_ok = all(getattr(window, name, None) is not None for name in
                       ("import_page", "clean_page", "calc_page", "plot_page", "export_page"))
        record("加载五个页面", pages_ok)
        window.close()
        app.processEvents()
    except Exception as exc:  # noqa: BLE001
        record("界面组件", False, "{0}: {1}".format(type(exc).__name__, exc))
        lines.append(traceback.format_exc())

    # ---- 汇总 ----
    elapsed = (time.perf_counter() - T0) * 1000
    lines.append("")
    lines.append("=" * 60)
    lines.append("耗时 {0:.0f} 毫秒".format(elapsed))
    if failures:
        lines.append("结论：有 {0} 项未通过 —— {1}".format(len(failures), "、".join(failures)))
    else:
        lines.append("结论：全部通过，软件功能正常。")
    lines.append("=" * 60)

    text = "\n".join(lines)

    # ---- 输出 ----
    # 打包成 --windowed 后，进程**没有有效的 stdout/stderr 句柄**：
    # 直接 print 会阻塞或抛异常（实测：exe --selftest 会永久卡住）。
    # 源码运行时 stdout 正常，保留打印便于开发时直接看。
    if sys.stdout is not None:
        try:
            print(text)
        except Exception:  # noqa: BLE001 - 无控制台时忽略
            pass

    # 结果文件：优先用户指定路径，否则写数据目录（用户一定能找到）
    target: Optional[Path] = None
    if report_path:
        target = Path(report_path)
    else:
        try:
            paths.ensure_dirs()
            target = Path(paths.TEMP_DIR) / "EcoTidy_自检结果.txt"
        except Exception:  # noqa: BLE001
            target = None
    if target is not None:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(text)
        except Exception as exc:  # noqa: BLE001
            if sys.stdout is not None:
                try:
                    print("[自检结果写入失败] {0}".format(exc))
                except Exception:  # noqa: BLE001
                    pass
            return 1
        if sys.stdout is not None:
            try:
                print("\n[自检结果已保存] {0}".format(target))
            except Exception:  # noqa: BLE001
                pass
    return 1 if failures else 0


if __name__ == "__main__":
    # 自检模式：EcoTidy.exe --selftest [结果文件路径]
    # 放在 GUI 之前，因为打包后没有控制台，只能靠结果文件看结论。
    if "--selftest" in sys.argv:
        _index = sys.argv.index("--selftest")
        _report = sys.argv[_index + 1] if len(sys.argv) > _index + 1 else None
        sys.exit(run_selftest(_report))

    sys.exit(main())
