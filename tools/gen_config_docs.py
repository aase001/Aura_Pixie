# -*- coding: utf-8 -*-
"""生成 config_docs.py（内嵌 README + 出厂 config.yaml 模板）。

正式工具，非一次性脚本：README 或任一模块默认值改动后须重跑本脚本，否则内嵌
文本会与实现漂移。跑法：python tools/gen_config_docs.py（退出码 0 = 生成并通过自检）。

为什么机械生成而不是手抄：
  · README 384 行 + config.yaml 模板近百键，手抄必然出错且日后难核对；
  · 模板数值直接读 BALL_DEFAULTS / MONITOR_DEFAULTS / COLLECT_DEFAULTS，
    保证「出厂值 == 内置默认」在生成时刻就成立；
  · 每键的【作用范围】【影响】【取值范围】注释从 README 对应条目解析，
    README 与 config.yaml 注释同源，不会各说各话（决策4 要求每项都带说明）。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["AURA_AUTOSTART_DRYRUN"] = "1"
os.environ.setdefault("LOCALAPPDATA", os.path.join(ROOT, "build", "_gen_iso"))

from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])
import main as M  # noqa: E402
import monitor_ui as mu  # noqa: E402
import monitor_hub as mh  # noqa: E402

README_SRC = os.path.join(ROOT, "handover_documents", "old_documents", "配置文件夹README-v2.03.md")
OUT = os.path.join(ROOT, "config_docs.py")

readme = open(README_SRC, encoding="utf-8").read()

# ---------------------------------------------------------------- 解析 README 键块
# 形如：**ball.hover.trigger_ms** ｜ 悬停展开等待 ｜ 默认 300
#        - 作用范围：...
#        - 影响：...
#        - 取值范围：50~2000（毫秒）。
BLOCK = re.compile(
    r"\*\*([A-Za-z0-9_.]+)\*\*\s*｜\s*(.+?)\s*｜\s*默认\s*(.+?)\s*\n((?:-\s.+\n)+)")
INFO = {}
for m in BLOCK.finditer(readme):
    key, disp, bullets = m.group(1), m.group(2), m.group(4)
    d = {}
    for b in bullets.strip().splitlines():
        b = b.lstrip("- ").strip()
        for tag in ("作用范围", "影响", "取值范围", "说明"):
            if b.startswith(tag + "："):
                d[tag] = b[len(tag) + 1:].strip()
    INFO[key] = {"disp": disp, **d}

print("解析到 README 键块：", len(INFO))
for k in ("ball.hover.trigger_ms", "monitor.pane.base_w", "monitor.spark.max_h_px",
          "monitor.semantics.green", "collect.history_points",
          "ball.click.confirm_extra_ms", "monitor.spark.exclude_keys"):
    assert k in INFO, f"README 缺少键块 {k}"

# ---------------------------------------------------------------- 结构规格
BALL_GROUPS = [
    ("hover", [("trigger_ms", "hover_trigger_ms"),
               ("leave_collapse_ms", "leave_collapse_ms"),
               ("poll_ms", "poll_ms"),
               ("ring_pad", "ring_pad")]),
    ("click", [("confirm_extra_ms", "confirm_extra_ms")]),
    ("drag", [("start_px", "drag_start_px"), ("stiffness", "drag_stiffness"),
              ("grace_ms", "drag_grace_ms"), ("press_hold_ms", "press_hold_ms"),
              ("snap_margin", "snap_margin"), ("snap_anim_ms", "snap_anim_ms")]),
    ("anim", [("expand_ms", "expand_ms"), ("collapse_ms", "collapse_ms"),
              ("flash_ok_ms", "flash_ms_ok"), ("flash_fail_ms", "flash_ms_fail"),
              ("warn_period_ms", "warn_period_ms")]),
]
PANE = ["base_w", "base_w_expanded", "base_h", "font_px", "pad_x", "pad_y",
        "col_gap", "row_gap", "corner_px", "halo_px", "halo_alpha", "body_alpha"]
SPARK = ["max_h_px", "bottom_gap_px", "line_px", "breath_hz",
         "breath_alpha_min", "breath_alpha_max", "exclude_keys"]
SNAP = ["drag_threshold_px", "preview_px", "strong_px", "anim_ms", "fuse_dash_px"]
COLLECT = ["refresh_ms", "idle_stop_sec", "sidecar_interval_ms", "stale_sec",
           "max_restarts", "poll_ms", "history_points", "first_frame_timeout_sec"]
BANDS = ["percent", "celsius", "rpm", "net", "freq", "power", "size"]
BANDS_INFO = {
    "percent": ("百分比类分档边界（%）",
                "所有利用率指标（CPU/内存/GPU/显存利用）与用量比例类"
                "（内存/共享内存/显存用量按用量÷总量）共用这组边界",
                "调低=更早变色更敏感；改 red_max 还会改变曲线红档纵轴上限"),
    "celsius": ("温度类分档边界（℃）", "CPU温度、GPU温度、硬盘温度的绿/黄/红分界",
                "调低=更早报红；red_max 同时是曲线红档纵轴上限"),
    "rpm": ("转速类分档边界（RPM）", "风扇转速的绿/黄/红分界",
            "调低=更早报红；本机若无可读风扇则观感无变化"),
    "net": ("网速类分档边界（MB/s）", "网络发送/接收速率的绿/黄/红分界",
            "调低=更早报红；网速曲线纵轴保留动态范围，不受 red_max 影响"),
    "freq": ("频率类分档边界（GHz）", "CPU频率的绿/黄/红分界", "调低=更早报红"),
    "power": ("功耗类分档边界（W）", "CPU功耗、GPU功耗的绿/黄/红分界", "调低=更早报红"),
    "size": ("容量类分档边界（GB）",
             "预留：当前无指标走 GB 绝对值配色（内存/共享内存/显存用量一律按百分比）",
             "现在改它没有可见变化；将来新增按 GB 配色的指标才生效"),
}

# ---------------------------------------------------------------- 工具函数
def val_repr(v):
    """把默认值渲染成 YAML 字面量。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return '"%s"' % v
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, int):
        return str(v)
    raise TypeError(v)


