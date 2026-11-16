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
import shutil
import sys
import tempfile
import time

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
    # 计划 §七：孤儿回收须在「启动 + 重载」两处触发——重载后 command.yaml 可能
    # 删掉了指令，对应脚本即成孤儿；只在启动回收会让缓存随编辑历史无限增长
    ck("reload_config 接线脚本孤儿回收（指令被删后缓存不残留）",
       "reclaim_runner_orphans" in inspect.getsource(M.AuraBall.reload_config)
       or "cleanup_runner_dir" in inspect.getsource(M.AuraBall.reload_config))

# ---- 场景7：v2.03 四文件路由（统一视图漏斗：write 拆分 + read 合并 + 旧单文件回退 + 导入不覆盖 state）----
import inspect as _insp   # noqa: E402
_d7 = os.path.join(_ISO, "Aura_Pixie")
os.makedirs(_d7, exist_ok=True)
_set7 = os.path.join(_d7, "setting.yaml")
_cmd7 = os.path.join(_d7, "command.yaml")
_state7 = os.path.join(_d7, "state.yaml")
# 显式把四路径指向隔离目录（write/read 漏斗按 path==SETTINGS_PATH 判主位置）
M.SETTINGS_PATH = M.CONFIG_PATH = _set7
M.COMMAND_PATH, M.STATE_PATH = _cmd7, _state7
for _f in (_set7, _cmd7, _state7):
    if os.path.exists(_f):
        os.remove(_f)
ck("COMMAND_PATH/STATE_PATH 与 setting.yaml 同目录（兄弟文件）",
   os.path.dirname(M.COMMAND_PATH) == os.path.dirname(M.SETTINGS_PATH)
   and os.path.dirname(M.STATE_PATH) == os.path.dirname(M.SETTINGS_PATH))

_full7 = {
    "functions": [{"name": "功能甲", "code": "print(1)", "active": True}],
    "appearance": {"breath": True, "color_mid": "#123456"},
    "autostart": {"enabled": False},
    "window": {"always_on_top": True, "locked": False, "pos_x": 111.0, "pos_y": 222.0},
    "monitor": {"enabled": True, "refresh_ms": 1500, "idle_stop_sec": 120,
                "units": {"cpu.load": {"enabled": True, "threshold": []}},
                "layout": {"cpu.load": {"x": 10, "y": 20, "group_id": "g1"}}},
}
M.write_setting(_full7, _set7)
_rs = yaml.safe_load(open(_set7, encoding="utf-8")) if os.path.exists(_set7) else {}
_rc = yaml.safe_load(open(_cmd7, encoding="utf-8")) if os.path.exists(_cmd7) else {}
_rt = yaml.safe_load(open(_state7, encoding="utf-8")) if os.path.exists(_state7) else {}
ck("write 拆分：functions 落 command.yaml", os.path.exists(_cmd7)
   and (_rc.get("functions") or [{}])[0].get("name") == "功能甲")
ck("write 拆分：setting.yaml 不再含 functions", "functions" not in _rs)
ck("write 拆分：window.pos 落 state.yaml",
   _rt.get("window", {}).get("pos_x") == 111.0 and _rt.get("window", {}).get("pos_y") == 222.0,
   str(_rt.get("window")))
ck("write 拆分：monitor.layout 落 state.yaml",
   _rt.get("monitor", {}).get("layout", {}).get("cpu.load", {}).get("x") == 10)
ck("write 拆分：setting.yaml window 不含 pos、保留开关",
   "pos_x" not in _rs.get("window", {}) and _rs.get("window", {}).get("always_on_top") is True,
   str(_rs.get("window")))
ck("write 拆分：setting.yaml monitor 不含 layout、保留 enabled/refresh_ms/idle_stop_sec/units",
   "layout" not in _rs.get("monitor", {}) and _rs.get("monitor", {}).get("enabled") is True
   and _rs.get("monitor", {}).get("refresh_ms") == 1500
   and _rs.get("monitor", {}).get("idle_stop_sec") == 120
   and "cpu.load" in _rs.get("monitor", {}).get("units", {}),
   str(list(_rs.get("monitor", {}))))
ck("write 拆分：setting.yaml 写 layout_version=2 迁移标记",
   _rs.get("layout_version") == 2, str(_rs.get("layout_version")))

