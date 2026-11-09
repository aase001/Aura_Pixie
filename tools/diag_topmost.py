"""置顶稳定性诊断（真实窗口，非离屏）。

检查各运行状态下 Win32 扩展样式 WS_EX_TOPMOST 是否保留，并实测 Z 序：
1) 启动后、展开/收起、休眠/唤醒、设置保存重设 flags 之后，置顶样式是否还在；
2) 非置顶窗口抢焦点后，球是否仍在最上层；
3) 第三方「同为置顶」的窗口出现后，球是否被压到下面（topmost 组内后来者居上）。

只读取配置，不写 %LOCALAPPDATA%（topmost_enabled 被 monkeypatch）。
"""
import ctypes
import ctypes.wintypes as WT
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt, QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget, QMainWindow  # noqa: E402

import main as M  # noqa: E402

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_LAYERED = 0x00080000

user32 = ctypes.windll.user32
user32.GetWindowLongW.restype = WT.LONG
user32.GetWindowLongW.argtypes = [WT.HWND, ctypes.c_int]


def topmost_style(hwnd: int) -> bool:
    return bool(user32.GetWindowLongW(int(hwnd), GWL_EXSTYLE) & WS_EX_TOPMOST)


def layered_style(hwnd: int) -> bool:
    return bool(user32.GetWindowLongW(int(hwnd), GWL_EXSTYLE) & WS_EX_LAYERED)


ENUM_CALLBACK = ctypes.WINFUNCTYPE(ctypes.c_bool, WT.HWND, WT.LPARAM)


def z_rank(hwnd: int) -> int:
    """全局 Z 序名次：0 = 最顶。只统计可见窗口。"""
    target = int(hwnd)
    order = []

    def cb(h, _l):
        if user32.IsWindowVisible(h):
            order.append(int(h))
        return True

    user32.EnumWindows(ENUM_CALLBACK(cb), 0)
    return order.index(target) if target in order else -1


def pump(ms: int = 120):
    import time
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    M.topmost_enabled = lambda *a, **k: True   # 不动真实配置

    ball = M.AuraBall()
    scr = app.primaryScreen().availableGeometry()
    ball.place_at(scr.center().x(), scr.center().y())
    ball.show()
    pump(300)
    hwnd = int(ball.winId())

    res = []
    res.append(("启动后置顶样式存在", topmost_style(hwnd)))
    res.append(("分层窗口(透明)存在", layered_style(hwnd)))

    # 展开 -> 收起
    ball._enter_expanding()
    pump(200)
    res.append(("扇形展开中置顶样式存在", topmost_style(int(ball.winId()))))
    ball._enter_collapsing()
    pump(700)
    res.append(("扇形收起后置顶样式存在", topmost_style(int(ball.winId()))))

    # 休眠 -> 唤醒
    ball._enter_sleep()
    pump(200)
    res.append(("休眠中置顶样式存在", topmost_style(int(ball.winId()))))
    ball._wake()
    pump(200)
    res.append(("唤醒后置顶样式存在", topmost_style(int(ball.winId()))))

    # 拖动位移
    ball.place_at(scr.center().x() + 30, scr.center().y() + 20)
    pump(200)
    res.append(("移动后置顶样式存在", topmost_style(int(ball.winId()))))

    # 设置保存链路：apply_topmost_setting(True) 此时与 _topmost 一致 → 早退，不重设 flags
    ball.apply_topmost_setting(True)
    pump(200)
    res.append(("保存后(状态一致早退)置顶样式存在", topmost_style(int(ball.winId()))))

    # 关闭再打开（关→开）走 setWindowFlags 重建 HWND
    ball.apply_topmost_setting(False)
    pump(250)
    h2 = int(ball.winId())
    res.append(("关掉置顶后置顶样式已移除", not topmost_style(h2)))
    ball.apply_topmost_setting(True)
    pump(250)
    h3 = int(ball.winId())
    res.append(("重新打开置顶后置顶样式存在", topmost_style(h3)))
    res.append(("HWND 是否被重建（关→开）", h2 != h3))

    # 设置窗打开/关闭是否影响
    dlg = M.SettingsDialog(M.read_all_rows(), ball)
    dlg.setModal(False)
    dlg.show()
    pump(300)
    res.append(("设置窗弹出后球仍置顶", topmost_style(int(ball.winId()))))
    dlg.close()
    pump(300)
    res.append(("设置窗关闭后球仍置顶", topmost_style(int(ball.winId()))))

    # Z 序实测 1：普通窗口抢焦点，球应在其上
    normal = QMainWindow()
    normal.setWindowTitle("diag-normal-window")
    normal.resize(400, 300)
    normal.show()
    normal.activateWindow()
    pump(400)
    hn = int(normal.winId())
    rb, rn = z_rank(hwnd if h3 == hwnd else h3), z_rank(hn)
    res.append((f"普通窗口抢焦点：球Z={rb} 普通Z={rn} 球在上", rb >= 0 and rn >= 0 and rb < rn))
    normal.close()
    pump(200)

    # Z 序实测 2：后来者同为置顶（模拟第三方置顶悬浮窗）
    rival = QWidget()
    rival.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint)
    rival.setWindowTitle("diag-rival-topmost")
    rival.resize(300, 200)
    rival.show()
    pump(400)
    hr = int(rival.winId())
    rb2, rr2 = z_rank(int(ball.winId())), z_rank(hr)
    print(f"  [竞态] 球Z={rb2} 竞品置顶窗口Z={rr2}")
    res.append(("竞品置顶窗口出现后，球被压到下层（预期如此）", rb2 >= 0 and rr2 >= 0 and rr2 < rb2))

    # 补救手段验证：raise_() 能否把球重新顶到 topmost 组最上
    ball.raise_()
    pump(300)
    rb3, rr3 = z_rank(int(ball.winId())), z_rank(hr)
    res.append((f"球 raise_() 后夺回最上：球Z={rb3} 竞品Z={rr3}", rb3 < rr3))

    # 补救手段验证 2：重设 flags（等价 _apply_topmost）能否夺回
    rival.raise_()
    pump(300)
    ball._apply_topmost()
    ball.show()
    ball.raise_()
    pump(300)
    rb4, rr4 = z_rank(int(ball.winId())), z_rank(hr)
    res.append((f"球 _apply_topmost+show+raise 后夺回：球Z={rb4} 竞品Z={rr4}", rb4 < rr4))

    rival.close()
    ball.close()

    fails = [t for t, ok in res if not ok]
    print()
    for t, ok in res:
        print(f"{'PASS' if ok else 'FAIL'}  {t}")
    print(f"\n总判定：{'PASS' if not fails else 'FAIL(' + str(len(fails)) + ' 项)'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
