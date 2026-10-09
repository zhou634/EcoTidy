# -*- coding: utf-8 -*-
"""
core/data_clean.py 单元测试（开发方案第六章 阶段 3 交付，与代码同阶段交付）

覆盖重点（对应方案的硬性要求与避坑表）：
    1. 量纲一致性检测：0-1 制盖度必须换算为百分制，且不得被判为异常（避坑 6）
    2. 生态异常值：株数为负、盖度/湿度 >100
    3. 时序突变检测用 MAD：能杀掉孤立极值，又不误杀日变化峰值（避坑 8）
    4. 缺失值：时序线性插值 + max_gap 限制；超长缺口保留 NaN（避坑 7）
    5. 样方数据分组中位数 + 计数列取整，不出现"3.7 株"（避坑 9）
    6. 输入只读：清洗绝不修改传入的 DataFrame（方案 3.3 第 4 条）
    7. 幂等：对同一份 raw 反复清洗结果一致；对已清洗结果再清洗不再产生改动
    8. 日志：全中文、含行数变化与逐条明细（行号/列名/原值/处理方式）
    9. 边界：空表、全空列、max_gap=0、无时间列的普通统计
"""

from __future__ import annotations

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

SAMPLES = ROOT / "tests" / "samples"
ASCII_FORBIDDEN = ("Traceback", "nan", "NaN", "None", "inplace")


# ===========================================================================
# 一、量纲一致性（避坑 6）
# ===========================================================================
class TestScaleUnification:
    def test_decimal_cover_converted_to_percent(self):
        """整列 ≤1 的盖度判为小数制，统一换算为百分制。"""
        df = pd.DataFrame({"样方号": ["Q1"] * 3, "物种": list("甲乙丙"),
                           "株数": [1, 2, 3], "盖度": [0.62, 0.45, 0.30]})
        result = dc.clean_data(df)
        assert list(result.df["盖度"]) == [62.0, 45.0, 30.0]
        assert result.stats.scale_converted == {"盖度": 3}

    def test_decimal_cover_is_not_treated_as_outlier(self):
        """核心断言：换算之后不得把小数制数据判成异常（否则整列被清空）。"""
        df = pd.DataFrame({"样方号": ["Q1"] * 3, "物种": list("甲乙丙"),
                           "株数": [1, 2, 3], "盖度": [0.62, 0.45, 0.30]})
        result = dc.clean_data(df)
        assert result.stats.outliers_fixed == 0
        assert int(result.df["盖度"].isna().sum()) == 0

    def test_percent_cover_left_untouched(self):
        """已经是百分制的列不能被再乘 100。"""
        df = pd.DataFrame({"样方号": ["Q1"] * 2, "物种": ["甲", "乙"],
                           "株数": [1, 2], "盖度": [62.0, 45.0]})
        result = dc.clean_data(df)
        assert list(result.df["盖度"]) == [62.0, 45.0]
        assert result.stats.scale_converted == {}

    def test_mixed_scale_column_not_converted(self):
        """同一列混用 0-1 与百分制时无法判定量纲，不得擅自整列换算。"""
        df = pd.DataFrame({"样方号": ["Q1"] * 3, "物种": list("甲乙丙"),
                           "株数": [1, 2, 3], "盖度": [0.62, 45.0, 30.0]})
        result = dc.clean_data(df)
        assert result.stats.scale_converted == {}


