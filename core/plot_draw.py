# -*- coding: utf-8 -*-
"""
matplotlib 绘图核心（开发方案 第六章 阶段 5 + 第九章避坑 10）

设计要点：
    1. 中文字体**不依赖系统字体名猜测**：优先用 static/fonts/ 内随包分发的开源字体，
       以 FontProperties(fname=绝对路径) 注册，并设 axes.unicode_minus=False。
       找不到随包字体时退用系统已装中文字体，并在说明里提醒（换机可能变方框）。
    2. 后端使用 matplotlib.backends.backend_qtagg（matplotlib 3.5+ 统一后端，兼容 PySide6），
       不使用已废弃的 backend_qt5agg。
    3. 四类图表统一科研配色、坐标轴标签、图例、字号。
    4. 导出支持 300dpi PNG 与 SVG 矢量图；文件名用「中文图表名 + 时间戳」，不静默覆盖同名文件。
    5. 图表标题与坐标轴标签**自动生成中文名**：绝不出现英文列名或代码字段名。

与界面的分工：
    本模块只负责"给数据画图并保存"，不 import 任何界面模块（除了可选的 qtagg 画布类），
    因此可以脱离界面单独测试。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

# ===========================================================================
# matplotlib 的延迟加载（启动性能优化）
# ===========================================================================
# 实测数据（pandas/numpy 已加载的前提下，即真实启动时的情形）：
#     import matplotlib              185 ms
#     from matplotlib.figure 导入    198 ms   ← 最贵，会拉起整个绘图栈
#     import matplotlib.pyplot        23 ms
#     matplotlib.font_manager          0 ms（随 matplotlib 一起来）
# 这些都只在"真的要画一张图"时才用得上，而用户刚打开软件时还没有任何图。
#
# 启动路径上因此只留下最便宜的代价：本模块本身的导入（约 26 ms）。
# 代价：matplotlib 缺失时不再在导入阶段就报错，而是在第一次绘图时明确提示
#       （见 ensure_matplotlib()），提示同样是中文且可操作的。
_MPL_CONFIGURED = False
_MPL_BACKEND_SET = False


def ensure_matplotlib() -> Any:
    """确保 matplotlib 可用并已设定后端，返回 matplotlib 模块。

    为什么在这里做三件事（顺序不能变）：
        1. 先确保缓冲目录可写 —— 在不可写主目录的机器上，
           导入 pyplot 会因为无法创建 fontlist 锁文件直接抛 PermissionError（实测踩坑）；
        2. 再 matplotlib.use("QtAgg") —— 必须在任何 pyplot/backend 导入之前，
           否则后端已被定死，嵌入画布会失败；
        3. 最后才真正 import，返回模块。
    """
    global _MPL_CONFIGURED, _MPL_BACKEND_SET

    if not _MPL_CONFIGURED:
        # 幂等：main.py 已设置过则原样保留
        try:
            from utils import paths as _paths

            _paths.ensure_mpl_config_dir()
        except Exception:  # pragma: no cover - 路径模块异常时不影响绘图本身
            pass
        _MPL_CONFIGURED = True

    import matplotlib

    if not _MPL_BACKEND_SET:
        # 统一使用 Qt 后端（方案要求：matplotlib 3.5+ 的 backend_qtagg，
        # 不用已废弃的 backend_qt5agg）
        try:  # pragma: no cover - 无 Qt 环境下退化为 Agg，便于纯计算测试
            matplotlib.use("QtAgg")
        except Exception:  # noqa: BLE001
            matplotlib.use("Agg")
        _MPL_BACKEND_SET = True
    return matplotlib


# ---------------------------------------------------------------------------
# 重资源的延迟加载（启动性能优化）
# ---------------------------------------------------------------------------
# 实测数据（pandas/numpy 已加载的前提下，即真实启动时的情形）：
#     import matplotlib             185 ms
#     from matplotlib.figure 导入   198 ms   ← 最贵，会拉起整个绘图栈
#     import matplotlib.pyplot       23 ms
#     matplotlib.font_manager         0 ms
# 这三样都只在"真的要画一张图"时才用得上，而用户刚打开软件时还没有任何图。
# 延迟后启动路径只保留最便宜的 import matplotlib。
# 顺序仍然正确：matplotlib.use() 在上面已执行，一定早于下面这些延迟导入。
_PYPLOT: Any = None
_FONT_MODULE: Any = None


def _lazy_pyplot() -> Any:
    """按需导入 pyplot 并缓存。"""
    global _PYPLOT
    if _PYPLOT is None:
        ensure_matplotlib()          # 后端必须在 pyplot 之前设定
        import matplotlib.pyplot as pyplot

        _PYPLOT = pyplot
    return _PYPLOT


def _lazy_font_module() -> Any:
    """按需导入 matplotlib.font_manager 并缓存。"""
    global _FONT_MODULE
    if _FONT_MODULE is None:
        ensure_matplotlib()
        from matplotlib import font_manager

        _FONT_MODULE = font_manager
    return _FONT_MODULE


# FontProperties 只出现在类型注解里（本文件启用了 from __future__ import annotations，
# 注解不会在运行时求值），所以不必真的导入它 —— 导入 font_manager 要花 200 ms。
# 这里给静态检查器一个别名，既让 pyflakes 满意，也不影响 IDE 的跳转提示。
if TYPE_CHECKING:  # pragma: no cover
    from matplotlib.figure import Figure
    from matplotlib.font_manager import FontProperties
else:
    Figure = Any
    FontProperties = Any


def _figure_class() -> Any:
    """按需导入 Figure 并缓存（约 200 ms，只在第一次真正建图时付一次）。"""
    ensure_matplotlib()
    from matplotlib.figure import Figure as figure_class

    return figure_class

# ===========================================================================
# 一、常量
# ===========================================================================
CHART_DIVERSITY_BAR = "多样性对比柱状图"
CHART_SENSOR_LINE = "时序折线图"
CHART_COMMUNITY_BOX = "群落指标箱线图"
CHART_QUADRAT_BAR = "样方统计对比图"

ALL_CHARTS: Tuple[str, ...] = (CHART_DIVERSITY_BAR, CHART_SENSOR_LINE,
                               CHART_COMMUNITY_BOX, CHART_QUADRAT_BAR)

# 图表类型 -> 该图表适合的数据表（用于界面提示与推荐）
CHART_TABLE_HINT: Dict[str, str] = {
    CHART_DIVERSITY_BAR: "汇总指标",
    CHART_SENSOR_LINE: "清洗数据",
    CHART_COMMUNITY_BOX: "行级指标",
    CHART_QUADRAT_BAR: "行级指标",
}

# ===========================================================================
# 二、配色方案（彩色 / 灰阶 / 纯黑白）
# ===========================================================================
# 三种模式的适用场景：
#     color  —— 屏幕汇报、PPT、需要一眼区分多个系列时；
#     gray   —— 与软件界面的白色单色调一致，黑白打印也能分辨（默认）；
#     bw     —— 期刊常见的"纯黑白"要求：只用黑/白填充，
#               靠**底纹（hatch）**区分系列，复印多次也不会糊。
#
# 为什么 bw 必须用底纹而不是灰阶：
#     灰阶在激光打印、复印、投影仪对比度不佳时会糊成一片，
#     而底纹（斜线/网格/点阵）在任何设备上都能区分。
PALETTE_MODE_COLOR = "彩色"
PALETTE_MODE_GRAY = "灰阶"
PALETTE_MODE_BW = "纯黑白"

ALL_PALETTE_MODES: Tuple[str, ...] = (PALETTE_MODE_GRAY, PALETTE_MODE_COLOR, PALETTE_MODE_BW)
DEFAULT_PALETTE_MODE = PALETTE_MODE_GRAY

# 颜色序列
_PALETTE_COLORS: Tuple[str, ...] = ("#165DFF", "#00B42A", "#FF7D00", "#F53F3F",
                                    "#722ED1", "#0FC6C2", "#EB0AA4", "#7D5A00")
# 8 级灰阶：相邻亮度差足够大（约 12%~80% 亮度跨度），黑白打印、投影、
# 色觉障碍下都能区分。
_PALETTE_GRAYS: Tuple[str, ...] = ("#1F2329", "#5A5F66", "#8C9096", "#C4C7CC",
                                   "#33383F", "#6E7379", "#A8ACB1", "#D8DADF")
# 纯黑白：填充在黑白之间交替，真正区分靠底纹
_PALETTE_BW: Tuple[str, ...] = ("#000000", "#FFFFFF")

# 底纹序列（仅 bw 模式使用）。
# 关键约束（实测踩坑）：**相邻两个系列必须一眼看得出差别**。
# 第一版用 ("", "//", "\\\\", "xx"…) 导致第 1 个与第 3 个都是实心、第 2 与第 4
# 只差斜线方向，小尺寸下图例几乎一样，等于没区分。
# 现在第 1 个实心黑，其余白底 + 差异很大的底纹：斜线 / 交叉 / 点阵 / 横线 / 竖线 / 密斜线 / 方格。
_HATCHES: Tuple[str, ...] = ("", "//", "xx", "..", "--", "||", "\\\\\\\\", "++")

# 纯黑白下折线图的线型序列（配合标记形状，保证多条线可区分）
_BW_LINE_STYLES: Tuple[str, ...] = ("-", "--", "-.", ":", (0, (3, 1, 1, 1)), (0, (5, 1)))
# 线标记形状：纯黑白下靠形状区分系列（不能用颜色）
_MARKERS: Tuple[str, ...] = ("o", "s", "^", "D", "v", "P", "X", "*")

# 兼容旧代码：PALETTE 仍指向默认（灰阶）方案。
# 说明：新增绘制请使用 palette_colors(spec) / series_style(spec, index)。
PALETTE: Tuple[str, ...] = _PALETTE_GRAYS

_PALETTES: Dict[str, Tuple[str, ...]] = {
    PALETTE_MODE_COLOR: _PALETTE_COLORS,
    PALETTE_MODE_GRAY: _PALETTE_GRAYS,
    PALETTE_MODE_BW: _PALETTE_BW,
}

# 折线图的标记色（单色：最深的黑，与灰阶柱状图区分开）
_LINE_MARKER_COLOR = "#1F2329"


def palette_for(mode: str) -> Tuple[str, ...]:
    """取某个配色模式的颜色序列（未知模式回退到默认灰阶）。"""
    return _PALETTES.get(mode, _PALETTES[DEFAULT_PALETTE_MODE])


def uses_hatch(mode: str) -> bool:
    """该模式是否需要用底纹区分系列（只有纯黑白需要）。"""
    return mode == PALETTE_MODE_BW


def series_style(mode: str, index: int, *, filled: bool = True) -> Dict[str, Any]:
    """给出第 index 个系列在指定配色模式下的样式关键字。

    参数：
        mode   ：配色模式（彩色 / 灰阶 / 纯黑白）
        index  ：系列序号（0 起）
        filled ：是否实心填充。折线图传 False（只要颜色/线型，不要填充）。
    返回可直接展开给 matplotlib 的字典：
        柱状图  axes.bar(..., **series_style(mode, i))
        箱线图  axes.boxplot(boxprops=dict(**series_style(mode, i)))
        折线图  axes.plot(..., **series_style(mode, i, filled=False))
    """
    colors = palette_for(mode)
    color = colors[index % len(colors)]

    if not filled:
        # 折线图：彩色/灰阶靠颜色区分；纯黑白靠线型 + 标记形状区分。
        # 注意：纯黑白下线条一律用黑色。若沿用"黑/白交替"的填充色，
        # 白色线条画在白底上等于看不见（实测踩坑）。
        if mode == PALETTE_MODE_BW:
            return {"color": "#000000",
                    "linestyle": _BW_LINE_STYLES[index % len(_BW_LINE_STYLES)],
                    "marker": _MARKERS[index % len(_MARKERS)]}
        return {"color": color, "marker": _MARKERS[index % len(_MARKERS)]}

    if uses_hatch(mode):
        # 只有第 1 个系列用实心黑，其余一律白底 + 差异明显的底纹。
        # 为什么不再让填充色在黑白之间交替：实心黑会出现两次，
        # 两个系列长得一模一样（实测踩坑，图例里完全分不出来）。
        if index == 0:
            return {"facecolor": "#000000", "edgecolor": "#000000", "linewidth": 0.9}
        hatch = _HATCHES[index % len(_HATCHES)]
        return {"facecolor": "#FFFFFF", "edgecolor": "#000000", "linewidth": 0.9,
                "hatch": hatch or "//"}

    # 彩色 / 灰阶：实心填充 + 白色描边，避免相邻柱子糊在一起
    style = {"color": color}
    style["facecolor"] = color
    style["edgecolor"] = "white"
    style["linewidth"] = 0.5
    return style

# 中文字体候选（随包字体优先，见 _resolve_font）
_BUNDLED_FONT_PATTERNS: Tuple[str, ...] = ("*.otf", "*.ttf", "*.ttc")
_SYSTEM_FONT_NAMES: Tuple[str, ...] = (
    "Noto Sans SC", "Source Han Sans SC", "Source Han Sans CN",
    "Microsoft YaHei", "SimHei", "DengXian", "SimSun", "WenQuanYi Micro Hei",
)

# 时序数据超过这个点数时按日均值聚合，避免折线糊成一团
_MAX_LINE_POINTS = 400

# 柱状图最多画多少个分类，超过则只画数值最大的若干项（阶段 8 大数据量回归定的值）
# 取值理由：屏幕上并排 40 根柱子已接近可读极限，再多就是一根挨一根的色带，
# 而且 300dpi 出图会慢到十几秒、图元多到几十万（实测 2000 根柱子即如此）。
_MAX_BAR_CATEGORIES = 40

# 列名的中文显示名（当列名本身不是中文时使用）
_COLUMN_LABELS: Dict[str, str] = {
    "S": "物种数 S", "N": "个体总数 N", "H": "香农指数 H", "D": "辛普森指数 D",
    "J": "Pielou 均匀度 J", "R": "Margalef 丰富度 R",
    "S 物种数": "物种数 S", "N 个体总数": "个体总数 N",
    "H 香农指数": "香农指数 H", "D 辛普森指数": "辛普森指数 D",
    "J Pielou均匀度": "Pielou 均匀度 J", "R Margalef丰富度": "Margalef 丰富度 R",
    "密度": "密度（株/m²）", "株数": "株数（株）", "胸径": "胸径（cm）",
    "盖度": "盖度（%）", "重要值": "重要值", "频度": "频度（%）",
    "相对密度": "相对密度（%）", "相对频度": "相对频度（%）",
    "相对优势度": "相对优势度（%）", "优势度": "优势度",
    "气温": "气温（℃）", "相对湿度": "相对湿度（%）", "土壤温度": "土壤温度（℃）",
    "光合有效辐射": "光合有效辐射 μmol/(m²·s)",
    "样方号": "样方号", "物种": "物种", "时间": "时间",
    "分组": "日期", "时间分组": "日期", "处理组": "处理组", "重复": "重复",
}


class PlotError(Exception):
    """绘图失败异常：信息为中文，可直接展示给用户。"""


@dataclass
class ChartSpec:
    """一张图的绘制要求。

    字段含义：
        table_key   ：数据来源（clean / index_row / index_summary）
        chart_type  ：四类图表之一
        title       ：标题（留空则自动生成中文标题）
        category    ：分类轴字段（X 轴）
        values      ：数值字段（Y 轴，可多选）
        group_by    ：分组字段（箱线图按它分组；柱状图按它并排）
        aggregate   ：时序折线图是否按日聚合（点多时自动开启）
        palette     ：配色方案（彩色 / 灰阶 / 纯黑白），打印投稿用"纯黑白"
    """
    table_key: str
    chart_type: str
    title: str = ""
    category: Optional[str] = None
    values: List[str] = field(default_factory=list)
    group_by: Optional[str] = None
    aggregate: bool = True
    #: 配色方案（彩色 / 灰阶 / 纯黑白），见 ALL_PALETTE_MODES
    palette: str = DEFAULT_PALETTE_MODE


@dataclass
class ChartResult:
    """绘图结果。"""
    figure: Figure
    title: str                                   # 最终使用的中文标题
    x_label: str = ""
    y_label: str = ""
    notes: List[str] = field(default_factory=list)   # 中文说明（字体来源、聚合、跳过的列等）
    font_source: str = ""                        # "随包字体" / "系统字体"


# ===========================================================================
# 二、字体处理（避坑 10）
# ===========================================================================
_FONT_CACHE: Dict[str, Any] = {"font": None, "source": "", "checked": False}


def _bundled_font_file() -> Optional[Path]:
    """在 static/fonts 下按 *.otf → *.ttf → *.ttc 顺序找随包中文字体。"""
    try:
        from utils import paths
    except Exception:  # pragma: no cover - 极端情况下仍要能出图
        return None
    fonts_dir = paths.FONTS_DIR
    if not fonts_dir.exists():
        return None
    for pattern in _BUNDLED_FONT_PATTERNS:
        for candidate in sorted(fonts_dir.glob(pattern)):
            if candidate.is_file() and candidate.stat().st_size > 10000:
                return candidate
    return None


def _system_cjk_font_file() -> Optional[Path]:
    """在系统已安装字体里找一个含中文字形的字体文件（仅作退路）。"""
    try:
        font_manager = _lazy_font_module()
        candidates = font_manager.findSystemFonts(fontpaths=None, fontext="ttf")
        candidates += font_manager.findSystemFonts(fontpaths=None, fontext="otf")
    except Exception:  # pragma: no cover
        return None
    markers = ("notosanssc", "notoserifsc", "sourcehansans", "sourcehanserif",
               "msyh", "simhei", "deng", "simsun", "wqy", "microsoftyahei")
    for path in candidates:
        name = Path(path).name.lower().replace(" ", "").replace("-", "")
        if any(marker in name for marker in markers):
            return Path(path)
    return None


def _make_font_properties(font_file: Path) -> Any:
    """把字体文件包成 FontProperties。

    为什么必须单独包一层 try（实测故障，打包版才会暴露）：
        导入 matplotlib.font_manager 时它会**构建并缓存字体索引**，
        需要往 MPLCONFIGDIR 写 fontlist-*.json 及其锁文件。
        若该目录不可写（受管控的电脑、临时目录被清理、安全软件拦截），
        导入会直接抛 PermissionError —— 结果是"整张图画不出来"。
        用户看到的就是"点了绘图没反应/报错"，而其实只是字体索引写不进去。

    降级策略：拿不到 FontProperties 时就返回 None。
        此时图中中文会退化成方框，但**图形本身能正常画出来**，
        同时由调用方在说明里提示"中文字体未生效"。
        宁可图能出来但有瑕疵，也不要整张图失败。
    """
    try:
        return _lazy_font_module().FontProperties(fname=str(font_file))
    except Exception:  # noqa: BLE001 - 字体索引不可写时降级，不让绘图整体失败
        _record_font_failure()
        return None


def _record_font_failure() -> None:
    """记一次"字体索引不可用"，供界面提示用。"""
    _FONT_CACHE["load_failed"] = True


def resolve_font() -> Tuple[Optional[FontProperties], str]:
    """确定使用哪个中文字体，返回 (FontProperties 或 None, 来源说明)。

    结果会缓存：字体不会在运行中变化，重复查找会拖慢每次出图。
    """
    if _FONT_CACHE["checked"]:
        return _FONT_CACHE["font"], _FONT_CACHE["source"]

    font: Optional[FontProperties] = None
    source = ""

    bundled = _bundled_font_file()
    if bundled is not None:
        font = _make_font_properties(bundled)
        source = "随包字体 {0}".format(bundled.name)
    else:
        system = _system_cjk_font_file()
        if system is not None:
            font = _make_font_properties(system)
            source = "系统字体 {0}".format(system.name)

    if font is None:
        source = "未能加载中文字体（图中中文可能显示为方框）"

    # 负号显示：使用中文字体后减号容易变成方框，必须显式关闭 unicode 负号
    matplotlib = ensure_matplotlib()
    matplotlib.rcParams["axes.unicode_minus"] = False
    if font is not None:
        # 同时设置字体名列表，让未显式传 fontproperties 的元素（如图例、刻度）也能显示中文
        try:
            matplotlib.rcParams["font.sans-serif"] = [font.get_name()] + list(
                matplotlib.rcParams.get("font.sans-serif", []))
        except Exception:  # pragma: no cover
            pass

    _FONT_CACHE.update({"font": font, "source": source, "checked": True})
    return font, source


def reset_font_cache() -> None:
    """清空字体缓存（测试用：放入新字体后可立即生效，不必重启软件）。"""
    _FONT_CACHE.update({"font": None, "source": "", "checked": False})


def _fp() -> Optional[FontProperties]:
    return resolve_font()[0]


# ===========================================================================
# 三、中文标签生成（不允许出现英文列名）
# ===========================================================================
def column_label(column: Optional[str]) -> str:
    """把列名转成中文显示名；已是中文的列名原样返回。"""
    if not column:
        return ""
    name = str(column)
    if name in _COLUMN_LABELS:
        return _COLUMN_LABELS[name]
    # 去掉常见英文后缀噪音，但保留中文
    if re.search(r"[\u4e00-\u9fff]", name):
        return name
    return name


def _has_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", str(text)))


def describe_chart(chart_type: str, category: Optional[str], values: Sequence[str],
                   group_by: Optional[str] = None) -> str:
    """按图表类型自动生成中文标题（如"不同样方香农多样性指数对比"）。"""
    value_labels = [column_label(v) for v in values]
    value_text = "、".join(value_labels[:2]) + ("等" if len(value_labels) > 2 else "")
    category_text = column_label(category)

    if chart_type == CHART_DIVERSITY_BAR:
        if len(values) == 1:
            return "不同{0}的{1}对比".format(category_text or "分组", value_labels[0])
        return "不同{0}的多样性指标对比".format(category_text or "分组")
    if chart_type == CHART_SENSOR_LINE:
        return "{0}随时间变化".format(value_text or "监测指标")
    if chart_type == CHART_COMMUNITY_BOX:
        if group_by:
            return "不同{0}的{1}分布".format(column_label(group_by), value_text)
        return "{0}的分布".format(value_text)
    if chart_type == CHART_QUADRAT_BAR:
        return "不同{0}的{1}对比".format(category_text or "分组", value_text)
    return "{0}统计图".format(value_text or "数据")


# ===========================================================================
# 四、画布与通用样式
# ===========================================================================
def create_figure(width: float = 9.0, height: float = 5.4, dpi: int = 100) -> Figure:
    """创建一个空白图（供界面嵌入或本模块内部使用）。"""
    figure = _figure_class()(figsize=(width, height), dpi=dpi, layout="constrained")
    return figure


def _style_axes(axes: Any, spec: ChartResult, font: Optional[FontProperties]) -> None:
    """统一设置标题、轴标签、网格与刻度字体。"""
    axes.set_title(spec.title, fontproperties=font, fontsize=14, pad=12)
    if spec.x_label:
        # labelpad：给刻度数字留出空间，否则轴标题会与刻度数字贴在一起
        # （实测：窄画布下"气温（℃）"会和刻度 40 叠字）
        axes.set_xlabel(spec.x_label, fontproperties=font, fontsize=12, labelpad=6)
    if spec.y_label:
        axes.set_ylabel(spec.y_label, fontproperties=font, fontsize=12, labelpad=8)
    axes.grid(True, axis="y", linestyle="--", linewidth=0.6, alpha=0.35)
    axes.set_axisbelow(True)
    for label in axes.get_xticklabels() + axes.get_yticklabels():
        label.set_fontproperties(font)
        label.set_fontsize(10)
    for spine in ("top", "right"):
        axes.spines[spine].set_visible(False)
    # 日期刻度容易首尾重叠（例如 …06-25 与 2024-07-01 挤在一起），
    # 让 matplotlib 自己减少刻度数量，比手动算密度可靠。
    _thin_date_ticks(axes)


def _thin_date_ticks(axes: Any) -> None:
    """让时间轴自动稀疏刻度，避免首尾标签互相压字。

    做法：交给 matplotlib 的 AutoLocator（它会按可用宽度选取"好看"的间隔），
    再按需要旋转标签。识别不出日期时静默跳过。
    """
    try:
        ensure_matplotlib()
        import matplotlib.dates as mdates

        locator = axes.xaxis.get_major_locator()
        if isinstance(locator, (mdates.AutoDateLocator, mdates.DayLocator,
                                mdates.MonthLocator, mdates.YearLocator)):
            axes.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=9))
            axes.xaxis.set_major_formatter(mdates.ConciseDateFormatter(
                axes.xaxis.get_major_locator()))
    except Exception:  # noqa: BLE001 - 纯美化，失败不影响出图
        pass


def _shared_axis_label(value_columns: Sequence[str]) -> str:
    """给共用一根 Y 轴的多条曲线起一个合适的轴标题。

    为什么要判断：把「气温（℃）、相对湿度（%）、土壤温度（℃）」整串竖着写在 Y 轴上，
    窄画布里会与刻度数字叠字（实测）。而多条曲线本来就是不同单位，写"数值"最诚实，
    具体哪条线是什么由图例说明。
    规则：
        只有一列            → 直接用它的完整名称（含单位）
        多列且单位一致      → 用「中文量名（单位）」
        多列且单位不一致    → "数值"
    """
    if len(value_columns) == 1:
        return column_label(value_columns[0])
    units = set()
    for column in value_columns:
        label = column_label(column)
        match = re.search(r"[（(]([^）)]*)[）)]\s*$", label)
        units.add(match.group(1) if match else "")
    if len(units) == 1:
        unit = units.pop()
        if not unit:
            # 多列都没有单位（例如 H 香农指数、D 辛普森指数）：用"数值"，
            # 硬套第一列的名字会让读者以为轴上是那一个指标。
            return "数值"
        base = re.sub(r"[（(][^）)]*[）)]\s*$", "", column_label(value_columns[0])).strip()
        return "{0}（{1}）".format(base, unit)
    return "数值"


def _empty_note(value_columns: Sequence[str]) -> List[str]:
    return ["以下列没有可用的数值，已跳过：{0}。".format("、".join(column_label(v) for v in value_columns))]


# ===========================================================================
# 五、四类图表
# ===========================================================================
def _draw_diversity_bar(df: pd.DataFrame, spec: ChartSpec, figure: Figure,
                        font: Optional[FontProperties]) -> ChartResult:
    """多样性对比柱状图：分类轴 = 样方（或分组），每个指标一组柱子并排。"""
    category = spec.category
    value_columns = [v for v in spec.values if v in df.columns
                     and pd.api.types.is_numeric_dtype(df[v])]
    if category not in df.columns:
        raise PlotError(
            "找不到分类字段「{0}」。\n\n怎么办：在 X 轴下拉框里重新选择一列作为分类。".format(
                column_label(category)))
    if not value_columns:
        raise PlotError(
            "没有可用的数值字段。\n\n怎么办：在 Y 轴里至少选择一个数值列（例如「香农指数 H」）。")

    axes = figure.add_subplot(111)
    # 按自然顺序排列分类（Q1, Q2 … Q10 而不是 Q1, Q10, Q2）
    ordered = natural_sorted(df[category].dropna().unique().tolist())
    total_categories = len(ordered)

    # 同一个分类值只保留一行（汇总表本身就是一行一个分类，行级表则取该分类的均值）
    plotted = _pivot_for_categories(df, category, value_columns, ordered)

    notes: List[str] = []
    if total_categories > _MAX_BAR_CATEGORIES:
        # 实测（阶段 8 大数据量回归）：2000 个样方直接画会得到一根挨一根的色带，
        # 既看不清，300dpi 出图也要十几秒、文件巨大。因此只画"最突出的若干项"。
        # 按第一个指标的数值降序取前 N 个，用户一眼就能看到对比最强的那些样方。
        primary = value_columns[0]
        ranked = plotted[primary].sort_values(ascending=False, kind="stable")
        kept = [value for value in ranked.index[: _MAX_BAR_CATEGORIES]]
        # 保持原有的自然顺序显示，避免"从大到小"打乱样方编号习惯
        kept_set = set(kept)
        ordered = [value for value in ordered if value in kept_set]
        plotted = plotted.reindex(ordered)
        notes.append(
            "分类过多（共 {0} 项），图中只画了「{1}」最大的 {2} 项，"
            "其余未画的项仍完整保留在数据表里。".format(
                total_categories, column_label(primary), len(ordered)))
        notes.append("如需查看全部项，建议在【数据来源】里换用汇总指标表并按分组统计，"
                     "或直接查看导出的数据表。")

    labels = [str(value) for value in ordered]
    positions = np.arange(len(labels))
    count = len(value_columns)
    width = 0.8 / max(count, 1)

    for index, column in enumerate(value_columns):
        offset = (index - (count - 1) / 2.0) * width
        axes.bar(positions + offset, plotted[column].to_numpy(),
                 width=width, label=column_label(column),
                 **series_style(spec.palette, index))

    axes.set_xticks(positions)
    axes.set_xticklabels(labels, rotation=0 if len(labels) <= 12 else 45,
                         ha="center" if len(labels) <= 12 else "right")
    axes.legend(prop=font, frameon=False, ncol=min(len(value_columns), 4))

    result = ChartResult(
        figure=figure,
        title=spec.title or describe_chart(spec.chart_type, category, value_columns),
        x_label=column_label(category),
        y_label=column_label(value_columns[0]) if len(value_columns) == 1 else "指标值",
        notes=notes,
    )
    _style_axes(axes, result, font)
    return result


def _pivot_for_categories(df: pd.DataFrame, category: str, value_columns: Sequence[str],
                          ordered_categories: Sequence[Any]) -> pd.DataFrame:
    """把"一分类多行"的数据整理成"一分类一行"，缺失的分类补 NaN。

    为什么需要：汇总表本身一行一个样方，但用户也可能对行级表画柱状图
    （同一样方有多个物种）。此时按分类求均值，保证柱状图每根柱子只对应一个数。
    """
    frame = df.copy(deep=True)
    for column in value_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    grouped = frame.groupby(category, dropna=False)[list(value_columns)].mean(numeric_only=True)
    # 按自然顺序重排（Q1, Q2 … Q10），并把没出现的分类保留下来
    ordered = [value for value in ordered_categories if value in grouped.index]
    return grouped.reindex(ordered)


def _draw_sensor_line(df: pd.DataFrame, spec: ChartSpec, figure: Figure,
                      font: Optional[FontProperties]) -> ChartResult:
    """时序折线图：X 轴为时间，Y 轴可多选；点数过多时按日均值聚合。"""
    if "时间" not in df.columns:
        raise PlotError(
            "这份数据里没有「时间」列，画不了时序折线图。\n\n"
            "怎么办：请在数据来源里选择传感器时序数据；若想比较样方之间的差异，"
            "请改用「多样性对比柱状图」或「样方统计对比图」。")

    value_columns = [v for v in spec.values if v in df.columns
                     and pd.api.types.is_numeric_dtype(df[v])]
    if not value_columns:
        raise PlotError("没有可用的数值字段。\n\n怎么办：在 Y 轴里至少选择一个数值列（例如「气温」）。")

    working = df.copy(deep=True)
    working["_时间"] = pd.to_datetime(working["时间"], errors="coerce")
    working = working[working["_时间"].notna()].sort_values("_时间")
    if working.empty:
        raise PlotError("时间列没有可解析的记录，无法绘制时序图。\n\n怎么办：请检查时间列的内容后重新导入。")

    notes: List[str] = []
    if spec.aggregate and len(working) > _MAX_LINE_POINTS:
        working["_时间"] = working["_时间"].dt.floor("D")
        working = working.groupby("_时间", as_index=False)[value_columns].mean()
        notes.append("数据点较多（超过 {0} 个），已按**日均值**绘制折线，避免线条挤在一起看不清。".format(
            _MAX_LINE_POINTS))

    axes = figure.add_subplot(111)
    show_markers = len(working) <= 40
    for index, column in enumerate(value_columns):
        # 线型与标记形状由 series_style 给出（纯黑白下靠它们区分系列）
        line_style = series_style(spec.palette, index, filled=False)
        if not show_markers:
            # 点太密时不画标记，否则线条会变成一串珠子
            line_style.pop("marker", None)
        axes.plot(working["_时间"], pd.to_numeric(working[column], errors="coerce"),
                  label=column_label(column), linewidth=1.6, markersize=3.5,
                  **line_style)
    axes.legend(prop=font, frameon=False, ncol=min(len(value_columns), 4))
    axes.grid(True, axis="x", linestyle=":", linewidth=0.5, alpha=0.3)
    # 不调用 figure.autofmt_xdate()：它与 constrained 布局不兼容，会打印
    # "layout engine incompatible with subplots_adjust" 的英文警告（方案 5.5 不允许）。
    # 直接旋转刻度标签即可达到同样效果。
    for label in axes.get_xticklabels():
        label.set_rotation(0)
        label.set_ha("center")

    result = ChartResult(
        figure=figure,
        title=spec.title or describe_chart(spec.chart_type, None, value_columns),
        x_label="时间",
        y_label=_shared_axis_label(value_columns),
        notes=notes,
    )
    _style_axes(axes, result, font)
    return result


def _draw_community_box(df: pd.DataFrame, spec: ChartSpec, figure: Figure,
                        font: Optional[FontProperties]) -> ChartResult:
    """群落指标箱线图：按分类字段分组，展示每个数值字段的分布。"""
    group = spec.group_by or spec.category
    value_columns = [v for v in spec.values if v in df.columns
                     and pd.api.types.is_numeric_dtype(df[v])]
    if group not in df.columns:
        raise PlotError(
            "找不到分组字段「{0}」。\n\n怎么办：在分组下拉框里选择一列（例如「物种」或「样方号」）。".format(
                column_label(group)))
    if not value_columns:
        raise PlotError("没有可用的数值字段。\n\n怎么办：在指标下拉框里至少选择一个数值列。")

    categories = natural_sorted(pd.unique(df[group].dropna()).tolist())
    notes: List[str] = []

    # 每个指标一个子图，避免把量纲不同的指标硬画到同一根坐标轴上
    axes_list = figure.subplots(1, len(value_columns), squeeze=False)[0]
    for index, column in enumerate(value_columns):
        axes = axes_list[index]
        data = [pd.to_numeric(df.loc[df[group] == category, column], errors="coerce").dropna().to_numpy()
                for category in categories]
        non_empty = [(cat, values) for cat, values in zip(categories, data) if len(values)]
        if not non_empty:
            axes.set_visible(False)
            notes.append("「{0}」没有可用数值，该子图已隐藏。".format(column_label(column)))
            continue
        # 箱线图每个子图只画一个指标，因此系列序号固定用子图序号
        # （纯黑白下相邻子图交替黑白底纹，翻页看时也有区分）
        box_style = series_style(spec.palette, index)
        if not uses_hatch(spec.palette):
            # 彩色/灰阶下保持半透明，避免多个箱体互相遮挡看不清
            box_style["alpha"] = 0.45
        axes.boxplot([values for _cat, values in non_empty],
                     labels=[cat for cat, _values in non_empty],
                     patch_artist=True,
                     boxprops=dict(**box_style),
                     medianprops=dict(color="#000000", linewidth=1.4),
                     whiskerprops=dict(color="#5A5F66"), capprops=dict(color="#5A5F66"),
                     flierprops=dict(marker="o", markersize=3, alpha=0.5,
                                     markerfacecolor="#1F2329", markeredgecolor="none"))
        axes.set_title(column_label(column), fontproperties=font, fontsize=12)
        axes.set_xlabel(column_label(group), fontproperties=font, fontsize=10)
        if len(non_empty) > 8:
            for label in axes.get_xticklabels():
                label.set_rotation(45)
                label.set_ha("right")

    result = ChartResult(
        figure=figure,
        title=spec.title or describe_chart(spec.chart_type, None, value_columns, group_by=group),
        x_label=column_label(group),
        y_label="",
        notes=notes,
    )
    # 多子图时只设总标题，_style_axes 只作用于第一个子图
    _style_axes(axes_list[0], result, font)
    for axes in axes_list[1:]:
        axes.grid(True, axis="y", linestyle="--", linewidth=0.6, alpha=0.35)
        axes.set_axisbelow(True)
        for label in axes.get_xticklabels() + axes.get_yticklabels():
            label.set_fontproperties(font)
            label.set_fontsize(10)
        for spine in ("top", "right"):
            axes.spines[spine].set_visible(False)
    return result


def _draw_quadrat_bar(df: pd.DataFrame, spec: ChartSpec, figure: Figure,
                      font: Optional[FontProperties]) -> ChartResult:
    """样方统计对比图：分类字段作为分组，各数值字段并排对比。"""
    category = spec.category
    value_columns = [v for v in spec.values if v in df.columns
                     and pd.api.types.is_numeric_dtype(df[v])]
    if category not in df.columns:
        raise PlotError(
            "找不到对比字段「{0}」。\n\n怎么办：在 X 轴下拉框里重新选择一列（例如「物种」或「样方号」）。".format(
                column_label(category)))
    if not value_columns:
        raise PlotError("没有可用的数值字段。\n\n怎么办：在 Y 轴里至少选择一个数值列。")

    # 同一个分类值可能有多行（例如某物种在多个样方出现），先按分类求均值再对比
    grouped = df.groupby(category, dropna=False)[value_columns].mean(numeric_only=True)
    grouped = grouped.dropna(how="all")
    # 按自然顺序排列（Q1, Q2 … Q10）
    ordered = [value for value in natural_sorted(grouped.index.tolist()) if value in grouped.index]
    grouped = grouped.reindex(ordered)
    if grouped.empty:
        raise PlotError("按「{0}」分组后没有可用的数值，无法绘图。".format(column_label(category)))

    notes: List[str] = []
    if len(df) > len(grouped):
        notes.append("同一个「{0}」对应多行数据，图中取的是各组的**平均值**。".format(column_label(category)))

    axes = figure.add_subplot(111)
    labels = [str(value) for value in grouped.index.tolist()]
    positions = np.arange(len(labels))
    count = len(value_columns)
    width = 0.8 / max(count, 1)
    for index, column in enumerate(value_columns):
        offset = (index - (count - 1) / 2.0) * width
        axes.bar(positions + offset, grouped[column].to_numpy(), width=width,
                 label=column_label(column), **series_style(spec.palette, index))
    axes.set_xticks(positions)
    axes.set_xticklabels(labels, rotation=0 if len(labels) <= 12 else 45,
                         ha="center" if len(labels) <= 12 else "right")
    axes.legend(prop=font, frameon=False, ncol=min(len(value_columns), 4))

    result = ChartResult(
        figure=figure,
        title=spec.title or describe_chart(spec.chart_type, category, value_columns),
        x_label=column_label(category),
        y_label=column_label(value_columns[0]) if len(value_columns) == 1 else "平均值",
        notes=notes,
    )
    _style_axes(axes, result, font)
    return result


_DRAWERS = {
    CHART_DIVERSITY_BAR: _draw_diversity_bar,
    CHART_SENSOR_LINE: _draw_sensor_line,
    CHART_COMMUNITY_BOX: _draw_community_box,
    CHART_QUADRAT_BAR: _draw_quadrat_bar,
}


def draw_chart(df: pd.DataFrame, spec: ChartSpec) -> ChartResult:
    """按 ChartSpec 绘图，返回 ChartResult（含 Figure 与中文标题、说明）。

    输入只读：全过程在副本上操作，绝不修改传入的数据表。
    """
    if df is None or len(df) == 0:
        raise PlotError("没有可以绘制的数据。\n\n怎么办：请先在前面几步导入并清洗数据。")
    if spec.chart_type not in _DRAWERS:
        raise PlotError("不支持的图表类型「{0}」。".format(spec.chart_type))

    font, source = resolve_font()
    figure = create_figure()
    result = _DRAWERS[spec.chart_type](df.copy(deep=True), spec, figure, font)
    result.font_source = source

    if not source:
        result.notes.append(
            "当前没有可用的中文字体，图表中的中文可能显示为方框。"
            "请把开源中文字体（如思源黑体）放入 static/fonts 目录后重试。")
    elif source.startswith("系统字体"):
        result.notes.append(
            "当前使用系统字体 {0} 显示中文。换一台电脑可能因缺少该字体而显示为方框，"
            "建议把开源中文字体放入 static/fonts 目录随软件一起分发。".format(source.split(" ", 1)[-1]))
    return result


# ===========================================================================
# 六、导出（300dpi PNG 与 SVG，文件名不覆盖）
# ===========================================================================
_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')


# 分类轴的自然排序辅助
_NATURAL_KEY_PATTERN = re.compile(r"(\d+)")


def natural_sort_key(value: Any) -> Tuple:
    """生成"自然顺序"排序键：让 Q2 排在 Q10 前面，而不是按字符串比较。

    为什么需要：分类轴上的样方号、处理编号常带数字。
    直接按字符串排序会得到 Q1, Q10, Q11, Q2… 这种让科研用户一眼看出"不对"的顺序，
    打印到论文里更是明显错误。
    """
    text = str(value)
    parts = _NATURAL_KEY_PATTERN.split(text)
    key: List[Any] = []
    for index, part in enumerate(parts):
        if index % 2 == 1:
            key.append((1, int(part), ""))       # 数字段：按数值比较，且排在文本前面
        elif part:
            key.append((0, 0, part))             # 文本段：按字符比较
    return tuple(key)


def natural_sorted(values: Sequence[Any]) -> List[Any]:
    """按自然顺序排序分类值（保留原始取值，只调整顺序）。"""
    try:
        return sorted(list(values), key=natural_sort_key)
    except TypeError:  # pragma: no cover - 极端混合类型兜底
        return list(values)


def safe_filename(name: str, fallback: str = "图表") -> str:
    """把中文图表名转成合法文件名（去掉非法字符，长度适中）。"""
    cleaned = _INVALID_FILENAME_CHARS.sub("", str(name or "")).strip().strip(".")
    cleaned = re.sub(r"\s+", "", cleaned)
    if not cleaned:
        cleaned = fallback
    return cleaned[:60]


def build_export_path(directory: Path, title: str, suffix: str,
                      timestamp: Optional[str] = None) -> Path:
    """生成导出路径：中文图表名_YYYYMMDD_HHMMSS.扩展名。

    若同一秒内已存在同名文件，自动追加 _2、_3… 序号，
    保证**绝不静默覆盖**用户已有的图片（方案阶段 5 第 4 条）。
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = timestamp or time.strftime("%Y%m%d_%H%M%S")
    base = "{0}_{1}".format(safe_filename(title), stamp)
    suffix = suffix if suffix.startswith(".") else "." + suffix
    candidate = directory / (base + suffix)
    counter = 2
    while candidate.exists():
        candidate = directory / ("{0}_{1}{2}".format(base, counter, suffix))
        counter += 1
    return candidate


