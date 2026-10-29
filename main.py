"""Aura Pixie —— 桌面发光圆球（阶段 1 + 设置界面 + 扇区执行上线）

规格
    · 呼吸圆球：基础 100×100px → 浮动 110×110px
    · 颜色循环：白 → #283FFE → 白，20 秒一周期，尺寸与颜色共用同一条时间轴
    · 光晕：球边缘外 +3px
    · 帧率：60 FPS（15ms 心跳 + 帧点节流）
    · 悬停：鼠标在球体 100×100 范围内累计 ≥300ms → 扇形外框展开
    · 扇形等分：= 激活功能模块数，最少 2、最多 9；总数不限
    · 扇形填充色：= 触发瞬间球体中心本色，展开期间冻结
    · 右键-设置：三列表格（复选框=激活 / 项目名 / 功能代码），支持拖拽排序，
      保存写回 %LOCALAPPDATA%\\Aura_Pixie\\setting.yaml 并立即重新加载（不得嵌套模态窗导致主程序退出）
    · 扇区点击：闪光反馈 → 静默执行代码 → 圆球复位

功能代码格式（标准化，三种终端）
    py:   或无前缀        —— Python 代码；交给当前解释器静默执行，崩溃不波及主程序
    cmd:  开头            —— CMD 命令；整段传给 cmd.exe /c 静默执行后结束
    ps:   / powershell:   —— Windows PowerShell 5.1 命令；-NoProfile -Command 静默执行后结束
    前缀可同行（cmd: dir）也可独占一行后换行写正文。空代码的条目按占位灰色处理。

运行
    .venv\\Scripts\\activate
    python main.py
    python main.py --selftest
"""

from __future__ import annotations

import ast
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import winreg

import yaml
from PySide6.QtCore import (QByteArray, QElapsedTimer, QEvent, QMimeData, QPoint,
                            QPointF, QRect, QRectF, QSharedMemory, Qt, QTimer, Signal)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtGui import (
    QCursor,
    QColor,
    QDrag,
    QFont,
    QIcon,
    QPalette,
    QPen,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QFileDialog,
    QScrollArea,
    QSpinBox,
    QStyledItemDelegate,
    QTableWidgetItem,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

# ---------------------------------------------------------------- 可调参数
CONFIG = {
    "size_base": 100.0,
    "size_breath": 110.0,
    # 呼吸颜色三段式：起始色 → 中间彩色 → 末尾色（「Aura」光效设置中可调）
    "color_start": "#FFFFFF",   # 呼吸起点色（固定白色，光效设置不可改）
    "color_mid": "#283FFE",     # 呼吸中间彩色（光效设置填十六进制 / 取色）
    "color_end": "#FFFFFF",     # 呼吸末尾色（固定白色，光效设置不可改）
    "aura_scale": 1.0,          # 呼吸动画速度倍率 0.1x~3x：相位推进 × 倍率（默认 1.0x）
    "idle_sec": 60,             # 动效休眠：无球体交互（悬停/点击/拖拽）达到该秒数 → 呼吸暂停+透明度10%（10~600）
    "opacity": 100,             # 总体透明度（%）：球体渲染 alpha 缩放，范围 10~100，默认 100
    "sleep_opacity": 10,        # 休眠透明度（%）：休眠时窗口透明度，范围 1~20，默认 10（超过总体透明度则存 10）
    "cycle_ms": 20000,
    "fps": 60,

    "glow_px": 3.0,
    "glow_alpha": 165,

    "ring_gap": 7.0,
    "ring_width": 44.0,
    "ring_sector_gap_deg": 4.0,
    "hover_trigger_ms": 300.0,
    "leave_collapse_ms": 180.0,
    "expand_ms": 320.0,
    "collapse_ms": 220.0,

    "sector_alpha": 150,
    "sector_alpha_hot": 235,
    "sector_alpha_dim": 70,
    "flash_ms_ok": 220.0,      # 成功闪光时长
    "flash_ms_fail": 420.0,    # 失败（红色）持续时长

    # —— 高亮响应（用户反馈落点检测不灵敏）——
    # 光标采样从「跟着 60fps 动画帧走」拆出来，单独 8ms 一轮：
    # 悬停高亮只需改一个扇区色，不必等动画帧，延迟远低于要求的 50ms。
    "poll_ms": 8,
    "ring_pad": 5.0,           # 感应外容差；同时补掉球与环之间的判定死区

    # —— 拖动与靠边吸附 ——
    "drag_stiffness": 120.0,  # 临界阻尼弹簧刚度 ω₀²(1/s²)：ω₀≈11 rad/s，起步缓、约 0.1s 达峰速
    "drag_start_px": 4.0,      # 按下后移动超过该距离才算拖动，否则仍算点击
    "snap_margin": 200.0,      # 扇形外边缘距屏幕边缘 ≤200px → 松手即吸附
    "snap_anim_ms": 280.0,     # 吸附 / 回位的非线性动画时长
    "warn_period_ms": 500.0,   # 越界闪烁周期
    "press_hold_ms": 500.0,    # 悬停展开扇形后按住球体超过该时长 → 判为即将拖动，回收扇形
    "drag_grace_ms": 500.0,    # 按住拖动回收宽限期：从按下起 500ms 内，无论是否已进入拖动，
                               # 都强制持续推进扇形收起（回收动画 220ms < 500ms，保证收完才跟手），
                               # 避免「扇形没收回就冻结」的半开扇形跟随鼠标

    "label_font_px": 13.0,     # 扇区名径向排布的基准字号（px）
    "label_spacing_em": 0.18,  # 字间距（相对字号）
    "label_max_units": 12,     # 项目名宽度上限：12 单位 = 6 汉字 = 12 英文字母
    # 字顶朝向："auto" = 任何位置都正读（上半环字顶朝外、下半环朝内，绝不倒置）；
    # "outward" = 恒朝外（环向徽章式，底部会倒读）；"center" = 恒朝球心（顶部会倒读）。
    # 三种都只做旋转、不反射，所以不存在左右镜像；字的竖轴始终沿半径穿过球心。
    "label_orientation": "auto",
}

def app_executable() -> str:
    """用户实际启动/分发的程序路径（配置文件的定位基准）。

    优先级：
      1) sys.argv[0] 的绝对路径且该文件真实存在 ——
         Nuitka onefile 引导（4.x 实测）把「原始 exe 全路径」作为 argv[0]
         传给 Python 跟随进程；此时 sys.executable 指向 TEMP 解压目录里
         的内部 python.exe，sys.frozen 也为 False，只有 argv[0] 可信。
      2) 其他 frozen（standalone 等）：sys.executable 就是 exe。
      3) 源码运行：本文件绝对路径。
    """
    argv0 = os.path.abspath(sys.argv[0]) if (sys.argv and sys.argv[0]) else ""
    if argv0 and os.path.isfile(argv0):
        return argv0
    if getattr(sys, "frozen", False):
        return sys.executable
    return os.path.abspath(__file__)


BASE_DIR = os.path.dirname(app_executable())   # exe/脚本所在目录（迁移源、图标、crash.log 位置）
# 配置文件主位置：从本版本起迁移到 %LOCALAPPDATA%\Aura_Pixie\setting.yaml
# （exe 目录可能只读；用户配置统一放系统为当前用户保留的本地应用数据目录）
_APP_DATA_ROOT = os.environ.get("LOCALAPPDATA") or os.path.join(
    os.path.expanduser("~"), "AppData", "Local")
APP_CONFIG_DIR = os.path.join(_APP_DATA_ROOT, "Aura_Pixie")
CONFIG_PATH = os.path.join(APP_CONFIG_DIR, "setting.yaml")   # 统一配置文件（functions+aura+后续块）
SETTINGS_PATH = CONFIG_PATH
ICON_PATH = os.path.join(BASE_DIR, "icon2.png")   # 兜底：内嵌资源加载失败时回退文件
RUNNER_DIR = os.path.join(tempfile.gettempdir(), "AuraPixie")   # py 代码临时脚本目录

try:
    from icon_res import load_app_icon as _load_builtin_icon
except Exception:   # noqa: BLE001 - 资源模块缺失/损坏时不阻塞启动
    def _load_builtin_icon():
        return QIcon()

PLACEHOLDER_COLOR = QColor("#9AA0A6")
MIN_SECTORS, MAX_SECTORS = 2, 9
CREATE_NO_WINDOW = 0x08000000

TARGET_FRAME_MS = 1000.0 / CONFIG["fps"]
HEARTBEAT_MS = 15   # 心跳比 16.667ms 密，逐拍跨帧点才重绘，平均正好 60FPS

HALO_PX = CONFIG["glow_px"]
HOVER_RADIUS = CONFIG["size_base"] / 2.0
RING_IN = HOVER_RADIUS + HALO_PX + CONFIG["ring_gap"]
RING_OUT = RING_IN + CONFIG["ring_width"]
IDLE_SIZE = int(CONFIG["size_breath"] + 2 * HALO_PX + 4)
ACTIVE_SIZE = int(2 * (RING_OUT + 6))

_WHITE = QColor("#FFFFFF")   # 装饰白：高光层/闪光提亮用
_FAIL_RED = QColor("#FF5252")


# ---------------------------------------------------------------- 项目名规则
def name_units(s: str) -> int:
    """宽度单位：CJK/全角=2，其余（英文/数字/半角符号）=1。12 单位=6 汉字=12 字母。"""
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in s)


def clamp_name(s: str, limit: int = 12) -> str:
    """截断到 limit 宽度单位以内。"""
    out, used = [], 0
    for ch in s:
        w = 2 if ord(ch) > 0x2E7F else 1
        if used + w > limit:
            break
        out.append(ch)
        used += w
    return "".join(out)


def char_advance_weight(ch: str) -> float:
    """弧上排布时单个字符占的宽度权重（相对字号）。"""
    return 1.0 if ord(ch) > 0x2E7F else 0.62


# ---------------------------------------------------------------- 颜色与缓动
def mix_color(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )


def with_alpha(color: QColor, alpha: int) -> QColor:
    out = QColor(color)
    out.setAlpha(max(0, min(255, alpha)))
    return out


def luminance(c: QColor) -> float:
    return 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()


def choose_text_color(fill: QColor) -> QColor:
    """扇区文字颜色跟随填充色明度：冻结色接近白时用深色字，否则白字。

    扇区填充 = 触发瞬间本色；在白色帧触发时扇区近乎白色，固定白字会完全看不见。
    """
    return QColor(14, 20, 48) if luminance(fill) > 150 else QColor(255, 255, 255)


def ease_at(phase: float) -> float:
    return (1.0 - math.cos(2.0 * math.pi * (phase % 1.0))) / 2.0


def ease_out_cubic(x: float) -> float:
    return 1.0 - (1.0 - x) ** 3


def ease_in_out_cubic(x: float) -> float:
    """非线性「慢-快-慢停」：两端导数为 0，中段最快，用于靠边吸附与回位。"""
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return 4.0 * x * x * x if x < 0.5 else 1.0 - ((-2.0 * x + 2.0) ** 3) / 2.0


def system_dark_mode() -> bool:
    """当前系统颜色模式（浅色/深色）。Windows 10+ 读取注册表 AppsUseLightTheme：
    0=深色、1=浅色；读取失败时按窗口基色明度兜底（深于 128 判深色）。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(val) == 0
    except (FileNotFoundError, OSError, ValueError):
        pass
    try:
        app = QApplication.instance()
        if app is not None:
            return app.palette().color(QPalette.ColorRole.Window).lightness() < 128
    except Exception:   # noqa: BLE001 - 兜底失败不影响主流程
        pass
    return False


def breath_colors() -> tuple[QColor, QColor, QColor]:
    """当前呼吸三段色（起始 / 中间 / 末尾）。每次取读，光效设置改完立即生效。"""
    return (QColor(CONFIG["color_start"]),
            QColor(CONFIG["color_mid"]),
            QColor(CONFIG["color_end"]))


def shape_at(phase: float) -> tuple[float, float, QColor]:
    ease = ease_at(phase)
    diameter = CONFIG["size_base"] + (CONFIG["size_breath"] - CONFIG["size_base"]) * ease
    # 三段式颜色循环（按相位分段，20s 一周期）：
    #   前 50% 起始色→中间彩色，后 50% 中间彩色→末尾色。
    # 注意必须按 phase（单调时间）分段；按 ease 分段会把回程（ease 递减段）
    # 误当成新的前半程，导致中间色在周期后半重现。
    t = phase % 1.0
    c_start, c_mid, c_end = breath_colors()
    if t < 0.5:
        color = mix_color(c_start, c_mid, t * 2.0)
    else:
        color = mix_color(c_mid, c_end, (t - 0.5) * 2.0)
    return diameter, diameter / 2.0, color


def center_color_at(phase: float) -> QColor:
    """触发瞬间要冻结的颜色 = 球体渐变中心处本色（不含装饰高光层）。"""
    return shape_at(phase)[2]


# ---------------------------------------------------------------- 代码格式标准化与执行
_KIND_RE = re.compile(r"^\s*(py|python|cmd|ps|powershell|pwsh|pwsh7|ps7)\s*:\s?(.*)$", re.IGNORECASE)


def classify_code(text: str) -> tuple[str, str, str]:
    """把功能代码归一成 (kind, payload, err)。

    kind: "py" | "cmd" | "ps"；err 非空表示未通过校验、不应执行。
    无标记默认为 py；cmd 多行会合并为一行（换行→" & "，cmd 的链接方式）。
    """
    body = (text or "").strip()
    if not body:
        return "py", "", "没有代码"

    kind = "py"
    lines = body.splitlines()
    m = _KIND_RE.match(lines[0])
    if m:
        tag = m.group(1).lower()
        kind = ("pwsh" if tag in ("pwsh", "pwsh7", "ps7")
                else "cmd" if tag == "cmd"
                else "ps" if tag in ("ps", "powershell")
                else "py")
        rest = [m.group(2)] + lines[1:] if m.group(2) or len(lines) > 1 else []
        body = "\n".join(rest).strip()
        if not body:
            return kind, "", f"{kind} 代码为空"

    if kind == "py":
        try:
            ast.parse(body)
        except SyntaxError as exc:
            return kind, "", f"Python 语法错误（第 {exc.lineno} 行）：{exc.msg}"
    return kind, body, ""


# ---------------------------------------------------------------- 次级动态扇形：成对特殊标记解析
_SPECIAL_MARK_RE = re.compile(r"^\s*【{3}(.+?)】{3}\s*$")


def parse_special_sections(text: str) -> dict:
    """解析指令中的「次级动态扇形」成对特殊标记。

    语义（成对标记，示例）：
      cmd: echo 123456          ← 第一个标记行之前 = 主指令（左键执行，可空）
      【【【子代码1】】】
      cmd: echo 234567
      【【【子代码1】】】         ← 同一对标记之间的代码 = 次级项「子代码1」的指令
      【【【子代码2】】】
      cmd: echo 345678
      【【【子代码2】】】

    规则：
      · 标记行 = 整行 `【【【标题】】】`（标题经 clamp_name 截断，空标题兜底「未命名」）；
      · 同名标记按「开→闭」顺序配对（最近同名配对；不支持嵌套），配对区间内的
        代码 = 该次级项指令；多个标记对按出现顺序生成多个次级项，独立生效；
      · 第一个标记行之前的所有代码 = 主指令；无任何标记时全文即主指令（向后兼容）；
      · 标记必须成对闭合，否则 err 非空（保存/测试校验会拦截）；
      · 次级项最多取前 MAX_SECTORS 个，超出截断。

    返回 {"main": 主指令文本, "total": 全部配对对数(未截断), "items": [{"name","code"},...],
          "err": 校验错误或空串}
    """
    body = text or ""
    if not body.strip():
        return {"main": "", "total": 0, "items": [], "err": ""}
    lines = body.splitlines()
    marks: list[int] = []       # 标记行的行索引
    for idx, ln in enumerate(lines):
        if _SPECIAL_MARK_RE.match(ln):
            marks.append(idx)
    if not marks:
        return {"main": body.strip(), "total": 0, "items": [], "err": ""}

    # 栈式同名配对：栈顶同名 → 视为闭合弹出配对；否则压栈（首个标记必然是「开」）
    stack: list[tuple[int, str]] = []            # (行索引, 标题)
    pairs: list[tuple[int, int, str]] = []       # (开行, 闭行, 标题)
    err = ""
    for idx in marks:
        tag = _SPECIAL_MARK_RE.match(lines[idx]).group(1).strip()
        if stack and stack[-1][1] == tag:
            open_idx, _ = stack.pop()
            pairs.append((open_idx, idx, tag))
        elif any(s[1] == tag for s in stack):
            if not err:
                err = f"特殊标记「{tag}」配对顺序异常（不支持嵌套）"
            stack.append((idx, tag))   # 继续尽力配对，错误以 err 拦下
        else:
            stack.append((idx, tag))
    if stack:
        names = "、".join(sorted({s[1] for s in stack}))
        suffix = f"；且「{names}」配对顺序异常" if err else ""
        err = err or f"特殊标记「{names}」未成对闭合{suffix}"

    # 主指令 = 第一个标记行之前全部行（保持原换行/缩进）
    main = "\n".join(lines[:marks[0]]).strip()
    # 次级项 = 各配对区间内的代码，按出现顺序（先出现的在前）
    pairs.sort(key=lambda p: p[0])
    total = len(pairs)              # 全部成对标记数量（子功能块；不截断，供数量校验）
    items = []
    for o, c, tag in pairs:
        code = "\n".join(lines[o + 1:c]).strip()
        items.append({"name": clamp_name(tag, CONFIG["label_max_units"]) or "未命名",
                      "code": code})
    if len(items) > MAX_SECTORS:
        items = items[:MAX_SECTORS]
    return {"main": main, "total": total, "items": items, "err": err}


def validate_sub_items_count(total: int, limit_min: int = MIN_SECTORS,
                             limit_max: int = MAX_SECTORS) -> str:
    """校验子功能块（成对特殊标记）数量是否在 [limit_min, limit_max]。

    返回错误信息；合法返回空串。用于保存校验：含特殊标记的指令，子功能块
    数量必须 ≥2 且 ≤9（与主环形扇区上限一致）。
    """
    if limit_min <= total <= limit_max:
        return ""
    if total < limit_min:
        return f"子功能块数量不足：当前 {total} 个，最少 {limit_min} 个"
    return f"子功能块数量过多：当前 {total} 个，最多 {limit_max} 个"


def _log_crash(where: str, exc: BaseException) -> None:
    """拦截回调异常并写入 crash.log。

    PySide6 6.x 对槽函数/虚函数里的未捕获异常会直接终止进程——上次点保存后
    程序整体消失正是这类问题。所有我们自己的回调一律兜住，程序必须活着。
    """
    import traceback
    msg = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    print(f"[异常拦截] {where}:\n{msg}", file=sys.stderr)
    try:
        with open(os.path.join(BASE_DIR, "crash.log"), "a", encoding="utf-8") as fh:
            fh.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {where} ===\n{msg}")
    except OSError:
        pass


def _spawn(argv: list[str]) -> subprocess.Popen:
    return subprocess.Popen(argv, cwd=BASE_DIR, creationflags=CREATE_NO_WINDOW)


def run_code(text: str) -> str | None:
    """静默执行功能代码。返回 None=已启动；返回字符串=未执行的错误说明。

    一律 fire-and-forget（不等待、不阻塞 UI）：
      py  → 写临时脚本，交给当前解释器执行（语法错误不执行；运行期崩溃不波及主程序）
      cmd → 写临时 .bat，交 cmd.exe /c 执行（引号原样保留，避免 start "" "路径" 被破坏）
      ps  → PowerShell 5.1：-EncodedCommand 传 UTF-16LE Base64（彻底规避引号转义）
    三种都带 CREATE_NO_WINDOW，控制台程序也不会闪出黑窗。
    """
    kind, payload, err = classify_code(text)
    if err:
        return err
    import base64

    if kind == "cmd":
        # 关键修复：不能把含引号的命令串整体作为参数传给 cmd.exe /c——
        # subprocess.list2cmdline 会把内部 " 转义成 \"，cmd 对 \" 的处理与 C 规则
        # 不同，start "" "path" 会被拆错，报「Windows 找不到 '\' 文件」。
        # 改成写 .bat：cmd 按批处理规则解析文件内容，引号原样保留。
        os.makedirs(RUNNER_DIR, exist_ok=True)
        bat = os.path.join(RUNNER_DIR, f"fn_{os.getpid()}_{int(time.time() * 1000)}.bat")
        # cmd 批处理默认按系统 ANSI 代码页(GBK)读取，中文路径用 gbk 写出
        try:
            with open(bat, "w", encoding="gbk") as fh:
                fh.write("@echo off\r\n")
                for ln in payload.splitlines():
                    if ln.strip():
                        fh.write(ln.rstrip() + "\r\n")
            proc = _spawn(["cmd.exe", "/c", bat])
        except UnicodeEncodeError:
            # gbk 无法覆盖的字符（生僻字/emoji）兜底退化为旧通道
            one_line = " & ".join(ln.strip() for ln in payload.splitlines() if ln.strip())
            proc = _spawn(["cmd.exe", "/c", one_line])
            _log_crash("cmd_unicode_fallback",
                       RuntimeError(f"含 gbk 外字符，退回直接执行：{one_line}"))
        SESSION_RUNNERS.append((proc, bat))
        return None
    if kind == "ps":
        # -EncodedCommand：UTF-16LE Base64，不含引号/特殊字符，绝无转义破坏问题
        enc = base64.b64encode(payload.encode("utf-16-le")).decode("ascii")
        _spawn(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-EncodedCommand", enc])
        return None
    if kind == "pwsh":
        # 目标机装有 PowerShell 7（pwsh.exe）才走本条；没有则回退 Windows PowerShell 5.1
        enc = base64.b64encode(payload.encode("utf-16-le")).decode("ascii")
        if shutil.which("pwsh"):
            _spawn(["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-EncodedCommand", enc])
            return None
        print("[执行] 本机未安装 PowerShell 7（pwsh），已回退用 5.1 执行", file=sys.stderr)
        _spawn(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-EncodedCommand", enc])
        return None

    os.makedirs(RUNNER_DIR, exist_ok=True)
    fname = os.path.join(RUNNER_DIR, f"fn_{os.getpid()}_{int(time.time() * 1000)}.py")
    with open(fname, "w", encoding="utf-8") as fh:
        fh.write(payload)
    proc = _spawn([sys.executable, fname])
    SESSION_RUNNERS.append((proc, fname))
    return None


# 本会话派发的一次性 py 脚本 [(进程, 路径)]；退出时只删已结束的，绝不等待。
SESSION_RUNNERS: list[tuple[subprocess.Popen, str]] = []


def cleanup_runner_dir() -> None:
    """删除 24 小时前的残留临时脚本；本会话新写入的随退出删除。"""
    try:
        cutoff = time.time() - 24 * 3600
        for name in os.listdir(RUNNER_DIR):
            path = os.path.join(RUNNER_DIR, name)
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------- 指令测试执行（指令编辑窗「测试」用）
# 与 run_code 同一执行语义（cmd→GBK .bat；ps/pwsh→-EncodedCommand；py→临时脚本；
# cwd=BASE_DIR、CREATE_NO_WINDOW 静默），但保留子进程对象供编辑窗轮询收集输出/退出码，
# 让「测试」如实复现扇区点击行为并把结果展示给用户。正式执行（扇区点击）仍走 run_code。
TEST_RUN_TIMEOUT_SEC = 15.0   # 测试等待超时（秒）：超时后停止收集，进程留在后台自行结束


def build_code_test(text: str):
    """为「测试」按钮校验并构造执行命令。

    返回 (kind, payload, argv, cleanup, err)：
      · err 非空 = 未通过校验（与 run_code 同一标准），调用方不得执行；
      · argv   = 要执行的真实命令行（测试与扇区点击使用同一解释器/参数）；
      · cleanup = 进程结束后应删除的临时文件路径列表（可能为空；py 脚本 / cmd bat）。
    """
    import base64
    # 含【【【…】】】成对特殊标记（次级扇形指令）：测试仅执行「主指令」（标记前），
    # 标记块与次级项代码一律剥除——避免标记行混入代码造成语法报错。
    parsed = parse_special_sections(text)
    if parsed["err"]:
        return "py", "", None, [], parsed["err"]
    if parsed["items"]:
        if not parsed["main"].strip():
            return ("py", "", None, [],
                    "该指令仅含次级功能（特殊标记），没有可测试的主指令；"
                    "请先填写主指令或改用「保存」校验格式。")
        text = parsed["main"]
    kind, payload, err = classify_code(text)
    if err:
        return kind, payload, None, [], err
    cleanup: list[str] = []
    if kind == "cmd":
        # 同 run_code：写 GBK .bat 交 cmd /c（引号原样保留）；GBK 外字符兜底退化为 & 连接
        os.makedirs(RUNNER_DIR, exist_ok=True)
        bat = os.path.join(RUNNER_DIR,
                           f"tst_{os.getpid()}_{int(time.time() * 1000)}.bat")
        try:
            with open(bat, "w", encoding="gbk") as fh:
                fh.write("@echo off\r\n")
                for ln in payload.splitlines():
                    if ln.strip():
                        fh.write(ln.rstrip() + "\r\n")
            argv = ["cmd.exe", "/c", bat]
        except UnicodeEncodeError:
            one_line = " & ".join(ln.strip() for ln in payload.splitlines()
                                  if ln.strip())
            argv = ["cmd.exe", "/c", one_line]
        cleanup = [bat]
    elif kind == "ps":
        enc = base64.b64encode(payload.encode("utf-16-le")).decode("ascii")
        argv = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-EncodedCommand", enc]
    elif kind == "pwsh":
        enc = base64.b64encode(payload.encode("utf-16-le")).decode("ascii")
        if shutil.which("pwsh"):
            argv = ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-EncodedCommand", enc]
        else:
            argv = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-EncodedCommand", enc]
    else:   # py（含无前缀默认）
        os.makedirs(RUNNER_DIR, exist_ok=True)
        fname = os.path.join(RUNNER_DIR,
                             f"tst_{os.getpid()}_{int(time.time() * 1000)}.py")
        with open(fname, "w", encoding="utf-8") as fh:
            fh.write(payload)
        argv = [sys.executable, fname]
        cleanup = [fname]
    return kind, payload, argv, cleanup, ""


def _test_spawn(argv: list[str]) -> subprocess.Popen:
    """以测试模式启动子进程：静默 + 捕获 stdout/stderr（不阻塞 UI，返回后可轮询）。"""
    return subprocess.Popen(argv, cwd=BASE_DIR, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW)


# ---------------------------------------------------------------- 配置读写
SETTINGS_HEADER = (
    "# Aura Pixie 设置（本文件位于 %LOCALAPPDATA%\\Aura_Pixie\\setting.yaml，"
    "由设置/动效界面写回）\n"
    "# 分块：functions = 功能扇区列表；aura = 光效（中间彩色+倍率+透明度）；"
    "autostart = 开机自启动；\n"
    "#       window = 窗口行为（always_on_top = 保持球体置顶；pos_x/pos_y = 球心记忆位置）。\n"
    "# 扇区等分数 = functions 中 active: true 的条目数；最少 2，最多 9（超出只取前 9）；"
    "扇区顺序 = 条目顺序。\n"
    "# code 格式：默认 py 代码；以 cmd: 开头走 CMD，以 ps:/powershell: 开头走 PowerShell 5.1，"
    "以 pwsh: 开头走 PowerShell 7，均静默执行。\n"
)


def read_setting(path: str = SETTINGS_PATH) -> dict:
    """读取指定 yaml 配置文件的全部内容（dict）。文件缺失 / 损坏时返回空 dict，不给主程序添乱。

    迁移（从根目录迁移源复制到 %LOCALAPPDATA%）只发生在启动时的
    ensure_settings 一次性流程中；本函数不做任何兜底读取。
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001
        print(f"[配置] 读取失败：{exc}", file=sys.stderr)
        return {}


