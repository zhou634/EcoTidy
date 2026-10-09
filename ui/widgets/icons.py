# -*- coding: utf-8 -*-
"""
界面图标：全部用线段绘制，不依赖字体或图片文件

为什么不用 Emoji（实测问题）：
    QString 里的 🗂📐📈 由系统 Emoji 字体渲染，是**彩色**的
    （黄色文件夹、彩色折线），与"白色单色调 + 直角"的界面风格冲突；
    而且不同 Windows 版本渲染差异很大，个别环境还没有该字形。
    因此这里统一用 QPainter 画黑白线稿：风格可控、跨机器一致、无需打包图片。

用法：
    label.setPixmap(icon_pixmap("folder", 48))
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap

# 图标类型
ICON_FOLDER = "folder"      # 数据导入：文件夹
ICON_BROOM = "broom"        # 智能清洗：扫帚（用斜线束表示）
ICON_RULER = "ruler"        # 指标计算：直尺
ICON_CHART = "chart"        # 可视化绘图：柱状图
ICON_DOCUMENT = "document"  # 报告导出：文稿
ICON_HISTORY = "history"    # 历史任务：时钟

ALL_ICONS: Tuple[str, ...] = (ICON_FOLDER, ICON_BROOM, ICON_RULER, ICON_CHART,
                              ICON_DOCUMENT, ICON_HISTORY)

_LINE = QColor("#1F2329")
_ACCENT = QColor("#1F2329")


def icon_pixmap(name: str, size: int = 48, color: Optional[QColor] = None,
                background: Optional[QColor] = None) -> QPixmap:
    """生成指定尺寸的黑白线稿图标。

    参数：
        name       ：ALL_ICONS 之一（未知类型返回一个空方块，便于发现拼写错误）
        size       ：正方形边长（像素）
        color      ：线条颜色，默认界面主色（近黑）
        background ：底色；为空表示透明底
    """
    pen_color = color or _LINE
    pixmap = QPixmap(size, size)
    pixmap.fill(background or QColor(0, 0, 0, 0))

    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 线宽随尺寸等比缩放，小图标也看得清
        pen = QPen(pen_color)
        pen.setWidthF(max(1.0, size / 16.0))
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)   # 直角，与界面一致
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        _draw(painter, name, float(size))
    finally:
        painter.end()
    return pixmap


def _draw(painter: QPainter, name: str, size: float) -> None:
    """按类型分派；所有坐标按 0~100 的比例给出，再映射到实际像素。"""
    unit = size / 100.0

    def rect(x: float, y: float, w: float, h: float) -> QRectF:
        return QRectF(x * unit, y * unit, w * unit, h * unit)

    def line(x1: float, y1: float, x2: float, y2: float) -> None:
        painter.drawLine(QPointF(x1 * unit, y1 * unit), QPointF(x2 * unit, y2 * unit))

    if name == ICON_FOLDER:
        # 文件夹：标签 + 主体，两段直边
        painter.drawPolyline([QPointF(10 * unit, 30 * unit), QPointF(10 * unit, 18 * unit),
                              QPointF(42 * unit, 18 * unit), QPointF(50 * unit, 28 * unit),
                              QPointF(90 * unit, 28 * unit)])
        painter.drawRect(rect(10, 28, 80, 54))

    elif name == ICON_BROOM:
        # 扫帚：斜向手柄 + 刷头（平行线）
        line(72, 10, 40, 52)
        painter.drawRect(rect(24, 52, 32, 30))
        for offset in (8, 16, 24):
            line(24 + offset, 52, 24 + offset, 82)

    elif name == ICON_RULER:
        # 直尺：矩形 + 刻度
        painter.drawRect(rect(8, 34, 84, 32))
        for index, x in enumerate(range(20, 90, 14)):
            height = 14 if index % 2 == 0 else 8
            line(x, 34, x, 34 + height)

    elif name == ICON_CHART:
        # 柱状图：三根等宽柱 + 基线
        painter.drawRect(rect(16, 52, 16, 30))
        painter.drawRect(rect(42, 36, 16, 46))
        painter.drawRect(rect(68, 22, 16, 60))
        line(8, 82, 92, 82)

    elif name == ICON_DOCUMENT:
        # 文稿：页面 + 折角 + 文字线
        painter.drawPolyline([QPointF(24 * unit, 12 * unit), QPointF(62 * unit, 12 * unit),
                              QPointF(78 * unit, 28 * unit), QPointF(78 * unit, 88 * unit),
                              QPointF(24 * unit, 88 * unit), QPointF(24 * unit, 12 * unit)])
        line(62, 12, 62, 28)
        line(62, 28, 78, 28)
        for y in (44, 56, 68):
            line(34, y, 68, y)

    elif name == ICON_HISTORY:
        # 时钟：圆 + 指针（用于历史任务）
        painter.drawEllipse(rect(12, 12, 76, 76))
        line(50, 50, 50, 28)
        line(50, 50, 68, 58)

    else:
        # 未知类型：画一个空方块，便于一眼看出参数写错
        painter.drawRect(rect(15, 15, 70, 70))


def icon_name_for_page(index: int) -> str:
    """第 index 个流程页对应的图标类型。"""
    order: List[str] = [ICON_FOLDER, ICON_BROOM, ICON_RULER, ICON_CHART, ICON_DOCUMENT]
    if 0 <= index < len(order):
        return order[index]
    return ICON_FOLDER
