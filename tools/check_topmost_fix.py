"""置顶稳定性回归验证（修复后，全程内存桩，绝不写真实配置/注册表/磁盘）。

注意：Z 序与置顶样式实测需要真实窗口（非离屏），运行时桌面会短暂出现
测试窗口与一个竞品置顶窗，属预期现象。

对应三项修复：
A（根因一）校验失败不写盘：配置与运行时不再错位；
B（根因二）重载/关窗对表：磁盘 window 块变化即时同步到运行时；
C（根因三）展开自愈：被第三方置顶窗压住后，展开时夺回置顶组第一名。
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


STORE = {"always_on_top": False}
POPUPS = []
WRITES = []


def fake_topmost_enabled(*a, **k):
    return bool(STORE["always_on_top"])


def fake_set_topmost(enabled, *a, **k):
    WRITES.append(f"topmost={enabled}")
    STORE["always_on_top"] = bool(enabled)


def fake_save_functions(rows, *a, **k):
    WRITES.append(f"save_functions({len(rows)})")


def fake_set_autostart(enabled, *a, **k):
    WRITES.append(f"autostart={enabled}")


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

    fails = []

    def chk(name, cond, extra=""):
        tag = "PASS" if cond else "FAIL"
        if not cond:
            fails.append(name)
        print(f"[{tag}] {name}   {extra}")

    # ================= 修复一：校验失败不写盘 =================
    STORE["always_on_top"] = False
    ball.apply_topmost_setting(False)
    pump(200)
    sub_lines = []
    for i in range(1, 11):   # 10 对次级标记，超过 9 个上限，必被拦
        sub_lines += [f"【【【子{i}】】】", "py: print(1)", f"【【【子{i}】】】"]
    bad_rows = [{"name": "test", "code": "py: print(1)", "active": True},
                {"name": "bad", "code": "\n".join(sub_lines), "active": True}]
    WRITES.clear()
    POPUPS.clear()
    dlg = M.SettingsDialog(bad_rows, ball)
    dlg.show()            # 真实显示，才能断言"校验失败后窗口仍开着"
    pump(200)
    dlg.chk_topmost.setChecked(True)
    dlg.chk_autostart.setChecked(True)
    dlg._on_save()
    pump(200)
    chk("R1a 校验失败时零写盘（置顶/自启/functions 均未落盘）",
        WRITES == [], f"writes={WRITES}")
    chk("R1b 磁盘仍 false，运行时仍 false（无错位）",
        STORE["always_on_top"] is False and not is_top(int(ball.winId())),
        f"disk={STORE['always_on_top']}")
    chk("R1c 设置窗保持打开且弹出校验警告",
        dlg.isVisible() and any(p.startswith("warn") for p in POPUPS),
        f"popups={POPUPS}")
    dlg.reject()
    pump(200)

    # 合法配置保存：应写盘且顺序正常
    good_rows = [{"name": "test", "code": "py: print(1)", "active": True}]
    WRITES.clear()
    dlg = M.SettingsDialog(good_rows, ball)
    dlg.chk_topmost.setChecked(True)
    dlg.chk_autostart.setChecked(False)
    dlg._on_save()
    pump(200)
    chk("R1d 校验通过：置顶写盘+functions写盘，且置顶写盘在校验之后",
        WRITES == ["autostart=False", "topmost=True", "save_functions(1)"],
        f"writes={WRITES}")
    chk("R1e accept 后设置窗关闭", not dlg.isVisible(), "")

    # ================= 修复二：重载/关窗对表 =================
    # 2a 右键「重载」同步置顶：磁盘 true → 重载后窗口应有 TOPMOST
    STORE["always_on_top"] = True
    ball.apply_topmost_setting(False)
    pump(200)
    chk("R2a0 前置：运行时已为非置顶", not is_top(int(ball.winId())), "")
    ball.reload_config()
    pump(300)
    chk("R2a 重载后按磁盘值应用置顶（TOPMOST 样式出现）",
        is_top(int(ball.winId())) and ball._topmost is True,
        f"_topmost={ball._topmost}")

    # 2b 重载幂等：磁盘不变时重载不应引起状态翻转
    rank_before = ball._topmost
    ball.reload_config()
    pump(200)
    chk("R2b 重载幂等（一致则跳过，不闪窗）",
        ball._topmost == rank_before and is_top(int(ball.winId())), "")

    # 2c 取消关窗对表：磁盘被外部改成 false，设置窗点取消 → 运行时应转非置顶
    STORE["always_on_top"] = False
    dlg = M.SettingsDialog(good_rows, ball)
    dlg.finished.connect(ball._on_settings_closed)   # 真实链路就是这样接的
    dlg.reject()          # 取消
    pump(300)
    chk("R2c 设置窗取消后仍按磁盘值对表（运行时转非置顶）",
        not is_top(int(ball.winId())) and ball._topmost is False,
        f"_topmost={ball._topmost}")

    # 2d 关窗对表不会反向乱写：对表只读磁盘、不写盘
    WRITES.clear()
    STORE["always_on_top"] = True
    dlg = M.SettingsDialog(good_rows, ball)
    dlg.finished.connect(ball._on_settings_closed)
    dlg.reject()
    pump(300)
    chk("R2d 对表过程零写盘（不覆盖磁盘）", WRITES == [], f"writes={WRITES}")

    # 2e Accepted 分支经 reload_config 统一同步，行为等价
    STORE["always_on_top"] = False
    ball.apply_topmost_setting(True)
    pump(300)
    dlg = M.SettingsDialog(good_rows, ball)
    dlg.finished.connect(ball._on_settings_closed)
    dlg.accept()
    pump(300)
    chk("R2e 保存成功走 reload_config，同样按磁盘值对表",
        ball._topmost is False and not is_top(int(ball.winId())),
        f"_topmost={ball._topmost}")

    # ================= 修复三：展开自愈 =================
    STORE["always_on_top"] = True
    ball.apply_topmost_setting(True)
    pump(300)
    rival = QWidget()
    rival.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint)
    rival.resize(320, 200)
    rival.show()
    pump(400)
    hr, hb = int(rival.winId()), int(ball.winId())
    rk_ball, rk_riv = z_rank(hb), z_rank(hr)
    chk("R3a 前置：第三方置顶窗确实压住了球", rk_riv < rk_ball,
        f"球Z={rk_ball} 竞品Z={rk_riv}")
    ball._enter_expanding()
    pump(300)
    rk_ball2 = z_rank(int(ball.winId()))
    chk("R3b 主环展开夺回最上（球名次优于竞品）", rk_ball2 < z_rank(hr),
        f"展开后球Z={rk_ball2} 竞品Z={z_rank(hr)}")

    # 3c 次级环展开（同样走 _enter_expanding）
    rival.raise_()
    pump(300)
    chk("R3c0 前置：竞品再次 raise 又压住球", z_rank(hr) < z_rank(int(ball.winId())),
        f"球Z={z_rank(int(ball.winId()))} 竞品Z={z_rank(hr)}")
    ball._enter_collapsing()
    ball._state = "hidden"                 # 直接置收起完成，不等动画
    ball._sub_pending_items = [{"name": "s1", "code": "py: print(1)"},
                               {"name": "s2", "code": "py: print(2)"}]
    ball._sub_launching = True
    ball._open_sub_ring()
    pump(300)
    chk("R3d 次级环展开同样夺回最上", z_rank(int(ball.winId())) < z_rank(hr),
        f"球Z={z_rank(int(ball.winId()))} 竞品Z={z_rank(hr)}")

    # 3e 非置顶时不自愈：不越权抢层
    STORE["always_on_top"] = False
    ball.apply_topmost_setting(False)
    pump(300)
    rk_riv_e = z_rank(hr)
    ball._enter_expanding()
    pump(300)
    chk("R3e 关闭置顶后展开不再 raise（不抢其他窗口层级）",
        z_rank(hr) == rk_riv_e, f"竞品Z={z_rank(hr)}（前 {rk_riv_e}）")

    # ================= 交互回归：菜单/休眠不破坏样式 =================
    STORE["always_on_top"] = True
    ball.apply_topmost_setting(True)
    pump(300)
    menu = QMenu(ball)
    menu.addAction("noop")
    menu.popup(scr.center())
    pump(200)
    menu.hide()
    pump(200)
    ball._enter_sleep()
    pump(200)
    ball._wake()
    pump(300)
    chk("R4a 休眠/唤醒链路与菜单交互后置顶样式完好",
        is_top(int(ball.winId())), "")
    ball._enter_collapsing()
    pump(1500)
    chk("R4b 收起回到球体后置顶样式仍在",
        is_top(int(ball.winId())), f"state={ball._state}")

    rival.close()
    ball.close()
    print()
    if fails:
        print(f"RESULT: FAIL ({len(fails)}) -> {fails}")
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