def save_figure(result: ChartResult, directory: Path, formats: Sequence[str] = ("png", "svg"),
                png_dpi: int = 300, timestamp: Optional[str] = None) -> List[Path]:
    """把图保存到目录下，返回实际写入的文件路径列表（阶段 8 起支持格式可选）。

    参数 formats 可传：
        ("png",)        只要位图
        ("svg",)        只要矢量图
        ("png", "svg")  两者都要
    位图格式（png/jpg/jpeg/tif/tiff/bmp/webp）按 png_dpi 输出；
    矢量格式（svg/pdf/eps/ps）不受 dpi 影响，可无损缩放。

    说明：内部会先做去重与排序（见 normalize_formats），
    传入 ("svg","png","svg") 只会导出两个文件，不会重复写同一格式。

    timestamp 一般不用传（默认取当前时间）。显式传入可让同一次导出的
    多个格式共用同一个时间戳，也便于自动化测试断言文件名。
    """
    written: List[Path] = []
    raster = {"png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp"}
    # 多格式共用同一个时间戳：否则跨秒时会得到两个不同的文件名主体
    stamp = timestamp or time.strftime("%Y%m%d_%H%M%S")
    for fmt in normalize_formats(formats):
        suffix = "." + fmt.lstrip(".").lower()
        target = build_export_path(Path(directory), result.title, suffix, timestamp=stamp)
        if suffix == ".svg":
            result.figure.savefig(target, format="svg", bbox_inches="tight",
                                  facecolor="white")
        elif suffix == ".pdf":
            result.figure.savefig(target, format="pdf", bbox_inches="tight",
                                  facecolor="white")
        elif suffix.lstrip(".") in raster:
            result.figure.savefig(target, dpi=png_dpi, bbox_inches="tight",
                                  facecolor="white")
        else:
            result.figure.savefig(target, bbox_inches="tight", facecolor="white")
        written.append(target)
    return written


