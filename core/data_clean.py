# -*- coding: utf-8 -*-
"""
数据清洗核心逻辑（开发方案 第六章 阶段 3）

职责：
    把"能读进来但还不能用"的原始数据，变成"可以直接算指标"的干净数据，
    并把每一次修改都记成中文日志（含具体位置、原值、处理方式）。

执行顺序（顺序不可调整，每一步都依赖前一步的结果）：
    0. 校验输入
    1. 量纲一致性检测与统一   ← 必须在"盖度 > 100 判异常"之前做
    2. 生态学异常值检测与修正（规则法）
    3. 时序突变检测（MAD 稳健统计，按日分组）
    4. 自动去重
    5. 缺失值填补（时序线性插值 + max_gap；样方分组中位数 + 计数列取整）
    6. 生成中文日志与明细表

关键纪律（方案第三章 3.3 第 4 条）：
    **输入只读、输出新对象**。全流程没有一处 inplace 修改，
    否则用户调整参数重跑时会在已插值的数据上二次插值，误差累积且不可逆。

为什么这些规则要这样定（方案第九章避坑表）：
    避坑 6：0-1 制盖度必须先统一量纲，否则整列误判或全部漏判；
    避坑 7：线性插值必须设 max_gap，传感器坏一周还插值等于凭空伪造数据；
    避坑 8：生态数据非正态且具季节性，用 3σ 会大量误杀真实极值，必须用 MAD；
    避坑 9：株数用均值填充会出现"3.7 株"，必须用分组中位数并对计数列取整。
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from core import data_parse

# ===========================================================================
# 一、常量与参数
# ===========================================================================

ACTION_DELETE_DUPLICATE = "删除完全重复的记录"
ACTION_FIX_OUTLIER = "修正异常值"
ACTION_UNIFY_SCALE = "统一量纲"
ACTION_FILL_INTERPOLATE = "线性插值填补"
ACTION_FILL_MEDIAN = "分组中位数填补"
ACTION_FILL_SPECIES_MEDIAN = "同物种中位数填补"
ACTION_FILL_PLOT_MEDIAN = "同样方中位数填补"
ACTION_FILL_COLUMN_MEDIAN = "按整列中位数填补"
ACTION_KEEP_STRUCTURAL = "该项无观测，保留空白"
ACTION_KEEP_BLANK = "缺口过长，保留空白"
ACTION_FILL_MODE = "按众数填补"

# 需要按"0~1 还是 0~100"统一量纲的比率型字段
RATIO_FIELDS: Tuple[str, ...] = ("盖度", "相对湿度")

# 判为异常的物理边界
COVERAGE_MAX = 100.0        # 盖度、相对湿度不得超过 100%
# 注意：这里的「高度」「株高」同时也是标准字段「树高」的别名，
#       所以用户表头写「树高」时，负值同样会被判异常（列名已标准化为 树高，
#       下面这几个别名只对未标准化的普通统计表生效）。
NON_NEGATIVE_FIELDS: Tuple[str, ...] = ("株数", "胸径", "盖度", "气温", "相对湿度", "土壤温度",
                                        "光合有效辐射", "个体总数", "高度", "株高", "生物量",
                                        "鲜重", "干重", "温度", "树高", "基径", "冠幅",
                                        "地径", "冠径")

# 填补后必须取整的计数类字段（避免出现"3.7 株"）
COUNT_FIELDS: Tuple[str, ...] = ("株数", "个体总数", "重复", "物种数", "萌发数")

# 数值列判定为"比率型、需检查量纲"的触发条件：整列最大值不超过该值即视为 0~1 制
DECIMAL_SCALE_MAX = 1.001

# 默认参数（方案 5.4：默认参数必须开箱即用）
DEFAULT_MAD_K = 3.0
DEFAULT_MAX_GAP = 3
MAD_MIN_SAMPLES = 8         # 少于这么多有效点就不做突变检测，避免小样本误判


# ===========================================================================
# 二、数据结构
# ===========================================================================
@dataclass
class CleanChange:
    """一条清洗改动记录（界面明细表与日志共用）。"""
    row: Optional[int]      # 原始行号（1 起算，与表格纵向表头一致）；整行删除时为 None
    column: str             # 列名
    old_value: Any          # 原值
    new_value: Any          # 处理后的值（删除整行时为 None）
    action: str             # 中文处理方式


@dataclass
class CleanStats:
    """清洗结果统计（日志摘要与界面提示都用它）。"""
    rows_in: int = 0                     # 原始行数
    rows_out: int = 0                    # 清洗后行数
    duplicates_removed: int = 0          # 删除的完全重复行数
    outliers_fixed: int = 0              # 修正的异常值个数
    outlier_breakdown: Dict[str, int] = field(default_factory=dict)   # 按原因分类的异常计数
    missing_filled: int = 0              # 填补的缺失值个数
    interpolated: int = 0                # 其中线性插值填补的个数
    median_filled: int = 0               # 其中"同一样方同一物种"中位数填补的个数
    species_filled: int = 0              # 其中参考"同一物种"填补的个数
    plot_filled: int = 0                 # 其中参考"同一样方"填补的个数
    column_filled: int = 0               # 其中按"整列中位数"填补的个数（无分组信息时）
    mode_filled: int = 0                 # 其中按众数填补的个数（文本列）
    blanks_kept: int = 0                 # 因缺口过长而保留的空白个数
    gaps_kept: int = 0                   # 保留下来的缺口段数
    scale_converted: Dict[str, int] = field(default_factory=dict)     # 量纲换算：列名 -> 换算个数
    columns_skipped: List[str] = field(default_factory=list)          # 无法处理的列及原因

    @property
    def total_changes(self) -> int:
        """改动总条数（不含整行删除）。"""
        return self.outliers_fixed + self.missing_filled


@dataclass
class CleanResult:
    """清洗结果。"""
    df: pd.DataFrame                              # 清洗后的新数据表（绝不复用输入对象）
    log: str                                      # 中文日志文本
    changes: pd.DataFrame                         # 明细表：行号/列名/原值/处理方式
    stats: CleanStats
    params: Dict[str, Any] = field(default_factory=dict)   # 实际生效的参数，供界面回显

    def has_changes(self) -> bool:
        return self.stats.duplicates_removed + self.stats.outliers_fixed + self.stats.missing_filled > 0


class CleanError(Exception):
    """清洗失败异常：信息为中文，可直接展示给用户。"""


# ===========================================================================
# 三、工具函数
# ===========================================================================
def _is_num(value: Any) -> bool:
    """判断是否为可参与计算的实数（排除 NaN / NaT / None / 布尔）。"""
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, (int, float, np.integer, np.floating)):
        return not (isinstance(value, float) and math.isnan(value))
    return False


def _to_number(value: Any) -> Optional[float]:
    """把值安全地转成 float；失败返回 None。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _fmt(value: Any) -> str:
    """把值格式化成日志里好读的样子。"""
    if value is None:
        return "（空）"
    if isinstance(value, float):
        if math.isnan(value):
            return "（空）"
        if float(value).is_integer():
            return str(int(value))
        return ("{0:.4f}".format(value)).rstrip("0").rstrip(".")
    if isinstance(value, (np.integer,)):
        return str(int(value))
    if isinstance(value, (np.floating,)):
        return _fmt(float(value))
    return str(value)


