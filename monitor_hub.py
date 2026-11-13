# -*- coding: utf-8 -*-
"""
Aura Pixie 资源监视模块 · 接口层（monitor_hub.py）

架构（依据《资源监视集成到Python单文件-方案评估》方式③）：
    MetricsHub ─┬─ SidecarRunner  → hwcollector.exe --stream（LibreHardwareMonitorLib 0.9.6）
                │                    温度 / 功耗 / CPU逐核频率 / GPU / 硬盘SMART / 风扇
                └─ PsutilSource   → 运行时间 / CPU使用率 / 内存 / 网速 / 盘符映射兜底
    侧车断连或不存在时，自动降级为纯 psutil（温度/功耗/频率等项标记为不可用）。

对外核心接口：
    hub = MetricsHub(); hub.start(); ...
    hub.get("cpu.temp")            -> Metric | None
    hub.uptime()                   -> (秒, "H:MM:SS")
    hub.list_disks()               -> [DiskInfo]        （多硬盘顺序罗列）
    hub.list_fans()                -> [FanInfo]         （多风扇顺序罗列）
    hub.snapshot()                 -> {key: Metric}     （UI 一次拉全）
    hub.stop()

状态口径（UI 据此渲染置灰/引导，不抛异常）：
    ok              有可信读数
    need_admin      需管理员权限（+PawnIO 驱动）：CPU温度/功耗/逐核频率/硬盘温度
    unsupported     本机硬件/平台不暴露该传感器（如大量笔记本无风扇读数）
    not_installed   侧车 exe 不存在或无法启动（除 psutil 项外全部不可用）
    stale           采集中断，展示的是最后一次读数

硬约束：
    · LibreHardwareMonitorLib 锁 0.9.6，映射表随该版本标定（本机 i7-11800H +
      RTX 3060 + UHD 核显 + 双 NVMe 实测），升级库版本必须重测。
    · GPU 型号匹配用「家族关键字」而非精确名（独显/NVIDIA/AMD/Radeon → 独显，
      Intel UHD/Iris/核显 → 核显），跨机型可用。
    · 本文件不得 import PySide6——保持可被 tools/ 自检脚本独立运行。
"""

from __future__ import annotations

import ctypes
import json
import os
import string
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

try:
    import psutil
except ImportError:  # 允许在无 psutil 环境下 import 本模块（自检 mock 场景）
    psutil = None

# ---------------------------------------------------------------------------
# 常量与状态
# ---------------------------------------------------------------------------

STATUS_OK = "ok"
STATUS_NEED_ADMIN = "need_admin"
STATUS_UNSUPPORTED = "unsupported"
STATUS_NOT_INSTALLED = "not_installed"
STATUS_STALE = "stale"

#: 历史环形缓冲点数（60 ≈ 1 分钟，侧车 ~1.1s/帧）。关窗不停采，重开秒回曲线。
HISTORY_POINTS = 60

CREATE_NO_WINDOW = 0x08000000

#: 侧车 exe 查找顺序：环境变量 → onefile 解压目录（Nuitka）→ 源码期 collector/pub
_SIDECAR_NAME = "hwcollector.exe"


def find_sidecar() -> Optional[str]:
    env = os.environ.get("AURA_HWCOLLECTOR")
    if env and os.path.isfile(env):
        return env
    # Nuitka onefile：内嵌数据解压在 __file__ 同级；源码期在 collector/pub
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [
        os.path.join(here, _SIDECAR_NAME),
        os.path.join(here, "collector", "pub", _SIDECAR_NAME),
    ]
    for p in cands:
        if os.path.isfile(p):
            return p
    return None


@dataclass
class Metric:
    key: str                 # 指标 key，见 METRIC_DEFS
    label: str               # 中文名（UI 直接展示）
    group: str               # cpu / mem / gpu_i / gpu_d / fan / disk / net / sys
    value: Optional[float]   # 换算后的值（GHz/℃/W/%/GB/MB/s/RPM/秒）；None=无读数
    unit: str
    status: str              # STATUS_* 之一
    source: str = ""         # "sidecar" / "psutil" / "psutil+sidecar"
    ts: float = 0.0          # time.time() 采样时刻
    hardware: str = ""       # 硬件名称（CPU/GPU/硬盘窗格小字标注；无则空）
    total: Optional[float] = None  # 容量总量（与 value 同单位；用量类按比例配色用，V16.2）

    @property
    def available(self) -> bool:
        return self.status == STATUS_OK and self.value is not None


# 指标定义表 = 白名单（加/减指标只改这里，UI 按 group 分组渲染）
# (key, label, group, unit)
METRIC_DEFS: list[tuple[str, str, str, str]] = [
    # sys.uptime（系统运行时间）已废弃（用户拍板）：不再列入监视白名单；
    # psutil 采集与 uptime()/uptime_fmt() 能力保留，未来需要可恢复。
    ("cpu.load",        "CPU利用",             "cpu",   "%"),
    ("cpu.freq",        "CPU频率",             "cpu",   "GHz"),
    ("cpu.temp",        "CPU温度",             "cpu",   "℃"),
    ("cpu.power",       "CPU功耗",             "cpu",   "W"),
    ("mem.load",        "内存利用",            "mem",   "%"),
    ("mem.used",        "内存用量",            "mem",   "GB"),
    ("gpu_i.load",      "GPU利用",             "gpu_i", "%"),
    ("gpu_i.shared",    "共享内存用量",        "gpu_i", "GB"),
    ("net.tx",          "网络发送速率",        "net",   "MB/s"),
    ("net.rx",          "网络接收速率",        "net",   "MB/s"),
    # 动态键（按实例数量展开）：gpu_d.{i}.* / disk.{i}.temp / disk.{i}.usage / fan.{slot}.{i}
]

# 独显全量枚举（用户拍板，参照硬盘）：每块独显 5 参数，多卡直接加设置项进组
# 顺序即 UI 顺序：GPU功耗 紧跟 GPU温度 下方（V16.3 用户拍板，仅独显有此参数）
_GPU_D_DEFS = [("gpu_d.{i}.load",      "GPU利用",       "gpu_d", "%"),
               ("gpu_d.{i}.temp",      "GPU温度",       "gpu_d", "℃"),
               ("gpu_d.{i}.power",     "GPU功耗",       "gpu_d", "W"),
               ("gpu_d.{i}.vram_load", "GPU显存利用",   "gpu_d", "%"),
               ("gpu_d.{i}.vram_used", "GPU显存用量",   "gpu_d", "GB")]
_GPU_D_SUFFIX = {"load": "利用", "temp": "温度", "power": "功耗",
                 "vram_load": "显存利用", "vram_used": "显存用量"}
_DISK_DEFS = [("disk.{i}.temp", "硬盘温度", "disk", "℃"),
              ("disk.{i}.usage", "硬盘占用空间", "disk", "%")]
