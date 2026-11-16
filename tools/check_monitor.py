# -*- coding: utf-8 -*-
"""monitor_hub 接口自检（离屏，无 UI）。

两节：
  A. mock 帧断言——映射表口径、单位换算、动态键、状态判定（零硬件依赖，CI 可跑）
  B. 真实冒烟——本机制品侧车 + psutil 端到端（侧车缺失时自动 SKIP，不算失败）

跑法：  python tools/check_monitor.py
退出码： 0 全过 / 1 有失败
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import monitor_hub as mh  # noqa: E402

PASS = FAIL = SKIP = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {msg}")


def near(a, b, tol, msg):
    ok(a is not None and abs(a - b) <= tol, f"{msg} (got {a}, want ~{b})")


# ---------------------------------------------------------------------------
# A. mock 帧 —— 按本机(管理员)实测的 LHM 0.9.6 传感器形状手工构造
# ---------------------------------------------------------------------------
def fake_sensors():
    S = lambda hw, ht, sen, name, v: {  # noqa: E731
        "hw": hw, "hwType": ht, "parent": None, "sensor": sen, "name": name, "value": v}
    return [
        S("i7-11800H", "Cpu", "Load", "CPU Total", 42.0),
        S("i7-11800H", "Cpu", "Temperature", "CPU Package", 69.0),
        S("i7-11800H", "Cpu", "Power", "CPU Package", 13.58),
        S("i7-11800H", "Cpu", "Power", "CPU Platform", 43.25),
        S("i7-11800H", "Cpu", "Clock", "CPU Core #1", 4207.6),
        S("i7-11800H", "Cpu", "Clock", "CPU Core #5", 1302.4),
        S("Total Memory", "Memory", "Load", "Memory", 17.0),
        S("Total Memory", "Memory", "Data", "Memory Used", 10.85),
        S("Virtual Memory", "Memory", "Load", "Memory", 99.0),  # 必须被忽略
        S("RTX 3060", "GpuNvidia", "Load", "GPU Core", 23.0),
        S("RTX 3060", "GpuNvidia", "Temperature", "GPU Core", 51.0),
        S("RTX 3060", "GpuNvidia", "Load", "GPU Memory", 5.16),
        S("RTX 3060", "GpuNvidia", "SmallData", "GPU Memory Used", 317.0),
        S("RTX 3060", "GpuNvidia", "SmallData", "GPU Memory Total", 6144.0),
        S("UHD Graphics", "GpuIntel", "Load", "D3D 3D", 1.9),
        S("UHD Graphics", "GpuIntel", "SmallData", "D3D Shared Memory Used", 624.55),
        S("UHD Graphics", "GpuIntel", "SmallData", "D3D Shared Memory Total", 48890.79),
        S("PM991a 512GB", "Storage", "Temperature", "Composite Temperature", 42.0),
        S("PM991a 512GB", "Storage", "Load", "Used Space", 38.6),
        S("ZHITAI 2TB", "Storage", "Temperature", "Composite Temperature", 40.0),
        S("ZHITAI 2TB", "Storage", "Data", "Total Space", 2048.4),  # 无 Used Space → psutil 兜底
        S("Dell 09FDV3", "Motherboard", "Fan", "Fan #1", 2400.0),
        S("RTX 3060", "GpuNvidia", "Fan", "GPU Core Fan", 1800.0),
        S("Dell 09FDV3", "Motherboard", "Fan", "CPU Fan", 3000.0),
    ]


def fake_meta():
    h = lambda t, n, o: {"type": t, "name": n, "order": o, "parent": None}  # noqa: E731
    return {"elevated": True, "hwIndex": [
        h("Motherboard", "Dell 09FDV3", 0), h("Cpu", "i7-11800H", 1),
        h("Memory", "Virtual Memory", 2), h("Memory", "Total Memory", 3),
        h("GpuNvidia", "RTX 3060", 4), h("GpuIntel", "UHD Graphics", 5),
        h("Storage", "PM991a 512GB", 6), h("Storage", "ZHITAI 2TB", 7),
    ]}


def section_a():
    print("[A] mock 帧映射断言")
    mr = mh.map_frame({"sensors": fake_sensors()}, fake_meta())
    near(mr.fixed["cpu.load"][0], 42.0, .01, "cpu.load")
    near(mr.fixed["cpu.temp"][0], 69.0, .01, "cpu.temp")
    near(mr.fixed["cpu.power"][0], 13.58, .01, "cpu.power 取 Package 非 Platform")
    near(mr.fixed["cpu.freq"][0], 4.2076, .001, "cpu.freq=逐核最大 MHz→GHz")
    near(mr.fixed["mem.load"][0], 17.0, .01, "mem.load 用 Total Memory 非 Virtual")
    near(mr.fixed["mem.used"][0], 10.85, .01, "mem.used GB")
    ok(len(mr.gpus_d) == 1 and mr.gpus_d[0]["name"] == "RTX 3060", "独显全量枚举单卡")
    near(mr.gpus_d[0]["load"], 23.0, .01, "独显负载")
    near(mr.gpus_d[0]["temp"], 51.0, .01, "独显温度")
    near(mr.gpus_d[0]["vram_used"], 317 / 1024, .001, "独显显存 MB→GB")
    near(mr.gpus_d[0]["vram_load"], 5.16, .01, "独显显存使用率")
    ok(mr.hw_names.get("cpu") == "i7-11800H", "CPU 硬件名采集")
    ok(mr.hw_names.get("gpu_i") == "UHD Graphics", "核显硬件名采集")
    near(mr.fixed["gpu_i.load"][0], 1.9, .01, "核显 D3D 3D")
    near(mr.fixed["gpu_i.shared"][0], 624.55 / 1024, .001, "共享内存 MB→GB")
    near(mr.shared_total, 48890.79 / 1024, .001, "共享内存总量 MB→GB（V16.2 比例配色）")
    near(mr.gpus_d[0]["vram_total"], 6144 / 1024, .001, "独显显存总量 MB→GB")
    ok(len(mr.disks) == 2 and mr.disks[0]["name"] == "PM991a 512GB", "多硬盘按枚举顺序")
    near(mr.disks[0]["usage_lhm"], 38.6, .01, "盘1 容量使用率(SMART)")
    ok(mr.disks[1]["usage_lhm"] is None and mr.disks[1]["total_gb"] == 2048.4,
       "盘2 无 Used Space，留容量给 psutil 兜底")
    slots = {f["slot"] for f in mr.fans}
    ok(slots == {"cpu", "gpu_d", "mobo"}, f"风扇槽位归类 {slots}")
    order = [(f["slot"], f["value"]) for f in mr.fans]
    ok(("mobo", 2400.0) in order and ("cpu", 3000.0) in order,
       "Fan #1→主板 / CPU Fan→cpu")

    # 独显显存只有绝对值时，使用率由 Used/Total 兜底计算
    s2 = [x for x in fake_sensors() if not (x["hwType"] == "GpuNvidia"
                                            and x["sensor"] == "Load" and x["name"] == "GPU Memory")]
    mr2 = mh.map_frame({"sensors": s2}, fake_meta())
    near(mr2.gpus_d[0]["vram_load"], 317 / 6144 * 100, .01, "显存使用率计算兜底")

    # 多独显（SLI/工作站）：全量枚举两卡，按 order 排序逐卡采集（防 last-wins 串值）
    meta_multi = fake_meta()
    meta_multi["hwIndex"].append({"type": "GpuNvidia", "name": "RTX 3060 #2",
                                  "order": 8, "parent": None})
    s_multi = fake_sensors() + [
        {"hw": "RTX 3060 #2", "hwType": "GpuNvidia", "parent": None,
         "sensor": "Load", "name": "GPU Core", "value": 88.0},
        {"hw": "RTX 3060 #2", "hwType": "GpuNvidia", "parent": None,
         "sensor": "Temperature", "name": "GPU Core", "value": 77.0},
    ]
    mr_multi = mh.map_frame({"sensors": s_multi}, meta_multi)
    ok(len(mr_multi.gpus_d) == 2, "多独显全量枚举两卡")
    near(mr_multi.gpus_d[0]["load"], 23.0, .01, "卡0（order 最小）负载不串值")
    near(mr_multi.gpus_d[0]["temp"], 51.0, .01, "卡0 温度不串值")
    near(mr_multi.gpus_d[1]["load"], 88.0, .01, "卡1 负载独立采集")
    near(mr_multi.gpus_d[1]["temp"], 77.0, .01, "卡1 温度独立采集")
    ok(mr_multi.gpus_d[1]["name"] == "RTX 3060 #2", "卡1 硬件名")

    # 核显误判回归：CPU 名含 "intel"（11th Gen Intel Core）且 order 小于真核显，
    # 不得抢占目标核显导致 gpu_i.* 整卡被跳过（真实机型 i7-11800H + UHD 实证）。
    meta_igpu = {"hwIndex": [
        {"type": "Cpu", "name": "11th Gen Intel Core i7-11800H", "order": 0, "parent": None},
        {"type": "GpuNvidia", "name": "NVIDIA GeForce RTX 3060 Laptop GPU", "order": 1, "parent": None},
        {"type": "GpuIntel", "name": "Intel(R) UHD Graphics", "order": 2, "parent": None},
    ]}
    s_igpu = [
        {"hw": "11th Gen Intel Core i7-11800H", "hwType": "Cpu", "parent": None,
         "sensor": "Load", "name": "CPU Total", "value": 8.0},
        {"hw": "NVIDIA GeForce RTX 3060 Laptop GPU", "hwType": "GpuNvidia", "parent": None,
         "sensor": "Load", "name": "GPU Core", "value": 31.0},
        {"hw": "Intel(R) UHD Graphics", "hwType": "GpuIntel", "parent": None,
         "sensor": "Load", "name": "D3D 3D", "value": 1.9},
        {"hw": "Intel(R) UHD Graphics", "hwType": "GpuIntel", "parent": None,
         "sensor": "SmallData", "name": "D3D Shared Memory Used", "value": 550.0},
    ]
    mr_igpu = mh.map_frame({"sensors": s_igpu}, meta_igpu)
    near(mr_igpu.fixed["gpu_i.load"][0], 1.9, .01, "CPU 含 intel 不抢占核显（负载）")
    near(mr_igpu.fixed["gpu_i.shared"][0], 550 / 1024, .001, "CPU 含 intel 不抢占核显（共享内存）")
    near(mr_igpu.gpus_d[0]["load"], 31.0, .01, "独显不受核显修复影响")

    # 非管理员空帧：无 CPU 温度族、无存储、无任何风扇行；功耗全 0
    sig = mh.map_absent_signals([
        s for s in fake_sensors()
        if s["sensor"] != "Fan"
        and s["hwType"] != "Storage"
        and not (s["hwType"] == "Cpu" and s["sensor"] in ("Temperature", "Clock"))
        and not (s["hwType"] == "Cpu" and s["sensor"] == "Power")
    ] + [dict(s, value=0.0) for s in fake_sensors()
         if s["hwType"] == "Cpu" and s["sensor"] == "Power"])
    ok(sig.get("cpu.temp") == mh.STATUS_NEED_ADMIN, "CPU 温度整族缺失→need_admin")
    ok(sig.get("__storage__") == mh.STATUS_NEED_ADMIN, "存储整类缺失→need_admin")
    ok(sig.get("__fan__") == mh.STATUS_UNSUPPORTED, "风扇缺失→unsupported")

    # CPU 功耗恒 0（非管理员特征）不得当成有效读数
    s3 = [dict(x) for x in fake_sensors()]
    for x in s3:
        if x["sensor"] == "Power" and "Package" in x["name"]:
            x["value"] = 0.0
    mr3 = mh.map_frame({"sensors": s3}, fake_meta())
    ok("cpu.power" not in mr3.fixed, "CPU 功耗恒 0 视为无读数")

    # 动态键定义还原
    ok(mh._def("disk.1.temp")[1] == "硬盘温度", "disk.1 标签（V16.2 无盘号后缀）")
    ok(mh._def("disk.0.usage")[1] == "硬盘占用空间", "disk.0.usage 标签（防误标温度）")
    ok(mh._def("disk.1.usage")[1] == "硬盘占用空间", "disk.1.usage 标签（V16.2 无盘号后缀）")
    ok(mh._def("fan.gpu_d.0")[1] == "GPU风扇转速", "风扇标签（V16.2 无独显括号）")

    # 格式化
    m_ok = mh.Metric("cpu.temp", "x", "cpu", 63.2, "℃", mh.STATUS_OK)
    m_na = mh.Metric("cpu.temp", "x", "cpu", None, "℃", mh.STATUS_NEED_ADMIN)
    ok(mh.MetricsHub.fmt(m_ok) == "63.2 ℃", "fmt 数值")
    ok(mh.MetricsHub.fmt(m_na) == "需管理员", "fmt need_admin")
    ok(mh.MetricsHub.uptime_fmt(3725) == "1:02:05", "uptime H:MM:SS")
    ok(mh.MetricsHub.uptime_fmt(90061) == "25:01:01", "uptime 跨天小时进位")
    # fmt_value：纯数值（不带单位），监视窗 B 列用
    ok(mh.MetricsHub.fmt_value(m_ok) == "63.2", "fmt_value 纯数值")
    ok(mh.MetricsHub.fmt_value(m_na) == "--", "fmt_value 无读数 --")
    m_fan = mh.Metric("fan.cpu.0", "x", "fan", 2400.0, "RPM", mh.STATUS_OK)
    ok(mh.MetricsHub.fmt_value(m_fan) == "2400", "fmt_value 风扇整数")
    ok(mh.MetricsHub.fmt_value(None) == "--", "fmt_value None → --")

    # 断流口径：侧车 stale 时旧帧里的硬盘温度/使用率一律标 stale（与 GPU 同口径）
    hub_m = mh.MetricsHub(sidecar_path=__file__)   # 假路径：installed 但永不出帧
    hub_m.runner._latest = {"ts": 0, "seq": 1, "sensors": fake_sensors()}
    hub_m.runner._meta = fake_meta()
    hub_m.runner._last_frame_time = time.time() - 100.0   # 远超 stale_sec → stale
    snap_m = hub_m.snapshot()
    ok(snap_m["disk.0.temp"].status == mh.STATUS_STALE, "断流时硬盘温度标 stale")
    ok(snap_m["disk.0.usage"].status == mh.STATUS_STALE, "断流时硬盘使用率标 stale")
    ok(snap_m["gpu_d.0.temp"].status == mh.STATUS_STALE, "断流时 GPU 温度标 stale（口径一致）")
    # 风扇同口径：侧车断流时旧帧风扇值也标 stale（旧实现恒 ok，与 GPU/硬盘不一致）
    ok(snap_m["fan.cpu.0"].status == mh.STATUS_STALE, "断流时风扇标 stale（口径一致）")

    # 启动宽限期：尚未出帧但进程活着 ≠ 断流（首帧前枚举可能超过 stale_sec，
    # 旧口径会把正常启动误判成断流，重启预算耗尽后首帧永远等不到）
    class _FakeProc:
        def __init__(self, rc): self._rc = rc
        def poll(self): return self._rc
    r_grace = mh.SidecarRunner(exe_path=__file__)
    r_grace._proc = _FakeProc(None)          # 进程存活、未出帧
    ok(r_grace.is_stale() is False, "未出帧且进程存活 → 启动宽限期不算 stale")
    r_grace._proc = _FakeProc(1)             # 进程已退出、未出帧
    ok(r_grace.is_stale() is True, "未出帧且进程已退出 → stale（触发重启）")

    # 幂等守卫（V16.2 修复）：进程活着时重复 start 绝不重新 spawn——
    # GUI 预览采集 + 建窗同步连续 start，重复 spawn 会孤立读取线程、
    # 覆盖进程句柄致数据帧丢失（meta 到 frame 不到）并泄漏孤儿进程。
    r_idem = mh.SidecarRunner(exe_path=__file__)
    _spawn_calls = []
    r_idem._spawn = lambda: _spawn_calls.append(1)   # 桩：记录 spawn 次数
    r_idem._proc = _FakeProc(None)                   # 进程存活
    ok(r_idem.start() is True, "进程活着 start 返回 True")
    ok(_spawn_calls == [], "进程活着重复 start 不重新 spawn（幂等守卫）")
    ok(r_idem.alive is True, "幂等 start 维持 alive")
    r_idem._proc = _FakeProc(1)                      # 进程已退出
    r_idem.start()
    ok(len(_spawn_calls) == 1, "进程退出后 start 重新 spawn 一次")


# ---------------------------------------------------------------------------
# B. 真实端到端（本机制品）
# ---------------------------------------------------------------------------
def section_b():
    print("[B] 真实侧车 + psutil 端到端")
    exe = mh.find_sidecar()
    if not exe:
        print("  SKIP: 未找到 hwcollector.exe（先 dotnet publish）")
        globals()["SKIP"] += 1
        return
    hub = mh.MetricsHub(poll_ms=500)
    hub.start()
    got = hub.runner.wait_first_frame(20)
    ok(got, "侧车首帧到达")
    if got:
        time.sleep(2.2)  # 至少 2 帧 + psutil 差分窗口
        snap = hub.snapshot()
        keys = set(snap)
        for k in ("cpu.load", "cpu.freq", "cpu.temp", "cpu.power",
                  "mem.load", "mem.used", "gpu_i.load", "gpu_i.shared",
                  "gpu_d.0.load", "gpu_d.0.temp", "gpu_d.0.vram_load", "gpu_d.0.vram_used",
                  "net.tx", "net.rx"):
            ok(k in keys, f"快照含 {k}")
        ok("sys.uptime" not in keys, "运行时间已废弃：快照不再产出该键")
        ok("gpu_d.load" not in keys, "旧独显固定键已废弃（改动态枚举）")
        # 独显全量枚举接口 + 硬件名注入
        gpus_d = hub.list_gpus_d(snapshot=snap)
        ok(isinstance(gpus_d, list), "list_gpus_d 返回列表")
        ok(snap["cpu.load"].hardware != "", "CPU 指标注入硬件名")
        if gpus_d:
            ok(snap["gpu_d.0.load"].hardware == gpus_d[0]["name"],
               "独显指标注入硬件名")
        ok(snap["cpu.load"].status == mh.STATUS_OK and 0 <= snap["cpu.load"].value <= 100,
           "cpu.load 合理")
        near_v = snap["mem.used"].value
        ok(near_v is not None and 0 < near_v < 1024, "内存用量 GB 量级")
        # 运行时间采集能力保留（uptime() 仍可用，只是不进监视白名单）
        up_secs, up_str = hub.uptime()
        ok(up_secs is not None and up_secs > 0, "uptime() 采集能力保留")
        # 权限决定温度可见性，两种路径都必须有明确状态
        t = snap["cpu.temp"]
        if hub.runner.elevated:
            ok(t.status == mh.STATUS_OK and t.value and 20 < t.value < 110,
               f"管理员下 CPU 温度合理 ({t.value})")
            ok(snap["cpu.power"].status == mh.STATUS_OK, "管理员下 CPU 功耗可用")
            ok(hub.list_disks(), "管理员下枚举到硬盘")
        else:
            ok(t.status == mh.STATUS_NEED_ADMIN, "非管理员下温度→need_admin")
            ok(snap["cpu.power"].status == mh.STATUS_NEED_ADMIN,
               "非管理员下功耗恒0→need_admin")
            d0 = snap.get("disk.0.temp")
            ok(d0 and d0.status == mh.STATUS_NEED_ADMIN, "非管理员下硬盘→need_admin")
        f = snap.get("fan.mobo.0")
        if hub.list_fans():
            ok(all(x.value and x.value > 0 for x in hub.list_fans()), "风扇读数>0")
        else:
            ok(f and f.status in (mh.STATUS_UNSUPPORTED, mh.STATUS_NEED_ADMIN),
               "无风扇时状态明确(unsupported/need_admin)")
        nv = snap["net.rx"]
        ok(nv.status == mh.STATUS_OK and nv.value is not None and nv.value >= 0,
           "网速非负")
        # 断连自动重启回归：杀死侧车进程后，看守线程应在限定时间内拉起新进程并恢复出帧。
        # 旧缺陷：读取线程 EOF 置位共享 _stop，看守线程连带退出，重启永远走不到。
        old_proc = hub.runner._proc
        if old_proc is not None:
            old_proc.terminate()
            frame_before = hub.runner.latest_frame()
            recovered, new_proc = False, None
            t_end = time.time() + 15.0
            while time.time() < t_end:
                time.sleep(0.3)
                new_proc = hub.runner._proc
                frame = hub.runner.latest_frame()
                # 重启后新进程 seq 从 1 重新计数，不能拿 seq 与旧进程比大小；
                # 判据 = 新进程对象存活 + 帧对象已换新（新进程出了帧）。
                if (new_proc is not None and new_proc is not old_proc
                        and new_proc.poll() is None
                        and frame is not None and frame is not frame_before):
                    recovered = True
                    break
            ok(recovered, "杀死侧车后看守线程自动重启并恢复出帧")
    # 停止后进程必须回收
    hub.stop()
    time.sleep(0.3)
    ok(hub.runner._proc is None or hub.runner._proc.poll() is not None,
       "stop() 后侧车进程已退出")


# ---------------------------------------------------------------------------
# C. 历史环形缓冲（阶段 5 增量：关窗不停采，重开秒回曲线）
# ---------------------------------------------------------------------------
def section_c():
    print("[C] 历史环形缓冲")
    ok(mh.HISTORY_POINTS == 60, "历史点数 60")
    # 假侧车路径：installed 但永不出帧 → 侧车系 stale，psutil 系需 start 才有值。
    # 直接驱动 _record_history（不启真实进程），用 mock 帧注入 psutil 缓存。
    hub = mh.MetricsHub(sidecar_path=__file__)
    now = time.time()
    with hub._lock:
        hub._ps_cache = {
            "cpu.load": (now, 42.0),
            "sys.uptime": (now, 3725.0),
            "mem.load": (now, 17.0),
            "mem.used": (now, 10.85),
            "net.tx": (now, 1.5),
            "net.rx": (now, 2.5),
            "cpu.freq_ps": (now, 4.2),
            "disk_busy": (now, 0.0),
        }
    # 积累 75 拍 → 环形淘汰最旧，容量恒 60
    for i in range(75):
        with hub._lock:
            hub._ps_cache["cpu.load"] = (now, float(i))
        hub._record_history()
    h = hub.history("cpu.load")
    ok(len(h) == 60, f"历史容量 60（{len(h)}）")
    ok(h[0] == 15.0 and h[-1] == 74.0, "环形淘汰最旧（首 15 末 74）")
    ok(h == sorted(h), "历史最旧→最新有序")
    # 读取返回副本：外部修改不污染内部缓冲
    h.append(999.0)
    ok(hub.history("cpu.load")[-1] == 74.0, "history 返回副本（外部改不污染）")
    # 无记录键返回空列表
    ok(hub.history("nonexistent.key") == [], "无记录键返回空列表")
    # None 断线：侧车系指标（无帧）available=False → 记 None
    hub._record_history()
    ht = hub.history("cpu.temp")
    ok(ht and ht[-1] is None, "无读数键历史记 None（断线）")
    # 多键各自独立缓冲
    ok(len(hub.history("mem.load")) == 61 or len(hub.history("mem.load")) == 60,
       "多键各自独立缓冲")
    ok(hub.history("mem.load")[-1] == 17.0, "mem.load 历史值正确")
    # start/stop 生命周期：stop 后缓冲仍在（关窗不停采语义——缓冲随 hub 存活）
    hub.stop()
    ok(len(hub.history("cpu.load")) == 60, "stop 后历史缓冲仍保留")


# ---------------------------------------------------------------------------
# D. start() 幂等（poller 不重复累积；侧车缺失环境零进程依赖）
# ---------------------------------------------------------------------------
def section_d():
    print("[D] start() 幂等（poller 线程不重复累积）")
    # 假侧车路径：installed 但永不出帧 → runner.start() 直接返回 False，
    # 不拉真实进程；poller 线程仍会创建，可安全计数。
    hub = mh.MetricsHub(sidecar_path=__file__)
    try:
        hub.start()
        first = hub._poller
        ok(first is not None and first.is_alive(), "首次 start 建立 poller")
        # 回归判据：预览采集 + 建窗同步会连续调 start（坑 86 同款链路），
        # 重复 start 绝不新建 poller——否则网速差分窗口被缩短、history 倍速积累。
        hub.start()
        hub.start()
        alive = [t for t in __import__("threading").enumerate()
                 if t.name == "metrics-hub-poller" and t.is_alive()]
        ok(len(alive) == 1, f"重复 start 不累积 poller（存活 {len(alive)} 个，应为 1）")
        ok(hub._poller is first, "重复 start 复用同一 poller 线程")
        # stop 后再 start 须能重建（is_alive 守卫不误伤正常重启）
        hub.stop()
        ok(not first.is_alive(), "stop 后 poller 已退出")
        hub.start()
        ok(hub._poller is not None and hub._poller.is_alive()
           and hub._poller is not first, "stop 后 start 重建新 poller")
    finally:
        hub.stop()


# ---------------------------------------------------------------------------
# E. v2.03 配置拆分：collect 调参写入口 apply_tuning（钳制 + HISTORY_POINTS 单一真源）
# ---------------------------------------------------------------------------
def section_e():
    print("[E] collect 调参写入口 apply_tuning")
    ok(hasattr(mh, "COLLECT_DEFAULTS"), "COLLECT_DEFAULTS 冻结默认快照存在")
    ok(hasattr(mh, "apply_tuning"), "apply_tuning 唯一写入口存在")
    ok(hasattr(mh, "COLLECT_LIVE"), "COLLECT_LIVE 生效值表存在")
    if not (hasattr(mh, "apply_tuning") and hasattr(mh, "COLLECT_DEFAULTS")
            and hasattr(mh, "COLLECT_LIVE")):
        print("  [E] collect 调参写入口未实现，跳过行为断言")
        return
    d = mh.COLLECT_DEFAULTS
    ok(set(d) >= {"history_points", "sidecar_interval_ms", "stale_sec",
                  "max_restarts", "poll_ms", "first_frame_timeout_sec"},
       f"COLLECT_DEFAULTS 六键齐全（实际 {sorted(d)}）")
    ok(d["history_points"] == 60,
       f"COLLECT_DEFAULTS.history_points=60（实际 {d['history_points']}）")
    try:
        # apply_tuning({}) 幂等回内置默认
        mh.apply_tuning({})
        ok(mh.HISTORY_POINTS == 60 and mh.COLLECT_LIVE["poll_ms"] == 1000,
           f"apply_tuning({{}}) 幂等回默认（HISTORY_POINTS={mh.HISTORY_POINTS} poll_ms={mh.COLLECT_LIVE['poll_ms']}）")
        # history_points → HISTORY_POINTS（决策12 单一真源，被 _record_history 消费）
        mh.apply_tuning({"history_points": 90})
        ok(mh.HISTORY_POINTS == 90, f"history_points→HISTORY_POINTS 生效（实际 {mh.HISTORY_POINTS}）")
        mh.apply_tuning({"history_points": 9999})
        ok(mh.HISTORY_POINTS == 300, f"history_points 超上界钳到 300（实际 {mh.HISTORY_POINTS}）")
        mh.apply_tuning({"history_points": 1})
        ok(mh.HISTORY_POINTS == 10, f"history_points 超下界钳到 10（实际 {mh.HISTORY_POINTS}）")
        # COLLECT_LIVE 各键生效（main 侧构造 hub/runner 时读此表）
        mh.apply_tuning({"sidecar_interval_ms": 1500, "stale_sec": 8.0,
                         "max_restarts": 5, "poll_ms": 2000,
                         "first_frame_timeout_sec": 30})
        L = mh.COLLECT_LIVE
        ok(L["sidecar_interval_ms"] == 1500 and L["poll_ms"] == 2000,
           f"sidecar_interval_ms/poll_ms 生效（{L['sidecar_interval_ms']}/{L['poll_ms']}）")
        ok(L["stale_sec"] == 8.0 and L["max_restarts"] == 5
           and L["first_frame_timeout_sec"] == 30,
           f"stale_sec/max_restarts/first_frame_timeout_sec 生效"
           f"（{L['stale_sec']}/{L['max_restarts']}/{L['first_frame_timeout_sec']}）")
        # 超界钳制（范围对照计划 §五 collect 组）
        mh.apply_tuning({"sidecar_interval_ms": 99999, "stale_sec": 999,
                         "max_restarts": 999, "poll_ms": 1,
                         "first_frame_timeout_sec": 999})
        L = mh.COLLECT_LIVE
        ok(L["sidecar_interval_ms"] == 5000, f"sidecar_interval_ms 钳到上界 5000（实际 {L['sidecar_interval_ms']}）")
        ok(L["stale_sec"] == 30, f"stale_sec 钳到上界 30（实际 {L['stale_sec']}）")
        ok(L["max_restarts"] == 10, f"max_restarts 钳到上界 10（实际 {L['max_restarts']}）")
        ok(L["poll_ms"] == 200, f"poll_ms 钳到下界 200（实际 {L['poll_ms']}）")
        ok(L["first_frame_timeout_sec"] == 60, f"first_frame_timeout_sec 钳到上界 60（实际 {L['first_frame_timeout_sec']}）")
        # max_restarts 下界 0 合法（禁用重启），不得抬成默认
        mh.apply_tuning({"max_restarts": 0})
        ok(mh.COLLECT_LIVE["max_restarts"] == 0,
           f"max_restarts=0 合法保留（实际 {mh.COLLECT_LIVE['max_restarts']}）")
        # 非法值回退默认（不致命）
        mh.apply_tuning({"history_points": "abc", "poll_ms": None, "stale_sec": "x"})
        ok(mh.HISTORY_POINTS == 60 and mh.COLLECT_LIVE["poll_ms"] == 1000
           and mh.COLLECT_LIVE["stale_sec"] == 5.0,
           f"非法值回退默认（HP={mh.HISTORY_POINTS} poll={mh.COLLECT_LIVE['poll_ms']} stale={mh.COLLECT_LIVE['stale_sec']}）")
    finally:
        # 幂等回默认，防污染（section_c 依赖 HISTORY_POINTS==60，但其先于本节执行）
        mh.apply_tuning({})


# ---------------------------------------------------------------------------
# F. v2.03：collect 生效接线——MetricsHub 构造读 COLLECT_LIVE + retune_collect 热重载
#    main 侧用无参 MetricsHub()，故构造默认须读 COLLECT_LIVE 才能让调参生效。
# ---------------------------------------------------------------------------
def section_f():
    print("[F] collect 生效接线（构造读 COLLECT_LIVE + retune_collect 热重载）")
    if not hasattr(mh, "apply_tuning"):
        print("  [F] apply_tuning 未实现，跳过")
        return
    ok(hasattr(mh.MetricsHub, "retune_collect"),
       "MetricsHub.retune_collect 热重载入口存在")
    if not hasattr(mh.MetricsHub, "retune_collect"):
        mh.apply_tuning({})
        return
    try:
        # 构造读 COLLECT_LIVE：设 sidecar_interval/poll/stale/max_restarts → 无参构造 hub
        mh.apply_tuning({"sidecar_interval_ms": 2000, "poll_ms": 2500,
                         "stale_sec": 9.0, "max_restarts": 7})
        hub = mh.MetricsHub(sidecar_path=__file__)   # 假路径：installed 但永不出帧
        ok(hub.poll_ms == 2500, f"无参构造 poll_ms 读 COLLECT_LIVE（实际 {hub.poll_ms}）")
        ok(hub.runner.interval_ms == 2000,
           f"无参构造 runner.interval_ms 读 COLLECT_LIVE（实际 {hub.runner.interval_ms}）")
        ok(hub.runner.stale_sec == 9.0,
           f"无参构造 runner.stale_sec 读 COLLECT_LIVE（实际 {hub.runner.stale_sec}）")
        ok(hub.runner.max_restarts == 7,
           f"无参构造 runner.max_restarts 读 COLLECT_LIVE（实际 {hub.runner.max_restarts}）")
        # 显式传参优先于 COLLECT_LIVE（向后兼容旧调用）
        hub2 = mh.MetricsHub(sidecar_path=__file__, poll_ms=500)
        ok(hub2.poll_ms == 500, f"显式 poll_ms=500 覆盖 COLLECT_LIVE（实际 {hub2.poll_ms}）")
        hub2.stop()
        # retune_collect 热重载：poll_ms 现读即生效（_poll_loop 每拍重读 self.poll_ms）
        mh.apply_tuning({"poll_ms": 3000})
        hub.retune_collect()
        ok(hub.poll_ms == 3000, f"retune_collect 后 poll_ms 热更新（实际 {hub.poll_ms}）")
        # history_points 变 → 重建历史（清旧 deque，下拍按新 maxlen 积累）
        now = time.time()
        with hub._lock:
            hub._ps_cache = {"cpu.load": (now, 42.0), "sys.uptime": (now, 1.0),
                             "mem.load": (now, 1.0), "mem.used": (now, 1.0),
                             "net.tx": (now, 0.0), "net.rx": (now, 0.0),
                             "cpu.freq_ps": (now, 1.0), "disk_busy": (now, 0.0)}
        for _ in range(5):
            hub._record_history()
        ok(len(hub.history("cpu.load")) == 5, "retune 前历史已积累 5 点")
        mh.apply_tuning({"history_points": 90})
        hub.retune_collect()
        ok(hub.history("cpu.load") == [],
           f"history_points 变 → retune_collect 清空历史待重建（实际 {len(hub.history('cpu.load'))} 点）")
        ok(mh.HISTORY_POINTS == 90, f"retune_collect 同步 HISTORY_POINTS=90（实际 {mh.HISTORY_POINTS}）")
        # sidecar_interval 变 → runner.interval_ms 更新（真实重启侧车需活进程，此处验属性）
        mh.apply_tuning({"sidecar_interval_ms": 1234})
        hub.retune_collect()
        ok(hub.runner.interval_ms == 1234,
           f"sidecar_interval 变 → runner.interval_ms 更新（实际 {hub.runner.interval_ms}）")
        hub.stop()
        # first_frame_timeout_sec 接线：wait_first_frame 默认 timeout 读 COLLECT_LIVE
        mh.apply_tuning({"first_frame_timeout_sec": 6})
        hub3 = mh.MetricsHub(sidecar_path=__file__)
        t0 = time.time()
        hub3.runner.wait_first_frame()   # 假路径永不出帧、alive=False → 立即 False
        ok(hub3.runner.wait_first_frame() is False,
           "wait_first_frame 默认读 COLLECT_LIVE 且不阻塞（假路径立即 False）")
        ok(time.time() - t0 < 3, "wait_first_frame 未卡满（alive=False 即时返回）")
        hub3.stop()
    finally:
        mh.apply_tuning({})


if __name__ == "__main__":
    section_a()
    section_b()
    section_c()
    section_d()
    section_e()
    section_f()
    print(f"\n== PASS {PASS} / FAIL {FAIL} / SKIP {SKIP} ==")
    sys.exit(1 if FAIL else 0)