def comments(key, indent):
    """按 README 条目生成注释行（决策4：每项都带中文作用范围/影响）。"""
    info = INFO.get(key)
    if not info:
        return []
    pad = " " * indent
    out = ["%s# %s" % (pad, info["disp"])]
    for tag in ("作用范围", "影响", "取值范围", "说明"):
        if info.get(tag):
            out.append("%s#   %s：%s" % (pad, tag, info[tag]))
    return out


# ---------------------------------------------------------------- 生成 config.yaml
L = []
A = L.append
A("# Aura Pixie 数值调参（v2.03 起独立成文件）")
A("#")
A("# 本文件位于 %LOCALAPPDATA%\\Aura_Pixie\\config.yaml，全部数值可调。")
A("# 详细说明见同目录 README.md（每个参数的作用范围/影响/取值范围都有中文解释）。")
A("#")
A("# 【总开关 enabled 最重要，先读这条】")
A("#   false（出厂默认）= 安全模式：忽略本文件所有数值改动，一律按程序内置默认运行。")
A("#   true             = 本文件数值生效（每个键仍会做范围钳制，非法值回退默认）。")
A("#   调参三步：① enabled 改 true → ② 改想调的数值 → ③ 球体上右键「重载」生效。")
A("#   （程序不监听文件，改完必须手动重载；想一键回默认就把 enabled 改回 false。）")
A("#")
A("# 【出厂值 == 内置默认】所以下面这些数字现在改不改都一样，只有 enabled: true")
A("#   之后才体现差异。超范围会被钳到边界、非法值回退默认，不会把程序搞坏；")
A("#   问题会记入同目录 crash.log。")
A("#")
A("# 【不影响】command.yaml 的功能指令、setting.yaml 的外观偏好、state.yaml 的")
A("#   坐标布局，无论开关真假都照常生效。")
A("")
A("enabled: false")
A("")
A("# ===========================================================================")
A("# ball 球体交互（悬停 / 点击 / 拖动吸附 / 动画）")
A("# ===========================================================================")
A("ball:")
for sub, keys in BALL_GROUPS:
    A("  %s:" % sub)
    for yk, ck in keys:
        full = ".".join(("ball", sub, yk))
        for c in comments(full, 4):
            A(c)
        A("    %s: %s" % (yk, val_repr(M.BALL_DEFAULTS[ck])))
        A("")
