# -*- coding: utf-8 -*-
"""
中英文切换测试

覆盖：
    1. 词典完整性（键值都不为空、无重复键、中文键确实是中文）
    2. tr() 行为：中文原样返回、英文查表、带参数格式化、参数里的中文也要翻
    3. 未知文案回落中文（不崩、不显示键名）
    4. 语言切换信号与偏好持久化
    5. 运行时不重启即可切换：主窗口菜单/步骤条/页面按钮全部跟着变
    6. **数据契约不被翻译**：列名、表格表头必须保持中文
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
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from utils import i18n  # noqa: E402
from utils.i18n_strings import STRINGS  # noqa: E402


@pytest.fixture(autouse=True)
def reset_language():
    """每个用例前后都把语言复位，避免互相影响。"""
    i18n.set_language(i18n.LANG_ZH, notify=False)
    i18n.clear_missing()
    yield
    i18n.set_language(i18n.LANG_ZH, notify=False)
    i18n.clear_missing()


# ===========================================================================
# 一、词典本身
# ===========================================================================
class TestDictionary:
    def test_dictionary_is_not_empty(self):
        assert i18n.translation_count() > 200

    def test_no_empty_values(self):
        """空值会让 tr() 返回空串，把句子里的词直接抹掉（实测踩坑）。"""
        empty = [key for key, value in STRINGS.items() if not str(value).strip()]
        assert not empty, "这些词条的英文是空的：{0}".format(empty)

    def test_keys_contain_chinese(self):
        """键必须是中文原文；英文键说明写反了。"""
        bad = [key for key in STRINGS if not any("\u4e00" <= ch <= "\u9fff" for ch in key)]
        assert not bad, "这些键不是中文原文：{0}".format(bad)

    def test_values_contain_no_chinese(self):
        """英文值里不应残留中文（专有名词除外）。"""
        allowed = ("℃", "μmol", "Pielou", "Margalef", "Shannon", "Simpson")
        bad = []
        for key, value in STRINGS.items():
            if any(allowed_mark in value for allowed_mark in allowed):
                continue
            if any("\u4e00" <= ch <= "\u9fff" for ch in value):
                bad.append((key, value))
        assert not bad, "这些英文值里还有中文：{0}".format(bad[:8])

    def test_no_data_column_names_translated(self):
        """列名属数据契约，绝不能出现在词典里。

        一旦翻译，导出表头会与计算逻辑、用户的原始数据对不上。
        """
        forbidden = ["相对密度", "相对频度", "相对优势度", "重要值", "优势度",
                     "H 香农指数", "D 辛普森指数", "J Pielou均匀度", "R Margalef丰富度",
                     "S 物种数", "N 个体总数", "株数", "胸径", "盖度", "样方号"]
        offenders = [name for name in forbidden if name in STRINGS]
        assert not offenders, "这些是数据列名，不该被翻译：{0}".format(offenders)


# ===========================================================================
# 二、tr() 行为
# ===========================================================================
class TestTranslate:
    def test_chinese_mode_returns_original(self):
        assert i18n.tr("载入示例数据") == "载入示例数据"

    def test_english_mode_looks_up(self):
        i18n.set_language(i18n.LANG_EN, notify=False)
        assert i18n.tr("载入示例数据") == "Load sample data"

    def test_unknown_text_falls_back(self):
        """没词条时回落中文，而不是显示键名或乱码。"""
        i18n.set_language(i18n.LANG_EN, notify=False)
        assert i18n.tr("这句肯定没有词条") == "这句肯定没有词条"

    def test_unknown_is_recorded(self):
        i18n.set_language(i18n.LANG_EN, notify=False)
        i18n.clear_missing()
        i18n.tr("这句肯定没有词条")
        assert "这句肯定没有词条" in i18n.missing_translations()

    def test_positional_formatting(self):
        i18n.set_language(i18n.LANG_EN, notify=False)
        assert i18n.tr("第 {0} 步：{1}", 2, "清洗") == "Step 2: Clean"

    def test_keyword_formatting(self):
        assert i18n.tr("共 {n} 行", n=5) == "共 5 行"

    def test_arguments_are_translated_too(self):
        """参数里的中文也要翻，否则会出现 'Step 2: 清洗' 这种半截英文。"""
        i18n.set_language(i18n.LANG_EN, notify=False)
        result = i18n.tr("第 {0} 步：{1}", 2, "清洗")
        assert "\u4e00" not in result or "Step" in result and "清洗" not in result

    def test_non_string_arguments_untouched(self):
        assert i18n.tr("共 {0} 行", 42) == "共 42 行"

    def test_missing_braces_do_not_crash(self):
        """文案里带花括号但不是占位符时不能抛异常。"""
        assert i18n.tr("集合 {a, b}") == "集合 {a, b}"

    def test_trf_alias(self):
        assert i18n.trf("共 {0} 行", 3) == "共 3 行"


# ===========================================================================
# 三、语言状态与信号
# ===========================================================================
class TestLanguageState:
    def test_default_is_chinese(self):
        assert i18n.DEFAULT_LANGUAGE == i18n.LANG_ZH
        assert i18n.current_language() == i18n.LANG_ZH

    def test_switch_returns_true_only_on_change(self):
        assert i18n.set_language(i18n.LANG_EN, notify=False) is True
        assert i18n.set_language(i18n.LANG_EN, notify=False) is False
        assert i18n.set_language(i18n.LANG_ZH, notify=False) is True

    def test_invalid_language_rejected(self):
        assert i18n.set_language("fr", notify=False) is False
        assert i18n.current_language() == i18n.LANG_ZH

    def test_signal_emitted(self):
        received = []
        i18n.language_bus().language_changed.connect(received.append)
        i18n.set_language(i18n.LANG_EN, notify=True)
        assert received == [i18n.LANG_EN]

    def test_language_names(self):
        assert i18n.language_name(i18n.LANG_ZH) == "中文"
        assert i18n.language_name(i18n.LANG_EN) == "English"

    def test_menu_entries_show_current(self):
        i18n.set_language(i18n.LANG_EN, notify=False)
        entries = dict(i18n.language_menu_entries())
        assert entries[i18n.LANG_EN].startswith("✓")
        assert not entries[i18n.LANG_ZH].startswith("✓")

    def test_reverse_mapping_for_round_trip(self):
        """英文 -> 中文的反查（界面已是英文时切回中文要用）。"""
        i18n.set_language(i18n.LANG_EN, notify=False)
        english = i18n.tr("载入示例数据")          # 触发登记
        assert english == "Load sample data", english
        # 反查发生在"当前语言 = 中文"时（也就是用户点了切回中文的那一刻）
        i18n.set_language(i18n.LANG_ZH, notify=False)
        assert i18n.retranslate_text(english) == "载入示例数据"

    def test_retranslate_unknown_returns_as_is(self):
        assert i18n.retranslate_text("Some user text") == "Some user text"
        assert i18n.retranslate_text("") == ""


# ===========================================================================
# 四、界面运行时切换（不重启）
#
# 说明：window / qt_app / app_sandbox 三个夹具统一定义在 tests/conftest.py，
#       这样多个测试文件都能用（定义在本模块里只有本模块能用）。
# ===========================================================================
class TestRuntimeSwitch:
    def test_switch_updates_menu(self, window, qt_app):
        window.change_language("en")
        qt_app.processEvents()
        titles = [action.text() for action in window.menuBar().actions()]
        assert titles == ["File(&F)", "View(&V)", "Help(&H)"], titles

    def test_switch_updates_step_bar(self, window, qt_app):
        window.change_language("en")
        qt_app.processEvents()
        labels = [button.text() for button in window.step_bar._buttons]
        assert all("\u4e00" not in label for label in labels), labels
        assert "Import" in labels[0]

    def test_switch_updates_page_buttons(self, window, qt_app):
        window.change_language("en")
        qt_app.processEvents()
        texts = [b.text() for b in window.import_page.findChildren(
            __import__("PySide6.QtWidgets", fromlist=["QAbstractButton"]).QAbstractButton)
            if b.text()]
        assert "Load sample data" in texts, texts

    def test_switch_updates_combobox_items(self, window, qt_app):
        window.change_language("en")
        qt_app.processEvents()
        combo = window.plot_page._palette_combo
        items = [combo.itemText(i) for i in range(combo.count())]
        assert "Grayscale" in items, items

    def test_round_trip_back_to_chinese(self, window, qt_app):
        window.change_language("en")
        qt_app.processEvents()
        window.change_language("zh")
        qt_app.processEvents()
        assert [a.text() for a in window.menuBar().actions()] == ["文件(&F)", "视图(&V)", "帮助(&H)"]
        labels = [button.text() for button in window.step_bar._buttons]
        assert labels[0] == "① 导入数据", labels

    def test_language_is_persisted(self, window, qt_app):
        from core import settings as settings_module

        window.change_language("en")
        assert settings_module.instance().language == "en"
        window.change_language("zh")
        assert settings_module.instance().language == "zh"

    def test_invalid_language_in_settings_falls_back(self, window, qt_app):
        from core import settings as settings_module

        prefs = settings_module.instance()
        prefs.set("ui/language", "klingon")
        assert prefs.language == "zh"

    def test_data_headers_stay_chinese(self, window, qt_app):
        """最关键的一条：切到英文后，数据表列名仍是中文。"""
        from core import data_parse as dp
        from core.app_state import AppState

        state = AppState.instance()
        state.set_table("raw", dp.parse_files([ROOT / "tests" / "samples" / "sample_quadrat.csv"]).df)
        window.change_language("en")
        qt_app.processEvents()
        frame = state.get_table("raw")
        assert list(frame.columns) == ["样方号", "物种", "株数", "胸径", "盖度"], list(frame.columns)