def _yaml_blocks_legal(src) -> bool:
    """判断一个解析后的 yaml 内容的功能块是否合法（迁移/导入校验用）。

    不按文件名区分来源：只要包含至少一个「程序能识别的功能块」即视为合法。
    合法块：
      · functions：列表，元素为 dict（含 name/code/active 任一字段）
      · aura：dict
      · autostart：dict 或 bool
      · window：dict
    旧版 functions.yaml / aura.yaml 不再做专属处理，与普通 *.yaml 一视同仁，
    内容含上述任一功能块即视为合法；完全无法识别的内容返回 False。
    """
    if not isinstance(src, dict) or not src:
        return False
    funcs = src.get("functions")
    if isinstance(funcs, list) and any(isinstance(f, dict) for f in funcs):
        return True
    if isinstance(src.get("aura"), dict) or isinstance(src.get("autostart"), (dict, bool)) \
            or isinstance(src.get("window"), dict):
        return True
    return False


def _find_root_yamls(base: str | None = None) -> list[str]:
    """在指定目录（默认 exe 根目录）收集可迁移的配置文件（*.yaml / *.yaml.bak）。

    不按文件名判断（旧版 functions.yaml / aura.yaml 视为普通 yaml，不做专属
    处理）；仅检查内容的功能块是否合法（见 _yaml_blocks_legal）。返回按
    修改时间新→旧排序的合法文件路径列表；没有合法文件返回空列表。
    base 可注入（自检用隔离目录），None 时用 exe 所在目录。
    """
    root = base if base is not None else BASE_DIR
    if not os.path.isdir(root):
        return []
    cands: list[tuple[float, str]] = []
    try:
        for name in os.listdir(root):
            low = name.lower()
            if not (low.endswith(".yaml") or low.endswith(".yaml.bak")):
                continue
            p = os.path.join(root, name)
            try:
                with open(p, encoding="utf-8") as fh:
                    src = yaml.safe_load(fh) or {}
                if _yaml_blocks_legal(src):
                    cands.append((os.path.getmtime(p), p))
            except Exception:   # noqa: BLE001 - 解析失败视为不合法，跳过
                pass
    except OSError:
        return []
    cands.sort(key=lambda item: item[0], reverse=True)
    return [p for _t, p in cands]


def write_setting(data: dict, path: str = SETTINGS_PATH) -> None:
    """全量写回 setting.yaml（保留注释头；调用方负责构造完整 dict）。

    目标目录可能尚不存在（%LOCALAPPDATA%\\Aura_Pixie），先创建再写。
    """
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as exc:
            _log_crash("mkdir_settings", exc)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(SETTINGS_HEADER)
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False,
                       default_flow_style=False, width=1000)


def default_settings() -> dict:
    """出厂默认配置块（首次运行 / 迁移合并时的缺省兜底）。"""
    return {
        "functions": [],
        "aura": {"color_mid": "#FFFFFF", "aura_scale": 1.0, "idle_sec": 60,
                 "opacity": 100, "sleep_opacity": 10},
        "autostart": {"enabled": False},
        "window": {"always_on_top": False, "pos_x": None, "pos_y": None},
    }


def _merge_config(base: dict, src: dict) -> dict:
    """把迁移源/导入源内容合并进 base（就地返回 base）。

    · functions：源为**非空列表**时整体替换（避免空列表清掉已有功能）
    · aura / window：dict 字段级合并（源字段覆盖 base 同名字段）
    · autostart：dict 字段级合并；裸 bool 转 {enabled: bool}
    """
    funcs = src.get("functions")
    if isinstance(funcs, list) and funcs:
        base["functions"] = funcs
    if isinstance(src.get("aura"), dict):
        base["aura"] = {**(base.get("aura") or {}), **src["aura"]}
    astart = src.get("autostart")
    if isinstance(astart, dict):
        base["autostart"] = {**(base.get("autostart") or {}), **astart}
    elif isinstance(astart, bool):
        base["autostart"] = {"enabled": astart}
    if isinstance(src.get("window"), dict):
        base["window"] = {**(base.get("window") or {}), **src["window"]}
    return base


def merge_settings_with_defaults(src: dict) -> dict:
    """把迁移源/导入源内容与出厂默认块合并：缺少的功能块补默认，已有块保留原值。

    迁移源可能是完整 setting.yaml，也可能是旧版 functions.yaml（只有
    functions 块）或 aura.yaml（只有 aura 块）——一律视为普通 yaml，
    按块合并后写入主位置，保证 functions/aura/autostart/window 齐全。
    """
    return _merge_config(default_settings(), src)


def ensure_settings(path: str = SETTINGS_PATH, base: str | None = None) -> dict:
    """保证配置文件存在并返回其内容（配置主位置 %LOCALAPPDATA%\\Aura_Pixie\\setting.yaml）。

    检测顺序：
      1) 主位置已有 setting.yaml → 直接读取载入，返回；
      2) 主位置缺失 → 创建 %LOCALAPPDATA%\\Aura_Pixie 目录；
      3) 扫描根目录（默认 exe 所在目录）*.yaml / *.yaml.bak——
         不按文件名区分（旧版 functions.yaml / aura.yaml 视为普通 yaml），
         只看内容功能块是否合法（_yaml_blocks_legal）。候选按修改时间
         新→旧，逐个与出厂默认块合并写主位置并载入，根源改名 *.yaml.bak_done；
      4) 根目录没有合法 yaml → 写出厂默认配置并返回。
    base 可注入扫描目录（自检隔离用）；None 时用 exe 所在目录。
    """
    # 1) 主位置已有 → 直接读取
    data = read_setting(path)
    if data:
        return data

    # 2) 主位置缺失 → 先创建目录
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
            print(f"[配置] 已创建配置目录：{parent}", file=sys.stderr)
        except OSError as exc:
            _log_crash("ensure_settings_mkdir", exc)

    # 3) 扫描根目录合法 yaml，按修改时间新→旧累积合并（后源覆盖同名字段，
    #    新文件优先），一次性写主位置；每个迁移源随之改名 *.yaml.bak_done
    sources = _find_root_yamls(base)
    if sources:
        merged = default_settings()
        for root_config in reversed(sources):   # 旧→新累积，最新文件最后写、获胜
            try:
                with open(root_config, encoding="utf-8") as fh:
                    src = yaml.safe_load(fh) or {}
                if isinstance(src, dict) and _yaml_blocks_legal(src):
                    merged = _merge_config(merged, src)
                    print(f"[配置] 已合并根目录配置：{root_config}", file=sys.stderr)
                    try:
                        done_path = root_config + ".bak_done"
                        os.rename(root_config, done_path)
                        print(f"[配置] 迁移源已归档为 {done_path}", file=sys.stderr)
                    except OSError as exc:
                        _log_crash("ensure_settings_archive", exc)
            except Exception as exc:  # noqa: BLE001
                _log_crash("ensure_settings_migrate", exc)
                print(f"[配置] 根目录配置迁移失败：{root_config}：{exc}", file=sys.stderr)
        try:
            write_setting(merged, path)
        except OSError as exc:
            _log_crash("ensure_settings_write", exc)
        return merged

    # 4) 根目录没有合法 yaml → 写默认
    defaults = default_settings()
    try:
        write_setting(defaults, path)
        print(f"[配置] 已生成出厂默认配置：{path}", file=sys.stderr)
    except OSError as exc:
        _log_crash("ensure_settings_defaults", exc)
    return defaults


def load_functions(path: str = CONFIG_PATH) -> list[dict]:
    """读取 setting.yaml → 扇区列表。顺序 = 文件条目顺序（设置界面可拖拽调整）。

    激活数决定等分数量：<2 补占位到 2，>9 截断到 9；代码为空的条目按占位灰色处理。
    """
    raw: list = []
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        raw = [f for f in (data.get("functions") or []) if isinstance(f, dict)]
    except FileNotFoundError:
        print(f"[配置] 未找到 {path}，使用占位扇区", file=sys.stderr)
    except Exception as exc:
        print(f"[配置] 解析失败，使用占位扇区：{exc}", file=sys.stderr)

    active = [f for f in raw if f.get("active")]
    if len(active) > MAX_SECTORS:
        print(f"[配置] 激活功能 {len(active)} 个超过 {MAX_SECTORS} 个上限，仅前 "
              f"{MAX_SECTORS} 个参与扇区", file=sys.stderr)
        active = active[:MAX_SECTORS]

    sectors = []
    n_sectors = max(MIN_SECTORS, len(active))
    for i in range(n_sectors):
        item = active[i] if i < len(active) else None
        code = str(item.get("code") or item.get("command") or "") if item else ""
        name = str(item.get("name", "功能")) if item else f"空位 {i + 1}"
        sectors.append({
            "name": name,
            "code": code,
            # 没配功能或没写代码 → 灰色占位：看得见位置，但点击不执行
            "placeholder": item is None or not code.strip(),
        })
    return sectors


def save_functions(rows: list[dict], path: str = CONFIG_PATH) -> None:
    """写回功能扇区块（functions），保留 setting.yaml 中其他配置块（aura/autostart/未来扩展）。

    条目顺序 = 表格行顺序；总数不限，可含未激活。
    """
    data = read_setting(path)
    data["functions"] = [
        {"name": r["name"], "code": r["code"], "active": bool(r["active"])}
        for r in rows
    ]
    write_setting(data, path)


def read_all_rows(path: str = CONFIG_PATH) -> list[dict]:
    """设置界面用：读出全部功能条目（含未激活），保持顺序。"""
    data = read_setting(path)
    items = [f for f in (data.get("functions") or []) if isinstance(f, dict)]
    return [{"name": str(i.get("name", "")),
             "code": str(i.get("code") or i.get("command") or ""),
             "active": bool(i.get("active"))} for i in items]


# ---------------------------------------------------------------- 开机自启动
# 写当前用户的注册表启动项（HKCU\...\Run），无需管理员权限；
# 仅选中「开机自启动」复选框后写入，默认不写入、不自启动。
_RUN_KEY_NAME = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE_NAME = "AuraPixie"


def _is_packaged() -> bool:
    """当前进程是否为打包分发形态（exe）。源码调试形态（python main.py）返回 False。"""
    if getattr(sys, "frozen", False):
        return True
    return app_executable().lower().endswith(".exe")


def _quiet_interpreter() -> str:
    """无控制台窗口的 Python 解释器：pythonw.exe（与 python.exe 同目录，GUI 子系统
    不开终端框）；缺失时回退 python.exe（仅兜底，开机自启会弹终端框）。"""
    py = sys.executable
    w = os.path.join(os.path.dirname(py), "pythonw.exe")
    return w if os.path.isfile(w) else py


def _run_value() -> str:
    """要写入注册表启动项的命令行：
    打包 exe（onefile/standalone）→ 真实分发 exe 路径；
    源码运行 → pythonw.exe（无控制台窗口，开机自启不弹终端）+ 本脚本。"""
    if _is_packaged():
        return f'"{app_executable()}"'
    return f'"{_quiet_interpreter()}" "{os.path.abspath(__file__)}"'


def autostart_enabled() -> bool:
    """当前是否真实自启动（以注册表 Run 键实况为准）。"""
    return _registry_autostart()


def autostart_state() -> dict:
    """开机自启动状态（含 setting.yaml 的 autostart 记录 + 注册表实况）。

    兼容旧格式：autostart 块在旧版本是裸布尔（false / true），新版本为
    {enabled: bool}，统一按注册表实况判定。
    """
    data = read_setting()
    astart = data.get("autostart")
    if isinstance(astart, dict):
        saved = bool(astart.get("enabled", False))
    else:
        saved = bool(astart) if astart is not None else False
    return {
        "enabled": saved,
        "registry": _registry_autostart(),
        "registry_value": _registry_value(),
    }


def _registry_autostart() -> bool:
    """注册表 Run 键中是否真实存在 AuraPixie 项。"""
    return _registry_value() is not None