A("# ===========================================================================")
A("# monitor 监视窗格（窗格几何 / 曲线 / 磁吸 / 语义色与分档）")
A("# ===========================================================================")
A("monitor:")
A("  pane:")
for k in PANE:
    for c in comments("monitor.pane." + k, 4):
        A(c)
    A("    %s: %s" % (k, val_repr(mu.MONITOR_DEFAULTS["pane"][k])))
    A("")
A("  spark:")
for k in SPARK:
    full = "monitor.spark." + k
    if k == "exclude_keys":
        info = INFO.get(full, {})
        A("    # %s" % info.get("disp", "不画曲线的指标清单"))
        if info.get("作用范围"):
            A("    #   作用范围：%s" % info["作用范围"])
        if info.get("影响"):
            A("    #   影响：%s" % info["影响"])
        A("    #   取值：指标键名列表（支持 {i} 模板，如 disk.{i}.temp）")
        A("    exclude_keys:")
        for pat in mu.MONITOR_DEFAULTS["spark"]["exclude_keys"]:
            A('      - "%s"' % pat)
        A("")
        continue
    for c in comments(full, 4):
        A(c)
    A("    %s: %s" % (k, val_repr(mu.MONITOR_DEFAULTS["spark"][k])))
    A("")
A("  snap:")
for k in SNAP:
    for c in comments("monitor.snap." + k, 4):
        A(c)
    A("    %s: %s" % (k, val_repr(mu.MONITOR_DEFAULTS["snap"][k])))
    A("")
A("  semantics:")
A("    # 语义色：三档各自显示纯色、档内不渐变（绿就是绿黄就是黄红就是红）。")
A("    #   色值完全自由，红黄绿倒序也行——改的是颜色，不是「按数值分三档」这件事。")
for k in ("green", "yellow", "red"):
    info = INFO.get("monitor.semantics." + k, {})
    A("    # %s" % info.get("disp", k))
    if info.get("作用范围"):
        A("    #   作用范围：%s" % info["作用范围"])
    if info.get("影响"):
        A("    #   影响：%s" % info["影响"])
    A("    %s: %s" % (k, val_repr(mu.MONITOR_DEFAULTS["semantics"][k])))
    A("")
A("    # 分档边界：七类单位各自的 绿档上界 / 黄档上界 / 红档上界。")
A("    #   绿档 = 0 ~ green_max，黄档 = green_max ~ yellow_max，红档 = yellow_max ~ red_max。")
A("    #   约束：同类内必须 0 < green_max < yellow_max < red_max，否则该类整体回退默认。")
A("    #   red_max 不是摆设——它同时是曲线在红档时的纵轴上限。")
A("    #   优先级：setting.yaml 里给某指标单独设的 threshold > 这里的 bands > 内置默认。")
A("    bands:")
for bk in BANDS:
    disp, scope, effect = BANDS_INFO[bk]
    g, y, r = mu.MONITOR_DEFAULTS["semantics"]["bands"][bk]
    A("      # %s" % disp)
    A("      #   作用范围：%s" % scope)
    A("      #   影响：%s" % effect)
    A("      %s:" % bk)
    A("        green_max: %s" % val_repr(g))
    A("        yellow_max: %s" % val_repr(y))
    A("        red_max: %s" % val_repr(r))
    A("")
A("# ===========================================================================")
A("# collect 采集层（拉取节奏 / 闲置停采 / 侧车 / 历史缓冲）")
A("# ===========================================================================")
A("collect:")
for k in COLLECT:
    for c in comments("collect." + k, 2):
        A(c)
    A("  %s: %s" % (k, val_repr(mh.COLLECT_DEFAULTS[k])))
    A("")

tuning = "\n".join(L)

# ---------------------------------------------------------------- 自检生成结果
import yaml as _yaml  # noqa: E402