def normalize_formats(formats: Sequence[str]) -> Tuple[str, ...]:
    """把用户选择的格式整理成合法、去重、有序的元组。

    规则：
        · 去掉前导点、转小写、忽略空白与重复项（传 ("svg","png","svg") 只会得到两个）；
        · 只要涉及 png / svg，就按 png 在前、svg 在后的固定顺序输出，
          保证同一张图无论怎么勾选，文件名与顺序都一致；
        · 其它已知格式（jpg/tif/pdf/eps…）原样保留在后面，便于将来扩展。
    """
    known_order = ("png", "svg", "jpg", "jpeg", "tif", "tiff", "bmp", "webp",
                   "pdf", "eps", "ps")
    wanted = []
    for item in formats:
        name = str(item).strip().lstrip(".").lower()
        if name and name not in wanted:
            wanted.append(name)
    ordered = [fmt for fmt in known_order if fmt in wanted]
    # 未知格式追加在最后，保持"不会因为拼错就静默丢掉用户的选择"
    ordered.extend(fmt for fmt in wanted if fmt not in known_order)
    return tuple(ordered)


def describe_formats(formats: Sequence[str]) -> str:
    """用中文说明本次会导出哪些格式（界面提示用）。"""
    ordered = normalize_formats(formats)
    if not ordered:
        return "未选择输出格式"
    names = {"png": "PNG 位图（300dpi，可直接投稿或插入文档）",
             "svg": "SVG 矢量图（可无损放大，便于后期调整）"}
    return "、".join(names[fmt] for fmt in ordered)


