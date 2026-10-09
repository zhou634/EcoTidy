# -*- coding: utf-8 -*-
"""
列映射对话框（开发方案 第一章、第五章 5.5，阶段 2 交付）

为什么必须有它（方案第一章辅助文件职责表原文）：
    多源数据列名千奇百怪（温度 / Temp / T(℃) / air_temp），纯自动识别必然存在失败案例。
    没有手工映射入口时，用户将完全无路可走。

界面设计（左右对照，方案阶段 2 第 4 条）：
    左侧 = 软件需要的标准字段（标注"必需 / 可选"+ 一句通俗说明）
    右侧 = 下拉框，列出你文件里的真实列名
用户在弹窗里同时可以改数据类型——因为"识别不出来"和"选错类型"往往是同一个问题。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui import style as ui_style
from core import data_parse
from utils.i18n import tr

# 每个标准字段的一句话说明（就地解释，用户不必查文档）
_FIELD_TIPS: Dict[str, str] = {
    "样方号": "标识采样方块编号的列，例如 Q1、样方1、A-3。",
    "物种": "物种名称或代码所在的列。",
    "株数": "该样方内该物种个体数量所在的列。",
    "胸径": "乔木胸径（厘米）所在的列；灌木草本数据可以没有这一列。",
    "盖度": "覆盖百分比所在的列；0~100 或 0~1 写法都可以，软件会自动统一。",
    "时间": "记录时刻所在的列，支持 2024-05-03 14:00:00、2024/5/3 14:00、20240503、2024年5月3日，"
            "以及 Excel 里存成数字的时间。",
    "气温": "空气温度所在的列。",
    "相对湿度": "空气相对湿度所在的列。",
    "土壤温度": "土壤温度所在的列（可选）。",
    "处理组": "实验处理名称所在的列，例如对照、增温。",
    "重复": "同一处理下重复编号所在的列。",
}

# 不映射时的下拉显示文字
_NOT_MAPPED = "（不使用这一列）"


class ColumnMapperDialog(QDialog):
    """列映射对话框。

    用法：
        dlg = ColumnMapperDialog(columns=df.columns, data_type="样方群落",
                                 mapping=识别结果映射, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data_type = dlg.selected_data_type()
            mapping = dlg.selected_mapping()
    """

    def __init__(self, columns: List[Any], data_type: str = data_parse.TYPE_QUADRAT,
                 mapping: Optional[Dict[str, Any]] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("列映射 · 把软件需要的字段对应到你的列")
        self.setObjectName("column_mapper")
        self.setModal(True)
        self.setMinimumSize(680, 560)

        self._columns: List[Any] = list(columns)
        self._combos: Dict[str, QComboBox] = {}
        self._field_area: Optional[QVBoxLayout] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(10)

        # ---- 顶部说明：这一步在做什么、为什么需要它 ----
        intro = QLabel(
            "软件没能自动认出全部字段。<b>左边是软件需要的字段，右边请选择你文件里对应的列。</b><br>"
            "带 <span style='color:#1F2329;font-weight:bold'>＊</span> 的是必需字段；没有把握的可以留空，"
            "之后再回来改。改完点【确定】即可继续导入。",
            self,
        )
        intro.setObjectName("mapper_intro")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        # ---- 数据类型选择（识别不出来时，类型也常需要一起改）----
        type_row = QHBoxLayout()
        type_row.setSpacing(8)
        type_row.addWidget(QLabel("这份数据属于：", self))
        self._type_combo = QComboBox(self)
        self._type_combo.setObjectName("mapper_type_combo")
        self._type_combo.setMinimumWidth(180)
        for item in data_parse.DATA_TYPES:
            self._type_combo.addItem(item)
        if data_type in data_parse.DATA_TYPES:
            self._type_combo.setCurrentText(data_type)
        self._type_combo.currentTextChanged.connect(self._rebuild_fields)
        type_row.addWidget(self._type_combo)
        type_row.addStretch(1)
        self._hint = ui_style.HintQLabel("", self)
        self._hint.setObjectName("mapper_hint")
        self._hint.setWordWrap(True)
        type_row.addWidget(self._hint, 3)
        outer.addLayout(type_row)

        separator = QFrame(self)
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setObjectName("mapper_line")
        outer.addWidget(separator)

        # ---- 字段对照区（可滚动，字段多时不会挤压窗口）----
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget(scroll)
        self._field_area = QVBoxLayout(holder)
        self._field_area.setContentsMargins(0, 0, 0, 0)
        self._field_area.setSpacing(8)
        scroll.setWidget(holder)
        outer.addWidget(scroll, 1)

        # ---- 底部按钮：确定 / 取消，外加"恢复自动识别结果" ----
        buttons = QDialogButtonBox(self)
        self._ok_button = buttons.addButton(tr("确定"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(tr("取消"), QDialogButtonBox.ButtonRole.RejectRole)
        self._auto_button = buttons.addButton(tr("重新自动识别"), QDialogButtonBox.ButtonRole.ResetRole)
        self._auto_button.setToolTip(tr("恢复自动识别结果"))
        self._auto_button.clicked.connect(self._reset_to_auto)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self._initial_mapping: Dict[str, Any] = dict(mapping or {})
        self._rebuild_fields(self._type_combo.currentText())

    # ---------------------------------------------------------------------
    # 对外接口
    # ---------------------------------------------------------------------
    def selected_data_type(self) -> str:
        """返回用户选择的数据类型。"""
        return self._type_combo.currentText()

    def selected_mapping(self) -> Dict[str, Any]:
        """返回用户确认的「标准字段 -> 原始列名」映射（未选择的字段不出现）。"""
        result: Dict[str, Any] = {}
        for field_name, combo in self._combos.items():
            column = combo.currentData()
            if column is not None:
                result[field_name] = column
        return result

    # ---------------------------------------------------------------------
    # 内部实现
    # ---------------------------------------------------------------------
    def _rebuild_fields(self, data_type: str) -> None:
        """按所选数据类型重建字段行。"""
        if self._field_area is None:
            return
        # 清空旧控件
        while self._field_area.count():
            item = self._field_area.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._combos.clear()

        fields = data_parse.TYPE_FIELDS.get(data_type, {})
        if not fields:
            label = QLabel("这类数据不需要指定固定字段，软件会把所有数值列都作为测量指标处理。", self)
            label.setWordWrap(True)
            self._field_area.addWidget(label)
            self._field_area.addStretch(1)
            self._hint.setText("")
            return

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)

        for field_name, role in fields.items():
            required = role == data_parse._FIELD_ROLE_REQUIRED
            title = "{0}{1}".format("<span style='color:#1F2329;font-weight:bold'>＊</span> " if required else "", field_name)
            label = QLabel(title + "<br><span style='color:#8C9096;font-size:11px'>{0}</span>".format(
                _FIELD_TIPS.get(field_name, "")), self)
            label.setWordWrap(True)
            label.setMinimumWidth(260)

            combo = QComboBox(self)
            combo.setMinimumWidth(220)
            combo.addItem(_NOT_MAPPED, None)
            for column in self._columns:
                combo.addItem(str(column), column)
            # 回填：优先用传入的映射，其次同名精确匹配
            preset = self._initial_mapping.get(field_name)
            index = combo.findData(preset) if preset is not None else -1
            if index < 0:
                index = combo.findData(field_name)
            combo.setCurrentIndex(max(0, index))
            combo.currentIndexChanged.connect(self._update_hint)
            self._combos[field_name] = combo

            form.addRow(label, combo)
        self._field_area.addLayout(form)
        self._field_area.addStretch(1)
        self._update_hint()

    def _update_hint(self) -> None:
        """实时提示还缺哪些必需字段（不阻塞，只提示）。"""
        data_type = self._type_combo.currentText()
        fields = data_parse.TYPE_FIELDS.get(data_type, {})
        missing = [name for name, role in fields.items()
                   if role == data_parse._FIELD_ROLE_REQUIRED
                   and self._combos.get(name) is not None
                   and self._combos[name].currentData() is None]
        if not fields:
            self._hint.setText("")
        elif missing:
            self._hint.setText("还缺必需字段：{0}。缺了它软件无法完成该类型的数据分析。".format("、".join(missing)))
            self._hint.set_state("error")
        else:
            self._hint.setText("必需字段已齐，可以点【确定】。")
            self._hint.set_state("error")

    def _reset_to_auto(self) -> None:
        """重新按软件自动识别的结果回填（用户改乱了可以一键回到起点）。"""
        self._initial_mapping = {}
        self._rebuild_fields(self._type_combo.currentText())

    def _on_accept(self) -> None:
        """确定前做一次校验：必需字段缺失时给出明确提示，并允许用户坚持继续。"""
        data_type = self._type_combo.currentText()
        fields = data_parse.TYPE_FIELDS.get(data_type, {})
        missing = [name for name, role in fields.items()
                   if role == data_parse._FIELD_ROLE_REQUIRED
                   and self._combos.get(name) is not None
                   and self._combos[name].currentData() is None]
        if missing:
            answer = QMessageBox.warning(
                self,
                "还有必需字段没有指定",
                "以下必需字段还没有对应到你文件里的列：\n{0}\n\n"
                "缺少它们时，后续的清洗与指标计算可能无法进行。\n\n"
                "仍然要继续吗？".format("\n".join("· " + name for name in missing)),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.accept()
