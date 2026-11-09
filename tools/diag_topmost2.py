"""置顶"不稳定"根因实证（全程内存桩，绝不写真实配置/注册表/磁盘）。

场景 A：勾选置顶后保存，但指令校验失败提前 return → 配置写盘 true，运行时未生效。
场景 B：右键「重载」只重载 functions，不同步 window.always_on_top。
场景 C：topmost 组内名次竞态——第三方置顶窗抢位后球被压，且无自动夺回机制。
"""
import ctypes
import ctypes.wintypes as WT
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QWidget, QMenu, QMessageBox,
)

import main as M  # noqa: E402

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
user32 = ctypes.windll.user32
user32.GetWindowLongW.restype = WT.LONG
user32.GetWindowLongW.argtypes = [WT.HWND, ctypes.c_int]
ENUM_CALLBACK = ctypes.WINFUNCTYPE(ctypes.c_bool, WT.HWND, WT.LPARAM)


def is_top(hwnd) -> bool:
    return bool(user32.GetWindowLongW(int(hwnd), GWL_EXSTYLE) & WS_EX_TOPMOST)


def z_rank(hwnd: int):
    target = int(hwnd)
    order = []

    def cb(h, _l):
        if user32.IsWindowVisible(h):
            order.append(int(h))
        return True

    user32.EnumWindows(ENUM_CALLBACK(cb), 0)
    return order.index(target) if target in order else -1


def pump(ms: int = 200):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


# ---- 内存桩：拦掉一切落盘/注册表副作用 ----
STORE = {"always_on_top": False}
POPUPS = []


def fake_topmost_enabled(*a, **k):
    return bool(STORE["always_on_top"])


def fake_set_topmost(enabled, *a, **k):
    STORE["always_on_top"] = bool(enabled)


def fake_save_functions(*a, **k):
    POPUPS.append("save_functions!")


def fake_set_autostart(*a, **k):
    POPUPS.append("set_autostart!")


def fake_log_crash(tag, exc):
    POPUPS.append(f"crash:{tag}:{exc}")