_back7 = M.read_setting(_set7)
ck("read 合并视图：functions 还原", (_back7.get("functions") or [{}])[0].get("name") == "功能甲")
ck("read 合并视图：window.pos 还原", _back7.get("window", {}).get("pos_x") == 111.0)
ck("read 合并视图：window 开关还原", _back7.get("window", {}).get("always_on_top") is True)
ck("read 合并视图：monitor.layout 还原",
   _back7.get("monitor", {}).get("layout", {}).get("cpu.load", {}).get("x") == 10)
ck("read 合并视图：monitor.units 还原", "cpu.load" in _back7.get("monitor", {}).get("units", {}))
ck("read 合并视图：appearance 还原", _back7.get("appearance", {}).get("color_mid") == "#123456")

# 优雅回退：删 command/state，仅留旧式全量 setting.yaml（老用户未迁移也不丢配置）
for _f in (_cmd7, _state7):
    if os.path.exists(_f):
        os.remove(_f)
_legacy7 = {
    "functions": [{"name": "旧功能", "code": "print(9)", "active": True}],
    "appearance": {"color_mid": "#ABCDEF"},
    "window": {"always_on_top": False, "pos_x": 555.0, "pos_y": 666.0},
    "monitor": {"enabled": False, "layout": {"cpu.temp": {"x": 1, "y": 2}}},
}
with open(_set7, "w", encoding="utf-8") as fh:
    yaml.safe_dump(_legacy7, fh, allow_unicode=True)
_rb7 = M.read_setting(_set7)
ck("旧单文件回退：functions 从 setting.yaml 读", (_rb7.get("functions") or [{}])[0].get("name") == "旧功能")
ck("旧单文件回退：window.pos 从 setting.yaml 读", _rb7.get("window", {}).get("pos_x") == 555.0)
ck("旧单文件回退：monitor.layout 从 setting.yaml 读",
   _rb7.get("monitor", {}).get("layout", {}).get("cpu.temp", {}).get("x") == 1)

# 导入不覆盖 state.yaml：apply_imported_config 保留当前坐标/布局，不被导入包冲掉
M.write_setting(_full7, _set7)   # 先建立 state：pos 111/222、layout cpu.load 10/20
ck("前置：state.yaml 已建立坐标 111/222",
   os.path.exists(_state7)
   and (yaml.safe_load(open(_state7, encoding="utf-8")).get("window", {}).get("pos_x") == 111.0))
ck("apply_imported_config 函数存在", hasattr(M, "apply_imported_config"))
if hasattr(M, "apply_imported_config"):
    _imp7 = {"functions": [{"name": "导入功能", "code": "print(2)", "active": True}],
             "appearance": {"color_mid": "#654321"},
             "window": {"always_on_top": True, "pos_x": 999.0, "pos_y": 999.0},
             "monitor": {"enabled": True, "layout": {"cpu.load": {"x": 999, "y": 999}}}}
    M.apply_imported_config(_imp7, _set7)
    _ra7 = yaml.safe_load(open(_state7, encoding="utf-8"))
    ck("导入不覆盖 state.yaml：坐标仍 111/222（不被导入的 999 冲掉）",
       _ra7.get("window", {}).get("pos_x") == 111.0, str(_ra7.get("window")))
    ck("导入不覆盖 state.yaml：layout 仍 10/20",
       _ra7.get("monitor", {}).get("layout", {}).get("cpu.load", {}).get("x") == 10)
    _rsi = yaml.safe_load(open(_set7, encoding="utf-8"))
    _rci = yaml.safe_load(open(_cmd7, encoding="utf-8")) if os.path.exists(_cmd7) else {}
    ck("导入写 setting.yaml（appearance/window 开关用导入值）",
       _rsi.get("appearance", {}).get("color_mid") in ("#654321", "#654321".upper())
       and _rsi.get("window", {}).get("always_on_top") is True)
    ck("导入写 command.yaml（functions 用导入值）",
       (_rci.get("functions") or [{}])[0].get("name") == "导入功能")

# 非主位置路径（导出 .bak）写完整单文件，不拆分、不覆盖主 command/state
_bak7 = os.path.join(_d7, "setting.yaml.bak")
_cmd_before = (yaml.safe_load(open(_cmd7, encoding="utf-8"))
               if os.path.exists(_cmd7) else None)