_parsed = _yaml.safe_load(tuning)
assert _parsed["enabled"] is False, "enabled 必须默认 false"
assert set(_parsed) >= {"enabled", "ball", "monitor", "collect"}, sorted(_parsed)
for _sub, _keys in BALL_GROUPS:
    for _yk, _ck in _keys:
        assert _parsed["ball"][_sub][_yk] == M.BALL_DEFAULTS[_ck], (_sub, _yk)
for _k in PANE:
    assert _parsed["monitor"]["pane"][_k] == mu.MONITOR_DEFAULTS["pane"][_k], _k
for _k in SPARK:
    if _k == "exclude_keys":
        assert tuple(_parsed["monitor"]["spark"][_k]) == \
            tuple(mu.MONITOR_DEFAULTS["spark"][_k]), _k
    else:
        assert _parsed["monitor"]["spark"][_k] == mu.MONITOR_DEFAULTS["spark"][_k], _k
for _k in SNAP:
    assert _parsed["monitor"]["snap"][_k] == mu.MONITOR_DEFAULTS["snap"][_k], _k
for _k in ("green", "yellow", "red"):
    assert _parsed["monitor"]["semantics"][_k] == \
        mu.MONITOR_DEFAULTS["semantics"][_k], _k
for _bk in BANDS:
    _g, _y, _r = mu.MONITOR_DEFAULTS["semantics"]["bands"][_bk]
    _got = _parsed["monitor"]["semantics"]["bands"][_bk]
    assert (_got["green_max"], _got["yellow_max"], _got["red_max"]) == (_g, _y, _r), _bk
for _k in COLLECT:
    assert _parsed["collect"][_k] == mh.COLLECT_DEFAULTS[_k], _k
assert "points" not in _parsed["monitor"]["spark"], "spark.points 已废弃不得出现"
assert tuning.count("作用范围") >= 20 and tuning.count("影响") >= 20
print("模板自检通过：作用范围 %d 次 / 影响 %d 次 / 总长 %d 字符"
      % (tuning.count("作用范围"), tuning.count("影响"), len(tuning)))


# ---------------------------------------------------------------- 三文件逐键注释
# 需求：参考 config.yaml 的注释方式（# 名称 + 作用范围/影响/取值范围），给
# setting/command/state 三个 yaml 的参数名也加注释。yaml.safe_dump 不产注释，
# 故注释表机械生成后内嵌 config_docs，由 main._write_yaml_block 写盘时注入。
# 键 = 点分路径；functions.*/monitor.layout.* 为列表项模板（渲染器对每个条目套用）。
# 文案与文件头/README 口径一致；数值范围读自代码常量，避免手抄漂移。
def _c(disp, scope, effect, rng=None, note=None):
    d = {"disp": disp, "作用范围": scope, "影响": effect}
    if rng:
        d["取值范围"] = rng
    if note:
        d["说明"] = note
    return d

