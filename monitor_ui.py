# -*- coding: utf-8 -*-
"""资源监视 UI —— 阶段 1：契约骨架 + 单元渲染与动态布局。

形态（已拍板）：N 个磁吸迷你窗，每指标单元一个顶层 QWidget。
本模块禁止 import main（main 反向 import 本模块，会循环依赖）。
共用能力一律构造注入：hub / scale_getter / place_fn / theme /
topmost_getter / log_fn / config_io（manager 另持 config_io）。

阶段进度：
  - 阶段 0：UnitRow 行模型、空壳契约、monitor 配置块默认结构
  - 阶段 1：MonitorUnitWidget 三列内容驱动渲染 + 边缘虚化光晕 + 动态行高
  - 阶段 2~5：数据可视化 / 磁吸 / 解绑收口 / 集成接线（方法留 NotImplementedError）

后端契约（monitor_hub.py，只读消费）：
  - 每 tick 只调一次 snapshot()，全单元共享同一帧；禁止 get(key)
  - list_disks 必须传入已取帧复用
  - 格式化一律 MetricsHub.fmt / uptime_fmt，UI 不自造换算
  - UI 不自建采样线程/子进程，只做定时拉取；hub.start()/stop() 归 main.py（阶段 5）
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget

import monitor_hub as mh

# ---------------------------------------------------------------------------
# 注入契约（阶段 0 定死，后续阶段只消费）
# ---------------------------------------------------------------------------
# scale_getter() -> float            显示缩放 0.5~2.0，只乘绝对像素量
# place_fn(widget, anchor_rect)      弹窗智能定位（相对球体选空间大侧）
# theme                              dict：呼吸三色 + system_dark_mode 判定
# topmost_getter() -> bool           置顶开关（window.always_on_top）
# log_fn(where, exc)                 崩溃日志（main._log_crash 签名）
# config_io                          monitor 配置块读写回调（UI 不碰 yaml 路径）

ScaleGetter = Callable[[], float]
PlaceFn = Callable[..., None]
TopmostGetter = Callable[[], bool]
LogFn = Callable[[str, BaseException], None]

# ---------------------------------------------------------------------------
# UI 行模型
# ---------------------------------------------------------------------------
SPARKLINE_POINTS = 60  # sparkline 点数 ≈ 1 分钟（侧车 ~1.1s/帧）

# ---------------------------------------------------------------------------
# 阶段 1 渲染常量（均为绝对像素量，渲染时乘 scale_getter()；
# 行为参数——吸附阈值/拖动判定/时间常量——不在此列、不乘缩放）
# ---------------------------------------------------------------------------
FONT_PX = 13          # 基础字号
PAD_X = 10            # 左右内边距
PAD_Y = 8             # 上下内边距
COL_GAP = 8           # 列间距
ROW_GAP = 4           # 行间距
CORNER_PX = 8         # 圆角
HALO_PX = 4           # 光晕退晕宽（规格 3~5px）
HALO_ALPHA = 0.15     # 光晕主题色透明度 15%
BODY_ALPHA = 0.82     # 本体底色透明度（保证文字可读，无深色收边）
SPARK_H = 18          # sparkline 行下方预留高（阶段 2 画折线）
FONT_FAMILY = "Microsoft YaHei"


def halo_color(theme: dict) -> QColor:
    """光晕/本体基色 = 主题过渡色（呼吸三色之中色）。"""
    c = QColor(str(theme.get("color_mid", "#283FFE")))
    return c if c.isValid() else QColor("#283FFE")


def body_color(theme: dict) -> QColor:
    """本体底色随系统深浅色：深色→深底、浅色→浅底（禁深色收边）。"""
    return QColor(24, 26, 32) if theme.get("system_dark_mode") else QColor(250, 250, 252)


def text_color(theme: dict) -> QColor:
    """前景文字色随系统深浅色（浅色系统下白字不可见）。"""
    return QColor(235, 238, 245) if theme.get("system_dark_mode") else QColor(30, 32, 40)


@dataclass
class UnitRow:
    """一行指标。label 取自 Metric.label，不自造名称。

    history：每 tick append 一次；None=无读数（sparkline 该点断线）。
    """
    key: str
    label: str
    value: Optional[float]
    unit: str
    status: str
    history: deque = field(
        default_factory=lambda: deque(maxlen=SPARKLINE_POINTS))

    @property
    def available(self) -> bool:
        return self.status == mh.STATUS_OK and self.value is not None


# ---------------------------------------------------------------------------
# monitor 配置块默认结构（阶段 5 四函数同步用；每次调用返回全新副本）
# ---------------------------------------------------------------------------
def default_monitor_config() -> dict:
    """monitor 块出厂默认。

    refresh_ms      拉取周期（匹配侧车 ~1.1s/帧）
    sparkline_points 历史点数（60 ≈ 1 分钟）
    idle_stop_sec   连续无监视窗多少秒后停采（0=永不停采）
    units           {key: {enabled, threshold:[{min,max,color}...]}}
    layout          {unit_key: {x, y, group_id}}（位置与吸附组记忆）
    """
    return {
        "refresh_ms": 1000,
        "sparkline_points": SPARKLINE_POINTS,
        "idle_stop_sec": 300,
        "units": {key: {"enabled": True, "threshold": []}
                  for key, _label, _group, _unit in mh.METRIC_DEFS},
        "layout": {},
    }


# ---------------------------------------------------------------------------
# 单元窗（阶段 1 已实现渲染与布局；阶段 2~4 继续填充）
# ---------------------------------------------------------------------------
class MonitorUnitWidget(QWidget):
    """单个指标单元 = 一个顶层迷你窗。

    窗口标志：FramelessWindowHint | Tool（Tool 防任务栏按钮）、
    WA_TranslucentBackground + WA_NoSystemBackground、置顶随 topmost_getter()。
    尺寸内容驱动：宽 = 三列内容最大宽 + padding，高 = 文本行 + sparkline + padding；
    所有绝对像素量渲染时现查 scale_getter()，不缓存成第二真源。
    """

    def __init__(self, unit_key: str, hub,
                 scale_getter: ScaleGetter,
                 place_fn: PlaceFn,
                 theme: dict,
                 topmost_getter: TopmostGetter,
                 log_fn: LogFn,
                 manager: "MonitorWindowManager"):
        super().__init__()
        self.unit_key = unit_key
        self.hub = hub
        self.scale_getter = scale_getter
        self.place_fn = place_fn
        self.theme = theme
        self.topmost_getter = topmost_getter
        self.log_fn = log_fn
        self.manager = manager
        # 状态机占位（阶段 3/4 消费）
        self._group = None            # 所属 SnapGroup；None=独立
        self._is_snapped = False      # 吸附态（关光晕、画融合虚线）
        self._preview_target = None   # 移动中预览目标（窗/屏幕边）
        self._preview_edge = None     # 预览贴合边
        self._rows: list[UnitRow] = []
        # 布局几何缓存（update_layout 重算，paintEvent 消费；缩放每次现查）
        self._col_w = (0, 0, 0)       # A 名称 / B 数值 / C 单位 列宽
        self._row_h = 0               # 单行文本高
        self._geo_scale = 1.0         # 本次布局所用缩放（paint 时校验是否需重算）
        self._last_h = 0              # 上次窗高（变高方向同帧 repaint 防脏区，坑 53）
        # 窗口形态（全局约束第 3 条）
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.apply_topmost()

    # ---- 窗口形态 ----
    def apply_topmost(self) -> None:
        """置顶按注入 topmost_getter() 决定，不得写死。"""
        try:
            flags = self.windowFlags()
            if self.topmost_getter():
                flags |= Qt.WindowType.WindowStaysOnTopHint
            else:
                flags &= ~Qt.WindowType.WindowStaysOnTopHint
            self.setWindowFlags(flags)
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.apply_topmost", e)

    def show_at(self, anchor_rect) -> None:
        """首次显示：先定位（place_fn）再 show；尺寸已在构造/布局末尾 adjustSize。"""
        try:
            self.place_fn(self, anchor_rect)
            self.show()
            self.raise_()
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.show_at", e)

    # ---- 阶段 1：渲染与布局 ----
    def _font(self) -> QFont:
        f = QFont(FONT_FAMILY)
        f.setPixelSize(max(1, round(FONT_PX * self.scale_getter())))
        return f

    def update_layout(self) -> None:
        """三列内容驱动布局：QFontMetrics 量各列最大宽，总宽由此推导。

        宽 = max(label) + max(value) + max(unit) + 2*列间距 + 2*padding（乘缩放）；
        高 = 行数*行高 + 行间距 + sparkline 预留 + 2*padding（乘缩放）。
        不写死尺寸；末尾 adjustSize()（Qt 未 show 读尺寸是假 640x480，坑 50）。
        """
        try:
            s = self.scale_getter()
            fm = QFontMetrics(self._font())
            rows = self._rows
            # 各列内容最大宽（B 列数值用 fmt 渲染文本量宽，含状态文案）
            w_label = max((fm.horizontalAdvance(r.label) for r in rows), default=0)
            w_value = max((fm.horizontalAdvance(self._value_text(r)) for r in rows),
                          default=0)
            w_unit = max((fm.horizontalAdvance(r.unit) for r in rows), default=0)
            gap = round(COL_GAP * s)
            pad_x = round(PAD_X * s)
            pad_y = round(PAD_Y * s)
            self._col_w = (w_label, w_value, w_unit)
            self._row_h = fm.height()
            self._geo_scale = s
            n = max(1, len(rows))
            row_gap = round(ROW_GAP * s)
            spark_h = round(SPARK_H * s)
            total_w = w_label + w_value + w_unit + 2 * gap + 2 * pad_x
            total_h = (n * self._row_h + (n - 1) * row_gap
                       + spark_h + 2 * pad_y)
            self.setFixedSize(total_w, total_h)
            self.adjustSize()
            # 变高方向同帧 repaint 防露脏区（坑 53）
            if total_h > self._last_h:
                self.repaint()
            self._last_h = total_h
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.update_layout", e)

    def _value_text(self, row: UnitRow) -> str:
        """B 列数值文本：格式化一律走 hub.fmt（不自造换算）。

        阶段 1 仅用 fmt 的数值/状态文案；五态灰显与状态小字在阶段 2 细化。
        """
        m = mh.Metric(key=row.key, label=row.label, group="",
                      value=row.value, unit=row.unit, status=row.status)
        return mh.MetricsHub.fmt(m)

    def set_rows(self, rows: list) -> None:
        """动态行增删（多盘/风扇）：替换行集 → 重算高度 → adjustSize。"""
        try:
            self._rows = list(rows)
            self.update_layout()
            self.update()
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.set_rows", e)

    def paintEvent(self, event) -> None:  # noqa: N802
        try:
            # 缩放每次现查：与布局缓存不一致先重算（不缓存第二真源）
            if abs(self.scale_getter() - self._geo_scale) > 1e-6:
                self.update_layout()
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            s = self.scale_getter()
            rect = QRectF(self.rect())
            halo = round(HALO_PX * s)
            corner = CORNER_PX * s
            # 本体区 = 窗体内缩光晕宽（光晕画在本体外围，退晕虚化）
            body = rect.adjusted(halo, halo, -halo, -halo)
            if not self._is_snapped:
                self._paint_halo(p, rect, body, halo, corner, s)
            # 本体底色（圆角，无深色收边/硬边框/阴影）
            bc = body_color(self.theme)
            bc.setAlphaF(BODY_ALPHA)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bc)
            p.drawRoundedRect(body, corner, corner)
            self._paint_rows(p, body, s)
            p.end()
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.paintEvent", e)

    def _paint_halo(self, p: QPainter, rect: QRectF, body: QRectF,
                    halo: int, corner: float, s: float) -> None:
        """独立态光晕：主题色 15% 多层 alpha 退晕（3~5px×缩放）。

        用多层圆角环由外向内渐隐模拟虚化（QPainter 无高斯模糊 Shader，
        不引入 QML/OpenGL）；禁止深色收边。吸附态由调用方关闭。
        """
        base = halo_color(self.theme)
        layers = max(2, halo)
        for i in range(layers):
            # 由外向内：越靠外越透明，最内层接近 HALO_ALPHA
            t = (i + 1) / layers
            a = HALO_ALPHA * (t ** 2)
            c = QColor(base)
            c.setAlphaF(a)
            inset = halo * (1 - t)
            r = rect.adjusted(inset, inset, -inset, -inset)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(r, corner + inset, corner + inset)

    def _paint_rows(self, p: QPainter, body: QRectF, s: float) -> None:
        """三列文本：A 名称左对齐 / B 数值右对齐 / C 单位左对齐，垂直居中。"""
        pad_x = round(PAD_X * s)
        pad_y = round(PAD_Y * s)
        gap = round(COL_GAP * s)
        row_gap = round(ROW_GAP * s)
        w_label, w_value, w_unit = self._col_w
        x_label = body.left() + pad_x
        x_value = x_label + w_label + gap          # B 列左界
        x_unit = x_value + w_value + gap           # C 列左界
        p.setFont(self._font())
        tc = text_color(self.theme)
        y = body.top() + pad_y
        for row in self._rows:
            row_rect = QRectF(x_label, y, body.width() - 2 * pad_x, self._row_h)
            # A 列：名称（左对齐，垂直居中）
            p.setPen(tc)
            p.drawText(QRectF(x_label, y, w_label, self._row_h),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       row.label)
            # B 列：数值（右对齐）；不可用灰显（阶段 2 补状态小字）
            vc = QColor(tc)
            if not row.available:
                vc.setAlphaF(0.45)
            p.setPen(vc)
            p.drawText(QRectF(x_value, y, w_value, self._row_h),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       self._value_text(row))
            # C 列：单位（左对齐）
            p.setPen(tc)
            p.drawText(QRectF(x_unit, y, w_unit, self._row_h),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       row.unit)
            y += self._row_h + row_gap
        # sparkline 区域（阶段 2 画折线）：此处仅预留，不绘制

    # ---- 阶段 2：数据可视化 ----
    def update_data(self, metric) -> None:
        raise NotImplementedError("阶段 2：五态驱动与 sparkline")

    # ---- 阶段 4：测试钩子（保留为公开方法）----
    def simulate_data_update(self, key: str, value, status: str) -> None:
        raise NotImplementedError("阶段 4：无后端演示钩子")


# ---------------------------------------------------------------------------
# 窗口管理器骨架（阶段 2~5 填充）
# ---------------------------------------------------------------------------
class MonitorWindowManager:
    """统一持有全部 SnapGroup 与单元窗生命周期。

    普通 Python 类（不用 QObject 父子窗口，避免 Z 序问题）。
    hub 生命周期归 main.py（阶段 5），本类不调 start()/stop()。
    """

    def __init__(self, hub,
                 scale_getter: ScaleGetter,
                 place_fn: PlaceFn,
                 theme: dict,
                 topmost_getter: TopmostGetter,
                 log_fn: LogFn,
                 config_io):
        self.hub = hub
        self.scale_getter = scale_getter
        self.place_fn = place_fn
        self.theme = theme
        self.topmost_getter = topmost_getter
        self.log_fn = log_fn
        self.config_io = config_io
        self._groups: list = []       # SnapGroup 列表（阶段 3）
        self._widgets: dict = {}      # unit_key -> MonitorUnitWidget
        self._timer = None            # 拉取 QTimer（阶段 2）

    # ---- 阶段 2：拉取节奏 ----
    def tick(self) -> None:
        raise NotImplementedError("阶段 2：每 tick 一次 snapshot 全单元共享")

    # ---- 阶段 3：磁吸 ----
    def snap_groups(self) -> list:
        raise NotImplementedError("阶段 3：吸附组管理")

    # ---- 阶段 4：测试钩子（保留为公开方法）----
    def simulate_drag(self, widget, dx: float, dy: float) -> None:
        raise NotImplementedError("阶段 4：离屏拖动演示钩子")
