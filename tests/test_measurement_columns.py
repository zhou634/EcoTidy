# -*- coding: utf-8 -*-
"""
测量列（树高 / 基径 / 冠幅）识别与贯通测试

背景：用户的数据表头形如
    样方号 | 物种 | 株数 | 树高（m） | 胸径（cm） | 基径（cm） | 冠幅（cm） | 盖度（%）
其中「树高」「基径」「冠幅」是本次新增的测量列。

约定的行为（用户已确认）：
    · 三列是**可选**字段，缺了不影响识别成"样方群落"；
    · 只作为测量值保存，**不参与指标计算**（优势度仍只看胸径与盖度）；
    · 负值属物理不可能，必须被判为异常并修正；
    · 表头带不带单位、用括号还是方括号，都要能识别；
    · 标识列（样方号）不能因为"是整数"就被当成绘图数值轴。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "temp" / "mplconfig_test"))

from core import data_clean as dc  # noqa: E402
from core import data_parse as dp  # noqa: E402
from core import plot_draw as pdraw  # noqa: E402

#: 用户截图里的那套表头（带单位）
HEADERS_WITH_UNITS = ["样方号", "物种", "株数", "树高（m）", "胸径（cm）",
                      "基径（cm）", "冠幅（cm）", "盖度（%）"]


def _write(tmp_path: Path, headers, rows, name: str = "quadrat.csv") -> Path:
    frame = pd.DataFrame(rows, columns=headers)
    path = tmp_path / name
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    return path


SAMPLE_ROWS = [
    [1, "油松", 3, 12.5, 18.0, None, 320, 60],
    [1, "辽东栎", 2, 9.8, 12.5, None, 280, 45],
    [1, "胡枝子", 5, 1.2, None, 1.5, 90, 20],
]


# ===========================================================================
# 一、列名识别
# ===========================================================================
class TestColumnRecognition:
    def test_fields_are_optional(self):
        for name in ("树高", "基径", "冠幅"):
            assert name in dp.QUADRAT_FIELDS, name
            assert dp.QUADRAT_FIELDS[name] == dp._FIELD_ROLE_OPTIONAL, name

    def test_measurement_fields_constant(self):
        assert set(dp.QUADRAT_MEASUREMENT_FIELDS) == {"树高", "基径", "冠幅"}

    def test_headers_with_units_are_recognized(self, tmp_path):
        path = _write(tmp_path, HEADERS_WITH_UNITS, SAMPLE_ROWS)
        result = dp.parse_files([path])
        assert result.data_type == dp.TYPE_QUADRAT
        for name in ("树高", "基径", "冠幅"):
            assert name in result.df.columns, list(result.df.columns)

    def test_unit_and_bracket_variants_are_stripped(self, tmp_path):
        """括号/单位/空格不该影响识别。"""
        variants = [
            ["样方号", "物种", "株数", "树高(m)", "基径 (cm)", "冠幅[cm]"],
            ["样方号", "物种", "株数", "树高", "基径", "冠幅"],
            ["样方号", "物种", "株数", "株高（M）", "地径（cm）", "冠径（cm）"],
        ]
        for index, headers in enumerate(variants):
            rows = [[1, "油松", 3, 12.5, 1.5, 320]]
            path = _write(tmp_path, headers, rows, "v{0}.csv".format(index))
            result = dp.parse_files([path])
            for name in ("树高", "基径", "冠幅"):
                assert name in result.df.columns, (headers, list(result.df.columns))

    def test_english_headers(self, tmp_path):
        """英文表头（驼峰式）也要能识别。"""
        headers = ["样方号", "物种", "株数", "TreeHeight", "BasalDiameter", "CrownWidth"]
        rows = [[1, "油松", 3, 12.5, 1.5, 320]]
        path = _write(tmp_path, headers, rows)
        result = dp.parse_files([path])
        for name in ("树高", "基径", "冠幅"):
            assert name in result.df.columns, list(result.df.columns)

    def test_breast_and_basal_diameter_not_confused(self, tmp_path):
        """「胸径」与「基径」只差一个字，绝不能互相误配。"""
        path = _write(tmp_path, HEADERS_WITH_UNITS, SAMPLE_ROWS)
        result = dp.parse_files([path])
        # 第一行胸径 18.0、基径为空；第三行反之
        assert result.df.loc[0, "胸径"] == 18.0
        assert result.df.loc[0, "基径"] != 18.0
        assert result.df.loc[2, "基径"] == 1.5
        assert result.df.loc[2, "胸径"] != 1.5

    def test_columns_are_numeric(self, tmp_path):
        path = _write(tmp_path, HEADERS_WITH_UNITS, SAMPLE_ROWS)
        result = dp.parse_files([path])
        for name in ("树高", "基径", "冠幅"):
            assert pd.api.types.is_numeric_dtype(result.df[name]), name

    def test_missing_measurement_columns_is_fine(self, tmp_path):
        """只有必需三列时依然能识别（测量列是可选的）。"""
        headers = ["样方号", "物种", "株数"]
        path = _write(tmp_path, headers, [[1, "油松", 3]])
        result = dp.parse_files([path])
        assert result.data_type == dp.TYPE_QUADRAT


# ===========================================================================
# 二、清洗
# ===========================================================================
class TestCleaning:
    def _clean(self, tmp_path, headers, rows):
        path = _write(tmp_path, headers, rows)
        parsed = dp.parse_files([path])
        result = dc.clean_data(parsed.df, data_type=parsed.data_type)
        return result.df if hasattr(result, "df") else result

    def test_negative_measurements_are_fixed(self, tmp_path):
        """负的树高/基径/冠幅违反物理常识，必须被判为异常并修正。"""
        rows = [
            [1, "油松", 3, -2.0, 18.0, None, 320],
            [1, "辽东栎", 2, 9.8, 12.5, -1.5, 280],
            [2, "胡枝子", 5, 1.2, None, 1.5, -90],
        ]
        headers = ["样方号", "物种", "株数", "树高", "胸径", "基径", "冠幅"]
        clean = self._clean(tmp_path, headers, rows)
        for name in ("树高", "基径", "冠幅"):
            values = pd.to_numeric(clean[name], errors="coerce").dropna()
            assert (values >= 0).all(), (name, list(clean[name]))

    def test_measurements_survive_cleaning(self, tmp_path):
        """正常测量值不能被清洗改掉。"""
        clean = self._clean(tmp_path, HEADERS_WITH_UNITS, SAMPLE_ROWS)
        assert clean.loc[0, "树高"] == 12.5
        assert clean.loc[0, "冠幅"] == 320
        assert clean.loc[1, "树高"] == 9.8


# ===========================================================================
# 三、不参与指标计算（本次约定的边界）
# ===========================================================================
class TestNotUsedInMetrics:
    def test_dominance_still_uses_dbh_or_cover(self, tmp_path):
        """优势度口径不变：仍只看胸径/盖度，测量列不介入。"""
        from core import eco_index

        path = _write(tmp_path, HEADERS_WITH_UNITS, SAMPLE_ROWS)
        parsed = dp.parse_files([path])
        clean = dc.clean_data(parsed.df, data_type=parsed.data_type)
        clean_df = clean.df if hasattr(clean, "df") else clean
        # 有胸径时用胸径；只有基径/树高/冠幅时也不会改用它们
        assert eco_index._resolve_dominance_mode(clean_df) == "胸径"

        only_measurements = clean_df.drop(columns=[c for c in ("胸径", "盖度")
                                                   if c in clean_df.columns])
        assert eco_index._resolve_dominance_mode(only_measurements) == "无"

    def test_measurements_do_not_change_any_metric(self, tmp_path):
        """核心断言：加不加这三列，算出来的指标必须一模一样。

        为什么不断言"指标表里没有这三列"：行级指标表会**原样保留**输入表的列
        （便于用户对照原始测量值），所以树高/基径/冠幅会出现在行级表里，
        但它们是"带过来的原始值"，不是计算出来的指标。真正要保证的是
        它们**不参与任何指标计算**。
        """
        from core import eco_index

        base_headers = ["样方号", "物种", "株数", "胸径", "盖度"]
        base_rows = [
            [1, "油松", 3, 18.0, 60],
            [1, "辽东栎", 2, 12.5, 45],
            [2, "胡枝子", 5, None, 20],
            [2, "油松", 4, 15.0, 55],
        ]
        extended_headers = base_headers + ["树高", "基径", "冠幅"]
        extended_rows = [row + extra for row, extra in zip(
            base_rows, [[12.5, None, 320], [9.8, None, 280],
                        [1.2, 1.5, 90], [11.0, None, 300]])]

        def metrics(headers, rows, name):
            path = _write(tmp_path, headers, rows, name)
            parsed = dp.parse_files([path])
            clean = dc.clean_data(parsed.df, data_type=parsed.data_type)
            frame = clean.df if hasattr(clean, "df") else clean
            outcome = eco_index.compute_indicators(
                frame, data_type=parsed.data_type,
                groups=["群落结构指标", "多样性指标"])
            return outcome

        without = metrics(base_headers, base_rows, "base.csv")
        with_extra = metrics(extended_headers, extended_rows, "extended.csv")

        # 行级表的指标列（排除原始测量列）必须逐格相同
        raw_columns = set(extended_headers) | set(base_headers)
        left = without.row_table.drop(columns=[c for c in without.row_table.columns
                                               if c in raw_columns], errors="ignore")
        right = with_extra.row_table.drop(columns=[c for c in with_extra.row_table.columns
                                                   if c in raw_columns], errors="ignore")
        assert list(left.columns) == list(right.columns), (list(left.columns), list(right.columns))
        for column in left.columns:
            assert left[column].equals(right[column]), column

        # 汇总表里绝不能出现这三列（汇总表全是算出来的指标）
        if with_extra.summary_table is not None:
            for name in ("树高", "基径", "冠幅"):
                assert name not in with_extra.summary_table.columns, name


# ===========================================================================
# 四、绘图字段
# ===========================================================================
class TestPlotting:
    def test_measurements_are_plottable(self, tmp_path):
        path = _write(tmp_path, HEADERS_WITH_UNITS, SAMPLE_ROWS)
        parsed = dp.parse_files([path])
        clean = dc.clean_data(parsed.df, data_type=parsed.data_type)
        clean_df = clean.df if hasattr(clean, "df") else clean
        plottable = pdraw._plottable_numeric(clean_df)
        assert "树高" in plottable
        assert "冠幅" in plottable

    def test_id_column_is_not_a_value_axis(self, tmp_path):
        """样方号是编号，不能被推荐成 Y 轴数值。

        实测缺陷：样方号是整数，被当成数值列，箱线图画出一排无意义的编号。
        """
        path = _write(tmp_path, HEADERS_WITH_UNITS, SAMPLE_ROWS)
        parsed = dp.parse_files([path])
        clean = dc.clean_data(parsed.df, data_type=parsed.data_type)
        clean_df = clean.df if hasattr(clean, "df") else clean
        spec = pdraw.recommend_spec(clean_df, "clean")
        assert "样方号" not in list(spec.values), spec
        assert spec.category != "样方号" or "样方号" not in list(spec.values)

    def test_recommended_chart_can_be_drawn(self, tmp_path):
        path = _write(tmp_path, HEADERS_WITH_UNITS, SAMPLE_ROWS)
        parsed = dp.parse_files([path])
        clean = dc.clean_data(parsed.df, data_type=parsed.data_type)
        clean_df = clean.df if hasattr(clean, "df") else clean
        spec = pdraw.recommend_spec(clean_df, "clean")
        chart = pdraw.draw_chart(clean_df, spec)
        assert chart is not None
        pdraw.close_figure(chart)


# ===========================================================================
# 六、绘图页的「数值 / Y 轴」列表（用户反馈：这三列没出现）
# ===========================================================================
class TestPlotPageValueList:
    """用户反馈「绘图页 Y 轴下拉里没有树高/基径/冠幅」。

    根因不是识别失败，而是**列表内容取决于当前数据源**：
        · 清洗数据、行级指标 → 含原始测量列
        · 汇总指标（默认选中）→ 按样方聚合，只有统计指标，本来就没有测量列
    因此这里逐数据源断言，并确认界面已把"哪个表有几列可选"标出来，
    避免用户以为列被软件弄丢了。
    """

    @staticmethod
    def _value_names(plot_page) -> list:
        return [plot_page._value_list.item(i).data(0x0100)
                for i in range(plot_page._value_list.count())]

    def test_measurements_listed_for_clean_and_row_tables(self, window, qt_app, tmp_path):
        from core import data_clean, data_parse, eco_index
        from core.app_state import AppState

        path = _write(tmp_path, HEADERS_WITH_UNITS, SAMPLE_ROWS)
        parsed = data_parse.parse_files([path])
        state = AppState.instance()
        state.set_table("raw", parsed.df, {"source_files": [str(path)]})
        state.set_text("data_type", parsed.data_type)
        clean = data_clean.clean_data(parsed.df, data_type=parsed.data_type)
        clean_df = clean.df if hasattr(clean, "df") else clean
        state.set_table("clean", clean_df)
        outcome = eco_index.compute_indicators(
            clean_df, data_type=parsed.data_type,
            groups=["群落结构指标", "多样性指标"])
        if outcome.row_table is not None:
            state.set_table("index_row", outcome.row_table)

        page = window.plot_page
        page._on_data_changed("*")
        qt_app.processEvents()

        for label in ("清洗数据", "行级指标"):
            index = page._source_combo.findText(label)
            assert index >= 0, (label, [page._source_combo.itemText(i)
                                        for i in range(page._source_combo.count())])
            page._source_combo.setCurrentIndex(index)
            qt_app.processEvents()
            names = self._value_names(page)
            for column in ("树高", "基径", "冠幅"):
                assert column in names, (label, names)

    def test_summary_table_has_no_measurements(self, window, qt_app, tmp_path):
        """汇总指标是按样方聚合的，不该出现原始测量列 —— 这是正确行为。"""
        from core import data_clean, data_parse, eco_index
        from core.app_state import AppState

        path = _write(tmp_path, HEADERS_WITH_UNITS, SAMPLE_ROWS)
        parsed = data_parse.parse_files([path])
        state = AppState.instance()
        clean = data_clean.clean_data(parsed.df, data_type=parsed.data_type)
        clean_df = clean.df if hasattr(clean, "df") else clean
        state.set_table("clean", clean_df)
        # 真的把汇总表算出来，而不是"没有就跳过"——否则这条测试永远不会真正验证
        outcome = eco_index.compute_indicators(
            clean_df, data_type=parsed.data_type,
            groups=["群落结构指标", "多样性指标"])
        if outcome.summary_table is not None:
            state.set_table("index_summary", outcome.summary_table)
        if outcome.row_table is not None:
            state.set_table("index_row", outcome.row_table)

        page = window.plot_page
        page._on_data_changed("*")
        qt_app.processEvents()
        index = page._source_combo.findText("汇总指标")
        assert index >= 0, [page._source_combo.itemText(i)
                            for i in range(page._source_combo.count())]
        page._source_combo.setCurrentIndex(index)
        qt_app.processEvents()
        names = self._value_names(page)
        assert names, "汇总指标表应有可画的统计指标列"
        assert not any(name in names for name in ("树高", "基径", "冠幅")), names

    def test_value_label_states_source_and_count(self, window, qt_app, tmp_path):
        """标签要写清"哪个数据源、几列可选"，否则用户以为列丢了。"""
        from core import data_clean, data_parse
        from core.app_state import AppState

        path = _write(tmp_path, HEADERS_WITH_UNITS, SAMPLE_ROWS)
        parsed = data_parse.parse_files([path])
        state = AppState.instance()
        clean = data_clean.clean_data(parsed.df, data_type=parsed.data_type)
        state.set_table("clean", clean.df if hasattr(clean, "df") else clean)
        page = window.plot_page
        page._on_data_changed("*")
        qt_app.processEvents()
        index = page._source_combo.findText("清洗数据")
        if index < 0:
            pytest.skip("没有清洗数据表")
        page._source_combo.setCurrentIndex(index)
        qt_app.processEvents()
        text = page._value_label.text()
        assert "清洗数据" in text, text
        assert "列可选" in text, text

    def test_id_column_never_appears_in_value_list(self, window, qt_app, tmp_path):
        """样方号是编号，任何数据源下都不该出现在 Y 轴列表里。"""
        from core import data_clean, data_parse
        from core.app_state import AppState

        path = _write(tmp_path, HEADERS_WITH_UNITS, SAMPLE_ROWS)
        parsed = data_parse.parse_files([path])
        state = AppState.instance()
        clean = data_clean.clean_data(parsed.df, data_type=parsed.data_type)
        state.set_table("clean", clean.df if hasattr(clean, "df") else clean)
        page = window.plot_page
        page._on_data_changed("*")
        qt_app.processEvents()
        for index in range(page._source_combo.count()):
            page._source_combo.setCurrentIndex(index)
            qt_app.processEvents()
            assert "样方号" not in self._value_names(page), \
                page._source_combo.currentText()

    def test_ui_uses_shared_numeric_filter(self):
        """回归防线：绘图页不许自己再写一遍数值列筛选。

        实测踩坑：core 里改好了排除标识列，界面却仍用 is_numeric_dtype
        自己筛一遍，结果样方号照样混进 Y 轴 —— 等于没改。
        """
        source = (ROOT / "ui" / "page_plot.py").read_text(encoding="utf-8")
        assert "plottable_numeric" in source, \
            "绘图页应调用 plot_draw.plottable_numeric，而不是自己判断数值列"


# ===========================================================================
# 七、文档同步（帮助面板要写到这些列）
# ===========================================================================
class TestHelpIsUpdated:
    def test_help_mentions_measurement_columns(self):
        from ui.widgets import help_content

        text = help_content.DATA_REQUIREMENTS
        for name in ("树高", "基径", "冠幅"):
            assert name in text, name
        assert "必需" in text, "数据要求里应标出哪些列是必需的"
