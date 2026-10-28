"""把关键状态渲染成一张预览图，方便肉眼确认「感觉对不对」。

上排：呼吸四相位（0s 白/100px → 5s 上升 → 10s 蓝/110px → 15s 回落），光晕 +3px
下排：扇形外框展开态 2 / 4 / 6 / 9 等分，扇区内沿中心圆环弧向排布项目名
      （字顶指向球心）。填充色 = 触发瞬间球心本色：前两列模拟蓝色帧触发，
      后两列模拟浅蓝 / 近白帧触发。

离屏环境字体库是空的，必须先显式加载中文字体文件，否则汉字只会画成方框
（实际运行时窗口在真实图形环境里，字体本来就可用）。

用法
    .venv\\Scripts\\activate
    python tools\\render_preview.py
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QFontDatabase, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import main as M  # noqa: E402

BALL_CELL = M.IDLE_SIZE + 24
RING_CELL = M.ACTIVE_SIZE + 24

# 离屏字体：按优先级尝试，成功加载即可显示真实汉字
FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",     # 微软雅黑（含 Microsoft YaHei UI 家族）
    r"C:\Windows\Fonts\simhei.ttf",   # 黑体
    r"C:\Windows\Fonts\simsun.ttc",   # 宋体
)


def ensure_fonts() -> bool:
    for path in FONT_CANDIDATES:
        if os.path.isfile(path):
            fid = QFontDatabase.addApplicationFont(path)
            if fid >= 0 and QFontDatabase.applicationFontFamilies(fid):
                return True
    return False


def draw(phase: float, n_sectors: int | None, highlight: int = -1,
         fill_phase: float | None = None) -> QImage:
    """n_sectors=None 画收起态小球；否则画展开态（球 + 环 + 弧向项目名）。"""
    size = M.IDLE_SIZE if n_sectors is None else M.ACTIVE_SIZE
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    M.paint_ball(painter, size, phase)
    if n_sectors is not None:
        names = ["关闭夸克", "明日方舟", "MAA助手", "天气", "时钟",
                 "音乐", "截屏", "翻译", "计算器"]
        sectors = [{"name": names[i % len(names)], "code": "x", "placeholder": False}
                   for i in range(n_sectors)]
        fill = M.center_color_at(fill_phase if fill_phase is not None else phase)
        M.paint_ring(painter, size, sectors, 1.0, highlight, fill)
    painter.end()
    return img.convertToFormat(QImage.Format.Format_ARGB32)


def build_row(images: list[QImage], cell: int, bg: QColor) -> QImage:
    row = QImage(cell * len(images), cell, QImage.Format.Format_ARGB32_Premultiplied)
    row.fill(bg)
    painter = QPainter(row)
    for i, img in enumerate(images):
        x0 = i * cell + (cell - img.width()) // 2
        y0 = (cell - img.height()) // 2
        painter.drawImage(x0, y0, img)
    painter.end()
    return row


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    if not ensure_fonts():
        print("[警告] 未找到可用中文字体，预览中的项目名会显示为方框")

    ball_row = build_row([draw(p, None) for p in (0.0, 0.25, 0.5, 0.75)],
                         BALL_CELL, QColor(24, 26, 32))
    ring_row = build_row([
        draw(0.5, 2, 0, 0.5),
        draw(0.5, 4, 1, 0.5),
        draw(0.2, 6, 2, 0.2),
        draw(0.1, 9, 4, 0.1),
    ], RING_CELL, QColor(24, 26, 32))

    out = QImage(max(ball_row.width(), ring_row.width()),
                 ball_row.height() + ring_row.height() + 10,
                 QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(QColor(12, 12, 14))
    painter = QPainter(out)
    painter.drawImage(0, 0, ball_row)
    painter.drawImage(0, ball_row.height() + 10, ring_row)
    painter.end()

    dest = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "preview_breath.png")
    if not out.save(dest, "PNG"):
        print("保存失败")
        return 1
    print(f"{dest}\n尺寸 {out.width()}x{out.height()}px")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