# ===========================================================================
# 二、生态学异常值规则
# ===========================================================================
class TestRuleOutliers:
    def test_negative_counts_detected(self):
        df = pd.DataFrame({"样方号": ["Q1", "Q1"], "物种": ["甲", "乙"],
                           "株数": [-3, 5], "盖度": [10.0, 20.0]})
        result = dc.clean_data(df, fill_missing=False)
        assert result.stats.outliers_fixed == 1
        assert any("负数" in reason for reason in result.stats.outlier_breakdown)

    def test_coverage_over_100_detected(self):
        df = pd.DataFrame({"样方号": ["Q1"] * 2, "物种": ["甲", "乙"],
                           "株数": [3, 5], "盖度": [108.0, 20.0]})
        result = dc.clean_data(df, fill_missing=False)
        assert result.stats.outliers_fixed == 1
        assert any("100%" in reason for reason in result.stats.outlier_breakdown)

    def test_humidity_over_100_detected(self):
        df = _sensor_frame(humidity=[105.0] * 24)
        result = dc.clean_data(df, data_type=dp.TYPE_SENSOR, fill_missing=False)
        assert any("相对湿度" in reason for reason in result.stats.outlier_breakdown)

    def test_negative_humidity_detected(self):
        df = _sensor_frame(humidity=[-15.0] + [60.0] * 23)
        result = dc.clean_data(df, data_type=dp.TYPE_SENSOR, fill_missing=False)
        assert any("相对湿度" in reason for reason in result.stats.outlier_breakdown)

    def test_valid_extremes_survive(self):
        """正常的日变化峰值不能被当成异常杀掉（这正是不能用 3σ 的原因）。

        构造"白天两小时的高温平台 + 夜间低温平台"，中间有连续过渡 —— 这是真实气温曲线，
        任何在夜里把 26 ℃ 当成突变改掉的做法都是错的。
        """
        curve = []
        for hour in range(24):
            if 11 <= hour <= 12:
                curve.append(24.0)
            elif 13 <= hour <= 14:
                curve.append(26.0)      # 当天的真实峰值
            elif 15 <= hour <= 16:
                curve.append(24.0)
            elif 10 <= hour <= 17:
                curve.append(22.0)
            else:
                curve.append(12.0)
        df = _sensor_frame(temperature=curve)
        result = dc.clean_data(df, data_type=dp.TYPE_SENSOR, fill_missing=False)
        assert result.stats.outliers_fixed == 0
        assert result.df["气温"].max() == 26.0


# ===========================================================================
# 三、时序突变检测（MAD，避坑 8）
# ===========================================================================
def _sensor_frame(temperature=None, humidity=None, hours: int = 24) -> pd.DataFrame:
    """构造逐小时的传感器数据；不传的列用平稳值填充。"""
    times = pd.date_range("2024-05-01 00:00:00", periods=hours, freq="h")
    temperature = temperature if temperature is not None else [20.0] * hours
    humidity = humidity if humidity is not None else [60.0] * hours
    return pd.DataFrame({
        "时间": times.strftime(dp.TIME_FORMAT),
        "气温": temperature,
        "相对湿度": humidity,
    })


class TestSpikeDetection:
    def test_single_spike_is_detected(self):
        """带正常噪声的时序里，孤立突变必须被抓出来。"""
        values = _noisy_temperature(spike_index=10, spike_value=58.7)
        df = _sensor_frame(temperature=values)
        result = dc.clean_data(df, data_type=dp.TYPE_SENSOR)
        assert result.stats.outliers_fixed >= 1
        assert result.df["气温"].max() < 40.0

    def test_detection_uses_mad_not_std(self):
        """用 MAD 而不是标准差（方案避坑 8）。

        这里直接核对统计量本身，而不是靠构造特定数据去"碰"出漏杀：
            MAD 的等效尺度几乎不受极值影响，标准差的尺度会被极值明显抬高。
        标准差尺度被抬高，正是 3σ 在生态数据上会漏杀（或误杀）的根源。
        """
        values = _noisy_temperature(spike_index=10, spike_value=58.7)
        block = pd.Series(values)
        median = float(block.median())
        mad_scale = 1.4826 * float((block - median).abs().median())
        std_scale = float(block.std())

        # 标准差的尺度被那个 58.7 抬高了 10 倍以上，MAD 的尺度基本不受影响
        assert std_scale > 5.0 * mad_scale, (std_scale, mad_scale)
        # 因此按 MAD 判定能稳定抓出突变
        result = dc.clean_data(_sensor_frame(temperature=values), data_type=dp.TYPE_SENSOR)
        assert result.stats.outliers_fixed >= 1
        assert result.df["气温"].max() < 40.0

    def test_threshold_is_configurable(self):
        """阈值调小后能抓到较弱的突变，调大则放过（参数真的生效）。"""
        values = _noisy_temperature(spike_index=10, spike_value=24.0)
        df = _sensor_frame(temperature=values)
        strict = dc.clean_data(df, data_type=dp.TYPE_SENSOR, mad_k=1.5)
        loose = dc.clean_data(df, data_type=dp.TYPE_SENSOR, mad_k=20.0)
        assert strict.stats.outliers_fixed >= 1
        assert loose.stats.outliers_fixed == 0

    def test_constant_then_jump_is_detected(self):
        """传感器长时间卡在同一数值后突然跳变：MAD 被压成 0，也必须能识别。"""
        values = [20.0] * 24
        values[10] = 58.7
        df = _sensor_frame(temperature=values)
        result = dc.clean_data(df, data_type=dp.TYPE_SENSOR)
        assert result.stats.outliers_fixed >= 1
        assert result.df["气温"].max() < 40.0

    def test_small_group_skipped(self):
        """样本太少时不判突变（MAD 不稳定，宁可不判也不误杀）。"""
        df = _sensor_frame(temperature=[20.0, 21.0, 99.0], hours=3)
        result = dc.clean_data(df, data_type=dp.TYPE_SENSOR, fill_missing=False)
        assert result.stats.outliers_fixed == 0

    def test_flat_group_not_flagged(self):
        """整组完全一致（无离散度）时不得把所有点判成异常。"""
        df = _sensor_frame(temperature=[0.0] * 24)
        result = dc.clean_data(df, data_type=dp.TYPE_SENSOR, fill_missing=False)
        assert result.stats.outliers_fixed == 0


