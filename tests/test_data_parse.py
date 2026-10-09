# -*- coding: utf-8 -*-
"""
core/data_parse.py 单元测试（开发方案第六章 阶段 2 交付，与代码同阶段交付）

覆盖重点（全部对应方案里点名的踩坑项）：
    1. 编码探测：UTF-8 带 BOM / GB18030（验证避坑 1）
    2. 分隔符探测：逗号与制表符（sample_plain_gbk.txt 是制表符）
    3. 三类数据自动识别
    4. 时间容错：Excel 序列号、多种文本格式、无法解析置 NaT 并计数
    5. 乱序时间戳排序、长缺口保留
    6. 混合类型必须报错、同类型可合并（验证避坑 11）
    7. "有条件补全"：普通统计数据绝不被强行补出时间列
    8. 错误信息必须可操作（哪个文件 / 哪一列 / 为什么 / 怎么办）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

# 让测试可以直接 python -m pytest 运行（工程根加入搜索路径）
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import data_parse as dp  # noqa: E402

SAMPLES = ROOT / "tests" / "samples"
QUADRAT_CSV = SAMPLES / "sample_quadrat.csv"
SENSOR_XLSX = SAMPLES / "sample_sensor.xlsx"
PLAIN_GBK = SAMPLES / "sample_plain_gbk.txt"


# ===========================================================================
# 一、编码与分隔符（避坑 1）
# ===========================================================================
class TestEncodingDetection:
    """编码探测顺序 utf-8-sig → utf-8 → gb18030。"""

    def test_quadrat_csv_prefers_utf8_sig(self):
        """带 BOM 的 CSV 必须被识别为 utf-8-sig，否则首列列名会多出 \\ufeff。"""
        raw = QUADRAT_CSV.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf"), "样本本身应当是 UTF-8 带 BOM"
        assert dp.detect_encoding(QUADRAT_CSV) == "utf-8-sig"

    def test_bom_does_not_leak_into_column_name(self):
        """核心断言：读回来的首列列名不能带 BOM 字符。"""
        df, _encoding, _delimiter = dp.read_table(QUADRAT_CSV)
        first_column = str(df.columns[0])
        assert first_column == "样方号"
        assert "\ufeff" not in first_column

    def test_plain_gbk_falls_back_to_gb18030(self):
        """GBK 文件用 utf-8 解码必然失败，必须落到 gb18030。"""
        assert dp.detect_encoding(PLAIN_GBK) == "gb18030"
        df, encoding, _delimiter = dp.read_table(PLAIN_GBK)
        assert encoding == "gb18030"
        assert "温度" in [str(c) for c in df.columns]

    def test_utf8_without_bom(self, tmp_path):
        """不带 BOM 的 UTF-8 文件也必须能读（utf-8-sig 兼容无 BOM）。"""
        path = tmp_path / "no_bom.csv"
        path.write_text("样方号,物种,株数\nQ1,油松,10\n", encoding="utf-8")
        assert dp.detect_encoding(path) == "utf-8-sig"
        df, _e, _d = dp.read_table(path)
        assert list(df.columns) == ["样方号", "物种", "株数"]


class TestDelimiterDetection:
    """分隔符探测：逗号、制表符都要支持。"""

    def test_comma(self):
        assert dp.detect_delimiter("a,b,c") == ","

    def test_tab(self):
        """sample_plain_gbk.txt 是制表符分隔，写死逗号会把整行当成一列。"""
        assert dp.detect_delimiter("处理组\t重复\t温度") == "\t"

    def test_semicolon(self):
        assert dp.detect_delimiter("a;b;c") == ";"

    def test_single_column_defaults_to_comma(self):
        assert dp.detect_delimiter("单列名") == ","

    def test_plain_gbk_uses_tab(self):
        df, _e, delimiter = dp.read_table(PLAIN_GBK)
        assert delimiter == "\t"
        assert len(df.columns) == 5


# ===========================================================================
# 二、数据类型识别
# ===========================================================================
class TestTypeRecognition:
    """三类数据的自动识别。"""

    def test_quadrat_recognized(self):
        result = dp.parse_files([QUADRAT_CSV])
        assert result.data_type == dp.TYPE_QUADRAT
        assert list(result.df.columns) == ["样方号", "物种", "株数", "胸径", "盖度"]
        assert result.files[0].needs_mapping is False

    def test_sensor_recognized(self):
        result = dp.parse_files([SENSOR_XLSX])
        assert result.data_type == dp.TYPE_SENSOR
        assert "时间" in result.df.columns

    def test_plain_recognized(self):
        result = dp.parse_files([PLAIN_GBK])
        assert result.data_type == dp.TYPE_PLAIN

    def test_column_name_variants_are_matched(self):
        """列名写法不同也要能认出（温度 / Temp / T(℃) / air_temp）。"""
        df = pd.DataFrame({"Temp(℃)": [20.1], "RH": [55.0], "Timestamp": ["2024-05-03 14:00:00"]})
        recognized = dp.recognize(df)
        assert recognized["data_type"] == dp.TYPE_SENSOR
        mapping = recognized["mapping"]
        assert mapping["时间"] == "Timestamp"
        assert mapping["气温"] == "Temp(℃)"
        assert mapping["相对湿度"] == "RH"

    def test_missing_required_column_requests_manual_mapping(self, tmp_path):
        """识别不出列名时绝不擅自猜测，必须要求手工映射。

        注意区分两个概念：
            recognize()["missing_fields"]  —— 识别阶段"建议用户补哪些字段"
            ParsedFile.missing_fields      —— 解析阶段"最终仍缺哪些必需字段"（界面据此提示）
        本用例断言后者，因为界面用的是它。
        """
        path = tmp_path / "weird.csv"
        path.write_text("colA,colB,colC\nA,油松,3\n", encoding="utf-8")
        parsed = dp.parse_file(path)
        assert parsed.needs_mapping is True
        # 完全认不出表头时类型回落普通统计（没有必需字段），但必须明确提示用户去指定
        assert parsed.warnings, "必须给出中文建议，告诉用户下一步怎么做"
        assert any("列映射" in message for message in parsed.warnings)

    def test_unrecognized_quadrat_reports_missing_columns(self, tmp_path):
        """能部分认出是样方数据但缺必需列时，必须精确列出缺哪一列。"""
        path = tmp_path / "half.csv"
        path.write_text("样方号,物种,colC\nQ1,油松,3\n", encoding="utf-8")
        parsed = dp.parse_file(path)
        assert parsed.needs_mapping is True
        # 类型回落普通统计（无必需字段），但识别阶段已明确指出缺「株数」
        assert "株数" in parsed.suggested_missing
        assert "样方号" not in parsed.suggested_missing
        assert any("株数" in message for message in parsed.warnings)

    def test_partial_recognition_reports_exact_missing_field(self):
        """只认出一部分列时，missing_fields 必须精确列出缺失的那个必需字段。"""
        df = pd.DataFrame({"样方号": ["Q1"], "物种": ["油松"], "colC": [3]})
        recognized = dp.recognize(df)
        assert recognized["needs_mapping"] is True
        assert "株数" in recognized["missing_fields"]
        assert "样方号" not in recognized["missing_fields"]
        assert "物种" not in recognized["missing_fields"]

    def test_short_ambiguous_names_do_not_false_match(self):
        """「地块」「植物」这类短名字不得被误配到「样地号」「植物种」，
        否则样方数据会被误判成缺字段，逼用户做无意义的列映射。"""
        df = pd.DataFrame({"地块": ["A"], "植物": ["油松"], "株数": [3], "样方号": ["Q1"]})
        recognized = dp.recognize(df)
        assert recognized["mapping"]["样方号"] == "样方号"
        assert recognized["mapping"]["物种"] is None

    def test_unknown_table_falls_back_to_plain_with_mapping_request(self):
        """完全不认识的表：类型回落为普通统计，同时要求用户确认列映射。"""
        df = pd.DataFrame({"col1": [1, 2], "col2": ["x", "y"]})
        recognized = dp.recognize(df)
        assert recognized["data_type"] == dp.TYPE_PLAIN
        assert recognized["needs_mapping"] is True
        assert recognized["suggestions"], "必须给出中文建议"


# ===========================================================================
# 三、条件补全：普通统计数据不得被强行补时间列
# ===========================================================================
class TestConditionalCompletion:
    """方案阶段 2 第 4 条：仅对识别到的时间类数据补时间列。"""

    def test_plain_data_gets_no_time_column(self):
        result = dp.parse_files([PLAIN_GBK])
        assert "时间" not in result.df.columns, "普通统计数据本就没有时间列，不得强行补出整列 NaT"
        assert result.time_range is None

    def test_quadrat_data_gets_no_time_column(self):
        result = dp.parse_files([QUADRAT_CSV])
        assert "时间" not in result.df.columns

    def test_sensor_data_has_normalized_time(self):
        result = dp.parse_files([SENSOR_XLSX])
        assert "时间" in result.df.columns
        # 统一输出格式 YYYY-MM-DD HH:MM:SS，且必须是第一列
        assert str(result.df.columns[0]) == "时间"
        sample = result.df["时间"].dropna().iloc[0]
        assert pd.to_datetime(sample, format=dp.TIME_FORMAT) is not None
        assert len(sample) == 19


# ===========================================================================
# 四、时间容错解析
# ===========================================================================
class TestTimeParsing:
    """阶段 2 第 3 条点名的四种格式 + Excel 序列号。"""

    @pytest.mark.parametrize("text", [
        "2024-05-03 14:00:00",
        "2024/5/3 14:00",
        "2024年5月3日",
        "2024-05-03",
        "2024.05.03",
    ])
    def test_text_formats(self, text):
        parsed, nat = dp.parse_time_column(pd.Series([text]))
        assert nat == 0, "{0} 应当能解析".format(text)
        assert parsed.iloc[0] is not pd.NaT

    def test_compact_yyyymmdd(self):
        parsed, nat = dp.parse_time_column(pd.Series(["20240503"]))
        assert nat == 0
        assert parsed.iloc[0].strftime("%Y-%m-%d") == "2024-05-03"

    def test_excel_serial_number(self):
        """Excel 序列号 45413 = 2024-05-01（origin=1899-12-30）。"""
        parsed, nat = dp.parse_time_column(pd.Series([45413.0]))
        assert nat == 0
        assert parsed.iloc[0].strftime("%Y-%m-%d") == "2024-05-01"

    def test_excel_serial_fraction_becomes_hour(self):
        """45413.5 应当是当天 12:00。"""
        parsed, _nat = dp.parse_time_column(pd.Series([45413.5]))
        assert parsed.iloc[0].strftime("%H:%M") == "12:00"

    def test_unparsable_becomes_nat_and_is_counted(self):
        """无法解析的时间置 NaT 并计数上报，绝不静默丢弃。"""
        parsed, nat = dp.parse_time_column(pd.Series(["2024-05-03 14:00:00", "不是时间", "", None]))
        assert nat == 3
        assert parsed.notna().sum() == 1

    def test_seconds_are_rounded(self):
        """Excel 序列号的浮点尾差必须被取整到秒，不能出现 01:00:00.028799。"""
        parsed, _nat = dp.parse_time_column(pd.Series([45413.04166666666]))
        assert parsed.iloc[0].strftime(dp.TIME_FORMAT) == "2024-05-01 01:00:00"


class TestSensorSampleTimeFacts:
    """针对固定样本 sample_sensor.xlsx 的客观断言（阶段验收判据）。"""

    def test_row_count_and_span(self):
        result = dp.parse_files([SENSOR_XLSX])
        assert len(result.df) == 230
        assert result.time_range == ("2024-05-01 00:00:00", "2024-05-10 23:00:00")

    def test_no_unparsable_time(self):
        result = dp.parse_files([SENSOR_XLSX])
        assert result.nat_count == 0

    def test_out_of_order_detected_and_sorted(self):
        """样本里刻意交换了两对时间戳，解析后必须按时间升序排好。"""
        result = dp.parse_files([SENSOR_XLSX])
        assert result.out_of_order_rows == 2
        parsed = pd.to_datetime(result.df["时间"], format=dp.TIME_FORMAT)
        assert parsed.is_monotonic_increasing

    def test_long_gap_preserved(self):
        """连续 10 点的断测不能被补出来：时间轴上必须留下 11 小时的跳变。"""
        result = dp.parse_files([SENSOR_XLSX])
        parsed = pd.to_datetime(result.df["时间"], format=dp.TIME_FORMAT)
        deltas = parsed.diff().dropna()
        assert deltas.max() == pd.Timedelta(hours=11)

    def test_missing_values_preserved_not_filled(self):
        """解析阶段绝不做填充（填充是阶段 3 的职责，且要遵守 max_gap）。"""
        result = dp.parse_files([SENSOR_XLSX])
        assert int(result.df["气温"].isna().sum()) == 2
        assert int(result.df["相对湿度"].isna().sum()) == 1
        assert int(result.df["土壤温度"].isna().sum()) == 4

    def test_spike_value_untouched(self):
        """突变值属于清洗阶段的职责，解析阶段必须原样保留。"""
        result = dp.parse_files([SENSOR_XLSX])
        assert float(result.df["气温"].max()) == 58.7
        assert float(result.df["相对湿度"].min()) == -15.0


# ===========================================================================
# 五、样方样本的解析结果
# ===========================================================================
class TestQuadratSample:
    """sample_quadrat.csv 的解析事实（脏数据必须原样带过来，交给阶段 3 处理）。"""

    def test_shape_and_columns(self):
        result = dp.parse_files([QUADRAT_CSV])
        assert len(result.df) == 38
        assert list(result.df.columns) == ["样方号", "物种", "株数", "胸径", "盖度"]

    def test_dirty_values_survive_parsing(self):
        result = dp.parse_files([QUADRAT_CSV])
        assert int((result.df["株数"] < 0).sum()) == 2
        assert int((result.df["盖度"] > 100).sum()) == 3
        assert int(result.df.duplicated().sum()) == 2
        assert int(result.df["株数"].isna().sum()) == 1

    def test_cover_scale_mixture_preserved(self):
        """0-1 制与百分制混用是刻意设计的脏数据，解析阶段不得擅自换算。"""
        result = dp.parse_files([QUADRAT_CSV])
        cover = result.df["盖度"].dropna()
        assert int(((cover > 0) & (cover <= 1)).sum()) == 11

    def test_numeric_columns_are_numeric(self):
        """数值列必须转成数值类型，否则阶段 3 无法做数值判断。"""
        result = dp.parse_files([QUADRAT_CSV])
        for column in ("株数", "胸径", "盖度"):
            assert pd.api.types.is_numeric_dtype(result.df[column]), column + " 应当是数值列"


# ===========================================================================
# 六、多文件：混合类型报错、同类型合并（避坑 11）
# ===========================================================================
class TestMultiFile:
    def test_mixed_types_raise(self):
        with pytest.raises(dp.ParseMixedTypesError) as excinfo:
            dp.parse_files([QUADRAT_CSV, SENSOR_XLSX])
        error = excinfo.value
        assert dp.TYPE_QUADRAT in error.types and dp.TYPE_SENSOR in error.types
        assert error.suggestion, "必须给出中文处理建议"

    def test_same_type_merges(self):
        demo = ROOT / "static" / "demo" / "demo_quadrat.csv"
        result = dp.parse_files([QUADRAT_CSV, demo])
        assert len(result.df) == 38 + 72
        assert len(result.files) == 2
        assert any("合并" in message for message in result.warnings)

    def test_overlapping_quadrats_are_warned(self):
        """同一批样方被导入两次会重复计入，必须提示用户（这类错误在结果里看不出来）。"""
        demo = ROOT / "static" / "demo" / "demo_quadrat.csv"
        result = dp.parse_files([demo, demo])
        assert any("样方号 + 物种" in message for message in result.warnings)


# ===========================================================================
# 七、错误信息必须可操作
# ===========================================================================
class TestErrorMessages:
    """方案 5.5：报错必须回答 哪个文件 / 哪一列 / 为什么 / 怎么办。"""

    def test_missing_file(self, tmp_path):
        with pytest.raises(dp.ParseError) as excinfo:
            dp.parse_files([tmp_path / "不存在.csv"])
        error = excinfo.value
        assert "不存在.csv" in str(error)
        assert error.reason and error.suggestion

    def test_empty_file(self, tmp_path):
        path = tmp_path / "empty.csv"
        path.write_text("", encoding="utf-8")
        with pytest.raises(dp.ParseError) as excinfo:
            dp.parse_files([path])
        assert "空文件" in str(excinfo.value)

    def test_unsupported_format_mentions_supported_ones(self, tmp_path):
        path = tmp_path / "x.docx"
        path.write_text("x", encoding="utf-8")
        with pytest.raises(dp.ParseError) as excinfo:
            dp.parse_files([path])
        error = excinfo.value
        # 主文案说清"哪个文件"，suggestion 说清"支持哪些格式、怎么办"
        assert "x.docx" in str(error)
        assert "xlsx" in error.reason and "csv" in error.reason
        assert "另存为" in error.suggestion

    def test_no_files_selected(self):
        with pytest.raises(dp.ParseError) as excinfo:
            dp.parse_files([])
        assert "示例数据" in excinfo.value.suggestion

    def test_header_only_file_rejected(self, tmp_path):
        path = tmp_path / "header_only.csv"
        path.write_text("样方号,物种,株数\n", encoding="utf-8")
        with pytest.raises(dp.ParseError) as excinfo:
            dp.parse_files([path])
        assert "没有可用数据行" in str(excinfo.value)

    def test_error_messages_are_chinese(self, tmp_path):
        """禁止技术性英文裸奔：错误主文案里不应出现英文异常类名。"""
        with pytest.raises(dp.ParseError) as excinfo:
            dp.parse_files([tmp_path / "缺失.csv"])
        message = str(excinfo.value)
        for forbidden in ("Traceback", "FileNotFoundError", "Exception"):
            assert forbidden not in message


# ===========================================================================
# 八、示例数据与质量概览
# ===========================================================================
class TestDemoData:
    def test_demo_quadrat_loads(self):
        result = dp.load_demo_data()
        assert result.data_type == dp.TYPE_QUADRAT
        assert len(result.df) == 72
        assert result.df["样方号"].nunique() == 12
        assert result.df["物种"].nunique() == 6

    def test_demo_sensor_loads(self):
        result = dp.load_demo_data(dp.TYPE_SENSOR)
        assert result.data_type == dp.TYPE_SENSOR
        assert len(result.df) == 720
        assert result.time_range == ("2024-06-01 00:00:00", "2024-06-30 23:00:00")

    def test_demo_data_is_clean(self):
        """示例数据必须干净：不能有负数、超限盖度、重复行或缺失值。"""
        result = dp.load_demo_data()
        assert int((result.df["株数"] <= 0).sum()) == 0
        assert int((result.df["盖度"] > 100).sum()) == 0
        assert int(result.df.duplicated().sum()) == 0
        assert int(result.df[["样方号", "物种", "株数", "盖度"]].isna().sum().sum()) == 0

    def test_quality_summary(self):
        result = dp.parse_files([SENSOR_XLSX])
        summary = dp.quality_summary(result.df)
        assert summary["rows"] == 230
        assert summary["columns"] == 4
        assert summary["total_missing"] == 7
        assert summary["time_span"] == ("2024-05-01 00:00:00", "2024-05-10 23:00:00")
        assert summary["missing_rate"]["气温"] == pytest.approx(0.87, abs=0.01)

    def test_is_empty_frame(self):
        assert dp.is_empty_frame(None) is True
        assert dp.is_empty_frame(pd.DataFrame()) is True
        assert dp.is_empty_frame(pd.DataFrame({"a": [1]})) is False


# ===========================================================================
# 九、列映射覆盖自动识别（手工映射必须真正生效）
# ===========================================================================
class TestManualMapping:
    def test_user_mapping_overrides_guessing(self, tmp_path):
        """列名完全对不上时，用户手工指定后必须能正常解析。"""
        path = tmp_path / "weird.csv"
        path.write_text("A,B,C\nQ1,油松,12\nQ1,辽东栎,8\n", encoding="utf-8")
        mapping = {"样方号": "A", "物种": "B", "株数": "C"}
        result = dp.parse_files([path], data_type=dp.TYPE_QUADRAT, mapping=mapping)
        assert list(result.df.columns) == ["样方号", "物种", "株数"]
        assert len(result.df) == 2
        assert result.df["株数"].sum() == 20

    def test_user_specified_type_is_respected(self, tmp_path):
        """用户把类型改成普通统计后，不得再按样方群落去要求必需字段。"""
        path = tmp_path / "plain.csv"
        path.write_text("处理组,重复,温度\n对照,1,25.1\n", encoding="utf-8")
        result = dp.parse_files([path], data_type=dp.TYPE_PLAIN)
        assert result.data_type == dp.TYPE_PLAIN
        assert result.files[0].needs_mapping is False

    def test_plain_type_does_not_rename_columns(self, tmp_path):
        """普通统计数据里的「温度」不得被改名成「气温」——那类数据没有这个字段。"""
        path = tmp_path / "plain2.csv"
        path.write_text("处理组,重复,温度\n对照,1,25.1\n", encoding="utf-8")
        result = dp.parse_files([path], data_type=dp.TYPE_PLAIN)
        assert "温度" in result.df.columns
        assert "气温" not in result.df.columns


# ===========================================================================
# 十、probe_file（导入页文件列表用）
# ===========================================================================
class TestProbeFile:
    def test_probe_returns_type_and_columns(self):
        probe = dp.probe_file(QUADRAT_CSV)
        assert probe["data_type"] == dp.TYPE_QUADRAT
        assert probe["columns"] == ["样方号", "物种", "株数", "胸径", "盖度"]
        assert probe["encoding"] == "utf-8-sig"

    def test_probe_bad_file_does_not_raise(self, tmp_path):
        """探测脏文件必须返回错误信息而不是抛异常，否则一选十几个文件就整批中断。"""
        path = tmp_path / "bad.docx"
        path.write_text("x", encoding="utf-8")
        probe = dp.probe_file(path)
        assert probe["data_type"] is None
        assert probe["error"]
        assert probe["suggestion"]
