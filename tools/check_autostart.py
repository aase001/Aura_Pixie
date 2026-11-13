"""Aura Pixie 自启动（计划任务方案）回归测试 · 零系统副作用

背景：V11 起程序要求以管理员身份运行，开机自启由「HKCU\\...\\Run 注册表项」
      改为「任务计划程序：登录触发 + /RL HIGHEST」。原因：Run 键无 UAC 通道，
      提权程序放 Run 键开机时会被静默拦掉或每次弹 UAC。
      原「注入注册表启动」代码按要求以注释形式留存于 main.py，本脚本第 C 组
      同时守护该回退路径——确保没有存活代码在写启动项注册表。

隔离手段：
  · M.subprocess 换成假模块（捕获 schtasks 参数，绝不真实执行）
  · M.winreg 换成内存假实现（不读写真实注册表）
  · M.is_admin / M._is_packaged / M.app_executable 打桩
  · LOCALAPPDATA 指向临时目录（不触碰真实配置）
"""

from __future__ import annotations

import os
import subprocess as _real_sp
import sys
import tempfile

_ISO = tempfile.mkdtemp(prefix="aura_autostart_")
os.environ["LOCALAPPDATA"] = _ISO
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import main as M  # noqa: E402

failures: list[str] = []
_SRC = open(os.path.join(_ROOT, "main.py"), encoding="utf-8").read()


def check(name: str, ok: bool, extra: str = "") -> None:
    print(("[OK] " if ok else "[FAIL] ") + name + (f"  {extra}" if extra else ""))
    if not ok:
        failures.append(name)


def _norm_expect(mod) -> str:
    return mod._norm_cmd(mod._run_value())


# ----------------------------- 假 schtasks（内存任务库） ---------------------
class FakeTasks:
    """捕获 /Create /Delete /Query，模拟任务库；不执行真实 schtasks。"""

    def __init__(self):
        self.tasks: dict[str, str] = {}   # TN -> /TR 值（已还原转义）
        self.calls: list[list[str]] = []
        self.assert_fail: list[str] = []

    def run(self, argv, **kw):
        args = list(argv)
        self.calls.append(args)

        class R:
            returncode = 0
            stdout = b""
            stderr = b""

        r = R()
        a = args[1:]  # 去掉 schtasks.exe 本身
        verb = a[0] if a else ""
        if verb == "/Create":
            if "/SC" not in a or a[a.index("/SC") + 1] != "ONLOGON":
                self.assert_fail.append("缺少 /SC ONLOGON")
            if "/RL" not in a or a[a.index("/RL") + 1] != "HIGHEST":
                self.assert_fail.append("缺少 /RL HIGHEST")
            if "/F" not in a:
                self.assert_fail.append("缺少 /F（覆盖）")
            if "/ARG" in a:
                self.assert_fail.append("/ARG 不是 schtasks 合法参数")
            tn, tr = a[a.index("/TN") + 1], a[a.index("/TR") + 1]
            # /TR 现为普通引号包裹（无 \" 转义，V16.3），按原样入库
            self.tasks[tn] = tr
        elif verb == "/Delete":
            self.tasks.pop(a[a.index("/TN") + 1], None)
        elif verb == "/Query":
            tn = a[a.index("/TN") + 1]
            if tn not in self.tasks:
                r.returncode = 1
            else:
                tr = self.tasks[tn]
                cmd, _, rest = tr.partition(" ")

                def esc(s: str) -> str:
                    return (s.replace("&", "&amp;").replace("<", "&lt;")
                             .replace(">", "&gt;").replace('"', "&quot;"))
                # 与真实 schtasks /XML 同口径：UTF-16LE 带 BOM。
                # 曾按 utf-8 输出，掩盖了主程序 utf-8 解码的真实编码 bug。
                r.stdout = (f"<Task><Actions><Exec><Command>{esc(cmd)}</Command>"
                            f"<Arguments>{esc(rest)}</Arguments></Exec></Actions>"
                            "</Task>").encode("utf-16")
        return r


class FakeSubprocess:
    """假 subprocess 模块：run 走 FakeTasks，其余透传真模块（不误伤 PySide 等）。"""

    def __init__(self, tasks: FakeTasks):
        self._run_impl = tasks.run

    def run(self, argv, **kw):
        return self._run_impl(argv, **kw)

    def __getattr__(self, item):
        return getattr(_real_sp, item)


class FakeWinreg:
    """假 winreg：可预置旧启动项用于清理断言，并记录写入/删除次数。"""
    KEY_SET_VALUE = 1
    KEY_QUERY_VALUE = 2
    REG_SZ = 1
    HKEY_CURRENT_USER = object()

    def __init__(self, initial: dict | None = None):
        self.data = dict(initial or {})
        self.writes = 0
        self.deletes = 0

    def OpenKey(self, hkey, sub, *_):
        if sub not in self.data:
            raise FileNotFoundError(sub)
        return _FK(self.data[sub], self)

    def CreateKeyEx(self, hkey, sub, *_):
        self.data.setdefault(sub, {})
        return _FK(self.data[sub], self)

    def SetValueEx(self, key, name, _r, _t, value):
        self.writes += 1
        key._v[name] = value

    def DeleteValue(self, key, name):
        self.deletes += 1
        if name not in key._v:
            raise FileNotFoundError(name)
        del key._v[name]

    def QueryValueEx(self, key, name):
        if name not in key._v:
            raise FileNotFoundError(name)
        return key._v[name], 1