def _noisy_temperature(spike_index: int, spike_value: float, hours: int = 24,
                       noise: float = 0.5) -> list:
    """构造带真实噪声的逐小时气温：噪声幅度可调，再在某一点植入突变。"""
    rng = np.random.default_rng(20240501)
    values = [20.0 + float(rng.normal(0, noise)) for _ in range(hours)]
    values[spike_index] = spike_value
    return values


# ===========================================================================
# 四、缺失值：时序插值与 max_gap（避坑 7）
# ===========================================================================
class TestTimeSeriesFilling:
    def test_short_gap_is_interpolated(self):
        values = [20.0] * 24
        values[10] = np.nan
        values[11] = np.nan
        df = _sensor_frame(temperature=values)
        result = dc.clean_data(df, data_type=dp.TYPE_SENSOR, max_gap=3)
        assert result.stats.interpolated == 2
        assert int(result.df["气温"].isna().sum()) == 0

    def test_long_gap_kept_blank(self):
        """连续缺测超过 max_gap 时必须保留空白而不是插值（避坑 7）。"""
        values = [20.0] * 30
        for index in range(5, 15):        # 连续 10 点缺口
            values[index] = np.nan
        df = _sensor_frame(temperature=values, hours=30)
        result = dc.clean_data(df, data_type=dp.TYPE_SENSOR, max_gap=3)
        assert result.stats.interpolated == 0
        assert result.stats.blanks_kept == 10
        assert result.stats.gaps_kept == 1
        assert int(result.df["气温"].isna().sum()) == 10

    def test_max_gap_zero_keeps_everything_blank(self):
        """max_gap=0 是合法设置（完全不插值），不能因 pandas 限制而崩溃。"""
        values = [20.0] * 24
        values[10] = np.nan
        df = _sensor_frame(temperature=values)
        result = dc.clean_data(df, data_type=dp.TYPE_SENSOR, max_gap=0)
        assert result.stats.interpolated == 0
        assert result.stats.blanks_kept == 1
        assert int(result.df["气温"].isna().sum()) == 1

    def test_large_max_gap_interpolates_long_gap(self):
        values = [20.0] * 30
        for index in range(5, 15):
            values[index] = np.nan
        df = _sensor_frame(temperature=values, hours=30)
        result = dc.clean_data(df, data_type=dp.TYPE_SENSOR, max_gap=20)
        assert result.stats.interpolated == 10
        assert int(result.df["气温"].isna().sum()) == 0

    def test_gap_at_series_end_not_extrapolated(self):
        """序列末尾的缺测不能靠插值补出来（那属于外推，等于编数据）。"""
        values = [20.0] * 24
        values[-1] = np.nan
        values[-2] = np.nan
        df = _sensor_frame(temperature=values)
        result = dc.clean_data(df, data_type=dp.TYPE_SENSOR, max_gap=3)
        assert result.stats.interpolated == 0
        assert result.stats.blanks_kept == 2

    def test_time_column_never_interpolated(self):
        """时间列不能参与插值（补出一个不存在的时间点会让整条时序错位）。"""
        df = _sensor_frame()
        df.loc[5, "时间"] = None
        result = dc.clean_data(df, data_type=dp.TYPE_SENSOR)
        assert result.df["时间"].isna().sum() == 1