def _registry_value() -> str | None:
    """读取注册表 Run 键中 AuraPixie 项的当前值；不存在/读取失败返回 None。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY_NAME) as key:
            val, _ = winreg.QueryValueEx(key, _RUN_VALUE_NAME)
            return str(val)
    except FileNotFoundError:
        return None
    except OSError:
        return None


def autostart_self_heal() -> None:
    """开机自启动自我保护：每次程序运行检查一次「配置期望」与「注册表实况」是否一致。

    只做修复、绝不误删：
      · 配置为启用（enabled: true）→ 注册表缺失或被改写（清理/安全软件误删、
        exe 挪位后旧路径失效）→ 重建/修正为当前程序的正确命令行；
      · 配置为未启用 → 不动注册表（尊重用户在该处的其他手动操作）。
    全部异常兜底写 crash.log，不打断启动。

    重要：仅「打包形态」（exe）运行参与注册表修正。源码调试形态（python main.py）
    绝不改写注册表——否则每次开发运行都会把用户正式的自启动项改成开发命令
    （python.exe 弹终端框），正是 2026-10-28 开机弹终端的根因。
    """
    if not _is_packaged():
        return
    try:
        state = autostart_state()
        if not state["enabled"]:
            return
        expected = _run_value()
        current = state["registry_value"]
        if current == expected:
            return
        access = winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY_NAME, 0, access) as key:
            winreg.SetValueEx(key, _RUN_VALUE_NAME, 0, winreg.REG_SZ, expected)
        print(f"[自启动] 已自动修正开机自启动项（{current or '缺失'} → {expected}）",
              file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        _log_crash("autostart_self_heal", exc)


def set_autostart(enabled: bool) -> None:
    """开启则写入注册表启动项并在 setting.yaml 的 autostart 块记录；关闭则删除两处。"""
    access = winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY_NAME, 0, access) as key:
        if enabled:
            winreg.SetValueEx(key, _RUN_VALUE_NAME, 0, winreg.REG_SZ, _run_value())
        else:
            try:
                winreg.DeleteValue(key, _RUN_VALUE_NAME)
            except FileNotFoundError:
                pass  # 本来就未写入，无需删除
    # 同步记录到 setting.yaml（保留其他配置块）
    data = read_setting()
    data["autostart"] = {"enabled": bool(enabled)}
    try:
        write_setting(data)
    except OSError as exc:
        _log_crash("autostart_setting_save", exc)


# ---------------------------------------------------------------- 窗口置顶
def topmost_enabled(path: str = SETTINGS_PATH) -> bool:
    """当前是否保持球体置顶（以 setting.yaml 的 window.always_on_top 为准，默认 false）。"""
    data = read_setting(path)
    w = data.get("window")
    if isinstance(w, dict):
        return bool(w.get("always_on_top", False))
    return False


def set_topmost(enabled: bool, path: str = SETTINGS_PATH) -> None:
    """写入/清除 setting.yaml 的 window.always_on_top；不影响其他配置块。"""
    data = read_setting(path)
    w = data.get("window")
    if not isinstance(w, dict):
        w = {}
    w["always_on_top"] = bool(enabled)
    data["window"] = w
    try:
        write_setting(data, path)
    except OSError as exc:
        _log_crash("topmost_setting_save", exc)


# ---------------------------------------------------------------- 球心位置记忆
def saved_ball_position(path: str = SETTINGS_PATH) -> tuple[float, float] | None:
    """读取设置中记录的上次球心坐标（window.pos_x / pos_y）。

    两者均为合法数值才返回 (x, y)；缺失/非法/越界返回 None（表示无记录，
    调用方应使用默认位置）。"""
    data = read_setting(path)
    w = data.get("window")
    if not isinstance(w, dict):
        return None
    try:
        x = float(w.get("pos_x"))
        y = float(w.get("pos_y"))
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    return x, y


def save_ball_position(x: float, y: float, path: str = SETTINGS_PATH) -> None:
    """把球心坐标写入 setting.yaml 的 window.pos_x / pos_y（保留其他配置块）。

    非法数值不落盘（守护：坐标来源异常时不污染配置）。"""
    if not (math.isfinite(x) and math.isfinite(y)):
        return
    try:
        data = read_setting(path)
        w = data.get("window")
        if not isinstance(w, dict):
            w = {}
        w["pos_x"] = round(float(x), 1)
        w["pos_y"] = round(float(y), 1)
        data["window"] = w
        write_setting(data, path)
    except Exception as exc:  # noqa: BLE001 - 位置保存失败不打断退出
        _log_crash("save_ball_position", exc)


# ---------------------------------------------------------------- 扇区几何
def _angular_dev(a: float, b: float) -> float:
    """两角（度）的最短夹角差，归一化到 [0, 180]。"""
    d = (a - b) % 360.0
    return min(d, 360.0 - d)


def sector_index_at(dx: float, dy: float, n: int) -> int:
    """鼠标相对球心的偏移 → 扇区编号。0 号中心指正上方，顺时针。

    用「距扇区中心角最近」判定：旧实现 `(90-deg)%360 // step` 在顶部偏左
    这一处环绕点有劈切 bug——`%360` 把角度绕成 359° 再整除，会把整个
    「顶部左侧」区域切给相反方向的扇区；等分越少该错误区占环比例越大
    （2 等分错 25% 且跳到对面扇区，9 等分只占 1/9 不太显眼），
    这正是「少扇区高亮混乱、9 扇区准确」的成因。n≤9，逐一比中心最稳。
    """
    if n <= 0:
        return -1
    a = math.degrees(math.atan2(-dy, dx)) % 360.0
    step = 360.0 / n
    best_i, best_d = 0, 1e9
    for i in range(n):
        dev = _angular_dev(a, 90.0 - i * step)
        if dev < best_d:
            best_i, best_d = i, dev
    return best_i


def sector_center_deg(i: int, n: int) -> float:
    return 90.0 - i * (360.0 / n)


def polar_point(cx: float, cy: float, r: float, deg: float) -> QPointF:
    rad = math.radians(deg)
    return QPointF(cx + r * math.cos(rad), cy - r * math.sin(rad))


# ---------------------------------------------------------------- 绘制
def _scaled_alpha(color: QColor, alpha: int) -> QColor:
    """乘总体透明度系数后的颜色：alpha = 原始 α × opacity/100。"""
    pct = float(CONFIG.get("opacity", 100))
    pct = max(10.0, min(100.0, pct)) / 100.0
    return with_alpha(color, round(alpha * pct))


def _scaled_alpha_ring(alpha: float) -> float:
    """扇形/占位填充 alpha 乘总体透明度系数（输入常为 float，保留浮点）。"""
    pct = float(CONFIG.get("opacity", 100))
    pct = max(10.0, min(100.0, pct)) / 100.0
    return alpha * pct


def paint_ball(painter: QPainter, size: int, phase: float,
               override_color: QColor | None = None) -> None:
    """绘制球体。override_color 非空时整球用该底色（休眠顺色渲染用），
    呼吸相位仅决定尺寸；None 时走正常的呼吸三段色。

    总体透明度（CONFIG.opacity %）对光晕/本体/高光三层的 alpha 统一缩放。
    """
    diameter, radius, color = shape_at(phase)
    if override_color is not None:
        color = override_color
    center = QPointF(size / 2.0, size / 2.0)

    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)

    halo_r = radius + HALO_PX * 2.0
    edge = radius * 0.62 / halo_r
    halo = QRadialGradient(center, halo_r)
    halo.setColorAt(0.0, _scaled_alpha(color, CONFIG["glow_alpha"]))
    halo.setColorAt(edge, _scaled_alpha(color, round(CONFIG["glow_alpha"] * 0.75)))
    halo.setColorAt(1.0, _scaled_alpha(color, 0))
    painter.setBrush(halo)
    painter.drawEllipse(center, halo_r, halo_r)

    # 球体本体：中心本色 → 边缘 alpha 退晕（0.55 半径起缓慢渐隐，过渡拉长更柔和），
    # 与下层光晕叠加成连续虚化过渡；无任何深色收边/阴影分层。
    body = QRadialGradient(center, radius)
    body.setColorAt(0.00, _scaled_alpha(color, 255))
    body.setColorAt(0.55, _scaled_alpha(color, 255))
    body.setColorAt(0.75, _scaled_alpha(color, 220))
    body.setColorAt(0.88, _scaled_alpha(color, 130))
    body.setColorAt(0.97, _scaled_alpha(color, 45))
    body.setColorAt(1.00, _scaled_alpha(color, 0))
    painter.setBrush(body)
    painter.drawEllipse(center, radius, radius)

    spec_r = radius * 0.62
    spec_c = QPointF(center.x() - radius * 0.14, center.y() - radius * 0.40)
    spec = QRadialGradient(spec_c, spec_r)
    spec.setColorAt(0.00, _scaled_alpha(_WHITE, 120))
    spec.setColorAt(0.45, _scaled_alpha(_WHITE, 46))
    spec.setColorAt(1.00, _scaled_alpha(_WHITE, 0))
    painter.setBrush(spec)
    painter.drawEllipse(spec_c, spec_r, spec_r)

    # 无描边：球体边缘直接由光晕 + 本体径向渐变虚化过渡，不画轮廓圈


def paint_ring(painter: QPainter, size: int, sectors: list[dict], progress: float,
               highlight: int, fill_color: QColor, flash: dict | None = None) -> None:
    """扇形外框。填充 = 冻结的触发瞬间本色；flash = 点击后的闪光覆盖层。"""
    if progress <= 0.0 or not sectors:
        return
    p = ease_out_cubic(min(1.0, progress))
    n = len(sectors)
    cx = cy = size / 2.0
    r_out = RING_IN + (RING_OUT - RING_IN) * p
    step = 360.0 / n
    gap = CONFIG["ring_sector_gap_deg"]

    outer = QRectF(cx - r_out, cy - r_out, 2 * r_out, 2 * r_out)
    inner = QRectF(cx - RING_IN, cy - RING_IN, 2 * RING_IN, 2 * RING_IN)
    text_color = choose_text_color(fill_color)

    painter.setRenderHint(QPainter.Antialiasing)
    painter.setFont(QFont("Microsoft YaHei UI", 8))
    for i, sec in enumerate(sectors):
        start = sector_center_deg(i, n) - (step - gap) / 2.0
        span = step - gap

        path = QPainterPath()
        path.arcMoveTo(outer, start)
        path.arcTo(outer, start, span)
        path.arcTo(inner, start + span, -span)
        path.closeSubpath()

        if sec["placeholder"]:
            fill = with_alpha(PLACEHOLDER_COLOR, round(_scaled_alpha_ring(
                CONFIG["sector_alpha_dim"] * p)))
        elif flash and flash["sector"] == i:
            # 闪光覆盖层：从近实色亮面淡出。成功=本色掺白提亮，失败=红色
            t = flash["ms"] / flash["dur"]
            flash_color = mix_color(fill_color, _WHITE, 0.75) if flash["ok"] else _FAIL_RED
            fill = with_alpha(flash_color, round(_scaled_alpha_ring(255 * (1.0 - t) + 150)))
        elif i == highlight:
            fill = with_alpha(mix_color(fill_color, _WHITE, 0.30),
                              round(_scaled_alpha_ring(CONFIG["sector_alpha_hot"] * p)))
        else:
            fill = with_alpha(fill_color, round(_scaled_alpha_ring(CONFIG["sector_alpha"] * p)))

        painter.setBrush(fill)
        painter.setPen(QColor(255, 255, 255, round(120 * p)))
        painter.drawPath(path)

        if p > 0.75:
            # 项目名沿扇区中心圆环弧向排布；淡入期间用透明度控制，颜色已随填充明度选定
            fade = min(1.0, (p - 0.75) / 0.25)
            label = clamp_name(sec["name"], CONFIG["label_max_units"])
            if sec["placeholder"]:
                pen = with_alpha(PLACEHOLDER_COLOR, round(210 * fade))
            else:
                pen = with_alpha(text_color, round(255 * fade))
            paint_arc_label(painter, cx, cy, (RING_IN + r_out) / 2.0,
                            sector_center_deg(i, n), label,
                            CONFIG["label_font_px"], pen,
                            CONFIG["label_spacing_em"], step - gap - 4.0)


def label_rotation_deg(a_deg: float, top_outward: bool) -> float:
    """落点数学角 a（度；屏幕 y 向下，该点朝外径向视觉向量 = (cos a, −sin a)）→ rotate 角。

    painter.rotate(θ) 把本地 (x,y) 映到设备 (x·cosθ − y·sinθ, x·sinθ + y·cosθ)。
    字「头顶」是本地 (0,−1)，映射后 = (sinθ, −cosθ)：
      朝外：要等于朝外径向 (cos a, −sin a) → θ = 90 − a
      朝球心：差 180° → θ = −90 − a
    两种都只是旋转（不含反射），字的竖轴恒沿半径穿过球心、基线恒与环相切。
    """
    return (90.0 - a_deg) if top_outward else (-90.0 - a_deg)


def label_top_outward(center_deg: float, mode: str) -> bool:
    """该扇区的字顶是否朝外。

    auto：上半环（sin a > 0）字顶朝外、下半环朝球心——这样环上任何位置
    文字都是正读，不会出现顶部或底部整串倒 180°。
    outward / center：整环固定朝外（徽章式）或固定朝球心。
    """
    if mode == "outward":
        return True
    if mode == "center":
        return False
    return math.sin(math.radians(center_deg)) >= 0.0


def label_font(font_px: float) -> QFont:
    """扇区名统一用字体：粗体 + 关闭 hinting。

    关闭 hinting 是居中的关键一环：默认 hinting 会把基线吸附到整数像素网格
    （13px 时 33.5 → 33），整串因此固定偏上约 1px，看上去就是「没对准弧心」。
    """
    f = QFont("Microsoft YaHei UI")
    f.setPixelSize(int(round(font_px)))
    f.setBold(True)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def arc_label_layout(cx: float, cy: float, r_mid: float, center_deg: float,
                     text: str, font_px: float, spacing_em: float,
                     max_span_deg: float, mode: str) -> tuple[list[dict], float]:
    """算出每个字的落点，返回 (placements, 实际字号)。

    placement = {ch, x, y, rot, ink_x, ink_y}：
      (x, y) 是中心圆环上的弧心落点；rot 是该处旋转角；(ink_x, ink_y) 是该字
      墨迹中心相对「基线左端」的偏移。绘制按 translate(x,y) → rotate(rot)
      → translate(-ink_x,-ink_y) → drawText(0,0)，字的墨迹中心就精确落在弧心上。
    """
    if not text:
        return [], font_px

    def measure(fp: float) -> tuple[list[float], float]:
        m = QFontMetricsF(label_font(fp))
        adv = [m.horizontalAdvance(ch) + fp * spacing_em for ch in text]
        return adv, sum(adv)

    adv, total = measure(font_px)
    span = total / max(r_mid, 1.0)
    max_span = math.radians(max_span_deg)
    if span > max_span:                      # 超界：等比缩字号到刚好放下
        font_px = max(8.0, font_px * max_span / span)
        adv, total = measure(font_px)

    fm = QFontMetricsF(label_font(font_px))
    base = math.radians(center_deg)
    top_out = label_top_outward(center_deg, mode)
    step_sign = -1.0 if top_out else 1.0     # 与旋转角配对，保证读序不被镜像

    placements: list[dict] = []
    cursor = 0.0
    for ch, w in zip(text, adv):
        s_center = cursor + w / 2.0          # 该字中心在整串弧长中的位置
        cursor += w
        off = (s_center - total / 2.0) / max(r_mid, 1.0)
        a_rad = base + step_sign * off
        a_deg = math.degrees(a_rad)

        # 「字符中心」= 真实墨迹中心。不能用 boundingRect：它把 descent 空白也算进去
        # （13px 粗体 y=-13..+3 高 16，中文墨迹只 -12..+1），按它居中会整体偏上约 1px。
        # 用浮点版 tightBoundingRect：整数版会把中心量化到 0.5px 格子上。
        ink = fm.tightBoundingRect(ch)
        if ink.width() <= 0 or ink.height() <= 0:
            ink = fm.boundingRect(ch)
        if ink.width() <= 0 or ink.height() <= 0:
            ink_x = (w - font_px * spacing_em) / 2.0
            ink_y = (fm.descent() - fm.ascent()) / 2.0
        else:
            ink_x = ink.x() + ink.width() / 2.0
            ink_y = ink.y() + ink.height() / 2.0

        placements.append({
            "ch": ch,
            "x": cx + r_mid * math.cos(a_rad),
            "y": cy - r_mid * math.sin(a_rad),
            "rot": label_rotation_deg(a_deg, top_out),
            "ink_x": ink_x,
            "ink_y": ink_y,
        })
    return placements, font_px


def paint_arc_label(painter: QPainter, cx: float, cy: float, r_mid: float,
                    center_deg: float, text: str, font_px: float,
                    pen: QColor, spacing_em: float, max_span_deg: float) -> None:
    """项目名沿扇区中心圆环（环带中线）均匀排布，字符中心对准弧心。

    整串以扇区中心角为对称轴展开，字与字按实际字宽等弧长推进；张角不足时
    等比缩字号，保证文字不越进相邻扇区。朝向与读序推进方向由
    label_top_outward / arc_label_layout 成对决定，只做旋转不做反射。
    """
    if not text:
        return
    placements, used_px = arc_label_layout(cx, cy, r_mid, center_deg, text,
                                           font_px, spacing_em, max_span_deg,
                                           CONFIG["label_orientation"])
    painter.setFont(label_font(used_px))
    painter.setPen(pen)
    for pl in placements:
        painter.save()
        painter.translate(pl["x"], pl["y"])
        painter.rotate(pl["rot"])
        painter.translate(-pl["ink_x"], -pl["ink_y"])
        painter.drawText(QPointF(0, 0), pl["ch"])
        painter.restore()


# ---------------------------------------------------------------- 设置对话框
def drop_insert_slot(src: int, dst_raw: int, row_count: int) -> int:
    """把「鼠标指向的目标行」换算成 move_table_row 的插入下标。

    用户语义（需求）：
      · 指向第 1 行（dst_raw=0）→ 置于第 1 行，插入下标 0
      · 指向第 k 行 → 置于该行的「上一行」：即插入到 k 之前
      · 指向最后一行之外（dst_raw=-1）→ 置于末尾行，插入下标 = 移除源行前的行数-1
    转换为 move_table_row（插入到 dst 处，支持移除后行号前移）语义：
      · dst_raw > src：源行在目标行上方，移除源行后目标行前移 1 → 插入位 dst_raw-1
      · dst_raw <= src：直接插入到 dst_raw（此时在目标行之前）
    """
    if dst_raw < 0:
        return max(0, row_count - 1)
    if src < 0:
        src = 0
    return dst_raw if dst_raw <= src else dst_raw - 1


def move_table_row(table: QTableWidget, src: int, dst: int) -> None:
    """整行搬移到目标行位置（拖拽排序的落地函数，单独抽出便于测试）。

    QTableWidget 的 model() 包装器没有 takeRow，必须 takeItem 逐列摘走再回填：
    removeRow(src) → insertRow(dst) → setItem。统一「插入到目标行处」语义，
    向下拖落在目标行之后、向上拖落在目标行之前，双向自然。
    """
    if src < 0 or src >= table.rowCount() or dst < 0 or src == dst:
        return
    items = [table.takeItem(src, c) for c in range(table.columnCount())]
    table.removeRow(src)
    dst = min(dst, table.rowCount())
    table.insertRow(dst)
    for c, it in enumerate(items):
        if it is not None:
            table.setItem(dst, c, it)
    table.setCurrentCell(dst, 0)   # QTableWidget 没有 setCurrentRow，那是 QTreeView 的


class ReorderTable(QTableWidget):
    """支持整行实时让位拖拽排序的表格。

    交互：按住行拖动，被拖行随鼠标实时移动，其余行立即让位腾出空间；
    松开鼠标直接落入当前让出的位置（不再等 drop 才一次性搬移）。
    拖动期间只搬运行号，源行绝不由 Qt 自动清空（基类 startDrag 在
    InternalMove 下会 clearOrRemove 源行，必须覆盖掉）。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, 3, parent)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self._drag_src = -1   # 正在被拖的行号；拖动期间持续跟踪当前位置

    def startDrag(self, supportedActions) -> None:  # noqa: N802
        """自定义拖拽：进入模态拖拽循环，dragMoveEvent 里实时搬移让位。"""
        idx = self.currentIndex()
        if not idx.isValid():
            return
        self._drag_src = self.currentRow()
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData("application/x-qabstractitemmodeldatalist", QByteArray())
        drag.setMimeData(mime)
        drag.exec(supportedActions, Qt.DropAction.MoveAction)
        self._drag_src = -1   # 拖拽结束复位（行已在目标位，无需再搬）

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat("application/x-qabstractitemmodeldatalist"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def _live_move_to(self, pos_y: int) -> None:
        """按鼠标 y 坐标实时让位：把被拖行搬到该行让出的位置，其余行立即跟着移。

        rowAt 接收的是 viewport 内坐标（Qt6 拖拽事件 position 已相对 viewport，
        不可再 mapFrom(self) 二次转换，否则落点会整体偏一行）。
        """
        src = self._drag_src
        if src < 0:
            return
        dst_raw = self.rowAt(pos_y)
        dst = drop_insert_slot(src, dst_raw, self.rowCount())
        if dst != src:
            move_table_row(self, src, dst)
            self._drag_src = dst

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if not event.mimeData().hasFormat("application/x-qabstractitemmodeldatalist"):
            event.ignore()
            return
        self._live_move_to(round(event.position().y()))
        event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        # 行已在拖动过程中实时就位，这里只接受事件，不再搬移（避免二次搬移错位）
        if not event.mimeData().hasFormat("application/x-qabstractitemmodeldatalist"):
            return event.ignore()
        event.acceptProposedAction()

    def _column2_index_at_global(self, global_pos) -> "QModelIndex | None":
        """把全局坐标映射到 viewport 坐标并解析出命中的索引（右键定位用）。

        修复历史 bug：contextMenuEvent 传入的 event.pos() 在 Qt6 已是
        viewport 坐标，此前又做了一次 viewport().mapFrom(self) 二次换算，
        额外减去了表头高度，导致 indexAt 命中上一行（编辑最后一行必须在
        下方空白处右键的怪现象）。统一从 globalPos 映射，保证行定位准确。
        """
        pos = self.viewport().mapFromGlobal(global_pos)
        return self.indexAt(pos)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        """指令列（第 2 列）右键：弹出「高级编辑」入口，打开独立编辑窗。

        其余列/空白区域保持默认行为（不弹菜单）。编辑窗由设置窗唯一入口
        open_code_editor 打开（去重置前 + 先定位再 exec）；用 singleShot 推迟，
        避免在右键菜单栈内嵌套模态事件循环。
        """
        idx = self._column2_index_at_global(event.globalPos())
        if idx.isValid() and idx.column() == 2:
            menu = QMenu(self)
            act = menu.addAction("高级编辑…")
            act.setToolTip("在独立窗口编辑多行指令，可先「测试」运行再保存")
            chosen = menu.exec(event.globalPos())
            event.accept()
            if chosen is act:
                window = self.window()
                handler = window.open_code_editor if hasattr(
                    window, "open_code_editor") else None
                if handler is not None:
                    row = idx.row()
                    QTimer.singleShot(0, lambda r=row: handler(r))
            return
        super().contextMenuEvent(event)


class MultilineEditDelegate(QStyledItemDelegate):
    """指令列编辑器：QPlainTextEdit 多行输入，向下展开不遮住其他行。

    此前表格默认用单行编辑器，回车会结束编辑，多行指令根本没法输入——
    帮助里写的「多行」示例复制进单元格时只剩第一行。改用本委托后，
    指令单元格可以直接输入/粘贴多行内容（含换行）。
    """

    def createEditor(self, parent, option, index):  # noqa: N802
        if index.column() == 2:   # 指令列（多行代码）
            editor = QPlainTextEdit(parent)
            editor.setFont(QFont("Consolas", 9))
            editor.setTabChangesFocus(True)   # Tab 移出编辑器，不插入制表符
            return editor
        return super().createEditor(parent, option, index)

    def setEditorData(self, editor, index) -> None:  # noqa: N802
        if isinstance(editor, QPlainTextEdit):
            editor.setPlainText(str(index.data(Qt.ItemDataRole.DisplayRole) or ""))
        else:
            super().setEditorData(editor, index)

    def setModelData(self, editor, model, index) -> None:  # noqa: N802
        if isinstance(editor, QPlainTextEdit):
            model.setData(index, editor.toPlainText(), Qt.ItemDataRole.EditRole)
        else:
            super().setModelData(editor, model, index)

    def updateEditorGeometry(self, editor, option, index) -> None:  # noqa: N802
        if isinstance(editor, QPlainTextEdit):
            # 向下展开：从单元格顶部向下延伸，保证被编辑行的首行内容始终可见
            # （旧实现向上扩展，y 减去高出部分——编辑第 1 行时编辑器顶部越出
            #   视口顶部，第一行内容被裁剪不可见；现改为夹在视口范围内）
            lines = editor.document().lineCount()
            want_h = max(option.rect.height(), min(6, lines + 1) * 22 + 6)
            vp = editor.parentWidget()   # QAbstractItemView 的编辑器父对象 = viewport
            vp_h = vp.height() if vp is not None else want_h
            y = max(0, option.rect.y())              # 顶部不越出视口：首行必定可见
            h = min(want_h, vp_h - y)                # 底部不越出视口，超出回缩
            editor.setGeometry(option.rect.x(), y, option.rect.width(), h)
        else:
            super().updateEditorGeometry(editor, option, index)


# ---------------------------------------------------------------- 指令编辑窗（双击指令框弹出）
# 与行内编辑互补：大窗口编辑多行指令，支持「测试/保存/取消」。
# · 测试 = 真实执行（与点击扇区同一环境，可能启动程序/改文件/联网）→ 结果区展示输出与退出码
# · 保存 = 仅校验格式/语法（classify_code），非法弹窗「代码异常请查证」，不写回
# · 取消 = 不写回、直接退回设置界面
CODE_EDIT_WARN_TEXT = (
    "⚠ 测试将真实执行指令（与点击扇区完全一致），可能启动程序、修改文件或联网；"
    "运行前请确认指令安全无害")
CODE_EDIT_TEST_TOOLTIP = "真实执行当前指令（与点击扇区一致）。可能启动程序或产生实际效果，请谨慎。"


class CodeEditDialog(QDialog):
    """指令独立编辑窗。模态 exec：未保存/取消前设置窗不可操作（与动效窗同级交互）。

    编辑区内置隔离 tab 键（Tab 移出编辑区，不插入制表符）；「测试」在窗口内
    同步轮询子进程，不弹额外模态、不阻塞 UI；「保存」只做合法性校验不执行。
    """

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("指令编辑")
        self.resize(640, 480)
        self.setModal(True)
        # 异步测试状态：正在轮询的子进程与预删除文件（进程结束后清理），
        # 避免残留临时脚本；锁防止回调竞态后关闭窗口再访问表项。
        self._proc = None
        self._cleanup = []
        self._closed = False
        self.finished.connect(self._on_finished)

        # 上方编辑区：等宽、Tab 移出、深浅色适配（继承系统调色板，重影防护同设置窗）
        self.editor = QPlainTextEdit(self)
        self.editor.setFont(QFont("Consolas", 10))
        self.editor.setTabChangesFocus(True)
        self.editor.setPlainText(text or "")
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        # 测试警示行：醒目配色（深色/浅色模式均有对比），位于测试按钮正上方
        warn = QLabel(CODE_EDIT_WARN_TEXT, self)
        warn.setWordWrap(True)
        warn.setStyleSheet(
            "background-color:#3d1d00; color:#ffb36b; border:1px solid #ff8c42;"
            "border-radius:4px; padding:6px; font-weight:bold;")
        self.warn = warn

        # 结果区（测试输出）：只读，初始隐藏；测试后展开展示
        self.result = QPlainTextEdit(self)
        self.result.setFont(QFont("Consolas", 9))
        self.result.setReadOnly(True)
        self.result.setPlaceholderText("指令输出会显示在这里…")
        self.result.setMaximumHeight(140)
        self.result.hide()

        # 底部按钮行
        btn_test = QPushButton("测试", self)
        btn_test.setToolTip(CODE_EDIT_TEST_TOOLTIP)
        # 醒目警示配色：橙色边框 + 橙色文字 + 警告底色
        btn_test.setStyleSheet(
            "QPushButton { background-color:#5c1f00; color:#ffd28f;"
            " border:2px solid #ff8c42; border-radius:4px; padding:6px 16px;"
            " font-weight:bold; }"
            "QPushButton:hover { background-color:#7a2a00; }"
            "QPushButton:pressed { background-color:#481800; }"
            "QPushButton:disabled { color:#8a6f55; border-color:#8a6f55; }")
        self.btn_test = btn_test
        btn_save = QPushButton("保存", self)
        btn_save.setDefault(True)
        btn_cancel = QPushButton("取消", self)
        btn_test.clicked.connect(self._on_test)
        btn_save.clicked.connect(self._on_save)
        btn_cancel.clicked.connect(self.reject)

        btn_row = QHBoxLayout()
        btn_row.addWidget(btn_test)
        btn_row.addStretch(2)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_cancel)

        layout = QVBoxLayout(self)
        layout.addWidget(self.editor, 1)
        layout.addWidget(warn)
        layout.addWidget(self.result)
        layout.addLayout(btn_row)

    # ---------------- 对外只读接口（供自检/断言）
    def text(self) -> str:
        """编辑器当前内容。"""
        return self.editor.toPlainText()

    def _on_test(self) -> None:
        """测试：真实执行当前指令，输出/退出码/错误展示进结果区。

        与扇区点击同一执行环境（同解释器、cwd=BASE_DIR、CREATE_NO_WINDOW 静默），
        如实复现点击行为；测试在后台轮询，不阻塞 UI、不弹额外模态。
        """
        if self._proc is not None:   # 上一次测试仍在轮询：忽略连点，避免叠加
            return
        code = self.editor.toPlainText()
        try:
            _kind, _payload, argv, cleanup, err = build_code_test(code)
        except Exception as exc:   # noqa: BLE001 - 构造过程异常（如写临时文件失败）
            _log_crash("code_edit_test_build", exc)
            self._show_result("构造执行命令失败", f"{type(exc).__name__}: {exc}")
            return
        if err:
            self._show_result("指令未通过校验，未执行", err)
            return
        try:
            proc = _test_spawn(argv)
        except Exception as exc:   # noqa: BLE001 - 启动失败（解释器缺失等）
            _log_crash("code_edit_test_spawn", exc)
            for p in cleanup:
                try:
                    os.remove(p)
                except OSError:
                    pass
            self._show_result("启动执行失败", f"{type(exc).__name__}: {exc}")
            return
        self._proc = proc
        self._cleanup = cleanup
        self.result.setPlainText("")
        self.result.setPlaceholderText("正在执行…")
        self.result.show()
        self.btn_test.setEnabled(False)
        # 测试超时（TEST_RUN_TIMEOUT_SEC）：停止轮询与展示，进程留在后台自行结束
        self._poll_timer = QTimer(self)
        self._poll_timer.setSingleShot(False)
        self._poll_timer.timeout.connect(self._poll_test)
        self._poll_timer.start(150)
        self._deadline = time.time() + TEST_RUN_TIMEOUT_SEC
        QTimer.singleShot(0, self._poll_test)   # 立刻检查一次（可能瞬间结束）

    def _poll_test(self) -> None:
        """轮询测试子进程：结束后读出输出/退出码填入结果区并清理临时文件。"""
        if self._closed or self._proc is None:
            return
        proc, cleanup = self._proc, self._cleanup
        if proc.poll() is None:
            if time.time() >= self._deadline:
                self._stop_polling()
                self._show_result(
                    "已超时（超过 %g 秒）" % TEST_RUN_TIMEOUT_SEC,
                    "指令仍在后台运行完成。正式执行（扇区点击）同样不等待，属正常行为。")
            return
        try:
            out, err_out = proc.communicate(timeout=2)
        except Exception:   # noqa: BLE001 - 读取残留输出失败不致命
            out, err_out = b"", b""
        self._stop_polling()
        self._cleanup_run(cleanup)
        text_out = out.decode("utf-8", "replace").rstrip()
        text_err = err_out.decode("utf-8", "replace").rstrip()
        code = proc.returncode or 0
        if code == 0 and not text_err:
            title = "执行成功（退出码 0）"
            detail = text_out if text_out else "（无输出）"
        else:
            title = "执行结束，退出码 %s" % code
            detail = text_out + ("\n" if text_out and text_err else "") + text_err
        self._show_result(title, detail)

    def _stop_polling(self) -> None:
        try:
            if getattr(self, "_poll_timer", None) is not None:
                self._poll_timer.stop()
        except RuntimeError:
            pass
        self._proc = None

    def _cleanup_run(self, cleanup: list[str]) -> None:
        for p in cleanup:
            try:
                os.remove(p)
            except OSError:
                pass

    def _show_result(self, title: str, detail: str) -> None:
        self.result.setPlaceholderText("")
        self._result_title = title
        self.result.setPlainText(f"【{title}】\n{detail}" if detail else f"【{title}】")
        self.result.show()
        self.btn_test.setEnabled(True)

    def _on_save(self) -> None:
        """保存：仅校验格式/语法（不执行）。合法→accept 写回单元格；非法→弹窗提示留在编辑。

        含【【【…】】】成对特殊标记时，分别校验主指令与每个次级项代码（任一非法即拒）。
        """
        code = self.editor.toPlainText()
        parsed = parse_special_sections(code)
        if parsed["err"]:
            QMessageBox.warning(self, "代码异常请查证", f"特殊标记未通过校验：\n\n{parsed['err']}")
            return
        # 子功能块数量校验：含特殊标记时，成对数量必须 2~9
        if parsed["total"]:
            err_count = validate_sub_items_count(parsed["total"])
            if err_count:
                QMessageBox.warning(self, "代码异常请查证",
                                    f"{err_count}\n\n（次级环形最多 {MAX_SECTORS} 个，"
                                    f"至少 {MIN_SECTORS} 个才能形成环形菜单）")
                return
        # 主指令（若有内容）与每个次级项代码（若有内容）逐段校验
        candidates = [("主指令", parsed["main"])]
        candidates += [(f"次级项「{it['name']}」", it["code"]) for it in parsed["items"]]
        for label, seg in candidates:
            if not seg.strip():
                continue   # 空段=仅占位/仅含次级功能，合法
            _kind, _payload, err = classify_code(seg)
            if err:
                QMessageBox.warning(self, "代码异常请查证",
                                    f"{label} 未通过校验：\n\n{err}")
                return
        self.accept()

    def _on_finished(self, _result: int) -> None:
        """窗口关闭（保存/取消/×）：停止轮询与进程句柄持有，留给录播进程后台自行结束。"""
        self._closed = True
        try:
            if getattr(self, "_poll_timer", None) is not None:
                self._poll_timer.stop()
        except RuntimeError:
            pass
        self._proc = None   # 不再持有；进程若未结束会自行跑完（与正式执行一致）
