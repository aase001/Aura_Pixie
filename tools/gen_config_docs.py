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

README_SRC = os.path.join(ROOT, "handover_documents", "配置文件夹README-v2.03.md")
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


with open(OUT, "w", encoding="utf-8") as fh:
    fh.write('# -*- coding: utf-8 -*-\n')
    fh.write('"""v2.03 首启释放到配置文件夹的两份文本（代码内嵌，避免打包遗漏）。\n\n')
    fh.write('CONFIG_README_TEXT     -> %LOCALAPPDATA%\\\\Aura_Pixie\\\\README.md\n')
    fh.write('CONFIG_TUNING_TEMPLATE -> %LOCALAPPDATA%\\\\Aura_Pixie\\\\config.yaml\n\n')
    fh.write('本文件由 tools/gen_config_docs.py 从 handover_documents/ 的 README\n')
    fh.write('与各模块内置默认值机械生成，请勿手改：改了 README 或默认值后重新生成，\n')
    fh.write('否则两份文档会与实现漂移（check_config_compat 场景9 有零漂移断言看守）。\n')
    fh.write('"""\n\n')
    fh.write(as_literal(readme, "CONFIG_README_TEXT"))
    fh.write("\n")
    fh.write(as_literal(tuning, "CONFIG_TUNING_TEMPLATE"))

# 往返自检：把刚写出的模块 import 回来，逐字节比对——任何嵌入/转义 bug 在此暴露
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("_cfgdocs_roundtrip", OUT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
assert _mod.CONFIG_README_TEXT == readme, "README 往返不一致（嵌入 bug）"
assert _mod.CONFIG_TUNING_TEMPLATE == tuning, "config 模板往返不一致（嵌入 bug）"
# 模板往返后仍须是合法 YAML 且 enabled=false（防孤立反斜杠那类破坏文档结构的 bug）
_rt = _yaml.safe_load(_mod.CONFIG_TUNING_TEMPLATE)
assert _rt["enabled"] is False and set(_rt) >= {"enabled", "ball", "monitor", "collect"}
# README 往返后不得以反斜杠/空白开头（首行应是标题）
assert _mod.CONFIG_README_TEXT.startswith("# Aura Pixie"), \
    repr(_mod.CONFIG_README_TEXT[:40])
print("往返自检通过：README %d 字符 / 模板 %d 字符，均逐字节一致"
      % (len(_mod.CONFIG_README_TEXT), len(_mod.CONFIG_TUNING_TEMPLATE)))
print("已生成:", OUT, os.path.getsize(OUT), "字节")