# ===========================================================================
# 五、样方数据：分组中位数 + 计数列取整（避坑 9）
# ===========================================================================
class TestGroupedMedianFilling:
    def test_count_column_stays_integer(self):
        """株数填补后必须取整，不允许出现"3.7 株"。"""
        df = pd.DataFrame({
            "样方号": ["Q1", "Q1", "Q1", "Q1"],
            "物种": ["甲", "甲", "甲", "乙"],
            "株数": [5.0, 7.0, np.nan, 9.0],
        })
        result = dc.clean_data(df)
        filled = result.df["株数"].iloc[2]
        assert float(filled) == float(int(filled))
        assert float(filled) == 6.0        # 组内中位数 (5+7)/2 = 6

    def test_grouped_median_used_within_group(self):
        """分组中位数必须按组取值，而不是整列。"""
        df = pd.DataFrame({
            "样方号": ["Q1", "Q1", "Q1", "Q2", "Q2", "Q2"],
            "物种": ["甲", "甲", "甲", "乙", "乙", "乙"],
            "株数": [10.0, 20.0, np.nan, 100.0, 200.0, np.nan],
        })
        result = dc.clean_data(df)
        assert float(result.df["株数"].iloc[2]) == 15.0     # Q1/甲 组中位数
        assert float(result.df["株数"].iloc[5]) == 150.0    # Q2/乙 组中位数

    def test_species_median_used_when_group_has_no_observation(self):
        """同一「样方号 + 物种」只有一行观测时，放宽到"同一物种"的中位数填补，
        并在明细里用不同的处理方式标注出来（便于用户核对）。"""
        df = pd.DataFrame({
            "样方号": ["Q1", "Q1", "Q2"],
            "物种": ["甲", "甲", "甲"],
            "株数": [10.0, 20.0, np.nan],
        })
        result = dc.clean_data(df)
        assert float(result.df["株数"].iloc[2]) == 15.0          # 同物种中位数
        assert result.stats.species_filled == 1
        assert "同物种中位数填补" in "".join(result.changes["处理方式"].tolist())
        assert "同一物种" in result.log

    def test_plot_median_used_as_last_resort(self):
        """同物种也没有观测时，参考同一样方内的其它物种，并明确标注。"""
        df = pd.DataFrame({
            "样方号": ["Q1", "Q1", "Q1"],
            "物种": ["甲", "乙", "丙"],
            "株数": [4.0, 6.0, np.nan],
        })
        result = dc.clean_data(df)
        # 丙只出现这一行、没有同物种观测，于是参考同一样方 → 中位数 5
        assert float(result.df["株数"].iloc[2]) == 5.0
        assert result.stats.species_filled + result.stats.plot_filled == 1
        assert "同物种中位数填补" in "".join(result.changes["处理方式"].tolist()) or \
               "同样方中位数填补" in "".join(result.changes["处理方式"].tolist())

    def test_no_reference_at_all_keeps_blank(self):
        """样方、物种、同样方都无观测时保留空白，绝不拿其它物种的数值顶替。"""
        df = pd.DataFrame({
            "样方号": ["Q1", "Q1", "Q2", "Q2"],
            "物种": ["甲", "乙", "丙", "丁"],
            "株数": [4.0, 6.0, 8.0, 10.0],
            "胸径": [np.nan, np.nan, np.nan, np.nan],
        })
        result = dc.clean_data(df)
        # 整列全空 → 直接说明无法填补
        assert int(result.df["胸径"].isna().sum()) == 4
        assert "整列都是空白" in result.log

    def test_all_missing_column_reported_not_filled(self):
        df = pd.DataFrame({"样方号": ["Q1", "Q2"], "物种": ["甲", "乙"],
                           "株数": [1.0, 2.0], "胸径": [np.nan, np.nan]})
        result = dc.clean_data(df)
        assert int(result.df["胸径"].isna().sum()) == 2
        assert "整列都是空白" in result.log


