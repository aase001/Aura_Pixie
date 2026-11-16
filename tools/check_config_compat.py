# -*- coding: utf-8 -*-
"""配置兼容回归自检（V16.4）：setting.yaml 分区 / 旧配置兼容 / 默认不覆盖已有值。

覆盖：
  · V16.2 旧配置（缺 monitor_opacity / monitor.topmost / gpu_d power）启动不报错，
    新键补默认、已有值（含 idle_sec=0 永不、缩放 150%、布局记忆）一律不被覆盖；
  · 更老配置：旧块名 aura 合并、裸 autostart bool、idle_min→idle_sec 换算不被默认盖；
  · 空 functions 不清已有功能；空/损坏配置回退全默认块；
  · 外观保存往返一致、不动其他块、topmost 不写进 appearance。
隔离：LOCALAPPDATA 指向临时目录，绝不触碰真实用户配置。
跑法：python tools/check_config_compat.py（退出码 0 全过）
"""
import os
import sys
import tempfile

_ISO = tempfile.mkdtemp(prefix="aura_cfg_audit_")
os.environ["LOCALAPPDATA"] = _ISO
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["AURA_AUTOSTART_DRYRUN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtGui import QFontDatabase  # noqa: E402

APP = QApplication.instance() or QApplication([])
QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")
import main as M  # noqa: E402
import monitor_ui as mu  # noqa: E402

fails = []


def ck(name, cond, extra=""):
    print(("[OK] " if cond else "[FAIL] ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        fails.append(name)


# ---- 场景1：V16.2 旧配置（无 monitor_opacity / monitor.topmost / gpu_d power）----
OLD = {
    "functions": [{"name": "旧功能", "code": "cmd: echo hi", "active": True}],
    "appearance": {"breath": False, "color_start": "#112233", "color_mid": "#445566",
                   "color_end": "#112233", "monitor_color": "#202020",
                   "dur_start_mid": 7, "dur_mid_end": 8, "dur_end_start": 9,
                   "aura_scale": 2.0, "idle_sec": 0, "opacity": 40,
                   "sleep_opacity": 5,
                   "display_scale": {"ball": {"enabled": True, "percent": 150}}},
    "autostart": {"enabled": True},
    "window": {"always_on_top": True, "pos_x": 100, "pos_y": 200},
    "monitor": {"enabled": True, "refresh_ms": 1500,
                "idle_stop_sec": 120,
                "units": {"cpu.load": {"enabled": True, "threshold": []},
                          "gpu_d.0.load": {"enabled": True, "threshold": []}},
                "layout": {"cpu.load": {"x": 10, "y": 20, "group_id": "g1"}}},
}
p = os.path.join(_ISO, "Aura_Pixie", "setting.yaml")
os.makedirs(os.path.dirname(p), exist_ok=True)
with open(p, "w", encoding="utf-8") as fh:
    yaml.safe_dump(OLD, fh, allow_unicode=True)

M.SETTINGS_PATH = p
M.CONFIG_PATH = p
data = M.ensure_settings(p)
ck("旧配置启动不报错且原样载入", bool(data))

ap = M.load_appearance_config()
ck("旧配置缺 monitor_opacity → 默认 100", ap["monitor_opacity"] == 100, str(ap["monitor_opacity"]))
ck("旧配置缺 monitor_fill → 默认 True（观感与旧版一致）", ap["monitor_fill"] is True)
ck("已有 breath=False 不被覆盖", ap["breath"] is False)
ck("已有三色不被覆盖", ap["color_start"] == "#112233" and ap["color_mid"] == "#445566")
ck("已有 monitor_color 不被覆盖", ap["monitor_color"] == "#202020")
ck("已有时长 7/8/9 不被覆盖",
   (ap["dur_start_mid"], ap["dur_mid_end"], ap["dur_end_start"]) == (7, 8, 9))
ck("已有倍率 2.0 不被覆盖", ap["aura_scale"] == 2.0)
ck("已有 idle_sec=0（永不）不被抬成 10", ap["idle_sec"] == 0)
ck("已有 opacity=40 / sleep=5 不被覆盖", ap["opacity"] == 40 and ap["sleep_opacity"] == 5)
ck("已有 ball 缩放 150% 不被覆盖",
   ap["display_scale"]["ball"] == {"enabled": True, "percent": 150})
ck("缺失单元补默认 100%", ap["display_scale"]["monitor"]["percent"] == 100)

mon = M.load_monitor_config()
ck("旧配置缺 monitor.topmost → 默认 False", mon.get("topmost") is False)
ck("已有 monitor.enabled=True 不被覆盖", mon["enabled"] is True)
ck("已有 refresh_ms=1500 不被覆盖", mon["refresh_ms"] == 1500)
ck("已有 idle_stop_sec=120 不被覆盖", mon["idle_stop_sec"] == 120)
ck("已有勾选 cpu.load 保留", mon["units"]["cpu.load"]["enabled"] is True)
ck("已有布局记忆保留", mon["layout"]["cpu.load"]["x"] == 10)
ck("新增 gpu_d.0.power 键自动补默认不勾",
   mon["units"].get("gpu_d.0.power", {}).get("enabled") is False)
ck("球体置顶已有 True 保留", M.topmost_enabled() is True)
ck("监视置顶默认 False 且与球体解耦", M._monitor_topmost_getter() is False)

# ---- 场景2：更老配置（裸 autostart bool + 旧块名 aura + idle_min）----
OLDER = {"aura": {"color_mid": "#ABCDEF", "idle_min": 2}, "autostart": True,
         "functions": [{"name": "x", "code": "", "active": True}]}
p2 = os.path.join(_ISO, "old2.yaml")
with open(p2, "w", encoding="utf-8") as fh:
    yaml.safe_dump(OLDER, fh, allow_unicode=True)
merged = M._merge_config(M.default_settings(), OLDER)
ck("旧块名 aura 合并进 appearance", merged["appearance"]["color_mid"] == "#ABCDEF")
ck("合并后不残留 aura 块", "aura" not in merged)
ck("裸 autostart bool → {enabled:True}", merged["autostart"] == {"enabled": True})
ck("旧 idle_min=2 → idle_sec=120（不被默认60覆盖）",
   merged["appearance"]["idle_sec"] == 120, str(merged["appearance"]["idle_sec"]))
ck("换算后不残留 idle_min", "idle_min" not in merged["appearance"])
ck("默认块含 monitor.topmost", merged["monitor"].get("topmost") is False)
ck("默认块 appearance 含 monitor_opacity",
   "monitor_opacity" in merged["appearance"], str(sorted(merged["appearance"])))
ck("默认块 appearance 含 monitor_fill=True（V16.6 底色填充开关）",
   merged["appearance"].get("monitor_fill") is True)

# ---- 场景3：空 functions 不清已有功能 ----
m3 = M._merge_config({"functions": [{"name": "keep"}]}, {"functions": []})
ck("空 functions 不清掉已有功能", m3["functions"] == [{"name": "keep"}])

# ---- 场景4：全空/损坏配置 ----
ck("空 dict 配置 → 默认全块", set(M._merge_config(M.default_settings(), {})) >=
   {"functions", "appearance", "autostart", "window", "monitor"})

# ---- 场景5：外观保存往返（不丢新键、不动其他块）----
M.save_appearance_config(ap)
back = M.load_appearance_config()
ck("外观保存往返一致", back == ap)
d5 = M.read_setting(p)
ck("外观保存不动 monitor/window/functions",
   d5["monitor"]["enabled"] is True and d5["window"]["always_on_top"] is True
   and d5["functions"][0]["name"] == "旧功能")
ck("topmost 不写进 appearance 块", "topmost" not in d5["appearance"])
# V16.6 底色填充开关：关闭态保存往返不丢、不重置
ap_off = dict(back)
ap_off["monitor_fill"] = False
M.save_appearance_config(ap_off)
ck("monitor_fill=False 保存往返一致",
   M.load_appearance_config()["monitor_fill"] is False)
M.save_appearance_config(back)   # 还原，避免影响后续用例

# ---- 场景6：v2.03 config.yaml 调参加载（load_interaction_config 门控+钳制+幂等）----
ck("load_interaction_config 函数存在", hasattr(M, "load_interaction_config"))
ck("BALL_DEFAULTS 冻结默认快照存在", hasattr(M, "BALL_DEFAULTS"))
ck("TUNING_PATH 指向 config.yaml",
   hasattr(M, "TUNING_PATH") and M.TUNING_PATH.endswith("config.yaml"))
if hasattr(M, "load_interaction_config"):
    cfg_path = os.path.join(_ISO, "Aura_Pixie", "config.yaml")
    # 文件不存在 → 全默认（enabled 视为 false）
    M.load_interaction_config(os.path.join(_ISO, "no_such_config.yaml"))
    ck("config.yaml 不存在 → 回内置默认（不致命）",
       M.CONFIG["hover_trigger_ms"] == 300.0 and M.CONFIG["confirm_extra_ms"] == 30,
       f"{M.CONFIG['hover_trigger_ms']}/{M.CONFIG['confirm_extra_ms']}")
    # enabled=false → 强制默认（即使文件写了别的值）
    with open(cfg_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump({"enabled": False, "ball": {"hover": {"trigger_ms": 800}}}, fh)
    M.load_interaction_config(cfg_path)
    ck("enabled=false → 忽略文件值、强制内置默认",
       M.CONFIG["hover_trigger_ms"] == 300.0, str(M.CONFIG["hover_trigger_ms"]))
    # enabled=true → ball 键钳制生效
    with open(cfg_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump({"enabled": True, "ball": {"hover": {"trigger_ms": 800}}}, fh)
    M.load_interaction_config(cfg_path)
    ck("enabled=true → ball.hover.trigger_ms 钳制生效=800",
       M.CONFIG["hover_trigger_ms"] == 800, str(M.CONFIG["hover_trigger_ms"]))
    # enabled=true 超界钳制
    with open(cfg_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump({"enabled": True, "ball": {"hover": {"trigger_ms": 99999}}}, fh)
    M.load_interaction_config(cfg_path)
    ck("enabled=true 超上界 → 钳到 2000",
       M.CONFIG["hover_trigger_ms"] == 2000, str(M.CONFIG["hover_trigger_ms"]))
    # true→false 重载干净回退（防「关了还残留自定义」）
    with open(cfg_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump({"enabled": False}, fh)
    M.load_interaction_config(cfg_path)
    ck("true→false 重载干净回退默认",
       M.CONFIG["hover_trigger_ms"] == 300.0, str(M.CONFIG["hover_trigger_ms"]))
    # 坏 yaml → 回退默认 + 不致命（不抛异常）；打桩 _log_crash 防污染项目根 crash.log
    with open(cfg_path, "w", encoding="utf-8") as fh:
        fh.write("enabled: [unclosed\n  bad: yaml: :::")
    _saved_log = M._log_crash
    M._log_crash = lambda *a, **k: None
    try:
        M.load_interaction_config(cfg_path)
        bad_ok = True
    except Exception:   # noqa: BLE001
        bad_ok = False
    finally:
        M._log_crash = _saved_log
    ck("坏 yaml → 回退默认不致命",
       bad_ok and M.CONFIG["hover_trigger_ms"] == 300.0, str(M.CONFIG["hover_trigger_ms"]))
    # history_points 单一真源接线：collect.history_points → monitor_ui.SPARKLINE_POINTS + hub
    with open(cfg_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump({"enabled": True, "collect": {"history_points": 90}}, fh)
    M.load_interaction_config(cfg_path)
    ck("collect.history_points=90 → SPARKLINE_POINTS 与 hub.HISTORY_POINTS 同步（单一真源）",
       mu.SPARKLINE_POINTS == 90 and M.monitor_hub.HISTORY_POINTS == 90,
       f"{mu.SPARKLINE_POINTS}/{M.monitor_hub.HISTORY_POINTS}")
    # monitor 组接线：enabled=true monitor.pane.base_w → monitor_ui.BASE_W
    with open(cfg_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump({"enabled": True, "monitor": {"pane": {"base_w": 300}}}, fh)
    M.load_interaction_config(cfg_path)
    ck("enabled=true monitor.pane.base_w=300 → monitor_ui.BASE_W",
       mu.BASE_W == 300, str(mu.BASE_W))
    # enabled=false 时 monitor 组也须回默认（不只 ball）
    with open(cfg_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump({"enabled": False, "monitor": {"pane": {"base_w": 300}}}, fh)
    M.load_interaction_config(cfg_path)
    ck("enabled=false → monitor 组也回默认（BASE_W=250）",
       mu.BASE_W == 250, str(mu.BASE_W))
    # 还原：指向不存在文件触发全默认回退 + 删临时 config.yaml
    M.load_interaction_config(os.path.join(_ISO, "no_such_config.yaml"))
    try:
        os.remove(cfg_path)
    except OSError:
        pass
    ck("还原后三组全回默认",
       M.CONFIG["hover_trigger_ms"] == 300.0 and mu.BASE_W == 250
       and mu.SPARKLINE_POINTS == 60 and M.monitor_hub.HISTORY_POINTS == 60)
    # 接线看守：load_interaction_config 须在 main() 启动与 reload_config 各调一次
    # （决策2：手动重载生效、不做文件监听；漏接线则调参永不生效，源码看守锁定）
    import inspect
    ck("main() 启动接线 load_interaction_config",
       "load_interaction_config" in inspect.getsource(M.main))
    ck("reload_config 接线 load_interaction_config",
       "load_interaction_config" in inspect.getsource(M.AuraBall.reload_config))
    # 缺口③：尺寸类调参重载后须对已开窗格重排（apply_tuning 只改模块全局）
    ck("reload_config 接线 retune_layout（尺寸类调参重排）",
       "retune_layout" in inspect.getsource(M.AuraBall.reload_config))
    # collect 硬件参数热重载：reload_config 须调 hub.retune_collect
    # （stale/max_restarts/poll_ms 现读即生效；sidecar_interval 变→重启侧车；
    #   history_points 变→清历史。main 侧用无参 MetricsHub()，构造默认读 COLLECT_LIVE）
    ck("reload_config 接线 retune_collect（collect 硬件参数热重载）",
       "retune_collect" in inspect.getsource(M.AuraBall.reload_config))

print("\n== 审计结果:", "PASS" if not fails else f"FAIL {len(fails)}", "==")
for f in fails:
    print("  -", f)
sys.exit(1 if fails else 0)