def _numeric_columns(df: pd.DataFrame) -> List[str]:
    """列出数据表里的数值列。"""
    return [str(col) for col in df.columns if pd.api.types.is_numeric_dtype(df[col])]


def _time_series_mask(df: pd.DataFrame) -> Optional[pd.Series]:
    """判断是否为可用时序数据，返回解析后的时间序列（不可用时返回 None）。"""
    if "时间" not in df.columns:
        return None
    parsed = pd.to_datetime(df["时间"], format=data_parse.TIME_FORMAT, errors="coerce")
    return parsed if parsed.notna().sum() >= 2 else None


def _group_keys(df: pd.DataFrame) -> Optional[List[str]]:
    """样方群落数据的分组键：样方号 + 物种（方案阶段 3 第 5 条）。

    只有在两者都存在时才作为分组依据；否则退回 None（按整列统计），
    避免用半个键分组得出没有意义的"中位数"。
    """
    if "样方号" in df.columns and "物种" in df.columns:
        return ["样方号", "物种"]
    return None


# ===========================================================================
# 四、步骤 1：量纲一致性检测与统一
# ===========================================================================
def unify_ratio_scale(df: pd.DataFrame, columns: Sequence[str] = RATIO_FIELDS
                      ) -> Tuple[pd.DataFrame, Dict[str, int], List[str]]:
    """把比率型字段统一成百分制（0~100）。

    判定规则（方案阶段 3 第 1 条）：
        整列有效值的最大值 ≤ 1.001 → 判为小数制（0~1），整列 ×100；
        否则视为已经是百分制，不动。

    为什么必须先做这一步：
        跳过它，"盖度 > 100 判异常"在小数制数据上会全部漏判，
        而在混合量纲的数据上又会把 0.62 这种正常值当成异常。
    """
    result = df.copy()
    converted: Dict[str, int] = {}
    notes: List[str] = []

    for column in columns:
        if column not in result.columns:
            continue
        series = pd.to_numeric(result[column], errors="coerce")
        valid = series.dropna()
        if valid.empty:
            continue
        maximum = float(valid.max())
        minimum = float(valid.min())
        # 只有"整列都落在 0~1 且不是全 0"才判定为小数制，避免误伤真正的 1% 盖度
        if maximum <= DECIMAL_SCALE_MAX and maximum > 0 and minimum >= 0:
            count = int(valid.size)
            result[column] = series * 100.0
            converted[column] = count
            notes.append(
                "「{0}」列的最大值只有 {1}，判断为 0~1 小数制，已整列换算为百分制（{2} 个值）。".format(
                    column, _fmt(maximum), count))
    return result, converted, notes


# ===========================================================================
# 五、步骤 2：生态学异常值检测
# ===========================================================================
def detect_rule_outliers(df: pd.DataFrame) -> List[Tuple[int, str, Any, str]]:
    """按生态学规则找出异常单元格，返回 [(行序号, 列名, 原值, 原因)]。

    规则（方案阶段 3 第 2 条，在量纲统一之后执行）：
        · 株数、胸径、盖度、湿度等数值字段出现负数 → 异常；
        · 盖度、相对湿度 > 100 → 异常。
    不做的事情：
        · 不对传感器正常的高温/低温值做判断（那属于突变检测，见 detect_spikes）；
        · 不在这里修改数据，只负责"找出来"，由调用方决定怎么处理。
    """
    findings: List[Tuple[int, str, Any, str]] = []
    for column in df.columns:
        name = str(column)
        if not pd.api.types.is_numeric_dtype(df[column]):
            continue
        series = pd.to_numeric(df[column], errors="coerce")

        # 负数检查
        if name in NON_NEGATIVE_FIELDS:
            negative = series < 0
            for position in np.flatnonzero(negative.to_numpy()):
                findings.append((int(position), name, series.iloc[position],
                                 "「{0}」不允许为负数".format(name)))

        # 超过 100% 检查（量纲已统一，此处判断才可靠）
        if name in RATIO_FIELDS:
            over = series > COVERAGE_MAX
            for position in np.flatnonzero(over.to_numpy()):
                findings.append((int(position), name, series.iloc[position],
                                 "「{0}」是百分比，不应超过 100%".format(name)))
    return findings