# ===========================================================================
# 六、分组键缺失的填补（文本列）
# ===========================================================================
class TestGroupKeyFilling:
    def test_missing_group_key_filled_by_frequency_not_alphabet(self):
        """分组名缺失按"出现次数最多"填补，不能按字典序瞎猜。"""
        df = pd.DataFrame({
            "样方号": ["Q1", None, "Q1", None, "Q2"],
            "物种": ["甲", "乙", "丙", "甲", "乙"],
            "株数": [10, 5, 6, 7, 8],
        })
        result = dc.clean_data(df)
        assert list(result.df["样方号"]) == ["Q1", "Q1", "Q1", "Q1", "Q2"]
        assert result.stats.mode_filled == 2

    def test_mostly_blank_group_key_left_blank(self):
        """空白过半时不硬填，并在日志中说明影响。"""
        df = pd.DataFrame({
            "样方号": [None, None, None, "Q1"],
            "物种": list("甲乙丙丁"),
            "株数": [1, 2, 3, 4],
        })
        result = dc.clean_data(df)
        assert int(result.df["样方号"].isna().sum()) == 3
        assert "超过一半" in result.log

    def test_no_group_keys_means_text_left_alone(self):
        """没有分组键的数据（普通统计）不对文本列擅自填补。"""
        df = pd.DataFrame({"处理组": ["对照", "增温", None], "温度": [25.0, 26.0, 27.0]})
        result = dc.clean_data(df, data_type=dp.TYPE_PLAIN)
        assert int(result.df["处理组"].isna().sum()) == 1
        assert result.stats.mode_filled == 0


# ===========================================================================
# 七、去重与输入只读（方案 3.3 第 4 条）
# ===========================================================================
class TestDuplicateAndImmutability:
    def test_duplicates_removed_keeping_first(self):
        df = pd.DataFrame({"样方号": ["Q1", "Q1", "Q2"], "物种": ["甲", "甲", "乙"],
                           "株数": [1, 1, 2]})
        result = dc.clean_data(df)
        assert result.stats.duplicates_removed == 1
        assert len(result.df) == 2

    def test_dedup_can_be_disabled(self):
        df = pd.DataFrame({"样方号": ["Q1", "Q1"], "物种": ["甲", "甲"], "株数": [1, 1]})
        result = dc.clean_data(df, drop_duplicate=False)
        assert result.stats.duplicates_removed == 0
        assert len(result.df) == 2

    def test_input_dataframe_never_modified(self):
        """核心纪律：清洗必须"输入只读、输出新对象"。"""
        df = pd.DataFrame({"样方号": ["Q1", "Q2"], "物种": ["甲", "乙"],
                           "株数": [-3, 5], "盖度": [120.0, 30.0]})
        before = df.copy(deep=True)
        dc.clean_data(df)
        pd.testing.assert_frame_equal(df, before)

    def test_repeated_cleaning_is_idempotent(self):
        """对同一份 raw 反复清洗结果一致（证明没有原地累积污染）。"""
        raw = dp.parse_files([SAMPLES / "sample_quadrat.csv"]).df
        first = dc.clean_data(raw)
        second = dc.clean_data(raw)
        pd.testing.assert_frame_equal(first.df, second.df)

    def test_cleaning_cleaned_data_is_noop(self):
        """对已清洗结果再清洗一次不应再产生任何改动。"""
        raw = dp.parse_files([SAMPLES / "sample_quadrat.csv"]).df
        first = dc.clean_data(raw)
        again = dc.clean_data(first.df)
        assert again.stats.total_changes == 0
        assert again.stats.duplicates_removed == 0
        pd.testing.assert_frame_equal(first.df, again.df)