CODE_HELP_TEXT = (
    "【指令格式说明】\n\n"
    "指令有多种格式，先写「格式名: 」，后跟指令内容；不写格式时默认为 Python（py）。\n"
    "每条指令可以是单行，也可以是多行（在输入框里直接回车换行）。\n\n"
    "────────────────────────────\n"
    "■ 多行怎么写？（重要）\n"
    "  三种格式的多行处理方式完全不同：\n"
    "  · cmd（CMD 批处理）：多行会被逐行依次执行，中间会自动用 & 连接\n"
    "    （相当于在命令行里用 & 把命令串起来）。每一行都是独立的命令，\n"
    "    完全不需要缩进，也请勿使用缩进。\n"
    "  · ps / pwsh（PowerShell）：每一行都会被当作完整的命令执行，\n"
    "    行与行之间自动用换行连接（相当于直接把多句代码粘进终端）。\n"
    "    同一逻辑块内需要缩进的语句（如 if / for 内部），必须保持缩进；\n"
    "    顶层语句必须顶格，多行脚本前请勿多加缩进，否则会缩进报错。\n"
    "  · py（Python）：多行就是一段完整的 Python 程序，执行时保留\n"
    "    原有缩进与换行。块结构（if/for/def/with 等）内部必须缩进，\n"
    "    建议使用 4 个空格；程序顶层必须顶格，不要整体多缩进。\n\n"
    "  共同要点：\n"
    "  · 第一行只写「格式名: 」，指令内容从第二行开始写\n"
    "    （也可以把指令和格式名写在同一行，如 cmd: start notepad）。\n"
    "  · 单行指令直接写在格式名后面即可。\n\n"
    "────────────────────────────\n"
    "■ CMD（cmd:）\n"
    "  单行（打开记事本）：\n"
    "    cmd: start notepad\n"
    "  带路径（英文双引号包住路径）：\n"
    "    cmd: start \"C:\\Windows\\notepad.exe\"\n"
    "  多行（依次执行多条命令，无需缩进，可直接复制）：\n"
    "    cmd:\n"
    "    start notepad\n"
    "    echo 记事本已打开\n\n"
    "■ PS（ps: / powershell:）\n"
    "  单行：\n"
    "    ps: Start-Process notepad\n"
    "  多行（顶层顶格，块内缩进，可直接复制）：\n"
    "    ps:\n"
    "    Start-Process notepad\n"
    "    $n = 1 + 1\n"
    "    Write-Host \"结果：$n\"\n\n"
    "■ PWSH7（pwsh: / pwsh7: / ps7:）\n"
    "  与 ps 相同，但优先使用 PowerShell 7 执行；\n"
    "  本机没有安装 PowerShell 7 时会自动改用 5.1，不影响使用。\n"
    "  示例同 ps。\n\n"
    "■ PY（py: / python: / 不写格式）\n"
    "  单行（打开记事本）：\n"
    "    py: import subprocess; subprocess.Popen(['notepad.exe'])\n"
    "  多行（块内缩进 4 空格，顶层顶格，可直接复制）：\n"
    "    py:\n"
    "    import subprocess, os\n"
    "    p = os.path.join(os.environ['TEMP'], 'aura_test.txt')\n"
    "    with open(p, 'w', encoding='utf-8') as f:\n"
    "        f.write('hello')\n"
    "    subprocess.Popen(['notepad.exe', p])\n\n"
    "--------\n"
    "· 格式名大小写不敏感（CMD: 或 cmd: 均可）。\n"
    "· 路径含空格时，务必用英文双引号包住整个路径。\n"
    "· 所有指令都静默执行：不会弹出黑色控制台窗口。\n"
    "· 指令有语法/格式错误时不会执行，扇区闪红色提示。\n"
    "· 程序只负责启动指令，不等待其运行结束，界面不会卡住。\n\n"
    "────────────────────────────\n"
    "■ 如何编辑指令？\n"
    "  · 单击指令框 = 行内快速编辑，适合修改短指令；回车进入编辑，点其他处完成。\n"
    "  · 双击指令框 = 行内编辑，可在框内直接输入/粘贴多行内容。\n"
    "  · 在指令框上【右键】= 「高级编辑…」，打开独立大编辑窗，适合编写复杂/\n"
    "    多行指令：编辑窗内提供「测试 / 保存 / 取消」三个按钮。\n\n"
    "■ 高级编辑窗的三个按钮\n"
    "  · 测试 = 按扇区点击的同一逻辑真实执行当前指令（可能启动程序、修改文件或\n"
    "    联网），执行输出与退出码会显示在编辑窗内；运行前请确认指令安全无害。\n"
    "  · 保存 = 仅校验指令的格式/语法是否合法，不执行；校验不通过会弹窗提示\n"
    "    「代码异常请查证」，修正后才能写回指令框。\n"
    "  · 取消 = 不做任何更改，直接返回设置界面。\n\n"
    "  提示：多行指令建议用右键「高级编辑」编写；测试按钮会真实执行，请谨慎。\n\n"
    "────────────────────────────\n"
    "■ 次级动态扇形（右键展开子菜单）\n"
    "  主扇形展开后，在某个扇区上【右键】可展开该扇区专属的「次级动态扇形」\n"
    "  （先收回主扇形，约 300ms 后沿球心顺时针展开一圈新扇区，操作方式与主扇区相同）。\n\n"
    "  如何定义：在指令中用「成对特殊标记」把代码分区——\n"
    "    · 第一个标记行之前的所有代码 = 主指令（左键点击该扇区时执行，可为空）；\n"
    "    · 一对【【【名字】】】之间的代码 = 该次级项的指令；多对标记可定义多个次级项；\n"
    "    · 标记必须成对闭合（同名配对，不支持嵌套），配对顺序异常会校验失败。\n\n"
    "  示例（cmd 格式）：\n"
    "    cmd: echo 这是主指令\n"
    "    【【【子代码1】】】\n"
    "    cmd: echo 这是次级项1\n"
    "    【【【子代码1】】】\n"
    "    【【【子代码2】】】\n"
    "    cmd: echo 这是次级项2\n"
    "    【【【子代码2】】】\n\n"
    "  说明：\n"
    "    · 标题限 6 个汉字 / 12 个字母，超长自动截断；空标题显示「未命名」；\n"
    "    · 左键点击含标记的扇区 = 只执行「主指令」；主指令为空则仅收起、不执行、不闪红；\n"
    "    · 右键点击无标记的扇区 = 红色闪光提示（无下级功能）；\n"
    "    · 次级环上的扇区再右键不会继续套娃（不递归），仅闪红提示；\n"
    "    · 「测试」按钮对含标记指令只会测试「主指令」（标记块会被跳过，避免误报语法）。\n\n"
    "  提示：含『代码1』『代码2』等多子功能的扇区，最适合用右键一键展开次级菜单执行。"
)