SETTING_COMMENTS = {
    "appearance": _c("外观偏好", "球体与监视窗的视觉表现", "由「外观」设置窗写回，改后即时生效"),
    "appearance.breath": _c("呼吸渐变开关", "球体颜色是否随时间循环渐变", "关闭=恒起始色、相位停走"),
    "appearance.color_start": _c("呼吸起始色", "渐变第一段起点颜色", "改色即时生效", "#RRGGBB 十六进制"),
    "appearance.color_mid": _c("呼吸过渡色", "渐变中段颜色", "改色即时生效", "#RRGGBB 十六进制"),
    "appearance.color_end": _c("呼吸末尾色", "渐变末段颜色", "改色即时生效", "#RRGGBB 十六进制"),
    "appearance.dur_start_mid": _c("起始→过渡时长", "呼吸第一段渐变耗时", "调大=渐变越慢", "1~30 秒"),
    "appearance.dur_mid_end": _c("过渡→末尾时长", "呼吸第二段渐变耗时", "调大=渐变越慢", "1~30 秒"),
    "appearance.dur_end_start": _c("末尾→起始时长", "呼吸第三段渐变耗时", "调大=渐变越慢", "1~30 秒"),
    "appearance.aura_scale": _c("球体大小倍率", "球体直径/光晕/扇环几何整体缩放", "调大=球越大", "0.5~3.0"),
    "appearance.opacity": _c("球体透明度", "球体本体不透明度", "调小=越透明", "0~100（%）"),
    "appearance.sleep_opacity": _c("休眠透明度", "休眠态球体不透明度", "调小=休眠越隐形", "0~100（%）"),
    "appearance.idle_sec": _c("休眠倒计时", "无操作多少秒后进休眠", "0=永不休眠", "0 或 10~600 秒"),
    "appearance.monitor_color": _c("监视窗口色", "监视窗格底色/边缘虚化取色", "改色即时生效", "#RRGGBB 十六进制"),
    "appearance.monitor_fill": _c("监视底色填充开关", "窗格底色是否填充窗口色", "取消=填充透明色"),
    "appearance.monitor_opacity": _c("监视透明度", "监视窗格整体不透明度", "调小=窗格越透明", "0~100（%）"),
    "appearance.display_scale": _c("显示缩放（分单元）", "各单元独立缩放，未勾选恒按 100%", "勾选并设 percent 才生效"),
    "autostart": _c("开机自启动", "登录时自动拉起球体", "走计划任务（最高权限），不写注册表 Run"),
    "autostart.enabled": _c("自启开关", "是否注册开机自启计划任务", "勾选=注册，取消=删除任务"),
    "window": _c("球体窗口开关", "球体置顶与锁定", "球心坐标 pos_x/pos_y 在 state.yaml"),
    "window.always_on_top": _c("保持球体置顶", "球体是否总在最前", "与监视窗置顶相互独立"),
    "window.locked": _c("锁定球体", "禁止拖动球体", "锁定后仍可右键菜单"),
    "monitor": _c("资源监视", "监视总开关/置顶/锁定/采集周期/参数勾选", "窗格布局 layout 在 state.yaml"),
    "monitor.enabled": _c("监视总开关", "是否启用资源监视", "勾选并保存才拉起采集"),
    "monitor.topmost": _c("监视窗口置顶", "监视窗格是否总在最前", "与球体置顶独立"),
    "monitor.locked": _c("锁定监视窗格", "禁止移动窗格与拆分/合并组", "锁定后仍可右键菜单"),
    "monitor.refresh_ms": _c("拉取周期（用户已设值）", "窗格向采集层取数的间隔",
                             "三级优先级最高档：此处有值则盖过 config.yaml",
                             "200~5000 毫秒", "没设过才用 config.yaml 的 collect.refresh_ms"),
    "monitor.idle_stop_sec": _c("闲置停采（用户已设值）", "连续无监视窗多少秒后停采",
                                "三级优先级最高档：此处有值则盖过 config.yaml",
                                "0~3600 秒（0=永不停采）", "没设过才用 config.yaml 的 collect.idle_stop_sec"),
    "monitor.units": _c("参数勾选与阈值", "每个指标是否建窗 + 自定义分档阈值",
                        "在监视设置窗勾选；threshold 空=用默认分档"),
}
# display_scale 各单元（注册表单一来源）
for _uk, _ul in M.SCALE_UNITS:
    SETTING_COMMENTS[f"appearance.display_scale.{_uk}"] = _c(
        f"{_ul}缩放", f"{_ul}的显示缩放单元", "enabled 勾选 + percent 生效",
        "50~200 且为 25 的整数倍")
    SETTING_COMMENTS[f"appearance.display_scale.{_uk}.enabled"] = _c(
        "启用该单元缩放", f"是否对{_ul}应用自定义缩放", "取消=恒按 100%")
    SETTING_COMMENTS[f"appearance.display_scale.{_uk}.percent"] = _c(
        "缩放百分比", f"{_ul}的缩放比例", "调大=该单元越大", "50~200 且为 25 的整数倍")
# units 各参数键（指标定义表单一来源）
for _mk, _ml, _mg, _mu in mh.METRIC_DEFS:
    SETTING_COMMENTS[f"monitor.units.{_mk}"] = _c(
        f"{_ml}（{_mu}）", f"监视参数 {_mk} 的建窗开关与阈值", "enabled 勾选才建窗")