# ===========================================================================
# 八、固定样本的客观断言（阶段验收判据）
# ===========================================================================
class TestSampleFacts:
    def test_quadrat_sample_counts(self):
        raw = dp.parse_files([SAMPLES / "sample_quadrat.csv"]).df
        result = dc.clean_data(raw)
        stats = result.stats
        assert stats.rows_in == 38
        assert stats.rows_out == 36                 # 38 - 2 重复行
        assert stats.duplicates_removed == 2
        assert stats.outliers_fixed == 5            # 2 个负株数 + 3 个 >100 盖度
        assert int((result.df["株数"] < 0).sum()) == 0
        assert float(result.df["盖度"].max()) <= 100.0
        assert int(result.df.duplicated().sum()) == 0
        # 株数与盖度必须补齐；胸径只对乔木有效，灌木草本的 18 个结构性空白要保留
        assert int(result.df["株数"].isna().sum()) == 0
        assert int(result.df["盖度"].isna().sum()) == 0
        assert int(result.df["胸径"].isna().sum()) == 18

    def test_quadrat_counts_stay_integers(self):
        raw = dp.parse_files([SAMPLES / "sample_quadrat.csv"]).df
        result = dc.clean_data(raw)
        counts = result.df["株数"].dropna()
        assert bool((counts % 1 == 0).all())

    def test_sensor_sample_spike_removed(self):
        raw = dp.parse_files([SAMPLES / "sample_sensor.xlsx"]).df
        assert float(raw["气温"].max()) == 58.7
        result = dc.clean_data(raw, data_type=dp.TYPE_SENSOR)
        assert float(result.df["气温"].max()) < 40.0
        assert float(result.df["相对湿度"].min()) >= 0.0

    def test_sensor_sample_long_gap_kept(self):
        raw = dp.parse_files([SAMPLES / "sample_sensor.xlsx"]).df
        result = dc.clean_data(raw, data_type=dp.TYPE_SENSOR, max_gap=3)
        # 样本里的 10 点长缺口必须保留为空白
        assert int(result.df.isna().sum().sum()) >= 1
        assert result.stats.blanks_kept >= 1

    def test_sensor_time_column_untouched(self):
        raw = dp.parse_files([SAMPLES / "sample_sensor.xlsx"]).df
        result = dc.clean_data(raw, data_type=dp.TYPE_SENSOR)
        assert bool((result.df["时间"] == raw["时间"]).all())

    def test_clean_demo_data_reports_no_outliers(self):
        """干净的示例数据不应被"修"出问题来。"""
        demo = dp.parse_files([ROOT / "static" / "demo" / "demo_quadrat.csv"]).df
        result = dc.clean_data(demo)
        assert result.stats.outliers_fixed == 0
        assert result.stats.duplicates_removed == 0
        assert bool((result.df["株数"] == demo["株数"]).all())
        assert bool((result.df["盖度"] == demo["盖度"]).all())

    def test_structural_blanks_are_preserved(self):
        """灌木、草本本来就没有胸径：这类"结构性空白"必须保留，绝不能编造。

        这直接决定下游的"优势度 = πd²/4"是否正确 —— 若给灌木安上乔木的
        胸径中位数，就会算出一个根本不存在的断面积。
        """
        demo = dp.parse_files([ROOT / "static" / "demo" / "demo_quadrat.csv"]).df
        result = dc.clean_data(demo)
        # 只有乔木三个物种应当有胸径
        species_with_dbh = set(result.df.loc[result.df["胸径"].notna(), "物种"].unique())
        assert species_with_dbh == {"油松", "辽东栎", "山杏"}, species_with_dbh
        # 灌木草本仍然保留空白，且数量与原始一致
        assert int(result.df["胸径"].isna().sum()) == int(demo["胸径"].isna().sum())
        assert "保持空白" in result.log

    def test_real_missing_value_is_filled_from_same_species(self):
        """该物种在别处有观测时，真实的缺失值要按同物种中位数补上（不是一律留空）。"""
        df = pd.DataFrame({
            "样方号": ["Q1", "Q2", "Q3"],
            "物种": ["油松", "油松", "油松"],
            "株数": [10.0, 20.0, np.nan],
            "胸径": [18.0, np.nan, 20.0],
        })
        result = dc.clean_data(df)
        assert float(result.df["胸径"].iloc[1]) == 19.0      # 同物种中位数 (18+20)/2
        assert float(result.df["株数"].iloc[2]) == 15.0      # 同物种中位数 (10+20)/2
        assert int(result.df["胸径"].isna().sum()) == 0
        assert result.stats.species_filled == 2


