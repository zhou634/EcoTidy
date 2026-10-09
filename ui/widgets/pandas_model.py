# -*- coding: utf-8 -*-
"""
QAbstractTableModel 数据表模型（开发方案 第一章、第二章，阶段 2 交付）

为什么必须有它（方案第一章辅助文件职责表原文）：
    QTableWidget 逐单元格建 item，传感器时序动辄 10 万行，10 万 × 10 列 = 100 万个 item，
    内存与渲染都会崩溃。因此全工程统一使用 QTableView + 本模型，禁止用 QTableWidget 展示数据。

关键设计：
    1. 默认只向前端暴露前 5000 行（方案第二章"大表默认只渲染前 5000 行"），
       由 preview_rows 控制，界面必须同时显示"共 N 行，当前预览前 5000 行"。
    2. 缺失值统一显示为空白（而不是 nan / NaT / None），符合"不出现英文技术术语裸奔"。
    3. 单元格悬浮提示显示该列的中文说明（阶段 4 会传入指标公式，阶段 2 先用列名）。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import pandas as pd
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor

# 默认预览行数上限（方案第二章规定）
DEFAULT_PREVIEW_ROWS = 5000

# 被修改单元格的高亮色（浅橙：与主色蓝区分明显，黑白打印也能看出层次）
HIGHLIGHT_COLOR = QColor("#EDEEF0")
# 高亮单元格的悬浮提示前缀（让用户一眼知道这个格子为什么是橙色的）
HIGHLIGHT_TOOLTIP_PREFIX = "本单元格已被清洗修改："


class PandasModel(QAbstractTableModel):
    """把 pandas 数据表适配成 Qt 表格模型的只读适配器。

    用法：
        model = PandasModel(df)
        table_view.setModel(model)
        label.setText(model.summary_text())    # "共 1240 行 × 8 列，当前预览前 5000 行"
    切换数据时调用 set_dataframe(df)，模型会自行重置，不要新建模型后忘记 setModel。
    """

    def __init__(self, df: Optional[pd.DataFrame] = None,
                 preview_rows: int = DEFAULT_PREVIEW_ROWS,
                 column_tips: Optional[Dict[str, str]] = None,
                 parent: Optional[Any] = None) -> None:
        super().__init__(parent)
        self._df: pd.DataFrame = pd.DataFrame()
        self._preview_rows = max(1, int(preview_rows))
        self._column_tips: Dict[str, str] = dict(column_tips or {})
        # 高亮映射：{(行号(1起算), 列名): 说明文字}，用于标注"被清洗修改过的单元格"
        self._highlights: Dict[Tuple[int, str], str] = {}
        if df is not None:
            self.set_dataframe(df)

    # ---------------------------------------------------------------------
    # 数据装载
    # ---------------------------------------------------------------------
    def set_dataframe(self, df: Optional[pd.DataFrame]) -> None:
        """整体替换数据表并通知视图重绘。"""
        self.beginResetModel()
        self._df = df if isinstance(df, pd.DataFrame) else pd.DataFrame()
        # 换了数据表之后旧的坐标不再有意义，必须一起清掉，否则会高亮到无关单元格
        self._highlights = {}
        self.endResetModel()

    def set_highlight(self, highlights: Optional[Dict[Tuple[int, str], str]]) -> None:
        """设置需要高亮的单元格。

        参数格式：{(行号(从 1 开始，与左侧行号一致), 列名): 处理方式说明}
        清洗页用它把"被修改过的单元格"标成浅橙色，让用户看得见软件做了什么。
        """
        self.beginResetModel()
        self._highlights = dict(highlights or {})
        self.endResetModel()

    def highlight_count(self) -> int:
        """当前高亮的单元格个数（供界面提示与测试断言）。"""
        return len(self._highlights)

    def dataframe(self) -> pd.DataFrame:
        """返回当前持有的数据表（只读用途；修改数据请走 AppState）。"""
        return self._df

    def set_column_tips(self, tips: Dict[str, str]) -> None:
        """设置各列的中文说明，鼠标悬浮表头时显示（阶段 4 会传入指标公式）。"""
        self._column_tips = dict(tips or {})
        if self.columnCount() > 0:
            header = self.headerData(0, Qt.Orientation.Horizontal, Qt.ItemDataRole.ToolTipRole)
            _ = header  # 触发一次即可，视图会在下次绘制时重新取

    # ---------------------------------------------------------------------
    # 只读属性
    # ---------------------------------------------------------------------
    @property
    def preview_rows(self) -> int:
        """当前允许渲染的最大行数。"""
        return self._preview_rows

    @property
    def total_rows(self) -> int:
        """数据表真实总行数。"""
        return int(len(self._df))

    @property
    def total_columns(self) -> int:
        """数据表真实总列数。"""
        return int(len(self._df.columns))

    @property
    def is_truncated(self) -> bool:
        """是否因为超过预览上限而被截断显示。"""
        return self.total_rows > self._preview_rows

    def visible_rows(self) -> int:
        """实际提供给视图的行数。"""
        return min(self.total_rows, self._preview_rows)

    def summary_text(self) -> str:
        """生成界面提示文案：共 N 行，当前预览前 5000 行。"""
        if self.total_rows == 0:
            return "当前没有可显示的数据。"
        base = "共 {0:,} 行 × {1} 列".format(self.total_rows, self.total_columns)
        if self.is_truncated:
            return "{0}，当前预览前 {1:,} 行".format(base, self._preview_rows)
        return base + "，已全部显示"

    # ---------------------------------------------------------------------
    # QAbstractTableModel 接口
    # ---------------------------------------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return self.visible_rows()

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return self.total_columns

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole,
                        Qt.ItemDataRole.BackgroundRole):
            return None

        row, column = index.row(), index.column()
        if row >= self.visible_rows() or column >= self.total_columns:
            return None

        try:
            value = self._df.iat[row, column]
        except (IndexError, KeyError):
            return None
        column_name = str(self._df.columns[column])
        # 数据表里的行号是 1 起算（与左侧行号一致），高亮键用的也是这个行号
        change_note = self._highlights.get((row + 1, column_name))

        if role == Qt.ItemDataRole.BackgroundRole:
            return QBrush(HIGHLIGHT_COLOR) if change_note else None

        if role == Qt.ItemDataRole.ToolTipRole:
            base = self._column_tips.get(column_name)
            prefix = "{0}{1}".format(HIGHLIGHT_TOOLTIP_PREFIX, change_note) if change_note else ""
            detail = "{0}\n{1}".format(column_name, base) if base else "第 {0:,} 行 · {1}".format(row + 1, column_name)
            return "{0}\n{1}".format(prefix, detail) if prefix else detail
        return self._format_value(value)

    def headerData(self, section: int, orientation: Qt.Orientation,  # noqa: N802
                   role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal:
            if section >= self.total_columns:
                return None
            name = str(self._df.columns[section])
            if role == Qt.ItemDataRole.DisplayRole:
                return name
            if role == Qt.ItemDataRole.ToolTipRole:
                tip = self._column_tips.get(name)
                return "{0}\n{1}".format(name, tip) if tip else name
            return None
        # 纵向表头显示 1 起算的行号（用户与清洗日志里的"行号"才能对上）
        if role == Qt.ItemDataRole.DisplayRole:
            return str(section + 1)
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        """只读：所有单元格不可编辑（数据修改一律经 AppState，禁止直接改表）。"""
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    # ---------------------------------------------------------------------
    # 内部工具
    # ---------------------------------------------------------------------
    @staticmethod
    def _format_value(value: Any) -> str:
        """把单元格值转成界面文本：缺失值留空，浮点数不超过 4 位小数。"""
        if value is None:
            return ""
        # 缺失值判断（pd.isna 对数组会报错，这里只处理标量）
        try:
            if pd.isna(value):
                return ""
        except (TypeError, ValueError):
            pass

        if isinstance(value, float):
            if value != value:  # NaN
                return ""
            # 整数型浮点去掉多余小数位（12.0 → 12），其余保留 4 位
            if float(value).is_integer():
                return str(int(value))
            return "{0:.4f}".format(value).rstrip("0").rstrip(".")
        if isinstance(value, (pd.Timestamp,)):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        return str(value)


def column_tips_from_names(df: pd.DataFrame, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """为数据表的列生成中文悬浮说明。

    阶段 2 只能说明"这是什么列"；阶段 4 会把指标公式与生态学含义合并进来
    （方案 5.6"结果可解释"），届时通过参数 extra 覆盖本函数的默认说明。
    """
    tips: Dict[str, str] = {}
    for column in df.columns:
        name = str(column)
        tips[name] = _DEFAULT_COLUMN_TIPS.get(name, "数据列：{0}".format(name))
    if extra:
        tips.update(extra)
    return tips


# 常见列的中文说明（供所有页面复用，避免各处文案不一致）
_DEFAULT_COLUMN_TIPS: Dict[str, str] = {
    "样方号": "调查样方的编号，同一个编号下的若干行组成一个群落样方。",
    "物种": "物种名称或代码。",
    "株数": "该样方内该物种的个体数量（计数，清洗后为整数）。",
    "胸径": "乔木胸高直径，单位厘米；灌木与草本天然没有胸径，留空属正常。",
    "盖度": "植被覆盖百分比。软件会先判断是 0~1 还是 0~100 制，再统一换算为百分制。",
    "时间": "记录时刻，统一为 年-月-日 时:分:秒。",
    "气温": "空气温度，单位 ℃。",
    "相对湿度": "空气相对湿度，单位 %。",
    "土壤温度": "土壤温度，单位 ℃。",
    "光合有效辐射": "光合有效辐射，单位 μmol/(m²·s)。",
    "处理组": "实验处理名称，例如对照、增温、增温+降水。",
    "重复": "同一处理下的第几个重复。",
    "株高": "植株高度，单位 cm。",
    "生物量": "生物量，单位 g。",
}