for _dk in ("gpu_d.0.load", "gpu_d.0.temp", "gpu_d.0.power",
            "gpu_d.0.vram_load", "gpu_d.0.vram_used",
            "fan.cpu.0", "fan.gpu_d.0", "fan.mobo.0",
            "disk.0.temp", "disk.0.usage"):
    SETTING_COMMENTS[f"monitor.units.{_dk}"] = _c(
        f"动态参数 {_dk}", "按硬件实例展开的监视参数", "enabled 勾选才建窗；多卡/多盘运行时展开")
SETTING_COMMENTS["monitor.units.*.enabled"] = _c("建窗开关", "该参数是否显示监视窗格", "勾选=建窗")
SETTING_COMMENTS["monitor.units.*.threshold"] = _c(
    "自定义分档阈值", "该参数的绿/黄/红分档", "空列表=用默认分档；非空覆盖 bands",
    "[{min,max,color}...] 列表")
SETTING_COMMENTS["layout_version"] = _c(
    "四文件拆分标记", "配置 schema 版本锚点", "2=已拆分；程序自动维护，勿手改")

COMMAND_COMMENTS = {
    "functions": _c("功能扇区列表", "球体展开后的扇区指令", "顺序=条目顺序；等分数=active 数"),
    "functions.*.name": _c("功能名称", "扇区上显示的名字", "任意字符；空名兜底「未命名」"),
    "functions.*.code": _c("指令代码", "点击扇区执行的内容",
                           "默认 py；cmd:/ps:/powershell:/pwsh: 前缀切换通道，均静默执行"),
    "functions.*.active": _c("是否激活", "该条目是否参与扇区", "true 计入等分；最少 2 最多 9"),
}

STATE_COMMENTS = {
    "window": _c("球心坐标记忆", "球体上次停留位置", "程序自动写入，请勿手改"),
    "window.pos_x": _c("球心 X 坐标", "球心水平位置（像素）", "移动停止后自动落盘"),
    "window.pos_y": _c("球心 Y 坐标", "球心垂直位置（像素）", "移动停止后自动落盘"),
    "monitor": _c("监视布局记忆", "窗格位置与吸附组", "程序自动写入，请勿手改"),
    "monitor.layout": _c("窗格布局表", "每个参数窗格的位置与所属组", "退出/移动停止后自动落盘"),
    "monitor.layout.*.x": _c("窗格 X 坐标", "窗格左上角水平位置（像素）", "越屏会自动夹回"),
    "monitor.layout.*.y": _c("窗格 Y 坐标", "窗格左上角垂直位置（像素）", "越屏会自动夹回"),
    "monitor.layout.*.group_id": _c("吸附组编号", "窗格所属磁吸组", "null=独立窗；同组刚性移动"),
}

# 注释表自检：键路径必须能对应到真实结构（防写错键名静默不生效）
_ds = M.default_settings()
assert "appearance" in SETTING_COMMENTS and "layout_version" in SETTING_COMMENTS
for _k in _ds["appearance"]:
    assert f"appearance.{_k}" in SETTING_COMMENTS, _k
for _k in ("enabled", "topmost", "locked", "refresh_ms", "idle_stop_sec", "units"):
    assert f"monitor.{_k}" in SETTING_COMMENTS, _k
for _mk in _ds["monitor"]["units"]:
    assert f"monitor.units.{_mk}" in SETTING_COMMENTS, _mk
assert set(COMMAND_COMMENTS) == {"functions", "functions.*.name",
                                 "functions.*.code", "functions.*.active"}
assert set(STATE_COMMENTS) >= {"window.pos_x", "window.pos_y", "monitor.layout",
                               "monitor.layout.*.x", "monitor.layout.*.y",
                               "monitor.layout.*.group_id"}
print("三文件注释表自检通过：setting %d 键 / command %d 键 / state %d 键"
      % (len(SETTING_COMMENTS), len(COMMAND_COMMENTS), len(STATE_COMMENTS)))