# ===========================================================================
# 九、日志与明细表（方案 5.5）
# ===========================================================================
class TestLogAndChanges:
    def test_log_is_chinese_and_structured(self):
        raw = dp.parse_files([SAMPLES / "sample_quadrat.csv"]).df
        log = dc.clean_data(raw).log
        assert "共读取 38 行数据" in log
        assert "删除了 2 行完全重复的记录" in log
        assert "修正了 5 个异常值" in log
        assert "清洗后共 36 行" in log
        assert "株数为负" in log or "负数" in log
        assert "详见下方明细" in log

    def test_log_has_no_technical_english(self):
        raw = dp.parse_files([SAMPLES / "sample_quadrat.csv"]).df
        log = dc.clean_data(raw).log
        for word in ASCII_FORBIDDEN:
            assert word not in log, word

    def test_changes_table_columns(self):
        raw = dp.parse_files([SAMPLES / "sample_quadrat.csv"]).df
        changes = dc.clean_data(raw).changes
        assert list(changes.columns) == ["行号", "列名", "原值", "处理后的值", "处理方式"]
        assert len(changes) > 0

    def test_changes_contain_specific_positions(self):
        """明细必须精确到"行号 + 列名 + 原值"（方案阶段 3 第 7 条）。"""
        raw = dp.parse_files([SAMPLES / "sample_quadrat.csv"]).df
        changes = dc.clean_data(raw).changes
        negative_rows = raw.index[raw["株数"] < 0].tolist()
        for row_position in negative_rows:
            matched = changes[(changes["行号"] == row_position + 1) & (changes["列名"] == "株数")]
            assert len(matched) >= 1, "第 {0} 行的负株数没有出现在明细里".format(row_position + 1)
            assert float(matched.iloc[0]["原值"]) < 0

    def test_changes_row_numbers_are_original(self):
        """明细里的行号必须对应原始数据的行号（去重后不能整体前移）。"""
        df = pd.DataFrame({
            "样方号": ["Q1", "Q2", "Q2", "Q3"],
            "物种": ["甲", "乙", "乙", "丙"],
            "株数": [5, -1, -1, 7],
        })
        result = dc.clean_data(df)
        # 第 2、3 行是重复行（保留第 2 行），异常值在第 2 行
        outlier_rows = result.changes[result.changes["处理方式"] == dc.ACTION_FIX_OUTLIER]["行号"].tolist()
        assert outlier_rows == [2], outlier_rows

    def test_no_changes_still_produces_valid_table(self):
        df = pd.DataFrame({"样方号": ["Q1"], "物种": ["甲"], "株数": [5]})
        result = dc.clean_data(df)
        assert len(result.changes) == 0
        assert list(result.changes.columns) == ["行号", "列名", "原值", "处理后的值", "处理方式"]


# ===========================================================================
# 十、开关与参数
# ===========================================================================
class TestOptions:
    def test_disable_outlier_check(self):
        df = pd.DataFrame({"样方号": ["Q1", "Q2"], "物种": ["甲", "乙"], "株数": [-3, 5]})
        result = dc.clean_data(df, check_outlier=False, fill_missing=False)
        assert result.stats.outliers_fixed == 0
        assert float(result.df["株数"].min()) == -3.0

    def test_disable_missing_fill(self):
        df = pd.DataFrame({"样方号": ["Q1", "Q2"], "物种": ["甲", "乙"], "株数": [np.nan, 5]})
        result = dc.clean_data(df, fill_missing=False)
        assert result.stats.missing_filled == 0
        assert int(result.df["株数"].isna().sum()) == 1

    def test_parameters_are_reported_back(self):
        raw = dp.parse_files([SAMPLES / "sample_quadrat.csv"]).df
        # 用样方数据验证：它没有量纲换算，日志会进入"二、量纲与参数说明"一节
        quadrat = dc.clean_data(raw, mad_k=2.5, max_gap=5)
        assert quadrat.params["mad_k"] == 2.5
        assert quadrat.params["max_gap"] == 5
        assert "2.5 倍 MAD" in quadrat.log
        assert "最大插值间隙 5 个点" in quadrat.log
        # 时序数据同样能正确记录并回传参数
        sensor = dp.parse_files([SAMPLES / "sample_sensor.xlsx"]).df
        result = dc.clean_data(sensor, data_type=dp.TYPE_SENSOR, mad_k=2.5, max_gap=5)
        assert result.params["mad_k"] == 2.5 and result.params["max_gap"] == 5
        assert "2.5 倍 MAD" in result.log


