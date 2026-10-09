# -*- coding: utf-8 -*-
"""
自动报告生成与批量导出核心（开发方案 第六章 阶段 6）

职责：
    1. 生成固定四章结构的中文 TXT 分析报告；
    2. 把各数据表写成一个 xlsx（多 sheet，中文不乱码）；
    3. 把图片、清洗日志、报告一并写入目标文件夹；
    4. 导出文件名统一为 `任务名_内容类型_YYYYMMDD_HHMMSS.扩展名`；
    5. 导出完成后在同一目录写入一份《成果说明.txt》，逐条说明每个文件是什么、怎么用。

纪律：
    · 本模块只接收数据、写文件，不 import 任何界面模块，也不直接读 AppState，
      因此可以脱离界面完整测试；
    · 所有文本文件一律 UTF-8 带 BOM（utf-8-sig）写出：
      Excel 直接双击打开 csv 时，没有 BOM 会出现中文乱码（这是用户最高频的抱怨）；
    · 绝不静默覆盖：同名文件自动加序号。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from core import data_parse, eco_index, plot_draw
from utils import paths

# ===========================================================================
# 一、导出项定义（与界面勾选框一一对应）
# ===========================================================================
ITEM_CLEAN_TABLE = "清洗数据表"
ITEM_ROW_INDEX = "行级指标表"
ITEM_SUMMARY_INDEX = "汇总指标表"
ITEM_CLEAN_LOG = "清洗日志"
ITEM_FIGURES = "绘图图片"
ITEM_REPORT = "分析报告"

ALL_ITEMS: Tuple[str, ...] = (ITEM_CLEAN_TABLE, ITEM_ROW_INDEX, ITEM_SUMMARY_INDEX,
                              ITEM_CLEAN_LOG, ITEM_FIGURES, ITEM_REPORT)

# 每项在成果说明里的用途描述（用户看不懂文件名时看这里）
ITEM_PURPOSE: Dict[str, str] = {
    ITEM_CLEAN_TABLE: "清洗后的数据表，后续分析的基础。",
    ITEM_ROW_INDEX: "行级指标（每行 = 某样方中的某物种）。",
    ITEM_SUMMARY_INDEX: "汇总指标（每行 = 一个样方或分组）。",
    ITEM_CLEAN_LOG: "清洗日志：改了什么、为什么改，写方法部分可直接引用。",
    ITEM_FIGURES: "分析图表（PNG 300dpi / SVG 矢量图）。",
    ITEM_REPORT: "分析报告（数据信息 / 清洗统计 / 指标汇总 / 质量评价）。",
}

# 文件名里禁止出现的字符
_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')

# 文件名中的时间戳（YYYYMMDD_HHMMSS），用于复制图片时避免出现两个时间戳
_TIMESTAMP_PATTERN = re.compile(r"_?\d{8}_\d{6}")

DEFAULT_TASK_NAME = "生态数据分析"


class ReportError(Exception):
    """导出失败异常：信息为中文，可直接展示给用户。"""


@dataclass
class ExportResult:
    """批量导出结果。"""
    directory: Path
    files: List[Path] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)      # 中文说明（跳过了什么、为什么）
    items: List[str] = field(default_factory=list)      # 实际导出的成果项

    def count(self) -> int:
        return len(self.files)


# ===========================================================================
# 二、命名规范
# ===========================================================================
def safe_name(text: str, fallback: str = "未命名") -> str:
    """把任务名转成安全的文件名片段。"""
    cleaned = _INVALID_FILENAME_CHARS.sub("", str(text or "")).strip().strip(".")
    cleaned = re.sub(r"\s+", "", cleaned)
    return (cleaned or fallback)[:60]


def build_base_name(task_name: str, content_type: str, timestamp: Optional[str] = None) -> str:
    """生成 `任务名_内容类型_YYYYMMDD_HHMMSS` 形式的文件名主体。"""
    stamp = timestamp or time.strftime("%Y%m%d_%H%M%S")
    return "{0}_{1}_{2}".format(safe_name(task_name, DEFAULT_TASK_NAME),
                                safe_name(content_type, "成果"), stamp)


def unique_path(directory: Path, base_name: str, suffix: str) -> Path:
    """在目录下生成不冲突的文件路径：同名时自动追加 _2、_3…，绝不覆盖。"""
    directory = Path(directory)
    suffix = suffix if suffix.startswith(".") else "." + suffix
    candidate = directory / (base_name + suffix)
    counter = 2
    while candidate.exists():
        candidate = directory / ("{0}_{1}{2}".format(base_name, counter, suffix))
        counter += 1
    return candidate


# ===========================================================================
# 三、xlsx 写入（openpyxl，确保中文不乱码）
# ===========================================================================
def _autofit_columns(worksheet: Any, frame: pd.DataFrame, max_width: int = 42) -> None:
    """按内容长度调整列宽，并冻结首行。

    为什么不用 pandas.ExcelWriter 一把梭：列宽、冻结行这些直接影响可用性，
    用户拿到一个每列都只有默认宽度的表，还要自己拖一遍才能看。
    """
    from openpyxl.utils import get_column_letter

    for index, column in enumerate(frame.columns, start=1):
        header = str(column)
        # 中文按 2 个字符宽度估算，避免"列宽按字符数算但中文占两格"导致截断
        width = sum(2 if ord(ch) > 127 else 1 for ch in header) + 2
        sample = frame[column].head(200)
        for value in sample:
            text = "" if value is None or (isinstance(value, float) and value != value) else str(value)
            width = max(width, min(max_width, sum(2 if ord(ch) > 127 else 1 for ch in text) + 2))
        worksheet.column_dimensions[get_column_letter(index)].width = min(max(width, 8), max_width)
    worksheet.freeze_panes = "A2"


def write_tables_to_excel(tables: Sequence[Tuple[str, pd.DataFrame]], target: Path,
                          notes: Optional[List[str]] = None) -> Optional[Path]:
    """把若干 (sheet 名, 数据表) 写入一个 xlsx。

    参数：
        tables ：[(工作表名, 数据表)]，工作表名必须符合 Excel 限制（≤31 字符、不含特殊符号）
        target ：目标文件路径
    返回：写入成功返回路径；没有任何可用数据表时返回 None 并把原因写入 notes。
    """
    if notes is None:
        notes = []
    usable = [(name, frame) for name, frame in tables
              if frame is not None and len(frame) > 0 and len(frame.columns) > 0]
    if not usable:
        notes.append("没有可导出的数据表（可能还没做清洗或指标计算），已跳过 Excel 文件。")
        return None

    from openpyxl import Workbook

    workbook = Workbook()
    # 删掉默认的空表，避免成果里多出一个空白 sheet
    workbook.remove(workbook.active)

    for sheet_name, frame in usable:
        title = _safe_sheet_name(sheet_name, workbook.sheetnames)
        worksheet = workbook.create_sheet(title=title)
        worksheet.append([str(column) for column in frame.columns])
        for row in frame.itertuples(index=False, name=None):
            worksheet.append([_cell_value(value) for value in row])
        _autofit_columns(worksheet, frame)

    Path(target).parent.mkdir(parents=True, exist_ok=True)
    workbook.save(str(target))
    return Path(target)


def _safe_sheet_name(name: str, existing: Sequence[str]) -> str:
    """生成合法的 Excel 工作表名：≤31 字符、去掉 []:*?/\\ 等非法字符、不与已有重名。"""
    cleaned = re.sub(r"[\[\]:*?/\\]", "", str(name)).strip() or "数据"
    cleaned = cleaned[:31]
    candidate = cleaned
    counter = 2
    while candidate in existing:
        suffix = "_{0}".format(counter)
        candidate = cleaned[:31 - len(suffix)] + suffix
        counter += 1
    return candidate


def _cell_value(value: Any) -> Any:
    """把单元格值转成 Excel 能安全写入的类型（NaN 写空、时间写字符串）。"""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, float):
        return None if value != value else value
    if isinstance(value, (pd.Timestamp,)):
        return value.strftime(data_parse.TIME_FORMAT)
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


# ===========================================================================
# 四、分析报告（固定四章，全文中文）
# ===========================================================================
def _format_number(value: Any, digits: int = 4) -> str:
    """把数值格式化成人读的样子（NaN 显示为"—"）。"""
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number != number:      # NaN
        return "—"
    if float(number).is_integer() and abs(number) < 1e15:
        return "{0:,.0f}".format(number)
    return ("{0:." + str(digits) + "f}").format(number)


def _missing_table(df: Optional[pd.DataFrame]) -> List[Tuple[str, int, str]]:
    """统计各列缺失情况，返回 [(列名, 缺失个数, 缺失率)]。"""
    if df is None or len(df) == 0:
        return []
    rows = len(df)
    result: List[Tuple[str, int, str]] = []
    for column in df.columns:
        missing = int(df[column].isna().sum())
        rate = missing / rows * 100.0 if rows else 0.0
        result.append((str(column), missing, "{0:.2f}%".format(rate)))
    return result


def _parse_clean_stats(clean_log: str) -> Dict[str, str]:
    """从清洗日志里抽取关键数字，供报告第二章引用。

    为什么从日志文本里解析而不是要求调用方传对象：
        报告可能在"重新打开历史任务"的场景下生成，那时手上只有落盘的日志文本；
        保持"只依赖日志文本"可以让报告生成与流程解耦。
        解析失败不影响报告生成，只是第二章数字变成"—"。
    """
    stats: Dict[str, str] = {}
    if not clean_log:
        return stats
    patterns = {
        "总行数": r"共读取\s*([\d,]+)\s*行",
        "删除重复": r"删除了\s*([\d,]+)\s*行完全重复",
        "修正异常值": r"修正了\s*([\d,]+)\s*个异常值",
        "填补缺失": r"填补了\s*([\d,]+)\s*个缺失值",
        "保留空白": r"保留了\s*([\d,]+)\s*个空白",
        "清洗后行数": r"清洗后共\s*([\d,]+)\s*行",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, clean_log)
        if match:
            stats[key] = match.group(1)
    # 量纲换算说明
    scale_notes = re.findall(r"「([^」]+)」列为 0~1 小数制，已整列换算为百分制（([\d,]+) 个值）", clean_log)
    if scale_notes:
        stats["量纲换算"] = "；".join("{0}（{1} 个值）".format(name, count) for name, count in scale_notes)
    params = re.search(r"本次使用的参数：突变检测阈值\s*([\d.]+)\s*倍 MAD，最大插值间隙\s*(\d+)\s*个点", clean_log)
    if params:
        stats["参数"] = "突变检测阈值 {0} 倍 MAD，最大插值间隙 {1} 个点".format(params.group(1), params.group(2))
    return stats


def build_report_text(task_name: str, data_type: str = "",
                      raw: Optional[pd.DataFrame] = None,
                      clean: Optional[pd.DataFrame] = None,
                      row_index: Optional[pd.DataFrame] = None,
                      summary_index: Optional[pd.DataFrame] = None,
                      clean_log: str = "",
                      figure_files: Optional[Sequence[Path]] = None,
                      time_span: Optional[Tuple[str, str]] = None,
                      generated_at: Optional[str] = None) -> str:
    """生成固定四章结构的中文分析报告文本。

    四章结构（方案阶段 6 第 1 条）：
        一、数据基本信息（行数、列数、时间跨度）
        二、清洗统计摘要
        三、生态指标汇总（含各指标通俗解释）
        四、数据质量评价（各字段缺失率、异常率、插值占比）
    """
    stamp = generated_at or time.strftime("%Y-%m-%d %H:%M:%S")
    lines: List[str] = []
    add = lines.append

    add("=" * 72)
    add("{0} 数据分析报告".format(paths.APP_TITLE))
    add("=" * 72)
    add("任务名称：{0}".format(task_name or DEFAULT_TASK_NAME))
    add("生成时间：{0}".format(stamp))
    add("数据类型：{0}".format(data_type or "未标注"))
    add("生成软件：{0}（离线单机版）".format(paths.APP_TITLE))
    add("")

    # ---------------- 一、数据基本信息 ----------------
    add("-" * 72)
    add("一、数据基本信息")
    add("-" * 72)
    if raw is not None and len(raw):
        add("原始数据：共 {0:,} 行 × {1} 列。".format(len(raw), len(raw.columns)))
        add("数据字段：{0}。".format("、".join(str(column) for column in raw.columns)))
    else:
        add("原始数据：本次报告未包含原始数据表。")
    if clean is not None and len(clean):
        add("参与分析的数据：共 {0:,} 行 × {1} 列。".format(len(clean), len(clean.columns)))
    if time_span:
        add("时间跨度：{0} 至 {1}。".format(time_span[0], time_span[1]))
    elif clean is not None and "时间" in getattr(clean, "columns", []):
        parsed = pd.to_datetime(clean["时间"], errors="coerce").dropna()
        if not parsed.empty:
            add("时间跨度：{0} 至 {1}。".format(
                parsed.min().strftime(data_parse.TIME_FORMAT),
                parsed.max().strftime(data_parse.TIME_FORMAT)))
    else:
        add("时间跨度：本数据不含时间列（样方调查或实验统计数据属正常情况）。")
    add("")

    # ---------------- 二、清洗统计摘要 ----------------
    add("-" * 72)
    add("二、清洗统计摘要")
    add("-" * 72)
    stats = _parse_clean_stats(clean_log)
    if stats:
        if "总行数" in stats:
            add("读取行数：{0} 行。".format(stats["总行数"]))
        if "删除重复" in stats:
            add("删除完全重复记录：{0} 行（保留首次出现）。".format(stats["删除重复"]))
        if "修正异常值" in stats:
            add("修正异常值：{0} 个（株数为负、盖度或湿度超过 100%，以及时序单点突变）。".format(
                stats["修正异常值"]))
        if "填补缺失" in stats:
            add("填补缺失值：{0} 个（时序线性插值、样方分组中位数，计数列已取整）。".format(stats["填补缺失"]))
        if "保留空白" in stats:
            add("保留空白：{0} 个（连续缺测超过最大插值间隙，按规范不插值，避免凭空造数据）。".format(
                stats["保留空白"]))
        if "量纲换算" in stats:
            add("量纲统一：{0}。".format(stats["量纲换算"]))
        if "清洗后行数" in stats:
            add("清洗后行数：{0} 行。".format(stats["清洗后行数"]))
        if "参数" in stats:
            add("清洗参数：{0}。".format(stats["参数"]))
        add("")
        add("完整清洗过程（逐条明细含行号、列名、原值与处理方式）见同目录下的「清洗日志」文件。")
    else:
        add("本次导出没有附带清洗日志，无法给出清洗统计摘要。")
        add("说明：若你跳过了清洗步骤直接导出，属正常情况。")
    add("")

    # ---------------- 三、生态指标汇总 ----------------
    add("-" * 72)
    add("三、生态指标汇总")
    add("-" * 72)

    if summary_index is not None and len(summary_index):
        add("【汇总指标】共 {0:,} 行，每行代表一个样方或一个统计分组。".format(len(summary_index)))
        add("")
        add("① 群落多样性（按样方统计）")
        available = [name for name in eco_index.SUMMARY_METRIC_COLUMNS
                     if name in summary_index.columns]
        if available:
            for name in available:
                series = pd.to_numeric(summary_index[name], errors="coerce")
                valid = series.dropna()
                if valid.empty:
                    add("   · {0}：本次数据无法计算（原因见报告第四章）。".format(name))
                    continue
                add("   · {0}：平均 {1}，范围 {2} ~ {3}。".format(
                    name, _format_number(valid.mean()), _format_number(valid.min()),
                    _format_number(valid.max())))
        else:
            add("   · 本次汇总指标表里没有多样性指标列。")

        # 逐条给出指标的通俗解释（写论文时可直接引用）
        add("")
        add("② 指标含义说明")
        for name in available:
            description = eco_index.METRIC_DESCRIPTIONS.get(name)
            if description:
                add("   · {0}".format(description))
        add("")
        add("③ 环境时序统计（若数据为传感器监测）")
        env_columns = [name for name in summary_index.columns
                       if "昼夜均值差" in str(name) or "均值" in str(name)]
        if env_columns:
            for name in env_columns[:8]:
                series = pd.to_numeric(summary_index[name], errors="coerce").dropna()
                if not series.empty:
                    add("   · {0}：平均 {1}，范围 {2} ~ {3}。".format(
                        name, _format_number(series.mean()), _format_number(series.min()),
                        _format_number(series.max())))
            if len(env_columns) > 8:
                add("   · （另有 {0} 个统计列，详见汇总指标表）".format(len(env_columns) - 8))
        else:
            add("   · 本次未计算环境时序统计。")
    else:
        add("本次导出不包含汇总指标表。")
        add("说明：如需多样性指数等结果，请在【指标计算】页勾选「多样性指标」后重新计算。")
    add("")

    if row_index is not None and len(row_index):
        add("【行级指标】共 {0:,} 行，每行代表某样方中的某物种。".format(len(row_index)))
        if "重要值" in row_index.columns and "物种" in row_index.columns:
            ranked = (row_index.groupby("物种")["重要值"].mean()
                      .sort_values(ascending=False).head(10))
            add("按平均重要值排序的优势物种（前 {0} 位）：".format(len(ranked)))
            for position, (species, value) in enumerate(ranked.items(), start=1):
                add("   {0:>2}. {1}：重要值 {2}".format(position, species, _format_number(value)))
            add("")
            add("重要值 = (相对密度 + 相对频度 + 相对优势度) / 3，"
                "综合数量、分布与长势三项，是判断群落优势种最常用的指标。")
            add("相对密度与相对优势度在各样方内计算（同一样方各物种合计 100%），"
                "相对频度按物种跨样方统计（同物种各行取同值）。")
            add("没有胸径记录的行（例如灌木、草本）优势度留空，其重要值只由相对密度与相对频度两项贡献。")
        for name in ("密度", "频度", "优势度"):
            if name in row_index.columns:
                series = pd.to_numeric(row_index[name], errors="coerce").dropna()
                if not series.empty:
                    add("   · {0}：平均 {1}，范围 {2} ~ {3}。".format(
                        name, _format_number(series.mean()), _format_number(series.min()),
                        _format_number(series.max())))
    else:
        add("本次导出不包含行级指标表。")
    add("")

    # ---------------- 四、数据质量评价 ----------------
    add("-" * 72)
    add("四、数据质量评价")
    add("-" * 72)
    source = clean if clean is not None else raw
    if source is not None and len(source):
        rows = len(source)
        add("评价对象：{0}（共 {1:,} 行 × {2} 列）。".format(
            "清洗后数据" if clean is not None else "原始数据", rows, len(source.columns)))
        add("")
        add("各字段缺失情况：")
        missing = _missing_table(source)
        for name, count, rate in missing:
            flag = ""
            if count:
                flag = "　← 该列存在空白，见下方说明" if rate != "0.00%" else ""
            add("   · {0}：空白 {1} 个（{2}）{3}".format(name, count, rate, flag))

        total_missing = int(source.isna().sum().sum())
        total_cells = rows * len(source.columns)
        add("")
        add("整体缺失率：{0:.2f}%（{1} / {2} 个单元格）。".format(
            total_missing / total_cells * 100.0 if total_cells else 0.0, total_missing, total_cells))

        # 异常率与插值占比：优先用清洗日志里的口径，保证与第二章数字一致
        outliers = int(str(stats.get("修正异常值", "0")).replace(",", "") or 0)
        filled = int(str(stats.get("填补缺失", "0")).replace(",", "") or 0)
        kept = int(str(stats.get("保留空白", "0")).replace(",", "") or 0)
        add("异常值处理：共修正 {0} 个，占全部单元格的 {1:.2f}%。".format(
            outliers, outliers / total_cells * 100.0 if total_cells else 0.0))
        add("插值与填补：共填补 {0} 个值，占全部单元格的 {1:.2f}%。".format(
            filled, filled / total_cells * 100.0 if total_cells else 0.0))
        if kept:
            add("保留空白：{0} 个值因连续缺测过长而保留空白，"
                "这部分数据在计算指标时会被跳过，请评估其对结论的影响。".format(kept))
        add("")

        add("需要留意的情况：")
        notes: List[str] = []
        structural = [name for name, count, _rate in missing if count]
        if structural:
            notes.append("下列列存在空白：{0}。若这些是「该物种本来就没有的测量项」"
                         "（例如灌木、草本没有胸径），属正常情况，软件未用其它物种的数值顶替；"
                         "这些空白在计算优势度等指标时会留空，不会被当成 0。".format(
                             "、".join(structural)))
        # 只有确实什么都没改、且没有结构性空白时，才说"数据是干净的" —— 否则会与上一段自相矛盾
        if outliers == 0 and filled == 0 and not structural:
            notes.append("本次清洗未发现异常值、缺失值与重复记录，数据本身是干净的。")
        elif outliers == 0 and filled == 0:
            notes.append("本次清洗未发现异常值、重复记录，也没有需要填补的缺失值；"
                         "上表列出的空白属于该项本身没有观测，已按规范保留。")
        if not notes:
            notes.append("未发现明显的质量问题。")
        for note in notes:
            add("   · {0}".format(note))
    else:
        add("本次导出没有数据表，无法给出质量评价。")
    add("")

    # ---------------- 附：图表清单 ----------------
    if figure_files:
        add("-" * 72)
        add("附：本次导出的图表")
        add("-" * 72)
        for path in figure_files:
            add("   · {0}".format(Path(path).name))
        add("")
        # 按实际导出的扩展名说明用途：用户只选了 SVG 时不该再提 PNG
        suffixes = {Path(path).suffix.lower() for path in figure_files}
        notes: List[str] = []
        if ".png" in suffixes:
            notes.append("PNG 为 300dpi 位图，可直接用于投稿或汇报")
        if ".svg" in suffixes:
            notes.append("SVG 为矢量图，可在 Illustrator、Inkscape 等软件里继续调整字号与配色")
        if notes:
            add("；".join(notes) + "。")
        add("")

    add("=" * 72)
    add("报告结束。本报告由软件自动生成，数值均可回溯至同目录下的数据表与日志文件。")
    add("=" * 72)
    return "\n".join(lines)


# ===========================================================================
# 五、成果说明
# ===========================================================================
def build_readme_text(task_name: str, exported: Dict[str, List[Path]],
                      directory: Path, notes: Optional[Sequence[str]] = None,
                      generated_at: Optional[str] = None) -> str:
    """生成《成果说明.txt》：逐条说明每个文件是什么、怎么用。"""
    stamp = generated_at or time.strftime("%Y-%m-%d %H:%M:%S")
    lines: List[str] = []
    add = lines.append

    add("=" * 72)
    add("成果说明")
    add("=" * 72)
    add("任务名称：{0}".format(task_name or DEFAULT_TASK_NAME))
    add("导出时间：{0}".format(stamp))
    add("导出位置：{0}".format(directory))
    add("")
    add("本次共导出 {0} 个文件。下面逐条说明每个文件是什么、怎么用。".format(
        sum(len(files) for files in exported.values())))
    add("")

    for item in ALL_ITEMS:
        files = exported.get(item) or []
        if not files:
            continue
        add("-" * 72)
        add("【{0}】".format(item))
        add("用途：{0}".format(ITEM_PURPOSE.get(item, "")))
        add("文件：")
        for path in files:
            add("   · {0}".format(Path(path).name))
        add("")

    if notes:
        add("-" * 72)
        add("导出说明与温馨提示")
        add("-" * 72)
        for note in notes:
            add("   · {0}".format(note))
        add("")

    add("-" * 72)
    add("怎么打开这些文件")
    add("-" * 72)
    add("   · .xlsx：用 Excel 或 WPS 双击打开即可，各数据表在工作簿底部按工作表分开。")
    add("   · .csv：用 Excel 打开时若出现中文乱码，说明该文件不是 UTF-8 编码；"
        "本软件导出的 csv 均为「UTF-8 带 BOM」，正常不会乱码。")
    add("   · .txt：用记事本打开即可，编码为 UTF-8。")
    add("   · .png：图片文件，双击查看；300dpi，可直接插入论文。")
    add("   · .svg：矢量图，可用浏览器查看，或用 Illustrator / Inkscape 编辑"
        "（放大不失真，适合投稿与后期改配色）。")
    add("")
    add("=" * 72)
    add("说明结束。如需重新分析，请在软件里调整参数后重新导出，不会覆盖本次成果。")
    add("=" * 72)
    return "\n".join(lines)


# ===========================================================================
# 六、批量导出主入口
# ===========================================================================
def export_all(directory: Path, task_name: str = DEFAULT_TASK_NAME,
               items: Optional[Iterable[str]] = None,
               raw: Optional[pd.DataFrame] = None,
               clean: Optional[pd.DataFrame] = None,
               row_index: Optional[pd.DataFrame] = None,
               summary_index: Optional[pd.DataFrame] = None,
               clean_log: str = "",
               data_type: str = "",
               figure_files: Optional[Sequence[Any]] = None,
               timestamp: Optional[str] = None) -> ExportResult:
    """把选中的成果项批量导出到指定目录。

    参数 items 为空表示导出全部项（方案 5.6「导出即所得」：默认全选）。
    返回 ExportResult，包含实际写入的文件与中文说明。
    """
    target_dir = Path(directory)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ReportError(
            "无法创建输出文件夹：{0}\n\n原因：{1}\n\n"
            "怎么办：换一个可写的目录（例如「我的文档」下的文件夹）后重试。".format(
                target_dir, exc))

    selected = [item for item in (items if items is not None else ALL_ITEMS) if item in ALL_ITEMS]
    result = ExportResult(directory=target_dir, items=list(selected))
    exported: Dict[str, List[Path]] = {item: [] for item in ALL_ITEMS}
    stamp = timestamp or time.strftime("%Y%m%d_%H%M%S")

    # ---- 1. 数据表与指标表 → 一个 xlsx（多 sheet）----
    table_sheets: List[Tuple[str, pd.DataFrame]] = []
    if ITEM_CLEAN_TABLE in selected and clean is not None and len(clean):
        table_sheets.append(("清洗后数据", clean))
    if ITEM_ROW_INDEX in selected and row_index is not None and len(row_index):
        table_sheets.append(("行级指标", row_index))
    if ITEM_SUMMARY_INDEX in selected and summary_index is not None and len(summary_index):
        table_sheets.append(("汇总指标", summary_index))

    if table_sheets:
        base = build_base_name(task_name, "数据表", stamp)
        excel_path = unique_path(target_dir, base, ".xlsx")
        try:
            written = write_tables_to_excel(table_sheets, excel_path, result.notes)
        except PermissionError:
            raise ReportError(
                "无法写入文件：{0}\n\n原因：该文件可能正在 Excel 中打开。\n\n"
                "怎么办：关闭 Excel 后重新导出。".format(excel_path.name))
        except Exception as exc:  # noqa: BLE001
            raise ReportError(
                "写出 Excel 文件失败：{0}\n\n原因：{1}\n\n"
                "怎么办：换一个输出目录后重试。".format(excel_path.name, exc))
        if written is not None:
            result.files.append(written)
            for name, _frame in table_sheets:
                if name == "清洗后数据":
                    exported[ITEM_CLEAN_TABLE].append(written)
                elif name == "行级指标":
                    exported[ITEM_ROW_INDEX].append(written)
                elif name == "汇总指标":
                    exported[ITEM_SUMMARY_INDEX].append(written)
    elif ITEM_CLEAN_TABLE in selected or ITEM_ROW_INDEX in selected or ITEM_SUMMARY_INDEX in selected:
        result.notes.append("没有可导出的数据表（可能还没做清洗或指标计算），已跳过 Excel 文件。")

    # ---- 2. 清洗日志 ----
    if ITEM_CLEAN_LOG in selected:
        if clean_log and clean_log.strip():
            base = build_base_name(task_name, "清洗日志", stamp)
            log_path = unique_path(target_dir, base, ".txt")
            _write_text(log_path, clean_log)
            result.files.append(log_path)
            exported[ITEM_CLEAN_LOG].append(log_path)
        else:
            result.notes.append("本次没有清洗日志（未执行清洗或日志为空），已跳过该文件。")

    # ---- 3. 图片 ----
    copied_figures: List[Path] = []
    if ITEM_FIGURES in selected:
        sources = [Path(path) for path in (figure_files or [])]
        if not sources:
            result.notes.append("还没有导出过图片，已跳过图片复制。"
                                "如需图片，请先到【可视化绘图】页点【导出图片】。")
        for source in sources:
            if not source.exists():
                result.notes.append("图片文件已不存在，已跳过：{0}".format(source.name))
                continue
            # 图片名沿用"图表名_时间戳"：图表名本身已经说明了画的是什么，
            # 再拼一次任务名会得到又长又重复的文件名，反而不好认。
            target = unique_path(target_dir,
                                 build_base_name(source.stem, "图表", stamp),
                                 source.suffix)
            # 若目标与源是同一个文件（图片就导出在这个目录里），不要复制成两份
            if source.resolve() == target.resolve():
                copied_figures.append(source)
                continue
            # 源文件本身已经带有时间戳时，避免出现两个时间戳
            target = _avoid_duplicate_stamp(source, target_dir, stamp)
            _copy_file(source, target)
            copied_figures.append(target)
        if copied_figures:
            result.files.extend(copied_figures)
            exported[ITEM_FIGURES].extend(copied_figures)

    # ---- 4. 分析报告 ----
    if ITEM_REPORT in selected:
        report_text = build_report_text(
            task_name=task_name, data_type=data_type, raw=raw, clean=clean,
            row_index=row_index, summary_index=summary_index, clean_log=clean_log,
            figure_files=copied_figures)
        base = build_base_name(task_name, "分析报告", stamp)
        report_path = unique_path(target_dir, base, ".txt")
        _write_text(report_path, report_text)
        result.files.append(report_path)
        exported[ITEM_REPORT].append(report_path)

    # ---- 5. 成果说明 ----
    readme_notes = list(result.notes)
    readme_notes.append("导出文件名统一为「任务名_内容类型_时间戳」，"
                        "因此重复导出不会覆盖之前的成果。")
    readme = build_readme_text(task_name, exported, target_dir, notes=readme_notes)
    readme_path = target_dir / "成果说明.txt"
    if readme_path.exists():
        readme_path = unique_path(target_dir, "成果说明", ".txt")
    _write_text(readme_path, readme)
    result.files.append(readme_path)

    if not result.files:
        result.notes.append("没有勾选任何成果项，因此没有导出文件。")
    return result


def _write_text(path: Path, text: str) -> None:
    """写文本文件：统一 UTF-8 带 BOM（Excel / 记事本打开中文都不乱码）。

    注意：这里用 open() 而不是 Path.write_text(newline=...)——
    后者直到 Python 3.10 才支持 newline 参数，本项目要兼容 3.9。
    显式 newline="" 是为了避免 Windows 下 \n 被转成 \r\n 后
    与文本里已有的换行叠加成空行。
    """
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        handle.write(text)


def _copy_file(source: Path, target: Path) -> None:
    """复制文件（用二进制方式，避免图片被当文本处理损坏）。"""
    import shutil

    shutil.copyfile(source, target)


def _avoid_duplicate_stamp(source: Path, directory: Path, stamp: str) -> Path:
    """生成图片的目标文件名，且保证文件名里只有一个时间戳。

    实测踩坑：绘图页导出的图片名已经是「图表名_时间戳」，
    但"保存图片"与"导出成果"往往不是同一秒（本次实测 200652 与 200653），
    于是"源名里是否含本次时间戳"的判断会失手，
    复制后得到「图表名_时间戳_图表_时间戳.png」这种又长又重复的文件名。
    因此这里先把源文件名里**任意位置的** YYYYMMDD_HHMMSS 一律去掉，
    再按统一规范拼一次时间戳。
    """
    stem = _TIMESTAMP_PATTERN.sub("", source.stem).strip("_ ")
    stem = re.sub(r"_+", "_", stem).strip("_ ")
    if not stem:
        stem = "图表"
    return unique_path(directory, build_base_name(stem, "图表", stamp), source.suffix)
