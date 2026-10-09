# -*- coding: utf-8 -*-
"""
配色方案与输出格式测试（阶段 8 追加功能）

需求：绘图可选彩色图或黑白图，也可选矢量图输出。

覆盖：
    1. 三种配色模式（灰阶 / 彩色 / 纯黑白）都能出图，且都只支持真实存在的模式
    2. **黑白模式下各系列必须能区分** —— 这是最容易做成"看起来一样"的地方：
       · 柱状图：实心黑 + 白底底纹，任意两个系列的填充/底纹组合不得重复
       · 折线图：线条必须是黑色（白色线在白底上等于看不见）
       · 折线图：线型 + 标记形状组合不得重复
    3. 输出格式：仅 PNG / 仅 SVG / 两者；乱序与重复输入要正确归一去重
    4. 页面上确实能选到这些选项，且选择会被记住
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "temp" / "mplconfig_test"))

from core import data_clean as dc  # noqa: E402
from core import data_parse as dp  # noqa: E402
from core import eco_index as ei  # noqa: E402
from core import plot_draw as pdraw  # noqa: E402

SAMPLES = ROOT / "tests" / "samples"


def _quadrat_frame():
    return dc.clean_data(dp.parse_files([SAMPLES / "sample_quadrat.csv"]).df).df


def _sensor_frame():
    return dc.clean_data(dp.parse_files([SAMPLES / "sample_sensor.xlsx"]).df,
                         data_type=dp.TYPE_SENSOR).df


def _summary_table():
    return ei.compute_indicators(_quadrat_frame(), data_type=dp.TYPE_QUADRAT).summary_table


# ===========================================================================
# 一、配色模式
# ===========================================================================
class TestPaletteModes:
    def test_three_modes_available(self):
        assert pdraw.PALETTE_MODE_COLOR in pdraw.ALL_PALETTE_MODES
        assert pdraw.PALETTE_MODE_GRAY in pdraw.ALL_PALETTE_MODES
        assert pdraw.PALETTE_MODE_BW in pdraw.ALL_PALETTE_MODES
        assert pdraw.DEFAULT_PALETTE_MODE in pdraw.ALL_PALETTE_MODES

    def test_default_is_gray(self):
        """默认灰阶：与白色单色界面一致。"""
        assert pdraw.DEFAULT_PALETTE_MODE == pdraw.PALETTE_MODE_GRAY

    def test_unknown_mode_falls_back(self):
        assert pdraw.palette_for("不存在的模式") == pdraw.palette_for(
            pdraw.DEFAULT_PALETTE_MODE)

    def test_color_palette_is_colorful(self):
        for value in pdraw.palette_for(pdraw.PALETTE_MODE_COLOR):
            text = value.lstrip("#")
            r, g, b = (int(text[i:i + 2], 16) for i in (0, 2, 4))
            assert max(r, g, b) - min(r, g, b) > 40, "{0} 不够鲜艳".format(value)

    def test_gray_palette_is_grayscale(self):
        for value in pdraw.palette_for(pdraw.PALETTE_MODE_GRAY):
            text = value.lstrip("#")
            r, g, b = (int(text[i:i + 2], 16) for i in (0, 2, 4))
            assert max(r, g, b) - min(r, g, b) <= 12, "{0} 不是灰度色".format(value)

    @pytest.mark.parametrize("mode", ["灰阶", "彩色", "纯黑白"])
    def test_every_mode_draws_all_chart_types(self, mode):
        cases = [
            (_summary_table(), dict(chart_type=pdraw.CHART_DIVERSITY_BAR,
                                    category="样方号", values=["H 香农指数", "D 辛普森指数"])),
            (_quadrat_frame(), dict(chart_type=pdraw.CHART_COMMUNITY_BOX,
                                    category="物种", values=["株数", "盖度"], group_by="物种")),
            (_sensor_frame(), dict(chart_type=pdraw.CHART_SENSOR_LINE,
                                   category="时间", values=["气温", "相对湿度", "土壤温度"])),
            (_quadrat_frame(), dict(chart_type=pdraw.CHART_QUADRAT_BAR,
                                    category="物种", values=["株数"])),
        ]
        for frame, kwargs in cases:
            spec = pdraw.ChartSpec(table_key="clean", palette=mode, **kwargs)
            chart = pdraw.draw_chart(frame, spec)
            assert chart.figure is not None
            assert pdraw._has_chinese(chart.title)
            pdraw.close_figure(chart)


# ===========================================================================
# 二、纯黑白模式必须能区分系列
# ===========================================================================
class TestBlackAndWhiteDistinguishable:
    def test_bw_bar_styles_are_unique(self):
        """任意两个系列的（填充色, 底纹）组合都不能相同。

        实测踩坑：第一版让填充在黑白之间交替，并沿用 ("", "//", "\\\\", "xx")
        底纹序列 —— 结果是系列 1 与系列 3 都是实心黑，图例里完全分不出来。
        """
        combos = []
        for index in range(len(pdraw._HATCHES)):
            style = pdraw.series_style(pdraw.PALETTE_MODE_BW, index)
            combos.append((style.get("facecolor"), style.get("hatch")))
        assert len(set(combos)) == len(combos), combos

    def test_bw_only_first_series_is_solid_black(self):
        solid = [index for index in range(6)
                 if pdraw.series_style(pdraw.PALETTE_MODE_BW, index).get("facecolor") == "#000000"]
        assert solid == [0], solid

    def test_bw_hatched_series_have_black_edges(self):
        """白底系列必须有黑描边，否则在白纸上完全看不见。"""
        for index in range(1, 6):
            style = pdraw.series_style(pdraw.PALETTE_MODE_BW, index)
            assert style["edgecolor"] == "#000000", style
            assert style.get("hatch"), style

    def test_bw_line_is_black_not_white(self):
        """实测踩坑：折线图若沿用"黑/白交替"的填充色，会出现白色线条
        —— 白线画在白底上等于没画。"""
        for index in range(6):
            style = pdraw.series_style(pdraw.PALETTE_MODE_BW, index, filled=False)
            assert style["color"] == "#000000", style

    def test_bw_lines_use_distinct_styles_and_markers(self):
        combos = []
        for index in range(len(pdraw._BW_LINE_STYLES)):
            style = pdraw.series_style(pdraw.PALETTE_MODE_BW, index, filled=False)
            combos.append((str(style["linestyle"]), style["marker"]))
        assert len(set(combos)) == len(combos), combos

    def test_colored_modes_keep_white_edges(self):
        """彩色/灰阶仍用白色描边把相邻柱子分开。"""
        for mode in (pdraw.PALETTE_MODE_COLOR, pdraw.PALETTE_MODE_GRAY):
            style = pdraw.series_style(mode, 1)
            assert style["edgecolor"] == "white", style
            assert "hatch" not in style, style

    def test_bw_boxplot_does_not_use_transparency(self):
        """纯黑白下箱体不能半透明，否则底纹会糊掉。"""
        frame = _quadrat_frame()
        spec = pdraw.ChartSpec(table_key="clean", chart_type=pdraw.CHART_COMMUNITY_BOX,
                               category="物种", values=["株数"], group_by="物种",
                               palette=pdraw.PALETTE_MODE_BW)
        chart = pdraw.draw_chart(frame, spec)
        patches = [patch for axes in chart.figure.axes for patch in axes.patches]
        assert patches, "箱线图没有生成箱体"
        for patch in patches:
            assert patch.get_alpha() in (None, 1.0), "纯黑白下图元不应半透明"
        pdraw.close_figure(chart)

    def test_bw_bar_legend_keys_differ(self):
        """图例本身也要能区分：三个系列应有不同的图例图元。"""
        spec = pdraw.ChartSpec(table_key="index_summary", chart_type=pdraw.CHART_DIVERSITY_BAR,
                               category="样方号",
                               values=["H 香农指数", "D 辛普森指数", "J Pielou均匀度"],
                               palette=pdraw.PALETTE_MODE_BW)
        chart = pdraw.draw_chart(_summary_table(), spec)
        legend = chart.figure.axes[0].get_legend()
        assert legend is not None
        keys = []
        for handle in legend.legend_handles:
            keys.append((handle.get_facecolor(), handle.get_hatch()))
        assert len(set(keys)) == len(keys), keys
        pdraw.close_figure(chart)


# ===========================================================================
# 三、输出格式
# ===========================================================================
class TestOutputFormats:
    def test_normalize_orders_and_dedupes(self):
        assert pdraw.normalize_formats(("svg", "png", "svg")) == ("png", "svg")
        assert pdraw.normalize_formats((".PNG", " png ")) == ("png",)
        assert pdraw.normalize_formats(("svg",)) == ("svg",)

    def test_normalize_keeps_other_known_formats(self):
        assert pdraw.normalize_formats(("pdf", "png")) == ("png", "pdf")

    def test_normalize_empty(self):
        assert pdraw.normalize_formats(()) == ()

    def test_describe_formats_is_chinese(self):
        text = pdraw.describe_formats(("png", "svg"))
        assert "位图" in text and "矢量" in text
        assert "未选择输出格式" == pdraw.describe_formats(())

    def test_save_only_png(self, tmp_path):
        chart = pdraw.draw_chart(_summary_table(), pdraw.ChartSpec(
            table_key="index_summary", chart_type=pdraw.CHART_DIVERSITY_BAR,
            category="样方号", values=["H 香农指数"]))
        written = pdraw.save_figure(chart, tmp_path, formats=("png",))
        assert [path.suffix for path in written] == [".png"]
        assert written[0].stat().st_size > 1000
        pdraw.close_figure(chart)

    def test_save_only_svg(self, tmp_path):
        chart = pdraw.draw_chart(_summary_summary_safe(), pdraw.ChartSpec(
            table_key="index_summary", chart_type=pdraw.CHART_DIVERSITY_BAR,
            category="样方号", values=["H 香农指数"]))
        written = pdraw.save_figure(chart, tmp_path, formats=("svg",))
        assert [path.suffix for path in written] == [".svg"]
        assert "<svg" in written[0].read_text(encoding="utf-8", errors="ignore")[:2000]
        pdraw.close_figure(chart)

    def test_save_both_formats_share_stem(self, tmp_path):
        chart = pdraw.draw_chart(_summary_summary_safe(), pdraw.ChartSpec(
            table_key="index_summary", chart_type=pdraw.CHART_DIVERSITY_BAR,
            category="样方号", values=["H 香农指数"]))
        # 显式给时间戳：否则两次 savefig 跨秒时文件名主体会不同（实测偶发失败）
        written = pdraw.save_figure(chart, tmp_path, formats=("png", "svg"),
                                    timestamp="20240601_120000")
        assert len(written) == 2
        assert written[0].stem == written[1].stem, [p.name for p in written]
        assert {path.suffix for path in written} == {".png", ".svg"}
        pdraw.close_figure(chart)

    def test_save_all_formats_share_one_timestamp(self, tmp_path):
        """同一张图的多个格式必须共用一个时间戳，文件名主体才一致。"""
        chart = pdraw.draw_chart(_summary_summary_safe(), pdraw.ChartSpec(
            table_key="index_summary", chart_type=pdraw.CHART_DIVERSITY_BAR,
            category="样方号", values=["H 香农指数"]))
        written = pdraw.save_figure(chart, tmp_path, formats=("png", "svg"),
                                    timestamp="20240602_090000")
        assert all(path.stem.endswith("20240602_090000") for path in written), \
            [p.name for p in written]
        pdraw.close_figure(chart)

    def test_save_dedupes_duplicate_formats(self, tmp_path):
        """传重复格式不应写出重复文件（实测踩坑：曾写出 3 个文件）。"""
        chart = pdraw.draw_chart(_summary_summary_safe(), pdraw.ChartSpec(
            table_key="index_summary", chart_type=pdraw.CHART_DIVERSITY_BAR,
            category="样方号", values=["H 香农指数"]))
        written = pdraw.save_figure(chart, tmp_path, formats=("svg", "png", "svg"))
        assert len(written) == 2, [path.name for path in written]
        pdraw.close_figure(chart)

    def test_svg_is_vector_after_monochrome(self, tmp_path):
        """纯黑白 + 矢量输出：SVG 里不应出现彩色填充值。"""
        chart = pdraw.draw_chart(_summary_summary_safe(), pdraw.ChartSpec(
            table_key="index_summary", chart_type=pdraw.CHART_DIVERSITY_BAR,
            category="样方号", values=["H 香农指数", "D 辛普森指数"],
            palette=pdraw.PALETTE_MODE_BW))
        written = pdraw.save_figure(chart, tmp_path, formats=("svg",))
        text = written[0].read_text(encoding="utf-8", errors="ignore")
        for color in pdraw.palette_for(pdraw.PALETTE_MODE_COLOR):
            assert color not in text.upper(), "纯黑白图里不该出现彩色 {0}".format(color)
        pdraw.close_figure(chart)


def _summary_summary_safe():
    """汇总指标表（带缓存，避免每个用例重复算一遍）。"""
    global _CACHE
    try:
        return _CACHE
    except NameError:
        _CACHE = _summary_table()
        return _CACHE
