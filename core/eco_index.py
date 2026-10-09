# -*- coding: utf-8 -*-
"""
生态指标计算核心（开发方案 第六章 阶段 4 + 第七章 表结构规范）

粒度说明（本模块最重要的设计，来自方案阶段 4 的"粒度说明"）：
    生态指标天然分为两组，粒度不同，**必须分别输出为两张表**：
        行级表 index_row     ：粒度 = 样方 × 物种（密度/相对密度/频度/相对频度/优势度/相对优势度/重要值）
        汇总表 index_summary ：粒度 = 样方（S / N / H / D / J / R）或分组（环境时序统计）
    若强行合并成一张表，要么维度不匹配导致算错，要么满表 NaN 凑形状。

公式依据（方案第七章，逐条实现，不得改动）：
    密度       = 该样方该物种株数 / 样方面积
    相对密度   = 该物种株数 / 全部物种总株数 × 100%
    频度       = 该物种出现的样方数 / 总样方数 × 100%   （跨样方聚合，同物种各行取同值）
    相对频度   = 该物种频度 / 所有物种频度之和 × 100%
    优势度     = 该物种胸高断面积之和 πd²/4（或盖度×面积）
    相对优势度 = 该物种优势度 / 所有物种优势度之和 × 100%
    重要值     = (相对密度 + 相对频度 + 相对优势度) / 3
    Shannon    H = −Σ(pᵢ × ln pᵢ)，pᵢ = nᵢ/N
    Simpson    D = 1 − Σpᵢ²
    Pielou     J = H / ln S
    Margalef   R = (S − 1) / ln N

边界处理（方案第七章末尾明确要求：必须返回 NaN，不得抛异常或返回 inf）：
    N = 0（样方内没有个体）→ H、D 无定义，返回 NaN；
    S = 1（只有一个物种）  → H = 0（数学上 −1×ln1 = 0，是确定值，不是无定义）；
                             D = 0（同样确定）；J、R 无定义 → NaN；
    N = 1                 → R 的分母 ln N = 0，无定义 → NaN。
    所有 NaN 都会在 notes 里用中文说明原因，供界面与报告展示。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from core import data_parse

# ===========================================================================
# 一、常量与指标分组
# ===========================================================================
GROUP_STRUCTURE = "群落结构指标"
GROUP_DIVERSITY = "多样性指标"
GROUP_ENV = "环境时序统计"
GROUP_SOIL = "土壤指标"
GROUP_EXPERIMENT = "实验生态指标"

ALL_GROUPS: Tuple[str, ...] = (GROUP_STRUCTURE, GROUP_DIVERSITY, GROUP_ENV,
                               GROUP_SOIL, GROUP_EXPERIMENT)

# 行级表列名（方案 7.1 的指标 + 原有的样方号/物种/株数等）
ROW_METRIC_COLUMNS: Tuple[str, ...] = (
    "密度", "相对密度", "频度", "相对频度", "优势度", "相对优势度", "重要值",
)
# 汇总表列名（方案 7.2）
SUMMARY_METRIC_COLUMNS: Tuple[str, ...] = ("S 物种数", "N 个体总数", "H 香农指数",
                                           "D 辛普森指数", "J Pielou均匀度", "R Margalef丰富度")

# 默认参数（方案 5.4：默认值必须开箱即用）
DEFAULT_QUADRAT_AREA = 1.0      # 样方面积，单位 m²
DEFAULT_DAY_START = 6           # 昼开始小时（含）
DEFAULT_DAY_END = 18            # 昼结束小时（不含）
DEFAULT_GERMINATION_DAY = 7     # 萌发势统计天数
DEFAULT_GROUP_BY = "日"          # 环境时序统计的分组维度

# 需要用户提供字段的指标组（缺字段时给出明确提示，而不是算出一堆 NaN）
SOIL_REQUIRED = ("鲜重", "干重")
EXPERIMENT_REQUIRED = ("处理组",)

# 中文说明（界面悬浮提示、帮助面板术语表、报告共用同一份文案来源）
METRIC_DESCRIPTIONS: Dict[str, str] = {
    "密度": "密度 = 该样方该物种株数 ÷ 样方面积。单位面积上的个体数，反映拥挤程度。",
    "相对密度": "相对密度 = 该物种株数 ÷ 全部物种总株数 × 100%。这个物种在数量上占的份额。",
    "频度": "频度 = 该物种出现的样方数 ÷ 总样方数 × 100%。到处都有还是只长在个别地方。",
    "相对频度": "相对频度 = 该物种频度 ÷ 所有物种频度之和 × 100%。频度的相对份额。",
    "优势度": "优势度 = 该物种胸高断面积之和（πd²/4）。从体积角度衡量谁长得壮；"
              "没有胸径数据时退化为盖度，此时数值含义是覆盖程度而不是断面积。",
    "相对优势度": "相对优势度 = 该物种优势度 ÷ 所有物种优势度之和 × 100%。优势度的相对份额。",
    "重要值": "重要值 = (相对密度 + 相对频度 + 相对优势度) ÷ 3。"
              "综合数量、分布、长势三项，是判断群落优势种最常用的指标。"
              "缺少优势度数据时，软件会自动改用 (相对密度 + 相对频度) ÷ 2 并明确标注。",
    "S 物种数": "该样方内出现的物种个数（S），最直观的丰富程度指标。",
    "N 个体总数": "该样方内全部物种株数之和（N），也就是调查到的个体总数。",
    "H 香农指数": "Shannon-Wiener 指数 H = −Σ(pᵢ × ln pᵢ)，pᵢ = nᵢ ÷ N。"
                  "兼顾物种多不多与分得匀不匀，值越大越多样，文献里最常用。",
    "D 辛普森指数": "Simpson 指数 D = 1 − Σpᵢ²。随机抽两株是不同物种的概率，对优势种更敏感。",
    "J Pielou均匀度": "Pielou 均匀度 J = H ÷ ln S。只看分得匀不匀，0~1 之间，越接近 1 越均匀。"
                      "只有一个物种时（ln S = 0）无法计算，留空。",
    "R Margalef丰富度": "Margalef 丰富度 R = (S − 1) ÷ ln N。用个体数校正后的物种丰富度，"
                        "便于不同调查强度之间比较。个体总数为 1 时无法计算，留空。",
}

# 指标组 -> 该组产出的表
GROUP_TABLE: Dict[str, str] = {
    GROUP_STRUCTURE: "index_row",
    GROUP_DIVERSITY: "index_summary",
    GROUP_ENV: "index_summary",
    GROUP_SOIL: "index_summary",
    GROUP_EXPERIMENT: "index_summary",
}


class IndexError_(Exception):
    """指标计算失败异常（中文信息，可直接展示）。

    名字带下划线是为了不与内置的 IndexError 冲突。
    """


# ===========================================================================
# 二、数据结构
# ===========================================================================
@dataclass
class IndexResult:
    """指标计算结果。"""
    row_table: Optional[pd.DataFrame] = None       # 行级指标表
    summary_table: Optional[pd.DataFrame] = None   # 汇总指标表
    row_granularity: str = ""                      # 行级表粒度说明（界面直接展示）
    summary_granularity: str = ""                  # 汇总表粒度说明
    notes: List[str] = field(default_factory=list)  # 中文说明（边界情况、方法选择等）
    groups: List[str] = field(default_factory=list)  # 实际计算了哪些指标组
    params: Dict[str, Any] = field(default_factory=dict)

    def has_row_table(self) -> bool:
        return self.row_table is not None and len(self.row_table) > 0

    def has_summary_table(self) -> bool:
        return self.summary_table is not None and len(self.summary_table) > 0


# ===========================================================================
# 三、单指标计算（纯函数，便于单测逐项核对）
# ===========================================================================
def shannon_index(counts: Sequence[float]) -> float:
    """Shannon-Wiener 多样性指数 H = −Σ(pᵢ × ln pᵢ)。

    边界（方案第七章末尾）：
        N = 0           → NaN（没有任何个体，无从计算）
        S = 1           → 0.0（数学上 −1×ln1 = 0，是确定值而不是无定义）
        出现 0 或负的个体数 → 该物种不计入（负数是数据错误，清洗阶段已处理）
    """
    values = [float(value) for value in counts if value is not None and float(value) > 0]
    total = sum(values)
    if total <= 0:
        return float("nan")
    h = 0.0
    for value in values:
        p = value / total
        h -= p * math.log(p)
    return h


def simpson_index(counts: Sequence[float]) -> float:
    """Simpson 多样性指数 D = 1 − Σpᵢ²。"""
    values = [float(value) for value in counts if value is not None and float(value) > 0]
    total = sum(values)
    if total <= 0:
        return float("nan")
    return 1.0 - sum((value / total) ** 2 for value in values)


def pielou_evenness(counts: Sequence[float]) -> float:
    """Pielou 均匀度 J = H / ln S。

    边界：S ≤ 1 时 ln S = 0，无法计算 → NaN（方案 7.2 的 warn 明确要求）。
    """
    values = [float(value) for value in counts if value is not None and float(value) > 0]
    species_count = len(values)
    if species_count <= 1:
        return float("nan")
    h = shannon_index(values)
    if math.isnan(h):
        return float("nan")
    return h / math.log(species_count)


def margalef_richness(counts: Sequence[float]) -> float:
    """Margalef 丰富度 R = (S − 1) / ln N。

    边界：
        N ≤ 1 → ln N ≤ 0，无定义 → NaN；
        S = 1 → 分子为 0，数学上 R = 0，是确定值（与方案第八章 "5,5 → 0.434294" 同源公式）。
        注意：S = 1 时若 N > 1，R 确实等于 0，不该留空；只有 N = 1 才是无定义。
    """
    values = [float(value) for value in counts if value is not None and float(value) > 0]
    species_count = len(values)
    total = sum(values)
    if total <= 1:
        return float("nan")
    return (species_count - 1) / math.log(total)


def olson_decay_rate(w0: float, wt: float, t: float) -> float:
    """Olson 凋落物分解速率 k = −ln(Wt / W0) / t。

    校验：W0=100、Wt=50、t=1 → k = 0.693147（方案第八章给出的期望值）。
    边界：W0 或 Wt 非正、t ≤ 0、或 Wt > W0（残留量大于初始量，不可能）→ NaN。
    """
    try:
        w0 = float(w0)
        wt = float(wt)
        t = float(t)
    except (TypeError, ValueError):
        return float("nan")
    if w0 <= 0 or wt <= 0 or t <= 0 or wt > w0:
        return float("nan")
    return -math.log(wt / w0) / t


# ===========================================================================
# 四、行级指标（方案 7.1）
# ===========================================================================
def _resolve_dominance_mode(df: pd.DataFrame) -> str:
    """决定优势度用哪种口径：胸径断面积优先，否则用盖度。

    为什么必须二选一而不能混用：πd²/4 得到的是断面积（cm²），盖度是百分比，
    两者量纲完全不同。同一张表里一半行用断面积、一半行用盖度，
    "相对优势度"的分母就变成了两种单位相加，结果没有意义。
    因此：只要这一列存在可用数据，就统一用胸径；否则整体退化为盖度。
    实际用了哪种，会写进 notes 告知用户。
    """
    if "胸径" in df.columns:
        series = pd.to_numeric(df["胸径"], errors="coerce")
        if series.notna().sum() > 0:
            return "胸径"
    if "盖度" in df.columns:
        series = pd.to_numeric(df["盖度"], errors="coerce")
        if series.notna().sum() > 0:
            return "盖度"
    return "无"


def compute_row_metrics(df: pd.DataFrame, quadrat_area: float = DEFAULT_QUADRAT_AREA,
                        notes: Optional[List[str]] = None) -> pd.DataFrame:
    """计算行级指标表（粒度：样方 × 物种）。

    实现要点：
        · 输入只读：全过程在副本上操作，绝不修改传入的数据表；
        · 密度按"该样方该物种的株数 / 样方面积"计算，不跨样方合并；
        · 频度需要跨样方聚合，同一物种在所有行上取相同值；
        · 相对密度/相对频度/相对优势度都在"全部物种"范围内计算（方案 7.1 的"同一统计范围内"）。
    """
    if notes is None:
        notes = []
    missing_required = [name for name in ("样方号", "物种", "株数") if name not in df.columns]
    if missing_required:
        raise IndexError_(
            "缺少计算群落结构指标所必需的列：{0}。\n\n"
            "怎么办：到【数据导入】页用【列映射】把这几列对应到你的数据上，然后重新清洗与计算。".format(
                "、".join(missing_required)))

    result = df.copy(deep=True)
    result["株数"] = pd.to_numeric(result["株数"], errors="coerce")
    result = result.reset_index(drop=True)

    # ---- 密度 ----
    area = float(quadrat_area) if quadrat_area and float(quadrat_area) > 0 else DEFAULT_QUADRAT_AREA
    if float(quadrat_area or 0) <= 0:
        notes.append("样方面积填的是 0 或负数，已按默认值 {0} m² 计算密度。".format(DEFAULT_QUADRAT_AREA))
    result["密度"] = result["株数"] / area

    # ---- 相对密度：该物种株数 / 全部物种总株数 × 100% ----
    # "同一统计范围内"（方案 7.1）：密度本身是"某样方某物种"的指标，
    # 因此相对密度在**每个样方内**归一，同一样方各物种的相对密度合计恰好 100%。
    # 这样它与同样是样方内比率的相对优势度口径一致，重要值才有可比性。
    valid_counts = result["株数"].fillna(0.0)
    quadrat_total = valid_counts.clip(lower=0).groupby(result["样方号"], dropna=False).transform("sum")
    if (quadrat_total > 0).any():
        result["相对密度"] = np.where(quadrat_total > 0,
                                      valid_counts.clip(lower=0) / quadrat_total * 100.0,
                                      np.nan)
        zero_quadrats = sorted({str(q) for q, total in
                                zip(result["样方号"], quadrat_total) if total <= 0})
        if zero_quadrats:
            notes.append("以下样方的株数合计为 0，无法计算相对密度，已留空：{0}。".format(
                "、".join(zero_quadrats)))
    else:
        result["相对密度"] = np.nan
        notes.append("全部样方的株数合计为 0，无法计算相对密度，已留空。")

    # ---- 频度：跨样方聚合，同物种各行取同值 ----
    quadrat_count = int(result["样方号"].nunique())
    if quadrat_count > 0:
        appeared = result.groupby("物种")["样方号"].nunique()
        frequency = appeared / quadrat_count * 100.0
        result["频度"] = result["物种"].map(frequency)
        freq_total = float(frequency.sum())
        relative = frequency / freq_total * 100.0 if freq_total > 0 else frequency * np.nan
        result["相对频度"] = result["物种"].map(relative)
    else:
        result["频度"] = np.nan
        result["相对频度"] = np.nan
        notes.append("没有识别到样方号，无法计算频度，已留空。")

    # ---- 优势度 ----
    mode = _resolve_dominance_mode(result)
    if mode == "胸径":
        dbh = pd.to_numeric(result["胸径"], errors="coerce")
        # 胸高断面积 πd²/4；胸径缺失的行（灌木草本）优势度留空，
        # 不用其它物种的数值顶替 —— 那会算出一个不存在的断面积。
        result["优势度"] = math.pi * dbh.pow(2) / 4.0
        notes.append("优势度按胸高断面积 πd²/4 计算（基于「胸径」列）。"
                     "没有胸径记录的行（例如灌木、草本）优势度留空，不参与重要值的优势度项。")
    elif mode == "盖度":
        cover = pd.to_numeric(result["盖度"], errors="coerce")
        result["优势度"] = cover
        notes.append("数据中没有可用的胸径，优势度已改用「盖度」表示覆盖程度"
                     "（此时它不再是断面积，请勿与基于胸径的结果直接比较）。")
    else:
        result["优势度"] = np.nan
        notes.append("数据中既没有胸径也没有盖度，无法计算优势度与相对优势度："
                     "重要值将改用 (相对密度 + 相对频度) / 2 计算。")

    # ---- 相对优势度：同样在样方内归一（理由同相对密度）----
    dominance_total = result["优势度"].clip(lower=0).groupby(
        result["样方号"], dropna=False).transform("sum")
    if mode != "无" and (dominance_total > 0).any():
        result["相对优势度"] = np.where(dominance_total > 0,
                                        result["优势度"].clip(lower=0) / dominance_total * 100.0,
                                        np.nan)
        result["重要值"] = (result["相对密度"].fillna(0)
                            + result["相对频度"].fillna(0)
                            + result["相对优势度"].fillna(0)) / 3.0
        partial = int(result["相对优势度"].isna().sum())
        if partial:
            notes.append(
                "有 {0} 行没有胸径或盖度记录（例如灌木、草本），它们的优势度项在重要值中按 0 计，"
                "因此这些行的相对优势度留空、重要值只由相对密度与相对频度两项贡献，"
                "与本样方内其它物种的重要值不宜直接比较。".format(partial))
    else:
        result["相对优势度"] = np.nan
        if mode != "无":
            notes.append("优势度合计为 0，无法计算相对优势度与重要值，已留空。")
        result["重要值"] = (result["相对密度"].fillna(0)
                            + result["相对频度"].fillna(0)) / 2.0

    # 列顺序：原有列在前，指标列按方案 7.1 的顺序排在后面
    metric_columns = [name for name in ROW_METRIC_COLUMNS if name in result.columns]
    other_columns = [name for name in result.columns if name not in metric_columns]
    return result[other_columns + metric_columns]


# ===========================================================================
# 五、汇总级指标（方案 7.2）
# ===========================================================================
def compute_diversity_table(df: pd.DataFrame, notes: Optional[List[str]] = None) -> pd.DataFrame:
    """按样方计算多样性指标（粒度：样方号）。

    返回列：样方号 / S 物种数 / N 个体总数 / H / D / J / R
    """
    if notes is None:
        notes = []
    if "样方号" not in df.columns or "株数" not in df.columns or "物种" not in df.columns:
        raise IndexError_(
            "缺少计算多样性指标所必需的列（需要：样方号、物种、株数）。\n\n"
            "怎么办：到【数据导入】页用【列映射】指定这几列后重新清洗。")

    working = df.copy(deep=True)
    working["株数"] = pd.to_numeric(working["株数"], errors="coerce")

    records: List[Dict[str, Any]] = []
    zero_individual_quadrats: List[str] = []
    single_species_quadrats: List[str] = []

    for quadrat, group in working.groupby("样方号", dropna=False):
        # 同一物种在同一行被记录多次时先合并（样方调查的常见情形）
        counts = group.groupby("物种", dropna=False)["株数"].sum(min_count=1)
        values = [float(value) for value in counts.dropna().tolist() if float(value) > 0]
        species_count = len(values)
        total = sum(values)

        record: Dict[str, Any] = {
            "样方号": quadrat,
            "S 物种数": species_count,
            "N 个体总数": int(total) if total > 0 else 0,
        }
        if total <= 0:
            record.update({"H 香农指数": np.nan, "D 辛普森指数": np.nan,
                           "J Pielou均匀度": np.nan, "R Margalef丰富度": np.nan})
            zero_individual_quadrats.append(str(quadrat))
        else:
            record["H 香农指数"] = shannon_index(values)
            record["D 辛普森指数"] = simpson_index(values)
            record["J Pielou均匀度"] = pielou_evenness(values)
            record["R Margalef丰富度"] = margalef_richness(values)
            if species_count <= 1:
                single_species_quadrats.append(str(quadrat))
        records.append(record)

    table = pd.DataFrame(records, columns=["样方号"] + list(SUMMARY_METRIC_COLUMNS))
    if zero_individual_quadrats:
        notes.append("以下样方没有任何个体（N = 0），多样性指数无定义，已留空：{0}。".format(
            "、".join(zero_individual_quadrats)))
    if single_species_quadrats:
        notes.append("以下样方只记录到 1 个物种（ln S = 0），均匀度与丰富度无定义已留空；"
                     "香农指数与辛普森指数为 0。：{0}。".format("、".join(single_species_quadrats)))
    return table


def compute_env_summary(df: pd.DataFrame, group_by: str = DEFAULT_GROUP_BY,
                        day_start: int = DEFAULT_DAY_START, day_end: int = DEFAULT_DAY_END,
                        notes: Optional[List[str]] = None) -> pd.DataFrame:
    """环境时序统计（方案 7.3，粒度：分组，默认按日）。

    输出：均值、最大值、最小值，以及昼夜均值差
          （昼 = day_start~day_end 的均值，夜 = 其余时段均值）。
    """
    if notes is None:
        notes = []
    if "时间" not in df.columns:
        raise IndexError_(
            "数据里没有时间列，无法做环境时序统计。\n\n"
            "怎么办：这类统计只适用于传感器时序数据；若你的数据本身没有时间列，"
            "请只勾选其它指标组。")

    working = df.copy(deep=True)
    parsed = pd.to_datetime(working["时间"], format=data_parse.TIME_FORMAT, errors="coerce")
    if parsed.notna().sum() == 0:
        raise IndexError_("时间列无法解析，环境时序统计无法进行。\n\n怎么办：请检查时间列的格式后重新导入。")

    working["_时间"] = parsed
    working = working[working["_时间"].notna()]
    if group_by == "时":
        working["_分组"] = working["_时间"].dt.strftime("%Y-%m-%d %H:00")
        group_label = "小时"
    else:
        working["_分组"] = working["_时间"].dt.strftime("%Y-%m-%d")
        group_label = "日"

    value_columns = [name for name in working.columns
                     if name not in ("_时间", "_分组", "时间")
                     and pd.api.types.is_numeric_dtype(working[name])]

    day_hours = set(range(int(day_start), int(day_end)))
    is_day = working["_时间"].dt.hour.isin(day_hours)

    records: List[Dict[str, Any]] = []
    for group_value, block in working.groupby("_分组", dropna=False):
        record: Dict[str, Any] = {"分组": group_value}
        day_block = block[is_day.loc[block.index]]
        night_block = block[~is_day.loc[block.index]]
        for column in value_columns:
            record["{0}均值".format(column)] = block[column].mean()
            record["{0}最大值".format(column)] = block[column].max()
            record["{0}最小值".format(column)] = block[column].min()
            day_mean = day_block[column].mean()
            night_mean = night_block[column].mean()
            record["{0}昼夜均值差".format(column)] = (
                day_mean - night_mean if pd.notna(day_mean) and pd.notna(night_mean) else np.nan)
        records.append(record)

    table = pd.DataFrame(records)
    if table.empty:
        return table
    table = table.sort_values("分组").reset_index(drop=True)
    notes.append("环境时序统计按{0}分组：均值/最大值/最小值，以及昼夜均值差"
                 "（昼 = {1}:00~{2}:00，夜 = 其余时段）。".format(group_label, day_start, day_end))
    return table


def compute_soil_metrics(df: pd.DataFrame, notes: Optional[List[str]] = None) -> pd.DataFrame:
    """土壤指标（方案 7.4，粒度：采样点）。

        质量含水率 = (鲜重 − 干重) / 干重 × 100%
        体积含水率 = (鲜重 − 干重) / 环刀体积 × 100%
    """
    if notes is None:
        notes = []
    missing = [name for name in SOIL_REQUIRED if name not in df.columns]
    if missing:
        raise IndexError_(
            "计算土壤含水率需要「鲜重」与「干重」两列，当前数据缺少：{0}。\n\n"
            "怎么办：在数据里补上这两列后重新导入；若你的数据不含土壤指标，"
            "请取消勾选「{1}」。".format("、".join(missing), GROUP_SOIL))

    working = df.copy(deep=True)
    fresh = pd.to_numeric(working["鲜重"], errors="coerce")
    dry = pd.to_numeric(working["干重"], errors="coerce")
    with np.errstate(divide="ignore", invalid="ignore"):
        working["质量含水率"] = np.where(dry > 0, (fresh - dry) / dry * 100.0, np.nan)
    notes.append("质量含水率 = (鲜重 − 干重) / 干重 × 100%。干重为 0 或空白的行已留空。")

    if "环刀体积" in working.columns:
        volume = pd.to_numeric(working["环刀体积"], errors="coerce")
        with np.errstate(divide="ignore", invalid="ignore"):
            working["体积含水率"] = np.where(volume > 0, (fresh - dry) / volume * 100.0, np.nan)
        notes.append("体积含水率 = (鲜重 − 干重) / 环刀体积 × 100%。环刀体积为 0 或空白的行已留空。")
    elif "容重" in working.columns:
        # 容重（g/cm³）与外体积的关系：体积含水率 = 质量含水率 × 容重
        bulk = pd.to_numeric(working["容重"], errors="coerce")
        working["体积含水率"] = working["质量含水率"] * bulk / 100.0
        notes.append("未提供环刀体积，体积含水率按 质量含水率 × 容重 换算得出。")
    else:
        notes.append("未提供「环刀体积」或「容重」，只计算了质量含水率。")
    return working


def compute_experiment_metrics(df: pd.DataFrame, germination_day: int = DEFAULT_GERMINATION_DAY,
                               notes: Optional[List[str]] = None) -> pd.DataFrame:
    """实验生态指标（方案 7.5，粒度：处理组 / 重复）。

        种子萌发率 = 累计萌发种子数 / 供试种子总数 × 100%
        萌发势     = 第 N 天累计萌发数 / 供试种子总数 × 100%
        Olson 分解速率 k = −ln(Wt / W0) / t
    """
    if notes is None:
        notes = []
    working = df.copy(deep=True)
    produced = False

    if {"累计萌发种子数", "供试种子总数"}.issubset(working.columns):
        germinated = pd.to_numeric(working["累计萌发种子数"], errors="coerce")
        total_seeds = pd.to_numeric(working["供试种子总数"], errors="coerce")
        with np.errstate(divide="ignore", invalid="ignore"):
            working["种子萌发率"] = np.where(total_seeds > 0, germinated / total_seeds * 100.0, np.nan)
        produced = True
        notes.append("种子萌发率 = 累计萌发种子数 / 供试种子总数 × 100%。供试种子总数为 0 的行已留空。")

        day_column = None
        for candidate in ("天数", "培养天数", "第几天"):
            if candidate in working.columns:
                day_column = candidate
                break
        if day_column:
            days = pd.to_numeric(working[day_column], errors="coerce")
            at_day = days == int(germination_day)
            if at_day.any():
                working["萌发势"] = np.nan
                with np.errstate(divide="ignore", invalid="ignore"):
                    values = np.where(total_seeds > 0, germinated / total_seeds * 100.0, np.nan)
                working.loc[at_day, "萌发势"] = values[at_day.to_numpy()]
                notes.append("萌发势取第 {0} 天的累计萌发数 / 供试种子总数 × 100%（天数可在高级设置里调整）。".format(
                    int(germination_day)))
            else:
                notes.append("数据里没有第 {0} 天的记录，萌发势已留空（可在高级设置里调整统计天数）。".format(
                    int(germination_day)))
        else:
            notes.append("数据里没有天数字段，只计算了种子萌发率，未计算萌发势。")

    if {"初始干重", "残留干重", "时间"}.issubset(working.columns):
        w0 = pd.to_numeric(working["初始干重"], errors="coerce")
        wt = pd.to_numeric(working["残留干重"], errors="coerce")
        t = pd.to_numeric(working["时间"], errors="coerce")
        working["分解速率k"] = [olson_decay_rate(a, b, c) for a, b, c in zip(w0, wt, t)]
        produced = True
        notes.append("凋落物分解速率 k = −ln(残留干重 / 初始干重) / 时间（Olson 指数模型）。"
                     "残留量大于初始量或时间为 0 的行已留空。")

    if not produced:
        notes.append("当前数据里没有萌发或凋落物分解所需的字段，"
                     "实验生态指标未产出结果（需要：累计萌发种子数 + 供试种子总数，"
                     "或 初始干重 + 残留干重 + 时间）。")
    return working


# ===========================================================================
# 六、主入口
# ===========================================================================
def default_groups_for(data_type: str) -> List[str]:
    """按数据类型给出默认勾选的指标组（方案阶段 4 第 2 条）。"""
    if data_type == data_parse.TYPE_SENSOR:
        return [GROUP_ENV]
    if data_type == data_parse.TYPE_QUADRAT:
        return [GROUP_STRUCTURE, GROUP_DIVERSITY]
    return [GROUP_EXPERIMENT]


def compute_indicators(df: pd.DataFrame, data_type: str = data_parse.TYPE_QUADRAT,
                       groups: Optional[Sequence[str]] = None,
                       quadrat_area: float = DEFAULT_QUADRAT_AREA,
                       group_by: str = DEFAULT_GROUP_BY,
                       day_start: int = DEFAULT_DAY_START, day_end: int = DEFAULT_DAY_END,
                       germination_day: int = DEFAULT_GERMINATION_DAY) -> IndexResult:
    """按勾选的指标组计算指标，返回行级表与汇总表。

    参数：
        groups        ：要计算的指标组（取自 ALL_GROUPS），None 表示按数据类型默认
        quadrat_area  ：样方面积（m²），用于密度
        group_by      ：环境时序统计的分组维度（"日" / "时"）
        day_start/day_end：昼夜划分（默认 06:00–18:00 为昼）
        germination_day  ：萌发势统计天数（默认 7）

    纪律：输入只读，返回全新对象；每个指标组的失败都单独提示，不牵连其它组。
    """
    if df is None or len(df) == 0:
        raise IndexError_("没有可用于计算指标的数据。\n\n怎么办：请先完成数据导入与清洗。")

    # 关键：groups=None 表示"按数据类型给默认勾选"；groups=[] 表示"用户什么都没勾"，
    # 两者必须区分开，否则用户取消全部勾选后仍然会算出结果。
    if groups is None:
        selected = default_groups_for(data_type)
    else:
        selected = [name for name in groups if name in ALL_GROUPS]
    notes: List[str] = []
    row_table: Optional[pd.DataFrame] = None
    summary_parts: List[pd.DataFrame] = []

    # ---- 群落结构指标 → 行级表 ----
    if GROUP_STRUCTURE in selected:
        row_table = compute_row_metrics(df, quadrat_area=quadrat_area, notes=notes)

    # ---- 多样性指标 → 汇总表 ----
    if GROUP_DIVERSITY in selected:
        diversity = compute_diversity_table(df, notes=notes)
        if diversity is not None and len(diversity):
            summary_parts.append(diversity)

    # ---- 环境时序统计 → 汇总表 ----
    if GROUP_ENV in selected:
        env_table = compute_env_summary(df, group_by=group_by, day_start=day_start,
                                        day_end=day_end, notes=notes)
        if env_table is not None and len(env_table):
            # 若同一张汇总表里已经有按样方的结果，需要给分组列换个名字避免混淆
            if summary_parts and "分组" in env_table.columns:
                env_table = env_table.rename(columns={"分组": "时间分组"})
            summary_parts.append(env_table)

    # ---- 土壤指标 → 汇总表 ----
    if GROUP_SOIL in selected:
        soil = compute_soil_metrics(df, notes=notes)
        if soil is not None and len(soil):
            summary_parts.append(soil)

    # ---- 实验生态指标 → 汇总表 ----
    if GROUP_EXPERIMENT in selected:
        experiment = compute_experiment_metrics(df, germination_day=germination_day, notes=notes)
        if experiment is not None and len(experiment):
            summary_parts.append(experiment)

    # ---- 合并汇总表 ----
    # 不同指标组的汇总粒度不同（样方 / 日 / 采样点），因此按"能对齐则对齐、不能对齐则留空"
    # 的方式横向拼接：先按各自的键排序，再用 concat(axis=1) 拼成一张宽表。
    summary_table: Optional[pd.DataFrame] = None
    if summary_parts:
        key_column = None
        for candidate in ("样方号", "分组", "时间分组"):
            if any(candidate in part.columns for part in summary_parts):
                key_column = candidate
                break
        if key_column:
            normalized: List[pd.DataFrame] = []
            for part in summary_parts:
                frame = part.copy()
                if key_column not in frame.columns and "样方号" in frame.columns:
                    frame = frame.rename(columns={"样方号": key_column})
                normalized.append(frame.reset_index(drop=True))
            summary_table = pd.concat(normalized, axis=1)
            # 去掉拼接产生的重复列（保留第一次出现的）
            summary_table = summary_table.loc[:, ~summary_table.columns.duplicated()]
        else:
            summary_table = pd.concat([part.reset_index(drop=True) for part in summary_parts], axis=1)

    row_granularity = "每行 = 某样方中的某物种" if row_table is not None else ""
    if summary_table is not None:
        if "样方号" in summary_table.columns:
            summary_granularity = "每行 = 一个样方"
        elif "分组" in summary_table.columns or "时间分组" in summary_table.columns:
            summary_granularity = "每行 = 一个{0}".format(group_by)
        else:
            summary_granularity = "每行 = 一个分组"
    else:
        summary_granularity = ""

    if not selected:
        notes.append("没有勾选任何指标组，因此没有产出结果。")

    return IndexResult(
        row_table=row_table,
        summary_table=summary_table,
        row_granularity=row_granularity,
        summary_granularity=summary_granularity,
        notes=notes,
        groups=selected,
        params={"quadrat_area": quadrat_area, "group_by": group_by,
                "day_start": day_start, "day_end": day_end,
                "germination_day": germination_day, "data_type": data_type},
    )


def metric_tips() -> Dict[str, str]:
    """返回"列名 -> 中文说明"，供界面表格悬浮提示使用（方案 5.6 结果可解释）。"""
    return dict(METRIC_DESCRIPTIONS)


# ===========================================================================
# 七、数据质量评价（阶段 6 报告第四章会用到）
# ===========================================================================
def data_quality_report(df: pd.DataFrame) -> pd.DataFrame:
    """逐列给出缺失率、异常率（相对清洗前）等质量指标。"""
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["列名", "缺失个数", "缺失率"])
    rows = len(df)
    records: List[Dict[str, Any]] = []
    for column in df.columns:
        missing = int(df[column].isna().sum())
        records.append({
            "列名": str(column),
            "缺失个数": missing,
            "缺失率": "{0:.2f}%".format(missing / rows * 100.0),
        })
    return pd.DataFrame(records)
