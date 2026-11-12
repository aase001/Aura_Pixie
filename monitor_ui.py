# -*- coding: utf-8 -*-
"""资源监视 UI —— 阶段 4：契约骨架 + 渲染 + 数据可视化 + 磁吸 + 解绑收口。

形态（已拍板）：N 个磁吸迷你窗，每指标单元一个顶层 QWidget。
本模块禁止 import main（main 反向 import 本模块，会循环依赖）。
共用能力一律构造注入：hub / scale_getter / place_fn / theme /
topmost_getter / log_fn / config_io（manager 另持 config_io）。

阶段进度：
  - 阶段 0：UnitRow 行模型、空壳契约、monitor 配置块默认结构
  - 阶段 1：MonitorUnitWidget 三列内容驱动渲染 + 边缘虚化光晕 + 动态行高
  - 阶段 2：五态驱动视觉 + 语义色阈值插值 + 高负载呼吸 + sparkline + 拉取节奏
  - 阶段 3：SnapGroup + 拖动跟手 + 幽灵轮廓预览 + 松手 280ms 缓入缓出吸附
  - 阶段 4：右键拖拽解绑 + 组解散复位 + 最小菜单 + 四层残留清扫 + 可见性联动
  - 阶段 5：集成接线（方法留 NotImplementedError）

后端契约（monitor_hub.py，只读消费）：
  - 每 tick 只调一次 snapshot()，全单元共享同一帧；禁止 get(key)
  - list_disks 必须传入已取帧复用
  - 格式化一律 MetricsHub.fmt / uptime_fmt，UI 不自造换算
  - UI 不自建采样线程/子进程，只做定时拉取；hub.start()/stop() 归 main.py（阶段 5）
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

from PySide6.QtCore import (QElapsedTimer, QEvent, QPoint, QPointF, QRect, QRectF,
                            Qt, QTimer)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication, QPainter,
                           QPainterPath, QPen)
from PySide6.QtWidgets import QMenu, QWidget

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

# ---------------------------------------------------------------------------
# 阶段 2 数据可视化常量
# ---------------------------------------------------------------------------
SPARK_LINE_PX = 1     # sparkline 线宽（乘缩放）
BREATH_HZ = 1.0       # 高负载呼吸频率 1Hz
BREATH_ALPHA_MIN = 0.3
BREATH_ALPHA_MAX = 1.0
STATUS_TEXT = {
    mh.STATUS_NEED_ADMIN: "需管理员/驱动",
    mh.STATUS_UNSUPPORTED: "不可用",
    mh.STATUS_NOT_INSTALLED: "采集器缺失",
    mh.STATUS_STALE: "断流",
}
# 语义色三档（绿/黄/红）
SEM_GREEN = "#3FB950"
SEM_YELLOW = "#D29922"
SEM_RED = "#F85149"

# ---------------------------------------------------------------------------
# 阶段 3 磁吸行为参数（行为参数，不乘缩放）
# ---------------------------------------------------------------------------
DRAG_THRESHOLD_PX = 4     # 拖动判定阈值
SNAP_PREVIEW_PX = 20      # 移动中预览阈值（<20px 出幽灵轮廓）
SNAP_STRONG_PX = 10       # 强预览档（<10px 视觉更强）
SNAP_ANIM_MS = 280        # 松手吸附缓入缓出时长
FUSE_DASH_PX = 1          # 融合虚线线宽（乘缩放）


def fuse_color(theme: dict) -> QColor:
    """融合虚线/幽灵轮廓颜色随系统深浅色（浅色系统用深色 20%）。"""
    if theme.get("system_dark_mode"):
        return QColor(235, 238, 245, int(255 * 0.5))
    return QColor(30, 32, 40, int(255 * 0.2))


class SnapGroup:
    """吸附组：普通 Python 类（members 列表 + group_id）。

    不用 QObject 父子窗口（避免 Z 序问题）；由 MonitorWindowManager 统一持有。
    """

    def __init__(self, group_id: str):
        self.group_id = group_id
        self.members: list = []

    def add(self, widget) -> None:
        if widget not in self.members:
            self.members.append(widget)
        widget._group = self

    def remove(self, widget) -> None:
        if widget in self.members:
            self.members.remove(widget)
        if widget._group is self:
            widget._group = None

    def bounding_rect(self) -> QRect:
        """组包围盒（成员 frameGeometry 并集）。"""
        rect = QRect()
        for w in self.members:
            rect = rect.united(w.frameGeometry())
        return rect


class GhostOverlay(QWidget):
    """幽灵轮廓：独立透明顶层窗，画目标贴合位置的虚线框。

    Frameless|Tool|透明、WA_TransparentForMouseEvents 鼠标穿透。
    顶层窗 paintEvent 画不出自身边界外，故用独立 overlay 而非在单元窗上画别处。
    """

    def __init__(self, theme: dict, scale_getter: ScaleGetter):
        super().__init__()
        self.theme = theme
        self.scale_getter = scale_getter
        self._strong = False
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

    def show_at(self, rect: QRect, strong: bool) -> None:
        self._strong = strong
        self.setGeometry(rect)
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = fuse_color(self.theme)
        # 两档视觉强弱：<10px 强（更实），10~20px 弱（更虚）
        alpha = 0.85 if self._strong else 0.45
        c.setAlphaF(alpha)
        pen = QPen(c, max(1.0, FUSE_DASH_PX * self.scale_getter()))
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.drawRoundedRect(r, CORNER_PX * self.scale_getter(),
                          CORNER_PX * self.scale_getter())
        p.end()


def default_thresholds(key: str, unit: str) -> list:
    """按指标类别给默认阈值段（禁止全局写死 90%）。

    百分比类 0~60 绿 / 60~85 黄 / 85~100 红；温度/转速/网速/频率/功耗/用量
    各自默认段。段格式 {min, max, color}，段间线性插值（get_color_for_value）。
    """
    if unit == "%":
        return [{"min": 0, "max": 60, "color": SEM_GREEN},
                {"min": 60, "max": 85, "color": SEM_YELLOW},
                {"min": 85, "max": 100, "color": SEM_RED}]
    if unit == "℃":
        return [{"min": 0, "max": 60, "color": SEM_GREEN},
                {"min": 60, "max": 80, "color": SEM_YELLOW},
                {"min": 80, "max": 110, "color": SEM_RED}]
    if unit == "RPM":
        return [{"min": 0, "max": 2000, "color": SEM_GREEN},
                {"min": 2000, "max": 3500, "color": SEM_YELLOW},
                {"min": 3500, "max": 6000, "color": SEM_RED}]
    if unit == "MB/s":
        return [{"min": 0, "max": 10, "color": SEM_GREEN},
                {"min": 10, "max": 50, "color": SEM_YELLOW},
                {"min": 50, "max": 200, "color": SEM_RED}]
    if unit == "GHz":
        return [{"min": 0, "max": 3.0, "color": SEM_GREEN},
                {"min": 3.0, "max": 4.2, "color": SEM_YELLOW},
                {"min": 4.2, "max": 5.5, "color": SEM_RED}]
    if unit == "W":
        return [{"min": 0, "max": 35, "color": SEM_GREEN},
                {"min": 35, "max": 80, "color": SEM_YELLOW},
                {"min": 80, "max": 150, "color": SEM_RED}]
    if unit == "GB":
        return [{"min": 0, "max": 8, "color": SEM_GREEN},
                {"min": 8, "max": 16, "color": SEM_YELLOW},
                {"min": 16, "max": 32, "color": SEM_RED}]
    # sys.uptime（s）等无负载语义 → 单段中性色，不触发高负载呼吸
    return [{"min": 0, "max": 1e12, "color": SEM_GREEN}]


def get_color_for_value(value: Optional[float], thresholds: list) -> QColor:
    """按阈值段取语义色，段间线性插值。

    值落在某段内 → 在该段 [min,max] 上从段色向下一段色插值；
    最后一段（最高段）恒段色。低于首段 min → 首段色；高于末段 max → 末段色。
    value=None → 中性灰（调用方按五态另行灰显）。
    """
    if not thresholds:
        return QColor(SEM_GREEN)
    if value is None:
        return QColor(150, 150, 150)
    # 定位所在段
    idx = 0
    for i, seg in enumerate(thresholds):
        if value >= seg["min"]:
            idx = i
    seg = thresholds[idx]
    c0 = QColor(seg["color"])
    if idx + 1 < len(thresholds):
        nxt = thresholds[idx + 1]
        span = seg["max"] - seg["min"]
        t = 0.0 if span <= 0 else (value - seg["min"]) / span
        t = max(0.0, min(1.0, t))
        c1 = QColor(nxt["color"])
        return QColor(
            round(c0.red() + (c1.red() - c0.red()) * t),
            round(c0.green() + (c1.green() - c0.green()) * t),
            round(c0.blue() + (c1.blue() - c0.blue()) * t))
    return c0


def in_top_segment(value: Optional[float], thresholds: list) -> bool:
    """值是否进入该指标最高阈值段（触发高负载呼吸）。"""
    if value is None or not thresholds:
        return False
    top = thresholds[-1]
    return value >= top["min"]


def breath_alpha(elapsed_ms: float) -> float:
    """高负载呼吸 alpha：1Hz 正弦 0.3↔1.0，相位取自真实经过时间。"""
    phase = 2.0 * math.pi * BREATH_HZ * (elapsed_ms / 1000.0)
    mid = (BREATH_ALPHA_MIN + BREATH_ALPHA_MAX) / 2.0
    amp = (BREATH_ALPHA_MAX - BREATH_ALPHA_MIN) / 2.0
    return mid + amp * math.sin(phase)


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
    """monitor 块出厂默认（阶段 A：参数键化 + 总开关 + 默认全不勾选）。

    enabled         监视总开关（默认 False；勾选并保存才拉起采集模块）
    refresh_ms      拉取周期（匹配侧车 ~1.1s/帧）
    sparkline_points 历史点数（60 ≈ 1 分钟）
    idle_stop_sec   连续无监视窗多少秒后停采（0=永不停采）
    units           {参数key: {enabled, threshold:[{min,max,color}...]}}
                    固定 14 项 + 风扇三槽位 + 硬盘0 兜底项；默认全不勾选。
                    动态硬盘键（disk.1+）运行时按实例展开，勾选状态落盘保留。
    layout          {参数key: {x, y, group_id}}（位置与吸附组记忆）
    """
    units = {key: {"enabled": False, "threshold": []}
             for key, _label, _group, _unit in mh.METRIC_DEFS}
    for key in ("fan.cpu.0", "fan.gpu_d.0", "fan.mobo.0",
                "disk.0.temp", "disk.0.usage"):
        units[key] = {"enabled": False, "threshold": []}
    return {
        "enabled": False,
        "refresh_ms": 1000,
        "sparkline_points": SPARKLINE_POINTS,
        "idle_stop_sec": 300,
        "units": units,
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
        # 状态机（阶段 3 磁吸 / 阶段 4 解绑消费）
        self._group = None            # 所属 SnapGroup；None=独立
        self._is_snapped = False      # 吸附态（关光晕、画融合虚线）
        self._preview_target = None   # 移动中预览目标（widget 或 "screen"）
        self._preview_edge = None     # 预览贴合边（left/right/top/bottom）
        self._preview_rect = None     # 预览贴合位（QRect，幽灵轮廓画此处）
        self._preview_strong = False  # <10px 强预览档
        self._screen_edge = None      # 屏幕边吸附记录（不入窗间组）
        self._contact_edges: list = []  # 吸附接触边（画融合虚线）
        self._rows: list[UnitRow] = []
        # 拖动状态（阶段 3）
        self._dragging = False
        self._drag_started = False    # 位移超阈值才成立
        self._grab_offset = QPoint()
        self._press_global = QPoint()
        self._drag_members: list = []  # 组内同步偏移的成员
        # 松手吸附动画（QTimer 相位法，280ms 缓入缓出）
        self._snap_anim_timer = QTimer(self)
        self._snap_anim_timer.setInterval(16)
        self._snap_anim_timer.timeout.connect(self._on_snap_anim)
        self._snap_clock = QElapsedTimer()
        self._snap_from = QPoint()
        self._snap_to = QPoint()
        self._snap_animating = False
        self._snap_pending = None     # 落位后入组目标（widget 或 "screen"）
        # 阶段 4：右键拖动解绑 / 轻点菜单
        self._right_dragging = False
        self._right_drag_started = False
        self._right_press_global = QPoint()
        self._settings_callback: Optional[Callable] = None  # 打开设置（注入）
        # 布局几何缓存（update_layout 重算，paintEvent 消费；缩放每次现查）
        self._col_w = (0, 0, 0)       # A 名称 / B 数值 / C 单位 列宽
        self._row_h = 0               # 单行文本高
        self._geo_scale = 1.0         # 本次布局所用缩放（paint 时校验是否需重算）
        self._last_h = 0              # 上次窗高（变高方向同帧 repaint 防脏区，坑 53）
        # 阶段 2：语义色阈值（可注入；空→按指标类别默认段）
        self._thresholds: dict = {}   # key -> [{min,max,color}...]
        # 阶段 2：高负载呼吸（相位取真实经过时间，QElapsedTimer）
        self._breath_clock = QElapsedTimer()
        self._breathing = False
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(33)   # ~30FPS 重绘驱动呼吸
        self._anim_timer.timeout.connect(self._on_anim_tick)
        # 窗口形态（全局约束第 3 条）
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.apply_topmost()

    def _on_anim_tick(self) -> None:
        try:
            self.update()
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui._on_anim_tick", e)

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
            # 状态小字行高（五态非 ok 时在数值行下方显示，阶段 2）
            small = QFont(self._font())
            small.setPixelSize(max(1, round(FONT_PX * 0.72 * s)))
            self._status_h = QFontMetrics(small).height()
            self._geo_scale = s
            n = max(1, len(rows))
            row_gap = round(ROW_GAP * s)
            spark_h = round(SPARK_H * s)
            total_w = w_label + w_value + w_unit + 2 * gap + 2 * pad_x
            total_h = (n * (self._row_h + self._status_h) + (n - 1) * row_gap
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
        """B 列数值文本：纯数值（不带单位），走 hub.fmt_value（不自造换算）。

        单位独立画在 C 列；无读数显示 "--"，状态文案走数值行下方状态小字。
        （修复：旧实现用 fmt 带单位串，B 列宽按初始 "--" 量宽后不再重算，
        真实数据右对齐被裁剪只剩尾部单位——数值消失根因。）
        """
        m = mh.Metric(key=row.key, label=row.label, group="",
                      value=row.value, unit=row.unit, status=row.status)
        return mh.MetricsHub.fmt_value(m)

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
            if self._is_snapped:
                self._paint_fuse(p, s)
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
        """三列文本 + 状态小字：A 名称左 / B 数值右 / C 单位左，垂直居中。

        可用行数值着语义色；最高阈值段触发高负载呼吸（前景 alpha 1Hz 正弦）。
        不可用行灰显 + 状态小字（need_admin/stale/unsupported/not_installed）。
        """
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
        small = QFont(self._font())
        small.setPixelSize(max(1, round(FONT_PX * 0.72 * s)))
        y = body.top() + pad_y
        for row in self._rows:
            # A 列：名称（左对齐，垂直居中）
            p.setFont(self._font())
            p.setPen(tc)
            p.drawText(QRectF(x_label, y, w_label, self._row_h),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       row.label)
            # B 列：数值（右对齐）——可用着语义色 + 呼吸；不可用灰显
            vc = self._semantic_color(row)
            if row.available:
                alpha = self._row_alpha(row)
                vc.setAlphaF(alpha)
            else:
                vc = QColor(tc)
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
            # 状态小字（数值行下方，仅非 ok 显示）
            st_text = STATUS_TEXT.get(row.status, "")
            if not row.available and st_text:
                p.setFont(small)
                sc = QColor(tc)
                sc.setAlphaF(0.6)
                p.setPen(sc)
                p.drawText(QRectF(x_value, y + self._row_h,
                                  w_value + gap + w_unit, self._status_h),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                           st_text)
            y += self._row_h + self._status_h + row_gap
        # sparkline 区域：文本行下方，每行一条折线
        self._paint_sparklines(p, body, s)

    def _paint_sparklines(self, p: QPainter, body: QRectF, s: float) -> None:
        """文本行下方画 QPainterPath 折线：线宽 1px×缩放、当前语义色、
        不填充不画轴、None 点断开。窗口小，全量 update 即可。"""
        pad_x = round(PAD_X * s)
        pad_y = round(PAD_Y * s)
        row_gap = round(ROW_GAP * s)
        spark_h = round(SPARK_H * s)
        w_label, w_value, w_unit = self._col_w
        gap = round(COL_GAP * s)
        x0 = body.left() + pad_x
        full_w = w_label + w_value + w_unit + 2 * gap
        # sparkline 区起点 = 所有文本行（含状态小字）之后
        n = max(1, len(self._rows))
        y_base = (body.top() + pad_y
                  + n * (self._row_h + self._status_h) + (n - 1) * row_gap)
        pen_w = max(1.0, SPARK_LINE_PX * s)
        for i, row in enumerate(self._rows):
            # 每行 sparkline 占 spark_h/n 高，水平铺满内容宽
            band_h = spark_h / n
            top = y_base + i * band_h
            hist = list(row.history)
            if len(hist) < 2:
                continue
            color = self._semantic_color(row)
            if row.available:
                color.setAlphaF(self._row_alpha(row))
            else:
                color = QColor(text_color(self.theme))
                color.setAlphaF(0.35)
            pen = QPen(color, pen_w)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            # 归一化：用历史内 min/max（忽略 None）；全 None 不画
            vals = [v for v in hist if v is not None]
            if not vals:
                continue
            lo, hi = min(vals), max(vals)
            span = (hi - lo) or 1.0
            path = QPainterPath()
            started = False
            for j, v in enumerate(hist):
                x = x0 + full_w * (j / (len(hist) - 1))
                if v is None:
                    started = False   # None 点断开
                    continue
                yy = top + band_h * (1.0 - (v - lo) / span)
                if not started:
                    path.moveTo(x, yy)
                    started = True
                else:
                    path.lineTo(x, yy)
            p.drawPath(path)

    # ---- 阶段 2：数据可视化 ----
    def set_thresholds(self, thresholds: dict) -> None:
        """注入指标阈值段 {key: [{min,max,color}...]}（阶段 0 配置 threshold）。"""
        self._thresholds = dict(thresholds or {})

    def thresholds_for(self, row: UnitRow) -> list:
        segs = self._thresholds.get(row.key)
        if segs:
            return segs
        return default_thresholds(row.key, row.unit)

    def _semantic_color(self, row: UnitRow) -> QColor:
        return get_color_for_value(row.value, self.thresholds_for(row))

    def _row_alpha(self, row: UnitRow) -> float:
        """高负载呼吸：进入最高阈值段 → 1Hz 正弦 0.3↔1.0；否则恒 1.0。

        只影响前景（数值/sparkline），不影响背景。离开阈值段立即恢复。
        """
        if in_top_segment(row.value, self.thresholds_for(row)):
            return breath_alpha(self._breath_clock.elapsed())
        return 1.0

    def update_data(self, metric) -> None:
        """接 hub 真实数据：更新对应行值/状态，history append（None 断线）。

        五态：value=None 或 status!=ok → 灰显 + 状态小字，sparkline 记 None。
        高负载呼吸由 _breathing 聚合（任一行进入最高段即启动动画定时器）。
        """
        try:
            if metric is None:
                return
            row = next((r for r in self._rows if r.key == metric.key), None)
            if row is None:
                return
            row.value = metric.value
            row.status = metric.status
            row.label = metric.label or row.label
            row.unit = metric.unit or row.unit
            # history：可用记值，否则 None（断线）
            row.history.append(metric.value if row.available else None)
            self._refresh_breathing()
            # 内容驱动尺寸：文本变宽超过当前列宽 → 重算布局（窗格随之变宽）。
            # 只在变宽时重算，避免每 tick 无谓 setFixedSize；变窄不回缩（防抖动）。
            if self._text_outgrows_cols():
                self.update_layout()
            self.update()
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.update_data", e)

    def _text_outgrows_cols(self) -> bool:
        """当前行文本是否已超出列宽缓存（量宽用与布局同一字体）。"""
        fm = QFontMetrics(self._font())
        w_label, w_value, w_unit = self._col_w
        for r in self._rows:
            if (fm.horizontalAdvance(r.label) > w_label
                    or fm.horizontalAdvance(self._value_text(r)) > w_value
                    or fm.horizontalAdvance(r.unit) > w_unit):
                return True
        return False

    def _refresh_breathing(self) -> None:
        """任一行进入最高阈值段 → 启动呼吸时钟与动画定时器；全离开 → 停。"""
        any_top = any(in_top_segment(r.value, self.thresholds_for(r))
                      for r in self._rows if r.available)
        if any_top and not self._breathing:
            self._breath_clock.start()
            self._breathing = True
            if self.isVisible():
                self._anim_timer.start()
        elif not any_top and self._breathing:
            self._breathing = False
            self._anim_timer.stop()

    def hideEvent(self, event) -> None:  # noqa: N802
        """隐藏停自身呼吸相位推进 + 清交互态 + 可见性联动（阶段 4）。"""
        try:
            self._anim_timer.stop()
            self.clear_interaction_state()
            super().hideEvent(event)
            self.manager.on_widget_hidden()
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.hideEvent", e)

    def showEvent(self, event) -> None:  # noqa: N802
        try:
            super().showEvent(event)
            if self._breathing:
                self._breath_clock.restart()
                self._anim_timer.start()
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.showEvent", e)

    # ---- 阶段 3：拖动跟手 + 磁吸预览 + 松手吸附 ----
    def mousePressEvent(self, event) -> None:  # noqa: N802
        try:
            if event.button() == Qt.MouseButton.LeftButton:
                # 动画期间禁止再次拖动打断产生残留；新按下清齐四层
                if self._snap_animating:
                    return
                self._abort_snap_anim()
                self._press_global = event.globalPosition().toPoint()
                self._grab_offset = self._press_global - self.frameGeometry().topLeft()
                self._dragging = True
                self._drag_started = False
                # 组内成员拖动时同步偏移全组
                if self._group is not None:
                    self._drag_members = list(self._group.members)
                else:
                    self._drag_members = [self]
                event.accept()
            elif event.button() == Qt.MouseButton.RightButton:
                # 右键按下：记录起点，位移>4px 解绑、≤4px 抬起弹菜单
                self._right_press_global = event.globalPosition().toPoint()
                self._right_dragging = True
                self._right_drag_started = False
                event.accept()
            else:
                super().mousePressEvent(event)
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.mousePressEvent", e)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        try:
            gp = event.globalPosition().toPoint()
            # 右键拖动：组内窗位移>4px → 解绑自身，此后按独立窗处理
            if self._right_dragging:
                if not self._right_drag_started:
                    if (gp - self._right_press_global).manhattanLength() < DRAG_THRESHOLD_PX:
                        return
                    self._right_drag_started = True
                    if self._group is not None:
                        self.detach_from_group()
                # 解绑后独立窗跟手移动（无弹簧）
                if self._right_drag_started:
                    delta = gp - self._right_press_global
                    self.move(self.frameGeometry().topLeft() + delta)
                    self._right_press_global = gp
                    self._update_preview(gp)
                event.accept()
                return
            if not self._dragging:
                super().mouseMoveEvent(event)
                return
            if not self._drag_started:
                if (gp - self._press_global).manhattanLength() < DRAG_THRESHOLD_PX:
                    return
                self._drag_started = True
            # 直接 move 跟手（无弹簧）；组内同步偏移
            new_top = gp - self._grab_offset
            delta = new_top - self.frameGeometry().topLeft()
            for w in self._drag_members:
                w.move(w.pos() + delta)
            # 移动中检测预览（绝不改变位置去吸附）
            self._update_preview(gp)
            event.accept()
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.mouseMoveEvent", e)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        try:
            if event.button() == Qt.MouseButton.RightButton and self._right_dragging:
                self._right_dragging = False
                if not self._right_drag_started:
                    # 右键未拖动（位移≤4px 抬起）→ 弹最小菜单
                    self._show_min_menu(event.globalPosition().toPoint())
                else:
                    self._right_drag_started = False
                    self._finish_drag()
                event.accept()
                return
            if event.button() == Qt.MouseButton.LeftButton and self._dragging:
                self._dragging = False
                if self._drag_started:
                    self._finish_drag()
                self._drag_started = False
                event.accept()
            else:
                super().mouseReleaseEvent(event)
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.mouseReleaseEvent", e)

    def _update_preview(self, cursor_global: QPoint) -> None:
        """移动中检测：对其它独立窗/组包围盒四边与屏幕可用区四边算最小边距。

        <20px 记录预览目标/边/贴合位 + 画幽灵轮廓；≥20px 清预览。
        移动中绝不改变位置去吸附。
        """
        my_rect = self.frameGeometry()
        best = None   # (dist, target, edge, snap_rect)
        # 其它独立窗 / 组（排除自身与自身所在组）
        for other in self.manager._widgets.values():
            if other is self or not other.isVisible():
                continue
            if self._group is not None and other._group is self._group:
                continue
            target_rect = (other._group.bounding_rect()
                           if other._group is not None else other.frameGeometry())
            cand = self._edge_candidates(my_rect, target_rect)
            for dist, edge, sr in cand:
                if best is None or dist < best[0]:
                    best = (dist, other, edge, sr)
        # 屏幕可用区四边（screenAt 光标，不用 primaryScreen，坑 58）
        screen = QGuiApplication.screenAt(cursor_global)
        if screen is not None:
            avail = screen.availableGeometry()
            for dist, edge, sr in self._screen_candidates(my_rect, avail):
                if best is None or dist < best[0]:
                    best = (dist, "screen", edge, sr)
        if best is not None and best[0] < SNAP_PREVIEW_PX:
            dist, target, edge, sr = best
            self._preview_target = target
            self._preview_edge = edge
            self._preview_rect = sr
            self._preview_strong = dist < SNAP_STRONG_PX
            self.manager.show_ghost(sr, self._preview_strong)
        else:
            self._clear_preview()

    def _edge_candidates(self, my: QRect, target: QRect):
        """窗间四边贴合候选：(边距, 贴合边, 贴合位 QRect)。

        水平方向需垂直投影有重叠，垂直方向需水平投影有重叠，避免斜角误吸。
        交叉轴对齐（用户拍板）：左右贴合 → 顶边对齐（y=target.top）；
        上下贴合 → 左缘对齐（x=target.left）。
        """
        out = []
        v_overlap = my.top() < target.bottom() and my.bottom() > target.top()
        h_overlap = my.left() < target.right() and my.right() > target.left()
        # 贴合位用 left()+width() 口径（QRect.right()=left+width-1，避免 1px 缝）
        if v_overlap:
            # 我右边贴目标左边：my.left+my.width == target.left，顶边对齐
            d = abs((my.left() + my.width()) - target.left())
            sr = QRect(target.left() - my.width(), target.top(),
                       my.width(), my.height())
            out.append((d, "right", sr))
            # 我左边贴目标右边：my.left == target.left+target.width，顶边对齐
            d = abs(my.left() - (target.left() + target.width()))
            sr = QRect(target.left() + target.width(), target.top(),
                       my.width(), my.height())
            out.append((d, "left", sr))
        if h_overlap:
            # 我下边贴目标上边：my.top+my.height == target.top，左缘对齐
            d = abs((my.top() + my.height()) - target.top())
            sr = QRect(target.left(), target.top() - my.height(),
                       my.width(), my.height())
            out.append((d, "bottom", sr))
            # 我上边贴目标下边：my.top == target.top+target.height，左缘对齐
            d = abs(my.top() - (target.top() + target.height()))
            sr = QRect(target.left(), target.top() + target.height(),
                       my.width(), my.height())
            out.append((d, "top", sr))
        return out

    def _screen_candidates(self, my: QRect, avail: QRect):
        """屏幕可用区四边贴边候选（贴边停留，记 screen_edge，不入窗间组）。"""
        return [
            (abs(my.left() - avail.left()), "left",
             QRect(avail.left(), my.top(), my.width(), my.height())),
            (abs((my.left() + my.width()) - (avail.left() + avail.width())), "right",
             QRect(avail.left() + avail.width() - my.width(), my.top(),
                   my.width(), my.height())),
            (abs(my.top() - avail.top()), "top",
             QRect(my.left(), avail.top(), my.width(), my.height())),
            (abs((my.top() + my.height()) - (avail.top() + avail.height())), "bottom",
             QRect(my.left(), avail.top() + avail.height() - my.height(),
                   my.width(), my.height())),
        ]

    def _clear_preview(self) -> None:
        self._preview_target = None
        self._preview_edge = None
        self._preview_rect = None
        self._preview_strong = False
        self.manager.hide_ghost()

    def _finish_drag(self) -> None:
        """松手：有预览目标 → 280ms 缓入缓出滑到贴合位；无目标 → 原地停留。"""
        if self._preview_target is not None and self._preview_rect is not None:
            self._snap_from = self.pos()
            self._snap_to = self._preview_rect.topLeft()
            self._snap_pending = self._preview_target
            self._snap_edge_pending = self._preview_edge
            self._snap_animating = True
            self._snap_clock.start()
            self._snap_anim_timer.start()
            self.manager.hide_ghost()
        else:
            self._clear_preview()

    def _on_snap_anim(self) -> None:
        try:
            t = min(1.0, self._snap_clock.elapsed() / SNAP_ANIM_MS)
            # 缓入缓出（smoothstep）：两端慢、中段快
            e = t * t * (3 - 2 * t)
            x = round(self._snap_from.x() + (self._snap_to.x() - self._snap_from.x()) * e)
            y = round(self._snap_from.y() + (self._snap_to.y() - self._snap_from.y()) * e)
            self.move(x, y)
            if t >= 1.0:
                self._snap_anim_timer.stop()
                self._snap_animating = False
                self.move(self._snap_to)
                self._land_snap()
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui._on_snap_anim", e)

    def _land_snap(self) -> None:
        """落位后加入目标组 / 贴屏幕边，_is_snapped=True、关光晕、画融合虚线。"""
        target = self._snap_pending
        edge = getattr(self, "_snap_edge_pending", None)
        self._snap_pending = None
        self._contact_edges = [edge] if edge else []
        if target == "screen":
            # 屏幕边吸附 = 贴边停留并记 screen_edge，不入窗间组
            self._screen_edge = edge
            self._is_snapped = True
        elif isinstance(target, MonitorUnitWidget):
            self._screen_edge = None
            grp = target._group
            if grp is None:
                grp = self.manager.new_group()
                grp.add(target)
                target._is_snapped = True
                target._contact_edges = [self._opposite_edge(edge)]
            grp.add(self)
            self._is_snapped = True
        self._clear_preview()
        self.update()
        if target is not None and isinstance(target, MonitorUnitWidget):
            target.update()

    @staticmethod
    def _opposite_edge(edge: Optional[str]) -> Optional[str]:
        return {"left": "right", "right": "left",
                "top": "bottom", "bottom": "top"}.get(edge)

    def _paint_fuse(self, p: QPainter, s: float) -> None:
        """吸附态接触边画融合虚线（DashLine 1px×缩放，颜色随主题）。"""
        if not self._contact_edges:
            return
        c = fuse_color(self.theme)
        pen = QPen(c, max(1.0, FUSE_DASH_PX * s))
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        r = QRectF(self.rect())
        halo = round(HALO_PX * s)
        body = r.adjusted(halo, halo, -halo, -halo)
        for edge in self._contact_edges:
            if edge == "left":
                p.drawLine(QPointF(body.left(), body.top()),
                           QPointF(body.left(), body.bottom()))
            elif edge == "right":
                p.drawLine(QPointF(body.right(), body.top()),
                           QPointF(body.right(), body.bottom()))
            elif edge == "top":
                p.drawLine(QPointF(body.left(), body.top()),
                           QPointF(body.right(), body.top()))
            elif edge == "bottom":
                p.drawLine(QPointF(body.left(), body.bottom()),
                           QPointF(body.right(), body.bottom()))

    # ---- 阶段 4：解绑与状态机收口 ----
    def set_settings_callback(self, cb: Optional[Callable]) -> None:
        """注入「打开设置」回调（菜单项触发，非模态）。"""
        self._settings_callback = cb

    def detach_from_group(self) -> None:
        """从组移除自身；组剩余==1 时解散组，剩余成员复位 _is_snapped+恢复光晕。

        自身清 _group/_is_snapped、立即 update 恢复光晕；此后按独立窗处理。
        """
        try:
            grp = self._group
            if grp is None:
                # 独立窗也可能有屏幕边吸附态，解绑即恢复独立
                self._is_snapped = False
                self._screen_edge = None
                self._contact_edges = []
                self.update()
                return
            grp.remove(self)
            self._is_snapped = False
            self._contact_edges = []
            self._screen_edge = None
            # 组剩余==1 → 解散组，剩余成员必须复位（漏这步会永久残留融合态）
            if len(grp.members) == 1:
                last = grp.members[0]
                grp.remove(last)
                last._is_snapped = False
                last._contact_edges = []
                last._screen_edge = None
                last.update()
                self.manager.dissolve_group(grp)
            elif len(grp.members) == 0:
                self.manager.dissolve_group(grp)
            self.update()
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.detach_from_group", e)

    def _show_min_menu(self, global_pos: QPoint) -> None:
        """右键轻点最小菜单：关闭本窗 / 打开设置。

        菜单回调一律 QTimer.singleShot(0, ...) 非模态触发，禁 exec() 嵌套（坑 6）。
        用 popup（非模态）而非 exec；不得整体屏蔽 contextMenuEvent。
        """
        try:
            menu = QMenu(self)
            act_close = menu.addAction("关闭本窗")
            act_settings = menu.addAction("打开设置")
            act_close.triggered.connect(
                lambda: QTimer.singleShot(0, self.close))
            act_settings.triggered.connect(
                lambda: QTimer.singleShot(0, self._open_settings))
            menu.popup(global_pos)
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui._show_min_menu", e)

    def _open_settings(self) -> None:
        try:
            if self._settings_callback is not None:
                self._settings_callback()
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui._open_settings", e)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        """右键菜单走最小菜单（不整体屏蔽；与右键轻点同入口）。"""
        try:
            self._show_min_menu(event.globalPos())
            event.accept()
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.contextMenuEvent", e)

    def _abort_snap_anim(self) -> None:
        """打断吸附动画（新按下/关闭窗）：停定时器、清动画态。"""
        if self._snap_animating:
            self._snap_anim_timer.stop()
            self._snap_animating = False
            self._snap_pending = None

    def clear_interaction_state(self) -> None:
        """状态机守卫：清齐四层（_preview_target/_preview_edge/幽灵窗/动画定时器）。

        独立→预览→吸附→解绑→独立全链路每个切换点与打断点都调本方法。
        """
        self._preview_target = None
        self._preview_edge = None
        self._preview_rect = None
        self._preview_strong = False
        self.manager.hide_ghost()
        self._abort_snap_anim()

    def closeEvent(self, event) -> None:  # noqa: N802
        """关闭窗：清齐四层 + 从组解绑 + 通知管理器可见性联动。"""
        try:
            self.clear_interaction_state()
            if self._group is not None:
                self.detach_from_group()
            self._anim_timer.stop()
            super().closeEvent(event)
            self.manager.on_widget_hidden()
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.closeEvent", e)

    # ---- 阶段 4：测试钩子（公开方法，供离屏自检与无后端演示）----
    def simulate_data_update(self, key: str, value, status: str) -> None:
        """无后端演示：构造 Metric 走真实 update_data 路径。"""
        try:
            label, group, unit = self.manager._def_of(key)
            m = mh.Metric(key=key, label=label, group=group, value=value,
                          unit=unit, status=status)
            self.update_data(m)
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.simulate_data_update", e)


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
        self._refresh_ms = 1000
        self._timer = QTimer()        # 拉取 QTimer（阶段 2）
        self._timer.setInterval(self._refresh_ms)
        self._timer.timeout.connect(self.tick)
        # 阶段 3：幽灵轮廓 overlay（独立透明穿透顶层窗）
        self._ghost = GhostOverlay(theme, scale_getter)
        self._group_seq = 0
        # 阶段 A：新盘发现回调（参数级窗格下由 main 决定是否建窗）
        self.on_new_disk_keys: Optional[Callable] = None
        self._known_disk_keys: set = set()

    # ---- 阶段 2：单元窗注册与拉取节奏 ----
    def register_widget(self, unit_key: str, widget, row_keys: list,
                        thresholds: Optional[dict] = None) -> None:
        """注册一个单元窗及其行键集（行 label/unit 取自白名单定义）。"""
        self._widgets[unit_key] = widget
        widget.set_thresholds(thresholds or {})
        rows = []
        for key in row_keys:
            label, _group, unit = self._def_of(key)
            rows.append(UnitRow(key=key, label=label, value=None,
                                unit=unit, status=mh.STATUS_STALE))
        widget.set_rows(rows)

    @staticmethod
    def _def_of(key: str):
        for k, label, group, unit in mh.METRIC_DEFS:
            if k == key:
                return label, group, unit
        d = mh._def(key)   # 动态键（disk/fan）按模板还原
        return d[1], d[2], d[3]

    def start_polling(self, refresh_ms: int = 1000) -> None:
        self._refresh_ms = max(200, int(refresh_ms))
        self._timer.setInterval(self._refresh_ms)
        if self._any_visible():
            self._timer.start()

    def stop_polling(self) -> None:
        self._timer.stop()

    def _any_visible(self) -> bool:
        return any(w.isVisible() for w in self._widgets.values())

    def tick(self) -> None:
        """每 tick 只调一次 snapshot()，同一帧分发给所有可见单元。

        list_disks 传入该帧复用（禁止不传帧再取一帧）。窗口全部不可见 → 停定时器。
        """
        try:
            if not self._any_visible():
                self._timer.stop()
                return
            snap = self.hub.snapshot()          # 一帧一映射
            disks = self.hub.list_disks(snapshot=snap)
            self._notify_new_disks(disks)
            for w in self._widgets.values():
                if not w.isVisible():
                    continue
                for row in w._rows:
                    m = snap.get(row.key)
                    if m is not None:
                        w.update_data(m)
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.tick", e)

    def _notify_new_disks(self, disks: list) -> None:
        """参数级窗格：发现新盘键 → 回调 main（由其按配置决定是否建窗）。

        旧「多行塞一窗」同步已废弃（一参数一窗格，用户拍板）。
        """
        keys = set()
        for d in disks:
            i = d["index"]
            keys.add(f"disk.{i}.temp")
            keys.add(f"disk.{i}.usage")
        new = keys - self._known_disk_keys
        if new:
            self._known_disk_keys = keys
            if self.on_new_disk_keys is not None:
                self.on_new_disk_keys(sorted(new))

    # ---- 阶段 3：磁吸 ----
    def new_group(self) -> SnapGroup:
        self._group_seq += 1
        grp = SnapGroup(f"g{self._group_seq}")
        self._groups.append(grp)
        return grp

    def snap_groups(self) -> list:
        return list(self._groups)

    def dissolve_group(self, grp: SnapGroup) -> None:
        """解散组（剩余≤1 时调用）：从组列表移除。"""
        if grp in self._groups:
            self._groups.remove(grp)

    def on_widget_hidden(self) -> None:
        """单元窗隐藏/关闭 → 全部不可见时停拉取定时器（可见性联动）。"""
        try:
            if not self._any_visible():
                self._timer.stop()
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.on_widget_hidden", e)

    def show_ghost(self, rect: QRect, strong: bool) -> None:
        try:
            self._ghost.show_at(rect, strong)
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.show_ghost", e)

    def hide_ghost(self) -> None:
        try:
            self._ghost.hide()
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.hide_ghost", e)

    # ---- 阶段 4：测试钩子（公开方法，供离屏自检与无后端演示）----
    def simulate_drag(self, widget, dx: float, dy: float,
                      button: Qt.MouseButton = Qt.MouseButton.LeftButton) -> None:
        """离屏拖动演示钩子：sendEvent 驱动 按下→移动→松手 全链路。

        button=RightButton 时演示右键拖拽解绑。
        """
        from PySide6.QtGui import QMouseEvent
        start = widget.frameGeometry().topLeft() + QPoint(5, 5)
        end = start + QPoint(int(dx), int(dy))

        def ev(etype, gpos):
            return QMouseEvent(etype, QPointF(gpos), QPointF(gpos),
                               button, button,
                               Qt.KeyboardModifier.NoModifier)
        from PySide6.QtWidgets import QApplication as _QApp
        _QApp.sendEvent(widget, ev(QEvent.Type.MouseButtonPress, start))
        _QApp.sendEvent(widget, ev(QEvent.Type.MouseMove, end))
        _QApp.sendEvent(widget, ev(QEvent.Type.MouseButtonRelease, end))