_state_before = (yaml.safe_load(open(_state7, encoding="utf-8"))
                 if os.path.exists(_state7) else None)
M.write_setting(_full7, _bak7)
_rbak = yaml.safe_load(open(_bak7, encoding="utf-8"))
ck("导出 .bak 写完整单文件（含 functions + window.pos + monitor.layout）",
   (_rbak.get("functions") or [{}])[0].get("name") == "功能甲"
   and _rbak.get("window", {}).get("pos_x") == 111.0
   and _rbak.get("monitor", {}).get("layout", {}).get("cpu.load", {}).get("x") == 10,
   str(list(_rbak)))
_cmd_after = (yaml.safe_load(open(_cmd7, encoding="utf-8"))
              if os.path.exists(_cmd7) else None)
_state_after = (yaml.safe_load(open(_state7, encoding="utf-8"))
                if os.path.exists(_state7) else None)
ck("导出 .bak 不触发对主 command.yaml 的拆分覆盖（前后内容不变）",
   _cmd_before == _cmd_after, f"{_cmd_before} -> {_cmd_after}")
ck("导出 .bak 不触发对主 state.yaml 的覆盖（前后内容不变）",
   _state_before == _state_after, f"{_state_before} -> {_state_after}")

for _f in (_set7, _cmd7, _state7, _bak7):
    if os.path.exists(_f):
        try:
            os.remove(_f)
        except OSError:
            pass

# ---- 场景8：v2.03 首启一次性迁移 + backup 快照（决策7 + 计划 §十）----
_d8 = os.path.join(_ISO, "mig8")
os.makedirs(_d8, exist_ok=True)
_set8 = os.path.join(_d8, "setting.yaml")
_cmd8 = os.path.join(_d8, "command.yaml")
_state8 = os.path.join(_d8, "state.yaml")
_bkdir8 = os.path.join(_d8, "backup")
M.SETTINGS_PATH = M.CONFIG_PATH = _set8
M.COMMAND_PATH, M.STATE_PATH = _cmd8, _state8
M.BACKUP_DIR = _bkdir8
for _f in (_set8, _cmd8, _state8):
    if os.path.exists(_f):
        os.remove(_f)
shutil.rmtree(_bkdir8, ignore_errors=True)

# 注意：上面已 monkeypatch M.BACKUP_DIR 做隔离，hasattr 必为 True（弱断言，坑 65）；
# 故用源码看守验证 main.py 真的定义了 BACKUP_DIR 常量与迁移函数
_main_src8 = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "main.py"), encoding="utf-8").read()
ck("main.py 定义 BACKUP_DIR 常量", "BACKUP_DIR =" in _main_src8)
ck("migrate_to_split_layout 函数存在", hasattr(M, "migrate_to_split_layout"))
ck("迁移/备份在运行时读 BACKUP_DIR（可被隔离替换，非默认参数绑定）",
   "_backup_snapshot(" in _main_src8)