# ===========================================================================
# 六、步骤 3：时序突变检测（MAD 稳健统计）
# ===========================================================================
def detect_spikes(df: pd.DataFrame, mad_k: float = DEFAULT_MAD_K,
                  time_series: Optional[pd.Series] = None) -> List[Tuple[int, str, Any, str]]:
    """用 MAD 检测时序数据的单点突变，返回 [(行序号, 列名, 原值, 原因)]。

    为什么用 MAD 而不是 3σ（方案避坑 8）：
        生态数据非正态且具季节性，标准差会被极值本身放大，用 3σ 要么漏杀、
        要么把季节性峰值一起误杀。MAD 以中位数为基准，对极值不敏感。

    为什么按"日"分组统计（本实现的关键决定）：
        气温的日变化幅度本身就有 10 ℃ 以上。若整列一起算，白天高温会被当成突变；
        若按小时分组，每组样本太少。按日分组既保留了日变化规律，
        又能把"某天里孤立出现的 58.7 ℃"准确识别出来。
    """
    findings: List[Tuple[int, str, Any, str]] = []
    if time_series is None:
        time_series = _time_series_mask(df)
    if time_series is None:
        return findings

    groups = time_series.dt.date
    for column in df.columns:
        name = str(column)
        if name == "时间" or not pd.api.types.is_numeric_dtype(df[column]):
            continue
        series = pd.to_numeric(df[column], errors="coerce")

        for _day, index in series.groupby(groups).groups.items():
            block = series.loc[index]
            valid = block.dropna()
            if valid.size < MAD_MIN_SAMPLES:
                # 样本太少时 MAD 不稳定，宁可不判也不要误杀
                continue
            median = float(valid.median())
            deviation = (valid - median).abs()
            mad = float(deviation.median())
            # 1.4826 是把 MAD 折算成"等效标准差"的常数
            scale = 1.4826 * mad
            if scale <= 0:
                # 整组 MAD 为 0，有两种情况：
                #   ① 所有值完全相同（例如光合有效辐射夜间恒为 0）→ 没有噪声，也就无从判断突变，
                #      必须直接跳过，否则会把每一个点都判成异常；
                #   ② 绝大多数点相同、只有个别孤立尖峰（传感器卡值后突然跳变）
                #      → MAD 被大量相同值压成 0，但这类恰恰是必须抓出来的故障。
                if float(valid.max()) == float(valid.min()):
                    continue
                # 用"相邻点差值的中位数"作为噪声尺度：
                # 为什么不用"非零偏差的中位数"——只有一个尖峰时，那个非零偏差就是尖峰本身，
                # 会把尺度抬到与尖峰同量级，结果什么都检测不出来（实测踩坑）。
                # 相邻差值则不受单个尖峰主导：尖峰只影响两个差值为大数，
                # 其余差值仍反映真实噪声水平。
                diffs = valid.diff().abs().dropna()
                scale = 1.4826 * float(diffs.median()) if not diffs.empty else 0.0
                if scale <= 0:
                    # ③ 连相邻差值的中位数也是 0：说明绝大多数相邻点完全相同，
                    #    只有极个别孤立的跳变（典型故障：传感器卡住后突然跳一下）。
                    #    这种情况下任何"基于离散度"的尺度都会退化成 0，
                    #    因此不能用"偏离多少倍"来判断，只能判断它是否**孤立**：
                    #    把当天所有取值排序，若最大的那个间隔远大于其它间隔，
                    #    说明数据分成"一大簇 + 几个离群点"，这些离群点就是疑似故障值。
                    #    这比设一个绝对阈值更可靠：温度 58.7 ℃ 和湿度 500% 都能被抓出来，
                    #    而正常的日变化（连续变化、不分簇）不会触发。
                    if float(valid.max()) == float(valid.min()):
                        continue
                    # 关键约束：只处理"单个点脱离恒定基线"的情形。
                    # 若相邻点差值里有 3 个以上非零，说明数据在连续变化
                    # （例如气温 13 点、14 点两个峰值构成的高原），
                    # 那是真实变化而不是传感器故障，绝不能改。
                    neighbor_changes = int((diffs > 0).sum())
                    if neighbor_changes == 0 or neighbor_changes > 3:
                        continue
                    ordered = np.sort(valid.to_numpy(dtype="float64"))
                    if ordered.size < 4:
                        continue
                    gaps = np.diff(ordered)
                    largest = float(gaps.max())
                    if largest <= 0:
                        continue
                    rest = gaps[gaps < largest]
                    second = float(rest.max()) if rest.size else 0.0
                    # 判据：最大间隔必须远远大于其余间隔（10 倍以上），
                    # 说明数据是"一大簇 + 极少数离群点"，而不是连续分布。
                    if second > 0 and largest < 10.0 * second:
                        continue
                    boundary = ordered[int(np.argmax(gaps))]
                    isolated = block[block > boundary]
                    for position, value in isolated.items():
                        if pd.isna(value):
                            continue
                        findings.append((
                            int(position), name, value,
                            "整日数值几乎恒定，仅此点明显偏离（疑似传感器故障）"))
                    continue
            threshold = mad_k * scale
            for position, value in block.items():
                if pd.isna(value):
                    continue
                if abs(float(value) - median) > threshold:
                    # 调用方保证索引是 RangeIndex（parse/clean 都做过 reset_index），
                    # 因此索引值即行位置，行号 = 位置 + 1。
                    findings.append((
                        int(position),
                        name, value,
                        "偏离当日中位数 {0} 超过 {1} 倍 MAD".format(_fmt(median), _fmt(mad_k))))
    return findings


