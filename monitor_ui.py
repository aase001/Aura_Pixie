# -*- coding: utf-8 -*-
"""资源监视 UI —— 阶段 0：契约与骨架。

形态（已拍板）：N 个磁吸迷你窗，每指标单元一个顶层 QWidget。
本模块禁止 import main（main 反向 import 本模块，会循环依赖）。
共用能力一律构造注入：hub / scale_getter / place_fn / theme /
topmost_getter / log_fn / config_io（manager 另持 config_io）。

阶段 0 只定契约与骨架，不画任何像素：
  - UnitRow：UI 行模型（history 为 60 点环形缓冲）
  - MonitorUnitWidget / MonitorWindowManager：空壳，方法留 NotImplementedError
  - monitor 配置块默认结构（阶段 5 接线用）

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
# 单元窗骨架（阶段 1~4 填充）
# ---------------------------------------------------------------------------
class MonitorUnitWidget(QWidget):
    """单个指标单元 = 一个顶层迷你窗。

    窗口标志（阶段 1 落实）：FramelessWindowHint | Tool、
    WA_TranslucentBackground + WA_NoSystemBackground、置顶随 topmost_getter()。
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

    # ---- 阶段 1：渲染与布局 ----
    def update_layout(self) -> None:
        raise NotImplementedError("阶段 1：三列内容驱动布局")

    def set_rows(self, rows: list) -> None:
        raise NotImplementedError("阶段 1：动态行增删与高度重算")

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