# ---------------------------------------------------------------- 写 config_docs.py
def as_literal(text, varname):
    """把长文本渲染成 Python 原始三引号字面量。

    注意：原始字符串里 `\\`+换行**不是**续行符（是字面反斜杠+换行），故绝不能在
    开头加 `\\` 折行——那会让值以孤立反斜杠开头，YAML 把它当首个标量文档，
    后续 enabled: 就成了非法第二文档（本脚本先前踩过）。直接 r\"\"\"text\"\"\"。
    前置断言保证：text 不含三引号、不以反斜杠结尾（否则退回 repr）。
    """
    if '"""' not in text and not text.endswith("\\"):
        return '%s = r"""%s"""\n' % (varname, text)
    return "%s = %s\n" % (varname, repr(text))


def dict_literal(d, varname):
    """把注释表渲染成 Python dict 字面量（键序稳定，便于 diff 核对）。"""
    lines = ["%s = {" % varname]
    for k in sorted(d):
        lines.append("    %r: %r," % (k, d[k]))
    lines.append("}")
    return "\n".join(lines) + "\n"


with open(OUT, "w", encoding="utf-8") as fh:
    fh.write('# -*- coding: utf-8 -*-\n')
    fh.write('"""v2.03 配置文件夹内嵌文本与注释表（代码内嵌，避免打包遗漏）。\n\n')
    fh.write('CONFIG_README_TEXT     -> %LOCALAPPDATA%\\\\Aura_Pixie\\\\README.md\n')
    fh.write('CONFIG_TUNING_TEMPLATE -> %LOCALAPPDATA%\\\\Aura_Pixie\\\\config.yaml\n')
    fh.write('SETTING_COMMENTS / COMMAND_COMMENTS / STATE_COMMENTS\n')
    fh.write('    -> setting/command/state 三文件的逐键注释（config.yaml 同款风格），\n')
    fh.write('       由 main._write_yaml_block 写盘时注入（yaml.safe_dump 不产注释）。\n\n')
    fh.write('本文件由 tools/gen_config_docs.py 从 handover_documents/ 的 README\n')
    fh.write('与各模块内置默认值机械生成，请勿手改：改了 README 或默认值后重新生成，\n')
    fh.write('否则文档会与实现漂移（check_config_compat 场景9/11 有断言看守）。\n')
    fh.write('"""\n\n')
    fh.write(as_literal(readme, "CONFIG_README_TEXT"))
    fh.write("\n")
    fh.write(as_literal(tuning, "CONFIG_TUNING_TEMPLATE"))
    fh.write("\n")
    fh.write(dict_literal(SETTING_COMMENTS, "SETTING_COMMENTS"))
    fh.write("\n")
    fh.write(dict_literal(COMMAND_COMMENTS, "COMMAND_COMMENTS"))
    fh.write("\n")
    fh.write(dict_literal(STATE_COMMENTS, "STATE_COMMENTS"))

# 往返自检：把刚写出的模块 import 回来，逐字节比对——任何嵌入/转义 bug 在此暴露
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("_cfgdocs_roundtrip", OUT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
assert _mod.CONFIG_README_TEXT == readme, "README 往返不一致（嵌入 bug）"
assert _mod.CONFIG_TUNING_TEMPLATE == tuning, "config 模板往返不一致（嵌入 bug）"
assert _mod.SETTING_COMMENTS == SETTING_COMMENTS, "setting 注释表往返不一致"
assert _mod.COMMAND_COMMENTS == COMMAND_COMMENTS, "command 注释表往返不一致"
assert _mod.STATE_COMMENTS == STATE_COMMENTS, "state 注释表往返不一致"
# 模板往返后仍须是合法 YAML 且 enabled=false（防孤立反斜杠那类破坏文档结构的 bug）
_rt = _yaml.safe_load(_mod.CONFIG_TUNING_TEMPLATE)
assert _rt["enabled"] is False and set(_rt) >= {"enabled", "ball", "monitor", "collect"}
# README 往返后不得以反斜杠/空白开头（首行应是标题）
assert _mod.CONFIG_README_TEXT.startswith("# Aura Pixie"), \
    repr(_mod.CONFIG_README_TEXT[:40])
print("往返自检通过：README %d 字符 / 模板 %d 字符，均逐字节一致"
      % (len(_mod.CONFIG_README_TEXT), len(_mod.CONFIG_TUNING_TEMPLATE)))
print("已生成:", OUT, os.path.getsize(OUT), "字节")