def swallow_popups():
    def make(kind, ret):
        def _f(parent, title, text, *a, **k):
            POPUPS.append(f"{kind}:{title}")
            return ret
        return staticmethod(_f)

    QMessageBox.information = make("info", QMessageBox.StandardButton.Ok)
    QMessageBox.warning = make("warn", QMessageBox.StandardButton.Ok)
    QMessageBox.question = make("question", QMessageBox.StandardButton.No)


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    M.topmost_enabled = fake_topmost_enabled
    M.set_topmost = fake_set_topmost
    M.save_functions = fake_save_functions
    M.set_autostart = fake_set_autostart
    M._log_crash = fake_log_crash
    swallow_popups()

    ball = M.AuraBall()
    scr = app.primaryScreen().availableGeometry()
    ball.place_at(scr.center().x(), scr.center().y())
    ball.show()
    pump(300)

    report = []

    # ============ 场景 A：勾选置顶 + 保存，代码非法 → 状态错位 ============
    STORE["always_on_top"] = False
    ball.apply_topmost_setting(False)
    pump(200)
    rows = [{"name": "test", "code": "py: print(1)", "active": True},
            {"name": "bad", "code": "xxx: notepad", "active": True}]
    dlg = M.SettingsDialog(rows, ball)
    dlg.chk_topmost.setChecked(True)
    before = len(POPUPS)
    dlg._on_save()
    pump(200)
    new_popups = POPUPS[before:]
    hwnd = int(ball.winId())
    report.append((
        "A1 保存被校验弹窗拦下（未 accept，设置窗仍开着）",
        dlg.isVisible() and any(p.startswith("warn") for p in new_popups),
        f"弹窗={new_popups}"))
    report.append((
        "A2 置顶配置已写盘=true（写盘在校验之前）",
        STORE["always_on_top"] is True, ""))
    report.append((
        "A3 运行时未生效：_topmost=False 且窗口无 TOPMOST 样式",
        ball._topmost is False and not is_top(hwnd),
        f"_topmost={ball._topmost} is_top={is_top(hwnd)}"))
    report.append((
        "A4 ★状态错位=用户视角的「置顶失效」",
        STORE["always_on_top"] is True and not is_top(hwnd), ""))
    dlg.reject()
    pump(200)

    # 同一份配置下重启实例：置顶会突然"自己回来了"
    ball2_topmost = fake_topmost_enabled()
    report.append((
        "A5 ★按当前配置重启，置顶自动变 true（时好时坏的直接来源）",
        ball2_topmost is True and not is_top(int(ball.winId())), ""))

    # ============ 场景 B：右键「重载」不同步 window 块 ============
    STORE["always_on_top"] = False
    ball.apply_topmost_setting(False)
    pump(200)
    STORE["always_on_top"] = True          # 模拟用户手改 yaml / 导入配置
    ball.reload_config()                    # 右键「重载」只做这件事
    pump(250)
    report.append((
        "B1 重载后配置源=true", fake_topmost_enabled() is True, ""))
    report.append((
        "B2 ★重载不会应用置顶（reload_config 只碰 functions）",
        not is_top(int(ball.winId())),
        f"_topmost={ball._topmost} is_top={is_top(int(ball.winId()))}"))

    # 同理：设置窗「取消/直接关窗」也不会应用磁盘上已变化的 window 块
    STORE["always_on_top"] = True
    ball.apply_topmost_setting(False)
    pump(200)
    dlg2 = M.SettingsDialog(rows, ball)
    dlg2.reject()                            # 用户点取消或直接关窗
    pump(250)
    report.append((
        "B3 ★设置窗取消/关闭后，磁盘 true 仍未生效（仅 Accepted 才同步）",
        not is_top(int(ball.winId())),
        f"_topmost={ball._topmost}"))

    # ============ 场景 C：topmost 组内名次竞态 ============
    STORE["always_on_top"] = True
    ball.apply_topmost_setting(True)
    pump(250)
    rival = QWidget()
    rival.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint)
    rival.resize(320, 200)
    rival.show()
    pump(400)
    hr, hb = int(rival.winId()), int(ball.winId())
    r_ball, r_riv = z_rank(hb), z_rank(hr)
    report.append((
        "C1 第三方置顶窗出现后，球被压在下面（同为置顶，后来者在上）",
        r_riv < r_ball, f"球Z={r_ball} 竞品Z={r_riv}"))

    menu = QMenu(ball)
    menu.addAction("noop")
    menu.popup(rival.frameGeometry().center())
    pump(300)
    menu.hide()
    pump(400)
    b2 = z_rank(int(ball.winId()))
    report.append((
        f"C2 球自身右键菜单交互后名次={b2}（交互前={r_ball}）",
        True, "菜单类窗口同样在置顶组内插队"))

    rival.raise_()
    pump(300)
    b3 = z_rank(int(ball.winId()))
    report.append((
        "C3 竞品 raise 后球被压，程序无任何自动夺回（心跳帧不检查层级）",
        b3 > b2, f"球Z={b3}"))

    ball.raise_()
    pump(300)
    b4 = z_rank(int(ball.winId()))
    report.append((
        "C4 手动 raise 能夺回（证明缺的只是自动重assert）",
        b4 < b3, f"raise后球Z={b4}"))

    ball._enter_sleep()
    pump(200)
    ball._wake()
    pump(300)
    b5 = z_rank(int(ball.winId()))
    report.append((
        "C5 休眠→唤醒链路的 setWindowOpacity 不改变名次",
        b5 == b4, f"唤醒后球Z={b5}"))

    # 展开扇形时的 setFixedSize（改窗口尺寸）会不会掉置顶
    ball._enter_expanding()
    pump(250)
    keep = is_top(int(ball.winId()))
    report.append(("C6 展开时改尺寸后置顶样式仍在", keep, f"is_top={keep}"))

    rival.close()
    ball.close()

    print()
    for t, ok, extra in report:
        print(f"{'YES' if ok else 'NO '}  {t}   {extra}")
    print(f"\n副作用检查（应为空）：{[p for p in POPUPS if p.endswith('!')]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
