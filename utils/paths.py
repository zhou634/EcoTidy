# -*- coding: utf-8 -*-
"""
统一路径解析模块（开发方案 第四章 规格）

为什么必须有这个文件：
    程序一旦用 PyInstaller 打包成 EXE，sys.executable 指向 exe 所在目录，
    而 __file__ 指向的是临时解包目录。若代码里直接写 "temp/x.csv" 这类相对路径，
    文件会被写进解包目录，程序一退出全部丢失，历史任务、清洗快照等功能直接失效。

强制纪律（阶段 1 起冻结）：
    1. 全工程禁止出现相对路径字符串，所有路径一律经本模块解析后使用。
    2. 本模块只负责"算路径 + 建目录"，不做任何业务判断、不读写业务数据文件。
    3. 随包只读资源统一放 STATIC_DIR，可变数据统一放 DATA_DIR，两者严禁混用。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional

# =============================================================================
# 一、软件标识与运行形态
# =============================================================================

# ---------------------------------------------------------------------------
# 软件标识（全工程单一来源）
# ---------------------------------------------------------------------------
# 为什么集中在这里：main.py、core/settings.py、utils/errors.py、界面标题都要用，
# 各自定义一份就会出现"改了窗口标题却忘了改 QSettings 归属名"这类不一致。
# utils.paths 是最底层模块（只依赖标准库），谁都能安全导入，不会形成循环依赖。
APP_NAME: str = "EcoTidy"                  # 内部标识（QSettings 归属名）
APP_TITLE: str = "EcoTidy"                 # 界面显示名（窗口标题、关于、日志抬头）
APP_SUBTITLE: str = "生态数据清洗与分析"    # 一句话副标题（原超长全称已精简）
VERSION: str = "1.0"

# PyInstaller 打包运行时会注入 sys.frozen 属性；开发态该属性不存在。
IS_FROZEN: bool = bool(getattr(sys, "frozen", False))

# 开发态取工程根目录：本文件位于 <工程根>/utils/paths.py，故 parents[1] 即工程根。
# 打包态取 exe 所在目录：--onedir 模式下 static/ 与 exe 同级，路径规则最简单可靠。
BASE_DIR: Path = Path(sys.executable).resolve().parent if IS_FROZEN else Path(__file__).resolve().parents[1]


# =============================================================================
# 二、随包只读资源（打包时由 --add-data 一并复制，运行时严禁写入）
# =============================================================================

STATIC_DIR: Path = BASE_DIR / "static"          # 随包资源根目录，只读
FONTS_DIR: Path = STATIC_DIR / "fonts"          # 中文字体（思源黑体），解决图表中文方框
ICONS_DIR: Path = STATIC_DIR / "icons"          # 界面图标
DEMO_DIR: Path = STATIC_DIR / "demo"            # 内置示例数据（供「载入示例数据」一键体验）
HELP_DIR: Path = STATIC_DIR / "help"            # 内置帮助文档与术语表（F1 帮助面板读取）


# =============================================================================
# 三、用户可变数据根目录
# =============================================================================
# 为什么优先放 APPDATA 而不放程序目录：
#     1. 打包后程序目录通常在 Program Files 或只读介质中，写入会失败；
#     2. 用户重装/覆盖升级程序时，APPDATA 中的数据不受影响，历史任务不会丢。
#
# 但 APPDATA **不一定可写**（实测踩坑）：
#     本机（以及很多学校机房、受管控的公司电脑）%APPDATA% 下建目录
#     直接 [WinError 5] 拒绝访问，导致软件连启动都做不到。
#     因此这里不再"假设 APPDATA 可用"，而是逐个候选目录**真实探测可写性**，
#     第一个可写的就作为数据根；全都不可写时再抛中文异常由上层友好提示。
#
# 目录名随软件更名从 EcoDataAnalysis 改为 EcoTidy（见 _LEGACY_DATA_DIR_NAMES）。
_LEGACY_DATA_DIR_NAMES: tuple = ("EcoDataAnalysis",)


def _is_dir_writable(directory: Path) -> bool:
    """真实探测目录是否可写：建一个临时文件再删掉。

    为什么不只看 os.access(W_OK)：在 Windows 上它常因 ACL 与令牌的差异给出
    与实际写入结果不一致的答案（本机就是 ACL 显示 FullControl、实际写入却被拒绝）。
    只有真的写一次才靠得住。
    """
    probe = directory / ".write_probe"
    try:
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _data_dir_candidates() -> List[Path]:
    """按优先级给出候选数据目录。

    顺序说明：
        1. %APPDATA%\\EcoTidy    —— Windows 标准做法，重装/升级不丢数据；
        2. %LOCALAPPDATA%\\EcoTidy —— 漫游配置被限制时通常仍然可写；
        3. 程序目录下的 .ecodata  —— 便携/绿色部署，或前两者都被限制时；
        4. 系统临时目录           —— 最后的兜底，保证程序一定能启动（数据不持久）。
    """
    candidates: List[Path] = []
    for variable in ("APPDATA", "LOCALAPPDATA"):
        value = os.environ.get(variable)
        if value:
            candidates.append(Path(value) / APP_NAME)
    candidates.append(BASE_DIR / ".ecodata")
    import tempfile

    candidates.append(Path(tempfile.gettempdir()) / APP_NAME)
    # 同一个目录只留一次（环境变量可能指向同一处）
    unique: List[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


def _resolve_data_dir() -> Path:
    """解析用户数据根目录：返回第一个**确实可写**的候选目录。

    全部候选都不可写时返回首选目录（调用方会据 DATA_DIR_WRITABLE 给出提示），
    保证模块导入本身永不抛异常 —— 界面代码还要靠这些常量做路径拼接。
    """
    candidates = _data_dir_candidates()
    for index, candidate in enumerate(candidates):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        if _is_dir_writable(candidate):
            if index > 0:
                # 首选位置不可用，记下来以便启动时提示用户"数据存到别处了"
                global _DATA_DIR_FALLBACK_REASON
                _DATA_DIR_FALLBACK_REASON = (
                    "系统的用户配置目录（%APPDATA%）不可写" if index in (1, 2)
                    else "默认数据目录不可写")
            return candidate
    return candidates[0]


_DATA_DIR_FALLBACK_REASON: str = ""

DATA_DIR: Path = _resolve_data_dir()            # 用户数据根，程序自动创建
DB_PATH: Path = DATA_DIR / "ecodata.db"         # SQLite 本地库（阶段 7 使用）
TEMP_DIR: Path = DATA_DIR / "temp"              # 临时文件（含异常堆栈日志）
TASKS_DIR: Path = DATA_DIR / "tasks"            # 历史任务快照（阶段 7 使用）

# 首选数据目录（%APPDATA%\EcoTidy）。用于判断是否发生了回退。
PREFERRED_DATA_DIR: Path = _data_dir_candidates()[0] if _data_dir_candidates() else DATA_DIR

# 数据目录是否真的可写。False 表示所有候选都不可写，此时数据库、历史任务等
# 依赖落盘的功能不可用，但界面与纯计算功能仍能正常使用。
DATA_DIR_WRITABLE: bool = _is_dir_writable(DATA_DIR) if DATA_DIR.exists() else False


def legacy_data_dirs() -> List[Path]:
    """列出旧版本（更名前）可能留有数据的数据目录。

    用途：软件从 EcoDataAnalysis 更名为 EcoTidy 后，老用户的历史任务与偏好
    仍留在旧目录里。启动时探测一次并提示用户，避免"数据莫名其妙不见了"。
    只做只读探测，绝不自动搬移或删除。
    """
    found: List[Path] = []
    for variable in ("APPDATA", "LOCALAPPDATA"):
        value = os.environ.get(variable)
        if not value:
            continue
        for old_name in _LEGACY_DATA_DIR_NAMES:
            candidate = Path(value) / old_name
            if candidate != DATA_DIR and candidate.is_dir():
                found.append(candidate)
    return found

# 默认导出位置：用户的「文档」目录。路径解析失败时回退到用户主目录。
EXPORT_DIR: Path = Path.home() / "Documents" / APP_NAME

# 异常日志文件：全局异常钩子把堆栈写到这里，界面只显示通俗中文提示。
ERROR_LOG_PATH: Path = TEMP_DIR / "error.log"

# matplotlib 的字体缓存目录。
# 为什么必须由我们指定（实测踩坑）：
#     matplotlib 默认把字体缓存写到用户主目录下的 ~/.matplotlib，
#     并在那里创建 fontlist-*.json.matplotlib-lock 锁文件。
#     本机（以及很多受管控的公司/学校电脑）**主目录不可写**，
#     于是 import matplotlib.pyplot 直接抛
#     PermissionError: ... fontlist-v330.json.matplotlib-lock，软件连启动都做不到。
#     这与方案第九章避坑 2 是同一类问题（不能假设某个目录可写），
#     因此统一指到我们自己的数据目录，并且在使用前先探测可写性。
MPL_CONFIG_DIR: Path = DATA_DIR / "matplotlib"


# =============================================================================
# 四、目录自动创建
# =============================================================================

# 程序运行必须存在、缺失即自动创建的可变目录
_REQUIRED_DIRS: List[Path] = [DATA_DIR, TEMP_DIR, TASKS_DIR]


def ensure_dirs() -> List[Path]:
    """检查并创建全部可变目录，返回实际已存在的目录列表。

    调用时机：main.py 启动最开始处调用一次（阶段 1 接入）。
    失败处理：单个目录创建失败不抛出中断程序，而是收集后统一返回，
              由调用方决定是否提示用户（例如磁盘只读、权限不足）。
    """
    created: List[Path] = []
    for directory in _REQUIRED_DIRS:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            created.append(directory)
        except OSError:
            # 目录不可创建（权限/磁盘问题）时不在这里弹窗，交由上层统一处理
            continue
    return created


def data_dir_is_default() -> bool:
    """数据目录是否就是标准的 %APPDATA%\\EcoTidy。

    返回 False 说明发生了回退（例如便携部署或用户配置目录被限制），
    界面应当明确告诉用户"数据存在哪里"，否则用户换电脑后会找不到历史任务。
    """
    return DATA_DIR == PREFERRED_DATA_DIR


def describe_data_dir() -> str:
    """用中文说明数据目录位置，供界面提示使用。"""
    if data_dir_is_default():
        return "数据保存在：{0}".format(DATA_DIR)
    reason = _DATA_DIR_FALLBACK_REASON or "默认数据目录不可写"
    return ("数据保存在：{0}（因为{1}，已自动改存到这里）".format(DATA_DIR, reason))


def ensure_export_dir(target: Optional[Path] = None) -> Path:
    """确保导出目录存在并返回该目录（阶段 6 使用）。

    参数 target 为空时使用默认导出目录 EXPORT_DIR；
    用户自行选择目录时传入该目录，同样按需创建。
    """
    directory: Path = Path(target) if target is not None else EXPORT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def default_export_dir() -> Path:
    """给出一个**确实可写**的默认导出目录。

    为什么不能直接用「我的文档」（实测踩坑）：
        本机（以及很多受管控的电脑）「我的文档」不可写，用户点【一键导出】
        会直接撞上"无法创建输出文件夹"的报错 —— 而他什么都没做错。
        这里按「我的文档 → 用户数据目录 → 临时目录」依次探测可写性，
        保证第一次导出就能成功，用户之后再自行改到别处。
    """
    candidates: List[Path] = [EXPORT_DIR, DATA_DIR / "导出"]
    import tempfile

    candidates.append(Path(tempfile.gettempdir()) / APP_NAME / "导出")

    for directory in candidates:
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        if _is_dir_writable(directory):
            return directory
    return EXPORT_DIR


def export_dir_is_default() -> bool:
    """默认导出目录是否就是「我的文档」下的标准位置。"""
    return default_export_dir() == EXPORT_DIR


def resource_path(*parts: str) -> Path:
    """拼接 STATIC_DIR 下的只读资源路径。

    用法示例：resource_path("demo", "demo_quadrat.csv")
    作用：避免各处手写 "static/demo/xxx" 这类相对路径字符串（方案第四章禁止）。
    """
    return STATIC_DIR.joinpath(*parts)


# ===========================================================================
# 五、matplotlib 缓冲目录解析（必须在 import matplotlib 之前调用）
# ===========================================================================
def _has_readable_font_cache(directory: Path) -> bool:
    """目录里是否已有可读的字体缓存（供诊断与测试使用）。

    注意：本函数**不**用于决定 matplotlib 缓冲目录 —— 只有可读缓存并不够，
    matplotlib 启动时仍要写锁文件，目录不可写就会失败。判断逻辑见 resolve_mpl_config_dir。
    """
    try:
        for candidate in directory.glob("fontlist-*.json"):
            if candidate.is_file() and candidate.stat().st_size > 0:
                return True
    except OSError:
        return False
    return False


def resolve_mpl_config_dir() -> Path:
    """确定 matplotlib 的缓冲目录，保证它是**可写**的。

    选择顺序（顺序很关键，实测踩坑）：
        1. 用户已显式设置 MPLCONFIGDIR → 尊重用户设置；
        2. 我们自己的数据目录（%APPDATA%/EcoTidy/matplotlib）可写 → 首选；
        3. 系统默认的 ~/.matplotlib 可写 → 次选；
        4. 都不可写 → 临时目录下的 EcoTidy/matplotlib（方案第四章 TEMP_DIR
           的可写性有保证；代价只是字体缓存不持久，下次启动重新扫描一次字体）。

    为什么**不**采用"目录不可写但已有可读缓存就用它"这种省事做法（实测踩坑）：
        主目录里常常残留上次的字体缓存（本机就有 fontlist-v3.11.0.json），
        乍看"读得到就能用"，但 matplotlib 每次启动仍会尝试创建
        fontlist-*.json.matplotlib-lock 锁文件；只要不可写就抛
        PermissionError: ...matplotlib-lock —— 软件连界面都起不来。
        宁可换到可写的临时目录多扫一次字体，也不能让程序启动失败。

    关于显式设置了 MPLCONFIGDIR 的情况（实测踩坑）：
        原实现是"用户设了就用"，不再检查可写性 —— 这是错的。
        实测：MPLCONFIGDIR 指向一个**不存在又建不出来**的临时目录时，
        打包版绘图直接抛 PermissionError: fontlist-*.json.matplotlib-lock，
        整张图画不出来。因此现在显式目录也要过一遍"能建、能写"的检查，
        不可写就照常往下回退（并保留用户设置的意图，只是换个能用的位置）。
    """
    explicit = os.environ.get("MPLCONFIGDIR")
    if explicit:
        explicit_dir = Path(explicit)
        try:
            explicit_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            explicit_dir = None  # 类型标记：退回到候选列表
        if explicit_dir is not None and _is_dir_writable(explicit_dir):
            return explicit_dir

    home_cache = Path.home() / ".matplotlib"
    candidates: List[Path] = [MPL_CONFIG_DIR]
    if home_cache != MPL_CONFIG_DIR:
        candidates.append(home_cache)
    # 数据目录整体不可写时（数据根已回退到临时目录），再兜一层系统临时目录
    import tempfile

    candidates.append(Path(tempfile.gettempdir()) / APP_NAME / "matplotlib")

    for directory in candidates:
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        if _is_dir_writable(directory):
            return directory

    # 全部不可写：交回默认目录，由 matplotlib 报它自己的错误
    return home_cache


def ensure_mpl_config_dir() -> Path:
    """解析并**写入** MPLCONFIGDIR 环境变量，返回最终目录。

    调用时机极其重要：必须在任何 `import matplotlib` 之前执行一次，
    否则 matplotlib 会先用默认目录去建锁文件，在不可写的机器上直接抛 PermissionError。
    main.py 在导入任何界面模块之前调用本函数。

    幂等：已经设置过就直接返回，不会把用户/上层选定的目录改掉。

    注意（实测踩坑）：显式设置的 MPLCONFIGDIR 若不可写，这里会**换掉它** ——
    否则 matplotlib 建字体缓存锁文件时抛 PermissionError，绘图整个不可用。
    """
    explicit = os.environ.get("MPLCONFIGDIR")
    if explicit:
        explicit_dir = Path(explicit)
        try:
            explicit_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            explicit_dir = None
        if explicit_dir is not None and _is_dir_writable(explicit_dir):
            return explicit_dir
        # 显式目录不可用：清掉它，走正常的回退链
        os.environ.pop("MPLCONFIGDIR", None)
    directory = resolve_mpl_config_dir()
    os.environ["MPLCONFIGDIR"] = str(directory)
    return directory


# =============================================================================
# 五、自检入口
# =============================================================================
# 阶段 0 验收命令：
#     python -c "import utils.paths as p; print(p.DATA_DIR)"
# 期望：正常打印路径，并自动创建 DATA_DIR / TEMP_DIR / TASKS_DIR 三个目录。
if __name__ == "__main__":
    ensure_dirs()
    print("运行形态 IS_FROZEN :", IS_FROZEN)
    print("BASE_DIR           :", BASE_DIR)
    print("STATIC_DIR         :", STATIC_DIR)
    print("DATA_DIR           :", DATA_DIR)
    print("DB_PATH            :", DB_PATH)
    print("TEMP_DIR           :", TEMP_DIR)
    print("TASKS_DIR          :", TASKS_DIR)
    print("EXPORT_DIR         :", EXPORT_DIR)
    print("ERROR_LOG_PATH     :", ERROR_LOG_PATH)
