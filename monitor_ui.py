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

# ---------------------------------------------------------------------------
# 固定窗格尺寸（用户拍板 V16.1：初始 250×80px；宽度不够时整体扩到 350px，不逐数据撑宽）
# 绝对像素量，渲染时乘 scale_getter()；数据到达/跳动不再改变窗格尺寸
# ---------------------------------------------------------------------------
BASE_W = 250            # 基准窗格宽（×缩放）
BASE_W_EXPANDED = 350   # 内容自然宽超基准时的整体扩宽档（×缩放）
BASE_H = 80             # 基准窗格高（×缩放）；状态小字叠加绘制不额外占高
# 列宽预留样本：A 列按白名单全量标签（含动态键模板），B 列按典型值+极值，
# C 列按白名单全量单位——数据跳动不再触发重算撑宽（旧「变宽即重算」已废弃）
_SAMPLE_VALUES = ("--", "100.0", "9999.9", "12345", "123456")


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


# V16.2：用量类（GB）按 用量/总量 比例决定绿黄红——64G 内存用 16G 不再误闪红。
# 显示值/单位/history 保持 canonical GB（照网速 KB 先例），仅配色与呼吸判定换算。
_USAGE_RATIO_KEYS = ("mem.used", "gpu_i.shared")


def is_usage_ratio_key(key: str) -> bool:
    """是否用量类键（内存用量/共享内存用量/独显显存用量）。"""
    if key in _USAGE_RATIO_KEYS:
        return True
    parts = key.split(".")
    return (len(parts) == 3 and parts[0] == "gpu_d"
            and parts[1].isdigit() and parts[2] == "vram_used")


def usage_ratio(value: Optional[float], total: Optional[float]) -> Optional[float]:
    """用量÷总量×100；任一缺失或总量非正 → None（配色走中性，不闪红）。"""
    if value is None or total is None or total <= 0:
        return None
    return value / total * 100.0


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
    """本体底色：优先取「资源监视」颜色设置项（theme.monitor_color）；
    无该设置时回退系统深浅色（深色→深底、浅色→浅底，禁深色收边）。"""
    mc = theme.get("monitor_color")
    if mc:
        c = QColor(str(mc))
        if c.isValid():
            return c
    return QColor(24, 26, 32) if theme.get("system_dark_mode") else QColor(250, 250, 252)


def _luminance(c: QColor) -> float:
    """感知亮度（0~255，与 main.luminance 同口径）。"""
    return 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()