# ===========================================================================
# 七、步骤 5：缺失值填补
# ===========================================================================
def _fill_time_series(df: pd.DataFrame, columns: Sequence[str], max_gap: int
                      ) -> Tuple[pd.DataFrame, List[CleanChange], Dict[str, int]]:
    """时序数据缺失值处理：只对不超过 max_gap 的连续缺口做线性插值。

    关键实现细节（实测踩坑，务必按此实现）：
        pandas 的 interpolate(limit=N) 是按"从缺口两端各补 N 个点"来截断的，
        一个 10 点的长缺口会被补上两端各 3 个点、中间留空 —— 这在生态数据规范里
        是不允许的：缺口既然超过 max_gap，就应当整段保留空白，不能"补一半"。
        因此这里先逐段量出缺口长度，再只对长度 ≤ max_gap 的缺口做插值。

    返回 (新表, 改动明细, 计数)。
    """
    result = df.copy()
    changes: List[CleanChange] = []
    counters = {"interpolated": 0, "blanks_kept": 0, "gaps_kept": 0}

    for column in columns:
        if column not in result.columns or not pd.api.types.is_numeric_dtype(result[column]):
            continue
        series = pd.to_numeric(result[column], errors="coerce")
        if series.isna().sum() == 0:
            continue

        # 先量出每一段连续缺口的起止位置。
        # 注意：这里只需要"长缺口"——短缺口由下面的插值自然处理，
        #       所以不再单独算出 short_gaps（曾经算过但从未使用，属死代码）。
        gaps = _find_gaps(series.isna().to_numpy())
        long_gaps = [gap for gap in gaps if gap[1] - gap[0] > int(max_gap)]

        # 只对短缺口插值：把长缺口的位置先"挖掉"，保证插值不会跨过它们。
        # limit_area="inside" 保证不会向序列两端外推（末尾缺测不可能靠插值补出来）。
        masked = series.copy()
        for start, end in long_gaps:
            masked.iloc[start:end] = np.nan
        filled = masked.interpolate(method="linear", limit_area="inside")

        for start, end in gaps:
            length = end - start
            fillable = length <= int(max_gap)
            for offset in range(start, end):
                old_value = series.iloc[offset]
                new_value = filled.iloc[offset] if fillable else np.nan
                if fillable and pd.notna(new_value):
                    result.iat[offset, result.columns.get_loc(column)] = float(new_value)
                    changes.append(CleanChange(offset + 1, column, old_value, float(new_value),
                                               ACTION_FILL_INTERPOLATE))
                    counters["interpolated"] += 1
                else:
                    changes.append(CleanChange(offset + 1, column, old_value, None, ACTION_KEEP_BLANK))
                    counters["blanks_kept"] += 1
            if not fillable:
                counters["gaps_kept"] += 1
    return result, changes, counters


def _find_gaps(missing: np.ndarray) -> List[Tuple[int, int]]:
    """找出一维布尔数组里所有连续 True 段的 [起, 止) 位置（止为开区间）。"""
    gaps: List[Tuple[int, int]] = []
    position = 0
    total = len(missing)
    while position < total:
        if not missing[position]:
            position += 1
            continue
        start = position
        while position < total and missing[position]:
            position += 1
        gaps.append((start, position))
    return gaps