_FAN_DEFS = [("fan.{slot}.{i}", "风扇转速", "fan", "RPM")]
_FAN_SLOTS = ("cpu", "gpu_d", "mobo")  # 主板风扇；CPU/GPU 风扇按命名启发归类


# ---------------------------------------------------------------------------
# 侧车客户端：进程管理 + 流式解析 + 断连重启
# ---------------------------------------------------------------------------

class SidecarRunner:
    """常驻 hwcollector.exe --stream，守护线程逐行解析 NDJSON。

    重启策略：进程退出/断流超过 stale_sec 秒则重启，连续失败 max_restarts 次后
    静默放弃（上层指标转 not_installed / stale，不弹窗不报错）。

    线程事件分工（防回归）：_stop 只由 stop()/start() 管理，代表「用户要求
    停止」；读取线程 EOF 只置 _reader_done 并退出自己，绝不置 _stop——
    否则看守线程会被连带退出，进程崩溃路径上的重启永远走不到。
    """

    def __init__(self, exe_path: Optional[str] = None, interval_ms: int = 1000,
                 stale_sec: float = 5.0, max_restarts: int = 3,
                 on_frame: Optional[Callable[[dict], None]] = None):
        self.exe = exe_path if exe_path is not None else find_sidecar()
        self.interval_ms = max(200, int(interval_ms))
        self.stale_sec = stale_sec
        self.max_restarts = max_restarts
        self.on_frame = on_frame

        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._watch: Optional[threading.Thread] = None
        self._stop = threading.Event()       # 仅用户停止请求（stop() 置位）
        self._reader_done = threading.Event()  # 当前读取线程已退出（watch 重启时清）
        self._lock = threading.Lock()
        self._latest: Optional[dict] = None      # {"ts":..,"sensors":[..]}
        self._meta: Optional[dict] = None        # {"elevated":..,"hwIndex":[..]}
        self._last_frame_time = 0.0
        self._restarts = 0
        self.alive = False                        # 侧车可用（含曾经可用后 stale）
        self.installed = bool(self.exe and os.path.isfile(self.exe))

    # ------------------------------------------------------------- 生命周期 --
    def start(self) -> bool:
        """启动侧车与读取线程；返回是否成功 spawn（不代表已出帧，出帧看 latest）。

        幂等守卫（V16.2 修复）：进程已活着直接返回，绝不重复 spawn——
        GUI 里预览采集与建窗同步会连续调 start，重复 spawn 会孤立旧读取
        线程、覆盖进程句柄，导致数据帧读取错乱（meta 到而 frame 不到）
        且每次泄漏一个孤儿侧车进程。
        """
        if not self.installed:
            return False
        if self._proc is not None and self._proc.poll() is None:
            self.alive = True
            return True
        self._stop.clear()
        self._reader_done.clear()
        self._restarts = 0
        try:
            self._spawn()
        except OSError:
            self.installed = False
            return False
        self._thread = threading.Thread(target=self._read_loop,
                                        name="hwcollector-reader", daemon=True)
        self._watch = threading.Thread(target=self._watch_loop,
                                       name="hwcollector-watch", daemon=True)
        self._thread.start()
        self._watch.start()
        self.alive = True
        return True

    def stop(self) -> None:
        self._stop.set()
        p = self._proc
        if p is not None:
            try:
                p.terminate()   # 侧车无状态，直接 terminate；不 kill 强杀避免驱动句柄残留
            except OSError:
                pass
        for t in (self._thread, self._watch):
            if t is not None and t.is_alive():
                t.join(timeout=2.0)
        self.alive = False

    def wait_first_frame(self, timeout: float = 15.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self._latest is not None:
                return True
            if not self.alive or self._stop.is_set():
                return False
            time.sleep(0.05)
        return False

    # ------------------------------------------------------------------ 数据 --
    @property
    def elevated(self) -> Optional[bool]:
        return None if self._meta is None else bool(self._meta.get("elevated"))

    @property
    def hw_index(self) -> list[dict]:
        return (self._meta or {}).get("hwIndex", [])

    def latest_frame(self) -> Optional[dict]:
        with self._lock:
            return self._latest

    def is_stale(self) -> bool:
        if self._latest is None:
            # 尚未出帧 ≠ 断流：首帧前的硬件枚举可能超过 stale_sec（本机实测
            # 5~20s 波动）。进程活着就还在启动宽限期内，交给 wait_first_frame
            # 的超时窗口兜底；否则看守会把正常启动误判成断流，重启预算耗尽后
            # 静默放弃，首帧永远等不到。进程已死才按 stale 处理（触发重启）。
            return self._proc is None or self._proc.poll() is not None
        return (time.time() - self._last_frame_time) > self.stale_sec

    # -------------------------------------------------------------- 内部实现 --
    def _spawn(self) -> None:
        assert self.exe is not None
        self._proc = subprocess.Popen(
            [self.exe, "--stream", "--interval", str(self.interval_ms)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            bufsize=1,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            proc = self._proc
            if proc is None or proc.stdout is None:
                break
            line = proc.stdout.readline()
            if not line:  # EOF：进程退出
                break
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue  # 偶发脏行直接跳过
            if obj.get("meta"):
                self._meta = obj
            elif "sensors" in obj:
                with self._lock:
                    self._latest = obj
                self._last_frame_time = time.time()
                self._restarts = 0  # 成功出帧即重置重启预算
                if self.on_frame:
                    try:
                        self.on_frame(obj)
                    except Exception:
                        pass
        # EOF/异常退出只标记「本读取线程结束」，绝不置 _stop：
        # _stop 是用户停止信号，置位会连带杀死看守线程，重启逻辑就成死代码。
        self._reader_done.set()

    def _watch_loop(self) -> None:
        """进程死了或断流 → 重启；连续失败超限 → 静默放弃。

        循环只看 _stop（用户停止）；读取线程是否退出由 _reader_done 表达，
        重启时清掉它再拉新线程。放弃时只置 alive=False 并退出本线程，
        不置 _stop（stop() 仍可正常 join 回收）。
        """
        while not self._stop.wait(1.0):
            proc_dead = self._proc is not None and self._proc.poll() is not None
            reader_gone = self._reader_done.is_set()
            if not (proc_dead or reader_gone or self.is_stale()):
                continue
            if self._restarts >= self.max_restarts:
                self.alive = False
                return
            self._restarts += 1
            try:
                if self._proc is not None:
                    self._proc.terminate()
                # 等旧读取线程收尾（EOF 后很快退出），避免新旧线程短暂并存
                if self._thread is not None and self._thread.is_alive():
                    self._thread.join(timeout=2.0)
                self._reader_done.clear()
                self._spawn()
                self._thread = threading.Thread(target=self._read_loop,
                                                name="hwcollector-reader", daemon=True)
                self._thread.start()
                self.alive = True
            except OSError:
                self.alive = False
                return


# ---------------------------------------------------------------------------
# 传感器 → 指标 key 映射（LibreHardwareMonitorLib 0.9.6 标定，勿盲改）
# ---------------------------------------------------------------------------

def _match_gpu_family(hw_name: str, hw_type: str) -> Optional[str]:
    """独显/核显家族判定。返回 "dedicated" / "integrated" / None。
    用关键字而非精确型号名，跨机型可用；顺序敏感（Iris Xe 也含 Intel）。
    ⚠ 已知限制：radeon 关键字会把 AMD APU 核显（Radeon Vega 系列）误判为
    独显。映射表按 NVIDIA+Intel 机型标定，AMD 机型适配需真机验证后再收紧。"""
    n = (hw_name or "").lower()
    t = (hw_type or "")
    if any(k in n for k in ("nvidia", "geforce", "rtx", "gtx",
                            "radeon", "amd rx", "arc ")) or t == "GpuNvidia" or t == "GpuAmd":
        # Intel Arc 归独显；GpuNvidia/GpuAmd 类型即独显
        return "dedicated"
    if any(k in n for k in ("intel", "uhd", "iris", "hd graphics")) or t in ("GpuIntel",):
        return "integrated"
    # GpuIntegrated/GpuDiscrete 是 LHM 的抽象类型，按类型兜底
    if t == "GpuDiscrete":
        return "dedicated"
    if t == "GpuIntegrated":
        return "integrated"
    return None


@dataclass
class _MapResult:
    """一帧传感器映射后的中间结果（供 MetricsHub 合并）。"""
    fixed: dict[str, tuple[float, str]] = field(default_factory=dict)  # key -> (value, sensor)
    shared_total: Optional[float] = None  # 核显共享内存总量 GB（D3D Shared Memory Total，V16.2）
    disks: list[dict] = field(default_factory=list)   # 每盘: {name, temp, usage_lhm, total_gb}
    gpus_d: list[dict] = field(default_factory=list)  # 每独显: {name, load, temp, power, vram_load, vram_used}
    fans: list[dict] = field(default_factory=list)    # {slot, name, value, hw}
    hw_names: dict = field(default_factory=dict)      # 硬件名: cpu / gpu_i / disk.{i}（窗格小字）


def map_frame(frame: dict, meta: Optional[dict]) -> _MapResult:
    r = _MapResult()
    sensors = frame.get("sensors", [])

    # 独显全量枚举（用户拍板，参照硬盘）：所有独显按 hwOrder 排序逐卡采集，
    # 产出 gpu_d.{i}.* 动态键；核显仍取 order 最小首块（gpu_i.* 固定键）。
    # fam_by_hw：hw 名 → "d"/"i"，映射时据此分流；非目标硬件跳过防 last-wins。
    gpu_i_order: Optional[int] = None
    gpu_i_name: Optional[str] = None
    d_cards: list[tuple[int, str]] = []   # (order, name) 全部独显
    fam_by_hw: dict[str, str] = {}
    for h in (meta or {}).get("hwIndex", []):
        # 仅 GPU 类型硬件参与独显/核显判定：CPU 名含 "intel"（如 11th Gen
        # Intel Core i7）会被关键字误判核显，且 CPU order 更小会抢占目标核显，
        # 导致真核显传感器整卡被跳过、gpu_i.* 恒 unsupported。
        if not str(h.get("type", "")).startswith("Gpu"):
            continue
        fam = _match_gpu_family(h.get("name", ""), h.get("type", ""))
        if fam == "dedicated":
            d_cards.append((h["order"], h["name"]))
        elif fam == "integrated":
            if gpu_i_order is None or h["order"] < gpu_i_order:
                gpu_i_order, gpu_i_name = h["order"], h["name"]
    d_cards.sort(key=lambda x: x[0])
    d_index_by_name: dict[str, int] = {}
    for i, (_o, nm) in enumerate(d_cards):
        fam_by_hw[nm] = "d"
        d_index_by_name[nm] = i
    if gpu_i_name is not None:
        fam_by_hw[gpu_i_name] = "i"
        r.hw_names["gpu_i"] = gpu_i_name

    cpu_clocks: list[float] = []

    for s in sensors:
        hw, hwt, sen, name = s.get("hw", ""), s.get("hwType", ""), s.get("sensor", ""), s.get("name", "")
        val = s.get("value")

        # ---- CPU ----
        if hwt == "Cpu":
            r.hw_names["cpu"] = hw   # 硬件名（窗格小字标注）
            if sen == "Load" and name == "CPU Total":
                r.fixed["cpu.load"] = (val, "CPU Total")
            elif sen == "Temperature" and name == "CPU Package":
                _cpu_temp_put(r, val)
            elif sen == "Power" and name in ("CPU Package", "CPU Platform"):
                # Package 优先（不含 SoC 外的平台功耗）；缺失时不降级到 Platform。
                # ⚠ 非管理员下该传感器存在但恒为 0 → 0 视为无读数（need_admin 信号）
                if name == "CPU Package" and val:
                    r.fixed["cpu.power"] = (val, name)
            elif sen == "Clock" and name.startswith("CPU Core #") and val:
                cpu_clocks.append(val)  # MHz

        # ---- 内存（Total Memory = 物理内存条聚合，勿用 Virtual Memory）----
        elif hwt == "Memory" and hw == "Total Memory":
            if sen == "Load" and name == "Memory":
                r.fixed["mem.load"] = (val, "Memory")
            elif sen == "Data" and name == "Memory Used":
                r.fixed["mem.used"] = (val, "Memory Used")  # 单位 GB

        # ---- GPU ----
        elif hwt.startswith("Gpu"):
            fam = fam_by_hw.get(hw)
            if fam is None:
                if meta is not None:
                    # hwIndex 已选定目标卡：其余同家族卡整卡跳过（多独显防 last-wins）
                    continue
                # 无 meta（裸帧调用）退回按类型判家族，保持旧行为
                fam = _gpu_family_from_type(hwt)
            if fam == "d":
                # 逐卡采集（全量枚举）：按 hw 名定位卡序号，写入该卡 dict
                idx = d_index_by_name.get(hw)
                if idx is None:
                    continue
                while len(r.gpus_d) <= idx:
                    r.gpus_d.append({"name": "", "load": None, "temp": None,
                                     "power": None,
                                     "vram_load": None, "vram_used": None,
                                     "vram_total": None})
                g = r.gpus_d[idx]
                g["name"] = hw
                if sen == "Load" and name == "GPU Core":
                    g["load"] = val
                elif sen == "Temperature" and name == "GPU Core":
                    g["temp"] = val
                elif sen == "Power" and name.startswith("GPU"):
                    # GPU功耗（V16.3 新增，仅独显）：NVIDIA「GPU Power」、
                    # AMD「GPU Core」等；多路取最大（整卡功耗口径）
                    if g["power"] is None or val > g["power"]:
                        g["power"] = val
                elif sen == "Load" and name == "GPU Memory":
                    g["vram_load"] = val
                elif sen == "SmallData" and name == "GPU Memory Used":
                    g["vram_used"] = val / 1024.0   # MB→GB
                elif sen == "SmallData" and name == "GPU Memory Total":
                    g["vram_total"] = val / 1024.0
            elif fam == "i":
                if sen == "Load" and name == "D3D 3D":
                    r.fixed["gpu_i.load"] = (val, name)
                elif sen == "SmallData" and name == "D3D Shared Memory Used":
                    r.fixed["gpu_i.shared"] = (val / 1024.0, name)  # MB→GB
                elif sen == "SmallData" and name == "D3D Shared Memory Total":
                    r.shared_total = val / 1024.0   # MB→GB（用量按比例配色，V16.2）

        # ---- 硬盘：顺序 = 侧车枚举顺序（hwIndex 已给 order）----
        elif hwt == "Storage":
            _disk_update(r, hw, sen, name, val, meta)

        # ---- 风扇：LHM SensorType=Fan，hwType 常为 Motherboard；GPU 风扇在子硬件 ----
        if sen == "Fan" and val is not None:
            ln = name.lower()
            if "cpu" in ln:
                slot = "cpu"
            elif fam_by_hw.get(hw) == "d" or "gpu" in ln:
                slot = "gpu_d"
            else:
                slot = "mobo"
            r.fans.append({"slot": slot, "name": name, "value": val, "hw": hw})

    if cpu_clocks:
        r.fixed["cpu.freq"] = (max(cpu_clocks) / 1000.0, "CPU Core Max MHz")

    # 显存使用率缺失时用 Used/Total 兜底（AMD 卡常见只给绝对值）——逐卡独立计算
    for g in r.gpus_d:
        if g["vram_load"] is None and g["vram_used"] is not None and g["vram_total"]:
            g["vram_load"] = g["vram_used"] / g["vram_total"] * 100.0

    return r


def _gpu_family_from_type(hwt: str) -> Optional[str]:
    if hwt in ("GpuNvidia", "GpuAmd", "GpuDiscrete"):
        return "d"
    if hwt in ("GpuIntel", "GpuIntegrated"):
        return "i"
    return None


def _cpu_temp_put(r: _MapResult, val) -> None:
    """CPU 温度取值：null 直接丢弃（侧车已过滤无值传感器，此处双保险）。
    need_admin / unsupported 的信号判定在 map_absent_signals，不在本函数。"""
    if val is None:
        return
    r.fixed["cpu.temp"] = (val, "CPU Package")


def map_absent_signals(sensors: list[dict]) -> dict[str, str]:
    """反向判断缺失信号：侧车只回传有值传感器（null 已被过滤），所以
    「整帧无某类候选行」本身就是信号——对需要管理员的指标标 need_admin，
    对平台不暴露的指标（如风扇）标 unsupported。
    返回 key -> STATUS_NEED_ADMIN/STATUS_UNSUPPORTED 提示。
    注意：非管理员时 CPU 温度整族消失，故只要没有 Temperature 行即按
    need_admin 处理，不区分具体槽位名。"""
    out: dict[str, str] = {}
    has_cpu_temp = any(s["hwType"] == "Cpu" and s["sensor"] == "Temperature" for s in sensors)
    has_cpu_clock = any(s["hwType"] == "Cpu" and s["sensor"] == "Clock" and
                        s["name"].startswith("CPU Core #") for s in sensors)
    has_cpu_power = any(s["hwType"] == "Cpu" and s["sensor"] == "Power" and
                        s["value"] > 0 for s in sensors)
    has_storage = any(s["hwType"] == "Storage" for s in sensors)
    has_fan = any(s["sensor"] == "Fan" for s in sensors)
    if not has_cpu_temp:
        out["cpu.temp"] = STATUS_NEED_ADMIN
    if not has_cpu_clock:
        out["cpu.freq"] = STATUS_NEED_ADMIN
    if not has_cpu_power:
        out["cpu.power"] = STATUS_NEED_ADMIN
    if not has_storage:
        # 非管理员下存储设备整类不枚举
        out["__storage__"] = STATUS_NEED_ADMIN
    if not has_fan:
        out["__fan__"] = STATUS_UNSUPPORTED  # 提权也拿不到时 = 平台不暴露
    return out


def _disk_update(r: _MapResult, hw: str, sen: str, name: str, val, meta) -> None:
    for d in r.disks:
        if d["name"] == hw:
            break
    else:
        d = {"name": hw, "temp": None, "usage_lhm": None, "total_gb": None}
        r.disks.append(d)
    if sen == "Temperature" and name == "Composite Temperature" and d["temp"] is None:
        d["temp"] = val
    elif sen == "Load" and name == "Used Space" and d["usage_lhm"] is None:
        d["usage_lhm"] = val
    elif sen == "Data" and name == "Total Space" and d["total_gb"] is None:
        d["total_gb"] = val


# ---------------------------------------------------------------------------
# psutil 数据源（运行时间/CPU%/内存兜底/网速差分/盘符→物理盘映射）
# ---------------------------------------------------------------------------

# ---- Windows 盘符 → 物理盘号 映射（IOCTL_STORAGE_GET_DEVICE_NUMBER，免管理员）----
_IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x002D1080
_FILE_DEVICE_DISK = 0x00000007


class _STORAGE_DEVICE_NUMBER(ctypes.Structure):
    _fields_ = [("DeviceType", ctypes.c_uint32),
                ("DeviceNumber", ctypes.c_uint32),
                ("PartitionNumber", ctypes.c_uint32)]


def map_drive_letters() -> dict[int, list[str]]:
    """{physical_drive_number: ["C:", "D:", ...]}；失败返回空表（优雅降级）。"""
    if os.name != "nt":
        return {}
    out: dict[int, list[str]] = {}
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                     ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                     ctypes.c_void_p]
    kernel32.DeviceIoControl.restype = ctypes.c_int
    kernel32.DeviceIoControl.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                                         ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
                                         ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    # 以 0 访问权限打开卷句柄：只查 IOCTL 元数据，不需要管理员，也不受写锁影响
    open_existing = 3
    INVALID = ctypes.c_void_p(-1).value
    for letter in string.ascii_uppercase:
        vol = f"\\\\.\\{letter}:"
        h = kernel32.CreateFileW(vol, 0, 0x7, None, open_existing, 0x80, None)
        if h in (None, INVALID, -1, 0xFFFFFFFFFFFFFFFF):
            continue
        try:
            buf = _STORAGE_DEVICE_NUMBER()
            ret = ctypes.c_uint32(0)
            ok = kernel32.DeviceIoControl(h, _IOCTL_STORAGE_GET_DEVICE_NUMBER,
                                          None, 0, ctypes.byref(buf), ctypes.sizeof(buf),
                                          ctypes.byref(ret), None)
            if ok and ret.value >= ctypes.sizeof(buf) and buf.DeviceType == _FILE_DEVICE_DISK:
                out.setdefault(int(buf.DeviceNumber), []).append(letter)
        finally:
            kernel32.CloseHandle(h)
    return out


class PsutilSource:
    """psutil 差分与瞬时采样。所有方法线程安全（psutil 本身是），
    速率类指标需在固定节拍调用 sample_rates() 做窗口差分。"""

    def __init__(self) -> None:
        self._prev_net: Optional[tuple[float, int, int]] = None  # (t, bytes_sent, bytes_recv)
        self._prev_disk: Optional[dict] = None                   # {drive: (t, rtime+wtime)}
        self._drive_map: Optional[dict[int, list[str]]] = None
        self._partitions: list = []

    # ------------------------------------------------------------- 瞬时值 ----
    def uptime(self) -> Optional[float]:
        return time.time() - psutil.boot_time() if psutil else None

    def cpu_load(self) -> Optional[float]:
        # interval=None：与上次调用区间的均值，由 hub 轮询节拍保证窗口
        return psutil.cpu_percent(interval=None) if psutil else None

    def cpu_freq_ghz(self) -> Optional[float]:
        if not psutil:
            return None
        f = psutil.cpu_freq()
        return (f.current / 1000.0) if f and f.current else None

    def mem(self) -> tuple[Optional[float], Optional[float], Optional[float]]:
        """(使用率%, 已用GB, 总量GB)；总量供用量类按比例配色（V16.2）。"""
        if not psutil:
            return None, None, None
        vm = psutil.virtual_memory()
        return vm.percent, vm.used / (1024 ** 3), vm.total / (1024 ** 3)

    def disk_usage_pct(self, total_gb: Optional[float] = None) -> Optional[float]:
        """按容量把 LHM 盘匹配到物理盘，返回该物理盘已挂载分区的最大已用占比。

        ⚠ 不能用 LHM 枚举序号当物理盘号——两者无对应关系。
        匹配策略：每个物理盘号 → 盘符 → 分区容量求和，与 total_gb 最接近者胜出；
        匹配误差 >15% 视为不可信返回 None（宁缺勿错）。
        """
        if not psutil:
            return None
        if self._drive_map is None:
            self._refresh_partitions()
        if not self._partitions:
            return None
        if total_gb is None:
            # 无容量信息时退化为全盘最大已用占比（单盘场景正确，多盘标注来源）
            try:
                return max(psutil.disk_usage(p.mountpoint).percent for p in self._partitions)
            except OSError:
                return None
        per_drive: dict[int, tuple[float, list[str]]] = {}
        for phys, letters in (self._drive_map or {}).items():
            cap, parts = 0.0, []
            for part in self._partitions:
                mount = part.device.rstrip("\\").rstrip("/")  # "C:"
                if any(mount.upper() == letter.upper() for letter in letters):
                    cap += part.total / 1e9  # 十进制 GB，与 SMART Total Space 同口径
                    parts.append(mount)
            if parts:
                per_drive[phys] = (cap, parts)
        target = total_gb
        best, best_err = None, None
        for cap, parts in per_drive.values():
            err = abs(cap - target) / max(target, 1e-9)
            if (best_err is None or err < best_err) and err <= 0.15:
                best, best_err = parts, err
        if not best:
            return None
        pct = None
        for mount in best:
            try:
                u = psutil.disk_usage(mount + "\\").percent
            except OSError:
                continue
            if pct is None or u > pct:
                pct = u
        return pct

    def _refresh_partitions(self) -> None:
        self._drive_map = map_drive_letters()
        self._partitions = psutil.disk_partitions(all=False) if psutil else []

    # ------------------------------------------------------------- 速率值 ----
    def net_rates_mb(self) -> tuple[Optional[float], Optional[float]]:
        """返回本窗口的 (发送, 接收) MB/s。首次调用返回 (None, None) 建基线。"""
        if not psutil:
            return None, None
        now = time.time()
        io = psutil.net_io_counters()
        cur = (now, io.bytes_sent, io.bytes_recv)
        if self._prev_net is None:
            self._prev_net = cur
            return None, None
        dt = max(cur[0] - self._prev_net[0], 1e-6)
        tx = (cur[1] - self._prev_net[1]) / dt / (1024 ** 2)
        rx = (cur[2] - self._prev_net[2]) / dt / (1024 ** 2)
        self._prev_net = cur
        return max(tx, 0.0), max(rx, 0.0)

    def disk_busy_pct(self) -> dict[int, Optional[float]]:
        """物理盘 IO 忙碌率（备用口径，非用户选定口径；保留供 UI 后续切换）。"""
        if not psutil:
            return {}
        now = time.time()
        d = psutil.disk_io_counters(perdisk=True)
        res: dict[int, Optional[float]] = {}
        if self._prev_disk is None:
            self._prev_disk = {k: (now, v) for k, v in d.items()}
            return {}
        for key, counters in d.items():
            prev = self._prev_disk.get(key)
            if prev is None:
                continue
            dt = max(now - prev[0], 1e-6)
            busy = getattr(counters, "read_time", 0) + getattr(counters, "write_time", 0)
            pbusy = getattr(prev[1], "read_time", 0) + getattr(prev[1], "write_time", 0)
            pct = (busy - pbusy) / (dt * 1000.0) * 100.0
            idx = None
            if key.lower().startswith("physicaldrive"):
                try:
                    idx = int(key.lower().replace("physicaldrive", ""))
                except ValueError:
                    pass
            if idx is not None:
                res[idx] = min(pct, 100.0)
        self._prev_disk = {k: (now, v) for k, v in d.items()}
        return res


@dataclass
class FanInfo:
    slot: str            # cpu / gpu_d / mobo
    index: int           # 同槽位内序号，多风扇顺序罗列
    name: str            # 传感器原始名（"Fan #1"/"CPU Fan"…）
    value: Optional[float]
    status: str


# ---------------------------------------------------------------------------
# MetricsHub：聚合出口（UI 只认这一层）
# ---------------------------------------------------------------------------

class MetricsHub:
    """采集聚合层。线程模型：侧车读取线程更新最新帧（SidecarRunner 内部锁），
    本类不轮询；snapshot()/get() 在调用线程把最新帧映射成 Metric（映射成本
    ~200 行 dict，微秒级）。psutil 速率类指标由后台 poller 线程按 poll_ms 固定
    节拍差分，保证窗口准确。
    注：get()/list_disks()/list_fans() 各自重复映射同一帧，微秒级可接受；
    监视 UI 接入时改为一帧映射一次复用。"""

    def __init__(self, sidecar_path: Optional[str] = None,
                 interval_ms: int = 1000, poll_ms: int = 1000,
                 max_fans: int = 8, max_disks: int = 8):
        self.runner = SidecarRunner(exe_path=sidecar_path, interval_ms=interval_ms)
        self.ps = PsutilSource()
        self.poll_ms = max(200, int(poll_ms))
        self.max_fans = max_fans
        self.max_disks = max_disks
        self._poller: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        # psutil 侧最新值（poller 写、snapshot 读）
        self._ps_cache: dict[str, tuple[float, Optional[float]]] = {}
        # 历史环形缓冲（poller 写、history() 读）：关窗不停采，重开秒回曲线。
        # 每键一个 deque(maxlen=HISTORY_POINTS)；值 None=无读数（sparkline 断线）。
        self._history: dict[str, deque] = {}
        self._history_lock = threading.Lock()

    # ------------------------------------------------------------- 生命周期 --
    def start(self) -> None:
        # 幂等守卫（照 SidecarRunner.start 先例）：poller 活着直接返回，绝不
        # 重复建线程——预览采集与建窗同步会连续调 start，重复 spawn 会累积
        # 多个 poller 并行差分（网速窗口被缩短、history 倍速积累、CPU 空耗），
        # 且 stop() 只 join 最新一个，其余泄漏。stop 后 is_alive()=False 可重建。
        if self._poller is not None and self._poller.is_alive():
            return
        self.runner.start()
        # 立即采一轮基线（cpu_percent / 网速 / 盘忙碌首次调用建锚点）
        self._poll_ps()
        self._stop.clear()
        self._poller = threading.Thread(target=self._poll_loop,
                                        name="metrics-hub-poller", daemon=True)
        self._poller.start()

    def stop(self) -> None:
        self._stop.set()
        if self._poller is not None and self._poller.is_alive():
            self._poller.join(timeout=2.0)
        self.runner.stop()

    def _poll_loop(self) -> None:
        while not self._stop.wait(self.poll_ms / 1000.0):
            self._poll_ps()

    def _poll_ps(self) -> None:
        now = time.time()
        cache = {}
        tx, rx = self.ps.net_rates_mb()
        cache["net.tx"] = (now, tx)
        cache["net.rx"] = (now, rx)
        cache["sys.uptime"] = (now, self.ps.uptime())
        cache["cpu.load"] = (now, self.ps.cpu_load())
        mload, mused, mtotal = self.ps.mem()
        cache["mem.load"] = (now, mload)
        cache["mem.used"] = (now, mused)
        cache["mem.total"] = (now, mtotal)
        cache["cpu.freq_ps"] = (now, self.ps.cpu_freq_ghz())
        # 预留口径（当前无出口）：IO 忙碌率供后续 UI「容量/IO」切换用，
        # 用户已定硬盘使用率=容量使用率，见 disk_usage_pct
        cache["disk_busy"] = (now, self.ps.disk_busy_pct())
        with self._lock:
            self._ps_cache = cache
        # 历史积累：每拍把当前全量快照的值入环形缓冲（关窗也持续，重开秒回曲线）
        self._record_history()

    def _record_history(self) -> None:
        """把当前快照各键值入历史环形缓冲（None=无读数）。后台 poller 调用。"""
        try:
            snap = self.snapshot()
            with self._history_lock:
                for key, m in snap.items():
                    dq = self._history.get(key)
                    if dq is None:
                        dq = deque(maxlen=HISTORY_POINTS)
                        self._history[key] = dq
                    dq.append(m.value if m.available else None)
        except Exception:  # noqa: BLE001 - 历史积累失败不影响采集主流程
            pass

    def history(self, key: str) -> list:
        """读取某键历史环形缓冲（副本，最旧→最新）；无记录返回空列表。

        UI 重开单元窗时用它回填 sparkline，无需等待新帧积累。
        """
        with self._history_lock:
            dq = self._history.get(key)
            return list(dq) if dq is not None else []

    def _ps(self, key: str) -> tuple[Optional[float], float]:
        with self._lock:
            hit = self._ps_cache.get(key)
        return (hit[1], hit[0]) if hit else (None, 0.0)

    # ------------------------------------------------------------ 核心出口 ---
    def uptime(self) -> tuple[Optional[float], str]:
        """(运行秒, "H:MM:SS")。需求口径：时分秒。"""
        v, _ = self._ps("sys.uptime")
        if v is None:
            return None, "--:--:--"
        secs = int(v)
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        return v, f"{h}:{m:02d}:{s:02d}"

    def snapshot(self) -> dict[str, Metric]:
        """一次拉全所有指标。key 全集 = METRIC_DEFS 固定键 + disk.{i}.* + fan.{slot}.{i}。"""
        out: dict[str, Metric] = {}
        frame = self.runner.latest_frame()
        meta = self.runner._meta
        mr = map_frame(frame, meta) if frame else _MapResult()
        absent = map_absent_signals(frame.get("sensors", [])) if frame else {}
        now = time.time()

        # 侧车状态机：not_installed → starting(frame 未到) → ok / stale
        if not self.runner.installed:
            st_sidecar = STATUS_NOT_INSTALLED
        elif frame is None:
            st_sidecar = STATUS_STALE          # 正在启动/尚未出帧，勿误标 need_admin
        elif self.runner.is_stale():
            st_sidecar = STATUS_STALE          # 断流，展示旧值语义由 UI 处理
        else:
            st_sidecar = STATUS_OK
        sidecar_ok = st_sidecar == STATUS_OK

        def mk(key: str, val, source: str, status: str = STATUS_OK) -> Metric:
            d = _def(key)
            return Metric(key=key, label=d[1], group=d[2], value=val,
                          unit=d[3], status=status, source=source, ts=now)

        # ---- 固定键：优先侧车，psutil 兜底 ----
        # sys.uptime 已废弃：不再产出快照键（_ps_cache 仍采集，uptime() 能力保留）

        # CPU 使用率：psutil 为准（与任务管理器同口径、采样连续），侧车仅作 psutil 缺失兜底
        v_ps, v_ps_ts = self._ps("cpu.load")
        if v_ps is not None and now - v_ps_ts < self.poll_ms * 3 / 1000.0:
            out["cpu.load"] = mk("cpu.load", v_ps, "psutil")
        elif "cpu.load" in mr.fixed:
            out["cpu.load"] = mk("cpu.load", mr.fixed["cpu.load"][0], "sidecar")
        else:
            out["cpu.load"] = mk("cpu.load", None, "psutil", STATUS_STALE)

        # CPU 频率：侧车逐核最大（真实睿频）> psutil（Windows 下常为标称值）
        if "cpu.freq" in mr.fixed and sidecar_ok:
            out["cpu.freq"] = mk("cpu.freq", mr.fixed["cpu.freq"][0], "sidecar")
        else:
            v, t = self._ps("cpu.freq_ps")
            status = STATUS_OK if v is not None else (
                st_sidecar if not sidecar_ok else absent.get("cpu.freq", STATUS_NEED_ADMIN))
            out["cpu.freq"] = mk("cpu.freq", v, "psutil", status)

        for key in ("cpu.temp", "cpu.power"):
            if key in mr.fixed and sidecar_ok:
                out[key] = mk(key, mr.fixed[key][0], "sidecar")
            else:
                out[key] = mk(key, None, "sidecar",
                              st_sidecar if not sidecar_ok else absent.get(key, STATUS_NEED_ADMIN))

        # 内存：psutil 为准（口径稳定），侧车留作对照
        v, t = self._ps("mem.load")
        out["mem.load"] = mk("mem.load", v, "psutil",
                             STATUS_OK if v is not None else STATUS_STALE)
        v, t = self._ps("mem.used")
        out["mem.used"] = mk("mem.used", v, "psutil",
                             STATUS_OK if v is not None else STATUS_STALE)
        # 总量注入（V16.2 用量按比例配色）：64G 用 16G 不再误落红段
        out["mem.used"].total = self._ps("mem.total")[0]

        for key in ("gpu_i.load", "gpu_i.shared"):
            if key in mr.fixed and sidecar_ok:
                out[key] = mk(key, mr.fixed[key][0], "sidecar")
            elif not sidecar_ok:
                out[key] = mk(key, None, "sidecar", st_sidecar)
            else:
                # 侧车正常但无该槽位 → 本机没有对应 GPU（如无核显机型）
                out[key] = mk(key, None, "sidecar", STATUS_UNSUPPORTED)
        # 共享内存总量（D3D Shared Memory Total，V16.2 按比例配色）
        out["gpu_i.shared"].total = mr.shared_total

        # ---- 独显（全量枚举，参照硬盘）：每卡 5 参数 gpu_d.{i}.* ----
        gpus_d = mr.gpus_d
        if not gpus_d:
            # 无独显：产出卡0占位（无值也罗列，换机可用），状态同侧车口径
            st = st_sidecar if not sidecar_ok else STATUS_UNSUPPORTED
            for suffix in ("load", "temp", "power", "vram_load", "vram_used"):
                k = f"gpu_d.0.{suffix}"
                out[k] = mk(k, None, "sidecar", st)
        for i, g in enumerate(gpus_d):
            st = st_sidecar if not sidecar_ok else STATUS_OK
            for suffix in ("load", "temp", "power", "vram_load", "vram_used"):
                k = f"gpu_d.{i}.{suffix}"
                out[k] = mk(k, g.get(suffix), "sidecar", st)
            # 显存总量（GPU Memory Total，V16.2 按比例配色）
            out[f"gpu_d.{i}.vram_used"].total = g.get("vram_total")

        for key in ("net.tx", "net.rx"):
            v, t = self._ps(key)
            out[key] = mk(key, v, "psutil",
                          STATUS_OK if v is not None else STATUS_STALE)

        # ---- 硬盘（多盘顺序罗列；使用率=容量使用率，用户已定口径）----
        disks = mr.disks[: self.max_disks]
        if not disks:
            if sidecar_ok:
                # 侧车正常却无任何存储设备 = 非管理员（存储整类不枚举）
                st = absent.get("__storage__", STATUS_NEED_ADMIN)
            else:
                st = st_sidecar
            out["disk.0.temp"] = mk("disk.0.temp", None, "sidecar", st)
            out["disk.0.usage"] = mk("disk.0.usage", None, "sidecar", st)
        for i, d in enumerate(disks):
            # 与 GPU 各键同口径：侧车断流时旧帧值一律标 stale（展示旧值由 UI 处理），
            # 不因「值还在」就标 ok；无值时同样落 stale/not_installed。
            st = st_sidecar if not sidecar_ok else STATUS_OK
            out[f"disk.{i}.temp"] = mk(f"disk.{i}.temp", d["temp"], "sidecar", st)
            usage = d["usage_lhm"]
            src = "sidecar"
            if usage is None:
                # SMART 无 Used Space → 按容量匹配到盘符，用分区容量占比兜底
                usage = self.ps.disk_usage_pct(d.get("total_gb"))
                src = "psutil"
            out[f"disk.{i}.usage"] = mk(f"disk.{i}.usage", usage, src, st)

        # ---- 风扇（多风扇按槽位顺序罗列）----
        # 断流口径与 GPU/硬盘一致：侧车 stale 时旧帧风扇值也标 stale，
        # 不因「值还在」就标 ok（旧实现恒 ok，断流时误导用户以为读数新鲜）。
        fans = self.list_fans()
        for f in fans:
            key = f"fan.{f.slot}.{f.index}"
            st_fan = st_sidecar if not sidecar_ok else STATUS_OK
            out[key] = mk(key, f.value, "sidecar",
                          st_fan if f.value is not None else STATUS_STALE)
        if not fans:
            if not sidecar_ok:
                reason = st_sidecar
            elif self.runner.elevated is False:
                # 非管理员：无法区分"权限挡着"还是"平台没暴露"，一律引导提权
                reason = STATUS_NEED_ADMIN
            else:
                # 已提权仍无风扇传感器 = 本机平台不暴露（笔记本 EC 控制常见）
                reason = STATUS_UNSUPPORTED
            for slot in _FAN_SLOTS:
                out[f"fan.{slot}.0"] = mk(f"fan.{slot}.0", None, "sidecar", reason)

        # ---- 硬件名称注入（CPU/GPU/硬盘窗格小字标注；深白浅黑由 UI 渲染）----
        for k, m in out.items():
            if k.startswith("cpu."):
                m.hardware = mr.hw_names.get("cpu", "")
            elif k.startswith("gpu_i."):
                m.hardware = mr.hw_names.get("gpu_i", "")
            elif k.startswith("gpu_d."):
                gi = k.split(".")[1]
                if gi.isdigit() and int(gi) < len(mr.gpus_d):
                    m.hardware = mr.gpus_d[int(gi)].get("name", "")
            elif k.startswith("disk."):
                di = k.split(".")[1]
                if di.isdigit() and int(di) < len(mr.disks):
                    m.hardware = mr.disks[int(di)].get("name", "")

        return out

    def get(self, key: str) -> Optional[Metric]:
        return self.snapshot().get(key)

    # ------------------------------------------------------------ 枚举接口 ---
    def list_disks(self, snapshot: Optional[dict] = None) -> list[dict]:
        """[{index, name, temp, usage_pct, usage_src}]，顺序=侧车枚举顺序=物理盘展示顺序。"""
        frame = self.runner.latest_frame()
        if not frame:
            return []
        mr = map_frame(frame, self.runner._meta)
        snap = snapshot if snapshot is not None else self.snapshot()
        out = []
        for i, d in enumerate(mr.disks[: self.max_disks]):
            m = snap.get(f"disk.{i}.usage")
            out.append({
                "index": i,
                "name": d["name"],
                "temp": d["temp"],
                "usage_pct": m.value if m else None,
                "usage_src": m.source if m else "",
            })
        return out

    def list_gpus_d(self, snapshot: Optional[dict] = None) -> list[dict]:
        """[{index, name}]，独显全量枚举，顺序=侧车 hwIndex order（参照 list_disks）。"""
        frame = self.runner.latest_frame()
        if not frame:
            return []
        mr = map_frame(frame, self.runner._meta)
        return [{"index": i, "name": g["name"]} for i, g in enumerate(mr.gpus_d)]

    def list_fans(self) -> list[FanInfo]:
        """多风扇按槽位顺序罗列（cpu / gpu_d / mobo，同槽位内按出现顺序编号）。"""
        frame = self.runner.latest_frame()
        out: list[FanInfo] = []
        if not frame:
            return out
        mr = map_frame(frame, self.runner._meta)
        counters: dict[str, int] = {slot: 0 for slot in _FAN_SLOTS}
        for f in mr.fans[: self.max_fans]:
            slot = f["slot"] if f["slot"] in counters else "mobo"
            idx = counters[slot]
            counters[slot] += 1
            out.append(FanInfo(slot=slot, index=idx, name=f["name"],
                               value=f["value"], status=STATUS_OK))
        return out

    # ---- 便捷格式化（UI 直接用，避免各处重复换算）----
    @staticmethod
    def fmt(m: Optional[Metric], nd: int = 1) -> str:
        if m is None or m.value is None:
            if m is not None and m.status == STATUS_NEED_ADMIN:
                return "需管理员"
            if m is not None and m.status == STATUS_UNSUPPORTED:
                return "不可用"
            return "--"
        if m.key == "sys.uptime":
            return MetricsHub.uptime_fmt(m.value)
        if m.key.startswith("fan."):
            return f"{m.value:.0f} {m.unit}"
        return f"{m.value:.{nd}f} {m.unit}"

    @staticmethod
    def uptime_fmt(secs: Optional[float]) -> str:
        if secs is None:
            return "--:--:--"
        h, rem = divmod(int(secs), 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}"

    @staticmethod
    def fmt_value(m: Optional[Metric], nd: int = 1) -> str:
        """纯数值文本（不带单位）——监视窗 B 列数值用，单位独立画在 C 列。

        无读数返回 "--"（状态文案由 UI 的状态小字呈现，不占数值列）。
        换算口径与 fmt 完全一致（风扇整数 RPM、其余 nd 位小数）。
        """
        if m is None or m.value is None:
            return "--"
        if m.key.startswith("fan."):
            return f"{m.value:.0f}"
        return f"{m.value:.{nd}f}"


def _def(key: str) -> tuple[str, str, str, str]:
    """key → 定义行；动态键按模板还原。"""
    for row in METRIC_DEFS:
        if row[0] == key:
            return row
    parts = key.split(".")
    # 独显动态键 gpu_d.{i}.{suffix}：标签不带（独显）与「·卡N」后缀
    # （V16.2 用户拍板：设备名小字已能区分多卡，标签统一精简）。
    if (len(parts) == 3 and parts[0] == "gpu_d" and parts[1].isdigit()
            and parts[2] in _GPU_D_SUFFIX):
        label = "GPU" + _GPU_D_SUFFIX[parts[2]]
        unit = {"load": "%", "temp": "℃", "power": "W",
                "vram_load": "%", "vram_used": "GB"}[parts[2]]
        return (key, label, "gpu_d", unit)
    for tpl, label, group, unit in _DISK_DEFS:
        # 按完整模板匹配：disk.{i}.temp / disk.{i}.usage 两个模板头都是 disk，
        # 只比前缀会让 usage 恒命中第一个 temp 模板（标签错标成「硬盘温度」）。
        tparts = tpl.split(".")
        if (len(parts) == len(tparts) == 3 and parts[0] == tparts[0]
                and parts[1].isdigit() and parts[2] == tparts[2]):
            # V16.2：不带「·盘N」后缀（设备名小字区分多盘）
            return (key, label, group, unit)
    if key.startswith("fan."):
        parts = key.split(".")
        slot = parts[1]
        idx = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        label = {"cpu": "CPU风扇", "gpu_d": "GPU风扇", "mobo": "主板风扇"}.get(slot, slot)
        if idx > 0:
            label += f"·{idx + 1}"
        return (key, label + "转速", "fan", "RPM")
    return (key, key, key.split(".")[0], "")


# ---------------------------------------------------------------------------
# 独立运行：开发期冒烟（python monitor_hub.py [采样秒数]）
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 4.0
    hub = MetricsHub()
    hub.start()
    ok = hub.runner.wait_first_frame(timeout=15)
    print(f"sidecar installed={hub.runner.installed} first_frame={ok} "
          f"elevated={hub.runner.elevated}")
    t_end = time.time() + dur
    snap = {}
    while time.time() < t_end:
        time.sleep(0.5)
        snap = hub.snapshot()
    up = hub.uptime()
    print(f"系统运行时间: {up[1]}")
    order = ["cpu.load", "cpu.freq", "cpu.temp", "cpu.power",
             "mem.load", "mem.used", "gpu_i.load", "gpu_i.shared",
             "net.tx", "net.rx"]
    for k in order:
        m = snap.get(k)
        print(f"{m.label:<16} {MetricsHub.fmt(m):>12}   [{m.status}]  {m.hardware}")
    for g in hub.list_gpus_d():
        for suffix in ("load", "temp", "power", "vram_load", "vram_used"):
            m = snap.get(f"gpu_d.{g['index']}.{suffix}")
            if m:
                print(f"{m.label:<16} {MetricsHub.fmt(m):>12}   [{m.status}]  {m.hardware}")
    for d in hub.list_disks():
        print(f"硬盘#{d['index'] + 1} {d['name'][:28]:<28} 温度={d['temp']}℃ "
              f"使用率={d['usage_pct']}%({d['usage_src']})")
    for f in hub.list_fans():
        print(f"风扇 {f.slot}#{f.index} {f.name}: {f.value} RPM")
    if not hub.list_fans():
        print("风扇: 本机无传感器输出（平台不暴露或需管理员）")
    hub.stop()
    print("done")