class _FK:
    def __init__(self, values, reg):
        self._v = values
        self._reg = reg

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


_RUN_SUB = r"Software\Microsoft\Windows\CurrentVersion\Run"
_TN = "AuraPixie"
_EXE = r"D:\dist\AuraPixie.exe"

print("自启动计划任务方案回归（隔离任务库 / 注册表 / 配置）\n")

_saved = {"subprocess": M.subprocess, "winreg": M.winreg, "is_admin": M.is_admin,
          "_is_packaged": M._is_packaged, "app_executable": M.app_executable,
          "frozen": getattr(M.sys, "frozen", None)}

ft, reg = FakeTasks(), FakeWinreg({_RUN_SUB: {_TN: '"C:/old/legacy.exe"'}})
M.subprocess = FakeSubprocess(ft)       # type: ignore[assignment]
M.winreg = reg                          # type: ignore[assignment]
M.is_admin = lambda: True               # type: ignore[assignment]
M._is_packaged = lambda: True           # type: ignore[assignment]
M.app_executable = lambda: _EXE         # type: ignore[assignment]

# ============================ A 组：任务 CRUD ================================
print("A 组：开启 / 查询 / 关闭")
M.set_autostart(True)
check("开启后计划任务存在且名为 AuraPixie", _TN in ft.tasks, f"（{ft.tasks}）")
check("任务命令行 = 当前 exe", ft.tasks.get(_TN) == f'"{_EXE}"',
      f"（{ft.tasks.get(_TN)}）")
# V16.3 回归：/TR 不再做 \" 转义——subprocess 直传下反斜杠会被 schtasks
# 存成字面字符，任务目标变 \"路径\" 找不到程序。假库按原样入库，
# 若转义回归则下面两条与「任务命令行 = 当前 exe」必红。
check("/TR 为普通引号包裹、无 \\\" 转义（目标程序可被找到）",
      ft.tasks.get(_TN) == f'"{_EXE}"' and '\\"' not in ft.tasks.get(_TN, ""),
      f"（{ft.tasks.get(_TN)}）")
check("_task_tr_value 不含反斜杠转义（源码口径看守）",
      '\\"' not in M._task_tr_value() and M._task_tr_value() == M._run_value(),
      f"（{M._task_tr_value()}）")
check("创建参数含 ONLOGON / HIGHEST / F，且无非法 /ARG",
      not ft.assert_fail, f"（{ft.assert_fail}）")
check("旧注册表启动项被清理（避免双份自启）", _TN not in reg.data[_RUN_SUB])
check("清理是一次性删除", reg.deletes == 1, f"（deletes={reg.deletes}）")
check("全流程零注册表写入（注入启动项代码已停用）", reg.writes == 0,
      f"（writes={reg.writes}）")

st = M.autostart_state()
check("state.task 实况为 True", st["task"] is True)
check("state.task_action 解析正确", M._norm_cmd(st["task_action"]) == _norm_expect(M),
      f"（{st['task_action']}）")
check("编码口径看守：假库 XML 为 UTF-16（与真实 schtasks 一致）且 Command 可解析",
      ft.run(["schtasks.exe", "/Query", "/TN", _TN, "/XML"]).stdout.startswith(b"\xff\xfe")
      and M._task_query_xml() is not None
      and "<Command>" in M._task_query_xml()
      and M._task_action() is not None)
check("源码看守：_task_query_xml 按 utf-16 解码（防回归 utf-8 误判重建）",
      'decode("utf-16"' in _SRC)
check("旧 registry 字段保留但恒 None（兼容自检不报错）",
      st["registry"] is None and st["registry_value"] is None)
check("setting.yaml 记录 enabled=true",
      M.read_setting()["autostart"]["enabled"] is True)

M.set_autostart(False)
check("关闭后任务已删除", _TN not in ft.tasks)
check("关闭写入 enabled=false", M.read_setting()["autostart"]["enabled"] is False)
d_before = reg.deletes
M.set_autostart(True)
check("一次性清理幂等：再次开启不重复动注册表", reg.deletes == d_before,
      f"（{d_before} → {reg.deletes}）")


# ============================ B 组：自愈 ====================================
print("\nB 组：自愈（漂移才重建，只修不删）")
_a=M.read_setting(); _a.setdefault("autostart",{})["enabled"]=True
M.write_setting(_a)
ft.calls.clear()
M.autostart_self_heal()
check("任务与期望一致 → 不重建",
      not any("/Create" in c for c in ft.calls), f"（calls={ft.calls}）")