def _fill_grouped_median(df: pd.DataFrame, columns: Sequence[str], group_keys: Optional[List[str]]
                         ) -> Tuple[pd.DataFrame, List[CleanChange], Dict[str, int], List[str]]:
    """缺失值填补：按"样方号 + 物种"分组取中位数，找不到观测值时逐级放宽分组。

    为什么用中位数而不是均值（方案避坑 9）：
        均值会被极值拉偏，且会产生"3.7 株"这种不存在的个体数；
        中位数稳健，配合取整即可得到物理上合理的值。

    三级填补策略（本实现的关键决定，直接决定数据可不可信）：
        ① 优先用「该样方 + 该物种」自身的观测值 —— 最贴近事实；
        ② 该组合只有一行观测（样方调查的常态）时，放宽到「同一物种」——
           同一物种的胸径/株数本来就有可比性；
        ③ 再不行放宽到「同一样方」，仍不行则**保留空白**。
    绝不使用"整列中位数"：把乔木的胸径中位数填到灌木身上会凭空造出测量值，
    直接污染下游的"优势度 = πd²/4"。保留空白虽然难看，但它是诚实的，
    下游指标（阶段 4）会对缺测行明确给出"无法计算"而不是一个假数字。
    """
    result = df.copy()
    changes: List[CleanChange] = []
    counters = {"median_filled": 0, "fallback_filled": 0, "structural_kept": 0}
    skipped: List[str] = []
    level_columns: Dict[str, Dict[str, int]] = {"species": {}, "plot": {}, "column": {}}
    kept_columns: Dict[str, int] = {}

    species_key = "物种" if "物种" in df.columns else None
    plot_key = "样方号" if "样方号" in df.columns else None

    def _group_median(series: pd.Series, key: Any) -> pd.Series:
        """按某一列（或多列组合）分组求中位数，返回与 series 等长的序列。

        注意：样方数据的分组键是「样方号 + 物种」两列的组合，
        只用其中一列分组会把"同一物种"与"同一样方"两个层级混为一谈。
        """
        if key is None:
            return pd.Series(np.nan, index=series.index, dtype="float64")
        keys = result[key] if isinstance(key, list) else result[key]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                return series.groupby(keys, dropna=False).transform(
                    lambda block: block.median())
        except Exception:  # noqa: BLE001 - 分组失败时视为不可用
            return pd.Series(np.nan, index=series.index, dtype="float64")

    for column in columns:
        if column not in result.columns or not pd.api.types.is_numeric_dtype(result[column]):
            continue
        series = pd.to_numeric(result[column], errors="coerce")
        if series.isna().sum() == 0:
            continue
        # 整列都是缺失：没有任何依据可填，跳过并说明
        if series.notna().sum() == 0:
            skipped.append("「{0}」整列都是空白，无法填补，已保持空白。".format(column))
            continue

        is_count = column in COUNT_FIELDS
        # 分组层级（由细到粗）：
        #   ① 样方号 + 物种（最贴近事实，方案阶段 3 第 5 条要求的分组）
        #   ② 同一物种（同一物种的株数/胸径本来就有可比性）
        #   ③ 同一样方（林下植被与乔木同处一个样方，量级可比）
        #   ④ 整列中位数（仅当数据完全没有分组信息时）
        group_median = _group_median(series, group_keys) if group_keys else pd.Series(
            np.nan, index=series.index, dtype="float64")
        species_median = _group_median(series, species_key)
        plot_median = _group_median(series, plot_key)

        # 判断哪些物种"整列都没有本项观测"——例如灌木、草本永远没有胸径。
        # 这类空白不是数据缺失，而是"这项本来就测不了"，用别的物种的数值去填
        # 会凭空造出测量值（给一丛灌木安上 12.5 cm 的胸径），直接污染优势度计算。
        # 判据与领域无关：
        #   ① 该物种全表都没有本列的任何观测；
        #   ② 该物种出现的行数足够多（≥2 行），排除"只记录了一次、恰好缺失"的偶发情况
        #      —— 那种情况交给同一样方中位数兜底更合理。
        species_without_data: set = set()
        if species_key:
            rows_per_species = result[species_key].value_counts()
            observed = set(result.loc[series.notna(), species_key].dropna().unique())
            species_without_data = {
                name for name, count in rows_per_species.items()
                if name not in observed and count >= 2
            }

        for position in np.flatnonzero(series.isna().to_numpy()):
            old_value = series.iloc[position]
            value: Optional[float] = None
            action = ACTION_FILL_MEDIAN
            level = ""

            candidates = (
                (group_median.iloc[position] if group_keys else np.nan, "同一样方同一物种"),
                (species_median.iloc[position], "同一物种"),
                (plot_median.iloc[position], "同一样方"),
                # 没有分组键的普通统计数据（例如处理组对比表）：
                # 只能参考整列中位数，这是唯一可用的依据，单独标注以示区分。
                (series.median() if not group_keys else np.nan, "整列"),
            )
            for candidate, label in candidates:
                if pd.notna(candidate):
                    value = float(candidate)
                    level = label
                    break

            # 结构性空白的最终判定：该物种全表都没有本项观测（如灌木没有胸径），
            # 则无论哪一级能算出中位数，都不为它编造数值。
            if species_key and species_without_data:
                species_name = result[species_key].iloc[position]
                if species_name in species_without_data:
                    value = None
                    level = ""

            if value is None:
                # 三级都找不到参考值 → 保留空白（例如灌木、草本本来就没有胸径）
                counters["structural_kept"] += 1
                kept_columns[column] = kept_columns.get(column, 0) + 1
                changes.append(CleanChange(position + 1, column, old_value, None,
                                           ACTION_KEEP_STRUCTURAL))
                continue

            if is_count:
                # 计数列取整：不能出现"3.7 株"
                value = float(int(round(value)))
            result.iat[position, result.columns.get_loc(column)] = value
            if level == "同一样方同一物种":
                counters["median_filled"] += 1
            elif level == "同一物种":
                counters["species_filled"] = counters.get("species_filled", 0) + 1
                level_columns["species"][column] = level_columns["species"].get(column, 0) + 1
                action = ACTION_FILL_SPECIES_MEDIAN
            elif level == "同一样方":
                counters["fallback_filled"] += 1
                level_columns["plot"][column] = level_columns["plot"].get(column, 0) + 1
                action = ACTION_FILL_PLOT_MEDIAN
            else:
                counters["column_filled"] = counters.get("column_filled", 0) + 1
                level_columns["column"][column] = level_columns["column"].get(column, 0) + 1
                action = ACTION_FILL_COLUMN_MEDIAN
            changes.append(CleanChange(position + 1, column, old_value, value, action))

    for column, count in kept_columns.items():
        skipped.append(
            "「{0}」有 {1} 个空白在该样方、该物种、同一样方里都找不到可参考的观测值，"
            "已保持空白而没有用其它物种的数值顶替——否则会凭空造出测量值并影响优势度等指标。".format(
                column, count))
    for column, count in level_columns["species"].items():
        skipped.append(
            "「{0}」有 {1} 个空白是参考**同一物种**的观测值填补的（该样方该物种本身没有观测），"
            "请核对是否合理。".format(column, count))
    for column, count in level_columns["plot"].items():
        skipped.append(
            "「{0}」有 {1} 个空白是参考**同一样方**内其它物种的观测值填补的，"
            "属于统计估计值而非真实测量，用于指标计算前请自行判断是否可接受。".format(column, count))
    for column, count in level_columns["column"].items():
        skipped.append(
            "「{0}」有 {1} 个空白是按**整列中位数**填补的（该数据没有分组信息），"
            "属于统计估计值而非真实测量，用于指标计算前请自行判断是否可接受。".format(column, count))
    return result, changes, counters, skipped


