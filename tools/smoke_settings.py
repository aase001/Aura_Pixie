"""冒烟测试：真实事件循环下「打开设置 → 点保存」不得让主程序退出。

用户报的崩溃场景回归：上一版在右键菜单的模态栈里再嵌一层 QDialog.exec，
点 Save 后整个程序消失。现在菜单回调只投递 singleShot，对话框非模态 show，
保存走 finished 信号回调重载配置。

所有配置读写被重定向到临时副本，不碰真实 functions.yaml。

用法
    .venv\\Scripts\\activate
    python tools\\smoke_settings.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# —— 配置隔离：冒烟期间指向临时 LOCALAPPDATA，绝不触碰真实配置 ——
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="aura_smoke_la_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import main as M  # noqa: E402

TMPDIR = tempfile.mkdtemp(prefix="aura_smoke_")
SMOKE_CFG = os.path.join(TMPDIR, "functions.yaml")

# 重定向三个配置函数到临时副本（默认参数在定义时绑定，必须包一层）
_orig_save, _orig_read, _orig_load = M.save_functions, M.read_all_rows, M.load_functions
M.save_functions = lambda rows, path=SMOKE_CFG: _orig_save(rows, path)
M.read_all_rows = lambda path=SMOKE_CFG: _orig_read(path)
M.load_functions = lambda path=SMOKE_CFG: _orig_load(path)

_orig_save([{"name": "甲", "code": "print(1)", "active": True},
            {"name": "乙", "code": "print(2)", "active": True}], SMOKE_CFG)

_ball = None
_result = {"saved": False, "alive": False, "error": None, "sectors": []}


def open_then_save() -> None:
    try:
        _ball.open_settings()
    except Exception as exc:  # noqa: BLE001
        _result["error"] = f"open_settings: {exc!r}"
        QApplication.quit()


def click_save() -> None:
    dlg = getattr(_ball, "_settings_dlg", None)
    if dlg is None:
        _result["error"] = "设置对话框没有打开"
        QApplication.quit()
        return
    try:
        dlg._on_save()  # noqa: SLF001 - 等同用户点 Save
    except Exception as exc:  # noqa: BLE001
        _result["error"] = f"save: {exc!r}"
        QApplication.quit()


def verify_and_exit() -> None:
    _result["saved"] = _result["error"] is None
    _result["alive"] = (_ball is not None and _ball.isVisible()
                        and QApplication.instance() is not None)
    _result["sectors"] = [s["name"] for s in _ball._sectors]  # noqa: SLF001
    QApplication.quit()


def main() -> int:
    global _ball
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    _ball = M.AuraBall()
    _ball.show()
    _ball.start()

    QTimer.singleShot(200, open_then_save)
    QTimer.singleShot(600, click_save)
    QTimer.singleShot(1200, verify_and_exit)
    app.exec()

    ok = (_result["saved"] and _result["alive"] and _result["error"] is None
          and _result["sectors"] == ["甲", "乙"])
    print("冒烟结果:", _result)
    print("总判定:", "PASS" if ok else "FAIL")

    shutil.rmtree(TMPDIR, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