ft.tasks[_TN] = r'"C:\wrong\old.exe"'
M.autostart_self_heal()
check("命令漂移（exe 挪位）→ 重建为当前 exe",
      ft.tasks[_TN] == f'"{_EXE}"', f"（{ft.tasks[_TN]}）")

del ft.tasks[_TN]
M.autostart_self_heal()
check("配置启用但任务被第三方删除 → 自愈重建", _TN in ft.tasks)

M._is_packaged = lambda: False                      # type: ignore[assignment]
del ft.tasks[_TN]
M.autostart_self_heal()
check("源码形态绝不改写正式自启任务", _TN not in ft.tasks)

M._is_packaged = lambda: True                       # type: ignore[assignment]
M.is_admin = lambda: False                          # type: ignore[assignment]
M.autostart_self_heal()
check("非管理员自愈静默跳过（无权限建 HIGHEST 任务）", _TN not in ft.tasks)

M.is_admin = lambda: True                           # type: ignore[assignment]
_a=M.read_setting(); _a.setdefault("autostart",{})["enabled"]=False
M.write_setting(_a)
ft.calls.clear()
M.autostart_self_heal()
check("配置未启用 → 不动任务（尊重用户手动操作）",
      not any("/Create" in c for c in ft.calls) and not any("/Delete" in c for c in ft.calls))

# ============================ C 组：提权门禁 ================================
print("\nC 组：管理员提权门禁")
check("main() 入口接入 _ensure_admin 门禁",
      "if _ensure_admin(argv):" in _SRC)
check("提权走 ShellExecuteW runas", 'ShellExecuteW(None, "runas"' in _SRC)
check("拒绝 UAC 后有明确提示（非静默退出）", "需要管理员权限" in _SRC)
check("提供 --no-elevate 免提权逃生口", '"--no-elevate"' in _SRC or "_ELEVATE_FLAG" in _SRC)
check("_ensure_admin 跳过 --selftest / --elevated-child",
      '"--selftest" in argv' in _SRC and "_ELEVATED_CHILD in argv" in _SRC)
check("门禁位于 cleanup_runner_dir 之前（避免半权限副作用）",
      _SRC.index("if _ensure_admin(argv):") < _SRC.index("    cleanup_runner_dir()"))
_check_lines = [ln.strip() for ln in _SRC.splitlines()]
check("原注册表注入代码以注释形式留存（可回退）",
      "# def _set_registry_autostart(enabled: bool) -> None:" in _check_lines
      and any(ln.startswith("#") and "winreg.SetValueEx" in ln for ln in _check_lines))
_active = [ln for ln in _check_lines
           if "winreg.SetValueEx" in ln and not ln.startswith("#")]
check("无任何存活代码写注册表启动项", not _active, f"（{_active}）")
check("计划任务 dry-run 隔离开关存在（AURA_AUTOSTART_DRYRUN）",
      "AURA_AUTOSTART_DRYRUN" in _SRC and "_autostart_dryrun()" in _SRC)
for _t in ("check_breath.py", "smoke_settings.py"):
    _tp = os.path.join(_ROOT, "tools", _t)
    if os.path.isfile(_tp):
        check(f"{_t} 已置位 dry-run（保存路径自检零系统副作用）",
              'AURA_AUTOSTART_DRYRUN' in open(_tp, encoding="utf-8").read())
check("保存路径回报计划任务实况（沿用置顶写盘后对表铁律）",
      "autostart_enabled() != want" in _SRC)

# ============================ D 组：口径回归 ================================
print("\nD 组：源码形态自启命令口径（pythonw 不弹终端，沿用 V7 结论）")
M._is_packaged = lambda: False                      # type: ignore[assignment]
M.is_admin = lambda: True                           # type: ignore[assignment]
pv = M._run_value()
pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
check("源码 _run_value 用 pythonw.exe（无控制台）", pyw in pv, f"（{pv}）")
check("pythonw.exe 实际存在", os.path.isfile(pyw), f"（{pyw}）")
check("源码 _run_value 指向 main.py 绝对路径", os.path.abspath(M.__file__) in pv)
M.set_autostart(True)
check("源码形态开启自启 → 任务命令为 pythonw + main.py",
      pyw in ft.tasks.get(_TN, ""), f"（{ft.tasks.get(_TN)}）")
check("源码形态 _run_value 不含打包 exe 路径（两种形态命令口径不混用）",
      _EXE not in pv)

# ----------------------------- 恢复环境 ------------------------------------
M.subprocess = _saved["subprocess"]                 # type: ignore[assignment]
M.winreg = _saved["winreg"]
M.is_admin = _saved["is_admin"]                     # type: ignore[assignment]
M._is_packaged = _saved["_is_packaged"]             # type: ignore[assignment]
M.app_executable = _saved["app_executable"]
if _saved["frozen"] is None:
    if hasattr(M.sys, "frozen"):
        delattr(M.sys, "frozen")
else:
    M.sys.frozen = _saved["frozen"]

print("\n总判定:", "PASS" if not failures else "NEEDS_REVIEW")
for f in failures:
    print("  -", f)
raise SystemExit(0 if not failures else 1)