def _fill_text_columns(df: pd.DataFrame, columns: Sequence[str]
                       ) -> Tuple[pd.DataFrame, List[CleanChange], Dict[str, int], List[str]]:
    """分组键列的缺失值填补（按众数），返回 (新表, 明细, 计数, 需说明的事项)。

    为什么要补：样方号/物种这类分组键一旦缺失，"按分组取中位数"就失去意义，
    该行会散成一个孤立的组，导致它永远得不到填补。

    为什么不用 pandas 的 Series.mode()：
        实测发现它在"每个取值都只出现一次"时按**字典序**返回，
        例如 ['对照','增温'] 会返回 '增温' —— 用它做填补会得到与数据无关的结果，
        而且一旦选错，某一行会被悄悄归进错误的分组。因此这里自己按
        "出现次数最多、并列时取最先出现的那个"来选，结果稳定且可解释。

    特殊情况（必须如实告知，绝不硬填）：
        如果该列的空白超过一半，说明"众数"并不具有代表性，
        此时保持空白并在日志中说明影响，而不是给一半的行硬安一个分组名。
    """
    result = df.copy()
    changes: List[CleanChange] = []
    counters = {"mode_filled": 0}
    notes: List[str] = []

    for column in columns:
        if column not in result.columns or pd.api.types.is_numeric_dtype(result[column]):
            continue
        series = result[column]
        missing_count = int(series.isna().sum())
        if missing_count == 0:
            continue

        counts = series.value_counts(dropna=True)
        if counts.empty:
            notes.append("「{0}」整列都是空白，无法判断分组，已保持空白。".format(column))
            continue

        # 空白占比过半 → 众数不可信，保持空白并说明
        if missing_count > len(series) / 2:
            notes.append(
                "「{0}」有 {1} 行的分组名是空白的（超过一半），硬填会把这些行错误地归入同一组，"
                "因此保持空白。这些行可能无法参与按分组的统计，建议补齐原始记录后重新导入。".format(
                    column, missing_count))
            continue

        # 取出现次数最多者；并列时 value_counts 已按首次出现顺序排列，index[0] 即最先出现的
        fill_value = counts.index[0]
        for position in np.flatnonzero(series.isna().to_numpy()):
            old_value = series.iloc[position]
            result.iat[position, result.columns.get_loc(column)] = fill_value
            changes.append(CleanChange(position + 1, column, old_value, fill_value, ACTION_FILL_MODE))
            counters["mode_filled"] += 1
        notes.append(
            "「{0}」的 {1} 个空白分组名已按出现次数最多的「{2}」填补，请核对是否正确。".format(
                column, missing_count, fill_value))
    return result, changes, counters, notes


# ===========================================================================
# 八、日志生成（全中文，按方案 5.5 的格式组织）
# ===========================================================================
def build_log(stats: CleanStats, notes: Sequence[str] = (), skipped: Sequence[str] = (),
              params: Optional[Dict[str, Any]] = None) -> str:
    """生成中文清洗日志（方案 5.5：禁止英文技术语句，按分条摘要组织）。"""
    params = params or {}
    lines: List[str] = []
    lines.append("一、总体情况")
    lines.append("　共读取 {0:,} 行数据。".format(stats.rows_in))

    if stats.duplicates_removed:
        lines.append("　删除了 {0:,} 行完全重复的记录（保留首次出现的记录）。".format(stats.duplicates_removed))
    else:
        lines.append("　未发现完全重复的记录。")

    if stats.outliers_fixed:
        detail = "、".join("{0} {1} 处".format(reason, count)
                          for reason, count in stats.outlier_breakdown.items())
        lines.append("　修正了 {0:,} 个异常值（{1}，详见下方明细）。".format(stats.outliers_fixed, detail))
    else:
        lines.append("　未发现需要修正的异常值。")

    if stats.missing_filled:
        parts: List[str] = []
        if stats.interpolated:
            parts.append("时序数据线性插值 {0} 个".format(stats.interpolated))
        if stats.median_filled:
            parts.append("按「样方号 + 物种」分组中位数填补 {0} 个".format(stats.median_filled))
        if stats.species_filled:
            parts.append("参考同一物种填补 {0} 个".format(stats.species_filled))
        if stats.plot_filled:
            parts.append("参考同一样方填补 {0} 个".format(stats.plot_filled))
        if stats.column_filled:
            parts.append("按整列中位数填补 {0} 个".format(stats.column_filled))
        if stats.mode_filled:
            parts.append("分组名按众数填补 {0} 个".format(stats.mode_filled))
        lines.append("　填补了 {0:,} 个缺失值（{1}）。".format(stats.missing_filled, "、".join(parts)))
    else:
        lines.append("　没有需要填补的缺失值。")

    if stats.blanks_kept:
        lines.append("　因断测过长保留了 {0} 个空白值未填补（涉及 {1} 段超长缺口）。".format(
            stats.blanks_kept, stats.gaps_kept))

    lines.append("　清洗后共 {0:,} 行。".format(stats.rows_out))

    if stats.scale_converted or notes or skipped:
        lines.append("")
        lines.append("二、量纲与参数说明")
        for column, count in stats.scale_converted.items():
            lines.append("　「{0}」列为 0~1 小数制，已整列换算为百分制（{1} 个值）。".format(column, count))
        for note in notes:
            lines.append("　" + note)

    if skipped:
        lines.append("")
        lines.append("三、需要你留意的列")
        for message in skipped:
            lines.append("　" + message)

    lines.append("")
    lines.append("四、下一步")
    lines.append("　数据已可用于指标计算。建议先核对下方明细表中被修改的单元格，")
    lines.append("　确认无误后前往【指标计算】页。")
    return "\n".join(lines)


