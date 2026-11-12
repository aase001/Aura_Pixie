# -*- coding: utf-8 -*-
"""monitor_ui 阶段 0 自检（离屏，无真实硬件/侧车依赖）。

四节：
  A. 契约与骨架——注入接口、UnitRow、配置块默认、空壳冒烟
  B. 假 hub——字段与真实 Metric 完全一致、五态/序列可脚本化注入、
     snapshot 计数、list_disks 传帧复用、与真实 hub 输出交叉验证
  C. 源码看守——禁 import main、禁 QPropertyAnimation、禁 primaryScreen、
     禁 get(key) 拉取、禁 exec() 嵌套（改坏会红）
  D. 环境看守——offscreen 平台、QApplication 先于字体加载

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

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtGui import QFontDatabase  # noqa: E402

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
    ok(mgr._groups == [] and mgr._widgets == {} and mgr._timer is None,
       "管理器占位初始值")
    ok(w.scale_getter() == 1.0 and w.topmost_getter() is False,
       "注入回调可调用")

    # 空壳方法一律 NotImplementedError（阶段边界看守）
    raises_not_implemented(lambda: w.update_layout(), "update_layout 未实现")
    raises_not_implemented(lambda: w.set_rows([]), "set_rows 未实现")
    raises_not_implemented(lambda: w.update_data(None), "update_data 未实现")
    raises_not_implemented(
        lambda: w.simulate_data_update("cpu.load", 1.0, mh.STATUS_OK),
        "simulate_data_update 未实现")
    raises_not_implemented(lambda: mgr.tick(), "tick 未实现")
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


if __name__ == "__main__":
    section_a()
    section_b()
    section_c()
    section_d()
    print(f"\n== PASS {PASS} / FAIL {FAIL} ==")
    sys.exit(1 if FAIL else 0)
