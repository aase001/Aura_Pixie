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
    near(mr.fixed["gpu_d.load"][0], 23.0, .01, "独显负载")
    near(mr.fixed["gpu_d.temp"][0], 51.0, .01, "独显温度")
    near(mr.fixed["gpu_d.vram_used"][0], 317 / 1024, .001, "独显显存 MB→GB")
    near(mr.fixed["gpu_d.vram_load"][0], 5.16, .01, "独显显存使用率")
    near(mr.fixed["gpu_i.load"][0], 1.9, .01, "核显 D3D 3D")
    near(mr.fixed["gpu_i.shared"][0], 624.55 / 1024, .001, "共享内存 MB→GB")
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
    near(mr2.fixed["gpu_d.vram_load"][0], 317 / 6144 * 100, .01, "显存使用率计算兜底")

    # 多独显（SLI/工作站）：取 hwOrder 最小的第一块，第二块不得覆盖（防 last-wins）
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
    near(mr_multi.fixed["gpu_d.load"][0], 23.0, .01, "多独显取 order 最小首块（负载）")
    near(mr_multi.fixed["gpu_d.temp"][0], 51.0, .01, "多独显取 order 最小首块（温度）")

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
    ok(mh._def("disk.1.temp")[1] == "硬盘温度·盘2", "disk.1 标签")
    ok(mh._def("disk.0.usage")[1] == "硬盘使用率·盘1", "disk.0.usage 标签（防误标温度）")
    ok(mh._def("disk.1.usage")[1] == "硬盘使用率·盘2", "disk.1.usage 标签")
    ok(mh._def("fan.gpu_d.0")[1] == "GPU(独显)风扇转速", "风扇标签")

    # 格式化
    m_ok = mh.Metric("cpu.temp", "x", "cpu", 63.2, "℃", mh.STATUS_OK)
    m_na = mh.Metric("cpu.temp", "x", "cpu", None, "℃", mh.STATUS_NEED_ADMIN)
    ok(mh.MetricsHub.fmt(m_ok) == "63.2 ℃", "fmt 数值")
    ok(mh.MetricsHub.fmt(m_na) == "需管理员", "fmt need_admin")
    ok(mh.MetricsHub.uptime_fmt(3725) == "1:02:05", "uptime H:MM:SS")
    ok(mh.MetricsHub.uptime_fmt(90061) == "25:01:01", "uptime 跨天小时进位")

    # 断流口径：侧车 stale 时旧帧里的硬盘温度/使用率一律标 stale（与 GPU 同口径）
    hub_m = mh.MetricsHub(sidecar_path=__file__)   # 假路径：installed 但永不出帧
    hub_m.runner._latest = {"ts": 0, "seq": 1, "sensors": fake_sensors()}
    hub_m.runner._meta = fake_meta()
    hub_m.runner._last_frame_time = time.time() - 100.0   # 远超 stale_sec → stale
    snap_m = hub_m.snapshot()
    ok(snap_m["disk.0.temp"].status == mh.STATUS_STALE, "断流时硬盘温度标 stale")
    ok(snap_m["disk.0.usage"].status == mh.STATUS_STALE, "断流时硬盘使用率标 stale")
    ok(snap_m["gpu_d.temp"].status == mh.STATUS_STALE, "断流时 GPU 温度标 stale（口径一致）")

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
        for k in ("sys.uptime", "cpu.load", "cpu.freq", "cpu.temp", "cpu.power",
                  "mem.load", "mem.used", "gpu_i.load", "gpu_i.shared",
                  "gpu_d.load", "gpu_d.temp", "gpu_d.vram_load", "gpu_d.vram_used",
                  "net.tx", "net.rx"):
            ok(k in keys, f"快照含 {k}")
        ok(snap["cpu.load"].status == mh.STATUS_OK and 0 <= snap["cpu.load"].value <= 100,
           "cpu.load 合理")
        near_v = snap["mem.used"].value
        ok(near_v is not None and 0 < near_v < 1024, "内存用量 GB 量级")
        up = snap["sys.uptime"].value
        ok(up is not None and up > 0, "运行时间>0")
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


if __name__ == "__main__":
    section_a()
    section_b()
    print(f"\n== PASS {PASS} / FAIL {FAIL} / SKIP {SKIP} ==")
    sys.exit(1 if FAIL else 0)