def _build_changes_table(changes: Sequence[CleanChange]) -> pd.DataFrame:
    """把改动明细转成可排序、可导出的表格（方案 5.5 要求）。"""
    if not changes:
        return pd.DataFrame(columns=["行号", "列名", "原值", "处理后的值", "处理方式"])
    rows = [{
        "行号": change.row,
        "列名": change.column,
        "原值": _fmt(change.old_value),
        "处理后的值": _fmt(change.new_value),
        "处理方式": change.action,
    } for change in changes]
    return pd.DataFrame(rows, columns=["行号", "列名", "原值", "处理后的值", "处理方式"])


# ===========================================================================
# 九、主入口
# ===========================================================================
def clean_data(df: pd.DataFrame, data_type: str = data_parse.TYPE_QUADRAT,
               check_outlier: bool = True, fill_missing: bool = True, drop_duplicate: bool = True,
               mad_k: float = DEFAULT_MAD_K, max_gap: int = DEFAULT_MAX_GAP) -> CleanResult:
    """清洗数据表，返回 (清洗后新表, 日志, 明细表, 统计)。

    参数（与方案阶段 3 的界面选项一一对应）：
        check_outlier  ：启用生态异常值检测（含量纲统一与时序突变检测）
        fill_missing   ：启用缺失值填补
        drop_duplicate ：启用自动去重
        mad_k          ：突变检测阈值倍数，默认 3 倍 MAD
        max_gap        ：最大插值间隙，默认 3 个点

    强制纪律：**输入 DataFrame 绝不被修改**，返回的是全新对象。
    """
    # ---- 步骤 0：输入校验（阶段 8 还会在界面层再拦一道）----
    if df is None or not isinstance(df, pd.DataFrame):
        raise CleanError("没有可清洗的数据。\n\n怎么办：请先到【数据导入】页导入数据。")
    if len(df) == 0 or len(df.columns) == 0:
        raise CleanError("数据表是空的，无法清洗。\n\n怎么办：请到【数据导入】页重新导入包含数据行的文件。")

    # 从头开始：复制一份，之后所有操作都在副本上进行
    working = df.copy(deep=True)
    original_columns = list(working.columns)
    changes: List[CleanChange] = []
    notes: List[str] = []
    skipped: List[str] = []
    stats = CleanStats(rows_in=len(working))

    # 记下原始行号，供明细表与"清洗前/后"对比使用
    working = working.reset_index(drop=True)

    time_series = _time_series_mask(working)
    is_time_series = time_series is not None
    group_keys = None if is_time_series else _group_keys(working)

    # ---- 步骤 A：自动去重（放在异常检测之前）----
    # 为什么先去重：明细表里的"行号"必须与用户看到的原始表一致。
    # 若先去异常再删行，后面记录的异常行号会因为删行而整体前移，
    # 用户按行号回查原始表时会找不到那一行。
    if drop_duplicate:
        duplicate_mask = working.duplicated(keep="first")
        duplicate_positions = np.flatnonzero(duplicate_mask.to_numpy())
        if duplicate_positions.size:
            for position in duplicate_positions:
                changes.append(CleanChange(int(position) + 1, "（整行）", "完全重复的行", None,
                                           ACTION_DELETE_DUPLICATE))
            stats.duplicates_removed = int(duplicate_positions.size)
            working = working.loc[~duplicate_mask].reset_index(drop=True)
            # 去重后行位置全部前移，之前记录的行号不再对应，因此把已记录的改动一起丢弃重建：
            # 去重记录本身用的是"原始行号"，保留即可；异常检测在其后进行，行号自然一致。
            changes = [c for c in changes if c.action == ACTION_DELETE_DUPLICATE]

    # ---- 步骤 1 & 2 & 3：异常值相关处理 ----
    outlier_positions: Dict[Tuple[int, str], str] = {}
    if check_outlier:
        # 1. 量纲统一（必须先做）
        working, converted, scale_notes = unify_ratio_scale(working)
        stats.scale_converted = converted
        notes.extend(scale_notes)

        # 2. 规则法异常检测
        rule_findings = detect_rule_outliers(working)
        # 3. 时序突变检测（仅时序数据）
        spike_findings = detect_spikes(working, mad_k=mad_k, time_series=time_series) if is_time_series else []

        breakdown: Dict[str, int] = {}
        for position, column, old_value, reason in list(rule_findings) + list(spike_findings):
            key = (int(position), str(column))
            if key in outlier_positions:
                continue      # 同一个单元格只处理一次
            outlier_positions[key] = reason
            breakdown[reason] = breakdown.get(reason, 0) + 1
            # 异常值统一处理为"置为缺失"，随后由填补步骤决定能不能救回来；
            # 这样既不会凭空猜测一个替代值，也不会把整行数据丢掉。
            working.iat[key[0], working.columns.get_loc(key[1])] = np.nan
            changes.append(CleanChange(key[0] + 1, key[1], old_value, None, ACTION_FIX_OUTLIER))
        stats.outliers_fixed = len(outlier_positions)
        stats.outlier_breakdown = breakdown

    # ---- 步骤 5：缺失值填补 ----
    if fill_missing:
        # 先补分组键，否则"按分组取中位数"会因为键缺失而失去意义
        if group_keys:
            working, text_changes, text_counters, text_notes = _fill_text_columns(working, group_keys)
            changes.extend(text_changes)
            stats.mode_filled = text_counters["mode_filled"]
            skipped.extend(text_notes)

        numeric_columns = _numeric_columns(working)
        if is_time_series:
            # 时序数据：时间列不可作为被插值对象
            target_columns = [name for name in numeric_columns if name != "时间"]
            working, fill_changes, counters = _fill_time_series(working, target_columns, int(max_gap))
            changes.extend(fill_changes)
            stats.interpolated = counters["interpolated"]
            stats.blanks_kept = counters["blanks_kept"]
            stats.gaps_kept = counters["gaps_kept"]
            if counters["blanks_kept"]:
                notes.append(
                    "有 {0} 个空白落在连续缺测超过 {1} 个点的缺口内，按规范保留空白而不插值。".format(
                        counters["blanks_kept"], int(max_gap)))
        else:
            working, fill_changes, counters, fill_skipped = _fill_grouped_median(
                working, numeric_columns, group_keys)
            changes.extend(fill_changes)
            stats.median_filled = counters["median_filled"]
            stats.species_filled = counters.get("species_filled", 0)
            stats.plot_filled = counters.get("fallback_filled", 0)
            stats.column_filled = counters.get("column_filled", 0)
            skipped.extend(fill_skipped)

        stats.missing_filled = (stats.interpolated + stats.median_filled + stats.species_filled
                                + stats.plot_filled + stats.column_filled + stats.mode_filled)

    stats.rows_out = len(working)

    # 列顺序保持不变（用户看到的表结构不应因为清洗而跳动）
    ordered = [name for name in original_columns if name in working.columns]
    working = working[ordered]

    # 时间列解析结果不参与计算，仅在需要时保证格式一致
    if "时间" in working.columns:
        parsed = pd.to_datetime(working["时间"], format=data_parse.TIME_FORMAT, errors="coerce")
        working["时间"] = parsed.dt.strftime(data_parse.TIME_FORMAT).where(parsed.notna(), None)

    params = {
        "check_outlier": bool(check_outlier),
        "fill_missing": bool(fill_missing),
        "drop_duplicate": bool(drop_duplicate),
        "mad_k": float(mad_k),
        "max_gap": int(max_gap),
        "data_type": data_type,
        "is_time_series": bool(is_time_series),
    }
    # 参数说明必须出现在日志里（用户要能回看"这次到底用的什么参数"），
    # 因此无论有没有量纲换算，都补一条参数说明。
    if params:
        notes.append("本次使用的参数：突变检测阈值 {0} 倍 MAD，最大插值间隙 {1} 个点。".format(
            _fmt(params["mad_k"]), _fmt(params["max_gap"])))
    log = build_log(stats, notes=notes, skipped=skipped, params=params)
    return CleanResult(df=working, log=log, changes=_build_changes_table(changes),
                       stats=stats, params=params)