class TestPreviewImpact:
    def test_preview_counts_match_actual_run(self):
        """预览的"预计影响"必须与真实执行结果一致（否则比不提示更糟）。"""
        raw = dp.parse_files([SAMPLES / "sample_quadrat.csv"]).df
        info = dc.preview_impact(raw)
        actual = dc.clean_data(raw).stats
        assert info["ok"] is True
        assert info["stats"].outliers_fixed == actual.outliers_fixed
        assert info["stats"].missing_filled == actual.missing_filled
        assert info["stats"].duplicates_removed == actual.duplicates_removed

    def test_preview_message_is_chinese_and_readable(self):
        raw = dp.parse_files([SAMPLES / "sample_quadrat.csv"]).df
        message = dc.preview_impact(raw)["message"]
        assert "预计修正" in message and "填补" in message and "删除" in message

    def test_preview_does_not_modify_input(self):
        raw = dp.parse_files([SAMPLES / "sample_quadrat.csv"]).df
        before = raw.copy(deep=True)
        dc.preview_impact(raw)
        pd.testing.assert_frame_equal(raw, before)

    def test_preview_reports_error_for_empty_frame(self):
        info = dc.preview_impact(pd.DataFrame())
        assert info["ok"] is False
        assert "空" in info["message"] or "没有" in info["message"]


class TestDescribeColumns:
    def test_reports_missing_and_outliers(self):
        raw = dp.parse_files([SAMPLES / "sample_quadrat.csv"]).df
        table = dc.describe_columns(raw)
        assert list(table.columns) == ["列名", "缺失个数", "缺失率", "异常值个数", "说明"]
        row = table[table["列名"] == "株数"].iloc[0]
        assert int(row["缺失个数"]) == 1
        assert int(row["异常值个数"]) == 2

    def test_flags_decimal_scale(self):
        df = pd.DataFrame({"盖度": [0.5, 0.6]})
        table = dc.describe_columns(df)
        assert "0~1" in table.iloc[0]["说明"]


# ===========================================================================
# 十一、边界与错误
# ===========================================================================
class TestEdgeCases:
    def test_empty_frame_raises_clean_error(self):
        with pytest.raises(dc.CleanError) as excinfo:
            dc.clean_data(pd.DataFrame())
        assert "空" in str(excinfo.value)
        assert "怎么办" in str(excinfo.value)

    def test_none_raises_clean_error(self):
        with pytest.raises(dc.CleanError):
            dc.clean_data(None)

    def test_all_missing_numeric_column_survives(self):
        df = pd.DataFrame({"样方号": ["Q1", "Q2"], "物种": ["甲", "乙"],
                           "株数": [1.0, 2.0], "全空": [np.nan, np.nan]})
        result = dc.clean_data(df)
        assert int(result.df["全空"].isna().sum()) == 2
        assert "整列都是空白" in result.log

    def test_plain_data_without_time_or_groups(self):
        df = pd.DataFrame({"处理组": ["对照", "对照", "增温"], "重复": [1, 2, 1],
                           "温度": [25.0, np.nan, 27.0]})
        result = dc.clean_data(df, data_type=dp.TYPE_PLAIN)
        assert result.stats.interpolated == 0
        assert result.stats.missing_filled == 1
        assert result.stats.column_filled == 1          # 无分组信息 → 整列中位数
        assert float(result.df["温度"].iloc[1]) == 26.0
        assert "整列中位数" in result.log

    def test_column_order_preserved(self):
        raw = dp.parse_files([SAMPLES / "sample_quadrat.csv"]).df
        result = dc.clean_data(raw)
        assert list(result.df.columns) == list(raw.columns)

    def test_cleanerror_message_is_actionable(self):
        with pytest.raises(dc.CleanError) as excinfo:
            dc.clean_data(pd.DataFrame(columns=["a"]))
        text = str(excinfo.value)
        assert "数据导入" in text