def close_figure(result: Optional[ChartResult]) -> None:
    """释放图对象占用的内存（界面反复绘图时必须调用，否则内存持续增长）。

    用 Figure.clear() 而不是 plt.close()：
        plt.close() 需要先导入 pyplot（约 400 ms），而 clear() 是 Figure 自带方法，
        效果一样（解除对数据的引用、让内存可回收），却不需要拉起整个 pyplot。
        这样"画完一张图再关掉"的路径不会把 pyplot 拖进来。
    """
    if result is None:
        return
    try:
        figure = result.figure
        figure.clear()
    except Exception:  # pragma: no cover
        # 退回 pyplot 方式（例如 figure 不是 matplotlib 的）
        try:
            _lazy_pyplot().close(result.figure)
        except Exception:  # noqa: BLE001
            pass


# ===========================================================================
# 七、图表推荐（方案 5.6 智能默认图表：进页面即可直接点"绘制图表"）
# ===========================================================================
#: 这些列虽然是数值型，但语义是"标识/编号"，不能当作 Y 轴数值来画。
#: 实测踩坑：样方号是整数，推荐图表时被选成数值轴，箱线图画出一排无意义的编号。
_ID_LIKE_COLUMNS: Tuple[str, ...] = ("样方号", "样地号", "编号", "序号", "重复", "id", "index")