# ===========================================================================
# 十、参数实时反馈（方案 5.4：调整参数后即时显示预计影响，无需真正执行）
# ===========================================================================
def preview_impact(df: pd.DataFrame, data_type: str = data_parse.TYPE_QUADRAT,
                   check_outlier: bool = True, fill_missing: bool = True,
                   drop_duplicate: bool = True, mad_k: float = DEFAULT_MAD_K,
                   max_gap: int = DEFAULT_MAX_GAP) -> Dict[str, Any]:
    """在不修改任何数据的前提下，预测本次清洗会影响多少内容。

    实现方式：直接跑一次完整清洗（参数规模都不大），再从统计里取数。
    这样保证"预测值"与"真正执行的结果"必然一致 —— 用另一套轻量算法估算
    反而会出现"提示说 23 个异常、实际改了 25 个"的不一致，比不提示更糟。
    """
    try:
        result = clean_data(df, data_type=data_type, check_outlier=check_outlier,
                            fill_missing=fill_missing, drop_duplicate=drop_duplicate,
                            mad_k=mad_k, max_gap=max_gap)
    except CleanError as exc:
        return {"ok": False, "message": str(exc)}

    stats = result.stats
    parts: List[str] = []
    parts.append("预计修正 {0} 个异常值".format(stats.outliers_fixed))
    parts.append("填补 {0} 个缺失值".format(stats.missing_filled))
    if stats.duplicates_removed:
        parts.append("删除 {0} 行重复记录".format(stats.duplicates_removed))
    if stats.blanks_kept:
        parts.append("保留 {0} 个空白（缺口过长）".format(stats.blanks_kept))
    if stats.scale_converted:
        parts.append("换算 {0} 列的量纲".format(len(stats.scale_converted)))
    return {
        "ok": True,
        "message": "；".join(parts) + "。",
        "stats": stats,
        "rows_in": stats.rows_in,
        "rows_out": stats.rows_out,
    }


def describe_columns(df: pd.DataFrame) -> pd.DataFrame:
    """生成"清洗前"数据体检表：每列的缺失率、异常值个数、量纲判断结果。

    用途：清洗页在用户点执行之前，先让他看到数据到底哪里有问题。
    """
    rows = len(df)
    records: List[Dict[str, Any]] = []
    rule_findings = detect_rule_outliers(df)
    spike_findings = detect_spikes(df)
    outlier_count: Dict[str, int] = {}
    for _position, column, _value, _reason in list(rule_findings) + list(spike_findings):
        outlier_count[column] = outlier_count.get(column, 0) + 1

    for column in df.columns:
        name = str(column)
        series = df[column]
        missing = int(series.isna().sum())
        record: Dict[str, Any] = {
            "列名": name,
            "缺失个数": missing,
            "缺失率": "{0:.2f}%".format(missing / rows * 100.0) if rows else "0.00%",
            "异常值个数": outlier_count.get(name, 0),
            "说明": "",
        }
        if name in RATIO_FIELDS and pd.api.types.is_numeric_dtype(series):
            valid = pd.to_numeric(series, errors="coerce").dropna()
            if not valid.empty and float(valid.max()) <= DECIMAL_SCALE_MAX and float(valid.max()) > 0:
                record["说明"] = "检测到 0~1 小数制，清洗时会自动换算为百分制"
        if record["异常值个数"] and not record["说明"]:
            record["说明"] = "存在违反生态学规则的取值，清洗时会修正"
        records.append(record)
    return pd.DataFrame(records, columns=["列名", "缺失个数", "缺失率", "异常值个数", "说明"])