if hasattr(M, "migrate_to_split_layout") and "BACKUP_DIR =" in _main_src8:
    # 老式单文件（无 layout_version，functions/坐标/layout 全挤在 setting.yaml）
    _legacy8 = {
        "functions": [{"name": "老功能", "code": "print(7)", "active": True},
                      {"name": "老功能2", "code": "print(8)", "active": False}],
        "appearance": {"breath": False, "color_mid": "#0A0B0C", "idle_sec": 90},
        "autostart": {"enabled": True},
        "window": {"always_on_top": True, "locked": True,
                   "pos_x": 777.0, "pos_y": 888.0},
        "monitor": {"enabled": True, "topmost": True, "refresh_ms": 2000,
                    "idle_stop_sec": 60,
                    "units": {"cpu.load": {"enabled": True, "threshold": []}},
                    "layout": {"cpu.load": {"x": 5, "y": 6, "group_id": "gg"}}},
    }
    with open(_set8, "w", encoding="utf-8") as fh:
        yaml.safe_dump(_legacy8, fh, allow_unicode=True)

    M.migrate_to_split_layout(_set8)
    # ① 迁移前全量快照落 backup\pre_split_<ts>\setting.yaml
    _pre = sorted(d for d in (os.listdir(_bkdir8) if os.path.isdir(_bkdir8) else [])
                  if d.startswith("pre_split_"))
    ck("迁移落 pre_split 快照目录", len(_pre) == 1, f"（实际 {_pre}）")
    _snap_path = os.path.join(_bkdir8, _pre[0], "setting.yaml") if _pre else None
    _snap = (yaml.safe_load(open(_snap_path, encoding="utf-8"))
             if _snap_path and os.path.exists(_snap_path) else None)
    ck("快照为迁移前全量内容（functions + 坐标 + layout）",
       isinstance(_snap, dict)
       and (_snap.get("functions") or [{}])[0].get("name") == "老功能"
       and _snap.get("window", {}).get("pos_x") == 777.0
       and _snap.get("monitor", {}).get("layout", {}).get("cpu.load", {}).get("x") == 5,
       f"（快照 {list(_snap) if isinstance(_snap, dict) else _snap}）")
    # ② 拆写三文件
    _rc8 = yaml.safe_load(open(_cmd8, encoding="utf-8")) if os.path.exists(_cmd8) else {}
    _rt8 = yaml.safe_load(open(_state8, encoding="utf-8")) if os.path.exists(_state8) else {}
    _rs8 = yaml.safe_load(open(_set8, encoding="utf-8"))
    ck("迁移拆出 command.yaml（functions 2 条全保留含未激活）",
       len(_rc8.get("functions") or []) == 2
       and _rc8["functions"][1].get("active") is False)
    ck("迁移拆出 state.yaml（坐标 777/888 + layout）",
       _rt8.get("window", {}).get("pos_x") == 777.0
       and _rt8.get("window", {}).get("pos_y") == 888.0
       and _rt8.get("monitor", {}).get("layout", {}).get("cpu.load", {}).get("x") == 5)
    ck("迁移后 setting.yaml 写 layout_version=2 且不含 functions/坐标/layout",
       _rs8.get("layout_version") == 2 and "functions" not in _rs8
       and "pos_x" not in _rs8.get("window", {})
       and "layout" not in _rs8.get("monitor", {}), str(list(_rs8)))
    # ③ 偏好与开关完整保留（不丢用户既有设置）
    ck("迁移保留 appearance（breath=False/色/idle_sec）",
       _rs8.get("appearance", {}).get("breath") is False
       and _rs8.get("appearance", {}).get("color_mid") == "#0A0B0C"
       and _rs8.get("appearance", {}).get("idle_sec") == 90, str(_rs8.get("appearance")))
    ck("迁移保留 window 开关（always_on_top/locked）",
       _rs8.get("window", {}).get("always_on_top") is True
       and _rs8.get("window", {}).get("locked") is True, str(_rs8.get("window")))
    ck("迁移保留 monitor 开关与 refresh_ms/idle_stop_sec（决策：不剥离，用户已设值）",
       _rs8.get("monitor", {}).get("enabled") is True
       and _rs8.get("monitor", {}).get("topmost") is True
       and _rs8.get("monitor", {}).get("refresh_ms") == 2000
       and _rs8.get("monitor", {}).get("idle_stop_sec") == 60,
       str(list(_rs8.get("monitor", {}))))
    ck("迁移保留 monitor.units 勾选",
       _rs8.get("monitor", {}).get("units", {}).get("cpu.load", {}).get("enabled") is True)
    ck("迁移保留 autostart", _rs8.get("autostart", {}).get("enabled") is True)
    # ④ 迁移后视图读回与老配置等价（端到端不丢数据）
    _view8 = M.read_setting(_set8)
    ck("迁移后视图还原：functions/坐标/layout/外观全部可读回",
       (_view8.get("functions") or [{}])[0].get("name") == "老功能"
       and _view8.get("window", {}).get("pos_x") == 777.0
       and _view8.get("window", {}).get("locked") is True
       and _view8.get("monitor", {}).get("layout", {}).get("cpu.load", {}).get("x") == 5
       and _view8.get("appearance", {}).get("color_mid") == "#0A0B0C")
    # ⑤ 幂等：已迁移（layout_version=2）再调不重复快照、不改内容
    _before_ids = sorted(os.listdir(_bkdir8))
    _before_cmd = yaml.safe_load(open(_cmd8, encoding="utf-8"))
    _before_state = yaml.safe_load(open(_state8, encoding="utf-8"))
    M.migrate_to_split_layout(_set8)
    ck("迁移幂等：已迁移再调不新增快照",
       sorted(os.listdir(_bkdir8)) == _before_ids, f"（{sorted(os.listdir(_bkdir8))}）")
    ck("迁移幂等：command/state 内容不变",
       yaml.safe_load(open(_cmd8, encoding="utf-8")) == _before_cmd
       and yaml.safe_load(open(_state8, encoding="utf-8")) == _before_state)
    # ⑥ backup 保留最近 5 份：预置 6 个不同时间戳的旧快照 + 触发一次真实迁移
    #    （不靠连续迁移：时间戳精度为秒，同秒多次会目录名冲突）
    shutil.rmtree(_bkdir8, ignore_errors=True)
    for _ts in ("20260101000001", "20260101000002", "20260101000003",
                "20260101000004", "20260101000005", "20260101000006"):
        os.makedirs(os.path.join(_bkdir8, f"pre_split_{_ts}"), exist_ok=True)
        with open(os.path.join(_bkdir8, f"pre_split_{_ts}", "setting.yaml"),
                  "w", encoding="utf-8") as fh:
            yaml.safe_dump({"marker": _ts}, fh)
    with open(_set8, "w", encoding="utf-8") as fh:      # 回到未迁移态
        yaml.safe_dump(_legacy8, fh, allow_unicode=True)
    for _f in (_cmd8, _state8):
        if os.path.exists(_f):
            os.remove(_f)
    M.migrate_to_split_layout(_set8)
    _allsnaps = sorted(d for d in os.listdir(_bkdir8) if d.startswith("pre_split_"))
    ck("backup 裁剪到最近 5 份 pre_split 快照", len(_allsnaps) == 5,
       f"（实际 {len(_allsnaps)} 份：{_allsnaps}）")
    ck("裁剪保留最新 5 份、删掉最旧的 20260101000001",
       "pre_split_20260101000001" not in _allsnaps
       and "pre_split_20260101000006" in _allsnaps, f"（{_allsnaps}）")
    ck("裁剪只动 backup 内自建快照，不碰配置四文件",
       os.path.exists(_set8) and os.path.exists(_cmd8) and os.path.exists(_state8))
    # ⑦ 导入覆盖前落 pre_import 快照
    shutil.rmtree(_bkdir8, ignore_errors=True)
    M.apply_imported_config({"functions": [{"name": "X", "code": "p", "active": True}],
                             "appearance": {"color_mid": "#111111"}}, _set8)
    _imps = sorted(d for d in os.listdir(_bkdir8) if d.startswith("pre_import_")) \
        if os.path.isdir(_bkdir8) else []
    ck("导入覆盖前落 pre_import 快照", len(_imps) >= 1, f"（实际 {_imps}）")
    _imp_snap_p = os.path.join(_bkdir8, _imps[0]) if _imps else None
    _imp_snap = None
    if _imp_snap_p and os.path.isdir(_imp_snap_p):
        _cands = [os.path.join(_imp_snap_p, n) for n in os.listdir(_imp_snap_p)]
        _imp_snap = yaml.safe_load(open(_cands[0], encoding="utf-8")) if _cands else None
    elif _imp_snap_p and os.path.isfile(_imp_snap_p):
        _imp_snap = yaml.safe_load(open(_imp_snap_p, encoding="utf-8"))
    ck("pre_import 快照为导入前完整视图（含被覆盖前 functions/坐标）",
       isinstance(_imp_snap, dict)
       and (_imp_snap.get("functions") or [{}])[0].get("name") == "老功能"
       and _imp_snap.get("window", {}).get("pos_x") == 777.0,
       f"（快照 {list(_imp_snap) if isinstance(_imp_snap, dict) else _imp_snap}）")
    _rt_after = yaml.safe_load(open(_state8, encoding="utf-8"))
    ck("导入后 state.yaml 坐标仍未被覆盖（777/888）",
       _rt_after.get("window", {}).get("pos_x") == 777.0, str(_rt_after.get("window")))

for _f in (_set8, _cmd8, _state8):
    if os.path.exists(_f):
        try:
            os.remove(_f)
        except OSError:
            pass
shutil.rmtree(_bkdir8, ignore_errors=True)
shutil.rmtree(_d8, ignore_errors=True)

print("\n== 审计结果:", "PASS" if not fails else f"FAIL {len(fails)}", "==")
for f in fails:
    print("  -", f)
sys.exit(1 if fails else 0)