# -*- coding: utf-8 -*-
"""
core/eco_index.py 单元测试（开发方案第六章 阶段 4 交付，与代码同阶段交付）

核心判据来自方案第八章"指标公式的手工核算期望值"：
    输入 5, 5        → H=0.693147  D=0.500000  J=1.000000  R=0.434294
    输入 1, 2, 3     → H=1.011404  D=0.611111  J=0.920619  R=1.116222
    Olson：W0=100, Wt=50, t=1 → k=0.693147
全部按 rel=1e-6 断言。

其余覆盖：边界（N=0、S=1、N=1 必须给 NaN 而不是异常或 inf）、
行级指标的公式核对（手工构造可心算的例子）、汇总粒度、输入只读、参数生效。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import data_clean as dc  # noqa: E402
from core import data_parse as dp  # noqa: E402
from core import eco_index as ei  # noqa: E402

SAMPLES = ROOT / "tests" / "samples"
TOL = dict(rel=1e-6, abs=1e-9)


# ===========================================================================
# 一、方案第八章的手工核算期望值（本阶段最重要的验收判据）
# ===========================================================================
class TestHandComputedValues:
    """方案第八章表格逐行核对。

    方案里给的是 6 位小数，因此按 abs=1e-6 核对（rel=1e-6 对 0.434294 这类
    量级会过严：0.4342944819 与 0.434294 的相对差是 1.1e-6）。
    """

    @pytest.mark.parametrize("counts,H,D,J,R", [
        ([5, 5], 0.693147, 0.500000, 1.000000, 0.434294),
        ([1, 2, 3], 1.011404, 0.611111, 0.920619, 1.116222),
    ])
    def test_diversity_indices(self, counts, H, D, J, R):
        assert ei.shannon_index(counts) == pytest.approx(H, abs=1e-6)
        assert ei.simpson_index(counts) == pytest.approx(D, abs=1e-6)
        assert ei.pielou_evenness(counts) == pytest.approx(J, abs=1e-6)
        assert ei.margalef_richness(counts) == pytest.approx(R, abs=1e-6)

    def test_olson_decay_rate(self):
        """W0=100，Wt=50，t=1 → k = 0.693147。"""
        assert ei.olson_decay_rate(100, 50, 1) == pytest.approx(0.693147, abs=1e-6)

    def test_olson_other_points(self):
        # 残留 25%（即又过一个半衰期）时 k 应为 2*ln2
        assert ei.olson_decay_rate(100, 25, 1) == pytest.approx(2 * math.log(2), **TOL)
        # t=2、Wt=25 时速率减半
        assert ei.olson_decay_rate(100, 25, 2) == pytest.approx(math.log(2), **TOL)


# ===========================================================================
# 二、边界处理（方案 7.2 的 warn 明确要求）
# ===========================================================================
class TestBoundaries:
    def test_n_zero_returns_nan(self):
        """样方内没有个体：H、D 无定义，必须返回 NaN 而不是异常或 inf。"""
        for value in (ei.shannon_index([]), ei.shannon_index([0, 0]), ei.simpson_index([])):
            assert math.isnan(value)

    def test_single_species_gives_zero_h_and_d(self):
        """S=1：H = −1×ln1 = 0、D = 1−1 = 0，都是确定值，不该留空。"""
        assert ei.shannon_index([10]) == pytest.approx(0.0, **TOL)
        assert ei.simpson_index([10]) == pytest.approx(0.0, **TOL)

    def test_single_species_j_is_nan_but_r_is_zero(self):
        """S=1：ln S = 0，均匀度无定义 → NaN；而丰富度分子 S−1 = 0，是确定值 0。"""
        assert math.isnan(ei.pielou_evenness([10]))
        assert ei.margalef_richness([10]) == pytest.approx(0.0, abs=1e-12)

    def test_n_one_margalef_is_nan(self):
        """N=1 时 ln N = 0，Margalef 无定义 → NaN。"""
        assert math.isnan(ei.margalef_richness([1]))

    def test_no_inf_anywhere(self):
        """全边界扫描：任何输入都不得产生 inf。"""
        cases = [[], [0], [1], [1, 0], [1, 1], [0, 0, 0], [5, 5], [1, 2, 3], [-3, 5]]
        for counts in cases:
            for func in (ei.shannon_index, ei.simpson_index,
                         ei.pielou_evenness, ei.margalef_richness):
                value = func(counts)
                assert not math.isinf(value), (func.__name__, counts, value)

    def test_olson_invalid_inputs(self):
        assert math.isnan(ei.olson_decay_rate(0, 50, 1))
        assert math.isnan(ei.olson_decay_rate(100, 0, 1))
        assert math.isnan(ei.olson_decay_rate(100, 50, 0))
        assert math.isnan(ei.olson_decay_rate(100, 120, 1))   # 残留量大于初始量
        assert math.isnan(ei.olson_decay_rate(None, 50, 1))

    def test_negative_counts_ignored(self):
        """个体数为负是数据错误（清洗阶段应已处理），计算时不得让它污染结果。"""
        assert ei.shannon_index([10, 20]) == pytest.approx(ei.shannon_index([10, 20, -5]), **TOL)


# ===========================================================================
# 三、行级指标（方案 7.1）
# ===========================================================================
def _quadrat_frame() -> pd.DataFrame:
    """两个样方、三个物种的小表，便于手工核对每个公式。"""
    return pd.DataFrame({
        "样方号": ["Q1", "Q1", "Q1", "Q2", "Q2"],
        "物种": ["甲", "乙", "丙", "甲", "乙"],
        "株数": [10, 20, 30, 5, 15],
        "胸径": [10.0, 20.0, np.nan, 10.0, 20.0],
        "盖度": [10.0, 20.0, 30.0, 10.0, 20.0],
    })


class TestRowMetrics:
    def test_density(self):
        table = ei.compute_row_metrics(_quadrat_frame(), quadrat_area=2.0)
        # 密度 = 株数 / 样方面积
        assert float(table.loc[0, "密度"]) == pytest.approx(5.0)     # 10 / 2
        assert float(table.loc[2, "密度"]) == pytest.approx(15.0)    # 30 / 2

    def test_relative_density(self):
        """相对密度在每个样方内归一（方案 7.1"同一统计范围内"）。"""
        table = ei.compute_row_metrics(_quadrat_frame())
        # Q1 内三个物种：10/60、20/60、30/60
        assert float(table.loc[0, "相对密度"]) == pytest.approx(10 / 60 * 100.0)
        # 每个样方内各物种的相对密度合计为 100%
        sums = table.groupby("样方号")["相对密度"].sum()
        for value in sums:
            assert float(value) == pytest.approx(100.0, rel=1e-9)

    def test_frequency_is_across_quadrats(self):
        """频度 = 出现该物种的样方数 / 总样方数 × 100%，同物种各行取同值。"""
        table = ei.compute_row_metrics(_quadrat_frame())
        # 甲与乙出现在 Q1、Q2（2/2 = 100%）；丙只出现在 Q1（1/2 = 50%）
        assert float(table.loc[table["物种"] == "甲", "频度"].iloc[0]) == pytest.approx(100.0)
        assert float(table.loc[table["物种"] == "乙", "频度"].iloc[0]) == pytest.approx(100.0)
        assert float(table.loc[table["物种"] == "丙", "频度"].iloc[0]) == pytest.approx(50.0)
        # 同一物种在不同样方里取到相同的频度
        jia = table[table["物种"] == "甲"]["频度"].tolist()
        assert jia[0] == pytest.approx(jia[1])

    def test_relative_frequency_sums_to_100(self):
        table = ei.compute_row_metrics(_quadrat_frame())
        species_freq = table.drop_duplicates("物种")["相对频度"]
        assert float(species_freq.sum()) == pytest.approx(100.0)

    def test_dominance_uses_basal_area_when_dbh_present(self):
        table = ei.compute_row_metrics(_quadrat_frame())
        # 优势度 = πd²/4，胸径 10 → 78.5398
        expected = math.pi * 10.0 ** 2 / 4.0
        assert float(table.loc[0, "优势度"]) == pytest.approx(expected, rel=1e-9)
        # 没有胸径的行（丙）优势度必须留空，不能用别的物种顶替
        assert math.isnan(float(table.loc[2, "优势度"]))

    def test_relative_dominance_and_importance_value(self):
        table = ei.compute_row_metrics(_quadrat_frame())
        row = table.loc[0]
        expected_iv = (float(row["相对密度"]) + float(row["相对频度"]) + float(row["相对优势度"])) / 3.0
        assert float(row["重要值"]) == pytest.approx(expected_iv, rel=1e-9)

    def test_importance_value_falls_back_without_dominance(self):
        """没有胸径也没有盖度时，重要值改用 (相对密度 + 相对频度) / 2 并说明。"""
        df = _quadrat_frame().drop(columns=["胸径", "盖度"])
        notes: list = []
        table = ei.compute_row_metrics(df, notes=notes)
        row = table.loc[0]
        expected = (float(row["相对密度"]) + float(row["相对频度"])) / 2.0
        assert float(row["重要值"]) == pytest.approx(expected, rel=1e-9)
        assert any("相对密度 + 相对频度" in note for note in notes)

    def test_dominance_falls_back_to_cover(self):
        df = _quadrat_frame().drop(columns=["胸径"])
        notes: list = []
        table = ei.compute_row_metrics(df, notes=notes)
        assert float(table.loc[0, "优势度"]) == pytest.approx(10.0)     # 直接用盖度
        assert any("盖度" in note and "不再是断面积" in note for note in notes)

    def test_column_order_and_extra_columns_kept(self):
        table = ei.compute_row_metrics(_quadrat_frame())
        assert list(table.columns) == ["样方号", "物种", "株数", "胸径", "盖度"] + list(ei.ROW_METRIC_COLUMNS)

    def test_input_not_modified(self):
        df = _quadrat_frame()
        before = df.copy(deep=True)
        ei.compute_row_metrics(df)
        pd.testing.assert_frame_equal(df, before)

    def test_missing_required_column_raises_readable_error(self):
        df = _quadrat_frame().drop(columns=["株数"])
        with pytest.raises(ei.IndexError_) as excinfo:
            ei.compute_row_metrics(df)
        assert "株数" in str(excinfo.value)
        assert "列映射" in str(excinfo.value)

    def test_zero_quadrat_area_falls_back(self):
        notes: list = []
        table = ei.compute_row_metrics(_quadrat_frame(), quadrat_area=0, notes=notes)
        assert float(table.loc[0, "密度"]) == pytest.approx(10.0)      # 用默认 1 m²
        assert any("样方面积" in note for note in notes)


# ===========================================================================
# 四、汇总级指标（方案 7.2）
# ===========================================================================
class TestDiversityTable:
    def test_columns_and_rows(self):
        table = ei.compute_diversity_table(_quadrat_frame())
        assert list(table.columns) == ["样方号"] + list(ei.SUMMARY_METRIC_COLUMNS)
        assert len(table) == 2                       # 两个样方

    def test_values_match_manual_calculation(self):
        table = ei.compute_diversity_table(_quadrat_frame())
        q1 = table[table["样方号"] == "Q1"].iloc[0]
        assert int(q1["S 物种数"]) == 3
        assert int(q1["N 个体总数"]) == 60
        assert float(q1["H 香农指数"]) == pytest.approx(ei.shannon_index([10, 20, 30]), **TOL)
        assert float(q1["D 辛普森指数"]) == pytest.approx(ei.simpson_index([10, 20, 30]), **TOL)

    def test_quadrat_with_zero_individuals(self):
        df = pd.DataFrame({"样方号": ["Q1", "Q2"], "物种": ["甲", "乙"], "株数": [5, 0]})
        notes: list = []
        table = ei.compute_diversity_table(df, notes=notes)
        q2 = table[table["样方号"] == "Q2"].iloc[0]
        assert int(q2["S 物种数"]) == 0
        assert int(q2["N 个体总数"]) == 0
        assert math.isnan(float(q2["H 香农指数"]))
        assert any("没有任何个体" in note for note in notes)

    def test_single_species_quadrat_reported(self):
        df = pd.DataFrame({"样方号": ["Q1", "Q2"], "物种": ["甲", "乙"], "株数": [5, 8]})
        notes: list = []
        table = ei.compute_diversity_table(df, notes=notes)
        assert float(table.loc[0, "H 香农指数"]) == pytest.approx(0.0, abs=1e-12)
        assert math.isnan(float(table.loc[0, "J Pielou均匀度"]))
        # S=1 时 (S−1)/ln N = 0，是确定值
        assert float(table.loc[0, "R Margalef丰富度"]) == pytest.approx(0.0, abs=1e-12)
        assert any("1 个物种" in note for note in notes)

    def test_duplicate_species_rows_are_summed(self):
        """同一样方同一物种被记录多次时应先合并再算指数。"""
        df = pd.DataFrame({"样方号": ["Q1", "Q1"], "物种": ["甲", "甲"], "株数": [5, 5]})
        table = ei.compute_diversity_table(df)
        assert int(table.loc[0, "N 个体总数"]) == 10


# ===========================================================================
# 五、环境时序统计（方案 7.3）
# ===========================================================================
class TestEnvSummary:
    def _sensor(self) -> pd.DataFrame:
        times = pd.date_range("2024-06-01 00:00:00", periods=48, freq="h")
        values = []
        for moment in times:
            values.append(20.0 if 6 <= moment.hour < 18 else 10.0)   # 昼 20、夜 10
        return pd.DataFrame({"时间": times.strftime(dp.TIME_FORMAT), "气温": values})

    def test_grouped_by_day(self):
        table = ei.compute_env_summary(self._sensor())
        assert "分组" in table.columns
        assert len(table) == 2                       # 两天
        assert list(table["分组"]) == ["2024-06-01", "2024-06-02"]

    def test_day_night_difference(self):
        table = ei.compute_env_summary(self._sensor())
        # 昼 20、夜 10 → 昼夜均值差 = 10
        assert float(table.loc[0, "气温昼夜均值差"]) == pytest.approx(10.0)

    def test_grouped_by_hour(self):
        table = ei.compute_env_summary(self._sensor(), group_by="时")
        assert len(table) == 48                      # 每小时一行
        assert all(":" in str(value) for value in table["分组"])

    def test_custom_day_window(self):
        """把昼设成 12:00~13:00，昼夜均值差应随之变化。"""
        table = ei.compute_env_summary(self._sensor(), day_start=12, day_end=13)
        assert float(table.loc[0, "气温昼夜均值差"]) != pytest.approx(10.0)

    def test_without_time_column_raises(self):
        with pytest.raises(ei.IndexError_) as excinfo:
            ei.compute_env_summary(pd.DataFrame({"气温": [1.0]}))
        assert "时间列" in str(excinfo.value)


# ===========================================================================
# 六、土壤与实验指标（方案 7.4、7.5）
# ===========================================================================
class TestSoilAndExperiment:
    def test_mass_water_content(self):
        df = pd.DataFrame({"采样点": ["S1"], "鲜重": [120.0], "干重": [100.0]})
        table = ei.compute_soil_metrics(df)
        assert float(table.loc[0, "质量含水率"]) == pytest.approx(20.0)

    def test_volumetric_water_content(self):
        df = pd.DataFrame({"采样点": ["S1"], "鲜重": [120.0], "干重": [100.0], "环刀体积": [100.0]})
        table = ei.compute_soil_metrics(df)
        assert float(table.loc[0, "体积含水率"]) == pytest.approx(20.0)

    def test_soil_missing_columns_raises(self):
        with pytest.raises(ei.IndexError_) as excinfo:
            ei.compute_soil_metrics(pd.DataFrame({"采样点": ["S1"]}))
        assert "鲜重" in str(excinfo.value) and "干重" in str(excinfo.value)

    def test_germination_rate_and_vigor(self):
        df = pd.DataFrame({
            "处理组": ["对照", "对照", "对照"],
            "天数": [3, 7, 14],
            "累计萌发种子数": [10, 30, 45],
            "供试种子总数": [50, 50, 50],
        })
        table = ei.compute_experiment_metrics(df, germination_day=7)
        assert float(table.loc[1, "种子萌发率"]) == pytest.approx(60.0)
        assert float(table.loc[1, "萌发势"]) == pytest.approx(60.0)     # 第 7 天
        assert math.isnan(float(table.loc[0, "萌发势"]))                 # 非第 7 天留空

    def test_decay_rate_column(self):
        df = pd.DataFrame({"初始干重": [100.0], "残留干重": [50.0], "时间": [1.0]})
        table = ei.compute_experiment_metrics(df)
        assert float(table.loc[0, "分解速率k"]) == pytest.approx(0.693147, **TOL)

    def test_notes_when_fields_absent(self):
        notes: list = []
        ei.compute_experiment_metrics(pd.DataFrame({"处理组": ["对照"]}), notes=notes)
        assert any("没有萌发" in note for note in notes)


# ===========================================================================
# 七、主入口：分组选择、两张表、粒度说明
# ===========================================================================
class TestComputeIndicators:
    def test_quadrat_produces_two_tables(self):
        result = ei.compute_indicators(_quadrat_frame(), data_type=dp.TYPE_QUADRAT)
        assert result.has_row_table() and result.has_summary_table()
        assert result.row_table is not None and "重要值" in result.row_table.columns
        assert result.summary_table is not None and "H 香农指数" in result.summary_table.columns

    def test_granularity_labels(self):
        result = ei.compute_indicators(_quadrat_frame(), data_type=dp.TYPE_QUADRAT)
        assert "样方" in result.row_granularity and "物种" in result.row_granularity
        assert "样方" in result.summary_granularity

    def test_default_groups_by_data_type(self):
        assert ei.default_groups_for(dp.TYPE_QUADRAT) == [ei.GROUP_STRUCTURE, ei.GROUP_DIVERSITY]
        assert ei.default_groups_for(dp.TYPE_SENSOR) == [ei.GROUP_ENV]
        assert ei.default_groups_for(dp.TYPE_PLAIN) == [ei.GROUP_EXPERIMENT]

    def test_sensor_produces_summary_only(self):
        df = pd.DataFrame({
            "时间": pd.date_range("2024-06-01", periods=24, freq="h").strftime(dp.TIME_FORMAT),
            "气温": list(range(24)),
        })
        result = ei.compute_indicators(df, data_type=dp.TYPE_SENSOR)
        assert result.has_row_table() is False
        assert result.has_summary_table() is True
        assert "均值" in "".join(result.summary_table.columns)

    def test_selecting_structure_only(self):
        result = ei.compute_indicators(_quadrat_frame(), data_type=dp.TYPE_QUADRAT,
                                       groups=[ei.GROUP_STRUCTURE])
        assert result.has_row_table() is True
        assert result.has_summary_table() is False

    def test_selecting_diversity_only(self):
        result = ei.compute_indicators(_quadrat_frame(), data_type=dp.TYPE_QUADRAT,
                                       groups=[ei.GROUP_DIVERSITY])
        assert result.has_row_table() is False
        assert result.has_summary_table() is True

    def test_empty_selection_yields_no_tables(self):
        result = ei.compute_indicators(_quadrat_frame(), data_type=dp.TYPE_QUADRAT, groups=[])
        assert result.has_row_table() is False
        assert result.has_summary_table() is False

    def test_empty_data_raises(self):
        with pytest.raises(ei.IndexError_) as excinfo:
            ei.compute_indicators(pd.DataFrame(), data_type=dp.TYPE_QUADRAT)
        assert "导入" in str(excinfo.value) or "清洗" in str(excinfo.value)

    def test_params_reported_back(self):
        result = ei.compute_indicators(_quadrat_frame(), quadrat_area=25.0)
        assert result.params["quadrat_area"] == 25.0
        assert isinstance(result.notes, list)

    def test_input_not_modified(self):
        df = _quadrat_frame()
        before = df.copy(deep=True)
        ei.compute_indicators(df)
        pd.testing.assert_frame_equal(df, before)

    def test_metric_tips_cover_all_columns(self):
        tips = ei.metric_tips()
        for column in ei.ROW_METRIC_COLUMNS + ei.SUMMARY_METRIC_COLUMNS:
            assert column in tips, column
            assert len(tips[column]) > 20


# ===========================================================================
# 八、固定样本的端到端指标（阶段验收判据）
# ===========================================================================
class TestSampleEndToEnd:
    def _cleaned_quadrat(self) -> pd.DataFrame:
        raw = dp.parse_files([SAMPLES / "sample_quadrat.csv"]).df
        return dc.clean_data(raw).df

    def test_row_table_shape(self):
        result = ei.compute_indicators(self._cleaned_quadrat(), data_type=dp.TYPE_QUADRAT,
                                       quadrat_area=1.0)
        table = result.row_table
        assert len(table) == 36                       # 6 样方 × 6 物种
        assert table["样方号"].nunique() == 6
        assert table["物种"].nunique() == 6
        for column in ei.ROW_METRIC_COLUMNS:
            assert column in table.columns

    def test_summary_table_shape_and_ranges(self):
        result = ei.compute_indicators(self._cleaned_quadrat(), data_type=dp.TYPE_QUADRAT)
        table = result.summary_table
        assert len(table) == 6
        assert list(table["样方号"]) == ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6"]
        # 多样性指数必须落在物理合理区间
        assert bool((table["H 香农指数"] >= 0).all())
        assert bool((table["D 辛普森指数"] >= 0).all()) and bool((table["D 辛普森指数"] <= 1).all())
        assert bool((table["J Pielou均匀度"] > 0).all()) and bool((table["J Pielou均匀度"] <= 1).all())
        assert bool((table["R Margalef丰富度"] > 0).all())
        assert bool((table["S 物种数"] == 6).all())

    def test_importance_value_ranks_species(self):
        """重要值排序必须反映群落的实际优势状况（可手工核对）。

        样方样本中辽东栎的相对密度、相对频度与断面积三项都不低，
        综合下来重要值最高；这一断言同时防止"三项权重被写错"这类错误。
        """
        result = ei.compute_indicators(self._cleaned_quadrat(), data_type=dp.TYPE_QUADRAT)
        by_species = result.row_table.groupby("物种")["重要值"].mean().sort_values(ascending=False)
        assert by_species.index[0] == "辽东栎", by_species.to_dict()
        assert bool((by_species > 0).all())
        # 重要值是三项百分比的平均，因此单个样方内各物种重要值之和应为 100
        per_quadrat = result.row_table.groupby("样方号")["重要值"].sum()
        for value in per_quadrat:
            assert float(value) == pytest.approx(100.0, abs=1e-6)

    def test_relative_density_sums_to_100_per_quadrat(self):
        result = ei.compute_indicators(self._cleaned_quadrat(), data_type=dp.TYPE_QUADRAT)
        sums = result.row_table.groupby("样方号")["相对密度"].sum()
        for value in sums:
            assert float(value) == pytest.approx(100.0, rel=1e-9)

    def test_demo_data_end_to_end(self):
        """用内置示例数据跑一遍：12 个样方、6 个物种、指数在合理区间。"""
        raw = dp.parse_files([ROOT / "static" / "demo" / "demo_quadrat.csv"]).df
        cleaned = dc.clean_data(raw).df
        result = ei.compute_indicators(cleaned, data_type=dp.TYPE_QUADRAT, quadrat_area=100.0)
        assert len(result.row_table) == 72
        assert len(result.summary_table) == 12
        # 样方面积 100 m² 时密度应明显小于按 1 m² 计算的结果
        density_100 = float(result.row_table["密度"].max())
        result_1 = ei.compute_indicators(cleaned, data_type=dp.TYPE_QUADRAT, quadrat_area=1.0)
        assert density_100 < float(result_1.row_table["密度"].max())

    def test_sensor_sample_env_summary(self):
        raw = dp.parse_files([SAMPLES / "sample_sensor.xlsx"]).df
        cleaned = dc.clean_data(raw, data_type=dp.TYPE_SENSOR).df
        result = ei.compute_indicators(cleaned, data_type=dp.TYPE_SENSOR)
        table = result.summary_table
        assert len(table) >= 10                       # 10 天数据
        assert "气温昼夜均值差" in table.columns
        # 白天气温高于夜间，差值应为正
        assert float(table["气温昼夜均值差"].mean()) > 0
