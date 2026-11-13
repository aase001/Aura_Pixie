# -*- coding: utf-8 -*-
"""monitor_ui 阶段 0~5 自检（离屏，无真实硬件/侧车依赖）。

九节：
  A. 契约与骨架——注入接口、UnitRow、配置块默认、空壳冒烟
  B. 假 hub——字段与真实 Metric 完全一致、五态/序列可脚本化注入、
     snapshot 计数、list_disks 传帧复用、与真实 hub 输出交叉验证
  C. 源码看守——禁 import main、禁 QPropertyAnimation、禁 primaryScreen、
     禁 get(key) 拉取、禁 exec() 嵌套（改坏会红）
  D. 环境看守——offscreen 平台、QApplication 先于字体加载
  E. 阶段 1 渲染与布局——窗口标志、三列内容驱动、缩放、光晕像素、动态行高
  F. 阶段 2 数据可视化——五态、阈值插值、呼吸相位、history、一帧一映射
  G. 阶段 3 磁吸——拖动→预览→松手落位→入组、移动中不吸附、幽灵穿透、屏幕边
  H. 阶段 4 解绑收口——解绑复位、右键轻点不拆组、四层清扫、全隐停、测试钩子
  I. 阶段 5 接线——配置四函数、注入契约、生命周期、布局记忆（隔离 LOCALAPPDATA）

跑法：  python tools/check_monitor_ui.py
退出码： 0 全过 / 1 有失败
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 自启隔离：接线测试若走保存路径，计划任务/旧启动项清理全部空跑
os.environ["AURA_AUTOSTART_DRYRUN"] = "1"
# 配置隔离：LOCALAPPDATA 指到临时目录，绝不触碰真实配置
_ISOLATED_LOCALAPPDATA = tempfile.mkdtemp(prefix="aura_mon_ui_localappdata_")
os.environ["LOCALAPPDATA"] = _ISOLATED_LOCALAPPDATA

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time  # noqa: E402
from collections import deque  # noqa: E402
from dataclasses import fields  # noqa: E402

from PySide6.QtCore import QPointF, QRect, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402
from PySide6.QtGui import QFontDatabase, QImage, QColor  # noqa: E402

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
        self._history: dict = {}
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
        if parts[0] == "gpu_d" and len(parts) == 3:
            d = mh._def(key)   # 独显全量枚举动态键（复用真实定义还原）
            return d[1], d[2], d[3]
        if parts[0] == "disk" and len(parts) == 3:
            label = "硬盘温度" if parts[2] == "temp" else "硬盘使用率"
            return label, "disk", ("℃" if parts[2] == "temp" else "%")
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

    def list_gpus_d(self, snapshot=None):
        snap = snapshot if snapshot is not None else self.snapshot()
        out = []
        i = 0
        while any(f"gpu_d.{i}.{s}" in snap
                  for s in ("load", "temp", "vram_load", "vram_used")):
            out.append({"index": i, "name": f"FakeGPU{i}"})
            i += 1
        return out

    def list_fans(self):
        return [mh.FanInfo(slot=s, index=0, name=f"FakeFan {s}",
                           value=None, status=mh.STATUS_UNSUPPORTED)
                for s in ("cpu", "gpu_d", "mobo")]

    def history(self, key):
        # 与真实 MetricsHub.history 同契约：返回副本，无记录空列表
        return list(self._history.get(key, []))

    def seed_history(self, key, values):
        self._history[key] = list(values)

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
    ok(cfg["enabled"] is False, "监视总开关默认不勾选")
    _fixed = {k for k, *_ in mh.METRIC_DEFS}
    _dyn = {"gpu_d.0.load", "gpu_d.0.temp", "gpu_d.0.vram_load", "gpu_d.0.vram_used",
            "fan.cpu.0", "fan.gpu_d.0", "fan.mobo.0", "disk.0.temp", "disk.0.usage"}
    ok(set(cfg["units"]) == _fixed | _dyn,
       "units 键全集=白名单固定键+独显卡0+风扇三槽+硬盘0（参数键化）")
    ok("sys.uptime" not in cfg["units"], "运行时间已废弃不列入")
    ok(all(u["enabled"] is False and u["threshold"] == []
           for u in cfg["units"].values()), "units 默认全不勾选+空阈值段")
    cfg["units"]["cpu.load"]["enabled"] = True
    cfg["layout"]["cpu.load"] = {"x": 1, "y": 2, "group_id": "g"}
    cfg2 = mu.default_monitor_config()
    ok(cfg2["units"]["cpu.load"]["enabled"] is False and cfg2["layout"] == {},
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

    # 阶段 0~4 方法均已实现（改由 E/F/G/H 节真实验证）；
    # 阶段 5 集成接线归 main.py，本模块无遗留 NotImplementedError 方法。

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
       {"key", "label", "group", "value", "unit", "status", "source", "ts",
        "hardware", "total"},
       "Metric 十字段齐全（含 hardware/total，V16.2）")
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
    ok("sys.uptime" not in rs, "运行时间已废弃：快照不再产出该键")
    ok(rs["cpu.load"].key == "cpu.load" and rs["cpu.load"].unit == "%",
       "真实 hub cpu.load 键/单位（白名单口径）")


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

    # 白名单键全集与后端定义一致（UI 不得私增/删指标）+ 动态默认键
    _expect = {k for k, *_ in mh.METRIC_DEFS} | {
        "gpu_d.0.load", "gpu_d.0.temp", "gpu_d.0.vram_load", "gpu_d.0.vram_used",
        "fan.cpu.0", "fan.gpu_d.0", "fan.mobo.0", "disk.0.temp", "disk.0.usage"}
    ok(set(mu.default_monitor_config()["units"]) == _expect,
       "配置 units 与 METRIC_DEFS+动态默认键同步（加键须两侧同改）")


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

    # ---- E2 固定基准尺寸（用户拍板 200×40；show 前非假 640x480）----
    rows = [_row("cpu.load", "CPU使用率", 42.0, "%"),
            _row("cpu.temp", "CPU温度", 63.2, "℃")]
    w.set_rows(rows)
    ok((w.width(), w.height()) != (640, 480),
       f"show 前尺寸非假 640x480（{w.width()}x{w.height()}）")
    ok(w.width() == round(mu.BASE_W * 1.0),
       f"基准宽恒 {mu.BASE_W}px（{w.width()}）")
    ok(w.height() == round(mu.BASE_H * 1.0),
       f"基准高恒 {mu.BASE_H}px（{w.height()}）")
    # 固定 200px 档：三列+间距+边距恰好铺满本体不溢出（白名单最长标签
    # 超宽时截断绘制，不撑宽）；列宽按白名单预留、数据跳动不改变尺寸
    fm = QFontMetrics(w._font())
    w_label, w_value, w_unit = w._col_w
    pad_x = round(mu.PAD_X * 1.0)
    content_right = (2 * pad_x + w_label + w_value + w_unit
                     + 2 * w._col_gap)
    ok(content_right <= w.width(),
       f"三列+间距+边距不溢出窗格（{content_right} ≤ {w.width()}）")
    ok(w_label > 0 and w_value > 0 and w_unit > 0, "三列宽均非零")
    # 扩宽档（250px）下白名单最长标签与典型值可完整预留不截断
    w_exp, _, mgr_exp = _make_widget()
    mgr_exp._width_expanded = True
    w_exp.set_rows([_row("gpu_d.vram_load", "GPU显存使用率", 88.8, "%")])
    ok(w_exp.width() == mu.BASE_W_EXPANDED, f"扩宽档窗格 {mu.BASE_W_EXPANDED}px")
    ok(w_exp._col_w[0] >= fm.horizontalAdvance("GPU显存使用率"),
       "扩宽档 A 列完整显示当前行最长标签")
    # 当前行真实内容（88.8）必须完整显示不截断（列宽优先保全当前行）
    ok(w_exp._col_w[1] >= fm.horizontalAdvance(w_exp._value_text(w_exp._rows[0])),
       "扩宽档 B 列完整显示当前行数值")
    ok(w_exp._col_w[2] >= fm.horizontalAdvance("%"), "扩宽档 C 列完整显示单位")
    w_exp.close()

    # ---- E3 数据跳动不改变窗格尺寸（固定基准核心断言）----
    w_st, hub_st, mgr_st = _make_widget()
    mgr_st.register_widget("cpu.load", w_st, ["cpu.load"])
    w_st.show()
    APP.processEvents()
    size0 = (w_st.width(), w_st.height())
    for v in (1.0, 42.0, 100.0, 9999.9):
        hub_st.set_metric("cpu.load", v, mh.STATUS_OK)
        w_st.update_data(hub_st.snapshot()["cpu.load"])
    ok((w_st.width(), w_st.height()) == size0,
       f"数据跳动尺寸不变（{size0} → {(w_st.width(), w_st.height())}）")
    hub_st.set_metric("cpu.load", None, mh.STATUS_NEED_ADMIN)
    w_st.update_data(hub_st.snapshot()["cpu.load"])
    ok((w_st.width(), w_st.height()) == size0, "五态切换尺寸不变（状态小字叠加）")
    ok(not mgr_st._width_expanded, "常规数据不触发全局扩宽")
    w_st.close()

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

    # ---- E5 动态行增删 → 高度恒定（固定 40px，一参数一窗格口径）----
    w_d, _, _ = _make_widget()
    w_d.set_rows([_row("disk.0.temp", "硬盘温度", 42.0, "℃")])
    h1 = w_d.height()
    ok(h1 == round(mu.BASE_H * 1.0), f"单行高=基准 {mu.BASE_H}px（{h1}）")
    w_d.set_rows([_row("disk.0.temp", "硬盘温度", 42.0, "℃"),
                  _row("disk.0.usage", "硬盘使用率", 38.6, "%"),
                  _row("disk.1.temp", "硬盘温度", 40.0, "℃")])
    ok(w_d.height() == h1, "多行高仍=基准（固定窗格不随行增删长高）")
    w_d.set_rows([_row("disk.0.temp", "硬盘温度", 42.0, "℃")])
    ok(w_d.height() == h1, "减行高度不变")
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

    # ---- E8 数值格式化走 hub.fmt_value（纯数值，单位独立 C 列）----
    w_f, _, _ = _make_widget()
    r_pct = _row("cpu.load", "CPU使用率", 42.0, "%")
    ok(w_f._value_text(r_pct) == "42.0", "百分比纯数值（不带单位）")
    r_na = _row("cpu.temp", "CPU温度", None, "℃", mh.STATUS_NEED_ADMIN)
    ok(w_f._value_text(r_na) == "--", "无读数显示 --（状态文案走小字）")
    r_fan = _row("fan.cpu.0", "CPU风扇转速", 2400.0, "RPM")
    ok(w_f._value_text(r_fan) == "2400", "风扇整数 RPM 纯数值")
    r_temp = _row("cpu.temp", "CPU温度", 63.2, "℃")
    ok(w_f._value_text(r_temp) == "63.2", "温度一位小数纯数值")
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

    # ---- F8 新盘发现回调（阶段 A：一参数一窗格，旧多行塞一窗已废弃）----
    w6, hub6, mgr6 = _make_widget()
    mgr6.register_widget("disk.0.temp", w6, ["disk.0.temp"])
    w6.show()
    APP.processEvents()
    discovered = []
    mgr6.on_new_disk_keys = lambda keys: discovered.extend(keys)
    hub6.set_metric("disk.0.temp", 42.0, mh.STATUS_OK)
    hub6.set_metric("disk.0.usage", 38.6, mh.STATUS_OK)
    hub6.set_metric("disk.1.temp", 40.0, mh.STATUS_OK)
    hub6.set_metric("disk.1.usage", 11.4, mh.STATUS_OK)
    mgr6.tick()
    ok(set(discovered) == {"disk.0.temp", "disk.0.usage",
                           "disk.1.temp", "disk.1.usage"},
       f"新盘键经回调通知（{discovered}）")
    ok([r.key for r in w6._rows] == ["disk.0.temp"],
       "窗格保持单参数行（不被塞入多盘行）")
    ok(w6._rows[0].value == 42.0, "盘0 温度同步")
    # 第二次 tick 无新盘 → 不重复回调
    discovered.clear()
    mgr6.tick()
    ok(discovered == [], "无新盘不重复回调")
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

    # ---- F11 曲线底部锚定（V16.3：贴窗底、高度≤窗高2/3、不再悬半腰）----
    w8, hub8, mgr8 = _make_widget()
    mgr8.register_widget("cpu.load", w8, ["cpu.load"])
    w8.show()
    APP.processEvents()
    # 锯齿历史：min/max 归一化后折线必触及曲线区顶/底（非平线）
    for v in (0.0, 100.0, 0.0, 100.0, 0.0, 100.0, 0.0, 100.0):
        hub8.set_metric("cpu.load", v, mh.STATUS_OK)
        w8.update_data(hub8.snapshot()["cpu.load"])
    # 捕获 drawPath 的 QPainterPath 包围盒（曲线几何真源，不受抗锯齿干扰）
    from PySide6.QtGui import QPainter
    paths = []
    orig_dp = QPainter.drawPath

    def _spy_path(self, path):
        paths.append(path.boundingRect())
        return orig_dp(self, path)

    QPainter.drawPath = _spy_path
    try:
        _grab(w8)
    finally:
        QPainter.drawPath = orig_dp
    ok(len(paths) >= 1, f"曲线真实绘制（drawPath {len(paths)} 次）")
    if paths:
        bb = paths[0]
        H = w8.height()
        band_top = H - round(mu.HALO_PX * 1.0) - round(mu.SPARK_BOTTOM_GAP_PX * 1.0) \
            - round(mu.BASE_H * 1.0 * mu.SPARK_H_RATIO)
        ok(bb.bottom() >= H - 10,
           f"曲线贴窗底渲染（底 {bb.bottom():.1f} vs 窗高 {H}，旧口径悬半腰必红）")
        ok(bb.top() >= band_top - 2,
           f"曲线区顶不超窗高2/3上限（顶 {bb.top():.1f} ≥ {band_top - 2}）")
        ok(bb.height() <= round(mu.BASE_H * 1.0 * mu.SPARK_H_RATIO) + 2,
           f"曲线区高不超窗高2/3（{bb.height():.1f}px）")
    # 源码看守：绘制顺序=曲线先、文字后（重叠时曲线在文字下层）
    src_f11 = open(os.path.join(root, "monitor_ui.py"), encoding="utf-8").read()
    ok(src_f11.index("self._paint_sparklines(p, body, s)")
       < src_f11.index("self._paint_rows(p, body, s)"),
       "曲线先画文字后画（重叠时曲线在文字下层）")
    ok("SPARK_H_RATIO" in src_f11 and "SPARK_BOTTOM_GAP_PX" in src_f11,
       "底部锚定常量在位（固定 SPARK_H 预留已废弃）")
    w8.close()

    w.close()
    w2.close()


# ---------------------------------------------------------------------------
# G. 阶段 3：磁吸预览与松手吸附
# ---------------------------------------------------------------------------
def _mouse_ev(etype, gpos):
    from PySide6.QtGui import QMouseEvent
    return QMouseEvent(etype, QPointF(gpos), QPointF(gpos),
                       Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)


def _send(w, etype, gpos):
    APP.sendEvent(w, _mouse_ev(etype, gpos))


def _two_widgets(mgr, hub):
    """建两个注册到同一 manager 的单元窗（离屏显式摆放）。"""
    inj = make_injections()
    w_inj = {k: v for k, v in inj.items() if k != "config_io"}
    wa = mu.MonitorUnitWidget("cpu", hub, manager=mgr, **w_inj)
    wb = mu.MonitorUnitWidget("mem", hub, manager=mgr, **w_inj)
    mgr.register_widget("cpu", wa, ["cpu.load"])
    mgr.register_widget("mem", wb, ["mem.load"])
    return wa, wb


def section_g():
    print("[G] 阶段 3 磁吸预览与松手吸附")
    from PySide6.QtCore import QEvent, QPoint, QRect
    from PySide6.QtGui import QMouseEvent  # noqa: F401

    hub = FakeHub()
    inj = make_injections()
    mgr = mu.MonitorWindowManager(hub, **inj)
    wa, wb = _two_widgets(mgr, hub)
    # 显式摆放：A 在 (100,100)，B 在 A 右侧远处
    wa.move(100, 100)
    wb.move(600, 100)
    wa.show()
    wb.show()
    APP.processEvents()
    wa_w = wa.width()
    wa_h = wa.height()

    # ---- G1 SnapGroup 基本行为 ----
    g = mgr.new_group()
    ok(g.group_id and g.members == [], "新组空成员")
    g.add(wa)
    ok(wa._group is g and wa in g.members, "入组指向组")
    g.add(wa)
    ok(len(g.members) == 1, "重复入组不叠加")
    br = g.bounding_rect()
    ok(br == wa.frameGeometry(), "单成员包围盒=自身")
    g.remove(wa)
    ok(wa._group is None and wa not in g.members, "出组清指向")

    # ---- G2 拖动阈值：位移<4px 不成立 ----
    start = wa.frameGeometry().topLeft() + QPoint(5, 5)
    _send(wa, QEvent.Type.MouseButtonPress, start)
    _send(wa, QEvent.Type.MouseMove, start + QPoint(2, 0))
    ok(not wa._drag_started, "位移 2px<4 不成立拖动")
    ok(wa.pos() == QPoint(100, 100), "未拖动位置不变")
    _send(wa, QEvent.Type.MouseButtonRelease, start + QPoint(2, 0))

    # ---- G3 移动中预览出现，但位置不被吸附改写 ----
    # 把 B 摆到 A 右边附近（A 右缘 + 15px 间隙），拖 A 向右靠近 B
    wb.move(100 + wa_w + 15, 100)
    APP.processEvents()
    start = wa.frameGeometry().topLeft() + QPoint(5, 5)
    _send(wa, QEvent.Type.MouseButtonPress, start)
    # 拖动 A 使其右缘距 B 左缘 < 20px：A 移到 x=100+wa_w+15-wa_w-5 = 110? 直接算
    # 目标：A.right 与 B.left 间隙 10px → A.x = B.x - wa_w - 10
    target_x = wb.x() - wa_w - 10
    move_to = QPoint(target_x + 5, 105)   # grab 偏移 5,5
    _send(wa, QEvent.Type.MouseMove, move_to)
    ok(wa._drag_started, "位移超阈值拖动成立")
    ok(wa._preview_target is wb, f"预览目标=B（{wa._preview_target}）")
    ok(wa._preview_edge == "right", f"预览贴合边=right（{wa._preview_edge}）")
    ok(not wa._preview_strong, "间隙 10px 属弱档（10~20px，非 <10）")
    # 关键：移动中位置=跟手位置，绝不被吸附改写
    ok(wa.pos() == QPoint(target_x, 100),
       f"移动中位置不被吸附改写（{wa.pos()} vs {(target_x, 100)}）")
    ok(mgr._ghost.isVisible(), "幽灵轮廓可见")
    ok(mgr._ghost.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents),
       "幽灵窗鼠标穿透")
    ok(mgr._ghost.geometry() == wa._preview_rect, "幽灵窗画在贴合位")

    # ---- G4 松手吸附：280ms 动画落位入组 ----
    _send(wa, QEvent.Type.MouseButtonRelease, move_to)
    ok(wa._snap_animating, "松手启动吸附动画")
    ok(not mgr._ghost.isVisible(), "松手即藏幽灵")
    # 动画期间禁止再次拖动打断
    _send(wa, QEvent.Type.MouseButtonPress, move_to)
    ok(not wa._dragging, "动画期间按下不启动拖动")
    _send(wa, QEvent.Type.MouseButtonRelease, move_to)
    # 推进动画到结束（真实 QTimer 相位法，processEvents 驱动）
    import time as _t
    t0 = _t.perf_counter()
    while wa._snap_animating and _t.perf_counter() - t0 < 2.0:
        APP.processEvents()
        _t.sleep(0.01)
    ok(not wa._snap_animating, "动画在 280ms 量级结束")
    # 落位：A 右缘贴 B 左缘（left+width 口径，无 1px 缝）
    a_right = wa.frameGeometry().left() + wa.frameGeometry().width()
    ok(a_right == wb.frameGeometry().left(),
       f"落位 A 右缘贴 B 左缘（{a_right} vs {wb.frameGeometry().left()}）")
    ok(wa._is_snapped and wb._is_snapped, "落位双方吸附态")
    ok(wa._group is not None and wa._group is wb._group, "落位同组")
    ok(wa in wa._group.members and wb in wa._group.members, "组成员齐全")
    ok(wa._contact_edges == ["right"], f"A 接触边 right（{wa._contact_edges}）")
    ok(wb._contact_edges == ["left"], f"B 接触边 left（{wb._contact_edges}）")
    ok(wa._preview_target is None and wa._preview_rect is None, "落位清预览")
    # 交叉轴对齐（阶段 A）：左右贴合 → 顶边对齐
    ok(wa.frameGeometry().top() == wb.frameGeometry().top(),
       f"左右贴合顶边对齐（{wa.frameGeometry().top()} vs {wb.frameGeometry().top()}）")
    # 上下贴合 → 左缘对齐（直接验证候选几何）
    from PySide6.QtCore import QRect as _QR
    _my = _QR(100, 100, 80, 60)
    _tg = _QR(120, 300, 90, 70)
    _cands = wa._edge_candidates(_my, _tg)
    _bottom = [sr for d, e, sr in _cands if e == "bottom"]
    _top = [sr for d, e, sr in _cands if e == "top"]
    ok(_bottom and _bottom[0].left() == _tg.left(),
       "上下贴合左缘对齐（bottom 候选）")
    ok(_top and _top[0].left() == _tg.left(),
       "上下贴合左缘对齐（top 候选）")
    # right 候选需垂直投影重叠：另构造水平相邻、垂直重叠的矩形
    _my2 = _QR(100, 310, 80, 60)
    _tg2 = _QR(200, 300, 90, 70)
    _cands2 = wa._edge_candidates(_my2, _tg2)
    _right = [sr for d, e, sr in _cands2 if e == "right"]
    ok(_right and _right[0].top() == _tg2.top(),
       "左右贴合顶边对齐（right 候选）")

    # ---- G5 吸附态关光晕（像素）----
    img_snapped = _grab(wa)
    halo = round(mu.HALO_PX * 1.0)
    # 吸附态本体外围光晕带 alpha 应很低（光晕关闭）
    a_band = _alpha(img_snapped, wa.width() // 2, max(1, halo // 2))
    ok(a_band < 40, f"吸附态光晕关闭（带 alpha={a_band}）")

    # ---- G6 组内拖动同步偏移 ----
    grp = wa._group
    wa_pos_before = wa.pos()
    wb_pos_before = wb.pos()
    start = wa.frameGeometry().topLeft() + QPoint(5, 5)
    _send(wa, QEvent.Type.MouseButtonPress, start)
    _send(wa, QEvent.Type.MouseMove, start + QPoint(40, 30))
    ok(wa.pos() == wa_pos_before + QPoint(40, 30), "组内拖动 A 偏移")
    ok(wb.pos() == wb_pos_before + QPoint(40, 30), "组内拖动 B 同步偏移")
    _send(wa, QEvent.Type.MouseButtonRelease, start + QPoint(40, 30))

    # ---- G7 屏幕边吸附语义（贴边记 screen_edge，不入窗间组）----
    wc = mu.MonitorUnitWidget("net", hub, manager=mgr,
                              **{k: v for k, v in inj.items() if k != "config_io"})
    mgr.register_widget("net", wc, ["net.rx"])
    # 摆到屏幕左上角附近，向左拖到贴左边
    scr = APP.primaryScreen().availableGeometry()
    wc.move(scr.left() + 12, scr.top() + 200)
    wc.show()
    APP.processEvents()
    start = wc.frameGeometry().topLeft() + QPoint(5, 5)
    _send(wc, QEvent.Type.MouseButtonPress, start)
    _send(wc, QEvent.Type.MouseMove, QPoint(scr.left() + 5 + 3, scr.top() + 205))
    ok(wc._preview_target == "screen", f"屏幕边预览目标=screen（{wc._preview_target}）")
    ok(wc._preview_edge == "left", f"屏幕边=left（{wc._preview_edge}）")
    _send(wc, QEvent.Type.MouseButtonRelease, QPoint(scr.left() + 8, scr.top() + 205))
    t0 = _t.perf_counter()
    while wc._snap_animating and _t.perf_counter() - t0 < 2.0:
        APP.processEvents()
        _t.sleep(0.01)
    ok(wc._screen_edge == "left", f"屏幕边吸附记 screen_edge（{wc._screen_edge}）")
    ok(wc._group is None, "屏幕边吸附不入窗间组")
    ok(wc._is_snapped, "屏幕边吸附态")
    ok(wc.frameGeometry().left() == scr.left(), "贴屏幕左缘")
    wc.close()

    # ---- G8 无预览目标松手 → 原地停留 ----
    wd = mu.MonitorUnitWidget("gpu_d", hub, manager=mgr,
                              **{k: v for k, v in inj.items() if k != "config_io"})
    mgr.register_widget("gpu_d", wd, ["gpu_d.load"])
    wd.move(2000, 2000)   # 远离其它窗与屏幕边
    wd.show()
    APP.processEvents()
    pos_before = wd.pos()
    start = wd.frameGeometry().topLeft() + QPoint(5, 5)
    _send(wd, QEvent.Type.MouseButtonPress, start)
    _send(wd, QEvent.Type.MouseMove, start + QPoint(30, 20))
    ok(wd._preview_target is None, "远处无预览目标")
    _send(wd, QEvent.Type.MouseButtonRelease, start + QPoint(30, 20))
    ok(not wd._snap_animating, "无目标不启动吸附动画")
    ok(wd.pos() == pos_before + QPoint(30, 20), "无目标原地停留（跟手位）")
    ok(not wd._is_snapped, "无目标不进吸附态")
    wd.close()

    # ---- G9 源码看守（改坏会红）----
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "monitor_ui.py"), encoding="utf-8").read()
    # 只查真实代码调用（剔除注释行），文档里「不用 primaryScreen」说明不误伤
    code_lines = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]
    code_only = "\n".join(code_lines)
    ok("screenAt" in code_only and "primaryScreen(" not in code_only,
       "屏幕判定用 screenAt 非 primaryScreen（坑 58）")
    ok("SNAP_ANIM_MS" in src and "3 - 2 * t" in src,
       "松手 280ms 缓入缓出（smoothstep 相位法）")
    ok("WA_TransparentForMouseEvents" in src, "幽灵窗鼠标穿透属性")
    ok("class SnapGroup" in src and "class GhostOverlay" in src,
       "SnapGroup 普通类 + 独立幽灵 overlay")
    ok("_clear_preview" in src and "hide_ghost" in src,
       "预览清理路径在位")
    ok("DRAG_THRESHOLD_PX" in src and "manhattanLength" in src,
       "拖动判定阈值 4px（行为参数不乘缩放）")

    wa.close()
    wb.close()


# ---------------------------------------------------------------------------
# H. 阶段 4：解绑与状态机收口
# ---------------------------------------------------------------------------
def _snapped_pair(mgr, hub):
    """建两个已吸附成组的单元窗（A 右贴 B 左）。"""
    wa, wb = _two_widgets(mgr, hub)
    wa.move(100, 100)
    wb.move(100 + wa.width(), 100)
    wa.show()
    wb.show()
    APP.processEvents()
    grp = mgr.new_group()
    grp.add(wa)
    grp.add(wb)
    wa._is_snapped = True
    wb._is_snapped = True
    wa._contact_edges = ["right"]
    wb._contact_edges = ["left"]
    return wa, wb, grp


def section_h():
    print("[H] 阶段 4 解绑与状态机收口")
    from PySide6.QtCore import QEvent, QPoint

    hub = FakeHub()
    inj = make_injections()
    mgr = mu.MonitorWindowManager(hub, **inj)

    # ---- H1 解绑：组剩余==1 解散，剩余成员复位光晕 ----
    wa, wb, grp = _snapped_pair(mgr, hub)
    ok(grp in mgr.snap_groups(), "组在管理器列表")
    wa.detach_from_group()
    ok(wa._group is None and not wa._is_snapped, "解绑自身清组与吸附态")
    ok(wa._contact_edges == [], "解绑自身清接触边")
    # 关键：剩余成员 wb 必须复位（漏这步会永久残留融合态）
    ok(wb._group is None, "剩余成员 wb 组已解散")
    ok(not wb._is_snapped, "剩余成员 wb 吸附态复位 False")
    ok(wb._contact_edges == [], "剩余成员 wb 接触边清空")
    ok(grp not in mgr.snap_groups(), "解散组从管理器列表移除")
    # 光晕恢复像素：wb 独立态本体外围光晕带 alpha 回升
    img = _grab(wb)
    halo = round(mu.HALO_PX * 1.0)
    a_band = _alpha(img, wb.width() // 2, max(1, halo // 2))
    ok(a_band > 5, f"解绑后剩余成员光晕恢复（带 alpha={a_band}）")

    # ---- H2 三成员组解绑一个 → 组保留（剩余 2）----
    wc = mu.MonitorUnitWidget("net", hub, manager=mgr,
                              **{k: v for k, v in inj.items() if k != "config_io"})
    mgr.register_widget("net", wc, ["net.rx"])
    wa.move(300, 300)
    wb.move(300 + wa.width(), 300)
    wc.move(300 + wa.width() + wb.width(), 300)
    wc.show()
    APP.processEvents()
    g3 = mgr.new_group()
    for w in (wa, wb, wc):
        g3.add(w)
        w._is_snapped = True
    wa.detach_from_group()
    ok(len(g3.members) == 2 and g3 in mgr.snap_groups(),
       "三成员解绑一个 → 组保留剩余 2")
    ok(wb._is_snapped and wc._is_snapped, "剩余 2 成员仍吸附（未误解散）")
    ok(wa._group is None, "被解绑者独立")

    # ---- H3 右键拖拽解绑全链路（sendEvent）----
    # 重建吸附对，用右键拖动 wa 解绑
    wa._group = None
    wb._group = None
    g3b = mgr.new_group()
    g3b.add(wa)
    g3b.add(wb)
    wa._is_snapped = True
    wb._is_snapped = True
    wa.move(500, 500)
    wb.move(500 + wa.width(), 500)
    APP.processEvents()
    # 远距离解绑（落点远离 wb 与屏幕边，无磁吸目标）→ 独立跟手原位停留
    mgr.simulate_drag(wa, 400, 300, button=Qt.MouseButton.RightButton)
    ok(wa._group is None, "右键拖动解绑自身")
    ok(wb._group is None and not wb._is_snapped,
       "右键拖动解绑 → 剩余成员复位")
    ok(wa.pos() == QPoint(900, 800), f"右键解绑后独立跟手（{wa.pos()}）")
    # V16.2 防重叠：把解绑窗摆到与 wb 重叠处 → 归位首位窗格(wb)右边
    wa.move(500 + wa.width() - 20, 500)
    APP.processEvents()
    ok(mgr._overlaps_others(wa), "解绑窗与剩余窗重叠")
    mgr.check_overlap_after_drop(wa)
    ok(not mgr._overlaps_others(wa), "重叠归位后无重叠")
    ok(wa.frameGeometry().left() >= wb.frameGeometry().left() + wb.width(),
       "归位到首位窗格右边")

    # ---- H4 右键轻点（位移≤4px）不拆组 + 弹菜单 ----
    wa.move(700, 700)
    wb.move(700 + wa.width(), 700)
    g4 = mgr.new_group()
    g4.add(wa)
    g4.add(wb)
    wa._is_snapped = True
    wb._is_snapped = True
    APP.processEvents()
    start = wa.frameGeometry().topLeft() + QPoint(5, 5)
    _send(wa, QEvent.Type.MouseButtonPress, start)  # 左键占位无关
    # 用右键轻点
    from PySide6.QtGui import QMouseEvent
    def rev(etype, gpos):
        return QMouseEvent(etype, QPointF(gpos), QPointF(gpos),
                           Qt.MouseButton.RightButton, Qt.MouseButton.RightButton,
                           Qt.KeyboardModifier.NoModifier)
    APP.sendEvent(wa, rev(QEvent.Type.MouseButtonPress, start))
    APP.sendEvent(wa, rev(QEvent.Type.MouseButtonRelease, start + QPoint(2, 1)))
    ok(wa._group is g4 and wa._is_snapped, "右键轻点不拆组")
    ok(len(g4.members) == 2, "右键轻点组成员不变")
    # 菜单已弹出（QMenu 实例存在且可见）
    menus = [ch for ch in wa.children() if isinstance(ch, QMenu)]
    ok(any(m.isVisible() for m in menus), "右键轻点弹出最小菜单")
    menu = next(m for m in menus if m.isVisible())
    acts = [a.text() for a in menu.actions()]
    ok("关闭本窗" in acts and "打开设置" in acts, f"菜单项齐全（{acts}）")
    menu.close()

    # ---- H5 菜单回调非模态（singleShot）+ 设置回调 ----
    called = []
    wa.set_settings_callback(lambda: called.append("settings"))
    wa._open_settings()
    ok(called == ["settings"], "设置回调触发")
    # 源码看守：菜单回调走 singleShot 非模态，禁 exec 嵌套
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "monitor_ui.py"), encoding="utf-8").read()
    ok("QTimer.singleShot(0" in src and "menu.popup" in src,
       "菜单回调 singleShot 非模态 + popup（非 exec）")
    ok(".exec(" not in src, "禁 exec() 嵌套（坑 6）")
    ok("def contextMenuEvent" in src, "未整体屏蔽 contextMenuEvent")

    # ---- H6 四层残留清扫 ----
    wa._preview_target = wb
    wa._preview_edge = "right"
    wa._preview_rect = QRect(0, 0, 10, 10)
    mgr.show_ghost(QRect(0, 0, 10, 10), True)
    wa._snap_animating = True
    wa.clear_interaction_state()
    ok(wa._preview_target is None and wa._preview_edge is None
       and wa._preview_rect is None, "清扫预览目标/边/贴合位")
    ok(not mgr._ghost.isVisible(), "清扫幽灵窗")
    ok(not wa._snap_animating and not wa._snap_anim_timer.isActive(),
       "清扫动画定时器")

    # ---- H7 关闭窗清四层 + 解绑 + 可见性联动 ----
    wa.move(900, 900)
    wb.move(900 + wa.width(), 900)
    g7 = mgr.new_group()
    g7.add(wa)
    g7.add(wb)
    wa._is_snapped = True
    mgr.start_polling(1000)
    ok(mgr._timer.isActive(), "有可见窗定时器运行")
    wa.close()
    APP.processEvents()
    ok(wa._group is None, "关闭窗解绑")
    ok(not wa._snap_animating, "关闭窗清动画态")
    # wb 仍可见 → 定时器不停
    ok(mgr._timer.isActive(), "尚有可见窗 → 定时器不停")
    wb.close()
    wc.close()
    APP.processEvents()
    ok(not mgr._timer.isActive(), "全部关闭 → 停拉取定时器")

    # ---- H8 测试钩子 simulate_data_update 走真实路径 ----
    w8 = mu.MonitorUnitWidget("cpu", hub, manager=mgr,
                              **{k: v for k, v in inj.items() if k != "config_io"})
    mgr.register_widget("cpu", w8, ["cpu.load"])
    w8.show()
    APP.processEvents()
    w8.simulate_data_update("cpu.load", 55.0, mh.STATUS_OK)
    ok(w8._rows[0].value == 55.0 and w8._rows[0].available,
       "simulate_data_update 走真实 update_data")
    ok(list(w8._rows[0].history) == [55.0], "演示钩子 history 入值")
    w8.simulate_data_update("cpu.load", None, mh.STATUS_NEED_ADMIN)
    ok(not w8._rows[0].available and w8._rows[0].history[-1] is None,
       "演示钩子五态断线")
    w8.close()

    # ---- H9 源码看守：解绑复位与状态机守卫（改坏会红）----
    ok("len(grp.members) == 1" in src and "last._is_snapped = False" in src,
       "组剩余==1 解散且剩余成员复位（漏检会红）")
    ok("def clear_interaction_state" in src and "def detach_from_group" in src,
       "四层清扫与解绑方法在位")
    ok("def closeEvent" in src and "clear_interaction_state()" in src,
       "关闭窗清四层")

    wa.close()
    wb.close()


# ---------------------------------------------------------------------------
# I. 阶段 5：集成接线与生命周期（import main，隔离 LOCALAPPDATA）
# ---------------------------------------------------------------------------
def section_i():
    print("[I] 阶段 5 集成接线与生命周期")
    import main as M

    # ---- I1 配置四函数：default 含 monitor 块 ----
    d = M.default_settings()
    ok("monitor" in d and isinstance(d["monitor"], dict),
       "default_settings 含 monitor 块")
    ok(d["monitor"]["refresh_ms"] == 1000
       and d["monitor"]["idle_stop_sec"] == 300,
       "monitor 默认拉取/闲置参数")
    _expect = {k for k, *_ in mh.METRIC_DEFS} | {
        "gpu_d.0.load", "gpu_d.0.temp", "gpu_d.0.vram_load", "gpu_d.0.vram_used",
        "fan.cpu.0", "fan.gpu_d.0", "fan.mobo.0", "disk.0.temp", "disk.0.usage"}
    ok(set(d["monitor"]["units"]) == _expect,
       "monitor units 键全集=白名单+动态默认键")
    ok(d["monitor"]["enabled"] is False, "monitor 总开关默认不勾选")

    # ---- I2 _merge_config：monitor 块字段级合并 + 缺键回退 ----
    base = M.default_settings()
    src = {"monitor": {"refresh_ms": 2000, "enabled": True,
                       "units": {"cpu.load": {"enabled": True}},
                       "layout": {"cpu.load": {"x": 10, "y": 20, "group_id": "g1"}}}}
    merged = M._merge_config(base, src)
    ok(merged["monitor"]["refresh_ms"] == 2000, "monitor 源字段覆盖")
    ok(merged["monitor"]["enabled"] is True, "monitor 总开关源字段覆盖")
    ok(merged["monitor"]["idle_stop_sec"] == 300, "monitor 缺键回退默认")
    ok(merged["monitor"]["units"]["cpu.load"]["enabled"] is True,
       "monitor units 子块合并")
    ok(merged["monitor"]["units"]["cpu.temp"]["enabled"] is False,
       "monitor units 未改键保留默认（不勾选）")
    ok(merged["monitor"]["layout"]["cpu.load"]["x"] == 10, "monitor layout 合并")
    # 无 monitor 源 → 保留 base 默认（不丢块）
    merged2 = M._merge_config(M.default_settings(), {"functions": []})
    ok("monitor" in merged2 and merged2["monitor"]["refresh_ms"] == 1000,
       "无 monitor 源保留默认块")

    # ---- I3 load/save 往返（隔离配置；base 指临时目录，不扫描项目根）----
    M.ensure_settings(base=tempfile.mkdtemp(prefix="aura_mon_root_"))
    cfg = M.load_monitor_config()
    ok(cfg["refresh_ms"] == 1000, "load_monitor_config 默认")
    cfg["refresh_ms"] = 1500
    cfg["idle_stop_sec"] = 120
    M.save_monitor_config(cfg)
    cfg2 = M.load_monitor_config()
    ok(cfg2["refresh_ms"] == 1500 and cfg2["idle_stop_sec"] == 120,
       "save/load 往返一致")
    # save_monitor_layout 只动 layout，不动拉取参数
    M.save_monitor_layout({"mem": {"x": 5, "y": 6, "group_id": None}})
    cfg3 = M.load_monitor_config()
    ok(cfg3["refresh_ms"] == 1500, "save_layout 不动拉取参数")
    ok(cfg3["layout"]["mem"]["x"] == 5, "save_layout 写入布局")

    # ---- I4 注入契约：scale/theme/topmost/place/log/config_io ----
    ok(abs(M._monitor_scale_getter() - M.display_scale_factor("monitor")) < 1e-9,
       "scale_getter 包 display_scale_factor('monitor')")
    th = M._monitor_theme()
    ok(th["color_mid"] == M.CONFIG["color_mid"]
       and "system_dark_mode" in th, "theme 包呼吸三色+深浅色")
    ok(M._monitor_topmost_getter() == M.topmost_enabled(),
       "topmost_getter 包 window.always_on_top")
    ok(callable(M._monitor_place_fn) and callable(M._monitor_log_fn),
       "place_fn/log_fn 可调用")
    # monitor_ui 仍禁 import main（循环依赖看守）
    mu_src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "monitor_ui.py"), encoding="utf-8").read()
    import ast
    bad = [n.module for n in ast.walk(ast.parse(mu_src))
           if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("main")]
    ok(not bad, "monitor_ui 仍禁 import main（接线后不回归）")

    # ---- I5 SCALE_UNITS tooltip 更新为已实现 ----
    mon_label = dict(M.SCALE_UNITS).get("monitor")
    ok(mon_label == "资源监视单元", "SCALE_UNITS monitor 单元在位")
    main_src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "main.py"), encoding="utf-8").read()
    ok("monitor_ui 已实现" in main_src, "monitor tooltip 注释更新为已实现")

    # ---- I6 生命周期（阶段 A）：总开关门禁 + 参数级窗格 + 关窗不停采 ----
    # 不替换 MetricsHub 类（monitor_ui 内部用 mh.MetricsHub.fmt_value）；
    # 先正常创建 manager，再把 hub 实例换成假 hub（避免启动侧车进程）。
    M._MONITOR_MGR = None
    M._MONITOR_HUB = None
    M._MONITOR_SETTINGS_DLG = None
    mgr = M._ensure_monitor_manager()
    fake = FakeHub()
    real_hub = M._MONITOR_HUB
    M._MONITOR_HUB = fake
    mgr.hub = fake
    try:
        ok(mgr is not None, "惰性创建 manager")
        ok(fake.start_calls == 0, "创建 manager 不启动采集")
        # 总开关默认关 → open_monitor 弹设置窗 + 预览采集（取设备名），但不建窗格
        M.open_monitor()
        APP.processEvents()
        ok(fake.start_calls == 1, "打开设置窗 → 预览采集启动（V16.2 取设备名）")
        ok(not any(w.isVisible() for w in mgr._widgets.values()),
           "总开关未启用 → 无监视窗格（预览不建窗）")
        ok(M._MONITOR_SETTINGS_DLG is not None, "open_monitor 弹监视设置窗")
        M._MONITOR_SETTINGS_DLG.hide()
        APP.processEvents()
        ok(fake.stop_calls >= 1, "关设置窗且无窗格未启用 → 预览采集停采释放")
        # 启用总开关 + 勾选两个参数 → 同步建窗 + start
        cfg = M.load_monitor_config()
        cfg["enabled"] = True
        cfg["units"]["cpu.load"]["enabled"] = True
        cfg["units"]["mem.load"]["enabled"] = True
        M.save_monitor_config(cfg)
        M._monitor_sync_windows(cfg)
        ok(fake.start_calls == 2, "总开关启用首开 → hub.start() 再次")
        ok(set(mgr._widgets) == {"cpu.load", "mem.load"},
           f"一参数一窗格（{set(mgr._widgets)}）")
        ok(all(w.isVisible() for w in mgr._widgets.values()), "勾选参数窗格可见")
        ok(mgr._timer.isActive(), "拉取定时器运行")
        # 每个窗格只有 1 行（一参数一窗格核心断言）
        ok(all(len(w._rows) == 1 for w in mgr._widgets.values()),
           "每窗格仅 1 个参数行")
        # 取消一个参数 → 对应窗格关闭，另一个保留
        cfg["units"]["mem.load"]["enabled"] = False
        M.save_monitor_config(cfg)
        M._monitor_sync_windows(cfg)
        ok(not mgr._widgets["mem.load"].isVisible(), "取消勾选 → 窗格关闭")
        ok(mgr._widgets["cpu.load"].isVisible(), "其余窗格保留")
        # 关窗不停采：隐藏剩余窗 → 停拉取但 hub 不停（增量口径，排除预览停采计数）
        _stop_before_hide = fake.stop_calls
        mgr._widgets["cpu.load"].hide()
        APP.processEvents()
        mgr.tick()
        ok(not mgr._timer.isActive(), "全隐 → 停拉取定时器")
        ok(fake.stop_calls == _stop_before_hide, "关窗不停采（hub.stop 未新增）")
        # 重开 → 历史回填秒回曲线
        fake.seed_history("cpu.load", [10.0, 20.0, 30.0])
        cfg["units"]["cpu.load"]["enabled"] = True
        M._monitor_sync_windows(cfg)
        row = mgr._widgets["cpu.load"]._rows[0]
        ok(list(row.history)[:3] == [10.0, 20.0, 30.0],
           "重开历史回填（秒回曲线）")
        # 总开关关闭 → 全窗关闭 + 停采
        cfg["enabled"] = False
        M.save_monitor_config(cfg)
        M._monitor_sync_windows(cfg)
        ok(not any(w.isVisible() for w in mgr._widgets.values()),
           "总开关关闭 → 全部窗格关闭")
        ok(fake.stop_calls >= 1, "总开关关闭 → hub.stop()")
        # 退出 → shutdown_monitor 必达 stop
        M.shutdown_monitor()
        ok(fake.stop_calls >= 2, "退出 → hub.stop() 必达")
    finally:
        for w in list(mgr._widgets.values()):
            w.close()
        if M._MONITOR_SETTINGS_DLG is not None:
            M._MONITOR_SETTINGS_DLG.close()
        real_hub.stop()
        M._MONITOR_MGR = None
        M._MONITOR_HUB = None
        M._MONITOR_SETTINGS_DLG = None

    # ---- I7 内置扇区动作 monitor: 前缀识别 ----
    ok("monitor:" in main_src and "open_monitor" in main_src,
       "扇区 monitor: 动作入口在位")
    ok("act_monitor" in main_src and 'menu.addAction("监视")' in main_src,
       "右键菜单监视入口在位（已改名）")
    ok("aboutToQuit.connect(shutdown_monitor)" in main_src,
       "退出钩子接线在位")

    # ---- I8 布局记忆落盘函数在位（移动停止 10s 先例）----
    ok("def _monitor_layout_settle" in main_src
       and "save_monitor_layout" in main_src,
       "布局记忆 10s 落盘接线在位")
    ok("def _monitor_idle_check" in main_src and "idle_stop_sec" in main_src,
       "闲置停采接线在位")


# ---------------------------------------------------------------------------
# J. 阶段 A：监视设置窗（总开关 + 参数复选 + 实时渲染 + 保存语义）
# ---------------------------------------------------------------------------
def section_j():
    print("[J] 阶段 A 监视设置窗")
    import main as M

    # 重置监视配置为出厂默认（I 节曾写入勾选状态，隔离配置共享需清场）
    M.save_monitor_config(M.monitor_ui.default_monitor_config())
    M._MONITOR_MGR = None
    M._MONITOR_HUB = None
    M._MONITOR_SETTINGS_DLG = None
    mgr = M._ensure_monitor_manager()
    fake = FakeHub()
    real_hub = M._MONITOR_HUB
    M._MONITOR_HUB = fake
    mgr.hub = fake
    try:
        dlg = M.MonitorSettingsDialog()
        # J1 结构：总开关 + 参数复选框齐全，默认全不勾选
        ok(dlg.chk_enable.text() == "启用资源监视", "总开关文案")
        ok(not dlg.chk_enable.isChecked(), "总开关默认不勾选")
        _expect = {k for k, *_ in mh.METRIC_DEFS} | {
            "gpu_d.0.load", "gpu_d.0.temp", "gpu_d.0.vram_load", "gpu_d.0.vram_used",
            "fan.cpu.0", "fan.gpu_d.0", "fan.mobo.0", "disk.0.temp", "disk.0.usage"}
        ok(set(dlg._param_checks) == _expect,
           f"参数复选框齐全（{len(dlg._param_checks)} 项）")
        ok("sys.uptime" not in dlg._param_checks, "运行时间已废弃不罗列")
        ok(all(not c.isChecked() for c in dlg._param_checks.values()),
           "参数默认全不勾选")
        ok(all(not c.isEnabled() for c in dlg._param_checks.values()),
           "总开关未勾选 → 参数复选置灰")
        # 无值参数也罗列（风扇/硬盘）
        ok("fan.cpu.0" in dlg._param_checks and "disk.0.temp" in dlg._param_checks,
           "无值参数（风扇/硬盘）也罗列")
        # J2 总开关勾选 → 参数复选启用
        dlg.chk_enable.setChecked(True)
        ok(all(c.isEnabled() for c in dlg._param_checks.values()),
           "总开关勾选 → 参数复选可用")
        # J3 参数勾选 → 实时写配置 + 建窗（总开关已启用）
        dlg._param_checks["cpu.load"].setChecked(True)
        APP.processEvents()
        cfg = M.load_monitor_config()
        ok(cfg["units"]["cpu.load"]["enabled"] is True, "勾选实时写配置")
        ok("cpu.load" in mgr._widgets and mgr._widgets["cpu.load"].isVisible(),
           "勾选实时渲染窗格")
        ok(fake.start_calls == 1, "首个窗格拉起采集")
        # J4 取消勾选 → 实时关窗
        dlg._param_checks["cpu.load"].setChecked(False)
        APP.processEvents()
        ok(not mgr._widgets["cpu.load"].isVisible(), "取消勾选实时关窗")
        # J5 保存：总开关勾选 + 参数 → 落盘 + 同步
        dlg._param_checks["mem.load"].setChecked(True)
        dlg._on_save()
        APP.processEvents()
        cfg = M.load_monitor_config()
        ok(cfg["enabled"] is True and cfg["units"]["mem.load"]["enabled"] is True,
           "保存落盘总开关与参数")
        ok(mgr._widgets["mem.load"].isVisible(), "保存后窗格可见")
        # J6 保存：总开关取消 → 全窗关 + 停采
        dlg.chk_enable.setChecked(False)
        dlg._on_save()
        APP.processEvents()
        cfg = M.load_monitor_config()
        ok(cfg["enabled"] is False, "保存总开关关闭落盘")
        ok(not any(w.isVisible() for w in mgr._widgets.values()),
           "总开关关闭保存 → 全窗关")
        ok(fake.stop_calls >= 1, "总开关关闭保存 → 停采")
        # J7 设备名小字（V16.2）：有设备名的参数下方均有小字标签
        _hw_keys = {k for k in dlg._param_checks
                    if k.startswith(("cpu.", "gpu_i.", "gpu_d.", "disk."))}
        ok(set(dlg._hw_labels) == _hw_keys,
           f"设备名小字覆盖全部有设备名参数（{len(dlg._hw_labels)} 项）")
        ok(all(k not in dlg._hw_labels for k in dlg._param_checks
               if k.startswith(("mem.", "net.", "fan."))),
           "内存/网络/风扇无设备名小字")
        ok(all(lab.text() == "—" for lab in dlg._hw_labels.values()),
           "无快照时设备名显示占位符而非旧名")
        ok(all(not lab.isEnabled() for lab in dlg._hw_labels.values()),
           "总开关关闭 → 设备名小字置灰")
        # J7b 字号防塌缩（V16.2 灰线根因）：默认字体点单位 pixelSize()=-1，
        # 旧 max(1, pixelSize()-N) 塌成 1px 渲染成灰线；设备名固定 9px（用户拍板），
        # 须可读且小于参数名字号（改坏会红）。
        from PySide6.QtGui import QFontMetricsF as _QFMF
        _param_px = _QFMF(dlg._param_checks["cpu.load"].font()).height()
        _hw_px = {k: lab.font().pixelSize() for k, lab in dlg._hw_labels.items()}
        ok(all(px == 9 for px in _hw_px.values()),
           f"设备名字号固定 9px（实得 {sorted(set(_hw_px.values()))}）")
        ok(all(px < _param_px for px in _hw_px.values()),
           "设备名字号小于参数名")
        # J7c 恒定中灰（V16.2 用户拍板）：不随主题变，深浅底都可见（改坏会红）
        ok(all("#808080" in lab.styleSheet() for lab in dlg._hw_labels.values()),
           "设备名颜色恒定中灰 #808080（不依赖主题）")
        # J8 设备名重载：注入硬件名 → 刷新为新名（改坏会红）
        fake.set_metric("cpu.load", 10.0, mh.STATUS_OK)
        fake._frame["cpu.load"].hardware = "i7-TEST"
        fake.set_metric("disk.0.temp", 40.0, mh.STATUS_OK)
        fake._frame["disk.0.temp"].hardware = "FakeDisk0"
        dlg.refresh_hardware_names()
        ok(dlg._hw_labels["cpu.load"].text() == "i7-TEST",
           "设备名从实时快照重载（CPU）")
        ok(dlg._hw_labels["disk.0.temp"].text() == "FakeDisk0",
           "设备名从实时快照重载（硬盘）")
        # J9 换设备名再刷新 → 跟随变化（防残留旧名）
        fake._frame["cpu.load"].hardware = "i7-NEW"
        dlg.refresh_hardware_names()
        ok(dlg._hw_labels["cpu.load"].text() == "i7-NEW", "换设备名后刷新为新名")
        # J10 设备名不落盘：配置与布局文件中不含设备名字段
        cfg_txt = open(M.CONFIG_PATH, encoding="utf-8").read()
        ok("i7-NEW" not in cfg_txt and "FakeDisk0" not in cfg_txt,
           "设备名不写入配置文件")
        # J11 管理器硬件名变化回调接线在位
        ok(mgr.on_hardware_names is not None, "硬件名变化回调已注入管理器")
        # J12 全选复选框（V16.2）：结构 + 默认不勾 + 随总开关置灰
        ok(dlg.chk_select_all.text() == "全选/全不选", "全选复选框文案")
        ok(not dlg.chk_select_all.isChecked(), "全选默认不勾选")
        ok(not dlg.chk_select_all.isEnabled(), "总开关关闭 → 全选置灰")
        dlg.chk_enable.setChecked(True)
        ok(dlg.chk_select_all.isEnabled(), "总开关勾选 → 全选可用")
        # J13 全选勾选 → 全部参数勾上 + 实时建窗（批量一次同步）
        dlg.chk_select_all.setChecked(True)
        APP.processEvents()
        ok(all(c.isChecked() for c in dlg._param_checks.values()),
           "全选勾选 → 全部参数勾上")
        cfg = M.load_monitor_config()
        ok(all((cfg["units"].get(k) or {}).get("enabled")
               for k in dlg._param_checks), "全选实时写配置（全部启用）")
        ok(all(mgr._widgets[k].isVisible() for k in dlg._param_checks
               if k in mgr._widgets), "全选实时建窗（全部可见）")
        # J14 全不选 → 全部取消 + 实时关窗
        dlg.chk_select_all.setChecked(False)
        APP.processEvents()
        ok(all(not c.isChecked() for c in dlg._param_checks.values()),
           "全不选 → 全部参数取消")
        ok(not any(w.isVisible() for w in mgr._widgets.values()),
           "全不选实时关窗（全部隐藏）")
        # J15 单参数反向联动：逐个全勾 → 全选自动勾上；取消一个 → 全选落下
        for chk in dlg._param_checks.values():
            chk.setChecked(True)
        APP.processEvents()
        ok(dlg.chk_select_all.isChecked(), "逐个全勾 → 全选态自动勾上")
        dlg._param_checks["cpu.load"].setChecked(False)
        APP.processEvents()
        ok(not dlg.chk_select_all.isChecked(), "取消一个 → 全选态落下")
        # J16 全选态下新盘/新卡自动勾上
        dlg.chk_select_all.setChecked(True)
        APP.processEvents()
        fake.set_metric("disk.1.temp", 40.0, mh.STATUS_OK)
        fake.set_metric("disk.1.usage", 50.0, mh.STATUS_OK)
        dlg.refresh_disk_params()
        APP.processEvents()
        ok("disk.1.temp" in dlg._param_checks
           and dlg._param_checks["disk.1.temp"].isChecked(),
           "全选态下新盘参数自动勾上")
        ok(dlg.chk_select_all.isChecked(), "新盘加入后全选态保持")
        # J17 首帧晚到 → 可见期间定时补刷新自动补上设备名（本轮修复）
        dlg.show()
        APP.processEvents()
        ok(dlg._hw_refresh_timer.isActive(), "设置窗可见 → 设备名补刷新定时器运行")
        fake._frame.clear()   # 模拟打开窗时采集尚未出帧
        dlg.refresh_hardware_names()
        ok(dlg._hw_labels["cpu.load"].text() == "—", "无帧时设备名占位「—」")
        fake.set_metric("cpu.load", 10.0, mh.STATUS_OK)
        fake._frame["cpu.load"].hardware = "i7-LATE"
        dlg._on_hw_refresh_tick()   # 定时补刷新触发（首帧晚到）
        ok(dlg._hw_labels["cpu.load"].text() == "i7-LATE",
           "首帧晚到 → 定时补刷新自动补上设备名")
        dlg.hide()
        APP.processEvents()
        ok(not dlg._hw_refresh_timer.isActive(), "设置窗隐藏 → 补刷新定时器停止")
        # J18 重置按钮（V16.3）：全不选 + 清位置记忆 + 重勾从初始位置渲染
        from PySide6.QtCore import QPoint
        ok(dlg.btn_reset.text() == "重置", "重置按钮文案")
        dlg.chk_enable.setChecked(True)
        dlg.chk_select_all.setChecked(True)   # 全选 → 建窗
        APP.processEvents()
        # 人为记忆位（模拟拖动后落盘）+ 组记忆
        for i, (k, w) in enumerate(mgr._widgets.items()):
            w.move(400 + i * 10, 300)
        cfg_mem = M.load_monitor_config()
        cfg_mem["layout"] = {k: {"x": 400, "y": 300, "group_id": "g9"}
                             for k in list(mgr._widgets)[:2]}
        M.save_monitor_config(cfg_mem)
        dlg._on_reset()
        APP.processEvents()
        ok(all(not c.isChecked() for c in dlg._param_checks.values()),
           "重置 → 全部参数取消勾选")
        cfg_r = M.load_monitor_config()
        ok(all(not (cfg_r["units"].get(k) or {}).get("enabled")
               for k in dlg._param_checks), "重置 → 配置全部停用")
        ok(cfg_r.get("layout") == {}, "重置 → 位置记忆清空落盘")
        ok(not any(w.isVisible() for w in mgr._widgets.values()),
           "重置 → 全部窗格隐藏")
        ok(all(w._group is None for w in mgr._widgets.values()),
           "重置 → 磁吸组解散")
        ok(all(w.pos() == QPoint(0, 0) for w in mgr._widgets.values()),
           "重置 → 窗格位置归零（未摆放哨兵）")
        ok(dlg.chk_enable.isChecked(), "重置不动总开关（保持勾选）")
        ok(cfg_r.get("enabled") is True, "重置后总开关配置保持启用")
        ok(not dlg.chk_select_all.isChecked(), "重置后全选态落下")
        # 重勾参数 → 初始摆放（不再回到旧记忆位 400,300）
        dlg._param_checks["cpu.load"].setChecked(True)
        APP.processEvents()
        w_cpu = mgr._widgets["cpu.load"]
        ok(w_cpu.isVisible(), "重置后重勾 → 窗格重新渲染")
        ok(w_cpu.pos() != QPoint(400, 300),
           f"重勾走初始摆放而非旧记忆位（{w_cpu.pos()}）")
        dlg._param_checks["cpu.load"].setChecked(False)
        APP.processEvents()
        dlg.close()
    finally:
        for w in list(mgr._widgets.values()):
            w.close()
        real_hub.stop()
        M._MONITOR_MGR = None
        M._MONITOR_HUB = None
        M._MONITOR_SETTINGS_DLG = None


# ---------------------------------------------------------------------------
# K. 固定窗格 + 全局扩宽同步 + 缩放磁吸重排（本轮修复）
# ---------------------------------------------------------------------------
def section_k():
    print("[K] 固定窗格 / 全局扩宽同步 / 缩放磁吸重排")
    from PySide6.QtCore import QEvent, QPoint

    # ---- K1 全局扩宽：任一窗格内容超基准 → 所有窗格统一 250px ----
    hub = FakeHub()
    inj = make_injections()
    mgr = mu.MonitorWindowManager(hub, **inj)
    w_inj = {k: v for k, v in inj.items() if k != "config_io"}
    wa = mu.MonitorUnitWidget("cpu.load", hub, manager=mgr, **w_inj)
    wb = mu.MonitorUnitWidget("mem.load", hub, manager=mgr, **w_inj)
    mgr.register_widget("cpu.load", wa, ["cpu.load"])
    mgr.register_widget("mem.load", wb, ["mem.load"])
    wa.show()
    wb.show()
    APP.processEvents()
    ok(not mgr._width_expanded, "初始未扩宽")
    ok(wa.width() == mu.BASE_W and wb.width() == mu.BASE_W,
       f"初始两窗均基准宽（{wa.width()}/{wb.width()}）")
    # 给 A 注入超长标签行（自然宽 > 200）→ 触发全局扩宽
    wa.set_rows([_row("cpu.load", "超长标签" * 12, 42.0, "%")])
    ok(mgr._width_expanded, "A 内容超基准 → 管理器全局扩宽标志置位")
    ok(wa.width() == mu.BASE_W_EXPANDED, f"A 扩到 {mu.BASE_W_EXPANDED}px")
    ok(wb.width() == mu.BASE_W_EXPANDED,
       f"B 未超宽也同步扩到 {mu.BASE_W_EXPANDED}px（一改变就全改变）")
    # 扩宽只升不降：短内容回注不缩回
    wa.set_rows([_row("cpu.load", "CPU使用率", 42.0, "%")])
    ok(wa.width() == mu.BASE_W_EXPANDED and wb.width() == mu.BASE_W_EXPANDED,
       "扩宽后不缩回（会话内统一宽度）")
    # 扩宽后新建窗格也按扩宽档渲染
    wc = mu.MonitorUnitWidget("net.rx", hub, manager=mgr, **w_inj)
    mgr.register_widget("net.rx", wc, ["net.rx"])
    ok(wc.width() == mu.BASE_W_EXPANDED, "扩宽后新建窗格同宽")
    wa.close()
    wb.close()
    wc.close()

    # ---- K2 数据路径触发全局扩宽（update_data 真实路径）----
    hub2 = FakeHub()
    mgr2 = mu.MonitorWindowManager(hub2, **inj)
    wd = mu.MonitorUnitWidget("cpu.load", hub2, manager=mgr2, **w_inj)
    we = mu.MonitorUnitWidget("mem.load", hub2, manager=mgr2, **w_inj)
    mgr2.register_widget("cpu.load", wd, ["cpu.load"])
    mgr2.register_widget("mem.load", we, ["mem.load"])
    wd.show()
    we.show()
    APP.processEvents()
    # 用超长 label 的 Metric 走 update_data（label 取自 metric）
    m_long = mh.Metric("cpu.load", "超长标签" * 12, "cpu", 42.0, "%", mh.STATUS_OK)
    wd.update_data(m_long)
    ok(mgr2._width_expanded, "update_data 超宽 → 全局扩宽")
    ok(wd.width() == mu.BASE_W_EXPANDED and we.width() == mu.BASE_W_EXPANDED,
       "数据路径扩宽两窗同步")
    wd.close()
    we.close()

    # ---- K3 缩放重排：磁吸组缩放后仍贴合对齐 ----
    hub3 = FakeHub()
    scale_box = {"v": 1.0}
    inj3 = make_injections()
    inj3["scale_getter"] = lambda: scale_box["v"]
    mgr3 = mu.MonitorWindowManager(hub3, **inj3)
    w_inj3 = {k: v for k, v in inj3.items() if k != "config_io"}
    wx = mu.MonitorUnitWidget("cpu.load", hub3, manager=mgr3, **w_inj3)
    wy = mu.MonitorUnitWidget("mem.load", hub3, manager=mgr3, **w_inj3)
    mgr3.register_widget("cpu.load", wx, ["cpu.load"])
    mgr3.register_widget("mem.load", wy, ["mem.load"])
    wx.move(100, 100)
    wy.move(100 + wx.width(), 100)   # B 在 A 右侧贴合
    wx.show()
    wy.show()
    APP.processEvents()
    # 建组并记录贴合约束（B 的 left 边贴 A）
    grp = mgr3.new_group()
    grp.add(wx)
    grp.add(wy)
    wx._is_snapped = True
    wy._is_snapped = True
    wx._contact_edges = ["right"]
    wy._contact_edges = ["left"]
    wy._snap_constraints = [(wx, "left")]
    # 缩放 1.0 → 2.0：窗格尺寸翻倍，rescale 后仍须贴合
    scale_box["v"] = 2.0
    mgr3.rescale()
    ok(wx.width() == round(mu.BASE_W * 2.0), f"缩放后 A 宽=基准×2（{wx.width()}）")
    ok(wy.width() == round(mu.BASE_W * 2.0), "缩放后 B 宽=基准×2")
    x_right = wx.frameGeometry().left() + wx.frameGeometry().width()
    ok(x_right == wy.frameGeometry().left(),
       f"缩放后 A 右缘仍贴 B 左缘（{x_right} vs {wy.frameGeometry().left()}）")
    ok(wx.frameGeometry().top() == wy.frameGeometry().top(),
       "缩放后左右贴顶边仍对齐")
    ok(wx.pos() == QPoint(100, 100), "锚点 A 位置不动")
    # 缩回 1.0：仍贴合
    scale_box["v"] = 1.0
    mgr3.rescale()
    x_right = wx.frameGeometry().left() + wx.frameGeometry().width()
    ok(x_right == wy.frameGeometry().left(), "缩回后仍贴合")
    wx.close()
    wy.close()

    # ---- K4 缩放重排：上下贴合左缘对齐 ----
    hub4 = FakeHub()
    scale_box4 = {"v": 1.0}
    inj4 = make_injections()
    inj4["scale_getter"] = lambda: scale_box4["v"]
    mgr4 = mu.MonitorWindowManager(hub4, **inj4)
    w_inj4 = {k: v for k, v in inj4.items() if k != "config_io"}
    wp = mu.MonitorUnitWidget("cpu.load", hub4, manager=mgr4, **w_inj4)
    wq = mu.MonitorUnitWidget("mem.load", hub4, manager=mgr4, **w_inj4)
    mgr4.register_widget("cpu.load", wp, ["cpu.load"])
    mgr4.register_widget("mem.load", wq, ["mem.load"])
    wp.move(200, 200)
    wq.move(200, 200 + wp.height())   # B 在 A 下方贴合
    wp.show()
    wq.show()
    APP.processEvents()
    grp4 = mgr4.new_group()
    grp4.add(wp)
    grp4.add(wq)
    wp._is_snapped = True
    wq._is_snapped = True
    wp._contact_edges = ["bottom"]
    wq._contact_edges = ["top"]
    wq._snap_constraints = [(wp, "top")]
    scale_box4["v"] = 1.5
    mgr4.rescale()
    p_bottom = wp.frameGeometry().top() + wp.frameGeometry().height()
    ok(p_bottom == wq.frameGeometry().top(),
       f"缩放后 A 下缘仍贴 B 上缘（{p_bottom} vs {wq.frameGeometry().top()}）")
    ok(wp.frameGeometry().left() == wq.frameGeometry().left(),
       "缩放后上下贴左缘仍对齐")
    wp.close()
    wq.close()

    # ---- K5 缩放重排：屏幕边吸附重新贴边 ----
    hub5 = FakeHub()
    scale_box5 = {"v": 1.0}
    inj5 = make_injections()
    inj5["scale_getter"] = lambda: scale_box5["v"]
    mgr5 = mu.MonitorWindowManager(hub5, **inj5)
    w_inj5 = {k: v for k, v in inj5.items() if k != "config_io"}
    ws = mu.MonitorUnitWidget("net.rx", hub5, manager=mgr5, **w_inj5)
    mgr5.register_widget("net.rx", ws, ["net.rx"])
    scr = APP.primaryScreen().availableGeometry()
    ws.move(scr.left(), scr.top() + 300)
    ws.show()
    APP.processEvents()
    ws._screen_edge = "left"
    ws._is_snapped = True
    scale_box5["v"] = 2.0
    mgr5.rescale()
    ok(ws.frameGeometry().left() == scr.left(), "缩放后仍贴屏幕左缘")
    ws.close()

    # ---- K6 解绑清约束：解绑后缩放不再误拉回 ----
    hub6 = FakeHub()
    scale_box6 = {"v": 1.0}
    inj6 = make_injections()
    inj6["scale_getter"] = lambda: scale_box6["v"]
    mgr6 = mu.MonitorWindowManager(hub6, **inj6)
    w_inj6 = {k: v for k, v in inj6.items() if k != "config_io"}
    wt = mu.MonitorUnitWidget("cpu.load", hub6, manager=mgr6, **w_inj6)
    wu = mu.MonitorUnitWidget("mem.load", hub6, manager=mgr6, **w_inj6)
    mgr6.register_widget("cpu.load", wt, ["cpu.load"])
    mgr6.register_widget("mem.load", wu, ["mem.load"])
    wt.move(400, 400)
    wu.move(400 + wt.width(), 400)
    wt.show()
    wu.show()
    APP.processEvents()
    grp6 = mgr6.new_group()
    grp6.add(wt)
    grp6.add(wu)
    wt._is_snapped = True
    wu._is_snapped = True
    wu._snap_constraints = [(wt, "left")]
    wu.detach_from_group()
    ok(wu._snap_constraints == [], "解绑清贴合约束")
    wu.move(900, 900)   # 解绑后拖远
    scale_box6["v"] = 2.0
    mgr6.rescale()
    ok(wu.pos() == QPoint(900, 900), "解绑窗缩放后位置不被拉回")
    wt.close()
    wu.close()

    # ---- K7 源码看守（改坏会红）----
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "monitor_ui.py"), encoding="utf-8").read()
    ok("def request_expand_width" in src and "_width_expanded" in src,
       "全局扩宽入口与标志在位")
    ok("def rescale" in src and "_snap_constraints" in src,
       "缩放重排与贴合约束在位")
    ok("_text_outgrows_cols" not in src, "旧「变宽即撑宽」逻辑已移除")
    main_src = open(os.path.join(root, "main.py"), encoding="utf-8").read()
    ok("_monitor_rescale_if_changed" in main_src,
       "main 缩放变化触发重排接线在位")


# ---------------------------------------------------------------------------
# L. 右键菜单抑制 / 使用率不画曲线 / 成组合并 / 组级屏幕贴边（本轮修复）
# ---------------------------------------------------------------------------
def _right_ev(etype, gpos):
    from PySide6.QtGui import QMouseEvent
    return QMouseEvent(etype, QPointF(gpos), QPointF(gpos),
                       Qt.MouseButton.RightButton, Qt.MouseButton.RightButton,
                       Qt.KeyboardModifier.NoModifier)


def _mk_group(mgr, hub, keys, origin):
    """建一个已吸附成组的窗格集（水平排列，相邻左右贴合）。"""
    inj = make_injections()
    w_inj = {k: v for k, v in inj.items() if k != "config_io"}
    widgets = []
    x = origin[0]
    for key in keys:
        w = mu.MonitorUnitWidget(key, hub, manager=mgr, **w_inj)
        mgr.register_widget(key, w, [key])
        w.move(x, origin[1])
        w.show()
        widgets.append(w)
        x += w.width()
    APP.processEvents()
    grp = mgr.new_group()
    for i, w in enumerate(widgets):
        grp.add(w)
        w._is_snapped = True
        if i > 0:
            w._snap_constraints = [(widgets[i - 1], "left")]
            w._contact_edges = ["left"]
            widgets[i - 1]._contact_edges = ["right"]
    return widgets, grp


def section_l():
    print("[L] 右键菜单抑制 / 使用率曲线 / 成组合并 / 组级贴边")
    from PySide6.QtCore import QEvent, QPoint
    from PySide6.QtGui import QContextMenuEvent

    # ---- L1 右键拖动解绑 → 不弹菜单；右键轻点 → 弹菜单 ----
    hub = FakeHub()
    inj = make_injections()
    mgr = mu.MonitorWindowManager(hub, **inj)
    wa, wb, grp = _snapped_pair(mgr, hub)
    start = wa.frameGeometry().topLeft() + QPoint(5, 5)
    # 右键拖动 >4px 解绑
    APP.sendEvent(wa, _right_ev(QEvent.Type.MouseButtonPress, start))
    APP.sendEvent(wa, _right_ev(QEvent.Type.MouseMove, start + QPoint(60, 40)))
    APP.sendEvent(wa, _right_ev(QEvent.Type.MouseButtonRelease, start + QPoint(60, 40)))
    ok(wa._group is None, "右键拖动解绑自身")
    ok(wa._suppress_context_menu, "右键拖动置位菜单抑制标志")
    # 模拟 Windows 右键抬起后派发的 contextMenuEvent → 应被吞掉
    menus_before = [c for c in wa.children() if isinstance(c, QMenu)]
    ce = QContextMenuEvent(QContextMenuEvent.Reason.Mouse,
                           wa.frameGeometry().center(),
                           wa.mapToGlobal(wa.rect().center()))
    APP.sendEvent(wa, ce)
    menus_after = [c for c in wa.children() if isinstance(c, QMenu) and c.isVisible()]
    ok(not menus_after, "右键拖动后 contextMenuEvent 不弹菜单")
    ok(not wa._suppress_context_menu, "抑制标志消费后复位")
    # 右键轻点（≤4px）→ 弹菜单
    start2 = wa.frameGeometry().topLeft() + QPoint(5, 5)
    APP.sendEvent(wa, _right_ev(QEvent.Type.MouseButtonPress, start2))
    APP.sendEvent(wa, _right_ev(QEvent.Type.MouseButtonRelease, start2 + QPoint(2, 1)))
    menus_light = [c for c in wa.children() if isinstance(c, QMenu) and c.isVisible()]
    ok(bool(menus_light), "右键轻点弹菜单")
    for m in menus_light:
        m.close()
    wa.close()
    wb.close()

    # ---- L2 仅硬盘使用率不画曲线；CPU/内存使用率与温度画曲线（V16.2 收窄）----
    hub2 = FakeHub()
    mgr2 = mu.MonitorWindowManager(hub2, **inj)
    w_inj = {k: v for k, v in inj.items() if k != "config_io"}

    def _spark_draw_calls(key, values):
        """注入波动历史后真实渲染，记录 QPainter.drawPath 调用次数。

        sparkline 折线走 drawPath（圆角矩形走 drawRoundedRect，互不干扰）；
        0=未画曲线，≥1=画了曲线。比像素对比可靠（不受抗锯齿混色/呼吸影响）。
        """
        from PySide6.QtGui import QPainter
        w = mu.MonitorUnitWidget(key, hub2, manager=mgr2, **w_inj)
        mgr2.register_widget(key, w, [key])
        w.show()
        APP.processEvents()
        for v in values:
            hub2.set_metric(key, v, mh.STATUS_OK)
            w.update_data(hub2.snapshot()[key])
        calls = []
        orig = QPainter.drawPath
        def _spy(self, *a, **k):
            calls.append(1)
            return orig(self, *a, **k)
        QPainter.drawPath = _spy
        try:
            _grab(w)
        finally:
            QPainter.drawPath = orig
        w.close()
        return len(calls)

    _vals = (10.0, 40.0, 70.0, 30.0, 90.0, 50.0)
    d_disk = _spark_draw_calls("disk.0.usage", _vals)
    ok(d_disk == 0, f"硬盘使用率不画曲线（drawPath 0 次，实得 {d_disk}）")
    d_cpu = _spark_draw_calls("cpu.load", _vals)
    ok(d_cpu >= 1, f"CPU使用率恢复画曲线（drawPath {d_cpu} 次）")
    d_mem = _spark_draw_calls("mem.load", _vals)
    ok(d_mem >= 1, f"内存使用率恢复画曲线（drawPath {d_mem} 次）")
    d_temp = _spark_draw_calls("cpu.temp", (50.0, 60.0, 70.0, 65.0, 80.0, 55.0))
    ok(d_temp >= 1, f"温度画曲线（drawPath {d_temp} 次）")
    # 源码看守：跳过条件按键判定 disk.{i}.usage，不再按标签「使用率」一刀切
    _src_l2 = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "monitor_ui.py"), encoding="utf-8").read()
    ok('_kp[2] == "usage"' in _src_l2 and '"使用率" in row.label' not in _src_l2,
       "曲线跳过仅按硬盘使用率键判定（源码看守）")

    # ---- L3 单对单：独立窗 A 吸独立窗 B → 同组 2 成员 ----
    hub3 = FakeHub()
    mgr3 = mu.MonitorWindowManager(hub3, **inj)
    w_inj3 = {k: v for k, v in inj.items() if k != "config_io"}
    a = mu.MonitorUnitWidget("cpu.load", hub3, manager=mgr3, **w_inj3)
    b = mu.MonitorUnitWidget("mem.load", hub3, manager=mgr3, **w_inj3)
    mgr3.register_widget("cpu.load", a, ["cpu.load"])
    mgr3.register_widget("mem.load", b, ["mem.load"])
    a.move(100, 100)
    b.move(100 + a.width() + 12, 100)
    a.show()
    b.show()
    APP.processEvents()
    mgr3.simulate_drag(a, b.x() - a.x() - a.width() - 2, 0)
    import time as _t
    t0 = _t.perf_counter()
    while a._snap_animating and _t.perf_counter() - t0 < 2.0:
        APP.processEvents()
        _t.sleep(0.01)
    ok(a._group is not None and a._group is b._group, "单对单：A/B 同组")
    ok(len(a._group.members) == 2, "单对单：组 2 成员")
    ok(a.frameGeometry().left() + a.width() == b.frameGeometry().left(),
       "单对单：A 右贴 B 左")
    a.close()
    b.close()

    # ---- L4 多对多：组 ABC 吸组 DEF → 合并为 6 成员单组，旧组解散 ----
    hub4 = FakeHub()
    mgr4 = mu.MonitorWindowManager(hub4, **inj)
    abc, g_abc = _mk_group(mgr4, hub4, ["cpu.load", "cpu.temp", "cpu.freq"], (100, 100))
    deff, g_def = _mk_group(mgr4, hub4, ["mem.load", "mem.used", "net.rx"],
                            (100 + abc[0].width() * 3 + 15, 100))
    ok(g_abc in mgr4.snap_groups() and g_def in mgr4.snap_groups(), "初始两组并存")
    # 拖 ABC 组（抓 A）向右靠近 DEF 组
    target_dx = deff[0].x() - abc[-1].x() - abc[-1].width() - 2
    mgr4.simulate_drag(abc[0], target_dx, 0)
    t0 = _t.perf_counter()
    while abc[0]._snap_animating and _t.perf_counter() - t0 < 2.0:
        APP.processEvents()
        _t.sleep(0.01)
    merged = abc[0]._group
    ok(merged is not None, "多对多：合并组存在")
    ok(all(w._group is merged for w in abc + deff), "多对多：6 窗同组")
    ok(len(merged.members) == 6, f"多对多：组 6 成员（{len(merged.members)}）")
    ok(g_abc not in mgr4.snap_groups(), "多对多：旧组 ABC 解散")
    ok(len(mgr4.snap_groups()) == 1, "多对多：管理器仅剩 1 组")
    # 整组刚性：ABC 内部相对位置保持（B 仍在 A 右侧贴合）
    ok(abc[0].frameGeometry().left() + abc[0].width() == abc[1].frameGeometry().left(),
       "多对多：ABC 内部贴合保持（整组刚性移动）")
    # 接触边：ABC 末窗右贴 DEF 首窗左
    ok(abc[-1].frameGeometry().left() + abc[-1].width() == deff[0].frameGeometry().left(),
       "多对多：ABC 末窗贴 DEF 首窗")
    for w in abc + deff:
        w.close()

    # ---- L5 多对单：组 AB 吸独立窗 C → 3 成员单组 ----
    hub5 = FakeHub()
    mgr5 = mu.MonitorWindowManager(hub5, **inj)
    ab, g_ab = _mk_group(mgr5, hub5, ["cpu.load", "cpu.temp"], (100, 200))
    w_inj5 = {k: v for k, v in inj.items() if k != "config_io"}
    c = mu.MonitorUnitWidget("mem.load", hub5, manager=mgr5, **w_inj5)
    mgr5.register_widget("mem.load", c, ["mem.load"])
    c.move(100 + ab[0].width() * 2 + 12, 200)
    c.show()
    APP.processEvents()
    mgr5.simulate_drag(ab[0], c.x() - ab[-1].x() - ab[-1].width() - 2, 0)
    t0 = _t.perf_counter()
    while ab[0]._snap_animating and _t.perf_counter() - t0 < 2.0:
        APP.processEvents()
        _t.sleep(0.01)
    ok(c._group is not None and ab[0]._group is c._group, "多对单：3 窗同组")
    ok(len(c._group.members) == 3, "多对单：组 3 成员")
    ok(g_ab not in mgr5.snap_groups(), "多对单：旧组 AB 解散")
    for w in ab + [c]:
        w.close()

    # ---- L6 单对多：独立窗 A 吸组 BC → 3 成员单组 ----
    hub6 = FakeHub()
    mgr6 = mu.MonitorWindowManager(hub6, **inj)
    bc, g_bc = _mk_group(mgr6, hub6, ["mem.load", "mem.used"], (300, 300))
    w_inj6 = {k: v for k, v in inj.items() if k != "config_io"}
    a6 = mu.MonitorUnitWidget("cpu.load", hub6, manager=mgr6, **w_inj6)
    mgr6.register_widget("cpu.load", a6, ["cpu.load"])
    a6.move(300 - a6.width() - 12, 300)
    a6.show()
    APP.processEvents()
    mgr6.simulate_drag(a6, bc[0].x() - a6.x() - a6.width() - 2, 0)
    t0 = _t.perf_counter()
    while a6._snap_animating and _t.perf_counter() - t0 < 2.0:
        APP.processEvents()
        _t.sleep(0.01)
    ok(a6._group is not None and a6._group is bc[0]._group, "单对多：3 窗同组")
    ok(len(a6._group.members) == 3, "单对多：组 3 成员")
    ok(a6.frameGeometry().left() + a6.width() == bc[0].frameGeometry().left(),
       "单对多：A 右贴 B 左")
    for w in [a6] + bc:
        w.close()

    # ---- L7 组级屏幕贴边：整组贴边，所有成员同步（不只首窗）----
    hub7 = FakeHub()
    mgr7 = mu.MonitorWindowManager(hub7, **inj)
    scr = APP.primaryScreen().availableGeometry()
    # 组 AB 水平排列，整体靠近屏幕左缘
    ab7, g_ab7 = _mk_group(mgr7, hub7, ["cpu.load", "cpu.temp"],
                           (scr.left() + 12, scr.top() + 400))
    # 拖整组向左贴屏幕左缘
    mgr7.simulate_drag(ab7[0], -(scr.left() + 12 - scr.left()) - 2, 0)
    t0 = _t.perf_counter()
    while ab7[0]._snap_animating and _t.perf_counter() - t0 < 2.0:
        APP.processEvents()
        _t.sleep(0.01)
    ok(ab7[0]._group_screen_edge == "left", "组级屏幕贴边记录 left")
    ok(ab7[0].frameGeometry().left() == scr.left(), "首窗贴屏幕左缘")
    # 关键：第二窗也同步移动（整组刚性），保持与首窗贴合
    ok(ab7[0].frameGeometry().left() + ab7[0].width() == ab7[1].frameGeometry().left(),
       "组贴边后第二窗同步（内部贴合保持，非首窗独动）")
    # 缩放后整组重新贴边
    ab7[0]._group_screen_edge = "left"
    ab7[1]._group_screen_edge = "left"
    mgr7.rescale()
    ok(ab7[0].frameGeometry().left() == scr.left(), "缩放后整组仍贴左缘")
    for w in ab7:
        w.close()

    # ---- L8 源码看守（改坏会红）----
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "monitor_ui.py"), encoding="utf-8").read()
    ok("_suppress_context_menu" in src, "右键菜单抑制标志在位")
    ok("def _merge_into_target" in src and "def _contact_pair" in src,
       "组合并与接触窗对方法在位")
    ok("_group_screen_edge" in src and "def _resnap_group_screen" in src,
       "组级屏幕贴边与整组重贴在位")
    ok("_snap_members" in src and "_snap_member_from" in src,
       "整组吸附动画成员字段在位")

    # ---- L9 逐窗格磁吸目标：竖直大组旁单窗可精确吸到组内顶窗下方 ----
    # 复现用户场景：第三列竖直堆叠 3 窗成组（包围盒高=3×窗高），第一列单窗
    # 想贴到组内顶部单窗的下方。旧「整组包围盒」口径会把下方候选推到整摞
    # 底下（失效）；逐窗格口径应命中顶窗底边。
    hub9 = FakeHub()
    mgr9 = mu.MonitorWindowManager(hub9, **inj)
    w_inj9 = {k: v for k, v in inj.items() if k != "config_io"}
    # 竖直堆叠 3 窗成组（x=400，y=100/100+h/100+2h）
    stack = []
    for i, key in enumerate(["cpu.load", "cpu.temp", "cpu.freq"]):
        w = mu.MonitorUnitWidget(key, hub9, manager=mgr9, **w_inj9)
        mgr9.register_widget(key, w, [key])
        stack.append(w)
    hh = stack[0].height()
    for i, w in enumerate(stack):
        w.move(400, 100 + i * hh)
        w.show()
    APP.processEvents()
    g9 = mgr9.new_group()
    for i, w in enumerate(stack):
        g9.add(w)
        w._is_snapped = True
        if i > 0:
            w._snap_constraints = [(stack[i - 1], "top")]
    # 组包围盒高度 = 3×窗高（远大于单窗）
    ok(g9.bounding_rect().height() == 3 * hh, "竖直组包围盒=3倍窗高")
    # 边语义：_edge_candidates 中 "top" = 我的上边贴目标下边（我在目标下方）。
    # 单窗放在顶窗正下方：单窗顶边距顶窗底边 8px（<20 阈值），水平对齐顶窗。
    # 单窗左上角 y = 顶窗底边 + 8 = top.y + hh + 8。
    # 为避免与中间窗（top.y+hh .. top.y+2hh）垂直重叠干扰，把堆叠组窗高方向
    # 拉开：单窗只与顶窗邻近——用顶窗矩形单独算候选隔离验证。
    single = mu.MonitorUnitWidget("mem.load", hub9, manager=mgr9, **w_inj9)
    mgr9.register_widget("mem.load", single, ["mem.load"])
    top = stack[0]
    top_r = top.frameGeometry()
    hh = top_r.height()
    # 单窗顶边在顶窗底边下 8px
    single.move(top_r.left(), top_r.top() + hh + 8)
    single.show()
    APP.processEvents()
    my_r = single.frameGeometry()
    grp_r = g9.bounding_rect()
    # 核心修复点：逐窗格目标（顶窗矩形）产生 top 候选（我在顶窗下方），距离 8px 可达。
    cand_top = single._edge_candidates(my_r, top_r)
    top_cand = [(d, sr) for d, e, sr in cand_top if e == "top"]
    ok(top_cand and top_cand[0][0] < 20,
       f"逐窗格：顶窗 top 候选距离<20（下方吸附可达，{top_cand[0][0] if top_cand else 'None'}）")
    ok(top_cand and top_cand[0][1].top() == top_r.top() + hh,
       "逐窗格：贴合位顶边=顶窗底边（精确非整摞底边）")
    # 旧整组包围盒口径：top 候选距离 = 单窗顶边 - 整摞底边 = (top.y+hh+8)-(top.y+3hh) = 8-2hh（绝对值 2hh-8≥20，失效）
    cand_grp = single._edge_candidates(my_r, grp_r)
    top_grp = [(d, sr) for d, e, sr in cand_grp if e == "top"]
    ok(top_grp and top_grp[0][0] >= 20,
       f"整组包围盒：top 候选距离≥20（旧口径下方失效，{top_grp[0][0] if top_grp else 'None'}）")
    single._clear_preview()
    for w in stack + [single]:
        w.close()
    ok("other.frameGeometry()" in src and "bounding_rect() if og" not in src,
       "预览候选用逐窗格矩形（非整组包围盒，源码看守）")


# ---------------------------------------------------------------------------
# M. 资源监视颜色设置项 / 字色对比度 / 字体加粗 / 主题刷新（本轮需求）
# ---------------------------------------------------------------------------
def section_m():
    print("[M] 资源监视颜色 / 字色对比度 / 加粗 / 主题刷新")
    import main as M

    # ---- M1 颜色函数：背景取设置项，字色按亮度自动黑/白 ----
    light = {"monitor_color": "#F0F0F0", "system_dark_mode": True}
    dark = {"monitor_color": "#101014", "system_dark_mode": False}
    ok(mu.body_color(light).name().upper() == "#F0F0F0", "浅色背景取设置项")
    ok(mu.body_color(dark).name().upper() == "#101014", "深色背景取设置项")
    ok(mu.text_color(light).lightness() < 128, "浅色背景 → 黑字")
    ok(mu.text_color(dark).lightness() > 128, "深色背景 → 白字")
    # 亮度分界 150：#969696(150)→白字，#979797(151)→黑字
    ok(mu.text_color({"monitor_color": "#969696"}).lightness() > 128,
       "亮度 150 → 白字（分界）")
    ok(mu.text_color({"monitor_color": "#979797"}).lightness() < 128,
       "亮度 151 → 黑字（分界）")
    # 无设置项 → 回退系统深浅色（兼容旧主题）
    ok(mu.body_color({"system_dark_mode": True}).lightness() < 128,
       "无设置项回退系统深色底")
    ok(mu.text_color({"system_dark_mode": False}).lightness() < 128,
       "无设置项回退系统浅色 → 黑字")
    # 非法色码 → 回退系统深浅色
    ok(mu.body_color({"monitor_color": "not-a-color",
                      "system_dark_mode": True}).lightness() < 128,
       "非法色码回退系统深色底")

    # ---- M2 字体加粗 ----
    w, hub, mgr = _make_widget()
    ok(w._font().bold(), "窗格字体加粗")
    w.close()

    # ---- M3 数值/曲线仍走阈值语义色（不受背景字色规则影响）----
    w2, hub2, mgr2 = _make_widget()
    mgr2.register_widget("cpu.load", w2, ["cpu.load"])
    w2.show()
    APP.processEvents()
    hub2.set_metric("cpu.load", 95.0, mh.STATUS_OK)   # 高负载 → 红
    w2.update_data(hub2.snapshot()["cpu.load"])
    row = w2._rows[0]
    sem = w2._semantic_color(row)
    ok(sem.name().upper() == mu.SEM_RED.upper(),
       f"高负载数值仍走阈值红色（{sem.name()}，不受背景字色影响）")
    # 浅色背景下数值仍是阈值色（非黑字）
    w2.theme = {"monitor_color": "#F0F0F0", "system_dark_mode": True}
    sem2 = w2._semantic_color(row)
    ok(sem2.name().upper() == mu.SEM_RED.upper(),
       "浅色背景数值仍阈值红（非黑字）")
    w2.close()

    # ---- M4 配置链路：默认/读/写/应用 monitor_color ----
    M.save_monitor_config(M.monitor_ui.default_monitor_config())
    d = M.default_settings()
    ok(d["appearance"].get("monitor_color") == "#181A20",
       "出厂默认 monitor_color=#181A20")
    cfg = M.load_appearance_config()
    ok(cfg.get("monitor_color") == "#181A20", "load 默认 monitor_color")
    cfg["monitor_color"] = "#ABCDEF"
    M.save_appearance_config(cfg)
    ok(M.load_appearance_config()["monitor_color"] == "#ABCDEF",
       "save/load 往返 monitor_color")
    M.apply_appearance_to_config(cfg)
    ok(M.CONFIG["monitor_color"] == "#ABCDEF", "apply 合并 monitor_color 到 CONFIG")
    ok(M._monitor_theme()["monitor_color"] == "#ABCDEF",
       "_monitor_theme 注入 monitor_color")
    # 非法色码归一：回退当前生效色（与 color_start/mid/end 同口径，非出厂默认）
    cfg["monitor_color"] = "xyz"
    M.apply_appearance_to_config(cfg)
    ok(M.CONFIG["monitor_color"] == "#ABCDEF",
       "非法 monitor_color 保持当前生效色（归一回退）")

    # ---- M5 外观窗 UI：资源监视颜色行 + 悬停提示 + 呼吸置灰排除 ----
    M.apply_appearance_to_config(M.load_appearance_config())
    dlg = M.AppearanceSettingsDialog()
    ok(hasattr(dlg, "edt_monitor"), "外观窗含资源监视颜色框 edt_monitor")
    ok(dlg.edt_monitor.text().upper() == M.CONFIG["monitor_color"],
       "资源监视颜色框初值=CONFIG")
    # 标签悬停提示
    labels = [c for c in dlg.findChildren(M.QLabel) if c.text() == "资源监视"]
    ok(any("建议黑灰白三色确保显示效果" in (c.toolTip() or "") for c in labels),
       "资源监视标签悬停提示在位")
    # 呼吸关闭 → 三色置灰但资源监视色恒可用
    dlg.chk_breath.setChecked(False)
    ok(not dlg.edt_start.isEnabled(), "呼吸关闭三色框置灰")
    ok(dlg.edt_monitor.isEnabled(), "呼吸关闭资源监视色框仍可用")
    ok(dlg._monitor_pick_btn.isEnabled(), "呼吸关闭资源监视取色钮仍可用")
    dlg.chk_breath.setChecked(True)
    # settings() 收集 monitor_color
    dlg.edt_monitor.setText("#223344")
    s = dlg.settings()
    ok(s.get("monitor_color") == "#223344", "settings() 收集 monitor_color")
    dlg.close()

    # ---- M6 主题刷新：改色后已开窗格即时生效 ----
    M._MONITOR_MGR = None
    M._MONITOR_HUB = None
    mgr3 = M._ensure_monitor_manager()
    fake = FakeHub()
    real_hub = M._MONITOR_HUB
    M._MONITOR_HUB = fake
    mgr3.hub = fake
    try:
        cfg = M.load_monitor_config()
        cfg["enabled"] = True
        cfg["units"]["cpu.load"]["enabled"] = True
        M.save_monitor_config(cfg)
        M._monitor_sync_windows(cfg)
        w3 = mgr3._widgets["cpu.load"]
        ok(w3.theme.get("monitor_color") == M.CONFIG["monitor_color"],
           "建窗时主题含 monitor_color")
        # 改色 → refresh_theme → 窗格主题即时更新
        new_cfg = dict(M.CONFIG)
        new_cfg["monitor_color"] = "#EEEEEE"
        M.apply_appearance_to_config(new_cfg)
        ok(w3.theme.get("monitor_color") == "#EEEEEE",
           "改色后已开窗格主题即时刷新")
        ok(mu.body_color(w3.theme).name().upper() == "#EEEEEE",
           "刷新后窗格背景取新色")
    finally:
        for w in list(mgr3._widgets.values()):
            w.close()
        real_hub.stop()
        M._MONITOR_MGR = None
        M._MONITOR_HUB = None

    # ---- M7 源码看守（改坏会红）----
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "monitor_ui.py"), encoding="utf-8").read()
    ok("def refresh_theme" in src, "管理器主题刷新方法在位")
    ok("monitor_color" in src and "_luminance" in src,
       "背景色取设置项 + 亮度字色判定在位")
    ok("setBold(True)" in src, "字体加粗在位")
    main_src = open(os.path.join(root, "main.py"), encoding="utf-8").read()
    ok("建议黑灰白三色确保显示效果" in main_src, "悬停提示文案在位")
    ok("refresh_theme(_monitor_theme())" in main_src,
       "main 外观应用链路主题刷新接线在位")


# ---------------------------------------------------------------------------
# N. 网速 KB 自适应 / 独显全量枚举 / 硬件名小字（本轮功能）
# ---------------------------------------------------------------------------
def section_n():
    print("[N] 网速KB自适应 / 独显全量枚举 / 硬件名小字")
    import main as M

    # ---- N1 网速 KB 自适应：数值×1024、单位 KB/s ----
    w, hub, mgr = _make_widget()
    mgr.register_widget("net.rx", w, ["net.rx"])
    w.show()
    APP.processEvents()
    row = w._rows[0]
    # <1MB/s → KB
    hub.set_metric("net.rx", 0.5, mh.STATUS_OK)
    w.update_data(hub.snapshot()["net.rx"])
    ok(w._value_text(row) == "512.0", f"0.5MB/s → 512.0 KB（{w._value_text(row)}）")
    ok(w._display_unit(row) == "KB/s", "0.5MB/s 单位 KB/s")
    # ≥1MB/s → MB
    hub.set_metric("net.rx", 2.5, mh.STATUS_OK)
    w.update_data(hub.snapshot()["net.rx"])
    ok(w._value_text(row) == "2.5", "2.5MB/s 数值不变")
    ok(w._display_unit(row) == "MB/s", "2.5MB/s 单位 MB/s")
    # 边界 1.0 → MB
    hub.set_metric("net.rx", 1.0, mh.STATUS_OK)
    w.update_data(hub.snapshot()["net.rx"])
    ok(w._display_unit(row) == "MB/s", "1.0MB/s 边界走 MB")
    # 0 → MB（0 不切 KB）
    hub.set_metric("net.rx", 0.0, mh.STATUS_OK)
    w.update_data(hub.snapshot()["net.rx"])
    ok(w._display_unit(row) == "MB/s", "0MB/s 不切 KB")
    # 无读数 → 单位仍 MB/s，数值 --
    hub.set_metric("net.rx", None, mh.STATUS_STALE)
    w.update_data(hub.snapshot()["net.rx"])
    ok(w._value_text(row) == "--", "无读数数值 --")
    ok(w._display_unit(row) == "MB/s", "无读数单位 MB/s")
    # history 记 canonical MB/s（不受 KB 显示影响）
    hub.set_metric("net.rx", 0.5, mh.STATUS_OK)
    w.update_data(hub.snapshot()["net.rx"])
    ok(row.history[-1] == 0.5, "history 记 canonical MB/s（0.5 非 512）")
    # 阈值色按 canonical MB/s（0.5）插值，而非 KB 显示值（512 会落红段）
    exp_color = mu.get_color_for_value(0.5, mu.default_thresholds("net.rx", "MB/s"))
    ok(w._semantic_color(row).name().upper() == exp_color.name().upper(),
       "网速阈值色按 MB/s 插值（非 KB 显示值）")
    ok(w._semantic_color(row).name().upper() != mu.SEM_RED.upper(),
       "0.5MB/s 不误落红段（证明未用 KB 值算色）")
    w.close()

    # ---- N2 独显全量枚举：动态键定义 + 多卡标签 ----
    ok(mh._def("gpu_d.0.load")[1] == "GPU使用率", "独显卡0标签（V16.2 无括号无卡号）")
    ok(mh._def("gpu_d.1.load")[1] == "GPU使用率", "独显卡1标签不带卡号（设备名区分）")
    ok(mh._def("gpu_d.0.vram_used")[3] == "GB", "独显显存用量单位 GB")
    ok(mh._def("gpu_d.2.temp")[1] == "GPU温度", "独显卡2温度标签（V16.2 无后缀）")
    # 快照多卡产出
    hub2 = FakeHub()
    for i in range(2):
        for s, v in (("load", 10.0 + i), ("temp", 50.0 + i),
                     ("vram_load", 5.0 + i), ("vram_used", 1.0 + i)):
            hub2.set_metric(f"gpu_d.{i}.{s}", v, mh.STATUS_OK)
    snap = hub2.snapshot()
    ok("gpu_d.0.load" in snap and "gpu_d.1.load" in snap, "快照产出两卡动态键")
    gpus = hub2.list_gpus_d(snapshot=snap)
    ok(len(gpus) == 2, "list_gpus_d 枚举两卡")

    # ---- N3 独显新卡发现回调 ----
    w3, hub3, mgr3 = _make_widget()
    mgr3.register_widget("gpu_d.0.load", w3, ["gpu_d.0.load"])
    w3.show()
    APP.processEvents()
    discovered = []
    mgr3.on_new_gpu_d_keys = lambda keys: discovered.extend(keys)
    hub3.set_metric("gpu_d.0.load", 10.0, mh.STATUS_OK)
    hub3.set_metric("gpu_d.1.load", 20.0, mh.STATUS_OK)
    mgr3.tick()
    ok("gpu_d.1.load" in discovered, f"新独显卡键经回调通知（{discovered}）")
    discovered.clear()
    mgr3.tick()
    ok(discovered == [], "无新卡不重复回调")
    w3.close()

    # ---- N4 硬件名小字：数据更新同步 + 渲染路径 ----
    w4, hub4, mgr4 = _make_widget()
    mgr4.register_widget("cpu.temp", w4, ["cpu.temp"])
    w4.show()
    APP.processEvents()
    m_hw = mh.Metric("cpu.temp", "CPU温度", "cpu", 63.0, "℃",
                     mh.STATUS_OK, hardware="i7-11800H")
    w4.update_data(m_hw)
    ok(w4._rows[0].hardware == "i7-11800H", "硬件名经 update_data 同步行")
    # 渲染不崩（硬件名小字绘制路径）
    img = _grab(w4)
    ok(img.width() > 0, "含硬件名窗格正常渲染")
    w4.close()

    # ---- N5 旧独显键迁移 ----
    ok(M._migrate_gpu_d_key("gpu_d.load") == "gpu_d.0.load", "旧键 gpu_d.load 迁移卡0")
    ok(M._migrate_gpu_d_key("gpu_d.vram_used") == "gpu_d.0.vram_used", "旧显存键迁移")
    ok(M._migrate_gpu_d_key("gpu_d.0.load") == "gpu_d.0.load", "新键原样保留")
    ok(M._migrate_gpu_d_key("cpu.load") == "cpu.load", "非独显键不动")
    # 合并旧配置 units/layout 迁移
    old_cfg = {"units": {"gpu_d.load": {"enabled": True, "threshold": []}},
               "layout": {"gpu_d.temp": {"x": 1, "y": 2, "group_id": None}}}
    merged = M._merge_monitor_block(None, old_cfg)
    ok(merged["units"].get("gpu_d.0.load", {}).get("enabled") is True,
       "旧配置独显勾选迁移到卡0")
    ok("gpu_d.load" not in merged["units"], "旧独显键不残留")
    ok("gpu_d.0.temp" in merged["layout"], "旧独显布局迁移到卡0")

    # ---- N7 用量类按比例配色（V16.2：64G 用 16G 不再闪红）----
    ok(mu.is_usage_ratio_key("mem.used"), "内存用量属比例配色键")
    ok(mu.is_usage_ratio_key("gpu_i.shared"), "共享内存用量属比例配色键")
    ok(mu.is_usage_ratio_key("gpu_d.0.vram_used"), "独显显存用量属比例配色键")
    ok(mu.is_usage_ratio_key("gpu_d.2.vram_used"), "多卡显存用量属比例配色键")
    ok(not mu.is_usage_ratio_key("mem.load"), "内存使用率不走比例（本身百分比）")
    ok(not mu.is_usage_ratio_key("cpu.temp"), "温度不走比例")
    ok(mu.usage_ratio(16.0, 64.0) == 25.0, "16/64G → 25%")
    ok(mu.usage_ratio(16.0, None) is None, "总量缺失 → None")
    ok(mu.usage_ratio(None, 64.0) is None, "无读数 → None")
    ok(mu.usage_ratio(16.0, 0.0) is None, "总量非正 → None")
    w7, hub7, mgr7 = _make_widget()
    mgr7.register_widget("mem.used", w7, ["mem.used"])
    w7.show()
    APP.processEvents()
    # 16/64G=25% → 绿段，不触发高负载呼吸（旧 GB 固定段 16 落红会闪）
    m7 = mh.Metric("mem.used", "内存用量", "mem", 16.0, "GB",
                   mh.STATUS_OK, total=64.0)
    w7.update_data(m7)
    r7 = w7._rows[0]
    ok(r7.total == 64.0, "总量经 update_data 同步行")
    exp_green = mu.get_color_for_value(25.0, mu.default_thresholds("mem.used", "%"))
    ok(w7._semantic_color(r7).name().upper() == exp_green.name().upper(),
       "16/64G 按 25% 走绿段（非旧 GB 段红）")
    ok(w7._semantic_color(r7).name().upper() != mu.SEM_RED.upper(),
       "16/64G 不误落红段")
    ok(w7._row_alpha(r7) == 1.0, "16/64G 不触发高负载呼吸")
    ok(not w7._breathing, "16/64G 呼吸聚合不启动")
    ok(r7.history[-1] == 16.0, "history 记 canonical GB（非比例值）")
    ok(w7._value_text(r7) == "16.0", "显示值保持 GB（非百分比）")
    ok(w7._display_unit(r7) == "GB", "显示单位保持 GB")
    # 56/64G=87.5% → 红段 + 呼吸（比例口径仍正确报高负载）
    m7b = mh.Metric("mem.used", "内存用量", "mem", 56.0, "GB",
                    mh.STATUS_OK, total=64.0)
    w7.update_data(m7b)
    ok(w7._row_alpha(r7) < 1.0 or w7._breathing, "56/64G=87.5% 触发高负载呼吸")
    exp_red = mu.get_color_for_value(87.5, mu.default_thresholds("mem.used", "%"))
    ok(w7._semantic_color(r7).name().upper() == exp_red.name().upper(),
       "56/64G 按 87.5% 走红段")
    # 总量缺失 → 中性色不闪红（宁缺勿错）
    m7c = mh.Metric("mem.used", "内存用量", "mem", 16.0, "GB",
                    mh.STATUS_OK, total=None)
    w7.update_data(m7c)
    ok(w7._semantic_color(r7).name().upper() == QColor(150, 150, 150).name().upper(),
       "总量缺失走中性色")
    ok(w7._row_alpha(r7) == 1.0, "总量缺失不呼吸")
    # 共享内存/显存用量同口径
    mgr7.register_widget("gpu_i.shared", w7, ["gpu_i.shared"])
    m7d = mh.Metric("gpu_i.shared", "共享内存用量", "gpu_i", 0.6, "GB",
                    mh.STATUS_OK, total=47.7)
    w7.update_data(m7d)
    r7s = next(r for r in w7._rows if r.key == "gpu_i.shared")
    exp_s = mu.get_color_for_value(0.6 / 47.7 * 100,
                                   mu.default_thresholds("gpu_i.shared", "%"))
    ok(w7._semantic_color(r7s).name().upper() == exp_s.name().upper(),
       "共享内存用量按比例配色")
    mgr7.register_widget("gpu_d.0.vram_used", w7, ["gpu_d.0.vram_used"])
    m7e = mh.Metric("gpu_d.0.vram_used", "GPU显存用量", "gpu_d", 1.0, "GB",
                    mh.STATUS_OK, total=6.0)
    w7.update_data(m7e)
    r7v = next(r for r in w7._rows if r.key == "gpu_d.0.vram_used")
    exp_v = mu.get_color_for_value(1.0 / 6.0 * 100,
                                   mu.default_thresholds("gpu_d.0.vram_used", "%"))
    ok(w7._semantic_color(r7v).name().upper() == exp_v.name().upper(),
       "独显显存用量按比例配色")
    # 用户自定义 threshold 仍按 GB 原值（预留口径不走比例）
    w7.set_thresholds({"mem.used": [{"min": 0, "max": 8, "color": mu.SEM_GREEN},
                                    {"min": 8, "max": 16, "color": mu.SEM_YELLOW},
                                    {"min": 16, "max": 32, "color": mu.SEM_RED}]})
    w7.update_data(m7)   # 16GB total=64，自定义段 16 落黄→红边界
    ok(w7._semantic_color(r7).name().upper() != exp_green.name().upper(),
       "自定义阈值按 GB 原值生效（不走比例）")
    w7.close()

    # ---- N6 源码看守（改坏会红）----
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "monitor_ui.py"), encoding="utf-8").read()
    ok("def _display_unit" in src and "KB/s" in src, "网速 KB 自适应在位")
    ok("def is_usage_ratio_key" in src and "def usage_ratio" in src,
       "用量比例配色函数在位（源码看守）")
    hub_src7 = open(os.path.join(root, "monitor_hub.py"), encoding="utf-8").read()
    ok("D3D Shared Memory Total" in hub_src7 and "mem.total" in hub_src7,
       "总量采集在位（共享内存/内存，源码看守）")
    ok("row.hardware" in src and "AlignRight" in src, "硬件名小字右对齐绘制在位")
    ok("_notify_new_gpus_d" in src, "独显新卡发现在位")
    hub_src = open(os.path.join(root, "monitor_hub.py"), encoding="utf-8").read()
    ok("gpus_d" in hub_src and "def list_gpus_d" in hub_src, "独显全量枚举接口在位")
    ok("hw_names" in hub_src, "硬件名采集在位")
    main_src = open(os.path.join(root, "main.py"), encoding="utf-8").read()
    ok("_monitor_gpu_d_keys" in main_src and "_migrate_gpu_d_key" in main_src,
       "main 独显动态键与迁移接线在位")


# ---------------------------------------------------------------------------
# O. 禁止重叠 + 首位窗格归位 + 重启恢复组（V16.2 本轮需求）
# ---------------------------------------------------------------------------
def section_o():
    print("[O] 禁止重叠 / 首位窗格归位 / 重启恢复组")
    import time as _t
    from PySide6.QtCore import QEvent, QPoint, QRect

    # ---- O1 首次批量建窗零重叠（无布局记忆，首位窗格向右排开）----
    hub = FakeHub()
    inj = make_injections()
    mgr = mu.MonitorWindowManager(hub, **inj)
    w_inj = {k: v for k, v in inj.items() if k != "config_io"}
    keys = ["cpu.load", "cpu.temp", "mem.load", "mem.used", "net.rx"]
    ws = []
    anchor = QRect(900, 500, 60, 60)
    for key in keys:
        w = mu.MonitorUnitWidget(key, hub, manager=mgr, **w_inj)
        mgr.register_widget(key, w, [key])
        w.show_at(anchor)   # 全部同一锚点——旧实现会叠在一起
        ws.append(w)
    APP.processEvents()
    ok(mgr.first_widget() is ws[0], "首位窗格=最先创建者")
    overlaps = [(a.unit_key, b.unit_key)
                for i, a in enumerate(ws) for b in ws[i + 1:]
                if mgr._rects_overlap(a.frameGeometry(), b.frameGeometry())]
    ok(not overlaps, f"批量建窗零重叠（冲突对 {overlaps}）")
    ok(ws[1].frameGeometry().left() == ws[0].frameGeometry().left() + ws[0].width(),
       "第二窗格在首位窗格右边")
    ok(ws[1].frameGeometry().top() == ws[0].frameGeometry().top(),
       "向右排开顶边对齐")

    # ---- O2 松手重叠 → 自动归位到首位窗格右边 ----
    mover = ws[2]
    first_r = ws[0].frameGeometry()
    # 直接摆到首位窗格身上（完全重叠），模拟松手落位
    mover.move(first_r.left() + 5, first_r.top() + 5)
    APP.processEvents()
    ok(mgr._overlaps_others(mover), "落位与首位窗格重叠")
    mgr.check_overlap_after_drop(mover)
    ok(not mgr._overlaps_others(mover), "归位后不再重叠")
    ok(mover.frameGeometry().left() >= first_r.left() + first_r.width(),
       "归位到首位窗格右边（含顺延空位）")
    ok(mover.frameGeometry().top() == first_r.top(), "归位顶边对齐首位窗格")

    # ---- O3 拖动松手全链路：无磁吸目标的重叠落位自动归位 ----
    # 部分重叠（压住首位窗格右半、上下错开）：四边候选距离均 ≥20px 不触发
    # 磁吸预览 → 松手无目标 → 重叠检测归位。完全重叠会命中距离 0 的贴合
    # 候选走正常合并（既有磁吸语义），不属于本用例口径。
    first = ws[0]
    for w in ws[1:]:
        w.move(3000, 3000 + ws.index(w) * 200)
    APP.processEvents()
    m2 = ws[3]
    fr = first.frameGeometry()
    target = QPoint(fr.left() + fr.width() - 40, fr.top() + 40)
    delta = target - m2.frameGeometry().topLeft()
    mgr.simulate_drag(m2, delta.x(), delta.y())
    t0 = _t.perf_counter()
    while m2._snap_animating and _t.perf_counter() - t0 < 2.0:
        APP.processEvents()
        _t.sleep(0.01)
    APP.processEvents()
    ok(not mgr._overlaps_others(m2), "拖动重叠松手 → 归位后无重叠")
    ok(m2.frameGeometry().left() >= fr.left() + fr.width(),
       "拖动归位到首位窗格右边")

    # ---- O4 重启恢复组：group_id 重建组与约束 ----
    hub2 = FakeHub()
    mgr2 = mu.MonitorWindowManager(hub2, **inj)
    wa = mu.MonitorUnitWidget("cpu.load", hub2, manager=mgr2, **w_inj)
    wb = mu.MonitorUnitWidget("cpu.temp", hub2, manager=mgr2, **w_inj)
    wc = mu.MonitorUnitWidget("mem.load", hub2, manager=mgr2, **w_inj)
    mgr2.register_widget("cpu.load", wa, ["cpu.load"])
    mgr2.register_widget("cpu.temp", wb, ["cpu.temp"])
    mgr2.register_widget("mem.load", wc, ["mem.load"])
    # 布局记忆：wa/wb 同组水平贴合，wc 独立
    wa.move(100, 100)
    wb.move(100 + wa.width(), 100)
    wc.move(100, 100 + wa.height() + 40)
    for w in (wa, wb, wc):
        w.show()
    APP.processEvents()
    layout_mem = {
        "cpu.load": {"x": 100, "y": 100, "group_id": "g7"},
        "cpu.temp": {"x": 100 + wa.width(), "y": 100, "group_id": "g7"},
        "mem.load": {"x": 100, "y": 100 + wa.height() + 40, "group_id": None},
    }
    # 捕获 restore_groups 内部异常（log_fn 兜底会吞异常，不捕获则崩溃漏检）
    _restore_errs = []
    _orig_log2 = mgr2.log_fn
    mgr2.log_fn = lambda where, exc: _restore_errs.append((where, exc))
    mgr2.restore_groups(layout_mem)
    mgr2.log_fn = _orig_log2
    ok(not _restore_errs, f"重启恢复无内部异常（{_restore_errs}）")
    ok(wa._group is not None and wa._group is wb._group, "重启恢复：wa/wb 同组")
    ok(wa._group.group_id == "g7", "重启恢复：组 id 来自布局记忆")
    ok(wc._group is None, "重启恢复：独立窗不入组")
    ok(wa._is_snapped and wb._is_snapped, "重启恢复：吸附态复位")
    ok(any(t is wb for t, e in wa._snap_constraints), "重启恢复：贴合约束重建")
    ok(wa._contact_edges == ["right"] and wb._contact_edges == ["left"],
       f"重启恢复：接触边含对侧（{wa._contact_edges}/{wb._contact_edges}）")
    # 整组移动行为一致：拖 wa → wb 同步偏移
    p0 = wb.pos()
    start = wa.frameGeometry().topLeft() + QPoint(5, 5)
    _send(wa, QEvent.Type.MouseButtonPress, start)
    _send(wa, QEvent.Type.MouseMove, start + QPoint(40, 0))
    ok(wb.pos() == p0 + QPoint(40, 0), "重启恢复后整组刚性移动")
    _send(wa, QEvent.Type.MouseButtonRelease, start + QPoint(40, 0))
    t0 = _t.perf_counter()
    while wa._snap_animating and _t.perf_counter() - t0 < 2.0:
        APP.processEvents()
        _t.sleep(0.01)

    # ---- O5 恢复后重叠校验：记忆位冲突 → 归位首位窗格右边 ----
    hub3 = FakeHub()
    mgr3 = mu.MonitorWindowManager(hub3, **inj)
    wx = mu.MonitorUnitWidget("cpu.load", hub3, manager=mgr3, **w_inj)
    wy = mu.MonitorUnitWidget("mem.load", hub3, manager=mgr3, **w_inj)
    mgr3.register_widget("cpu.load", wx, ["cpu.load"])
    mgr3.register_widget("mem.load", wy, ["mem.load"])
    wx.move(200, 200)
    wy.move(205, 205)   # 记忆位故意重叠
    wx.show()
    wy.show()
    APP.processEvents()
    mgr3.restore_groups({"cpu.load": {"x": 200, "y": 200, "group_id": None},
                         "mem.load": {"x": 205, "y": 205, "group_id": None}})
    ok(not mgr3._rects_overlap(wx.frameGeometry(), wy.frameGeometry()),
       "恢复后重叠校验 → 冲突窗格归位不重叠")

    # ---- O6 源码看守（改坏会红）----
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "monitor_ui.py"), encoding="utf-8").read()
    ok("def place_for_show" in src and "def _relocate_right_of_first" in src,
       "防重叠摆放与归位方法在位")
    ok("def restore_groups" in src and "def check_overlap_after_drop" in src,
       "组恢复与落位重叠检测在位")
    main_src = open(os.path.join(root, "main.py"), encoding="utf-8").read()
    ok("mgr.restore_groups(layout)" in main_src, "main 首开接线组恢复")
    for w in ws + [wa, wb, wc, wx, wy]:
        w.close()


if __name__ == "__main__":
    section_a()
    section_b()
    section_c()
    section_d()
    section_e()
    section_f()
    section_g()
    section_h()
    section_i()
    section_j()
    section_k()
    section_l()
    section_m()
    section_n()
    section_o()
    print(f"\n== PASS {PASS} / FAIL {FAIL} ==")
    sys.exit(1 if FAIL else 0)
