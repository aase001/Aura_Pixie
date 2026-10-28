"""Aura Pixie 自启动注册表修复回归测试（不触碰真实注册表 / 真实配置）

背景：2026-10-28 开机自启动弹出 `python.exe` 终端对话框。
根因：源码形态（python main.py 开发运行）时 autostart_self_heal() 每次启动
      都会把注册表自启项「修正」为 解释器+main.py；python.exe 是控制台程序，
      开机自启必然弹出终端框；关掉终端 = 终止进程 = 软件退出。
修复：① autostart_self_heal() 仅打包形态（exe）参与，源码形态绝不改写注册表；
      ② 源码形态自启命令改用 pythonw.exe（无控制台窗口），二次兜底不弹终端。

本脚本把 main.winreg 替换为假实现（内存字典），全程不读写真实注册表；
LOCALAPPDATA 隔离到临时目录，不触碰真实配置。
"""

from __future__ import annotations

import os
import sys
import tempfile

_ISO = tempfile.mkdtemp(prefix="aura_autostart_")
os.environ["LOCALAPPDATA"] = _ISO
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import main as M  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    print(("[OK] " if ok else "[FAIL] ") + name + (f"  {extra}" if extra else ""))
    if not ok:
        failures.append(name)


# ---------------- 假 winreg（内存字典，绝不触碰真实注册表） ----------------
_RUN_SUB = r"Software\Microsoft\Windows\CurrentVersion\Run"
_KEY = "AuraPixie"


class _FakeKey:
    def __init__(self, values: dict):
        self._v = values

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeWinreg:
    """假 winreg 模块：模块级 OpenKey/CreateKeyEx/SetValueEx/DeleteValue/QueryValueEx，
    与真实 winreg 的调用形态一致（第一个参数是 key 对象）。全程内存字典，绝不落盘。
    """
    KEY_SET_VALUE = 1
    KEY_QUERY_VALUE = 2
    REG_SZ = 1
    HKEY_CURRENT_USER = object()

    def __init__(self, initial: dict | None = None):
        self.data = dict(initial or {})

    def OpenKey(self, hkey, sub, *_):
        if sub not in self.data:
            raise FileNotFoundError(sub)
        return _FakeKey(self.data[sub])

    def CreateKeyEx(self, hkey, sub, *_):
        self.data.setdefault(sub, {})
        return _FakeKey(self.data[sub])

    def SetValueEx(self, key, name, _reserved, _typ, value):
        key._v[name] = value

    def DeleteValue(self, key, name):
        if name not in key._v:
            raise FileNotFoundError(name)
        del key._v[name]

    def QueryValueEx(self, key, name):
        if name not in key._v:
            raise FileNotFoundError(name)
        return key._v[name], 1


print("自启动注册表修复回归（隔离注册表与配置）\n")

# 统一准备：隔离 LOCALAPPDATA 下配置 autostart.enabled = True
M.write_setting({
    "functions": [],
    "aura": {"color_mid": "#FFFFFF", "aura_scale": 1.0,
             "idle_sec": 60, "opacity": 100, "sleep_opacity": 10},
    "autostart": {"enabled": True},
    "window": {"always_on_top": False, "pos_x": None, "pos_y": None},
})

# 备份真实引用，结束时恢复
_saved_winreg = M.winreg
_saved_apex = M.app_executable
_saved_frozen = getattr(M.sys, "frozen", None)
_saved_executable = M.sys.executable

O = {"old": '"C:/old/path/exe-any-variant.exe"'}

# ---------- A 组：源码形态（python main.py）----------
print("源码形态：判定与命令行格式")
check("源码形态 _is_packaged() == False", M._is_packaged() is False)

pv = M._run_value()
pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
check("源码 _run_value 使用 pythonw.exe（无控制台窗口）", pyw in pv, f"（{pv}）")
check("pythonw.exe 实际存在", os.path.isfile(pyw), f"（{pyw}）")
check("源码 _run_value 指向 main.py 绝对路径",
      os.path.abspath(M.__file__) in pv and "python.exe" not in pv.replace("pythonw.exe", ""),
      f"（{pv}）")

print("\n源码形态：自愈守卫（绝不改写注册表）")
_fake_a = FakeWinreg({_RUN_SUB: {_KEY: O["old"]}})
M.winreg = _fake_a
M.autostart_self_heal()
got_a = _fake_a.data[_RUN_SUB].get(_KEY)
check("源码形态 autostart_self_heal 不修正注册表", got_a == O["old"], f"（{got_a}）")

print("\n源码形态：set_autostart(True) 写入无控制台命令")
M.set_autostart(True)
got_b = _fake_a.data[_RUN_SUB].get(_KEY)
check("源码 set_autostart(True) 写入 pythonw 命令",
      got_b is not None and pyw in got_b and "python.exe" not in got_b.replace("pythonw.exe", ""),
      f"（{got_b}）")

# ---------- B 组：打包形态（exe）----------
print("\n打包形态（模拟 sys.frozen + exe 路径）")
M.sys.frozen = True
M.app_executable = lambda: r"D:\dist\AuraPixie.exe"  # type: ignore[assignment]
check("打包形态 _is_packaged() == True", M._is_packaged() is True)
check("打包 _run_value 为 exe 自身",
      M._run_value() == r'"D:\dist\AuraPixie.exe"', f"（{M._run_value()}）")

_fake_c = FakeWinreg({_RUN_SUB: {_KEY: O["old"]}})
M.winreg = _fake_c
M.autostart_self_heal()
got_c = _fake_c.data[_RUN_SUB].get(_KEY)
check("打包形态 autostart_self_heal 修正为 exe 路径",
      got_c == r'"D:\dist\AuraPixie.exe"', f"（{got_c}）")

_fake_d = FakeWinreg({_RUN_SUB: {_KEY: r'"D:\dist\AuraPixie.exe"'}})
M.winreg = _fake_d
M.set_autostart(False)
check("打包形态 set_autostart(False) 删除注册表项", _KEY not in _fake_d.data[_RUN_SUB])

# ---------- 恢复环境 ----------
M.winreg = _saved_winreg
M.app_executable = _saved_apex
if _saved_frozen is None:
    if hasattr(M.sys, "frozen"):
        delattr(M.sys, "frozen")
else:
    M.sys.frozen = _saved_frozen

print("\n总判定:", "PASS" if not failures else "NEEDS_REVIEW")
for f in failures:
    print("  -", f)
raise SystemExit(0 if not failures else 1)