class SettingsDialog(QDialog):
    """三列：复选框（勾选=激活） / 项目名（扇区显示） / 功能代码（py / cmd: / ps:）。

    拖动整行可调整扇区顺序；条目总数不限；保存写回 yaml 并触发重新加载。
    """

    def __init__(self, rows: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.resize(660, 440)
        self._help_dlg: QDialog | None = None
        self._aura_dlg: QDialog | None = None   # 动效二级弹窗（有则前台、无则新建）
        self._code_dlg: "CodeEditDialog | None" = None   # 指令高级编辑窗（右键入口，去重置前）

        self.table = ReorderTable(self)
        # 指令列用多行编辑器（QPlainTextEdit 委托）：支持直接输入/粘贴多行代码
        self.table.setItemDelegateForColumn(2, MultilineEditDelegate(self.table))
        # 修复编辑重影并适配系统深浅色：编辑器背景/文字跟随当前配色模式
        # （浅色=白底黑字，深色=黑底白字），避免底层文字透出形成重影。
        self._apply_table_editor_theme()
        self.table.setHorizontalHeaderLabels(
            ["激活", "功能", "指令"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        # 表头样式：居中 + 粗体；去掉点击排序交互（不显示排序箭头、
        # 禁点击排序，表头点击不再高亮/改变排序）
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        hh.setSortIndicatorShown(False)
        hh.setSectionsClickable(False)
        self.table.setSortingEnabled(False)
        font_h = hh.font()
        font_h.setBold(True)
        hh.setFont(font_h)
        self.table.setColumnWidth(0, 52)
        self.table.setColumnWidth(1, 170)
        self.table.verticalHeader().setVisible(False)
        self.table.itemChanged.connect(self._on_item_changed)

        # hint 必须先于填行创建：_insert_row 会调 _update_hint 写提示文本
        self.hint = QLabel(self)
        self.hint.setWordWrap(True)

        for i, r in enumerate(rows):
            self._insert_row(i, r)

        btn_add = QPushButton("+1", self)
        btn_del = QPushButton("-1", self)
        btn_add.setToolTip("添加一行")
        btn_del.setToolTip("删除选中")
        btn_add.clicked.connect(lambda: self._insert_row(
            self.table.rowCount(), {"name": "新功能", "code": "", "active": False}))
        btn_del.clicked.connect(self._delete_current)

        # 底部按钮行（自定义）：主题色 | 帮助 ……… 保存 | 取消
        # 帮助窗与设置窗同级（非模态 show），打开帮助不阻塞操作设置；
        # 点 Save 时若帮助窗还开着会一并关闭。
        btn_aura = QPushButton("动效", self)
        btn_help = QPushButton("帮助", self)
        btn_save = QPushButton("保存", self)
        btn_save.setDefault(True)
        btn_cancel = QPushButton("取消", self)
        # 导入/导出配置（位于帮助按钮右侧；导出为 .yaml.bak，导入则覆盖当前配置）
        btn_transfer = QPushButton("导入/导出配置", self)
        btn_aura.clicked.connect(self._open_aura_settings)
        btn_save.clicked.connect(self._on_save)
        btn_help.clicked.connect(self._show_code_help)
        btn_transfer.clicked.connect(self._transfer_config)
        btn_cancel.clicked.connect(self.reject)
        self.btn_aura, self.btn_save, self.btn_help = btn_aura, btn_save, btn_help
        self.btn_transfer = btn_transfer

        btn_row = QHBoxLayout()
        btn_row.addWidget(btn_aura)
        btn_row.addWidget(btn_help)
        btn_row.addWidget(btn_transfer)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_cancel)

        bar = QHBoxLayout()
        bar.addWidget(btn_add)
        bar.addWidget(btn_del)
        bar.addStretch(1)
        # —— 保持球体置顶（默认不勾选；勾选后写 window.always_on_top 并立即生效）——
        self.chk_topmost = QCheckBox("保持球体置顶", self)
        self.chk_topmost.setToolTip("勾选后球体始终显示在所有窗口最上方；不勾选则正常层级")
        self.chk_topmost.setChecked(topmost_enabled())
        bar.addWidget(self.chk_topmost)
        self.chk_autostart = QCheckBox("开机自启动", self)
        self.chk_autostart.setToolTip("勾选后开机自动运行本程序")
        self.chk_autostart.setChecked(autostart_enabled())
        bar.addWidget(self.chk_autostart)
        self.bar = bar   # 布局引用：自检断言「置顶复选框位于开机自启动上方」用

        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addWidget(self.hint)
        layout.addLayout(bar)
        layout.addLayout(btn_row)
        self._update_hint()

    def open_code_editor(self, row: int) -> None:
        """指令列「高级编辑」唯一入口：打开独立编辑窗（CodeEditDialog）。

        去重：编辑窗已打开则置前，不做新建；先定位再模态 exec（与动效窗一致，
        编辑期间设置窗不可操作）；保存（Accepted）后把编辑内容写回第 2 列单元格；
        取消不做任何更改。行号越界直接忽略（拖拽/删除后 emoji 场景防御）。
        """
        try:
            if self._code_dlg is not None and self._code_dlg.isVisible():
                self._code_dlg.raise_()
                self._code_dlg.activateWindow()
                return
            if not (0 <= row < self.table.rowCount()):
                return
            item = self.table.item(row, 2)
            cur = item.text() if item is not None else ""
            dlg = CodeEditDialog(cur, self)
            dlg.finished.connect(lambda _r: setattr(self, "_code_dlg", None))
            self._code_dlg = dlg   # 保引用，防止被回收
            self._place_sub_dialog(dlg)   # 先定位再 exec，模态栈内位置不被 Qt 打乱
            if dlg.exec() == int(QDialog.DialogCode.Accepted):
                # 保存写回当前行指令列（行可能已因拖拽/删除变化，按最新行号写回）
                if 0 <= row < self.table.rowCount():
                    it = self.table.item(row, 2)
                    if it is not None:
                        it.setText(dlg.text())
        except Exception as exc:   # noqa: BLE001
            _log_crash("open_code_editor", exc)
        finally:
            self._code_dlg = None

    def _apply_table_editor_theme(self) -> None:
        """输入框编辑时按系统深浅色模式着色：浅色=白底黑字，深色=黑底白字。

        同时给编辑器不透明背景，避免底层单元格文字透出形成重影。
        """
        palette = QApplication.instance().palette()
        dark = palette.color(QPalette.ColorRole.Window).lightness() < 128
        if dark:
            self.table.setStyleSheet("""
                QLineEdit {
                    background-color: #1f1f1f;
                    color: #ffffff;
                    border: 1px solid #5a5a5a;
                    padding: 2px;
                }
                QLineEdit:focus { border: 1px solid #4da3ff; }
            """)
        else:
            self.table.setStyleSheet("""
                QLineEdit {
                    background-color: #ffffff;
                    color: #000000;
                    border: 1px solid #767676;
                    padding: 2px;
                }
                QLineEdit:focus { border: 1px solid #0078d4; }
            """)

    def _on_item_changed(self, item) -> None:
        # 第二列（项目名）输入即截断到宽度上限：6 汉字 / 12 字母。
        # 截断要 blockSignals，否则 setText 再触发 itemChanged 造成递归。
        if item.column() == 1:
            limited = clamp_name(item.text(), CONFIG["label_max_units"])
            if limited != item.text():
                self.table.blockSignals(True)
                item.setText(limited)
                self.table.blockSignals(False)
        self._update_hint()

    def _insert_row(self, at: int, row: dict) -> None:
        self.table.blockSignals(True)   # 程序化填充不触发 itemChanged 刷新
        self.table.insertRow(at)
        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        chk.setCheckState(Qt.CheckState.Checked if row["active"] else Qt.CheckState.Unchecked)
        chk.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(at, 0, chk)
        self.table.setItem(at, 1, QTableWidgetItem(row["name"]))
        self.table.setItem(at, 2, QTableWidgetItem(row["code"]))
        self.table.blockSignals(False)
        self._update_hint()

    def _delete_current(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)
            self._update_hint()

    def _update_hint(self) -> None:
        n = sum(1 for i in range(self.table.rowCount())
                if (it := self.table.item(i, 0)) is not None
                and it.checkState() == Qt.CheckState.Checked)
        if n > MAX_SECTORS:
            self.hint.setText(f"已勾选 {n} 项，超过上限：圆环只显示前 {MAX_SECTORS} 项")
        elif n < MIN_SECTORS:
            self.hint.setText(f"已勾选 {n} 项，低于下限，圆环将用空位补齐2项")
        else:
            self.hint.setText(f"已勾选 {n} 项 → 圆环 {n} 等分")

    def rows(self) -> list[dict]:
        """收集全部行；项目名任意字符均合法（含空格/空串），不丢行、不因空名跳过。

        修复：旧实现先 strip 再 for 循环里 if not name: continue，用户项目名输入
        空格（strip 后为空）保存时整行被丢弃；若所有行都为空名，functions.yaml
        会被写成 functions: []，全部设置消失。现在不 strip、不跳过——
        纯空格名原样保留（超长已在输入时被 _on_item_changed 截断），
        完全空名兜底成「未命名」占位，保证行永久保留。
        """
        out = []
        for i in range(self.table.rowCount()):
            raw = self.table.item(i, 1).text()
            name = clamp_name(raw, CONFIG["label_max_units"]) if raw else "未命名"
            out.append({
                "name": name,
                "code": self.table.item(i, 2).text(),
                "active": self.table.item(i, 0).checkState() == Qt.CheckState.Checked,
            })
        return out

    def _on_save(self) -> None:
        # 保存时帮助窗若还开着（帮助与设置同级，可交叉操作），一并关闭
        if self._help_dlg is not None and self._help_dlg.isVisible():
            self._help_dlg.close()
        self._help_dlg = None
        # 开机自启动：按复选框状态写入/删除注册表启动项；失败提示但不打断保存
        try:
            set_autostart(self.chk_autostart.isChecked())
        except OSError as exc:
            _log_crash("autostart", exc)
            QMessageBox.warning(self, "设置", f"开机自启动设置失败：{exc}")
        # 保持球体置顶：写 setting.yaml 的 window.always_on_top 并立即生效
        try:
            set_topmost(self.chk_topmost.isChecked())
        except OSError as exc:
            _log_crash("topmost", exc)
            QMessageBox.warning(self, "设置", f"保持球体置顶设置失败：{exc}")
        # 指令校验：子功能块数量（成对特殊标记）必须 2~9，且每段代码格式合法
        for row in self.rows():
            row_code = row["code"]
            parsed = parse_special_sections(row_code)
            if parsed["err"]:
                QMessageBox.warning(
                    self, "代码异常请查证",
                    f"「{row['name']}」的指令未通过校验：\n\n{parsed['err']}")
                return
            if parsed["total"]:
                err_count = validate_sub_items_count(parsed["total"])
                if err_count:
                    QMessageBox.warning(
                        self, "代码异常请查证",
                        f"「{row['name']}」{err_count}\n\n"
                        f"（次级环形最多 {MAX_SECTORS} 个，至少 {MIN_SECTORS} 个才能形成环形菜单）")
                    return
                candidates = [("主指令", parsed["main"])]
                candidates += [(f"次级项「{it['name']}」", it["code"])
                               for it in parsed["items"]]
                for label2, seg in candidates:
                    if not seg.strip():
                        continue
                    _k2, _p2, err2 = classify_code(seg)
                    if err2:
                        QMessageBox.warning(
                            self, "代码异常请查证",
                            f"「{row['name']}」的{label2}未通过校验：\n\n{err2}")
                        return
        save_functions(self.rows())
        self.accept()

    # ------------------------------------------------------------ 导入 / 导出配置
    def _transfer_config(self) -> None:
        """导入/导出配置入口（按钮「导入/导出配置」）。

        弹菜单选动作；动作用 singleShot 推迟，避免在菜单模态栈内再嵌文件对话框。
        """
        menu = QMenu(self)
        act_export = menu.addAction("导出配置到文件夹…")
        act_import = menu.addAction("导入配置…")
        chosen = menu.exec(QCursor.pos())
        if chosen is act_export:
            QTimer.singleShot(0, self._export_config)
        elif chosen is act_import:
            QTimer.singleShot(0, self._import_config)

    def _export_config(self) -> None:
        """导出：选一个目标文件夹，把当前配置写为该文件夹下 setting.yaml.bak。"""
        folder = QFileDialog.getExistingDirectory(self, "选择导出文件夹")
        if not folder:
            return
        data = read_setting() or default_settings()
        target = os.path.join(folder, "setting.yaml.bak")
        try:
            write_setting(data, target)
        except Exception as exc:  # noqa: BLE001
            _log_crash("export_config", exc)
            QMessageBox.warning(self, "导入/导出配置", f"导出失败：{exc}")
            return
        QMessageBox.information(self, "导入/导出配置", f"已导出配置到：\n{target}")

    def _import_config(self) -> None:
        """导入：选一个 *.yaml / *.yaml.bak 文件，内容功能块合法才询问覆盖；
        确认后写入主位置并关闭设置窗（由 _on_settings_closed 触发即时生效）。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择要导入的配置文件", "", "配置文件 (*.yaml *.yaml.bak)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                src = yaml.safe_load(fh) or {}
        except Exception as exc:  # noqa: BLE001
            _log_crash("import_config_parse", exc)
            QMessageBox.warning(self, "导入/导出配置", f"无法读取所选文件：{exc}")
            return
        if not (isinstance(src, dict) and _yaml_blocks_legal(src)):
            QMessageBox.warning(
                self, "导入/导出配置",
                "所选文件不是有效的 Aura Pixie 配置文件（缺少可识别的功能块）。")
            return
        ret = QMessageBox.question(
            self, "导入/导出配置",
            f"将用所选配置覆盖当前配置：\n{path}\n\n是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        try:
            merged = merge_settings_with_defaults(src)
            write_setting(merged)
        except Exception as exc:  # noqa: BLE001
            _log_crash("import_config", exc)
            QMessageBox.warning(self, "导入/导出配置", f"导入失败：{exc}")
            return
        self.accept()   # 关闭设置窗，触发 reload_config 与应用置顶

    def _show_code_help(self) -> None:
        """弹窗说明「功能代码」的格式与运行方式。内容与 classify_code/run_code 一致。

        与设置窗同级：非模态 show 打开，不阻塞设置窗操作；
        已开时再次点 Help 只把窗口置前，不重复创建。
        """
        if self._help_dlg is not None and self._help_dlg.isVisible():
            self._help_dlg.raise_()
            self._help_dlg.activateWindow()
            return
        dlg = build_code_help_dialog(self)
        dlg.finished.connect(lambda _r: setattr(self, "_help_dlg", None))
        self._help_dlg = dlg
        dlg.show()
        dlg.raise_()
        # 二级弹窗定位：相对设置窗，选空间大的一侧、边缘相交、高度与设置窗一致
        self._place_sub_dialog(dlg)

    def _open_aura_settings(self) -> None:
        """打开动效设置（动效按钮）。去重：已打开则前台该窗口，未打开才新建。

        动效设置优先级高于基本设置：用模态 exec()，未完成（取消/关闭）前
        本设置窗不可操作；点确定后颜色立即生效并同步配置。
        """
        if self._aura_dlg is not None and self._aura_dlg.isVisible():
            self._aura_dlg.raise_()
            self._aura_dlg.activateWindow()
            return
        dlg = AuraSettingsDialog(self)
        dlg.finished.connect(lambda _r: setattr(self, "_aura_dlg", None))
        self._aura_dlg = dlg
        self._place_sub_dialog(dlg)   # 先定位再 exec，模态栈内位置不被 Qt 打乱
        if dlg.exec() == int(QDialog.DialogCode.Accepted):
            _apply_aura_config(dlg.settings())

    def _place_sub_dialog(self, dlg: QDialog) -> None:
        """二级弹窗（帮助/动效/未来扩展）定位：水平方向取空间较大的一侧
        （设置窗左/右），弹窗边缘与设置窗边缘相交；垂直方向下边缘与设置窗
        下边缘严格对齐。

        设置窗在屏幕中下方时，弹窗会出现在设置窗左侧/右侧且底部对齐，
        视觉上像依附于设置窗底部展开，不会被竖直居中打乱。
        """
        parent_geo = self.geometry()
        screen = QApplication.primaryScreen().availableGeometry()
        # 水平：选设置窗左右两侧空间较大的一侧，弹窗边缘与设置窗侧边缘相交
        left_gap = parent_geo.left() - screen.left()
        right_gap = screen.right() - parent_geo.right()
        if right_gap >= left_gap:
            x = parent_geo.right() + 1            # 设置在右：弹窗左缘贴设置窗右缘
        else:
            x = parent_geo.left() - dlg.width()   # 设置在左：弹窗右缘贴设置窗左缘
        # 垂直：下边缘严格对齐设置窗下边缘
        y = parent_geo.bottom() - dlg.height() + 1
        # 屏幕边界兜底
        if x + dlg.width() > screen.right() - 4:
            x = screen.right() - dlg.width() - 4
        if x < screen.left() + 4:
            x = screen.left() + 4
        if y + dlg.height() > screen.bottom() - 4:
            y = screen.bottom() - dlg.height() - 4
        if y < screen.top() + 4:
            y = screen.top() + 4
        dlg.move(round(x), round(y))


def build_code_help_dialog(parent: QWidget | None = None) -> QDialog:
    """构建指令格式帮助窗（独立函数，便于离屏冒烟测试不用真的 exec 阻塞）。"""
    dlg = QDialog(parent)
    dlg.setWindowTitle("指令格式说明")
    dlg.resize(560, 430)
    box = QVBoxLayout(dlg)
    label = QLabel(CODE_HELP_TEXT, dlg)
    label.setWordWrap(False)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    scroll = QScrollArea(dlg)
    scroll.setWidgetResizable(True)
    scroll.setWidget(label)
    box.addWidget(scroll)
    close_btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dlg)
    close_btn.rejected.connect(dlg.reject)
    close_btn.accepted.connect(dlg.reject)
    box.addWidget(close_btn)
    return dlg


# ---------------------------------------------------------------- Aura 光效设置
def _clamp_scale(v) -> float:
    """倍率钳制到 [0.1, 3.0]：超出或不足取最近边界值。"""
    try:
        return round(max(0.1, min(3.0, float(v))), 2)
    except (TypeError, ValueError):
        return 1.0


def _clamp_idle(v) -> int:
    """休眠秒数钳制到 [10, 600]；非法回退 60。"""
    try:
        return max(10, min(600, int(round(float(v)))))
    except (TypeError, ValueError):
        return 60


def _clamp_opacity(v) -> int:
    """总体透明度钳制到 [10, 100]（%）；非法回退 100。"""
    try:
        return max(10, min(100, int(round(float(v)))))
    except (TypeError, ValueError):
        return 100


def _clamp_sleep_op(v) -> int:
    """休眠透明度钳制到 [1, 20]（%）；非法回退 10。"""
    try:
        return max(1, min(20, int(round(float(v)))))
    except (TypeError, ValueError):
        return 10


def resolve_sleep_opacity(sleep_raw, opacity_raw) -> int:
    """休眠透明度最终取值：钳制到 [1, 20]；若大于总体透明度则取 10%。"""
    op = _clamp_opacity(opacity_raw)
    sl = _clamp_sleep_op(sleep_raw)
    return 10 if sl > op else sl


def load_aura_config() -> dict:
    """读取光效配置块（中间彩色 + 光效倍率 + 休眠秒数）。缺省回退 CONFIG 默认值；解析失败不打断启动。"""
    data = read_setting().get("aura") or {}
    if not isinstance(data, dict):
        data = {}
    # 兼容旧字段：已存在的 idle_min（分钟）按 60 换算成秒
    raw_idle = data.get("idle_sec") if "idle_sec" in data else (
        float(data["idle_min"]) * 60.0 if "idle_min" in data else CONFIG["idle_sec"])
    out = {
        "color_start": "#FFFFFF",  # 起始/末尾固定白色，仅兼容旧配置
        "color_mid": str(data.get("color_mid") or CONFIG["color_mid"]),
        "color_end": "#FFFFFF",
        "aura_scale": _clamp_scale(data.get("aura_scale", CONFIG["aura_scale"])),
        "idle_sec": _clamp_idle(raw_idle),
        "opacity": _clamp_opacity(data.get("opacity", CONFIG["opacity"])),
        "sleep_opacity": resolve_sleep_opacity(
            data.get("sleep_opacity", CONFIG["sleep_opacity"]),
            data.get("opacity", CONFIG["opacity"])),
    }
    return out


def save_aura_config(cfg: dict) -> None:
    """写回光效配置块，颜色归一成 #RRGGBB 大写，倍率/休眠秒数/透明度保留；不动其他配置块。

    休眠透明度若大于总体透明度，按规则归一为 10%。
    """
    data = read_setting()
    data["aura"] = {
        "color_mid": cfg["color_mid"].upper(),
        "aura_scale": _clamp_scale(cfg.get("aura_scale", 1.0)),
        "idle_sec": _clamp_idle(cfg.get("idle_sec", CONFIG["idle_sec"])),
        "opacity": _clamp_opacity(cfg.get("opacity", CONFIG["opacity"])),
        "sleep_opacity": resolve_sleep_opacity(
            cfg.get("sleep_opacity", CONFIG["sleep_opacity"]),
            cfg.get("opacity", CONFIG["opacity"])),
    }
    try:
        write_setting(data)
    except OSError as exc:
        _log_crash("save_aura_config", exc)


def apply_aura_to_config(cfg: dict) -> None:
    """把光效配置合并进 CONFIG（运行期立即生效，无需重启）。"""
    CONFIG["color_start"] = "#FFFFFF"
    CONFIG["color_mid"] = cfg["color_mid"]
    CONFIG["color_end"] = "#FFFFFF"
    CONFIG["aura_scale"] = _clamp_scale(cfg.get("aura_scale", CONFIG["aura_scale"]))
    CONFIG["idle_sec"] = _clamp_idle(cfg.get("idle_sec", CONFIG["idle_sec"]))
    CONFIG["opacity"] = _clamp_opacity(cfg.get("opacity", CONFIG["opacity"]))
    CONFIG["sleep_opacity"] = resolve_sleep_opacity(
        cfg.get("sleep_opacity", CONFIG["sleep_opacity"]),
        cfg.get("opacity", CONFIG["opacity"]))


def normalize_hex(text: str, fallback: str) -> str:
    """把用户输入归一成 #RRGGBB；不合法的返回 fallback（保持原色）。"""
    t = (text or "").strip().lstrip("#")
    if len(t) == 3:
        t = "".join(ch * 2 for ch in t)
    if len(t) == 6:
        try:
            int(t, 16)
            return "#" + t.upper()
        except ValueError:
            pass
    return fallback


class AuraSettingsDialog(QDialog):
    """光效设置对话框：中间彩色（十六进制+取色）+ 光效倍率（0.1x~3x）+
    总体透明度（10~100%）+ 休眠透明度（1~20%）。

    等级高于基本设置：模态 exec() 打开，未确定/取消前不能操作设置主窗。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("动效设置")
        self.resize(220, 245)   # 动效弹窗：220×245（新增透明度两行后加高）

        cur = load_aura_config()
        self._fallback = cur

        # 中间彩色：十六进制输入框 + 取色按钮
        mid_row = QHBoxLayout()
        self.edt_mid = QLineEdit(cur["color_mid"].upper(), self)
        self.edt_mid.setMaxLength(7)
        btn_pick = QPushButton("取色…", self)
        btn_pick.setToolTip("打开系统取色器，点选颜色后自动填入")
        btn_pick.clicked.connect(self._pick_mid)
        mid_row.addWidget(self.edt_mid, 1)
        mid_row.addWidget(btn_pick)

        # 光效倍率：0.1x ~ 3x，步进 0.1（QDoubleSpinBox）
        self.spin_scale = QDoubleSpinBox(self)
        self.spin_scale.setRange(0.1, 3.0)
        self.spin_scale.setSingleStep(0.1)
        self.spin_scale.setDecimals(1)
        self.spin_scale.setSuffix("x")
        self.spin_scale.setValue(float(cur.get("aura_scale", 1.0)))

        # 动效休眠：无球体交互（悬停/点击/拖拽）达到该秒数后呼吸暂停+透明度 sleep
        self.spin_idle = QSpinBox(self)
        self.spin_idle.setRange(10, 600)
        self.spin_idle.setSuffix(" 秒")
        self.spin_idle.setKeyboardTracking(False)   # 输入未提交时 value() 才不抢跑
        self.spin_idle.setValue(_clamp_idle(cur.get("idle_sec", CONFIG["idle_sec"])))
        self.spin_idle.editingFinished.connect(self._clamp_idle_input)

        # 总体透明度：10~100%，球体渲染 alpha 乘该系数
        self.spin_opacity = QSpinBox(self)
        self.spin_opacity.setRange(10, 100)
        self.spin_opacity.setSuffix("%")
        self.spin_opacity.setKeyboardTracking(False)
        self.spin_opacity.setValue(_clamp_opacity(cur.get("opacity", CONFIG["opacity"])))
        self.spin_opacity.editingFinished.connect(self._clamp_opacity_input)

        # 休眠透明度：1~20%，休眠时窗口透明度；若大于总体透明度则保存为 10%
        self.spin_sleep_op = QSpinBox(self)
        self.spin_sleep_op.setRange(1, 20)
        self.spin_sleep_op.setSuffix("%")
        self.spin_sleep_op.setKeyboardTracking(False)
        self.spin_sleep_op.setValue(_clamp_sleep_op(cur.get("sleep_opacity",
                                                           CONFIG["sleep_opacity"])))
        self.spin_sleep_op.editingFinished.connect(self._clamp_sleep_op_input)

        grid = QVBoxLayout()
        grid.addWidget(QLabel("主题色", self))
        grid.addLayout(mid_row)
        grid.addWidget(QLabel("呼吸倍率（0.1x~3.0x）", self))
        grid.addWidget(self.spin_scale)
        grid.addWidget(QLabel("休眠倒计时（秒）", self))
        grid.addWidget(self.spin_idle)
        grid.addWidget(QLabel("总体透明度（10%~100%）", self))
        grid.addWidget(self.spin_opacity)
        grid.addWidget(QLabel("休眠透明度（1%~20%）", self))
        grid.addWidget(self.spin_sleep_op)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                               | QDialogButtonBox.StandardButton.Cancel, self)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(grid)
        layout.addWidget(box)

    def _pick_mid(self) -> None:
        initial = normalize_hex(self.edt_mid.text(), self._fallback["color_mid"])
        color = QColorDialog.getColor(QColor(initial), self, "选择中间彩色")
        if color.isValid():
            self.edt_mid.setText(color.name().upper())

    def _clamp_idle_input(self) -> None:
        """休眠秒数输入失焦/回车时：就近取值钳制到 [10, 600] 并回填输入框，
        让用户看到修正后的值（<10 → 10，>600 → 600，非法 → 60）。"""
        v = self.spin_idle.value()
        clamped = _clamp_idle(v)
        if clamped != v:
            self.spin_idle.setValue(clamped)

    def _clamp_opacity_input(self) -> None:
        """总体透明度输入失焦/回车时：钳制到 [10, 100] 并回填。"""
        v = self.spin_opacity.value()
        clamped = _clamp_opacity(v)
        if clamped != v:
            self.spin_opacity.setValue(clamped)

    def _clamp_sleep_op_input(self) -> None:
        """休眠透明度输入失焦/回车时：钳制到 [1, 20] 并回填。"""
        v = self.spin_sleep_op.value()
        clamped = _clamp_sleep_op(v)
        if clamped != v:
            self.spin_sleep_op.setValue(clamped)

    def settings(self) -> dict:
        """收集当前设置（中间色非法回退原值；倍率夹在 0.1~3；休眠秒数就近钳制；
        总体透明度 [10,100]；休眠透明度按「大于总体则取 10%」规则归一）。"""
        op = _clamp_opacity(self.spin_opacity.value())
        return {
            "color_start": "#FFFFFF",
            "color_mid": normalize_hex(self.edt_mid.text(), self._fallback["color_mid"]),
            "color_end": "#FFFFFF",
            "aura_scale": round(float(self.spin_scale.value()), 2),
            "idle_sec": _clamp_idle(self.spin_idle.value()),
            "opacity": op,
            "sleep_opacity": resolve_sleep_opacity(self.spin_sleep_op.value(), op),
        }


def _apply_aura_config(cfg: dict) -> None:
    """光效设置确定后：写盘 + 合并到运行期 CONFIG。"""
    apply_aura_to_config(cfg)
    try:
        save_aura_config(cfg)
    except OSError as exc:  # noqa: BLE001
        _log_crash("aura_config_save", exc)


# ---------------------------------------------------------------- 主窗口
class AuraBall(QWidget):
    """无边框透明置顶圆球。收起时小窗口，展开扇形时临时放大，平时不挡桌面。"""

    def __init__(self) -> None:
        super().__init__()
        # 保持球体置顶：默认按配置（setting.yaml window.always_on_top，缺省 false）。
        # 置顶层是窗体级属性，运行时切换需重设 window flags，会伴随一次短暂隐藏/重显。
        self._topmost = topmost_enabled()
        self._apply_topmost()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFixedSize(IDLE_SIZE, IDLE_SIZE)
        icon = _load_builtin_icon()          # 优先内嵌资源（打包后不依赖外部 icon2.png）
        if icon.isNull() and os.path.exists(ICON_PATH):
            icon = QIcon(ICON_PATH)          # 兜底：源码运行时可直接读文件
        self.setWindowIcon(icon)

        self._sectors = load_functions()
        self._phase = 0.0
        self._clock = QElapsedTimer()
        self._last_ms = 0
        self._last_poll_ms = 0
        self._last_frame = -1
        self.frames_drawn = 0
        self.polls = 0                     # 高频采样次数（自检验响应频率用）

        self._hover_ms = 0.0
        self._out_ms = 0.0
        self._state = "hidden"          # hidden | expanding | open | collapsing
        self._progress = 0.0
        self._highlight = -1
        self._fill_color = center_color_at(0.0)
        self._flash: dict | None = None
        self._settings_dlg: SettingsDialog | None = None
        # 呼吸冻结：扇形展开期间球体呼吸暂停（颜色/大小停在展开瞬间），回收后继续。
        # 单独存冻结相位，收起途中回球重开时按新相位刷新冻结值。
        self._breath_frozen = False
        self._frozen_phase = 0.0
        # 按住球体到位回收：悬停展开后按下球体超时（500ms）未拖动 → 判定要移动球体，
        # 先回收扇形；该标志在「按住→隐藏」期间禁止扇形重新展开，直到松开。
        self._hold_collapsed = False

        # —— 动效休眠 ——
        # 无球体交互（悬停/点击/拖拽）累计达到 idle_sec 秒 → 呼吸暂停 + 透明度 10%；
        # 对球体有任何交互（悬停/点击/拖拽）立即重计；点击/悬停唤醒。
        self._idle_ms = 0.0            # 距上次球体交互的累计时长
        self._asleep = False           # 当前是否处于休眠（低透明度）

        # —— 单击/双击判别 ——
        # 需求：删除双击功能，只保留单击事件。双击由两下快速单击组成，
        # 第一击的抬起发生时无法预知后面是否还有一击，不能在第一击就执行扇区。
        # 因此单击执行改为「延迟确认」：松开时只登记待执行扇区并启动确认定时器；
        # 若在双击判定窗口内系统判定为双击（第二击按下进来）→ 整段取消，双击零效果；
        # 窗口内无第二击 → 定时器到点确认是单击，才真正执行扇区。
        # 确认窗口 = min(系统双击间隔, 300ms) + 30ms，单击最长延迟约 330ms，
        # 与悬停展开 300ms 的节奏一致。
        self._click_pending: int | None = None   # 待确认执行的扇区号（None=无）
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._commit_click)
        self._dbl_click_seen = False   # 系统已判定本次为双击，第二击的抬起整段吞掉

        # —— 拖动 / 吸附 ——
        # 球心是唯一的坐标真源（全局浮点），窗口位置由它推出；
        # _follow=True 时用临界阻尼弹簧把球心拉向鼠标目标，天然得到「慢-快-慢停」。
        self._center = QPointF(0.0, 0.0)
        self._vel = QPointF(0.0, 0.0)
        self._target = QPointF(0.0, 0.0)
        self._follow = False
        self._press: dict | None = None      # {grab: 全局按下点, offset: 球心相对鼠标}
        self._dragging = False
        self._snap: dict | None = None       # {from, to, ms, dur} 非线性吸附/回位
        # —— 次级动态扇形（V9）——
        # 右键展开的主列表保存；_sub_sectors 展开期间临时替换 _sectors；
        # _sub_pending_items = 待展开的次级项；_sub_launching = 收起飞行中等待
        # 延迟展开（禁止收回途中回球重开）；_sub_launching_aborted = 期间被打断
        # （拖动/休眠/二次操作）应取消本次次级展开。
        self._main_sectors = list(self._sectors)
        self._sub_sectors: list[dict] | None = None
        self._sub_pending_items: list[dict] | None = None
        self._sub_launching = False
        self._sub_launching_aborted = False
        self._sub_timer: QTimer | None = None
        self._ctx_consumed = False   # 右键已消费（环上触发次级/闪红）→ 抑制默认菜单
        self._warn_ms = 0.0                  # 越界累计时长，用于闪烁
        self._warn_on = False
        # —— 定位闪烁（「在哪儿？」跨进程定位红框）——
        # 复用越界警告的红虚线外圈绘制；_locate_left_ms>0 表示定位闪烁进行中
        self._locate_left_ms = 0.0
        self._locate_total_ms = 2000.0   # 定位红框持续时长
        self._poll_highlight = -1            # 上次采样的高亮，用于只在变化时重绘
        # 注入屏幕可用区（自检用）；None = 取真实屏幕
        self._screen_override: tuple | None = None

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(HEARTBEAT_MS)
        self._timer.timeout.connect(self._on_tick)

        # 高亮单独用 8ms 采样，与 60fps 动画帧解耦：动画掉帧不影响落点响应速度
        self._poll = QTimer(self)
        self._poll.setTimerType(Qt.TimerType.PreciseTimer)
        self._poll.setInterval(CONFIG["poll_ms"])
        self._poll.timeout.connect(self._on_poll)

    def _apply_topmost(self) -> None:
        """把当前置顶配置落到 window flags。

        置顶 = Frameless + WindowStaysOnTop + Tool；不置顶 = 前两者（去掉 StaysOnTop）。
        运行期切换会重设窗口标志（Qt 会隐藏再重显窗口），因此只在启动和
        设置保存后调用；球心坐标作为唯一真源，重显后立即按球心复位。
        """
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self._topmost:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        was_initialized = hasattr(self, "_center")
        c = QPointF(self._center) if was_initialized else None
        self.setWindowFlags(flags)
        if c is not None:
            self._center = c
            self._apply_center()

    def apply_topmost_setting(self, enabled: bool) -> None:
        """运行时按配置切换置顶（设置保存后调用）。已一致则跳过。"""
        if bool(enabled) == self._topmost:
            return
        self._topmost = bool(enabled)
        self._apply_topmost()
        self.show()
        self.raise_()   # 重显后置前，避免被新窗口标志的隐藏/重显打断

    # ------------------------------------------------------------ 运行
    def start(self) -> None:
        self._center = QPointF(self.geometry().center())
        self._target = QPointF(self._center)
        self._vel = QPointF(0.0, 0.0)
        self._clock.start()
        self._last_ms = 0
        self._last_poll_ms = 0
        self._last_frame = -1
        self._timer.start()
        self._poll.start()

    def stop(self) -> None:
        self._timer.stop()
        self._poll.stop()
        self._clock.invalidate()

    def _on_tick(self) -> None:
        if not self._clock.isValid():
            return
        elapsed = self._clock.elapsed()
        frame_index = int(elapsed // TARGET_FRAME_MS)
        if frame_index == self._last_frame:
            return
        dt_ms = elapsed - self._last_ms
        self._last_ms = elapsed
        self._last_frame = frame_index

        # 相位取自真实经过时间：掉帧只会变糙，循环不会被拉长。
        # 呼吸动画倍率 aura_scale：相位推进速度 × 倍率。
        #   0.1x → 每圈时长 = 原 20s / 0.1 = 200s（慢 10 倍）
        #   3.0x → 每圈时长 = 原 20s / 3.0 ≈ 6.67s（快 3 倍）
        scale = max(0.1, min(3.0, float(CONFIG.get("aura_scale", 1.0))))
        self._phase = (elapsed * scale % CONFIG["cycle_ms"]) / CONFIG["cycle_ms"]
        # 扇形展开 / 展开中 / 收起中途（即动态扇形可见期间）：呼吸冻结，
        # 相位停在展开瞬间；彻底回收（hidden）后继续呼吸。
        if self._breath_frozen:
            self._phase = self._frozen_phase
        try:
            self._advance_motion(dt_ms)
        except Exception as exc:  # noqa: BLE001 - 槽函数异常默认会终止进程，必须兜住
            _log_crash("motion", exc)
        self.frames_drawn += 1
        self.update()

    def _update_drag_target(self, gp: QPointF) -> bool:
        """全局光标位置 → 判定并推进拖动状态。返回 True 表示当前处于拖动中。

        按下但位移未过阈值时返回 False：那是要点扇区，必须照常走高亮评估，
        否则「按住→松开」会被吞掉，扇区永远点不中。

        拖动宽限（drag_grace_ms=500ms）：从按下起宽限期内，即使已判定为拖动，
        球体也**不响应鼠标移动**（不启动弹簧跟随、不更新目标位置），只持续推进
        扇形收起；宽限期结束后球体才从当前静止位置朝鼠标当前位置「飞」过去。
        """
        if self._press is None:
            return False
        if not self._dragging:
            # 右键次级展开的收起飞行中若开始拖动球体 → 取消次级展开（先保证拖动手感）
            if self._sub_launching:
                self._abort_sub_launch()
            grab = self._press["grab"]
            if math.hypot(gp.x() - grab.x(), gp.y() - grab.y()) < CONFIG["drag_start_px"]:
                return False
            self._dragging = True
            self._snap = None
            self._vel = QPointF(0.0, 0.0)
            if self._state != "hidden":
                self._enter_collapsing()   # 边拖边展开会让落点判定抖，先收起
        # 宽限期内：球体原地不动（不跟随），扇形由 _on_poll 继续推进收起
        if self._clock.isValid():
            hold_ms = float(self._clock.elapsed()) - self._press["t0"]
            if hold_ms < CONFIG["drag_grace_ms"]:
                self._follow = False
                return True
        # 宽限期已过：先确保扇形已完整收回（防御：若收起动画因系统卡顿未及时
        # 收完，必须在球开始飞行前强制收完——绝不出现半开扇形跟手），
        # 然后球体才从当前静止位置朝鼠标当前位置「飞」。
        if self._progress > 0.0 or self._state != "hidden":
            self._progress = 0.0
            self._on_hidden()   # 内部：state→hidden、解冻呼吸、窗口缩回 IDLE_SIZE
        self._follow = True
        off = self._press["offset"]
        self._target = QPointF(gp.x() + off.x(), gp.y() + off.y())
        return True

    def _advance_collapse_under_drag(self, dt_ms: float, elapsed_ms: float) -> None:
        """拖动期间的扇形回收推进（按住拖动回收宽限 drag_grace_ms 的落实）。

        旧缺陷：_on_poll 判定进入拖动后直接 return，跳过 _evaluate 状态机，
        收起动画（_progress 衰减 → _on_hidden 解冻/缩窗）因此不再推进——
        扇形半开、呼吸冻结，整个半开扇形跟随鼠标走。
        本方法在拖动分支里继续按 collapse_ms 推进 _progress，收完才落
        _on_hidden（解冻呼吸 + 窗口缩回 IDLE_SIZE），保证「扇形回收完成前
        不出现冻结形态」。
        仅按下后 drag_grace_ms 宽限期（500ms）内持续推进；收起动画仅需
        220ms，正常在宽限期内即收完解冻，宽限只是最迟到期的防御兜底。
        """
        if self._press is None:
            return
        if elapsed_ms - self._press["t0"] > CONFIG["drag_grace_ms"]:
            return
        if self._state in ("expanding", "open", "collapsing") and self._progress > 0.0:
            self._progress = max(0.0, self._progress - dt_ms / CONFIG["collapse_ms"])
            if self._progress <= 0.0:
                self._on_hidden()

    def _on_poll(self) -> None:
        """高频光标采样（默认 8ms）：高亮响应不再受 60fps 动画帧节流。

        用全局光标而不是鼠标事件：展开时窗口只有 220px，球一动鼠标就可能滑出窗口，
        事件口径会丢坐标；全局采样不受窗口边界影响，也不会断跟。
        """
        if not self._clock.isValid():
            return
        try:
            # dt 取「距上次采样的真实毫秒」而非固定 8ms：系统忙时采样会被推迟，
            # 写死 8ms 会让悬停 300ms 的判定越走越慢。
            elapsed = self._clock.elapsed()
            dt = max(1.0, float(elapsed - self._last_poll_ms))
            self._last_poll_ms = elapsed
            self.polls += 1
            gp = QPointF(QCursor.pos())
            if self._update_drag_target(gp):
                self._highlight = -1
                # 拖动期间持续推进扇形收起：修复「扇形没收回就冻结」的半开跟手。
                # 旧实现进入拖动后直接 return，跳过 _evaluate 状态机，收起动画
                # （_progress 衰减 → _on_hidden 解冻/缩窗）不再推进——扇形半开、
                # 呼吸冻结，整个半开扇形跟随鼠标走。现在每采样帧都把进度继续
                # 推向 zero（收起仅需 220ms，远小于用户要求的 500ms 宽限），
                # 收完（_on_hidden）后才进入干净跟手状态。
                self._advance_collapse_under_drag(dt, elapsed)
                self.update()
                return
            if self._check_hold_collapse(elapsed):
                return
            cur = QPointF(self.mapFromGlobal(QCursor.pos()))
            # 无操作计时：对球体的交互 = 悬停（光标在球内）/ 按压中（含拖动）——
            # 有任一交互即清零重计；无交互累计满 idle_sec 秒进入休眠
            half = self.width() / 2.0
            dist_c = math.hypot(cur.x() - half, cur.y() - half)
            interacting = (dist_c <= HOVER_RADIUS
                           or self._press is not None
                           or self._dragging
                           or self._snap is not None)
            self._note_idle(dt, interacting)
            self._evaluate(dt, cur)
            if self._highlight != self._poll_highlight:
                self._poll_highlight = self._highlight
                self.update()   # 高亮一改就重绘，不必等下一个动画帧
        except Exception as exc:  # noqa: BLE001
            _log_crash("poll", exc)

    # ------------------------------------------------------------ 运动：跟随 / 吸附 / 越界
    def _screen_rect(self):
        """球心所在屏幕的可用区（避开任务栏）。可用 _screen_override 注入（自检用）。"""
        if self._screen_override is not None:
            l, t, r, btm = self._screen_override
            return QRect(int(l), int(t), int(r - l), int(btm - t))
        probe = QPoint(round(self._center.x()), round(self._center.y()))
        screen = QApplication.screenAt(probe) or QApplication.primaryScreen()
        return screen.availableGeometry()

    def _edge_state(self, center: QPointF | None = None) -> dict:
        """按「扇形外边缘」（RING_OUT）计算与屏幕四边的间隙。

        gap > 0：外缘还在屏幕内；gap ≤ snap_margin：该吸附；gap < 0：已越界（负距离）。
        """
        c = center if center is not None else self._center
        rect = self._screen_rect()
        gaps = {
            "left": (c.x() - RING_OUT) - rect.left(),
            "right": rect.right() - (c.x() + RING_OUT),
            "top": (c.y() - RING_OUT) - rect.top(),
            "bottom": rect.bottom() - (c.y() + RING_OUT),
        }
        side = min(gaps, key=gaps.get)
        return {"gap": gaps[side], "side": side, "rect": rect, "gaps": gaps}

    def _snap_target(self, state: dict) -> QPointF:
        """把球心沿越界方向推回，使扇形外边缘正好贴住屏幕边（间隙 0）。"""
        c = QPointF(self._center)
        rect, side, gap = state["rect"], state["side"], state["gap"]
        if side == "left":
            c.setX(rect.left() + RING_OUT)
        elif side == "right":
            c.setX(rect.right() - RING_OUT)
        elif side == "top":
            c.setY(rect.top() + RING_OUT)
        else:
            c.setY(rect.bottom() - RING_OUT)
        # 另一轴夹进屏幕，保证整环可见（球心离上下边至少 RING_OUT；放不下则居中）
        margin_x = min(RING_OUT, (rect.width() - 2 * RING_OUT) / 2.0)
        margin_y = min(RING_OUT, (rect.height() - 2 * RING_OUT) / 2.0)
        c.setX(max(rect.left() + margin_x, min(c.x(), rect.right() - margin_x)))
        c.setY(max(rect.top() + margin_y, min(c.y(), rect.bottom() - margin_y)))
        return c

    def _advance_motion(self, dt_ms: float) -> None:
        dt = dt_ms / 1000.0

        if self._snap:
            self._snap["ms"] += dt_ms
            t = min(1.0, self._snap["ms"] / self._snap["dur"])
            e = ease_in_out_cubic(t)
            a, b = self._snap["from"], self._snap["to"]
            self._center = QPointF(a.x() + (b.x() - a.x()) * e,
                                   a.y() + (b.y() - a.y()) * e)
            self._vel = QPointF(0.0, 0.0)
            if t >= 1.0:
                self._center = QPointF(self._snap["to"])
                self._snap = None
            self._apply_center()
            if t < 1.0:
                return
            # 吸附刚结束这一帧继续往下走闪烁更新：警告圈在到位瞬间就熄灭，
            # 不会多停留一帧（此前写完 to 就 return，warn 判定被跳过了）

        if self._follow and not self._snap:
            # 临界阻尼弹簧：从静止起步慢、中段快、收尾渐停 —— 就是「慢-快-慢停」
            #
            # 必须子步积分：一帧 16.7ms 直接做显式欧拉，会在 1~2 帧内把速度冲到顶，
            # 「起步慢」被数值误差抹平（实测首帧位移 32px ≈ 峰值的 90%）。
            # 拆成 ≤4ms 子步后 ω₀·dt≈0.04，数值稳定，速度曲线回到真实形状。
            w0 = math.sqrt(CONFIG["drag_stiffness"])
            remaining = dt
            while remaining > 1e-9:
                h = min(remaining, 0.004)
                remaining -= h
                ax = w0 * w0 * (self._target.x() - self._center.x()) - 2.0 * w0 * self._vel.x()
                ay = w0 * w0 * (self._target.y() - self._center.y()) - 2.0 * w0 * self._vel.y()
                self._vel = QPointF(self._vel.x() + ax * h, self._vel.y() + ay * h)
                self._center = QPointF(self._center.x() + self._vel.x() * h,
                                       self._center.y() + self._vel.y() * h)
            self._apply_center()

        # 定位闪烁：「在哪儿？」红框定位，沿 RING_OUT 按 warn_period_ms 节拍闪烁，
        # 持续 _locate_total_ms 后自动结束；期间越界警告让位，结束后恢复。
        if self._locate_left_ms > 0:
            self._locate_left_ms -= dt_ms
            phase_ms = self._locate_total_ms - self._locate_left_ms
            half_p = CONFIG["warn_period_ms"] / 2.0
            self._warn_ms = phase_ms
            self._warn_on = int(phase_ms // half_p) % 2 == 0
            if self.width() != ACTIVE_SIZE:      # 红圈画到 RING_OUT，窗口得够大才不被裁
                self._resize_keep_center(ACTIVE_SIZE)
            if self._locate_left_ms <= 0.0:
                self._locate_left_ms = 0.0
                self._warn_on = False
                self._warn_ms = 0.0
                if self._state == "hidden" and self.width() != IDLE_SIZE:
                    self._resize_keep_center(IDLE_SIZE)
            return
        # 越界提示：扇形外边缘与屏幕边出现负距离 → 闪烁
        st = self._edge_state()
        if st["gap"] < 0.0:
            self._warn_ms += dt_ms
            half_p = CONFIG["warn_period_ms"] / 2.0
            self._warn_on = int(self._warn_ms // half_p) % 2 == 0
            if self.width() != ACTIVE_SIZE:      # 警告圈画到 RING_OUT，窗口得够大才不被裁
                self._resize_keep_center(ACTIVE_SIZE)
        else:
            self._warn_ms = 0.0
            self._warn_on = False
            if self._state == "hidden" and self.width() != IDLE_SIZE:
                self._resize_keep_center(IDLE_SIZE)

    def _apply_center(self) -> None:
        """窗口位置由球心推出。球心是全程唯一坐标真源，尺寸变化不影响它。"""
        self.move(round(self._center.x() - self.width() / 2.0),
                  round(self._center.y() - self.height() / 2.0))

    def place_at(self, x: float, y: float) -> None:
        """把球心放到全局坐标 (x, y) 并同步窗口（start() 与自检共用）。"""
        self._center = QPointF(x, y)
        self._target = QPointF(x, y)
        self._vel = QPointF(0.0, 0.0)
        self._apply_center()

    def _check_hold_collapse(self, elapsed_ms: float) -> bool:
        """按住球体超过 press_hold_ms 且未拖动 → 判定用户要拖球，回收动态扇形。

        展开后按住不动超时：移动球体是大概率意图，先把扇形收掉避免遮挡。
        返回 True 表示本次采样已触发回收（调用方不再走悬停评估）。
        """
        if self._press is None or self._dragging:
            return False
        held = elapsed_ms - self._press["t0"]
        if held < CONFIG["press_hold_ms"] or self._state == "hidden" \
                or self._hold_collapsed:
            return False
        self._hold_collapsed = True
        self._enter_collapsing()
        self._highlight = -1
        self.update()
        return True

    # ------------------------------------------------------------ 动效休眠
    def _enter_sleep(self) -> None:
        """进入休眠：按系统深浅色设定顺色渲染（浅色→黑，深色→白），
        呼吸暂停 + 整个窗口透明度降到 10%。

        扇形若展开则立即回收（休眠时几乎不可见，直接收起避免半展开卡住）。
        必须把 _progress 清零——否则 paintEvent 里 _progress > 0.0 仍成立，
        会在已缩回的小窗口上继续绘制扇形（残缺冻结形态）。
        """
        if self._asleep:
            return
        # 休眠前取消等待中的次级展开（若有）：避免残留定时器在休眠中意外展开
        if self._sub_launching:
            self._abort_sub_launch()
        self._asleep = True
        self._breath_frozen = True
        self._frozen_phase = self._phase
        # 休眠配色：浅色系统用黑球、深色系统用白球，与背景反色、淡入淡出可见
        self._sleep_color = QColor("#FFFFFF" if system_dark_mode() else "#000000")
        self._progress = 0.0
        self._highlight = -1
        self._hover_ms = 0.0
        self._out_ms = 0.0
        if self._state != "hidden":
            self._state = "hidden"
            self._resize_keep_center(IDLE_SIZE)
        try:
            # 休眠窗口透明度 = sleep_opacity%（1~20，配置读取时已归一）
            sleep_op = float(CONFIG.get("sleep_opacity", 10))
            sleep_op = max(1.0, min(20.0, sleep_op)) / 100.0
            self.setWindowOpacity(sleep_op)
        except Exception as exc:  # noqa: BLE001
            _log_crash("sleep_opacity", exc)
        self.update()

    def _wake(self) -> None:
        """点击球体唤起：透明度恢复、呼吸解冻、计时清零。"""
        if not self._asleep:
            self._idle_ms = 0.0
            return
        self._asleep = False
        self._breath_frozen = False
        self._idle_ms = 0.0
        try:
            self.setWindowOpacity(1.0)
        except Exception as exc:  # noqa: BLE001
            _log_crash("wake_opacity", exc)
        self.update()

    def locate_here(self) -> None:
        """「在哪儿？」定位：唤起球体 + 红框闪烁（跨进程定位指令回调）。

        若球体处于休眠先唤起；随后让红虚线外圈沿 RING_OUT 闪烁约 2 秒，
        窗口放大到 ACTIVE_SIZE 保证外圈完整可见。复用越界警告的 _warn_on/
        _warn_ms 驱动与 paintEvent 红圈绘制，定位期间越界警告让位。
        """
        try:
            self._wake()
            # 触发红圈显示：定位状态期间 _warn_on 跟随闪烁节拍置位
            self._locate_left_ms = self._locate_total_ms
            self._resize_keep_center(ACTIVE_SIZE)
            self.update()
        except Exception as exc:  # noqa: BLE001
            _log_crash("locate_here", exc)

    def _note_idle(self, dt_ms: float, interacting: bool) -> None:
        """无操作计时：对球体有任何交互（悬停/点击/拖拽）则清零；否则累计，
        达到 idle_sec 秒（10~600，默认 60）进入休眠。休眠中不重复计时。"""
        if interacting:
            self._idle_ms = 0.0
            return
        if self._asleep:
            return
        self._idle_ms += dt_ms
        limit_ms = max(10.0, float(CONFIG.get("idle_sec", 60))) * 1000.0
        if self._idle_ms >= limit_ms:
            self._idle_ms = 0.0
            self._enter_sleep()

    # ------------------------------------------------------------ 状态机
    def _evaluate(self, dt_ms: float, cursor: QPointF) -> None:
        """采样间隔 + 鼠标相对窗口中心坐标 → 高亮、悬停计时与展开/收起状态机。

        用全局光标轮询而不是 enterEvent：窗口是方的、球是圆的，
        事件口径会把角落透明区也算成悬停，判定半径必须自己算。
        """
        if self._flash:
            self._flash["ms"] += dt_ms
            if self._flash["ms"] >= self._flash["dur"]:
                self._flash = None

        # 休眠期间：不累计悬停、不高亮、不展开，仅保持低透明度静置
        if self._asleep:
            self._hover_ms = 0.0
            self._highlight = -1
            self._poll_highlight = -1
            return

        half = self.width() / 2.0
        dx, dy = cursor.x() - half, cursor.y() - half
        dist = math.hypot(dx, dy)

        in_ball = dist <= HOVER_RADIUS
        in_ring = self._ring_inner() <= dist <= RING_OUT + CONFIG["ring_pad"]
        # 拖动 / 吸附中：不给高亮、不计悬停，但收起动画必须继续推进，
        # 否则扇形会停在半展开状态（progress 不再衰减）。
        if self._dragging or self._snap:
            self._highlight = -1
            if self._state == "collapsing":
                self._progress = max(0.0, self._progress - dt_ms / CONFIG["collapse_ms"])
                if self._progress <= 0.0:
                    self._on_hidden()
            return
        self._highlight = sector_index_at(dx, dy, len(self._sectors)) if in_ring else -1

        if self._state == "hidden":
            # 按住超时回收后（仍按住）禁止悬停重开；松开后 _hold_collapsed 复位
            if self._hold_collapsed and self._press is not None:
                self._hover_ms = 0.0
                return
            self._hover_ms = self._hover_ms + dt_ms if in_ball else 0.0
            if self._hover_ms >= CONFIG["hover_trigger_ms"]:
                self._enter_expanding()
        elif self._state == "expanding":
            self._progress = min(1.0, self._progress + dt_ms / CONFIG["expand_ms"])
            self._settle_out(in_ball, in_ring, dt_ms)
            if self._progress >= 1.0:
                self._state = "open"
        elif self._state == "open":
            self._settle_out(in_ball, in_ring, dt_ms)
        elif self._state == "collapsing":
            # 收起途中鼠标回球/环 → 立即重新展开，不重等 300ms；冻结色按当时本色刷新
            # 例外1：长按回收（_hold_collapsed 且仍按住）期间不得重开，否则扇形
            #   刚收起就被展开——此前该分支未排除长按场景，导致回收永远不生效。
            # 例外2：次级展开的收起飞行（_sub_launching）同样不得重开，必须等
            #   收起完成 + 300ms 延迟，否则次级环会在未完全收回时被重开打断。
            if in_ball or in_ring:
                if (self._hold_collapsed and self._press is not None) or self._sub_launching:
                    self._hover_ms = 0.0
                else:
                    self._state = "expanding"
                    self._fill_color = center_color_at(self._phase)
                    self._frozen_phase = self._phase
                    self._breath_frozen = True
                    return
            self._progress = max(0.0, self._progress - dt_ms / CONFIG["collapse_ms"])
            if self._progress <= 0.0:
                self._on_hidden()

    def _ring_inner(self) -> float:
        """环内判定边界：展开态用环内缘；收起态鼠标只要挨着球就算「在环上」。"""
        return RING_IN if self._progress > 0.5 else 0.0

    def _settle_out(self, in_ball: bool, in_ring: bool, dt_ms: float) -> None:
        if in_ball or in_ring:
            self._out_ms = 0.0
            return
        self._out_ms += dt_ms
        if self._out_ms >= CONFIG["leave_collapse_ms"]:
            self._enter_collapsing()

    def _enter_expanding(self) -> None:
        """展开那一刻取球心本色，整个展开期间冻结（颜色+呼吸都停）。"""
        self._fill_color = center_color_at(self._phase)
        self._frozen_phase = self._phase
        self._breath_frozen = True
        self._state = "expanding"
        self._out_ms = 0.0
        self._hover_ms = 0.0
        if self.width() != ACTIVE_SIZE:
            self._resize_keep_center(ACTIVE_SIZE)

    def _enter_collapsing(self) -> None:
        self._state = "collapsing"
        self._highlight = -1

    def _on_hidden(self) -> None:
        """扇形彻底回收：呼吸解冻，从此刻的相位继续（视觉连续）。

        次级环场景：若正在等待次级展开（收起飞行中），此处不恢复主列表——
        延迟展开回调会替换 `_sectors` 并重新展开；若次级环完成点击/离开被收回
        （_sub_sectors 非 None 且非等待展开），则恢复主列表。
        """
        was_waiting_sub = bool(self._sub_launching)
        self._state = "hidden"
        self._hover_ms = 0.0
        self._breath_frozen = False
        self._resize_keep_center(IDLE_SIZE)
        if was_waiting_sub:
            return   # 等待延迟展开：_open_sub_ring 负责换列表并展开
        # 若此前展开的是次级环，收起完成 → 恢复主列表
        if self._sub_sectors is not None:
            self._sectors = self._main_sectors
            self._sub_sectors = None
            self._sub_launching = False
            self._sub_launching_aborted = False
            self._sub_pending_items = None

    def _resize_keep_center(self, size: int) -> None:
        self.setFixedSize(size, size)
        self._apply_center()

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, event) -> None:  # noqa: N802
        try:
            painter = QPainter(self)
            if self._asleep:
                # 休眠：按纵深色渲染（浅色系统→黑，深色系统→白）
                paint_ball(painter, self.width(), self._phase,
                           override_color=getattr(self, "_sleep_color", QColor("#000000")))
            else:
                paint_ball(painter, self.width(), self._phase)
            if self._progress > 0.0:
                paint_ring(painter, self.width(), self._sectors, self._progress,
                           self._highlight, self._fill_color, self._flash)
            if self._warn_on:
                # 越界提示：沿扇形外缘画一圈红色虚线，提醒松手后会回位
                c = QPointF(self.width() / 2.0, self.height() / 2.0)
                pen = QPen(_FAIL_RED, 2.0, Qt.PenStyle.DashLine)
                pen.setDashOffset(self._warn_ms / 40.0)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(c, RING_OUT, RING_OUT)
            painter.end()
        except Exception as exc:  # noqa: BLE001 - 绘制异常绝不能带走进程
            _log_crash("paintEvent", exc)

    # ------------------------------------------------------------ 交互：拖动与吸附
    def mousePressEvent(self, event) -> None:  # noqa: N802
        """左键按住球体：记下抓取偏移，等待移动阈值判定是点击还是拖动。

        右键：环上命中扇区 → 次级动态扇形（无标记闪红 / 有标记收起后展开）；
        球体或环外空白 → 交给 contextMenuEvent 的默认菜单处理。
        """
        try:
            # 右键：优先判定环上扇区的「次级动态扇形」触发
            if event.button() == Qt.MouseButton.RightButton:
                self._on_right_press(event)
                return
            if event.button() != Qt.MouseButton.LeftButton:
                return
            # 系统判定本击不是双击（若是双击，第二击会以 MouseButtonDblClick 派发、
            # 不会走到这里）；若此前还有未确认的单击，先确认执行——它已独立成一次单击。
            if self._click_pending is not None:
                self._commit_click()
            g = QPointF(event.globalPosition().toPoint())
            dist = math.hypot(g.x() - self._center.x(), g.y() - self._center.y())
            if dist > HOVER_RADIUS:      # 只允许抓球体本身，抓环不移动
                return
            self._wake()                 # 点击球体 → 从休眠唤起（透明度恢复、计时清零）
            self._press = {"grab": QPointF(g),
                           "offset": QPointF(self._center) - g,
                           "t0": float(self._clock.elapsed())}
            self._dragging = False
            self._follow = False
        except Exception as exc:  # noqa: BLE001
            _log_crash("mousePressEvent", exc)

    def _on_right_press(self, event) -> None:
        """右键环上扇区 → 次级动态扇形处理。

        · 鼠标在展开环上且命中扇区：
            - 该扇区指令无【【【…】】】标记 → 复用失败红闪（无下级功能）；
            - 有标记（成对）→ 记录待展开次级项 → 收起主环 → 延迟 300ms
              后顺时针整环展开次级环；
        · 无论在哪种状态，非环上命中（球体/空白）→ 不吞事件，放行默认右键菜单。
        """
        # 收起中且正在等次级展开：忽略重复右键
        if self._sub_launching:
            return
        if self._state not in ("open", "expanding") or self._highlight < 0:
            return   # 未展开 / 不在环上 → 保留默认右键菜单
        i = self._highlight
        sec = self._sectors[i]
        self._ctx_consumed = True   # 环上右键一律替换默认菜单
        if sec.get("placeholder"):
            # 占位扇区：闪红提示无下级功能
            self._flash = {"sector": i, "ok": False, "ms": 0.0,
                           "dur": CONFIG["flash_ms_fail"]}
            return
        parsed = parse_special_sections(sec["code"])
        if parsed["err"]:
            # 标记校验失败（不成对等）→ 闪红并以 stderr 提示，避免静默
            print(f"[次级扇形] {sec['name']} 标记异常：{parsed['err']}", file=sys.stderr)
            self._flash = {"sector": i, "ok": False, "ms": 0.0,
                           "dur": CONFIG["flash_ms_fail"]}
            return
        if not parsed["items"]:
            # 无标记：复用失败红闪（无下级功能）
            self._flash = {"sector": i, "ok": False, "ms": 0.0,
                           "dur": CONFIG["flash_ms_fail"]}
            return
        # 有标记：记录待展开项 → 收起主环 → 延迟 300ms 展开次级环
        self._sub_pending_items = parsed["items"]
        self._sub_launching = True
        self._enter_collapsing()
        # 收起完成后（_on_hidden）触发延迟展开
        self._sub_timer = QTimer(self)
        self._sub_timer.setSingleShot(True)
        self._sub_timer.setInterval(300)
        self._sub_timer.timeout.connect(self._open_sub_ring)
        self._sub_timer.start()

    def _open_sub_ring(self) -> None:
        """300ms 延迟后：把 _sectors 替换为次级项并展开（渲染/命中/执行全复用）。"""
        self._sub_timer = None
        if self._sub_launching_aborted or self._sub_pending_items is None:
            self._sub_pending_items = None
            self._sub_launching = False
            self._sub_launching_aborted = False
            return
        # 收起尚未完成（如被拖动/休眠打断）→ 取消本次次级展开
        if self._state != "hidden":
            self._sub_pending_items = None
            self._sub_launching = False
            self._sub_launching_aborted = False
            return
        self._sub_sectors = [{
            "name": it["name"],
            "code": it["code"],
            "placeholder": not it["code"].strip(),
        } for it in self._sub_pending_items]
        if len(self._sub_sectors) < MIN_SECTORS:   # 补齐到最少 2 个占位
            while len(self._sub_sectors) < MIN_SECTORS:
                self._sub_sectors.append({"name": f"空位 {len(self._sub_sectors) + 1}",
                                          "code": "", "placeholder": True})
        self._sectors = self._sub_sectors
        self._sub_pending_items = None
        self._sub_launching = False
        self._sub_launching_aborted = False
        self._enter_expanding()

    def _abort_sub_launch(self) -> None:
        """取消等待中的次级展开（右键后收起飞行内被拖动/其他强交互打断时）。"""
        self._sub_launching = False
        self._sub_launching_aborted = True
        self._sub_pending_items = None
        if self._sub_timer is not None:
            try:
                self._sub_timer.stop()
            except RuntimeError:
                pass
            self._sub_timer = None

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        """按住移动超过阈值即进入拖动：球心用临界阻尼弹簧跟随鼠标轨迹。

        真正的跟随在 _on_poll 里以 8ms 高频更新——窗口比球大得有限，
        球一旦跑在鼠标前面，这里就再也收不到移动事件了。
        """
        try:
            self._update_drag_target(QPointF(event.globalPosition().toPoint()))
        except Exception as exc:  # noqa: BLE001
            _log_crash("mouseMoveEvent", exc)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        """松手：拖动过则按边缘距离吸附或回位；没拖动则登记一次待确认的单击。

        单击执行采用「延迟确认」以彻底删除双击功能（详见 _arm_click）。
        """
        try:
            if event.button() != Qt.MouseButton.LeftButton:
                return
            # 双击第二击的抬起：DoubleClick 事件已判定并取消失效，
            # 本击不再产生任何动作。
            if self._dbl_click_seen:
                self._dbl_click_seen = False
                self._press = None
                self._dragging = False
                self._follow = False
                self._hold_collapsed = False
                return
            dragged = self._dragging
            self._press = None
            self._dragging = False
            self._follow = False
            self._warn_on = False
            self._warn_ms = 0.0
            self._hold_collapsed = False   # 松开后按住回收状态复位，可再正常悬停/点击
            if dragged:
                self._finish_drag()
                return
            if self._state in ("open", "expanding") and self._highlight >= 0:
                self._arm_click(self._highlight)   # 单击确认后才执行（双击不执行）
        except Exception as exc:  # noqa: BLE001
            _log_crash("mouseReleaseEvent", exc)

    # ---------- 双击拦截 / 单击确认 ----------
    def _arm_click(self, sector: int) -> None:
        """登记一次待确认的扇区单击，等待双击判定窗口过去后才真正执行。

        双击由「第一击抬起 + 第二击按下（以 MouseButtonDblClick 派发）」组成，
        Qt 判定双击发生在第二击按下时，晚于第一击抬起。若第一击抬起就立即
        执行扇区，双击会连带执行一次。因此把执行延后到双击窗口结束：
          · 窗口内收到 DoubleClick 事件 → _cancel_pending_click() 取消，双击零效果；
          · 窗口内无第二击 → _commit_click() 确认单击并执行。
        代价：单击执行延迟约（系统双击间隔 + 30ms），换取双击完全不触发。
        """
        if self._click_timer.isActive() and self._click_pending is not None:
            self._commit_click()          # 防御：旧的待确认先落地
        self._click_pending = sector
        self._click_timer.start(int(QApplication.doubleClickInterval()) + 30)

    def _cancel_pending_click(self) -> None:
        """取消待确认的单击（双击第二击已到达 / 其他原因放弃）。"""
        self._click_timer.stop()
        self._click_pending = None

    def _commit_click(self) -> None:
        """双击窗口已过，确认刚才的松开是一次单击 → 执行登记的扇区。"""
        self._click_timer.stop()
        hl = self._click_pending
        self._click_pending = None
        if hl is None:
            return
        try:
            self.trigger_sector(hl)
        except Exception as exc:  # noqa: BLE001
            _log_crash("commit_click", exc)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """系统判定为双击：取消首击登记的单击，双击整体不产生任何动作。

        双击功能已删除，只保留单击事件；本处理不包含退出或任何其他行为。
        """
        try:
            if event.button() == Qt.MouseButton.LeftButton:
                self._cancel_pending_click()
                self._dbl_click_seen = True   # 第二击的抬起需要整段吞掉
        except Exception as exc:  # noqa: BLE001
            _log_crash("mouseDoubleClickEvent", exc)

    def _finish_drag(self) -> None:
        """松手后决定去向：越界 → 回到最近合法位；边缘 50px 内 → 吸附贴边；否则就地停。

        判定用「扇形外边缘」而不是球边缘：这样吸附后打开扇形，整环仍完整可见。
        """
        self._vel = QPointF(0.0, 0.0)
        self._target = QPointF(self._center)
        st = self._edge_state()
        if st["gap"] < 0.0:                       # 负距离：已越界，回最近吸附位
            to = self._snap_target(st)
        elif st["gap"] <= CONFIG["snap_margin"]:  # ≤50px：非线性靠边吸附
            to = self._snap_target(st)
        else:
            to = QPointF(self._center)            # 就地停，不吸
        if math.hypot(to.x() - self._center.x(), to.y() - self._center.y()) < 0.5:
            self._apply_center()
            return
        self._snap = {"from": QPointF(self._center), "to": to,
                      "ms": 0.0, "dur": CONFIG["snap_anim_ms"]}

    def trigger_sector(self, i: int) -> None:
        """执行一个扇区：占位/空代码只收起；正常扇区起闪光并派发代码。

        含特殊标记的扇区：左键执行主指令（第一个标记前），主指令为空则只收起
        （占位语义）。执行 fire-and-forget，主程序绝不等待子进程（避坑 3）。
        """
        self._enter_collapsing()
        sec = self._sectors[i]
        if sec["placeholder"]:
            return
        code = sec["code"]
        # 含成对特殊标记（次级功能）时，左键只执行「主指令」（标记前）；无标记原样
        parsed = parse_special_sections(code)
        if parsed["err"]:
            err = parsed["err"]
            run_ok = False
        elif parsed["items"]:
            main_code = parsed["main"]
            if not main_code.strip():
                return   # 仅含次级功能、主指令为空 → 只收起不执行、不闪红
            try:
                err = run_code(main_code)
            except Exception as exc:  # noqa: BLE001
                _log_crash("run_code", exc)
                err = f"启动失败：{exc}"
            run_ok = err is None
        else:
            try:
                err = run_code(code)
            except Exception as exc:  # noqa: BLE001
                _log_crash("run_code", exc)
                err = f"启动失败：{exc}"
            run_ok = err is None
        if not run_ok:
            print(f"[执行] {sec['name']} 未执行：{err}", file=sys.stderr)
        self._flash = {
            "sector": i,
            "ok": run_ok,
            "ms": 0.0,
            "dur": CONFIG["flash_ms_ok"] if run_ok else CONFIG["flash_ms_fail"],
        }

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        try:
            # 环上右键已被「次级动态扇形」处理（展开/闪红）→ 不弹默认菜单
            if self._ctx_consumed:
                self._ctx_consumed = False
                return
            menu = QMenu(self)
            act_settings = menu.addAction("设置")
            act_reload = menu.addAction("重载")
            act_disappear = menu.addAction("消失")
            act_quit = menu.addAction("退出")
            chosen = menu.exec(event.globalPos())
            if chosen is act_settings:
                # 崩溃根因修复：绝不能在 QMenu.exec 的模态栈还没退出时再嵌一层
                # QDialog.exec —— 嵌套模态窗在 Windows 上会中断主事件循环、程序整体退出。
                # 用 singleShot(0) 把对话框推迟到本函数返回、菜单模态完全结束后再开。
                QTimer.singleShot(0, self.open_settings)
            elif chosen is act_reload:
                self.reload_config()
            elif chosen is act_disappear:
                # 消失：立即触发休眠（呼吸暂停 + 透明度 10%），点击球体即唤起
                if self._asleep:
                    self._wake()
                else:
                    self._enter_sleep()
            elif chosen is act_quit:
                QApplication.quit()
        except Exception as exc:  # noqa: BLE001
            _log_crash("contextMenuEvent", exc)

    def open_settings(self) -> None:
        try:
            # 去重：设置窗口已打开则前台该窗口，未打开才新建（与帮助/动效一致）
            if self._settings_dlg is not None and self._settings_dlg.isVisible():
                self._settings_dlg.raise_()
                self._settings_dlg.activateWindow()
                return
            dlg = SettingsDialog(read_all_rows(), self)
            dlg.setModal(False)      # 用 show 栈式打开，进一步避免嵌套模态循环
            dlg.finished.connect(self._on_settings_closed)
            self._settings_dlg = dlg  # 保引用，防止被回收
            dlg.show()
            dlg.raise_()
            # 智能定位：弹窗靠球体的最大外框摆放（等窗口尺寸定稿后执行）
            QTimer.singleShot(0, lambda: _place_dialog_near_rect(
                dlg, self._outer_frame_rect(), self))
        except Exception as exc:  # noqa: BLE001
            _log_crash("open_settings", exc)

    def _outer_frame_rect(self) -> QRect:
        """球体最大外框线：收起时是球本体+光晕的矩形，展开扇形时是 RING_OUT 圆的外接矩形。"""
        r = RING_OUT if self._state != "hidden" else IDLE_SIZE / 2.0
        c = self._center
        return QRect(round(c.x() - r), round(c.y() - r), round(2 * r), round(2 * r))

    def _on_settings_closed(self, result: int) -> None:
        try:
            if result == int(QDialog.DialogCode.Accepted):
                self.reload_config()
                self.apply_topmost_setting(topmost_enabled())   # 置顶即时生效
                self.update()
            self._settings_dlg = None
        except Exception as exc:  # noqa: BLE001
            _log_crash("settings_closed", exc)

    def reload_config(self) -> None:
        self._sectors = load_functions()
        self._main_sectors = list(self._sectors)   # 主列表同步刷新（含次级环状态则丢弃）
        self._highlight = -1
        self.update()

    # 双击不触发任何操作：此前双击（含双击展开的扇形）会直接退出程序，
    # 属于误触隐患；移除退出逻辑后，双击与单击保持相同行为（单击仍正常执行扇区）。

    def closeEvent(self, event) -> None:  # noqa: N802
        # 关闭前保存球心坐标（下次启动按记录打开）；失败不打断退出
        try:
            save_ball_position(self._center.x(), self._center.y())
        except Exception as exc:  # noqa: BLE001
            _log_crash("close_save_pos", exc)
        # 只删已经跑完的临时脚本，绝不等待仍在运行的子进程
        for proc, path in list(SESSION_RUNNERS):
            if proc.poll() is not None:
                try:
                    os.remove(path)
                except OSError:
                    pass
        super().closeEvent(event)

    # ------------------------------------------------------------ 自检接口
    def set_phase(self, phase: float) -> None:
        self._phase = phase % 1.0
        self.repaint()

    @property
    def fill_color(self) -> QColor:
        return self._fill_color

    @property
    def state(self) -> str:
        return self._state

    @property
    def flash(self) -> dict | None:
        return self._flash


def _place_dialog_near_rect(dlg: QWidget, target: QRect, anchor: "AuraBall | None" = None):
    """把弹窗摆到目标矩形（球体最大外框）旁边，弹窗边缘与目标矩形相切相交。

    选「目标中心到屏幕四边余量最大」的方向弹出；若该侧被切，自动换另一方向。
    二级弹窗相对设置窗口用同一函数定位（同高度保持由调用方保证）。
    """
    screen = anchor._screen_rect() if anchor is not None else QApplication.primaryScreen().availableGeometry()
    cx = target.left() + target.width() / 2.0
    cy = target.top() + target.height() / 2.0
    gaps = {
        "left": cx - screen.left(),
        "right": screen.right() - cx,
        "top": cy - screen.top(),
        "bottom": screen.bottom() - cy,
    }
    side = max(gaps, key=gaps.get)
    w, h = dlg.width(), dlg.height()

    if side == "left":
        x, y = round(target.left() - w), round(cy - h / 2.0)
    elif side == "right":
        x, y = round(target.right() + 1), round(cy - h / 2.0)
    elif side == "top":
        x, y = round(cx - w / 2.0), round(target.top() - h)
    else:
        x, y = round(cx - w / 2.0), round(target.bottom() + 1)

    # 夹进屏幕：优先保持所选方向；夹不进去时回退到相反方向重试
    if x < screen.left() + 4 or x + w > screen.right() - 4:
        x = round(target.right() + 1) if side == "left" else round(target.left() - w)
    if y < screen.top() + 4 or y + h > screen.bottom() - 4:
        y = round(target.bottom() + 1) if side == "top" else round(target.top() - h)
    x = max(screen.left() + 4, min(x, screen.right() - w - 4))
    y = max(screen.top() + 4, min(y, screen.bottom() - h - 4))
    dlg.move(x, y)


def _selftest() -> int:
    print(f"收起 {IDLE_SIZE}px  展开 {ACTIVE_SIZE}px  感应半径 {HOVER_RADIUS:.0f}px  "
          f"环 {RING_IN:.0f}→{RING_OUT:.0f}px  光晕 +{HALO_PX:.0f}px")
    sectors = load_functions()
    print(f"\n圆环 {len(sectors)} 等分 → " +
          " | ".join(f"{s['name']}{'(灰)' if s['placeholder'] else ''}" for s in sectors))
    for text in ("print(1)", "cmd: dir", "ps:\nGet-Date", "powershell: ls", "import os\n!!bad!!"):
        kind, payload, err = classify_code(text)
        print(f"  代码 {text!r:30} → kind={kind} err={err or '无'}")
    return 0


# ---------------------------------------------------------------- 单实例防护
_SINGLE_KEY = "AuraPixie_SingleInstance_v1"

# —— 跨进程「在哪儿？」定位唤醒（QLocalServer/QLocalSocket 命名管道）——
# 首实例监听固定管道名；第二实例点「在哪儿？」时连接管道发送 LOCATE 指令，
# 首实例收到后唤起球体并闪烁红框定位。纯 Qt 实现，事件循环内联、异常安全。
_LOCATE_PIPE = "Aura_Pixie_Locate_Pipe"
_LOCATE_MSG = "LOCATE"
# 保持引用：QLocalServer 必须存活到进程结束，否则被 GC 回收后监听即失效。
_locate_server_keep = None
_locate_callback: object | None = None   # 收到的定位回调（AuraBall.locate_here）


def _start_locate_server(callback) -> bool:
    """首实例启动定位监听（命名管道）。callback() 在收到 LOCATE 指令时调用。
    返回 True=监听成功；失败返回 False（不影响主程序运行）。"""
    global _locate_server_keep, _locate_callback
    _locate_callback = callback
    try:
        # 清理上次会话可能遗留的管道（进程异常退出时命名管道不会被自动删除）
        QLocalServer.removeServer(_LOCATE_PIPE)
        server = QLocalServer()
        if not server.listen(_LOCATE_PIPE):
            _log_crash("locate_server_listen",
                       RuntimeError(server.errorString()))
            return False
        server.newConnection.connect(lambda: _on_locate_client(server))
        _locate_server_keep = server
        return True
    except Exception as exc:  # noqa: BLE001
        _log_crash("locate_server", exc)
        return False


def _on_locate_client(server: QLocalServer) -> None:
    """新客户端连接：挂载 readyRead 读取发送内容。"""
    try:
        conn = server.nextPendingConnection()
        if conn is None:
            return
        conn.readyRead.connect(lambda: _on_locate_payload(conn))
    except Exception as exc:  # noqa: BLE001
        _log_crash("locate_client", exc)


def _on_locate_payload(conn: QLocalSocket) -> None:
    """收到客户端数据：LOCATE 指令 → 回调唤起+定位；随后断开连接。"""
    try:
        data = bytes(conn.readAll()).decode("utf-8", "ignore").strip()
        if data == _LOCATE_MSG and _locate_callback is not None:
            try:
                _locate_callback()
            except Exception:  # noqa: BLE001
                pass
        conn.disconnectFromServer()
    except Exception as exc:  # noqa: BLE001
        _log_crash("locate_payload", exc)


def _send_locate() -> bool:
    """向运行中的首实例发送「在哪儿？」定位唤醒指令（命名管道）。成功返回 True。"""
    try:
        sock = QLocalSocket()
        sock.connectToServer(_LOCATE_PIPE,
                             QLocalSocket.OpenModeFlag.WriteOnly)
        if not sock.waitForConnected(1500):
            return False
        sock.write(_LOCATE_MSG.encode("utf-8"))
        sock.flush()
        ok = sock.waitForBytesWritten(1500)
        sock.disconnectFromServer()
        return ok
    except Exception as exc:  # noqa: BLE001
        _log_crash("locate_send", exc)
        return False


def acquire_single_instance(shared: QSharedMemory) -> bool:
    """返回 True=独占成功（首个实例）；False=检测到另一实例在运行。

    二次启动时创建者应向用户弹出「本球在此」提示并退出（由调用方负责弹窗）。
    竞态兜底：create 报 AlreadyExists 时再 attach 验证是否真有活着实例，
    残留锁（无进程）不阻断本次启动。
    """
    if shared.attach():
        shared.detach()
        return False
    if not shared.create(1):
        if shared.error() == QSharedMemory.SharedMemoryError.AlreadyExists:
            if shared.attach():
                shared.detach()
                return False
            print("[单实例] 检测到残留锁，继续启动", file=sys.stderr)
    return True


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return _selftest()

    cleanup_runner_dir()

    def _hook(exc_type, exc, tb):
        import traceback
        msg = "".join(traceback.format_exception(exc_type, exc, tb))
        print(f"[未捕获异常]\n{msg}", file=sys.stderr)
        try:
            with open(os.path.join(BASE_DIR, "crash.log"), "a", encoding="utf-8") as fh:
                fh.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} unhandled ===\n{msg}")
        except OSError:
            pass

    sys.excepthook = _hook
    sys.unraisablehook = lambda unis: _log_crash("unraisable", unis.exc_value)

    app = QApplication(argv)
    # 关键：窗口短暂被关闭/重建（例如弹出设置、菜单）时绝不自动退出事件循环。
    # 上次「点保存程序整体消失」的直接原因之一就是最后可见窗口判定把应用带走了。
    app.setQuitOnLastWindowClosed(False)

    # 单实例防护：二次启动弹出「在哪儿？/看到了！」提示后退出
    guard = QSharedMemory(_SINGLE_KEY)
    if not acquire_single_instance(guard):
        # 第二个实例：提示用户球已在运行。「在哪儿？」→ 发定位指令给首实例
        # 让球体唤起并闪烁红框；「看到了！」→ 直接关闭弹窗结束。
        box = QMessageBox(None)
        box.setWindowTitle("Aura Pixie")
        box.setText("本球在此")
        box.setIcon(QMessageBox.Icon.Information)
        btn_where = box.addButton("在哪儿？", QMessageBox.ButtonRole.AcceptRole)
        btn_seen = box.addButton("看到了！", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_seen)
        box.exec()
        try:
            if box.clickedButton() is btn_where:
                _send_locate()
            elif box.clickedButton() is btn_seen:
                pass   # 用户已看到球，直接结束
        except Exception as exc:  # noqa: BLE001 - 定位失败不阻断退出
            _log_crash("locate_send", exc)
        return 0
    app._single_instance_guard = guard   # 持有引用防 GC 注销锁

    # 统一配置初始化：主位置 %LOCALAPPDATA%\Aura_Pixie\setting.yaml——
    # 有则直接载入；无则创建目录；再查 exe 根目录 *.yaml/*.yaml.bak（内容功能块
    # 合法者）合并载入并把根源改为 *.yaml.bak_done；都没有则保持空目录待首次写入
    ensure_settings()

    # 开机自启动自我保护：比对配置与注册表，被清理/误删则自动修正（不打扰、不打断）
    autostart_self_heal()

    # 启动即套用光效设置（setting.yaml 的 aura 块；缺失回退默认三段色）
    apply_aura_to_config(load_aura_config())
    ball = AuraBall()
    screen = QApplication.primaryScreen().availableGeometry()
    saved = saved_ball_position()
    if saved is not None:
        # 有位置记录：按上次球心摆放；分辨率/显示器变化导致越界时夹进可用区保球可见
        sx, sy = saved
        r = IDLE_SIZE / 2.0
        sx = max(screen.left() + r, min(sx, screen.right() - r))
        sy = max(screen.top() + r, min(sy, screen.bottom() - r))
    else:
        # 首次运行 / 无位置记录：屏幕右侧中间、扇形外缘贴右边缘吸附
        sx = screen.right() - RING_OUT
        sy = screen.center().y()
    ball.place_at(sx, sy)
    ball.show()
    ball.start()
    # 启动跨进程定位监听：第二实例点「在哪儿？」时经命名管道唤起本球
    try:
        _start_locate_server(ball.locate_here)
    except Exception as exc:  # noqa: BLE001 - 定位监听失败不影响主程序
        _log_crash("register_locate", exc)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
