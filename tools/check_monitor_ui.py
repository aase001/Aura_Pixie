# -*- coding: utf-8 -*-
"""monitor_ui 阶段 0+1+2 自检（离屏，无真实硬件/侧车依赖）。

六节：
  A. 契约与骨架——注入接口、UnitRow、配置块默认、空壳冒烟
  B. 假 hub——字段与真实 Metric 完全一致、五态/序列可脚本化注入、
     snapshot 计数、list_disks 传帧复用、与真实 hub 输出交叉验证
  C. 源码看守——禁 import main、禁 QPropertyAnimation、禁 primaryScreen、
     禁 get(key) 拉取、禁 exec() 嵌套（改坏会红）
  D. 环境看守——offscreen 平台、QApplication 先于字体加载
  E. 阶段 1 渲染与布局——窗口标志、三列内容驱动、缩放、光晕像素、动态行高
  F. 阶段 2 数据可视化——五态、阈值插值、呼吸相位、history、一帧一映射

跑法：  python tools/check_monitor_ui.py
退出码： 0 全过 / 1 有失败
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time  # noqa: E402
from collections import deque  # noqa: E402
from dataclasses import fields  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtGui import QFontDatabase, QImage  # noqa: E402

import monitor_hub as mh  # noqa: E402
import monitor_ui as mu  # noqa: E402

PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {msg}")


def raises_not_implemented(fn, msg):
    try:
        fn()
    except NotImplementedError:
        ok(True, msg)
    except Exception as e:  # noqa: BLE001
        ok(False, f"{msg}（抛了 {type(e).__name__} 而非 NotImplementedError）")
    else:
        ok(False, f"{msg}（未抛 NotImplementedError，空壳提前实现了？）")


# ---------------------------------------------------------------------------
# 环境 fixture：必须先建 QApplication 再加载字体（顺序反了原生崩溃，坑 4）
# ---------------------------------------------------------------------------
APP = QApplication.instance() or QApplication([])
_FONT_ID = QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")


# ---------------------------------------------------------------------------
# 假 hub：字段与真实 Metric 完全一致，可脚本化注入序列与五态
# ---------------------------------------------------------------------------
class FakeHub:
    """与 MetricsHub 同契约的假后端：snapshot/list_disks/list_fans/fmt/uptime_fmt。

    - set_metric(key, value, status)：注入单键（label/group/unit 自动取白名单定义）
    - set_frame(dict)：整帧替换
    - queue_frames(list[dict])：脚本化帧序列，snapshot 逐帧推进，末帧驻留
    - snapshot_calls / list_disks_calls：调用计数（一帧一映射看守用）
    """

    def __init__(self):
        self._frame: dict[str, mh.Metric] = {}
        self._queue: list[dict] = []
        self.snapshot_calls = 0
        self.list_disks_calls = 0
        self.start_calls = 0
        self.stop_calls = 0

    # -- 注入 --
    def set_metric(self, key, value, status=mh.STATUS_OK, source="fake", ts=None):
        label, group, unit = self._def_of(key)
        self._frame[key] = mh.Metric(
            key=key, label=label, group=group, value=value, unit=unit,
            status=status, source=source,
            ts=time.time() if ts is None else ts)

    def set_frame(self, mapping):
        """mapping: {key: Metric}（调用方保证字段完整）"""
        self._frame = dict(mapping)

    def queue_frames(self, frames):
        self._queue = list(frames)

    @staticmethod
    def _def_of(key):
        for k, label, group, unit in mh.METRIC_DEFS:
            if k == key:
                return label, group, unit
        parts = key.split(".")
        if parts[0] == "disk" and len(parts) == 3:
            label = "硬盘温度" if parts[2] == "temp" else "硬盘使用率"
            return f"{label}·盘{int(parts[1]) + 1}", "disk", (
                "℃" if parts[2] == "temp" else "%")
        if parts[0] == "fan" and len(parts) == 3:
            return mh._def(key)[1], "fan", "RPM"
        raise KeyError(key)

    # -- 契约出口（与 MetricsHub 同名同签名）--
    def snapshot(self):
        self.snapshot_calls += 1
        if self._queue:
            self._frame = dict(self._queue.pop(0))
        return dict(self._frame)

    def get(self, key):
        # 真实 get() 内部重取全量快照；假 hub 同语义（UI 禁用，仅供交叉验证）
        return self.snapshot().get(key)

    def list_disks(self, snapshot=None):
        self.list_disks_calls += 1
        snap = snapshot if snapshot is not None else self.snapshot()
        out = []
        i = 0
        while f"disk.{i}.usage" in snap or f"disk.{i}.temp" in snap:
            m = snap.get(f"disk.{i}.usage")
            out.append({"index": i, "name": f"FakeDisk{i}",
                        "temp": (snap.get(f"disk.{i}.temp").value
                                 if snap.get(f"disk.{i}.temp") else None),
                        "usage_pct": m.value if m else None,
                        "usage_src": m.source if m else ""})
            i += 1
        return out

    def list_fans(self):
        return [mh.FanInfo(slot=s, index=0, name=f"FakeFan {s}",
                           value=None, status=mh.STATUS_UNSUPPORTED)
                for s in ("cpu", "gpu_d", "mobo")]

    def start(self):
        self.start_calls += 1

    def stop(self):
        self.stop_calls += 1

    fmt = staticmethod(mh.MetricsHub.fmt)
    uptime_fmt = staticmethod(mh.MetricsHub.uptime_fmt)


def make_injections():
    """阶段 0 冒烟用注入件（与 main.py 阶段 5 真实注入同签名）。"""
    return {
        "scale_getter": lambda: 1.0,
        "place_fn": lambda widget, anchor_rect: None,
        "theme": {"color_start": "#FFFFFF", "color_mid": "#283FFE",
                  "color_end": "#FFFFFF", "system_dark_mode": True},
        "topmost_getter": lambda: False,
        "log_fn": lambda where, exc: None,
        "config_io": object(),
    }


# ---------------------------------------------------------------------------
# A. 契约与骨架
# ---------------------------------------------------------------------------
def section_a():
    print("[A] 契约与骨架")
    inj = make_injections()
    hub = FakeHub()

    # 注入接口签名与提示词定死的一致（参数名+顺序）
    import inspect
    w_params = list(inspect.signature(mu.MonitorUnitWidget.__init__).parameters)
    ok(w_params == ["self", "unit_key", "hub", "scale_getter", "place_fn",
                    "theme", "topmost_getter", "log_fn", "manager"],
       f"MonitorUnitWidget 构造签名 {w_params}")
    m_params = list(inspect.signature(mu.MonitorWindowManager.__init__).parameters)
    ok(m_params == ["self", "hub", "scale_getter", "place_fn", "theme",
                    "topmost_getter", "log_fn", "config_io"],
       f"MonitorWindowManager 构造签名 {m_params}")

    # UnitRow 行模型
    r = mu.UnitRow(key="cpu.load", label="CPU使用率", value=42.0,
                   unit="%", status=mh.STATUS_OK)
    ok(isinstance(r.history, deque) and r.history.maxlen == 60,
       "history 为 deque 且 maxlen=60")
    for i in range(75):
        r.history.append(float(i))
    ok(len(r.history) == 60 and r.history[0] == 15.0 and r.history[-1] == 74.0,
       "history 环形淘汰最旧点")
    r.history.append(None)
    ok(r.history[-1] is None, "history 允许 None（断线点）")
    ok(r.available is True, "available：ok+有值")
    r2 = mu.UnitRow("cpu.temp", "CPU温度", None, "℃", mh.STATUS_NEED_ADMIN)
    ok(r2.available is False, "available：need_admin 不可用")
    r3 = mu.UnitRow("cpu.load", "CPU使用率", None, "%", mh.STATUS_OK)
    ok(r3.available is False, "available：ok 但值 None 不可用")
    ok(mu.UnitRow("a", "x", 1.0, "%", mh.STATUS_OK).history
       is not mu.UnitRow("b", "y", 2.0, "%", mh.STATUS_OK).history,
       "各实例 history 独立（default_factory 非共享）")

    # 配置块默认结构
    cfg = mu.default_monitor_config()
    ok(cfg["refresh_ms"] == 1000, "默认 refresh_ms=1000")
    ok(cfg["sparkline_points"] == 60, "默认 sparkline_points=60")
    ok(cfg["idle_stop_sec"] == 300, "默认 idle_stop_sec=300（0=永不停采）")
    ok(cfg["layout"] == {}, "默认 layout 空")
    ok(set(cfg["units"]) == {k for k, *_ in mh.METRIC_DEFS},
       "units 键全集=白名单固定键（动态键运行时展开）")
    ok(all(u["enabled"] is True and u["threshold"] == []
           for u in cfg["units"].values()), "units 默认 enabled+空阈值段")
    cfg["units"]["cpu.load"]["enabled"] = False
    cfg["layout"]["cpu"] = {"x": 1, "y": 2, "group_id": "g"}
    cfg2 = mu.default_monitor_config()
    ok(cfg2["units"]["cpu.load"]["enabled"] is True and cfg2["layout"] == {},
       "默认每次返回全新副本（改旧不污染新）")

    # 空壳冒烟：构造成功、注入件原样持有、状态占位就位
    # （config_io 仅 manager 持有；单元窗注入件不含它）
    mgr = mu.MonitorWindowManager(hub, **inj)
    w_inj = {k: v for k, v in inj.items() if k != "config_io"}
    w = mu.MonitorUnitWidget("cpu", hub, manager=mgr, **w_inj)
    ok(w.unit_key == "cpu" and w.hub is hub and w.manager is mgr,
       "单元窗构造：注入件原样持有")
    ok(w._group is None and w._is_snapped is False
       and w._preview_target is None and w._preview_edge is None,
       "状态机占位初始值")
    ok(mgr._groups == [] and mgr._widgets == {}
       and mgr._timer is not None and not mgr._timer.isActive(),
       "管理器占位初始值（拉取定时器已建未启动）")
    ok(w.scale_getter() == 1.0 and w.topmost_getter() is False,
       "注入回调可调用")

    # 未实现方法一律 NotImplementedError（阶段边界看守；
    # update_layout/set_rows 阶段 1、update_data/tick 阶段 2 已实现，
    # 改由 E/F 节真实验证）
    raises_not_implemented(
        lambda: w.simulate_data_update("cpu.load", 1.0, mh.STATUS_OK),
        "simulate_data_update 未实现")
    raises_not_implemented(lambda: mgr.snap_groups(), "snap_groups 未实现")
    raises_not_implemented(lambda: mgr.simulate_drag(w, 10, 10),
                           "simulate_drag 未实现")

    # 阶段 0 不碰 hub 生命周期（start/stop 归 main.py 阶段 5）
    ok(hub.start_calls == 0 and hub.stop_calls == 0,
       "骨架构造不触发 hub.start/stop")


# ---------------------------------------------------------------------------
# B. 假 hub 驱动能力 + 与真实 hub 交叉验证
# ---------------------------------------------------------------------------
def section_b():
    print("[B] 假 hub 契约与驱动能力")
    hub = FakeHub()

    # 字段与真实 Metric 完全一致（构造同一 dataclass，字段集天然一致；
    # 此处看守注入路径没有丢字段/造错类型）
    hub.set_metric("cpu.load", 42.0, mh.STATUS_OK)
    m = hub.snapshot()["cpu.load"]
    ok(isinstance(m, mh.Metric), "注入产物是真实 Metric 类型")
    ok({f.name for f in fields(m)} ==
       {"key", "label", "group", "value", "unit", "status", "source", "ts"},
       "Metric 八字段齐全")
    ok(m.label == "CPU使用率" and m.group == "cpu" and m.unit == "%",
       "label/group/unit 取自白名单定义（不自造名称）")
    ok(m.source == "fake" and m.ts > 0, "source/ts 有值")
    ok(m.available is True, "available 属性与真实定义一致")

    # 五态全覆盖注入
    states = [mh.STATUS_OK, mh.STATUS_NEED_ADMIN, mh.STATUS_UNSUPPORTED,
              mh.STATUS_NOT_INSTALLED, mh.STATUS_STALE]
    for st in states:
        hub.set_metric("cpu.temp", None if st != mh.STATUS_OK else 63.0, st)
        mm = hub.snapshot()["cpu.temp"]
        ok(mm.status == st, f"注入状态 {st}")
        ok(mm.available == (st == mh.STATUS_OK),
           f"available 口径随 {st}")
    ok(len(states) == 5 and set(states) == {
        mh.STATUS_OK, mh.STATUS_NEED_ADMIN, mh.STATUS_UNSUPPORTED,
        mh.STATUS_NOT_INSTALLED, mh.STATUS_STALE}, "五态常量全集")

    # 脚本化序列：逐帧推进、末帧驻留
    hub2 = FakeHub()
    frames = []
    for v in (10.0, 55.0, 92.0):
        f = {}
        lab, grp, unit = FakeHub._def_of("cpu.load")
        f["cpu.load"] = mh.Metric("cpu.load", lab, grp, v, unit, mh.STATUS_OK)
        frames.append(f)
    hub2.queue_frames(frames)
    seq = [hub2.snapshot()["cpu.load"].value for _ in range(5)]
    ok(seq == [10.0, 55.0, 92.0, 92.0, 92.0],
       f"序列逐帧推进且末帧驻留 {seq}")
    ok(hub2.snapshot_calls == 5, "snapshot 调用计数")

    # 一帧一映射：list_disks 传入已取帧不得再调 snapshot
    hub3 = FakeHub()
    hub3.set_metric("disk.0.temp", 42.0)
    hub3.set_metric("disk.0.usage", 38.6)
    hub3.set_metric("disk.1.temp", 40.0)
    hub3.set_metric("disk.1.usage", 11.4)
    snap = hub3.snapshot()
    calls_before = hub3.snapshot_calls
    disks = hub3.list_disks(snapshot=snap)
    ok(hub3.snapshot_calls == calls_before,
       "list_disks 传帧复用：不重复 snapshot")
    ok([d["index"] for d in disks] == [0, 1]
       and disks[0]["usage_pct"] == 38.6 and disks[1]["temp"] == 40.0,
       "动态盘列表按序展开")
    hub3.list_disks()  # 不传帧 → 内部再取一帧（真实 hub 同语义）
    ok(hub3.snapshot_calls == calls_before + 1,
       "list_disks 不传帧会再取一帧（UI 必须传帧）")

    # 风扇空槽占位（本机无风扇属正常，UI 不得删行）
    fans = hub3.list_fans()
    ok({f.slot for f in fans} == {"cpu", "gpu_d", "mobo"},
       "风扇三槽位占位")

    # 格式化复用真实实现（同一函数对象，口径天然一致）
    ok(FakeHub.fmt is mh.MetricsHub.fmt, "fmt 复用真实实现")
    ok(FakeHub.uptime_fmt is mh.MetricsHub.uptime_fmt, "uptime_fmt 复用真实实现")
    ok(FakeHub.fmt(mh.Metric("cpu.temp", "CPU温度", "cpu", 63.2, "℃",
                             mh.STATUS_OK)) == "63.2 ℃", "fmt 数值口径")
    ok(FakeHub.fmt(mh.Metric("cpu.temp", "CPU温度", "cpu", None, "℃",
                             mh.STATUS_NEED_ADMIN)) == "需管理员",
       "fmt need_admin 口径")
    ok(FakeHub.uptime_fmt(3725) == "1:02:05", "uptime H:MM:SS 口径")

    # 交叉验证：同一 mock 帧喂真实 hub 与假 hub，固定键值/状态一致
    # （真实 hub 用假侧车路径：installed 但永不出帧 → 侧车系指标 stale，
    #  psutil 系指标走真实缓存；此处只比对 psutil 系与状态机口径）
    real = mh.MetricsHub(sidecar_path=__file__)
    real.runner._latest = None
    fake = FakeHub()
    rs = real.snapshot()
    ok(rs["cpu.temp"].status == mh.STATUS_STALE,
       "真实 hub：未出帧→stale（非 need_admin）")
    fake.set_metric("cpu.temp", None, mh.STATUS_STALE)
    fs = fake.snapshot()
    ok(fs["cpu.temp"].status == rs["cpu.temp"].status
       and fs["cpu.temp"].value == rs["cpu.temp"].value,
       "假 hub 与真实 hub 同状态同值（stale 口径交叉一致）")
    ok(rs["sys.uptime"].key == "sys.uptime"
       and rs["sys.uptime"].unit == "s",
       "真实 hub sys.uptime 键/单位（白名单口径）")


# ---------------------------------------------------------------------------
# C. 源码看守（架构红线，改坏会红）
# ---------------------------------------------------------------------------
def section_c():
    print("[C] 源码看守")
    import ast
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "monitor_ui.py"), encoding="utf-8").read()
    lines = [ln for ln in src.splitlines()
             if not ln.lstrip().startswith("#")]
    code = "\n".join(lines)

    # 导入检查走语法树：真实 import 语句才算数，文档字符串里的说明不误报
    bad_imports = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            bad_imports += [a.name for a in node.names
                            if a.name == "main" or a.name.startswith("main.")]
        elif isinstance(node, ast.ImportFrom):
            if node.module == "main" or (node.module or "").startswith("main."):
                bad_imports.append(node.module)
    ok(not bad_imports,
       f"monitor_ui 禁 import main（循环依赖）{bad_imports}")
    ok("QPropertyAnimation" not in code,
       "禁 QPropertyAnimation（动画用 QTimer+QElapsedTimer）")
    ok("primaryScreen" not in code,
       "禁 primaryScreen（多显示器用 screenAt，坑 58）")
    ok("hub.get(" not in code,
       "禁 hub.get(key) 拉取（一帧多映射）")
    ok(".exec(" not in code,
       "禁 exec() 嵌套（坑 6）")
    ok("import monitor_hub" in code, "后端只经 monitor_hub 消费")

    # 白名单键全集与后端定义一致（UI 不得私增/删指标）
    ok(set(mu.default_monitor_config()["units"]) ==
       {k for k, *_ in mh.METRIC_DEFS},
       "配置 units 与 METRIC_DEFS 同步（加键须两侧同改）")


# ---------------------------------------------------------------------------
# D. 环境看守
# ---------------------------------------------------------------------------
def section_d():
    print("[D] 环境看守")
    ok(os.environ.get("QT_QPA_PLATFORM") == "offscreen",
       "离屏平台 offscreen")
    ok(QApplication.instance() is not None, "QApplication 已建立")
    ok(_FONT_ID != -1, "msyh.ttc 字体加载成功（先 app 后字体，坑 4）")


# ---------------------------------------------------------------------------
# E. 阶段 1：渲染与动态布局
# ---------------------------------------------------------------------------
def _make_widget(unit_key="cpu", scale=1.0, topmost=False, theme=None):
    inj = make_injections()
    inj["scale_getter"] = lambda: scale
    inj["topmost_getter"] = lambda: topmost
    if theme is not None:
        inj["theme"] = theme
    hub = FakeHub()
    mgr = mu.MonitorWindowManager(hub, **inj)
    w_inj = {k: v for k, v in inj.items() if k != "config_io"}
    w = mu.MonitorUnitWidget(unit_key, hub, manager=mgr, **w_inj)
    return w, hub, mgr


def _row(key, label, value, unit, status=mh.STATUS_OK):
    return mu.UnitRow(key=key, label=label, value=value, unit=unit, status=status)


def _grab(w):
    """离屏 grab 渲染结果（真实 paintEvent 像素）。"""
    return w.grab().toImage()


def _alpha(img, x, y):
    return (img.pixel(x, y) >> 24) & 0xFF


def section_e():
    print("[E] 阶段 1 渲染与动态布局")
    from PySide6.QtGui import QFontMetrics

    # ---- E1 窗口标志与属性 ----
    w, hub, mgr = _make_widget()
    flags = w.windowFlags()
    ok(bool(flags & Qt.WindowType.FramelessWindowHint), "FramelessWindowHint 标志")
    ok(bool(flags & Qt.WindowType.Tool), "Tool 标志（防任务栏按钮）")
    ok(w.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground),
       "WA_TranslucentBackground")
    ok(w.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground),
       "WA_NoSystemBackground")
    ok(not bool(flags & Qt.WindowType.WindowStaysOnTopHint),
       "topmost_getter=False → 不置顶")
    w2, _, _ = _make_widget(topmost=True)
    ok(bool(w2.windowFlags() & Qt.WindowType.WindowStaysOnTopHint),
       "topmost_getter=True → 置顶（随注入不写死）")
    w2.close()

    # ---- E2 内容驱动尺寸（非写死、show 前非假 640x480）----
    rows = [_row("cpu.load", "CPU使用率", 42.0, "%"),
            _row("cpu.temp", "CPU温度", 63.2, "℃")]
    w.set_rows(rows)
    ok((w.width(), w.height()) != (640, 480),
       f"show 前尺寸非假 640x480（{w.width()}x{w.height()}）")
    ok((w.width(), w.height()) != (200, 40), "尺寸非写死 200x40")
    # 宽度 = 三列内容最大宽 + 2 列间距 + 2 padding（独立用 QFontMetrics 复算）
    fm = QFontMetrics(w._font())
    s = 1.0
    exp_w_label = max(fm.horizontalAdvance(r.label) for r in rows)
    exp_w_value = max(fm.horizontalAdvance(w._value_text(r)) for r in rows)
    exp_w_unit = max(fm.horizontalAdvance(r.unit) for r in rows)
    exp_w = (exp_w_label + exp_w_value + exp_w_unit
             + 2 * round(mu.COL_GAP * s) + 2 * round(mu.PAD_X * s))
    ok(w.width() == exp_w, f"宽=三列内容最大宽推导（{w.width()} vs {exp_w}）")
    ok(w._col_w == (exp_w_label, exp_w_value, exp_w_unit),
       "列宽缓存=各列内容最大宽")

    # ---- E3 内容变化 → 宽度变化 ----
    w_short, _, _ = _make_widget()
    w_short.set_rows([_row("cpu.load", "CPU", 1.0, "%")])
    w_long, _, _ = _make_widget()
    w_long.set_rows([_row("gpu_d.vram_load", "GPU(独显)显存使用率", 88.8, "%")])
    ok(w_long.width() > w_short.width(),
       f"长内容宽 > 短内容宽（{w_long.width()} > {w_short.width()}）")
    w_short.close()
    w_long.close()

    # ---- E4 缩放：绝对像素量乘缩放，行为参数不乘 ----
    w_s1, _, _ = _make_widget(scale=1.0)
    w_s1.set_rows(rows)
    w_s2, _, _ = _make_widget(scale=2.0)
    w_s2.set_rows(rows)
    ok(w_s2.width() > w_s1.width(), f"2x 缩放宽更大（{w_s2.width()} > {w_s1.width()}）")
    ok(w_s2.height() > w_s1.height(), "2x 缩放高更大")
    ok(w_s2._font().pixelSize() == round(mu.FONT_PX * 2.0),
       "字号随缩放（2x）")
    # 缩放现查：同一窗改 scale_getter 后 paint 触发重算（不缓存第二真源）
    scale_box = {"v": 1.0}
    w_dyn, _, _ = _make_widget()
    w_dyn.scale_getter = lambda: scale_box["v"]
    w_dyn.set_rows(rows)
    w_before = w_dyn.width()
    scale_box["v"] = 2.0
    _grab(w_dyn)  # paintEvent 检测缩放变化 → update_layout
    ok(w_dyn.width() > w_before,
       f"缩放现查重算（{w_before} → {w_dyn.width()}）")
    w_dyn.close()
    w_s1.close()
    w_s2.close()

    # ---- E5 动态行增删 → 高度重算 ----
    w_d, _, _ = _make_widget()
    w_d.set_rows([_row("disk.0.temp", "硬盘温度·盘1", 42.0, "℃")])
    h1 = w_d.height()
    w_d.set_rows([_row("disk.0.temp", "硬盘温度·盘1", 42.0, "℃"),
                  _row("disk.0.usage", "硬盘使用率·盘1", 38.6, "%"),
                  _row("disk.1.temp", "硬盘温度·盘2", 40.0, "℃")])
    h3 = w_d.height()
    ok(h3 > h1, f"3 行高 > 1 行高（{h3} > {h1}）")
    w_d.set_rows([_row("disk.0.temp", "硬盘温度·盘1", 42.0, "℃")])
    ok(w_d.height() == h1, "减行回到原高")
    w_d.close()

    # ---- E6 光晕像素：独立态有退晕、吸附态关闭 ----
    # 深色主题，光晕=主题过渡色。grab 真实渲染。
    theme_dark = {"color_start": "#FFFFFF", "color_mid": "#283FFE",
                  "color_end": "#FFFFFF", "system_dark_mode": True}
    w_h, _, _ = _make_widget(theme=theme_dark)
    w_h.set_rows([_row("cpu.load", "CPU使用率", 42.0, "%")])
    img = _grab(w_h)
    W, H = img.width(), img.height()
    halo = round(mu.HALO_PX * 1.0)
    # 四角最外像素：光晕退晕最外层，alpha 应很低（接近 0）
    corner_a = _alpha(img, 0, 0)
    # 本体外围光晕带中点（上边中央，距外缘 halo/2）：应有非零 alpha
    halo_mid_a = _alpha(img, W // 2, max(1, halo // 2))
    # 本体中心：不透明底色
    center_a = _alpha(img, W // 2, H // 2)
    ok(center_a > 150, f"本体中心近不透明（alpha={center_a}）")
    ok(0 < halo_mid_a < center_a,
       f"光晕带 alpha 介于透明与本体间（{halo_mid_a}）")
    ok(corner_a < halo_mid_a,
       f"光晕由外向内渐增（角 {corner_a} < 带中 {halo_mid_a}）")
    # 吸附态：光晕关闭 → 外围光晕带 alpha 显著下降
    w_h._is_snapped = True
    img_snap = _grab(w_h)
    halo_mid_a_snap = _alpha(img_snap, W // 2, max(1, halo // 2))
    ok(halo_mid_a_snap < halo_mid_a,
       f"吸附态光晕关闭（{halo_mid_a_snap} < {halo_mid_a}）")
    ok(_alpha(img_snap, W // 2, H // 2) > 150, "吸附态本体仍在")
    w_h.close()

    # ---- E7 光晕颜色随主题（非深色收边）----
    # 浅色主题光晕应偏浅底，深色主题偏深底；本体色随深浅色切换
    ok(mu.body_color({"system_dark_mode": True}).lightness() < 128,
       "深色系统 → 深底")
    ok(mu.body_color({"system_dark_mode": False}).lightness() > 128,
       "浅色系统 → 浅底")
    ok(mu.text_color({"system_dark_mode": True}).lightness() > 128,
       "深色系统 → 浅字")
    ok(mu.text_color({"system_dark_mode": False}).lightness() < 128,
       "浅色系统 → 深字（白字不可见问题）")
    ok(mu.halo_color({"color_mid": "#FF0000"}).name().upper() == "#FF0000",
       "光晕基色=主题过渡色")

    # ---- E8 数值格式化走 hub.fmt（不自造换算）----
    w_f, _, _ = _make_widget()
    r_up = _row("sys.uptime", "系统运行时间", 3725.0, "s")
    ok(w_f._value_text(r_up) == "1:02:05", "uptime 走 fmt（H:MM:SS）")
    r_na = _row("cpu.temp", "CPU温度", None, "℃", mh.STATUS_NEED_ADMIN)
    ok(w_f._value_text(r_na) == "需管理员", "need_admin 走 fmt 文案")
    r_fan = _row("fan.cpu.0", "CPU风扇转速", 2400.0, "RPM")
    ok(w_f._value_text(r_fan) == "2400 RPM", "风扇整数 RPM 走 fmt")
    w_f.close()

    # ---- E9 崩溃兜底：回调异常经 log_fn 不裸抛 ----
    logged = []
    w_log, _, _ = _make_widget()
    w_log.log_fn = lambda where, exc: logged.append(where)
    w_log.scale_getter = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    w_log.update_layout()  # 内部异常应被兜住
    ok(any("update_layout" in x for x in logged),
       f"update_layout 异常经 log_fn（{logged}）")
    w_log.close()

    # ---- E10 show_at 调 place_fn 再 show ----
    placed = []
    w_p, _, _ = _make_widget()
    w_p.place_fn = lambda widget, anchor: placed.append((widget, anchor))
    w_p.set_rows([_row("cpu.load", "CPU使用率", 42.0, "%")])
    w_p.show_at("ANCHOR")
    ok(len(placed) == 1 and placed[0][1] == "ANCHOR",
       "show_at 调 place_fn(widget, anchor)")
    ok(w_p.isVisible(), "show_at 后窗口可见")
    w_p.close()

    # ---- E11 变高方向同帧 repaint（源码看守，坑 53）----
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "monitor_ui.py"), encoding="utf-8").read()
    ok("self.repaint()" in src and "_last_h" in src,
       "变高方向同帧 repaint 防脏区（_last_h 看守在位）")
    ok("adjustSize()" in src, "布局末尾 adjustSize（坑 50）")

    # ---- E12 光晕绘制路径看守（改坏会红）----
    # paintEvent 必须在「非吸附态」分支调用 _paint_halo；
    # 若删除该调用或去掉吸附判断，E6 像素断言与本源码看守双重变红。
    ok("self._paint_halo(" in src, "paintEvent 调用 _paint_halo（绘制路径在位）")
    ok("if not self._is_snapped:" in src,
       "光晕仅在独立态绘制（吸附态关闭的分支在位）")
    ok("HALO_ALPHA" in src and "setAlphaF" in src,
       "光晕走 alpha 退晕（非深色收边）")

    w.close()


# ---------------------------------------------------------------------------
# F. 阶段 2：数据可视化层
# ---------------------------------------------------------------------------
def section_f():
    print("[F] 阶段 2 数据可视化层")
    from PySide6.QtGui import QColor

    # ---- F1 阈值插值边界 ----
    th = mu.default_thresholds("cpu.load", "%")
    ok(th[0]["max"] == 60 and th[1]["max"] == 85 and th[2]["max"] == 100,
       "百分比默认段 0~60/60~85/85~100（非全局 90%）")
    c0 = mu.get_color_for_value(0.0, th)
    ok(c0.name().upper() == mu.SEM_GREEN.upper(), "0 → 绿")
    c100 = mu.get_color_for_value(100.0, th)
    ok(c100.name().upper() == mu.SEM_RED.upper(), "100 → 红（最高段恒段色）")
    c60 = mu.get_color_for_value(60.0, th)
    ok(c60.name().upper() == mu.SEM_YELLOW.upper(), "60 → 黄（段界）")
    # 段间线性插值：72.5 在 60~85 中点 → 黄红各半
    cmid = mu.get_color_for_value(72.5, th)
    y = QColor(mu.SEM_YELLOW)
    r = QColor(mu.SEM_RED)
    exp_r = round((y.red() + r.red()) / 2)
    ok(abs(cmid.red() - exp_r) <= 1, f"段间线性插值（72.5 红通道 {cmid.red()}≈{exp_r}）")
    ok(mu.get_color_for_value(None, th).red() == 150, "None → 中性灰")
    # 温度/转速/网速各自默认段（不与百分比同）
    ok(mu.default_thresholds("cpu.temp", "℃")[1]["max"] == 80,
       "温度默认段（60/80）")
    ok(mu.default_thresholds("fan.cpu.0", "RPM")[0]["max"] == 2000,
       "转速默认段（2000/3500）")
    ok(mu.default_thresholds("net.rx", "MB/s")[1]["max"] == 50,
       "网速默认段（10/50）")
    ok(not mu.in_top_segment(50.0, mu.default_thresholds("cpu.temp", "℃")),
       "温度 50℃ 不误触发百分比阈值")
    ok(mu.in_top_segment(90.0, th), "百分比 90 进最高段")
    ok(not mu.in_top_segment(50.0, th), "百分比 50 不进最高段")

    # ---- F2 呼吸相位随真实时间推进 ----
    a0 = mu.breath_alpha(0.0)
    a250 = mu.breath_alpha(250.0)    # 1Hz 正弦 1/4 周期 → 峰
    a500 = mu.breath_alpha(500.0)    # 半周期 → 回中
    ok(abs(a0 - 0.65) < 1e-6, f"呼吸起点中值 0.65（{a0}）")
    ok(abs(a250 - 1.0) < 1e-6, f"1/4 周期峰值 1.0（{a250}）")
    ok(abs(a500 - 0.65) < 1e-6, f"半周期回中值（{a500}）")
    ok(mu.BREATH_ALPHA_MIN == 0.3 and mu.BREATH_ALPHA_MAX == 1.0,
       "呼吸幅度 0.3↔1.0")
    ok(mu.BREATH_HZ == 1.0, "呼吸频率 1Hz")

    # ---- F3 五态驱动 + history ----
    w, hub, mgr = _make_widget()
    mgr.register_widget("cpu", w, ["cpu.load", "cpu.temp"])
    w.show()
    APP.processEvents()
    # ok 有值
    hub.set_metric("cpu.load", 42.0, mh.STATUS_OK)
    hub.set_metric("cpu.temp", 63.0, mh.STATUS_OK)
    snap = hub.snapshot()
    for row in w._rows:
        w.update_data(snap[row.key])
    ok(w._rows[0].value == 42.0 and w._rows[0].available, "ok 行有值可用")
    ok(list(w._rows[0].history) == [42.0], "history append 值")
    # 五态：value=None 或 status!=ok → 不可用 + history None
    for st in (mh.STATUS_NEED_ADMIN, mh.STATUS_UNSUPPORTED,
               mh.STATUS_NOT_INSTALLED, mh.STATUS_STALE):
        hub.set_metric("cpu.temp", None, st)
        w.update_data(hub.snapshot()["cpu.temp"])
        row_t = w._rows[1]
        ok(not row_t.available, f"{st} 不可用")
        ok(row_t.history[-1] is None, f"{st} history 记 None（断线）")
        ok(mu.STATUS_TEXT.get(st, "") != "", f"{st} 有状态小字文案")
    ok(mu.STATUS_TEXT[mh.STATUS_NEED_ADMIN] == "需管理员/驱动",
       "need_admin 文案")
    ok(mu.STATUS_TEXT[mh.STATUS_STALE] == "断流", "stale 文案")
    ok(mu.STATUS_TEXT[mh.STATUS_NOT_INSTALLED] == "采集器缺失",
       "not_installed 文案")
    # status=ok 但 value=None 也不可用
    hub.set_metric("cpu.load", None, mh.STATUS_OK)
    w.update_data(hub.snapshot()["cpu.load"])
    ok(not w._rows[0].available, "ok 但值 None 不可用")

    # ---- F4 history 容量与 None 断线 ----
    w2, hub2, mgr2 = _make_widget()
    mgr2.register_widget("cpu", w2, ["cpu.load"])
    w2.show()
    APP.processEvents()
    for i in range(75):
        hub2.set_metric("cpu.load", float(i), mh.STATUS_OK)
        w2.update_data(hub2.snapshot()["cpu.load"])
    ok(len(w2._rows[0].history) == 60, "history 容量 60")
    ok(w2._rows[0].history[0] == 15.0, "history 淘汰最旧")
    # 插入 None 断线点
    hub2.set_metric("cpu.load", None, mh.STATUS_STALE)
    w2.update_data(hub2.snapshot()["cpu.load"])
    ok(w2._rows[0].history[-1] is None, "断线点 None 入 history")

    # ---- F5 高负载呼吸启动/停止 ----
    w3, hub3, mgr3 = _make_widget()
    mgr3.register_widget("cpu", w3, ["cpu.load"])
    w3.show()
    APP.processEvents()
    hub3.set_metric("cpu.load", 50.0, mh.STATUS_OK)
    w3.update_data(hub3.snapshot()["cpu.load"])
    ok(not w3._breathing and not w3._anim_timer.isActive(),
       "50% 未进最高段 → 不呼吸")
    hub3.set_metric("cpu.load", 95.0, mh.STATUS_OK)
    w3.update_data(hub3.snapshot()["cpu.load"])
    ok(w3._breathing and w3._anim_timer.isActive(),
       "95% 进最高段 → 启动呼吸定时器")
    ok(w3._breath_clock.isValid(), "呼吸时钟已启动（相位取真实时间）")
    hub3.set_metric("cpu.load", 30.0, mh.STATUS_OK)
    w3.update_data(hub3.snapshot()["cpu.load"])
    ok(not w3._breathing and not w3._anim_timer.isActive(),
       "离开最高段 → 立即停呼吸")
    # 隐藏停呼吸相位推进
    hub3.set_metric("cpu.load", 95.0, mh.STATUS_OK)
    w3.update_data(hub3.snapshot()["cpu.load"])
    ok(w3._anim_timer.isActive(), "呼吸中")
    w3.hide()
    APP.processEvents()
    ok(not w3._anim_timer.isActive(), "隐藏 → 停呼吸定时器")
    w3.close()

    # ---- F6 每 tick snapshot 只调一次（计数断言）----
    w4, hub4, mgr4 = _make_widget()
    mgr4.register_widget("cpu", w4, ["cpu.load", "cpu.temp"])
    w5, _, _ = _make_widget()
    mgr4.register_widget("mem", w5, ["mem.load"])
    w4.show()
    w5.show()
    APP.processEvents()
    hub4.set_metric("cpu.load", 42.0, mh.STATUS_OK)
    hub4.set_metric("cpu.temp", 63.0, mh.STATUS_OK)
    hub4.set_metric("mem.load", 17.0, mh.STATUS_OK)
    before = hub4.snapshot_calls
    mgr4.tick()
    ok(hub4.snapshot_calls == before + 1,
       f"每 tick 全单元共享一次 snapshot（{before}→{hub4.snapshot_calls}）")
    ok(w4._rows[0].value == 42.0 and w5._rows[0].value == 17.0,
       "同一帧分发到所有可见单元")
    # list_disks 传帧复用：tick 内不额外取帧
    ok(hub4.snapshot_calls == before + 1,
       "tick 内 list_disks 传帧复用（无额外 snapshot）")

    # ---- F7 全隐停定时器 ----
    mgr4.start_polling(1000)
    ok(mgr4._timer.isActive(), "有可见窗 → 拉取定时器运行")
    w4.hide()
    w5.hide()
    APP.processEvents()
    mgr4.tick()
    ok(not mgr4._timer.isActive(), "全部不可见 → 停拉取定时器")
    w4.close()
    w5.close()

    # ---- F8 多盘动态行同步 ----
    w6, hub6, mgr6 = _make_widget()
    mgr6.register_widget("disk", w6, ["disk.0.temp", "disk.0.usage"])
    w6.show()
    APP.processEvents()
    hub6.set_metric("disk.0.temp", 42.0, mh.STATUS_OK)
    hub6.set_metric("disk.0.usage", 38.6, mh.STATUS_OK)
    hub6.set_metric("disk.1.temp", 40.0, mh.STATUS_OK)
    hub6.set_metric("disk.1.usage", 11.4, mh.STATUS_OK)
    mgr6.tick()
    keys = [r.key for r in w6._rows]
    ok(keys == ["disk.0.temp", "disk.0.usage", "disk.1.temp", "disk.1.usage"],
       f"多盘动态行展开（{keys}）")
    ok(w6._rows[2].value == 40.0, "盘2 温度同步")
    w6.close()

    # ---- F9 sparkline 绘制路径源码看守（改坏会红）----
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "monitor_ui.py"), encoding="utf-8").read()
    ok("QPainterPath" in src and "drawPath" in src,
       "sparkline 走 QPainterPath 折线")
    ok("started = False" in src, "None 点断开逻辑在位")
    ok("self.hub.snapshot()" in src and "list_disks(snapshot=snap)" in src,
       "一帧一映射 + list_disks 传帧（源码看守）")
    ok("QElapsedTimer" in src and "breath_alpha" in src,
       "呼吸相位取 QElapsedTimer 真实时间")
    ok("get_color_for_value" in src and "in_top_segment" in src,
       "语义色阈值插值与最高段判定在位")

    # ---- F10 sparkline 真实渲染像素（有历史 vs 空历史）----
    w7, hub7, mgr7 = _make_widget()
    mgr7.register_widget("cpu", w7, ["cpu.load"])
    w7.show()
    APP.processEvents()
    img_empty = _grab(w7)
    for v in (10.0, 40.0, 70.0, 30.0, 90.0, 50.0):
        hub7.set_metric("cpu.load", v, mh.STATUS_OK)
        w7.update_data(hub7.snapshot()["cpu.load"])
    img_line = _grab(w7)
    # 逐像素差异：有折线应产生非零差异（sparkline 真实画出）
    diff = 0
    for yy in range(img_line.height()):
        for xx in range(img_line.width()):
            if img_line.pixel(xx, yy) != img_empty.pixel(xx, yy):
                diff += 1
    ok(diff > 20, f"sparkline 真实渲染产生像素差异（{diff} 像素）")
    w7.close()

    w.close()
    w2.close()


if __name__ == "__main__":
    section_a()
    section_b()
    section_c()
    section_d()
    section_e()
    section_f()
    print(f"\n== PASS {PASS} / FAIL {FAIL} ==")
    sys.exit(1 if FAIL else 0)
