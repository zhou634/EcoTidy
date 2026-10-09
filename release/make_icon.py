# -*- coding: utf-8 -*-
"""
生成 exe 图标：static/EcoTidy.ico

用法（在项目根目录执行）：
    env\\Scripts\\python.exe release\\make_icon.py

为什么需要这个脚本（而不是直接放一个 .ico 文件）：
    界面图标与应用图标全部是**代码画的**（见 ui/main_window.py 的
    build_app_icon），不依赖字体也不依赖图片文件。图标只有一个来源，
    改绘制逻辑后重跑本脚本即可同步 exe 图标，不会出现
    "界面图标换了、exe 图标还是旧的"这种不一致。

为什么 .ico 里要塞 7 档尺寸：
    Windows 在不同位置用不同尺寸 —— 标题栏 16px、任务栏 32px、
    桌面大图标 256px。只放一个尺寸会被系统拉伸得糊掉。

为什么用几何图形而不是 drawText("E")：
    开发机实测 QFontDatabase.families() 返回 0 个字体，
    drawText 画出来只有一块空白方块。图标是软件门面，不能依赖字体是否装好。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

from ui.main_window import build_app_icon  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)


def main() -> int:
    icon: QIcon = build_app_icon()
    target = ROOT / "static" / "EcoTidy.ico"
    target.parent.mkdir(parents=True, exist_ok=True)

    ok = icon.pixmap(QSize(256, 256)).save(str(target), "ICO")
    if not ok:
        # 退路：手工拼 ICO 容器（PNG 载荷，Vista+ 支持）
        import struct

        from PySide6.QtCore import QBuffer, QByteArray

        payloads = []
        for size in SIZES:
            pixmap = icon.pixmap(QSize(size, size))
            buffer = QBuffer(QByteArray())
            buffer.open(QBuffer.OpenModeFlag.WriteOnly)
            pixmap.save(buffer, "PNG")
            payloads.append((size, bytes(buffer.data())))
            buffer.close()

        header = struct.pack("<HHH", 0, 1, len(payloads))
        entries, blob, offset = b"", b"", 6 + 16 * len(payloads)
        for size, data in payloads:
            entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32,
                                   len(data), offset)
            blob += data
            offset += len(data)
        target.write_bytes(header + entries + blob)

    if not target.exists():
        print("[失败] 未能生成 {0}".format(target))
        return 1

    print("已生成: {0}".format(target))
    print("大小: {0} 字节".format(target.stat().st_size))

    # 校验：重新读回来，确认每个尺寸都在，且黑底白 E 都画出来了
    check = QIcon(str(target))
    print()
    print("=== 各尺寸校验 ===")
    failures = 0
    for size in SIZES:
        image = check.pixmap(QSize(size, size)).toImage()
        white = black = 0
        for y in range(image.height()):
            for x in range(image.width()):
                color = image.pixelColor(x, y)
                if color.alpha() < 40:
                    continue
                if color.red() > 200 and color.green() > 200 and color.blue() > 200:
                    white += 1
                elif color.red() < 60:
                    black += 1
        good = white > 0 and black > 0
        if not good:
            failures += 1
        print("  {0:>3}x{0:<3} 白 {1:>5} 黑 {2:>5}  {3}".format(
            size, white, black, "OK" if good else "异常（缺少黑底或白 E）"))

    if failures:
        print()
        print("[警告] 有 {0} 个尺寸异常".format(failures))
        return 1
    print()
    print("全部尺寸正常。打包时用 --icon \"static\\EcoTidy.ico\" 引用它。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
