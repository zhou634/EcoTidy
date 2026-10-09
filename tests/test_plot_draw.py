# -*- coding: utf-8 -*-
"""
core/plot_draw.py 单元测试（阶段 5 交付）

绘图模块的测试重点（无显示器也能跑，全部走 Agg 后端渲染）：
    1. 中文字体解析：优先随包字体，且 resolved 结果可用
    2. 中文标题与轴标签自动生成，绝不出现英文列名
    3. 四类图表都能画出来，且不修改输入数据
    4. 导出：300dpi PNG + SVG、文件名带时间戳且不覆盖同名文件
    5. 数据不适用时给出可操作的中文错误（而不是抛 IndexError 之类）
    6. 图表推荐：进入页面即可直接出图所依赖的推荐逻辑
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# matplotlib 需要可写的配置目录来缓存字体；沙箱/受限环境下指向工程内目录
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "temp" / "mplconfig_test"))

from core import data_clean as dc  # noqa: E402
from core import data_parse as dp  # noqa: E402
from core import eco_index as ei  # noqa: E402
from core import plot_draw as pdraw  # noqa: E402

SAMPLES = ROOT / "tests" / "samples"


# ===========================================================================
# 一、字体（避坑 10）
# ===========================================================================
class TestFont:
    def test_bundled_font_exists(self):
        """static/fonts 下必须有随包中文字体，否则打包后必然出现方框。"""
        font_file = pdraw._bundled_font_file()
        assert font_file is not None, "static/fonts 下没有找到字体文件（*.otf/*.ttf/*.ttc）"
        assert font_file.stat().st_size > 10000

    def test_resolve_font_prefers_bundled(self):
        pdraw.reset_font_cache()
        font, source = pdraw.resolve_font()
        assert font is not None
        assert source.startswith("随包字体"), source

    def test_unicode_minus_disabled(self):
        """使用中文字体后减号容易变方框，必须关闭 unicode 负号。"""
        import matplotlib

        pdraw.reset_font_cache()
        pdraw.resolve_font()
        assert matplotlib.rcParams["axes.unicode_minus"] is False

    def test_font_cache_avoids_repeat_lookup(self):
        pdraw.reset_font_cache()
        first = pdraw.resolve_font()
        second = pdraw.resolve_font()
        assert first == second


# ===========================================================================
# 二、中文标签与标题
# ===========================================================================
class TestChineseLabels:
    def test_column_label_known_columns(self):
        assert pdraw.column_label("H 香农指数") == "香农指数 H"
        assert pdraw.column_label("气温") == "气温（℃）"
        assert pdraw.column_label("密度") == "密度（株/m²）"

    def test_column_label_keeps_chinese(self):
        assert pdraw.column_label("我的自定义列") == "我的自定义列"

    @pytest.mark.parametrize("chart_type,expected", [
        (pdraw.CHART_DIVERSITY_BAR, "不同样方号的多样性指标对比"),
        (pdraw.CHART_SENSOR_LINE, "气温（℃）随时间变化"),
        (pdraw.CHART_QUADRAT_BAR, "不同物种的株数（株）对比"),
    ])
    def test_generated_titles_are_chinese(self, chart_type, expected):
        values = ["H 香农指数", "D 辛普森指数"] if chart_type == pdraw.CHART_DIVERSITY_BAR else ["气温"]
        if chart_type == pdraw.CHART_QUADRAT_BAR:
            values = ["株数"]
        title = pdraw.describe_chart(chart_type, "样方号" if "多样性" in chart_type else "物种",
                                     values)
        if chart_type == pdraw.CHART_SENSOR_LINE:
            assert title == expected, title
        else:
            assert "不同" in title and "对比" in title
        assert pdraw._has_chinese(title)

    def test_titles_never_contain_english_field_names(self):
        """标题里不允许出现英文列名（方案阶段 5 第 7 条）。"""
        for chart_type in pdraw.ALL_CHARTS:
            title = pdraw.describe_chart(chart_type, "样方号", ["H 香农指数"])
            assert "sample" not in title.lower()
            assert pdraw._has_chinese(title)


# ===========================================================================
# 三、四类图表
# ===========================================================================
def _quadrat() -> pd.DataFrame:
    raw = dp.parse_files([SAMPLES / "sample_quadrat.csv"]).df
    return dc.clean_data(raw).df


def _indices():
    return ei.compute_indicators(_quadrat(), data_type=dp.TYPE_QUADRAT)


def _sensor() -> pd.DataFrame:
    raw = dp.parse_files([SAMPLES / "sample_sensor.xlsx"]).df
    return dc.clean_data(raw, data_type=dp.TYPE_SENSOR).df


class TestCharts:
    def test_diversity_bar(self):
        result = _indices()
        spec = pdraw.ChartSpec(table_key="index_summary", chart_type=pdraw.CHART_DIVERSITY_BAR,
                               category="样方号", values=["H 香农指数", "D 辛普森指数"])
        chart = pdraw.draw_chart(result.summary_table, spec)
        assert chart.title.startswith("不同")
        assert chart.x_label == "样方号"
        assert chart.figure is not None
        pdraw.close_figure(chart)

    def test_sensor_line(self):
        spec = pdraw.ChartSpec(table_key="clean", chart_type=pdraw.CHART_SENSOR_LINE,
                               category="时间", values=["气温", "相对湿度"])
        chart = pdraw.draw_chart(_sensor(), spec)
        assert chart.x_label == "时间"
        # 两条曲线单位不同（℃ 与 %），共用一根 Y 轴时轴标题写"数值"最诚实，
        # 具体哪条线是什么由图例说明（写成一长串会与刻度数字挤在一起）
        assert chart.y_label == "数值", chart.y_label
        assert chart.title.endswith("随时间变化")
        pdraw.close_figure(chart)

    def test_sensor_line_single_series_keeps_unit(self):
        spec = pdraw.ChartSpec(table_key="clean", chart_type=pdraw.CHART_SENSOR_LINE,
                               category="时间", values=["气温"])
        chart = pdraw.draw_chart(_sensor(), spec)
        assert chart.y_label == "气温（℃）", chart.y_label
        pdraw.close_figure(chart)

    def test_shared_axis_label_rules(self):
        """共用 Y 轴的轴标题规则（避免长串文字与刻度数字叠字）。"""
        assert pdraw._shared_axis_label(["气温"]) == "气温（℃）"
        assert pdraw._shared_axis_label(["气温", "土壤温度"]) == "气温（℃）"
        assert pdraw._shared_axis_label(["气温", "相对湿度"]) == "数值"
        assert pdraw._shared_axis_label(["H 香农指数", "D 辛普森指数"]) == "数值"

    def test_axis_labels_do_not_overlap_ticks(self):
        """回归防线：轴标题与刻度数字、刻度之间都不能互相压字。

        实测踩坑：多条不同单位的曲线把轴标题写成一长串，
        窄画布下"气温（℃）、相对湿度（%）"会和刻度数字 40 叠在一起。
        """
        from matplotlib.backends.backend_agg import FigureCanvasAgg

        spec = pdraw.ChartSpec(table_key="clean", chart_type=pdraw.CHART_SENSOR_LINE,
                               category="时间", values=["气温", "相对湿度", "土壤温度"])
        chart = pdraw.draw_chart(_sensor(), spec)
        figure = chart.figure
        figure.set_size_inches(10, 6)
        canvas = FigureCanvasAgg(figure)
        canvas.draw()
        renderer = canvas.get_renderer()
        axes = figure.axes[0]

        ylabel_box = axes.yaxis.label.get_window_extent(renderer=renderer)
        axes_box = axes.get_window_extent(renderer=renderer)
        checked = 0
        for tick in axes.yaxis.get_major_ticks():
            label = tick.label1
            text = label.get_text()
            if not text or not label.get_visible():
                continue
            box = label.get_window_extent(renderer=renderer)
            # 只校验真正贴在左侧刻度区的那一列数字：
            # 远离坐标区竖直范围的多半是 X 轴刻度，不属于本次校验对象。
            if box.y1 < axes_box.y0 or box.y0 > axes_box.y1:
                continue
            checked += 1
            # 轴标题在左、刻度数字在右，两者不重叠的充要条件就是
            # 「刻度的左边缘」不越过「标题的右边缘」。
            # （一开始写成 box.x1 <= ylabel_box.x0 是反的：那要求刻度整体落在标题左边，
            #   在正常布局下永远不成立，必然误报。）
            assert box.x0 >= ylabel_box.x1, (
                "Y 轴标题与刻度 «{0}» 重叠：标题右边缘 {1:.1f}，刻度左边缘 {2:.1f}".format(
                    text, ylabel_box.x1, box.x0))
        assert checked >= 3, "没有校验到任何 Y 轴刻度（测试本身失效），实际 {0}".format(checked)

        boxes = sorted((label.get_window_extent(renderer=renderer)
                        for label in axes.xaxis.get_ticklabels() if label.get_text()),
                       key=lambda item: item.x0)
        for left, right in zip(boxes, boxes[1:]):
            assert right.x0 >= left.x1, "X 轴刻度互相重叠"
        pdraw.close_figure(chart)

    def test_community_box(self):
        spec = pdraw.ChartSpec(table_key="index_row", chart_type=pdraw.CHART_COMMUNITY_BOX,
                               category="物种", values=["株数", "盖度"], group_by="物种")
        chart = pdraw.draw_chart(_quadrat(), spec)
        assert "分布" in chart.title
        assert chart.x_label == "物种"
        pdraw.close_figure(chart)

    def test_quadrat_bar(self):
        spec = pdraw.ChartSpec(table_key="index_row", chart_type=pdraw.CHART_QUADRAT_BAR,
                               category="物种", values=["重要值"])
        chart = pdraw.draw_chart(_indices().row_table, spec)
        assert "对比" in chart.title
        assert pdraw._has_chinese(chart.title)
        pdraw.close_figure(chart)

    def test_input_not_modified(self):
        df = _quadrat()
        before = df.copy(deep=True)
        spec = pdraw.ChartSpec(table_key="clean", chart_type=pdraw.CHART_QUADRAT_BAR,
                               category="物种", values=["株数"])
        chart = pdraw.draw_chart(df, spec)
        pd.testing.assert_frame_equal(df, before)
        pdraw.close_figure(chart)

    def test_long_series_is_aggregated_by_day(self):
        """点数过多时按日均值聚合，并在说明里写清楚。"""
        times = pd.date_range("2024-06-01 00:00:00", periods=24 * 30, freq="h")
        df = pd.DataFrame({"时间": times.strftime(dp.TIME_FORMAT),
                           "气温": np.linspace(10, 25, len(times))})
        spec = pdraw.ChartSpec(table_key="clean", chart_type=pdraw.CHART_SENSOR_LINE,
                               category="时间", values=["气温"], aggregate=True)
        chart = pdraw.draw_chart(df, spec)
        assert any("日均值" in note for note in chart.notes), chart.notes
        pdraw.close_figure(chart)

    def test_missing_value_creates_gap_not_fake_line(self):
        """缺测不应被连成直线：折线里的 NaN 必须原样保留为断点。"""
        df = pd.DataFrame({
            "时间": pd.date_range("2024-06-01", periods=5, freq="h").strftime(dp.TIME_FORMAT),
            "气温": [10.0, np.nan, 12.0, np.nan, 14.0],
        })
        spec = pdraw.ChartSpec(table_key="clean", chart_type=pdraw.CHART_SENSOR_LINE,
                               category="时间", values=["气温"])
        chart = pdraw.draw_chart(df, spec)
        line = chart.figure.axes[0].lines[0]
        assert int(np.isnan(np.asarray(line.get_ydata(), dtype="float64")).sum()) == 2
        pdraw.close_figure(chart)


# ===========================================================================
# 四、错误信息必须可操作
# ===========================================================================
class TestChartErrors:
    def test_line_chart_without_time_column(self):
        spec = pdraw.ChartSpec(table_key="clean", chart_type=pdraw.CHART_SENSOR_LINE,
                               category=None, values=["株数"])
        with pytest.raises(pdraw.PlotError) as excinfo:
            pdraw.draw_chart(_quadrat(), spec)
        assert "时间" in str(excinfo.value)
        assert "怎么办" in str(excinfo.value)

    def test_bar_chart_without_numeric_values(self):
        df = pd.DataFrame({"样方号": ["Q1"], "物种": ["甲"]})
        spec = pdraw.ChartSpec(table_key="clean", chart_type=pdraw.CHART_DIVERSITY_BAR,
                               category="样方号", values=[])
        with pytest.raises(pdraw.PlotError) as excinfo:
            pdraw.draw_chart(df, spec)
        assert "数值" in str(excinfo.value)

    def test_unknown_chart_type(self):
        spec = pdraw.ChartSpec(table_key="clean", chart_type="饼图", category="物种", values=["株数"])
        with pytest.raises(pdraw.PlotError):
            pdraw.draw_chart(_quadrat(), spec)

    def test_empty_dataframe(self):
        spec = pdraw.ChartSpec(table_key="clean", chart_type=pdraw.CHART_QUADRAT_BAR,
                               category="物种", values=["株数"])
        with pytest.raises(pdraw.PlotError):
            pdraw.draw_chart(pd.DataFrame(), spec)

    def test_all_errors_are_chinese(self):
        cases = [
            (_quadrat(), pdraw.ChartSpec("clean", pdraw.CHART_SENSOR_LINE, values=["株数"])),
            (pd.DataFrame(), pdraw.ChartSpec("clean", pdraw.CHART_QUADRAT_BAR, values=["株数"])),
        ]
        for df, spec in cases:
            with pytest.raises(pdraw.PlotError) as excinfo:
                pdraw.draw_chart(df, spec)
            message = str(excinfo.value)
            assert pdraw._has_chinese(message)
            for forbidden in ("Traceback", "KeyError", "ValueError"):
                assert forbidden not in message


# ===========================================================================
# 五、导出（300dpi、文件名、不覆盖）
# ===========================================================================
class TestExport:
    def _chart(self):
        spec = pdraw.ChartSpec(table_key="index_summary", chart_type=pdraw.CHART_DIVERSITY_BAR,
                               category="样方号", values=["H 香农指数"])
        return pdraw.draw_chart(_indices().summary_table, spec)

    def test_save_png_and_svg(self, tmp_path):
        chart = self._chart()
        written = pdraw.save_figure(chart, tmp_path, formats=("png", "svg"), png_dpi=300)
        assert len(written) == 2
        for path in written:
            assert path.exists() and path.stat().st_size > 1000
        assert written[0].suffix == ".png" and written[1].suffix == ".svg"
        pdraw.close_figure(chart)

    def test_png_is_300dpi(self, tmp_path):
        chart = self._chart()
        written = pdraw.save_figure(chart, tmp_path, formats=("png",), png_dpi=300)
        from PIL import Image

        with Image.open(written[0]) as image:
            dpi = image.info.get("dpi", (0, 0))
        assert dpi and abs(float(dpi[0]) - 300) < 1.5, dpi
        pdraw.close_figure(chart)

    def test_filename_contains_title_and_timestamp(self, tmp_path):
        chart = self._chart()
        written = pdraw.save_figure(chart, tmp_path, formats=("png",))
        name = written[0].name
        # 中文图表名 + 时间戳（YYYYMMDD_HHMMSS）
        assert "不同" in name
        assert len(name.split("_")) >= 3, name
        pdraw.close_figure(chart)

    def test_never_overwrites_existing_file(self, tmp_path):
        chart = self._chart()
        first = pdraw.save_figure(chart, tmp_path, formats=("png",), )
        second = pdraw.save_figure(chart, tmp_path, formats=("png",))
        # 同一秒内两次导出的时间戳相同，第二个文件名必须自动加序号
        assert first[0] != second[0]
        assert first[0].exists() and second[0].exists()
        pdraw.close_figure(chart)

    def test_safe_filename_strips_illegal_chars(self):
        assert pdraw.safe_filename('气温/湿度:对比*图?"<>|') == "气温湿度对比图"
        assert pdraw.safe_filename("") == "图表"
        assert pdraw.safe_filename("   ") == "图表"

    def test_safe_filename_truncates(self):
        assert len(pdraw.safe_filename("中" * 200)) <= 60

    def test_build_export_path_creates_directory(self, tmp_path):
        target = tmp_path / "不存在" / "更深"
        path = pdraw.build_export_path(target, "测试图", ".png")
        assert path.parent.exists()


# ===========================================================================
# 六、图表推荐与可用类型（决定"进页面即可出图"）
# ===========================================================================
class TestRecommendation:
    def test_sensor_recommends_line_chart(self):
        spec = pdraw.recommend_spec(_sensor(), "clean")
        assert spec.chart_type == pdraw.CHART_SENSOR_LINE
        assert spec.category == "时间"
        assert "气温" in spec.values

    def test_summary_recommends_diversity_bar(self):
        spec = pdraw.recommend_spec(_indices().summary_table, "index_summary")
        assert spec.chart_type == pdraw.CHART_DIVERSITY_BAR
        assert spec.category == "样方号"
        assert any("香农" in value for value in spec.values)

    def test_row_table_recommends_box(self):
        spec = pdraw.recommend_spec(_indices().row_table, "index_row")
        assert spec.chart_type in (pdraw.CHART_COMMUNITY_BOX, pdraw.CHART_QUADRAT_BAR)
        assert spec.values, "必须推荐出至少一个数值字段"

    def test_recommended_spec_actually_draws(self):
        """推荐的设置必须真的能画出图 —— 否则"进页面直接点绘制"就是空话。"""
        for df, key in ((_sensor(), "clean"),
                        (_indices().summary_table, "index_summary"),
                        (_indices().row_table, "index_row"),
                        (_quadrat(), "clean")):
            spec = pdraw.recommend_spec(df, key)
            chart = pdraw.draw_chart(df, spec)
            assert chart.title and pdraw._has_chinese(chart.title)
            pdraw.close_figure(chart)

    def test_chart_types_for_sensor(self):
        types = pdraw.chart_types_for(_sensor())
        assert pdraw.CHART_SENSOR_LINE in types

    def test_chart_types_exclude_line_for_quadrat(self):
        types = pdraw.chart_types_for(_quadrat())
        assert pdraw.CHART_SENSOR_LINE not in types

    def test_all_declared_charts_are_drawable(self):
        """四类图表都必须有实现，不能只声明不实现。"""
        for chart_type in pdraw.ALL_CHARTS:
            assert chart_type in pdraw._DRAWERS

    def test_palette_is_grayscale(self):
        """配色必须是灰度（界面为白色单色调，图表也要一致）。

        判定：每个色值的 RGB 三通道差异很小，且整体不偏色。
        """
        for value in pdraw.PALETTE:
            text = value.lstrip("#")
            r, g, b = (int(text[i:i + 2], 16) for i in (0, 2, 4))
            assert max(r, g, b) - min(r, g, b) <= 12, \
                "调色板 {0} 不是灰度色（三通道差异过大）".format(value)

    def test_palette_has_enough_distinct_levels(self):
        """灰度级别要拉得开，否则多序列图会糊成一片。"""
        levels = sorted(int(value.lstrip("#")[:2], 16) for value in pdraw.PALETTE)
        assert len(set(levels)) >= 6, levels
        assert levels[-1] - levels[0] >= 100, levels


# ===========================================================================
# 八、matplotlib 缓冲目录（启动期 PermissionError 的回归防线）
# ===========================================================================
class TestMplConfigDir:
    """回归测试：matplotlib 必须在**可写**目录里建字体缓存锁文件。

    背景（实测踩坑）：matplotlib 默认用 ~/.matplotlib 缓存字体，并在那里创建
    fontlist-*.json.matplotlib-lock。用户主目录不可写的机器上，
    `import matplotlib.pyplot` 直接抛 PermissionError，软件连启动都做不到。
    本项目因此统一把 MPLCONFIGDIR 指到可写目录 —— 以下用例锁定该行为。
    """

    def test_env_has_writable_mpl_configdir(self):
        """应用启动链跑完后，MPLCONFIGDIR 必须存在且可写。"""
        from utils import paths

        directory = paths.ensure_mpl_config_dir()
        assert str(directory) == os.environ["MPLCONFIGDIR"]
        assert paths._is_dir_writable(directory), directory

    def test_resolver_never_returns_unwritable_dir(self):
        """解析结果必须可写 —— 这正是原缺陷的根因。"""
        from utils import paths

        saved = os.environ.pop("MPLCONFIGDIR", None)
        try:
            directory = paths.resolve_mpl_config_dir()
        finally:
            if saved is not None:
                os.environ["MPLCONFIGDIR"] = saved
            else:
                os.environ.pop("MPLCONFIGDIR", None)
        assert paths._is_dir_writable(directory), directory

    def test_explicit_env_is_respected(self, tmp_path):
        """用户显式设置了 MPLCONFIGDIR 就尊重它，不要自作主张改掉。"""
        from utils import paths

        saved = os.environ.get("MPLCONFIGDIR")
        os.environ["MPLCONFIGDIR"] = str(tmp_path)
        try:
            assert paths.ensure_mpl_config_dir() == tmp_path
            assert paths.resolve_mpl_config_dir() == tmp_path
        finally:
            if saved is not None:
                os.environ["MPLCONFIGDIR"] = saved
            else:
                os.environ.pop("MPLCONFIGDIR", None)

    def test_ensure_is_idempotent(self):
        from utils import paths

        first = paths.ensure_mpl_config_dir()
        second = paths.ensure_mpl_config_dir()
        assert first == second

    def test_prefers_our_own_data_dir_when_writable(self, tmp_path, monkeypatch):
        """自己的数据目录可写时应当优先选它（而不是主目录）。"""
        from utils import paths

        target = tmp_path / "appdata" / "matplotlib"
        monkeypatch.setattr(paths, "MPL_CONFIG_DIR", target)
        monkeypatch.setattr(paths, "_is_dir_writable", lambda directory: directory == target)
        saved = os.environ.pop("MPLCONFIGDIR", None)
        try:
            assert paths.resolve_mpl_config_dir() == target
        finally:
            if saved is not None:
                os.environ["MPLCONFIGDIR"] = saved
            else:
                os.environ.pop("MPLCONFIGDIR", None)

    def test_falls_back_to_temp_when_nothing_else_writable(self, monkeypatch):
        """主目录与数据目录都不可写时必须落到临时目录，而不是继续用不可写目录。"""
        import tempfile
        from pathlib import Path

        from utils import paths

        monkeypatch.delenv("MPLCONFIGDIR", raising=False)
        temp_root = Path(tempfile.gettempdir()).resolve()

        def fake_writable(directory):
            # 只有临时目录可写，模拟"用户主目录与数据目录都被限制"的机器
            try:
                Path(directory).resolve().relative_to(temp_root)
                return True
            except ValueError:
                return False

        monkeypatch.setattr(paths, "_is_dir_writable", fake_writable)
        directory = paths.resolve_mpl_config_dir()
        assert directory.exists()
        assert str(directory).startswith(str(temp_root)), directory

    def test_readable_cache_alone_is_not_considered_usable(self):
        """明确锁定：只有可读缓存不算"能用"。

        这是原缺陷的关键教训 —— 本机主目录里就有 fontlist 缓存看似可用，
        但 matplotlib 仍要写锁文件，于是启动直接失败。
        """
        from pathlib import Path

        from utils import paths

        home_cache = Path.home() / ".matplotlib"
        # 不论该目录是否有缓存，解析结果都不能因为"有缓存"就选它（除非它可写）
        has_cache = paths._has_readable_font_cache(home_cache)
        if has_cache and not paths._is_dir_writable(home_cache):
            saved = os.environ.pop("MPLCONFIGDIR", None)
            try:
                chosen = paths.resolve_mpl_config_dir()
            finally:
                if saved is not None:
                    os.environ["MPLCONFIGDIR"] = saved
                else:
                    os.environ.pop("MPLCONFIGDIR", None)
            assert chosen != home_cache, "不可写的主目录缓存不应被选用"


# ===========================================================================
# 九、分类轴的自然排序（Q1, Q2 … Q10 而不是 Q1, Q10, Q2）
# ===========================================================================
class TestNaturalOrder:
    def test_natural_sort_key_orders_numbers(self):
        values = ["Q1", "Q10", "Q2", "Q11", "Q3"]
        assert pdraw.natural_sorted(values) == ["Q1", "Q2", "Q3", "Q10", "Q11"]

    def test_natural_sort_handles_plain_numbers(self):
        assert pdraw.natural_sorted(["10", "9", "100"]) == ["9", "10", "100"]

    def test_natural_sort_handles_chinese_labels(self):
        values = ["处理10", "处理2", "处理1"]
        assert pdraw.natural_sorted(values) == ["处理1", "处理2", "处理10"]

    def test_natural_sort_keeps_all_values(self):
        values = ["A", "B", "A"]
        assert sorted(pdraw.natural_sorted(values)) == ["A", "A", "B"]

    def test_bar_chart_x_axis_is_naturally_ordered(self):
        """柱状图的 X 轴必须按 Q1…Q12 的自然顺序排列。"""
        df = pd.DataFrame({
            "样方号": ["Q1", "Q10", "Q2", "Q11", "Q3"],
            "H 香农指数": [1.0, 1.1, 1.2, 1.3, 1.4],
        })
        spec = pdraw.ChartSpec(table_key="index_summary", chart_type=pdraw.CHART_DIVERSITY_BAR,
                               category="样方号", values=["H 香农指数"])
        chart = pdraw.draw_chart(df, spec)
        labels = [text.get_text() for text in chart.figure.axes[0].get_xticklabels()]
        assert labels == ["Q1", "Q2", "Q3", "Q10", "Q11"], labels
        pdraw.close_figure(chart)

    def test_box_chart_categories_are_naturally_ordered(self):
        df = pd.DataFrame({
            "物种": ["物种1", "物种10", "物种2"] * 5,
            "株数": list(range(15)),
        })
        spec = pdraw.ChartSpec(table_key="index_row", chart_type=pdraw.CHART_COMMUNITY_BOX,
                               category="物种", values=["株数"], group_by="物种")
        chart = pdraw.draw_chart(df, spec)
        labels = [text.get_text() for text in chart.figure.axes[0].get_xticklabels()]
        assert labels == ["物种1", "物种2", "物种10"], labels
        pdraw.close_figure(chart)

    def test_multi_row_categories_are_averaged(self):
        """同一样方有多行（行级表）时，柱子取该样方的均值，不会画出重复柱子。"""
        df = pd.DataFrame({
            "样方号": ["Q1", "Q1", "Q2"],
            "密度": [10.0, 20.0, 30.0],
        })
        spec = pdraw.ChartSpec(table_key="index_row", chart_type=pdraw.CHART_DIVERSITY_BAR,
                               category="样方号", values=["密度"])
        chart = pdraw.draw_chart(df, spec)
        axes = chart.figure.axes[0]
        assert len(axes.get_xticklabels()) == 2, [t.get_text() for t in axes.get_xticklabels()]
        heights = [patch.get_height() for patch in axes.patches]
        assert 15.0 in heights and 30.0 in heights, heights
        pdraw.close_figure(chart)