def _plottable_numeric(df: pd.DataFrame) -> List[str]:
    """挑出适合作为数值轴的列（排除标识/编号列）。"""
    result: List[str] = []
    for name in (str(c) for c in df.columns):
        if not pd.api.types.is_numeric_dtype(df[name]):
            continue
        if name in _ID_LIKE_COLUMNS or name.lower() in _ID_LIKE_COLUMNS:
            continue
        result.append(name)
    return result


#: 对外别名：界面也用它，保证"什么能当数值轴"只有一处定义。
plottable_numeric = _plottable_numeric


def recommend_spec(df: pd.DataFrame, table_key: str) -> ChartSpec:
    """按数据表的内容推荐图表类型与字段，让用户进页面就能直接出图。

    推荐规则（按数据表的"长相"判断，而不是按表名硬编码）：
        有时间列 + 数值列          → 时序折线图，X = 时间，Y = 前两个数量指标
        有分类列 + 多个数值列       → 箱线图（看分布）
        只有一个数值列但有多行      → 柱状图（看对比）
    """
    columns = [str(name) for name in df.columns]
    numeric = _plottable_numeric(df)
    category_candidates = [name for name in columns if name not in numeric]
    # 样方号/物种/分组这类才是好的分类轴；时间不参与分类
    preferred_categories = [name for name in ("分组", "时间分组", "样方号", "物种", "处理组")
                            if name in category_candidates]
    category = preferred_categories[0] if preferred_categories else (
        category_candidates[0] if category_candidates else None)

    if "时间" in columns and numeric:
        return ChartSpec(table_key=table_key, chart_type=CHART_SENSOR_LINE,
                         category="时间", values=numeric[:2])

    # 汇总表里若同时有 S/N 与多样性指数，优先画多样性对比
    diversity = [name for name in ("H 香农指数", "D 辛普森指数", "J Pielou均匀度",
                                   "R Margalef丰富度") if name in numeric]
    if diversity and category:
        return ChartSpec(table_key=table_key, chart_type=CHART_DIVERSITY_BAR,
                         category=category, values=diversity)

    if "物种" in columns and numeric:
        return ChartSpec(table_key=table_key, chart_type=CHART_COMMUNITY_BOX,
                         category="物种", values=numeric[:2], group_by="物种")

    if category and numeric:
        return ChartSpec(table_key=table_key, chart_type=CHART_QUADRAT_BAR,
                         category=category, values=numeric[:2])

    # 实在没有分类列：把数值列的分布画出来
    return ChartSpec(table_key=table_key, chart_type=CHART_COMMUNITY_BOX,
                     category=columns[0] if columns else None, values=numeric[:2])


def chart_types_for(df: pd.DataFrame) -> List[str]:
    """根据数据表内容列出可用的图表类型（不适用的不列出，避免用户困惑）。"""
    columns = [str(name) for name in df.columns]
    numeric = _plottable_numeric(df)
    available: List[str] = []
    if "时间" in columns and numeric:
        available.append(CHART_SENSOR_LINE)
    has_category = any(name not in numeric for name in columns)
    if has_category and numeric:
        available.extend([CHART_COMMUNITY_BOX, CHART_QUADRAT_BAR])
    diversity = [name for name in ("H 香农指数", "D 辛普森指数", "J Pielou均匀度",
                                   "S 物种数", "N 个体总数") if name in numeric]
    if diversity and has_category:
        available.insert(0, CHART_DIVERSITY_BAR)
    if not available and numeric:
        available.append(CHART_COMMUNITY_BOX)
    # 去重并保持固定顺序
    return [name for name in ALL_CHARTS if name in set(available)]