def text_color(theme: dict) -> QColor:
    """名称/单位/状态小字颜色：按背景色亮度自动黑/白（浅色底→黑字、深色底→白字）。

    有「资源监视」背景色设置项时按其亮度判定；无则回退系统深浅色。
    数值与曲线不走本函数——它们按阈值范围着语义色（用户拍板）。
    """
    mc = theme.get("monitor_color")
    if mc:
        c = QColor(str(mc))
        if c.isValid():
            return QColor(30, 32, 40) if _luminance(c) > 150 else QColor(235, 238, 245)
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
    hardware: str = ""   # 硬件名（CPU/GPU/硬盘窗格小字，与参数名右对齐）
    total: Optional[float] = None  # 容量总量（与 value 同单位；用量类按比例配色，V16.2）
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
                    固定 10 项 + 独显卡0 四参 + 风扇三槽位 + 硬盘0 兜底项；
                    默认全不勾选。动态键（gpu_d.1+ / disk.1+）运行时按实例展开，
                    勾选状态落盘保留。
    layout          {参数key: {x, y, group_id}}（位置与吸附组记忆）
    """
    units = {key: {"enabled": False, "threshold": []}
             for key, _label, _group, _unit in mh.METRIC_DEFS}
    for key in ("gpu_d.0.load", "gpu_d.0.temp", "gpu_d.0.vram_load", "gpu_d.0.vram_used",
                "fan.cpu.0", "fan.gpu_d.0", "fan.mobo.0",
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
        self._snap_screen_edge_pending = None  # 落位后屏幕贴合边（缩放重贴用）
        self._snap_constraints: list = []  # [(目标窗, 贴合边)] 缩放重排用
        self._snap_members: list = []  # 整组吸附动画成员（含自身）
        self._snap_member_from: dict = {}  # 成员 id → 动画起始位置
        # 阶段 4：右键拖动解绑 / 轻点菜单
        self._right_dragging = False
        self._right_drag_started = False
        self._right_press_global = QPoint()
        self._suppress_context_menu = False  # 右键拖动解绑后抑制菜单（仅轻点弹）
        self._group_screen_edge = None  # 组级屏幕贴边记录（整组贴边，缩放重贴用）
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
        """首次显示：管理器防重叠摆放（V16.2）再 show。

        旧实现每个窗格都 place_fn 到球体旁同一坐标 → 首启全部重叠；
        现由管理器统一摆放：有布局记忆用记忆位，无记忆按首位窗格向右排开。
        """
        try:
            place = getattr(self.manager, "place_for_show", None)
            if callable(place):
                place(self, anchor_rect)
            else:
                self.place_fn(self, anchor_rect)
            self.show()
            self.raise_()
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.show_at", e)

    # ---- 阶段 1：渲染与布局 ----
    def _font(self) -> QFont:
        f = QFont(FONT_FAMILY)
        f.setPixelSize(max(1, round(FONT_PX * self.scale_getter())))
        f.setBold(True)   # 窗格字体加粗（用户拍板，提升小窗可读性）
        return f

    def update_layout(self) -> None:
        """固定基准窗格布局（用户拍板：初始 200×40px，×显示缩放）。

        宽：列宽按「白名单全量标签/典型值样本/全量单位」与当前行文本取大者
        预留（数据跳动不再撑宽）；默认恒 BASE_W，列间富余均分；任一窗格
        内容自然宽超基准 → 管理器把全部窗格统一切到 BASE_W_EXPANDED
        （250px，一改变就全改变），不再逐数据无限长大。
        高：恒 BASE_H（状态小字叠加在数值行下方绘制，不额外占高）。
        末尾 adjustSize()（Qt 未 show 读尺寸是假 640x480，坑 50）。
        """
        try:
            s = self.scale_getter()
            fm = QFontMetrics(self._font())
            rows = self._rows
            gap = round(COL_GAP * s)
            pad_x = round(PAD_X * s)
            base_w = round(BASE_W * s)
            expanded_w = round(BASE_W_EXPANDED * s)
            # 扩宽判定只看「当前行真实内容自然宽」（不含白名单预留样本）：
            # 白名单最长标签在基准档内截断适配，不触发扩宽；只有真实内容
            # （如运行时超长标签/极值）超基准才扩。扩宽档由管理器统一持有
            # （用户拍板：一改变就全改变，所有窗格同宽）。
            expanded = bool(getattr(self.manager, "_width_expanded", False))
            if not expanded and self._natural_width() > base_w:
                request = getattr(self.manager, "request_expand_width", None)
                if callable(request):
                    request()
                    expanded = bool(getattr(self.manager, "_width_expanded", False))
            total_w = expanded_w if expanded else base_w
            # 列宽分配（当前行真实内容优先保全，恒完整显示不截断）：
            #   下限 = 当前行各列实际文本宽（硬约束，任何档位都不截断当前内容）；
            #   预留 = 白名单全量标签/典型值样本/全量单位（数据跳动不撑宽）；
            #   窗格宽放得下预留 → 按预留分配、富余均分列间距；放不下 → 先保全
            #   当前行下限，剩余空间再尽量给预留样本（超宽样本让位，不截当前内容）。
            cur_label = max((fm.horizontalAdvance(r.label) for r in rows), default=0)
            cur_value = max((fm.horizontalAdvance(self._value_text(r)) for r in rows),
                            default=0)
            cur_unit = max((fm.horizontalAdvance(self._display_unit(r)) for r in rows),
                           default=0)
            label_samples = [label for _k, label, _g, _u in mh.METRIC_DEFS]
            label_samples += [mh._def(k)[1] for k in
                              ("gpu_d.0.load", "gpu_d.0.temp",
                               "gpu_d.0.vram_load", "gpu_d.0.vram_used",
                               "disk.0.temp", "disk.0.usage",
                               "fan.cpu.0", "fan.gpu_d.0", "fan.mobo.0")]
            value_samples = list(_SAMPLE_VALUES)
            unit_samples = ([u for _k, _l, _g, u in mh.METRIC_DEFS]
                            + ["℃", "%", "RPM", "KB/s"])
            res_label = max((fm.horizontalAdvance(t) for t in label_samples), default=0)
            res_value = max((fm.horizontalAdvance(t) for t in value_samples), default=0)
            res_unit = max((fm.horizontalAdvance(t) for t in unit_samples), default=0)
            avail = total_w - 2 * gap - 2 * pad_x   # 三列可用总宽
            cur_sum = cur_label + cur_value + cur_unit
            res_sum = res_label + res_value + res_unit
            if res_sum <= avail:
                # 预留样本放得下：按预留分配，富余均分列间距
                w_label, w_value, w_unit = res_label, res_value, res_unit
                gap += (avail - res_sum) // 2
            elif cur_sum <= avail:
                # 当前行放得下、预留放不下：保全当前行下限，剩余空间按预留比例补
                extra = avail - cur_sum
                need_l = max(0, res_label - cur_label)
                need_v = max(0, res_value - cur_value)
                need_u = max(0, res_unit - cur_unit)
                need_sum = need_l + need_v + need_u
                if need_sum > 0:
                    w_label = cur_label + extra * need_l // need_sum
                    w_value = cur_value + extra * need_v // need_sum
                    w_unit = cur_unit + extra * need_u // need_sum
                else:
                    w_label, w_value, w_unit = cur_label, cur_value, cur_unit
                    gap += extra // 2
            else:
                # 极端：当前行本身超窗格宽（如运行时超长标签）——保全数值/单位，
                # 名称列截断绘制（drawText 矩形裁剪），并请求全局扩宽
                w_value, w_unit = cur_value, cur_unit
                w_label = max(0, avail - cur_value - cur_unit)
            self._col_w = (w_label, w_value, w_unit)
            self._col_gap = gap
            self._row_h = fm.height()
            # 状态小字行高（叠加绘制用，不占窗格高度）
            small = QFont(self._font())
            small.setPixelSize(max(1, round(FONT_PX * 0.72 * s)))
            self._status_h = QFontMetrics(small).height()
            self._geo_scale = s
            total_h = round(BASE_H * s)
            self.setFixedSize(total_w, total_h)
            self.adjustSize()
            # 变高方向同帧 repaint 防露脏区（坑 53）
            if total_h > self._last_h:
                self.repaint()
            self._last_h = total_h
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.update_layout", e)

    def _natural_width(self) -> int:
        """当前行内容自然宽（三列实际文本+列间距+内边距，×缩放）。

        供管理器判定是否需要全局扩宽；与 update_layout 同一量宽口径。
        """
        fm = QFontMetrics(self._font())
        s = self.scale_getter()
        rows = self._rows
        w_label = max((fm.horizontalAdvance(r.label) for r in rows), default=0)
        w_value = max((fm.horizontalAdvance(self._value_text(r)) for r in rows),
                      default=0)
        w_unit = max((fm.horizontalAdvance(r.unit) for r in rows), default=0)
        return (w_label + w_value + w_unit
                + 2 * round(COL_GAP * s) + 2 * round(PAD_X * s))

    def _value_text(self, row: UnitRow) -> str:
        """B 列数值文本：纯数值（不带单位），走 hub.fmt_value（不自造换算）。

        单位独立画在 C 列；无读数显示 "--"，状态文案走数值行下方状态小字。
        （修复：旧实现用 fmt 带单位串，B 列宽按初始 "--" 量宽后不再重算，
        真实数据右对齐被裁剪只剩尾部单位——数值消失根因。）
        """
        # 网速 KB 自适应（用户拍板）：采样 <1MB/s → 数值×1024 显示 KB；
        # 历史缓冲与阈值色仍按 MB/s canonical 口径，仅显示层换算
        v = row.value
        if (row.key in ("net.tx", "net.rx") and v is not None
                and 0 < v < 1.0):
            v = v * 1024.0
        m = mh.Metric(key=row.key, label=row.label, group="",
                      value=v, unit=row.unit, status=row.status)
        return mh.MetricsHub.fmt_value(m)

    def _display_unit(self, row: UnitRow) -> str:
        """C 列单位文本：网速 <1MB/s 时显示 KB/s，其余按白名单单位。"""
        if (row.key in ("net.tx", "net.rx") and row.value is not None
                and 0 < row.value < 1.0):
            return "KB/s"
        return row.unit

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
        gap = getattr(self, "_col_gap", round(COL_GAP * s))
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
            # C 列：单位（左对齐；网速 KB 自适应）
            p.setPen(tc)
            p.drawText(QRectF(x_unit, y, w_unit, self._row_h),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       self._display_unit(row))
            # 状态小字（数值行下方叠加绘制，不额外占窗格高度——固定 40px 口径）
            st_text = STATUS_TEXT.get(row.status, "")
            if not row.available and st_text:
                p.setFont(small)
                sc = QColor(tc)
                sc.setAlphaF(0.6)
                p.setPen(sc)
                p.drawText(QRectF(x_value, y + self._row_h - 2,
                                  w_value + gap + w_unit, self._status_h),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                           st_text)
            # 硬件名小字（CPU/GPU/硬盘）：画在参数名正下方，穿透 B/C 列用全本体宽，
            # 字号自适应——从状态小字字号起逐步缩小，优先保证一行显示完整硬件名，
            # 缩到下限仍放不下才截断（用户拍板）；颜色走深白浅黑（text_color）。
            if row.hardware:
                hw_full_w = w_label + w_value + w_unit + 2 * gap
                hw_px = max(1, round(FONT_PX * 0.72 * s))
                hw_min_px = max(1, round(FONT_PX * 0.45 * s))
                hw_font = QFont(self._font())
                hw_font.setPixelSize(hw_px)
                # 逐步缩字号直到一行放得下（或到下限）；每步重算字体度量
                while hw_px > hw_min_px:
                    if QFontMetrics(hw_font).horizontalAdvance(row.hardware) <= hw_full_w:
                        break
                    hw_px -= 1
                    hw_font.setPixelSize(hw_px)
                p.setFont(hw_font)
                hc = QColor(tc)
                hc.setAlphaF(0.75)
                p.setPen(hc)
                p.drawText(QRectF(x_label, y + self._row_h - 2,
                                  hw_full_w, self._status_h),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           row.hardware)
            y += self._row_h + row_gap
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
        gap = getattr(self, "_col_gap", round(COL_GAP * s))
        x0 = body.left() + pad_x
        full_w = w_label + w_value + w_unit + 2 * gap
        # sparkline 区起点 = 所有文本行之后（状态小字叠加绘制，不占行高）
        n = max(1, len(self._rows))
        y_base = (body.top() + pad_y
                  + n * self._row_h + (n - 1) * row_gap)
        pen_w = max(1.0, SPARK_LINE_PX * s)
        for i, row in enumerate(self._rows):
            # 每行 sparkline 占 spark_h/n 高，水平铺满内容宽
            band_h = spark_h / n
            top = y_base + i * band_h
            # 仅硬盘使用率（容量占比）波动极小、曲线近乎横线无信息量，不绘制
            # （V16.2 用户拍板收窄范围：CPU/内存/GPU/显存使用率恢复画曲线）。
            # 按键判定不依赖标签文本；数据层 history 照常记录，仅渲染跳过。
            _kp = row.key.split(".")
            if (len(_kp) == 3 and _kp[0] == "disk"
                    and _kp[1].isdigit() and _kp[2] == "usage"):
                continue
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

    def _color_value(self, row: UnitRow) -> Optional[float]:
        """配色判定值：用量类（有总量）换算成百分比，其余用 canonical 原值。

        V16.2：内存/共享内存/显存用量按 用量÷总量×100 走百分比三段
        （0~60 绿/60~85 黄/85~100 红）；总量缺失返回 None → 中性色不闪红。
        用户自定义 threshold 仍按 GB 原值生效（预留口径，不走比例）。
        """
        if is_usage_ratio_key(row.key) and not self._thresholds.get(row.key):
            return usage_ratio(row.value, row.total)
        return row.value

    def _semantic_color(self, row: UnitRow) -> QColor:
        if is_usage_ratio_key(row.key) and not self._thresholds.get(row.key):
            ratio = usage_ratio(row.value, row.total)
            if ratio is None:
                return QColor(150, 150, 150)   # 总量缺失：中性色，不误闪红
            return get_color_for_value(ratio, default_thresholds(row.key, "%"))
        return get_color_for_value(row.value, self.thresholds_for(row))

    def _row_alpha(self, row: UnitRow) -> float:
        """高负载呼吸：进入最高阈值段 → 1Hz 正弦 0.3↔1.0；否则恒 1.0。

        只影响前景（数值/sparkline），不影响背景。离开阈值段立即恢复。
        用量类按百分比段判定（与配色同口径，V16.2）。
        """
        if is_usage_ratio_key(row.key) and not self._thresholds.get(row.key):
            ratio = usage_ratio(row.value, row.total)
            if ratio is not None and in_top_segment(
                    ratio, default_thresholds(row.key, "%")):
                return breath_alpha(self._breath_clock.elapsed())
            return 1.0
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
            old_unit = self._display_unit(row)
            row.value = metric.value
            row.status = metric.status
            row.label = metric.label or row.label
            row.unit = metric.unit or row.unit
            row.hardware = getattr(metric, "hardware", "") or row.hardware
            row.total = getattr(metric, "total", None)   # 用量类按比例配色（V16.2）
            # history：可用记值，否则 None（断线；网速记 canonical MB/s）
            row.history.append(metric.value if row.available else None)
            self._refresh_breathing()
            # 网速 KB↔MB 切换 → 单位列宽变化，重算布局（其余数据跳动不重算）
            if self._display_unit(row) != old_unit:
                self.update_layout()
            # 固定基准窗格：列宽已按白名单+样本预留，数据跳动不改变尺寸
            # （旧「变宽即重算撑宽」已废弃——窗格随数据长大是用户反馈的问题）。
            # 仅当内容真实宽度超基准 → 管理器把全部窗格统一扩宽（一改变就全改变）
            if (not getattr(self.manager, "_width_expanded", False)
                    and self._natural_width() > round(BASE_W * self.scale_getter())):
                request = getattr(self.manager, "request_expand_width", None)
                if callable(request):
                    request()
            self.update()
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.update_data", e)

    def _refresh_breathing(self) -> None:
        """任一行进入最高阈值段 → 启动呼吸时钟与动画定时器；全离开 → 停。

        用量类按 用量÷总量 百分比段判定（与 _row_alpha 同口径，V16.2）。
        """
        def _top(r) -> bool:
            if is_usage_ratio_key(r.key) and not self._thresholds.get(r.key):
                ratio = usage_ratio(r.value, r.total)
                return ratio is not None and in_top_segment(
                    ratio, default_thresholds(r.key, "%"))
            return in_top_segment(r.value, self.thresholds_for(r))
        any_top = any(_top(r) for r in self._rows if r.available)
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
                self._suppress_context_menu = False
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
                    self._suppress_context_menu = True  # 拖动解绑 → 抑制右键菜单
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

    def _drag_group_rect(self) -> QRect:
        """被拖移动单元的包围盒：成组=组包围盒（整组刚性移动），独立=自身。"""
        if self._group is not None:
            return self._group.bounding_rect()
        return self.frameGeometry()

    def _update_preview(self, cursor_global: QPoint) -> None:
        """移动中检测：以「被拖组包围盒」对其它每个窗格各自矩形与屏幕边算边距。

        <20px 记录预览目标/边/贴合位 + 画幽灵轮廓；≥20px 清预览。
        移动中绝不改变位置去吸附。

        磁吸目标粒度（V16.1 修复）：对其它窗格**逐个**用其自身 frameGeometry
        算候选，而非整组 bounding_rect——否则竖直堆叠的大组包围盒会把单窗
        的「下方」候选推到整摞底下，导致单窗下方吸附失效（只能接上/左）。
        逐窗格目标让新窗能精确贴到组内任一单窗的四边。
        """
        my_rect = self._drag_group_rect()
        best = None   # (dist, target, edge, snap_rect)
        # 其它窗格逐个算候选（排除自身与自身所在组成员）
        for other in self.manager._widgets.values():
            if other is self or not other.isVisible():
                continue
            if self._group is not None and other._group is self._group:
                continue
            target_rect = other.frameGeometry()
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
        """松手：有预览目标 → 整组 280ms 缓入缓出滑到贴合位；无目标 → 原地停留。

        动画按「组包围盒左上角」从当前位置滑到预览贴合位，组内成员记录各自
        起始位置，每帧按同一 delta 刚性平移（多对多磁吸整组同步，不丢成员）。
        """
        if self._preview_target is not None and self._preview_rect is not None:
            grp_rect = self._drag_group_rect()
            self._snap_from = grp_rect.topLeft()
            self._snap_to = self._preview_rect.topLeft()
            self._snap_members = (list(self._group.members)
                                  if self._group is not None else [self])
            self._snap_member_from = {id(w): w.pos() for w in self._snap_members}
            self._snap_pending = self._preview_target
            self._snap_edge_pending = self._preview_edge
            self._snap_screen_edge_pending = (
                self._preview_edge if self._preview_target == "screen" else None)
            self._snap_animating = True
            self._snap_clock.start()
            self._snap_anim_timer.start()
            self.manager.hide_ghost()
        else:
            self._clear_preview()
            # V16.2：无磁吸目标松手 → 若与他窗重叠，自动归位到首位窗格右边
            self.manager.check_overlap_after_drop(self)

    def _on_snap_anim(self) -> None:
        try:
            t = min(1.0, self._snap_clock.elapsed() / SNAP_ANIM_MS)
            # 缓入缓出（smoothstep）：两端慢、中段快
            e = t * t * (3 - 2 * t)
            gx = round(self._snap_from.x() + (self._snap_to.x() - self._snap_from.x()) * e)
            gy = round(self._snap_from.y() + (self._snap_to.y() - self._snap_from.y()) * e)
            delta = QPoint(gx, gy) - self._snap_from
            # 整组刚性平移：每个成员从各自起始位 + 同一 delta
            for w in getattr(self, "_snap_members", [self]):
                base = self._snap_member_from.get(id(w))
                if base is not None:
                    w.move(base + delta)
            if t >= 1.0:
                self._snap_anim_timer.stop()
                self._snap_animating = False
                final_delta = self._snap_to - self._snap_from
                for w in getattr(self, "_snap_members", [self]):
                    base = self._snap_member_from.get(id(w))
                    if base is not None:
                        w.move(base + final_delta)
                self._land_snap()
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui._on_snap_anim", e)

    def _land_snap(self) -> None:
        """落位：整组合并到目标组 / 整组贴屏幕边，_is_snapped=True、关光晕。

        成组逻辑（单对单/单对多/多对单/多对多统一）：被拖移动单元（独立窗或
        整组）与目标单元（独立窗或整组）合并为一个组——目标组为存续组，被拖
        组成员全部并入、旧组解散；接触边按落位几何在实际接触的两个窗之间重算，
        结构化约束 _snap_constraints 记在该接触窗对上（缩放重排据此对齐）。
        屏幕边吸附：整组贴边，组级 _group_screen_edge 记录（缩放整组重贴）。
        """
        target = self._snap_pending
        edge = getattr(self, "_snap_edge_pending", None)
        screen_edge = getattr(self, "_snap_screen_edge_pending", None)
        self._snap_pending = None
        self._snap_screen_edge_pending = None
        moving = list(self._snap_members) if self._snap_members else [self]
        if target == "screen":
            # 整组贴屏幕边：组级记录，所有成员共享同一贴边（缩放整组重贴）
            se = screen_edge or edge
            for w in moving:
                w._screen_edge = se
                w._is_snapped = True
                w._contact_edges = []
            if self._group is not None:
                self._group_screen_edge = se
            self._clear_preview()
            for w in moving:
                w.update()
            return
        if isinstance(target, MonitorUnitWidget):
            for w in moving:
                w._screen_edge = None
            self._merge_into_target(target, edge, moving)
        self._clear_preview()
        for w in moving:
            w.update()
        if isinstance(target, MonitorUnitWidget):
            target.update()
        # V16.2：落位后若仍与他窗重叠（如贴屏幕边压住他窗）→ 归位首位窗格右边
        self.manager.check_overlap_after_drop(self)

    def _merge_into_target(self, target, edge, moving) -> None:
        """把被拖移动单元（moving）并入目标 target 所在组，旧组解散。

        存续组优先取目标组（目标不动，被拖组滑过来贴合）；目标独立则新建组。
        接触窗对：被拖组里最靠近目标边的窗 vs 目标组里最靠近被拖组的窗，
        约束记在该窗对上（而非笼统记 self/target，多对多才不会错）。
        """
        mgr = self.manager
        target_grp = target._group
        # keep_grp：目标组优先；目标独立 → 新建组并先纳入目标
        if target_grp is None:
            keep_grp = mgr.new_group()
            keep_grp.add(target)
            target._is_snapped = True
        else:
            keep_grp = target_grp
        # 被拖组成员全部并入存续组（含 self）；旧组若不同则解散
        for w in moving:
            if w._group is not None and w._group is not keep_grp:
                old = w._group
                old.remove(w)
                if len(old.members) <= 1:
                    for last in list(old.members):
                        old.remove(last)
                        last._group = None
                    mgr.dissolve_group(old)
            keep_grp.add(w)
            w._is_snapped = True
            w._group_screen_edge = None
        # 目标组成员清组级屏幕贴边（已并入窗间组）
        for w in keep_grp.members:
            w._group_screen_edge = None
        # 接触窗对：按落位几何找实际贴合的两个窗
        a, b = self._contact_pair(moving, list(keep_grp.members), edge)
        if a is not None and b is not None and edge:
            a._contact_edges = [edge]
            b._contact_edges = [self._opposite_edge(edge)]
            a._snap_constraints = [c for c in a._snap_constraints if c[0] is not b]
            a._snap_constraints.append((b, edge))
        # 其余成员接触边清空（仅接触窗对画融合虚线）
        for w in keep_grp.members:
            if w is not a and w is not b:
                w._contact_edges = []

    @staticmethod
    def _contact_pair(moving, target_members, edge):
        """按贴合边找实际接触的两个窗：被拖侧最靠边窗 vs 目标侧最靠边窗。"""
        if not moving or not target_members:
            return (None, None)
        if edge in ("left", "right"):
            # 水平贴合：被拖侧取垂直方向与目标重叠最多者，目标侧同理
            def v_overlap(w, others):
                r = w.frameGeometry()
                return sum(max(0, min(r.bottom(), o.frameGeometry().bottom())
                               - max(r.top(), o.frameGeometry().top()))
                           for o in others)
            a = max(moving, key=lambda w: v_overlap(w, target_members))
            b = max(target_members, key=lambda w: v_overlap(w, moving))
        else:
            def h_overlap(w, others):
                r = w.frameGeometry()
                return sum(max(0, min(r.right(), o.frameGeometry().right())
                               - max(r.left(), o.frameGeometry().left()))
                           for o in others)
            a = max(moving, key=lambda w: h_overlap(w, target_members))
            b = max(target_members, key=lambda w: h_overlap(w, moving))
        return (a, b)

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
            self._group_screen_edge = None
            self._snap_constraints = []   # 解绑即清贴合约束（缩放重排不再引用）
            # 组剩余==1 → 解散组，剩余成员必须复位（漏这步会永久残留融合态）
            if len(grp.members) == 1:
                last = grp.members[0]
                grp.remove(last)
                last._is_snapped = False
                last._contact_edges = []
                last._screen_edge = None
                last._group_screen_edge = None
                last._snap_constraints = []
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
        """右键菜单走最小菜单（不整体屏蔽；与右键轻点同入口）。

        右键拖动解绑后 Qt 仍会派发本事件（Windows 右键抬起触发）——见抑制
        标志即吞掉，避免解绑同时弹菜单影响观感；仅右键轻点（位移≤4px）才弹。
        """
        try:
            if self._suppress_context_menu:
                self._suppress_context_menu = False
                event.accept()
                return
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
        self._widget_order: list = []  # 创建顺序（V16.2 首位窗格=最先创建者）
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
        self.on_new_gpu_d_keys: Optional[Callable] = None
        self._known_gpu_d_keys: set = set()
        # V16.2：硬件名变化回调（设置窗设备名小字每次运行重载，不落盘）
        self.on_hardware_names: Optional[Callable] = None
        self._last_hw_names: dict = {}
        # 全局扩宽档（用户拍板：任一窗格内容超基准宽 → 所有窗格统一扩宽，
        # 避免大小不一；只升不降，会话内保持统一宽度）
        self._width_expanded = False

    # ---- 阶段 2：单元窗注册与拉取节奏 ----
    def register_widget(self, unit_key: str, widget, row_keys: list,
                        thresholds: Optional[dict] = None) -> None:
        """注册一个单元窗及其行键集（行 label/unit 取自白名单定义）。"""
        self._widgets[unit_key] = widget
        if widget not in self._widget_order:
            self._widget_order.append(widget)
        widget.set_thresholds(thresholds or {})
        rows = []
        for key in row_keys:
            label, _group, unit = self._def_of(key)
            rows.append(UnitRow(key=key, label=label, value=None,
                                unit=unit, status=mh.STATUS_STALE))
        widget.set_rows(rows)
        # 新窗格内容超基准宽 → 全局扩宽（含本窗，所有窗格同宽）
        if not self._width_expanded and widget._natural_width() > round(
                BASE_W * widget.scale_getter()):
            self.request_expand_width()

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
            gpus_d = self.hub.list_gpus_d(snapshot=snap)
            self._notify_new_gpus_d(gpus_d)
            self._notify_hardware_names(snap)
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

    def _notify_new_gpus_d(self, gpus_d: list) -> None:
        """独显全量枚举：发现新卡键 → 回调 main（参照新盘发现，一参数一窗格）。"""
        keys = set()
        for g in gpus_d:
            i = g["index"]
            for suffix in ("load", "temp", "vram_load", "vram_used"):
                keys.add(f"gpu_d.{i}.{suffix}")
        new = keys - self._known_gpu_d_keys
        if new:
            self._known_gpu_d_keys = keys
            if self.on_new_gpu_d_keys is not None:
                self.on_new_gpu_d_keys(sorted(new))

    def _notify_hardware_names(self, snap: dict) -> None:
        """V16.2：硬件名变化 → 回调 main 刷新设置窗设备名小字。

        设备名每次运行从实时快照重取（不落盘）；仅名称集合变化时回调，
        避免每帧刷新设置窗。无设备名的键不进集合。
        """
        names = {k: m.hardware for k, m in snap.items()
                 if getattr(m, "hardware", "")}
        if names != self._last_hw_names:
            self._last_hw_names = names
            if self.on_hardware_names is not None:
                self.on_hardware_names(dict(names))

    # ---- V16.2：禁止重叠 + 首位窗格归位 + 重启恢复组 ----
    def first_widget(self, exclude=()):
        """首位窗格 = 最先创建的可见窗格（用户拍板）；无可见窗返回 None。

        exclude：排除的窗格集（归位时排除被移动组自身成员）。
        """
        ex = set(id(t) for t in exclude)
        for w in self._widget_order:
            if w.isVisible() and id(w) not in ex:
                return w
        return None

    @staticmethod
    def _rects_overlap(a: QRect, b: QRect) -> bool:
        """严格相交才算重叠（边贴边不算）。"""
        return (a.left() < b.left() + b.width()
                and a.left() + a.width() > b.left()
                and a.top() < b.top() + b.height()
                and a.top() + a.height() > b.top())

    @staticmethod
    def _opposite_edge(edge):
        """贴合边取对侧（与 MonitorUnitWidget._opposite_edge 同口径）。"""
        return {"left": "right", "right": "left",
                "top": "bottom", "bottom": "top"}.get(edge)

    def _screen_avail_for(self, anchor_rect: QRect):
        """锚点所在屏可用区；screenAt 取不到时回退 screens()[0]（离屏自检）。"""
        screen = QGuiApplication.screenAt(anchor_rect.center())
        if screen is None:
            screens = QGuiApplication.screens()
            screen = screens[0] if screens else None
        return screen.availableGeometry() if screen is not None else None

    def place_for_show(self, widget, anchor_rect: QRect) -> None:
        """显示前防重叠摆放（V16.2）：有布局记忆用记忆位（冲突顺延），
        无记忆以首位窗格为基准向右依次排开、超屏换行，互不重叠。"""
        try:
            layout = {}
            try:
                layout = (self.config_io["read"]().get("layout") or {})
            except Exception:  # noqa: BLE001
                layout = {}
            rec = layout.get(widget.unit_key)
            if isinstance(rec, dict):
                try:
                    x, y = float(rec.get("x")), float(rec.get("y"))
                    if math.isfinite(x) and math.isfinite(y):
                        widget.move(round(x), round(y))
                        if self._overlaps_others(widget):
                            self._relocate_right_of_first(widget)
                        return
                except (TypeError, ValueError):
                    pass
            elif widget.pos() != QPoint(0, 0):
                # 调用方已按布局记忆 move 过（_monitor_restore_layout 先例）：
                # 尊重现位，仅冲突时归位，不覆盖记忆坐标
                if self._overlaps_others(widget):
                    self._relocate_right_of_first(widget)
                return
            first = self.first_widget()
            if first is None or first is widget:
                # 首位窗格：按注入 place_fn 定位（球体旁空间大侧）
                widget.place_fn(widget, anchor_rect)
                return
            fr = first.frameGeometry()
            wr = widget.frameGeometry()
            avail = self._screen_avail_for(fr)
            pos = self._find_free_slot(
                fr.left() + fr.width(), fr.top(), wr.width(), wr.height(),
                avail, exclude=(widget,))
            widget.move(*pos)
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.place_for_show", e)

    def _overlaps_others(self, widget) -> bool:
        """widget 是否与其他可见窗格重叠（自身组成员除外——整组刚性移动）。"""
        r = widget.frameGeometry()
        grp = widget._group
        for other in self._widgets.values():
            if other is widget or not other.isVisible():
                continue
            if grp is not None and other._group is grp:
                continue
            if self._rects_overlap(r, other.frameGeometry()):
                return True
        return False

    def _find_free_slot(self, x: int, y: int, w: int, h: int,
                        avail, exclude=()):
        """从 (x,y) 起找不与任何可见窗格重叠的空位：向右顺延，超屏换行。"""
        ex = set(id(t) for t in exclude)
        others = [o.frameGeometry() for o in self._widgets.values()
                  if o.isVisible() and id(o) not in ex]
        cx, cy = x, y
        start_x = x
        for _ in range(4096):
            cand = QRect(cx, cy, w, h)
            if not any(self._rects_overlap(cand, r) for r in others):
                return (cx, cy)
            cx += w
            if avail is not None and cx + w > avail.left() + avail.width():
                cx = start_x if start_x >= avail.left() else avail.left()
                cy += h
        return (x, y)

    def _relocate_right_of_first(self, widget) -> None:
        """重叠归位：把窗格（整组）磁吸到首位窗格右边，被占则顺延空位。"""
        try:
            members = (list(widget._group.members)
                       if widget._group is not None else [widget])
            first = self.first_widget(exclude=members)
            if first is None:
                return
            fr = first.frameGeometry()
            br = widget._group.bounding_rect() if widget._group is not None \
                else widget.frameGeometry()
            avail = self._screen_avail_for(fr)
            pos = self._find_free_slot(
                fr.left() + fr.width(), fr.top(), br.width(), br.height(),
                avail, exclude=members)
            delta = QPoint(*pos) - br.topLeft()
            for w in members:
                w.move(w.pos() + delta)
                w.update()
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui._relocate_right_of_first", e)

    def check_overlap_after_drop(self, widget) -> None:
        """松手落位后检测：与他窗重叠 → 自动磁吸到首位窗格右边（V16.2）。"""
        try:
            if widget._snap_animating or not widget.isVisible():
                return
            if self._overlaps_others(widget):
                self._relocate_right_of_first(widget)
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.check_overlap_after_drop", e)

    def restore_groups(self, layout: dict) -> None:
        """重启恢复磁吸组（V16.2）：按布局记忆 group_id 重建组与贴合约束。

        约束按落位几何重建（相邻边贴合检测，容差 2px）；恢复后做重叠校验，
        冲突窗格按归位规则磁吸到首位窗格右边。
        """
        try:
            by_gid: dict = {}
            for key, w in self._widgets.items():
                rec = (layout or {}).get(key)
                gid = rec.get("group_id") if isinstance(rec, dict) else None
                if gid and w.isVisible():
                    by_gid.setdefault(gid, []).append(w)
            for gid, members in by_gid.items():
                if len(members) < 2:
                    continue
                grp = SnapGroup(gid)
                self._groups.append(grp)
                for w in members:
                    grp.add(w)
                    w._is_snapped = True
                    w._snap_constraints = []
                    w._contact_edges = []
                # 相邻边贴合检测重建约束（左右贴→顶对齐、上下贴→左对齐口径）
                for i, a in enumerate(members):
                    ra = a.frameGeometry()
                    for b in members[i + 1:]:
                        rb = b.frameGeometry()
                        edge = None
                        if (abs(ra.left() + ra.width() - rb.left()) <= 2
                                and abs(ra.top() - rb.top()) <= 2):
                            edge = "right"   # a 右贴 b 左
                        elif (abs(rb.left() + rb.width() - ra.left()) <= 2
                                and abs(ra.top() - rb.top()) <= 2):
                            edge = "left"    # a 左贴 b 右
                        elif (abs(ra.top() + ra.height() - rb.top()) <= 2
                                and abs(ra.left() - rb.left()) <= 2):
                            edge = "top"     # a 上贴 b 下（a 在 b 下方）
                        elif (abs(rb.top() + rb.height() - ra.top()) <= 2
                                and abs(ra.left() - rb.left()) <= 2):
                            edge = "bottom"  # a 下贴 b 上（a 在 b 上方）
                        if edge:
                            a._snap_constraints.append((b, edge))
                            a._contact_edges = [edge]
                            b._contact_edges = [self._opposite_edge(edge)]
            # 恢复后重叠校验：冲突窗格归位到首位窗格右边
            for w in list(self._widget_order):
                if w.isVisible() and self._overlaps_others(w):
                    self._relocate_right_of_first(w)
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.restore_groups", e)

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

    def refresh_theme(self, theme: dict) -> None:
        """外观改动（资源监视背景色/呼吸三色）后刷新全部窗格与幽灵窗主题。

        窗格构造时持有主题副本，改色后须显式刷新才生效；只换主题+重绘，
        不动布局/位置/磁吸关系。由 main 在外观应用链路调用。
        """
        try:
            self.theme = dict(theme)
            self._ghost.theme = dict(theme)
            for w in self._widgets.values():
                w.theme = dict(theme)
                w.update()
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.refresh_theme", e)

    def request_expand_width(self) -> None:
        """任一窗格内容自然宽超基准 → 全部窗格统一切到扩宽档（一改变就全改变）。

        用户拍板：不允许窗格大小不一。扩宽只升不降（会话内保持统一宽度）；
        切换后 rescale() 重算全部窗格布局并按磁吸约束重新对齐。
        新建窗格注册时也会检查自身内容，超宽同样触发全局扩宽。
        """
        try:
            if self._width_expanded:
                return
            self._width_expanded = True
            self.rescale()
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.request_expand_width", e)

    # ---- 显示缩放变化：磁吸关系重排（防错位） ----
    def rescale(self) -> None:
        """显示缩放变化后：先重算全部窗格布局，再按贴合关系重新对齐。

        磁吸关系以落位时记录的结构化约束为准（_snap_constraints：目标窗+
        贴合边），不依赖绝对坐标——缩放改变窗格尺寸后，从每组锚点成员
        （首个入组者，位置不动）出发 BFS 重定位：左右贴→顶边对齐、上下贴→
        左缘对齐（与 _edge_candidates 口径一致）；屏幕边吸附重新贴边。
        独立窗只重算尺寸、位置不动。由 main 在外观缩放因子变化时调用。
        """
        try:
            widgets = list(self._widgets.values())
            for w in widgets:
                w.update_layout()
            placed: set = set()
            for grp in list(self._groups):
                members = [w for w in grp.members if w.isVisible()]
                if not members:
                    continue
                anchor = members[0]   # 首个入组者=锚点，缩放后位置不动
                placed.add(id(anchor))
                queue = [anchor]
                while queue:
                    cur = queue.pop(0)
                    cur_r = cur.frameGeometry()
                    for w in members:
                        if id(w) in placed:
                            continue
                        rel = self._constraint_between(w, cur)
                        if rel is None:
                            continue
                        owner, edge = rel
                        r = w.frameGeometry()
                        if owner is w:
                            # w 的 edge 边贴 cur 的对边
                            if edge == "right":
                                pos = (cur_r.left() - r.width(), cur_r.top())
                            elif edge == "left":
                                pos = (cur_r.left() + cur_r.width(), cur_r.top())
                            elif edge == "bottom":
                                pos = (cur_r.left(), cur_r.top() - r.height())
                            else:   # top
                                pos = (cur_r.left(), cur_r.top() + cur_r.height())
                        else:
                            # cur 的 edge 边贴 w 的对边 → 反解 w 位置
                            if edge == "right":
                                pos = (cur_r.left() + cur_r.width(), cur_r.top())
                            elif edge == "left":
                                pos = (cur_r.left() - r.width(), cur_r.top())
                            elif edge == "bottom":
                                pos = (cur_r.left(), cur_r.top() + cur_r.height())
                            else:   # top
                                pos = (cur_r.left(), cur_r.top() - r.height())
                        w.move(*pos)
                        placed.add(id(w))
                        queue.append(w)
            # 组级屏幕贴边：整组按包围盒重新贴边（所有成员同一 delta 刚性平移）
            done_groups: set = set()
            for grp in list(self._groups):
                members = [w for w in grp.members if w.isVisible()]
                if not members:
                    continue
                edge = next((w._group_screen_edge for w in members
                             if w._group_screen_edge), None)
                if not edge or id(grp) in done_groups:
                    continue
                done_groups.add(id(grp))
                self._resnap_group_screen(members, edge)
            # 独立窗屏幕贴边：尺寸变化后重新贴边（交叉轴坐标保持）
            for w in widgets:
                if not w.isVisible() or w._group is not None:
                    continue
                edge = w._screen_edge
                if not edge:
                    continue
                screen = QGuiApplication.screenAt(w.frameGeometry().center())
                if screen is None:
                    continue
                avail = screen.availableGeometry()
                r = w.frameGeometry()
                if edge == "left":
                    w.move(avail.left(), r.top())
                elif edge == "right":
                    w.move(avail.left() + avail.width() - r.width(), r.top())
                elif edge == "top":
                    w.move(r.left(), avail.top())
                elif edge == "bottom":
                    w.move(r.left(), avail.top() + avail.height() - r.height())
        except Exception as e:  # noqa: BLE001
            self.log_fn("monitor_ui.rescale", e)

    @staticmethod
    def _resnap_group_screen(members, edge) -> None:
        """整组按包围盒重新贴屏幕边：算包围盒目标位 → 全员同一 delta 平移。"""
        rect = QRect()
        for w in members:
            rect = rect.united(w.frameGeometry())
        screen = QGuiApplication.screenAt(rect.center())
        if screen is None:
            return
        avail = screen.availableGeometry()
        if edge == "left":
            target = QPoint(avail.left(), rect.top())
        elif edge == "right":
            target = QPoint(avail.left() + avail.width() - rect.width(), rect.top())
        elif edge == "top":
            target = QPoint(rect.left(), avail.top())
        else:   # bottom
            target = QPoint(rect.left(), avail.top() + avail.height() - rect.height())
        delta = target - rect.topLeft()
        for w in members:
            w.move(w.pos() + delta)

    @staticmethod
    def _constraint_between(a, b):
        """a、b 之间的贴合约束：返回 (约束持有者, 贴合边)；无约束返回 None。"""
        for target, edge in getattr(a, "_snap_constraints", []):
            if target is b:
                return (a, edge)
        for target, edge in getattr(b, "_snap_constraints", []):
            if target is a:
                return (b, edge)
        return None

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
