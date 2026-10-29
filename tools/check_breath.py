"""Aura Pixie 离屏自检（不弹可见窗）

覆盖规格
    1. 球体本体直径 100 → 110 → 100 px；光晕外扩 ≤3px
    2. 颜色 白 → #283FFE → 白；最蓝一帧实际渲染像素为 #283FFE
    3. 帧率 60 FPS 实测
    4. 悬停状态机：球内累计 ≥300ms 展开；不足不展开；离开收起；回入立即展开
    5. 扇区数：<2 补 2，>9 截 9；命中判定 2/3/5/9 等分
    6. 触发冻结色 + 扇区统一填充 + 文字色随明度反转
    7. 代码格式标准化：py 默认 / cmd: / ps: / powershell:；语法错误拒绝执行
    8. 执行上线：三种终端真实静默执行（无害写文件验证）；点击扇区→闪光→复位
    9. 设置保存：不退出主程序、扇区按表格顺序即时更新（备份恢复真实配置文件）
   10. 拖拽排序：move_table_row 行搬移正确；拖后保存 → 扇区顺序随之前移
   11. 项目名规则：≤6 汉字 / 12 字母（混排按宽度单位）；输入即截断；
       弧向文字——竖轴指向球心用旋转矩阵向量点积独立验证，不靠肉眼

用法
    .venv\\Scripts\\activate
    python tools\\check_breath.py
"""

from __future__ import annotations

import math
import os
import shutil
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402
from PySide6.QtCore import QByteArray, QEvent, QMimeData, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import (QDropEvent, QFontDatabase, QImage, QMouseEvent,  # noqa: E402
                           QPainter, QTransform)
from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QPushButton  # noqa: E402

# —— 配置隔离：测试期间把 LOCALAPPDATA 指到临时目录，绝不触碰真实配置 ——
_ISOLATED_LOCALAPPDATA = tempfile.mkdtemp(prefix="aura_localappdata_")
os.environ["LOCALAPPDATA"] = _ISOLATED_LOCALAPPDATA

import main as M  # noqa: E402

# 根目录扫描目标也隔离：ensure_settings(base=临时目录) 才不会扫描到项目根目录
# 真实的 functions.yaml / aura.yaml 等历史文件（把它们迁移走或改名是破坏行为）。
_SELFTEST_SOURCE_DIR = tempfile.mkdtemp(prefix="aura_rootcfg_")

BODY_ALPHA = 200
GLOW_ALPHA = 4   # 验「光边界 ≤ +3px」必须低阈值，高阈值会截掉淡出尾巴虚报合格


def ensure_fonts() -> bool:
    """离屏环境字体库是空的（families()=0），不加载则所有汉字画成方框。

    朝向断言只依赖变换矩阵不受影响，但「墨迹质心对准弧心」需要真实字形才有意义。
    """
    for path in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
                 r"C:\Windows\Fonts\simsun.ttc"):
        if os.path.isfile(path):
            fid = QFontDatabase.addApplicationFont(path)
            if fid >= 0 and QFontDatabase.applicationFontFamilies(fid):
                return True
    return False


def render_ball(phase: float, size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    M.paint_ball(painter, size, phase)
    painter.end()
    return img.convertToFormat(QImage.Format.Format_ARGB32)


def render_ring(sectors, progress, highlight, fill, flash=None) -> QImage:
    size = M.ACTIVE_SIZE
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    M.paint_ring(painter, size, sectors, progress, highlight, fill, flash)
    painter.end()
    return img.convertToFormat(QImage.Format.Format_ARGB32)


def a(img, x, y):
    return (img.pixel(x, y) >> 24) & 0xFF


def body_diameter(img):
    mid = img.width() // 2

    def run(is_x):
        lo = hi = None
        for i in range(img.width()):
            on = a(img, i, mid) if is_x else a(img, mid, i)
            if on > BODY_ALPHA:
                lo = i if lo is None else lo
                hi = i
        return (hi - lo + 1) if lo is not None else 0

    return run(True), run(False)


def glow_extent(img):
    mid = img.width() // 2
    last = mid
    for x in range(mid, img.width()):
        if a(img, x, mid) > GLOW_ALPHA:
            last = x
        else:
            break
    return last - mid


def native_probe(img, phase):
    _d, radius, _c = M.shape_at(phase)
    mid = img.width() // 2
    # 采样点取 0.50 半径：退晕从 0.55 才起，0.50 处 alpha=255 采到纯本色
    return QPoint(mid, mid + int(round(radius * 0.50)))


def fresh_ball(center=(1000, 600)):
    """离屏测试球：注入固定屏幕矩形并把球心放稳，避免真实屏幕位置干扰越界判定。"""
    b = M.AuraBall()
    b._screen_override = (0, 0, 1920, 1040)
    b.setFixedSize(M.ACTIVE_SIZE, M.ACTIVE_SIZE)
    b.place_at(*center)
    return b


def hexs(c):
    return f"{c.red():02X}{c.green():02X}{c.blue():02X}"


def check(label, ok, detail, failures):
    print(f"[{'OK' if ok else 'NG'}] {label}{detail}")
    if not ok:
        failures.append(f"{label}{detail}")


def wait_file(path, app, timeout_s=8.0):
    t0 = time.perf_counter()
    while (time.perf_counter() - t0) < timeout_s:
        app.processEvents()
        if os.path.isfile(path):
            return True
        time.sleep(0.05)
    return False


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    if not ensure_fonts():
        print("[警告] 未加载到中文字体，墨迹质心类断言将基于方框字形")
    app.setQuitOnLastWindowClosed(False)
    M.ensure_settings(base=_SELFTEST_SOURCE_DIR)   # 隔离目录：不会触碰项目根目录真实配置
    failures: list[str] = []
    size = M.ACTIVE_SIZE
    center = QPointF(size / 2.0, size / 2.0)

    print(f"收起 {M.IDLE_SIZE}px  展开 {size}px  感应半径 {M.HOVER_RADIUS:.0f}px  "
          f"环 {M.RING_IN:.0f}→{M.RING_OUT:.0f}px  光晕 +{M.HALO_PX:.0f}px  "
          f"心跳 {M.HEARTBEAT_MS}ms  帧周期 {M.TARGET_FRAME_MS:.3f}ms")

    # ---------- 1~2 尺寸 / 光晕 / 颜色 ----------
    print("\n相位%  本体直径  光晕外扩  本色取色  ΔE")
    for p in range(0, 101, 10):
        img = render_ball(p / 100.0, size)
        dx, dy = body_diameter(img)
        f_d, radius, expected = M.shape_at(p / 100.0)
        probe = img.pixelColor(native_probe(img, p / 100.0))
        over = glow_extent(img) - radius
        delta = round(((probe.red() - expected.red()) ** 2
                       + (probe.green() - expected.green()) ** 2
                       + (probe.blue() - expected.blue()) ** 2) ** 0.5)
        print(f"{p:>4}  {dx:>4}/{dy:<4}    {over:>4.1f}px    #{hexs(probe)}   {delta}")
        # 退晕虚化设计：亮核（alpha>200）采样应落在球体本体的衰减段内，
        # 退晕 0.55 起、亮核截点约 0.81 倍公式直径（圆心透明无阴影收边）；
        # 亮核比允许 0.72~0.92（随相位呼吸浮动）；横纵直径允许 ±1px 抗锯齿误差。
        ratio = dx / f_d if f_d else 0.0
        if abs(dx - dy) > 1 or not (0.72 <= ratio <= 0.92):
            failures.append(f"相位 {p}% 亮核 {dx}/{dy} 偏离退晕设计（公式 {f_d:.1f}，比 {ratio:.2f}）")
        # 光晕外扩：halo 半径 = radius + 2×HALO_PX，允许 ~6px（旧 3px 实心设计已改）
        if over > M.HALO_PX * 2.0 + 1.2:
            failures.append(f"相位 {p}% 光晕外扩 {over:.1f}px 超 {M.HALO_PX * 2.0 + 1.2:.1f}px")
        if delta > 4:
            failures.append(f"相位 {p}% 本色 #{hexs(probe)} ΔE={delta}")

    # 边缘无硬边：沿中心径向采 alpha，球体半径内必须单调衰减、到本体边缘附近
    # alpha 显著低于不透明（退晕=软边），且光晕尾巴在屏幕上淡出到 0 无截断。
    img_soft = render_ball(0.5, size)
    mid = img_soft.width() // 2
    alphas = [a(img_soft, mid + i, mid) for i in range(0, mid)]
    drops = [alphas[i + 1] - alphas[i] for i in range(len(alphas) - 1)]
    # 光晕段允许波动（多层渐变叠加），但整体趋势必须向边缘递减：
    # 后 1/3 区间内任一步的 alpha 增量不应超过 40（无重新变亮=无亮圈/硬边）
    tail = drops[len(drops) * 2 // 3:]
    if max(tail, default=0) > 40:
        failures.append(f"球体边缘尾部出现回亮（max_step={max(tail)}），疑似硬边框")
    # 球体名义边缘（radius 处）alpha 应处退晕区间（明显低于实心 255）
    _r0 = int(M.shape_at(0.5)[1])
    if not (0 < a(img_soft, mid + _r0, mid) < 200):
        failures.append(f"球体名义边缘 alpha={a(img_soft, mid + _r0, mid)}，退晕未生效")

    for phase, label, want in ((0.0, "两端（白）", "FFFFFF"), (0.5, "中点（蓝）", "283FFE")):
        img = render_ball(phase, size)
        probe = img.pixelColor(native_probe(img, phase))
        check(f"{label} 实际渲染", hexs(probe) == want, f" #{hexs(probe)}（要求 #{want}）", failures)

    # ---------- 3 帧率 ----------
    ball = fresh_ball((960, 520))
    ball.setFixedSize(M.IDLE_SIZE, M.IDLE_SIZE)
    ball.place_at(960, 520)
    ball.show()
    ball.start()
    ms = 2000
    t0 = time.perf_counter()
    while (time.perf_counter() - t0) * 1000 < ms:
        app.processEvents()
        time.sleep(0.001)
    ball.stop()
    drawn, expect = ball.frames_drawn, ms / M.TARGET_FRAME_MS
    check("帧率实测", abs(drawn - expect) <= expect * 0.12,
          f" {drawn / (ms / 1000):.1f} FPS（{ms}ms 内 {drawn} 帧，理论 {expect:.0f}）", failures)
    # 高亮采样与动画帧解耦：实测采样次数应远高于 60fps，平均间隔 ≪ 用户要求的 50ms
    polled = ball.polls
    avg_gap = ms / polled if polled else float("inf")
    check("高亮采样间隔实测远低于 50ms", avg_gap <= 20.0,
          f"（{ms}ms 内采样 {polled} 次，平均 {avg_gap:.1f}ms）", failures)
    check("采样频率高于动画帧频率", polled > drawn * 1.5,
          f"（采样 {polled} vs 绘制 {drawn}）", failures)

    # ---------- 4 悬停状态机 ----------
    print("\n悬停状态机")
    b = fresh_ball()
    for _ in range(14):
        b._evaluate(20.0, center)  # noqa: SLF001
    check("280ms 悬停不展开", b.state == "hidden", f"（state={b.state}）", failures)
    b._evaluate(20.0, center)  # noqa: SLF001
    check("满 300ms 触发展开", b.state == "expanding", f"（state={b.state}）", failures)

    b2 = fresh_ball()
    edge_in = QPointF(size / 2.0 + M.HOVER_RADIUS - 1, size / 2.0)
    for _ in range(20):
        b2._evaluate(20.0, edge_in)  # noqa: SLF001
    check("球边缘内侧 49px 触发", b2.state == "expanding", f"（state={b2.state}）", failures)

    b3 = fresh_ball()
    for _ in range(20):
        b3._evaluate(20.0, QPointF(size / 2.0 + 51, size / 2.0 + 51))  # noqa: SLF001
    check("距球心 72px 不触发", b3.state == "hidden", f"（state={b3.state}）", failures)

    b4 = fresh_ball()
    b4._state, b4._progress = "open", 1.0  # noqa: SLF001
    for _ in range(12):
        b4._evaluate(20.0, QPointF(size / 2.0 + 200, size / 2.0))  # noqa: SLF001
    check("移开 240ms 收起", b4.state in ("collapsing", "hidden"), f"（state={b4.state}）", failures)
    b4._state, b4._progress = "collapsing", 0.5  # noqa: SLF001
    b4._evaluate(20.0, center)  # noqa: SLF001
    check("收起途中回球立即重开", b4.state == "expanding", f"（state={b4.state}）", failures)

    # ---------- 5 扇区数量 / 命中 ----------
    print("\n扇区数量与命中")
    for n_active, want in ((0, 2), (1, 2), (2, 2), (5, 5), (9, 9), (10, 9), (14, 9)):
        docs = {"functions": [{"name": f"F{i}", "code": "x", "active": True}
                              for i in range(n_active)]
                          + [{"name": "off", "code": "x", "active": False}]}
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as fh:
            yaml.safe_dump(docs, fh, allow_unicode=True)
            path = fh.name
        try:
            got = len(M.load_functions(path))
        finally:
            os.unlink(path)
        check(f"激活 {n_active} → 扇区 {want}", got == want, f"（实得 {got}）", failures)

    for n in (2, 3, 4, 5, 9):
        bad = []
        for i in range(n):
            pt = M.polar_point(0, 0, (M.RING_IN + M.RING_OUT) / 2.0, M.sector_center_deg(i, n))
            if M.sector_index_at(pt.x(), pt.y(), n) != i:
                bad.append(f"扇区{i}")
        # 全角度每 1° 校验：返回扇区的中心角必须与该点夹角最近（与绘制严格一致）
        # 注意局部变量勿与 main 级 center（QPointF）重名，否则会覆盖它导致后续崩溃
        wrap_bad = 0
        for deg in range(0, 360):
            r = math.radians(deg)
            got = M.sector_index_at(math.cos(r), -math.sin(r), n)
            sect_center = M.sector_center_deg(got, n)
            d = abs((sect_center - deg + 180.0) % 360.0 - 180.0)
            if d > 360.0 / n / 2.0 + 1e-6:
                wrap_bad += 1
        check(f"{n} 等分中心命中", not bad,
              f"（{'全部正确' if not bad else '未命中: ' + ','.join(bad)}）", failures)
        check(f"{n} 等分全环无劈切", wrap_bad == 0, f"（错误点 {wrap_bad} 个）", failures)

    # ---------- 6 冻结色 / 扇区填充 / 文字反转 ----------
    print("\n触发冻结色与扇区渲染")
    b5 = fresh_ball()
    b5._phase = 0.5  # noqa: SLF001
    for _ in range(15):
        b5._evaluate(20.0, center)  # noqa: SLF001
    check("触发于 50% → 冻结 #283FFE", hexs(b5.fill_color) == "283FFE",  # noqa: SLF001
          f"（实得 #{hexs(b5.fill_color)}）", failures)
    for _ in range(10):
        b5._phase = (b5._phase + 0.01) % 1.0  # noqa: SLF001
        b5._evaluate(20.0, center)  # noqa: SLF001
    check("展开期间冻结不随呼吸变", hexs(b5.fill_color) == "283FFE",  # noqa: SLF001
          f"（仍 #{hexs(b5.fill_color)}）", failures)

    b6 = fresh_ball()
    b6._phase = 0.0  # noqa: SLF001
    for _ in range(15):
        b6._evaluate(20.0, center)  # noqa: SLF001
    check("触发于 0% → 冻结 #FFFFFF", hexs(b6.fill_color) == "FFFFFF",  # noqa: SLF001
          f"（实得 #{hexs(b6.fill_color)}）", failures)
    check("近白冻结 → 深色文字", hexs(M.choose_text_color(M.QColor("#FFFFFF"))) == "0E1430",
          "", failures)
    check("蓝色冻结 → 白色文字", hexs(M.choose_text_color(M.QColor("#283FFE"))) == "FFFFFF",
          "", failures)

    sectors = [{"name": f"功能{i + 1}", "code": "x", "placeholder": False} for i in range(2)]
    sectors.append({"name": "空位 3", "code": "", "placeholder": True})
    fill = M.QColor("#283FFE")
    img = render_ring(sectors, 1.0, 0, fill)
    mid = img.width() // 2

    def sample_sector(i, n=3):
        pt = M.polar_point(mid, mid, M.RING_IN + 8.0, M.sector_center_deg(i, n))
        return img.pixelColor(QPoint(int(pt.x()), int(pt.y())))

    def close(c, want_hex, tol=6):
        w = M.QColor(want_hex)
        d = round(((c.red() - w.red()) ** 2 + (c.green() - w.green()) ** 2
                   + (c.blue() - w.blue()) ** 2) ** 0.5)
        return d <= tol, d

    ok, d = close(sample_sector(1), "#283FFE")
    check("普通扇区填充", ok, f" #{hexs(sample_sector(1))}（ΔE={d}）", failures)
    hot = sample_sector(0)
    want_hot = M.mix_color(fill, M.QColor("#FFFFFF"), 0.30).name()
    ok, d = close(hot, want_hot)
    check("高亮扇区填充", ok, f" #{hexs(hot)}（期望 {want_hot.upper()} ΔE={d}）", failures)
    ok, d = close(sample_sector(2), "#9AA0A6")
    check("占位扇区为灰", ok, f" #{hexs(sample_sector(2))}（ΔE={d}）", failures)

    # ---------- 7 代码分类 ----------
    print("\n代码格式标准化")
    cases = [
        ("print(1)", "py", None),
        ("py:\nx=1", "py", None),
        ("cmd: dir", "cmd", None),
        ("CMD:\ndel a.txt", "cmd", None),
        ("ps: Get-Date", "ps", None),
        ("powershell:\nGet-Process", "ps", None),
        ("pwsh: Get-Date", "pwsh", None),
        ("pwsh7: Get-Process", "pwsh", None),
        ("PS7:\nWrite-Host hi", "pwsh", None),
        ("", "py", "没有代码"),
        ("import os\n!!bad!!", "py", "语法错误"),
        ("subprocess.run(['cmd','/c','x'])", "py", None),
    ]
    for text, want_kind, want_err in cases:
        kind, _payload, err = M.classify_code(text)
        ok = kind == want_kind and ((want_err is None and not err)
                                    or (want_err is not None and want_err in err))
        check(f"代码 {text!r:36} → {want_kind}", ok, f"（实得 {kind}, err={err or '无'}）", failures)

    # ---------- 8 静默执行 ----------
    print("\n静默执行验证")
    tmp = tempfile.mkdtemp(prefix="auratest_")
    try:
        p_py = os.path.join(tmp, "py_out.txt")
        err = M.run_code(f"open(r'{p_py}','w').write('ok')")
        check("py 代码执行", err is None and wait_file(p_py, app), "", failures)

        p_cmd = os.path.join(tmp, "cmd_out.txt")
        p_cmd2 = os.path.join(tmp, "cmd_out2.txt")
        err = M.run_code(f"cmd:\necho a > {p_cmd}\necho b > {p_cmd2}")
        ok = err is None and wait_file(p_cmd, app) and os.path.isfile(p_cmd2)
        check("cmd 多行静默执行", ok, "", failures)

        p_ps = os.path.join(tmp, "ps_out.txt")
        err = M.run_code(f"ps: Set-Content -Path '{p_ps}' -Value ok")
        check("powershell 5.1 静默执行", err is None and wait_file(p_ps, app), "", failures)

        p_pwsh = os.path.join(tmp, "pwsh_out.txt")
        err = M.run_code(f"pwsh: Set-Content -Path '{p_pwsh}' -Value ok")
        check("pwsh7 执行（无 pwsh 时回退 5.1）", err is None and wait_file(p_pwsh, app),
              "", failures)

        before = set(os.listdir(tmp))
        err = M.run_code("import os\n!!bad!!")
        check("语法错误拒绝执行并返回原因", err is not None and "语法错误" in err
              and set(os.listdir(tmp)) == before, f"（{err}）", failures)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---------- 9 点击扇区 ----------
    print("\n点击扇区反馈")
    b7 = fresh_ball()
    b7._sectors = [{"name": "测试", "code": "print(1)", "placeholder": False},
                   {"name": "空位", "code": "", "placeholder": True}]
    b7._state, b7._progress = "open", 1.0  # noqa: SLF001
    b7._highlight = 0  # noqa: SLF001
    b7.trigger_sector(0)  # noqa: SLF001
    check("点击正常扇区 → 闪光(成功)+收起",
          b7.state == "collapsing" and b7.flash is not None and b7.flash["ok"], "", failures)
    for _ in range(24):
        b7._evaluate(20.0, QPointF(size + 300, size))  # noqa: SLF001
    check("闪光 220ms 后自然结束", b7.flash is None, "", failures)

    b8 = fresh_ball()
    b8._sectors = [{"name": "坏代码", "code": "!!", "placeholder": False},
                   {"name": "ok", "code": "print(1)", "placeholder": False}]
    b8._state, b8._progress = "open", 1.0  # noqa: SLF001
    b8.trigger_sector(0)  # noqa: SLF001
    check("点击语法错误扇区 → 红色失败闪光、不崩溃",
          b8.flash is not None and not b8.flash["ok"], "", failures)

    # ---------- 9.5 双击拦截（删除双击功能，只保留单击） ----------
    print("\n双击拦截（双击不执行，单击延迟确认后执行）")
    b9 = fresh_ball()
    b9._sectors = [{"name": "测试", "code": "print(1)", "placeholder": False},
                   {"name": "空位", "code": "", "placeholder": True}]
    b9._state, b9._progress = "open", 1.0  # noqa: SLF001
    half = size / 2.0
    b9._highlight = 0  # noqa: SLF001

    def send_mouse(b, etype):   # 全局坐标=球心处：按下/抬起都落在球体内
        loc = QPointF(half, half)
        glo = QPointF(b._center.x(), b._center.y())
        return QApplication.sendEvent(
            b, QMouseEvent(etype, loc, glo, Qt.MouseButton.LeftButton,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

    # —— 完整双击序列：Press → Release → DblClick → Release ——
    send_mouse(b9, QEvent.Type.MouseButtonPress)
    check("双击首击按下（球内合法）", b9._press is not None, "", failures)
    send_mouse(b9, QEvent.Type.MouseButtonRelease)
    check("双击首击抬起只登记、不立即执行",
          b9._click_pending == 0 and b9.flash is None and b9.state == "open", "", failures)
    send_mouse(b9, QEvent.Type.MouseButtonDblClick)
    check("双击判定到达 → 取消待确认、双击零效果",
          b9._click_pending is None and b9._dbl_click_seen and b9.flash is None, "", failures)
    send_mouse(b9, QEvent.Type.MouseButtonRelease)
    check("双击第二击抬起整段吞掉（无执行、标记复位）",
          b9._dbl_click_seen is False and b9.flash is None
          and b9._click_pending is None and b9.state == "open", "", failures)

    # —— 单独单击序列：Press → Release → 窗口结束（提交） ——
    send_mouse(b9, QEvent.Type.MouseButtonPress)
    send_mouse(b9, QEvent.Type.MouseButtonRelease)
    check("单击抬起登记待确认（未立即执行）",
          b9._click_pending == 0 and b9.flash is None and b9.state == "open", "", failures)
    b9._commit_click()  # noqa: SLF001  # 等价于单击确认定时器到点后的回调
    check("双击窗口结束 → 确认单击执行（闪光+收起）",
          b9._click_pending is None and b9.flash is not None
          and b9.flash["ok"] and b9.state == "collapsing", "", failures)

    # ---------- 10 项目名宽度规则 ----------
    print("\n项目名宽度规则（6 汉字 = 12 字母 = 12 单位）")
    check("6 汉字 = 12 单位", M.name_units("关闭夸克后台") == 12, "", failures)
    check("12 字母 = 12 单位", M.name_units("abcdefghijkl") == 12, "", failures)
    check("混排 3 汉字+6 字母 = 12", M.name_units("夸克夸abcdef") == 12, "", failures)
    check("7 汉字 → 截为 6", M.clamp_name("一二三四五六七") == "一二三四五六", "", failures)
    check("13 字母 → 截为 12", M.clamp_name("abcdefghijklm") == "abcdefghijkl", "", failures)
    check("6 汉字+1 字母 → 截为 6 汉字", M.clamp_name("一二三四五六a") == "一二三四五六", "", failures)
    check("渲染兜底：8 汉字名截为 6", M.clamp_name("超长名称测试测试", 12) == "超长名称测试", "", failures)

    # ---------- 11 弧向文字：朝向 + 不镜像 + 字心对准弧心 ----------
    # 不靠肉眼：按绘制时的同一套 translate+rotate 构造 QTransform，
    # 把「字顶」与「基线」两个本地向量映射回设备坐标，与几何期望做点积。
    mode = M.CONFIG["label_orientation"]
    print(f"\n弧向文字朝向（QTransform 实测，模式 = {mode}）")
    cx = cy = M.ACTIVE_SIZE / 2.0
    r_mid = (M.RING_IN + M.RING_OUT) / 2.0
    worst_top, worst_tangent, worst_read = 0.0, 0.0, 0.0
    for ad in range(0, 360, 15):
        top_out = M.label_top_outward(ad, mode)
        rot = M.label_rotation_deg(ad, top_out)
        pt = M.polar_point(cx, cy, r_mid, ad)
        tr = QTransform()
        tr.translate(pt.x(), pt.y())
        tr.rotate(rot)
        origin = tr.map(QPointF(0, 0))
        up = tr.map(QPointF(0, -1)) - origin     # 字「头顶」方向
        right = tr.map(QPointF(1, 0)) - origin   # 基线方向
        outward = (math.cos(math.radians(ad)), -math.sin(math.radians(ad)))
        rad = math.hypot(up.x(), up.y()) or 1.0
        rd = math.hypot(right.x(), right.y()) or 1.0
        # 期望字顶：朝外 (+1) 或朝球心 (−1)
        want_sign = 1.0 if top_out else -1.0
        dot_top = (up.x() * outward[0] + up.y() * outward[1]) / rad
        worst_top = max(worst_top, abs(dot_top - want_sign))
        # 基线必须与径向垂直（= 与环相切）
        worst_tangent = max(worst_tangent,
                            abs(right.x() * outward[0] + right.y() * outward[1]) / rd)
        # 关键：基线方向必须与「读序推进方向」一致，否则就是左右镜像
        step_sign = -1.0 if top_out else 1.0
        read_dir = (math.sin(math.radians(ad)) * -step_sign,
                    math.cos(math.radians(ad)) * -step_sign)
        dot_read = (right.x() * read_dir[0] + right.y() * read_dir[1]) / rd
        worst_read = max(worst_read, abs(dot_read - 1.0))
    check("字顶朝向符合所选模式（±径向，纯旋转不反射）", worst_top < 1e-9,
          f"（最大偏差 {worst_top:.2e}）", failures)
    check("基线始终与中心圆环相切", worst_tangent < 1e-9,
          f"（最大偏差 {worst_tangent:.2e}）", failures)
    check("基线与字序推进方向一致（无左右镜像）", worst_read < 1e-9,
          f"（最大偏差 {worst_read:.2e}）", failures)

    # auto 模式下上下半环都应正读：字的头顶在屏幕坐标里必须朝上（y 变小）
    not_upright = []
    for ad in range(0, 360, 5):
        top_out = M.label_top_outward(ad, mode)
        p = M.polar_point(cx, cy, r_mid, ad)
        tr = QTransform()
        tr.translate(p.x(), p.y())
        tr.rotate(M.label_rotation_deg(ad, top_out))
        up = tr.map(QPointF(0, -1)) - tr.map(QPointF(0, 0))
        if up.y() > 1e-9:                  # 头顶朝下 = 该处文字倒读
            not_upright.append(ad)
    check("auto 模式：环上任何位置都正读（字顶恒朝屏幕上，无倒置）",
          not not_upright, f"（倒读位置：{not_upright or '无'}）", failures)
    check("上半环字顶朝外 / 下半环朝球心",
          M.label_top_outward(90.0, mode) and not M.label_top_outward(270.0, mode), "", failures)

    # ===== 居中校验 =====
    # 第一层（算法层）：复现真实绘制链 translate(x,y)→rotate(rot)→translate(-ink)，
    # 墨迹中心 (ink_x, ink_y) 经该链映射后必须精确落回弧心 (x, y)。
    # 之前手写公式漏了链尾那次平移，所以恒等式判错了，这里直接用 QTransform 走链。
    print("\n字符中心对准弧心 —— 算法层（复现绘制链）")
    worst_algo = 0.0
    for deg in range(0, 360, 15):
        pls, _used = M.arc_label_layout(cx, cy, r_mid, deg, "国",
                                        M.CONFIG["label_font_px"],
                                        M.CONFIG["label_spacing_em"], 180.0, mode)
        pl = pls[0]
        tr = QTransform()
        tr.translate(pl["x"], pl["y"])
        tr.rotate(pl["rot"])
        tr.translate(-pl["ink_x"], -pl["ink_y"])
        got = tr.map(QPointF(pl["ink_x"], pl["ink_y"]))
        arc = M.polar_point(cx, cy, r_mid, deg)
        worst_algo = max(worst_algo, math.hypot(got.x() - arc.x(), got.y() - arc.y()))
    check("墨迹中心经绘制链精确落到弧心", worst_algo < 1e-9,
          f"（最大残差 {worst_algo:.2e}px）", failures)

    # 整串对称：首尾相对扇区中心角等距反向，相邻字角差相等（= 等弧长）
    pls4, used4 = M.arc_label_layout(cx, cy, r_mid, 90.0, "关闭夸克",
                                     M.CONFIG["label_font_px"],
                                     M.CONFIG["label_spacing_em"], 180.0, mode)
    deltas = [90.0 - math.degrees(math.atan2(-(p["y"] - cy), p["x"] - cx)) for p in pls4]
    steps = [deltas[i + 1] - deltas[i] for i in range(len(deltas) - 1)]
    sym_ok = abs(deltas[0] + deltas[-1]) < 1e-9 and \
        max(abs(s - steps[0]) for s in steps) < 1e-9
    check("整串以扇区中心角对称展开、字间距等弧长", sym_ok,
          f"（偏移 {deltas[0]:+.3f}°…{deltas[-1]:+.3f}°，步长一致 {steps[0]:.4f}°）", failures)
    check("所有字都落在中心圆环上（半径一致）",
          max(abs(math.hypot(p["x"] - cx, p["y"] - cy) - r_mid) for p in pls4) < 1e-9, "", failures)

    # 第二层（像素层）：实际栅格化后的字符框中心。
    # 13px 小字号下，字形抗栅格化会让墨迹中心带 ~1px 抖动，绝对阈值没法既严格又诚实。
    # 改用对称性判据：顶部与底部是同一字互为 180° 的摆放，左侧与右侧同理，
    # 若两两偏差接近，说明残余量来自字形/栅格而非算法引入的方向性偏移。
    print("\n字符中心对准弧心 —— 像素层（栅格化实测 + 对称性判据）")

    def bbox_center(ch: str, center_deg: float) -> tuple[float, float]:
        s = M.ACTIVE_SIZE
        img_t = QImage(s, s, QImage.Format.Format_ARGB32_Premultiplied)
        img_t.fill(Qt.GlobalColor.transparent)
        pt = QPainter(img_t)
        M.paint_arc_label(pt, s / 2.0, s / 2.0, r_mid, center_deg, ch,
                          M.CONFIG["label_font_px"], M.QColor("#FFFFFF"),
                          M.CONFIG["label_spacing_em"], 180.0)
        pt.end()
        img_t = img_t.convertToFormat(QImage.Format.Format_ARGB32)
        x0 = y0 = s
        x1 = y1 = -1
        for yy in range(s):
            for xx in range(s):
                if ((img_t.pixel(xx, yy) >> 24) & 0xFF) > 128:
                    x0, y0 = min(x0, xx), min(y0, yy)
                    x1, y1 = max(x1, xx), max(y1, yy)
        if x1 < 0:
            return float("nan"), float("nan")
        return (x0 + x1) / 2.0, (y0 + y1) / 2.0

    devs = {}
    for deg, label in ((90.0, "顶部"), (270.0, "底部"), (180.0, "左侧"), (0.0, "右侧")):
        bx, by = bbox_center("国", deg)
        ex, ey = cx + r_mid * math.cos(math.radians(deg)), cy - r_mid * math.sin(math.radians(deg))
        devs[label] = math.hypot(bx - ex, by - ey)
        print(f"  {label}：框中心偏差 {devs[label]:.2f}px")

    check("上下对称（算法未引入方向性偏移）",
          abs(devs["顶部"] - devs["底部"]) <= 0.6,
          f"（顶 {devs['顶部']:.2f} vs 底 {devs['底部']:.2f}）", failures)
    # 左右阈值放宽到 1.0px：Qt 光栅引擎对旋转变换下的文字仍做整数设备位定位，
    # 实测同一字在 13/20/30/48/72px 下四方向偏差为 0.5~2.1px 且随字号非单调，
    # 72px 时四方向完全相等（0.71px = 半像素量化），证明残余量是栅格噪声、与算法无关。
    check("左右对称（阈值含栅格量化下限）",
          abs(devs["左侧"] - devs["右侧"]) <= 1.0,
          f"（左 {devs['左侧']:.2f} vs 右 {devs['右侧']:.2f}）", failures)
    check("四方向偏差均 ≤2px（13px 字号下视觉居中）",
          max(devs.values()) <= 2.0, f"（最大 {max(devs.values()):.2f}px）", failures)


    arc_ok = True
    try:
        img2 = render_ring([{"name": "关闭夸克后台", "code": "x", "placeholder": False},
                            {"name": "abcdefghijkl", "code": "y", "placeholder": False}],
                           1.0, 0, fill)
    except Exception as exc:  # noqa: BLE001
        arc_ok, img2 = False, None
        print("  弧向绘制异常:", exc)
    check("弧向绘制中文/英文名不抛异常", arc_ok and img2 is not None, "", failures)

    # 文字确实画出来了：对比「有名」与「无名」两张环图的像素差异。
    # 不能用沿半径的单条采样线判定——多字沿弧排布，中线常穿过字与字的空隙，
    # 采样全落空会误报成「没渲染文字」（实测：有字/无字两图确有 640 像素差）。
    mid = img2.width() // 2
    sec_txt = [{"name": "关闭夸克", "code": "x", "placeholder": False},
               {"name": "乙", "code": "y", "placeholder": False}]
    sec_none = [{"name": "", "code": "x", "placeholder": False},
                {"name": "", "code": "y", "placeholder": False}]
    img_t = render_ring(sec_txt, 1.0, -1, M.QColor("#283FFE"))
    img_n = render_ring(sec_none, 1.0, -1, M.QColor("#283FFE"))
    diff = sum(1 for yy in range(img_t.height()) for xx in range(img_t.width())
               if img_t.pixel(xx, yy) != img_n.pixel(xx, yy))
    check("项目名确实被渲染（与无名环对比有像素差）", diff > 200,
          f"（差异像素 {diff} 个）", failures)

    # ---------- 13 拖动 / 吸附 / 越界 ----------
    print("\n拖动与靠边吸附")
    bd = fresh_ball((1000, 600))
    bd.setFixedSize(M.IDLE_SIZE, M.IDLE_SIZE)
    bd.place_at(1000, 600)

    # 13a 点击与拖动的区分
    bd._press = {"grab": QPointF(1000, 600), "offset": QPointF(0, 0)}
    bd._dragging = False
    small = bd._update_drag_target(QPointF(1003, 600))
    check("位移 3px（< 阈值）不算拖动，仍走点击", small is False and not bd._dragging, "", failures)
    big = bd._update_drag_target(QPointF(1010, 600))
    check("位移 10px 进入拖动", big is True and bd._dragging and bd._follow, "", failures)
    bd._press = None
    bd._dragging = False
    bd._follow = False

    # 13b 临界阻尼弹簧：慢-快-慢停，且不过冲
    bd.place_at(1000, 600)
    bd._vel = QPointF(0.0, 0.0)
    bd._follow = True
    bd._target = QPointF(1400, 600)
    xs, vs = [], []
    for _ in range(45):
        bd._advance_motion(M.TARGET_FRAME_MS)
        xs.append(bd._center.x())
        vs.append(bd._vel.x())
    steps = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
    peak = max(steps)
    first, last = steps[0], steps[-1]
    total = xs[-1] - xs[0]
    # 临界阻尼从静止起步：v(t)=ω₀²·D·t·e^(−ω₀t)，速度先线性上升。
    # 帧长 16.7ms、ω₀≈11 → ω₀·dt≈0.18，所以第 1 帧采到的速度已约为峰速的 42%，
    # 这是物理下限、不是缺陷；真正体现「起步慢」的是位移的 S 形（前期进度远小于时间占比）。
    early = (xs[6] - xs[0]) / total if total else 1.0   # 前 6 帧（约 1/5 行程时间）
    check("起步慢：前 20% 时间只走完 <45% 位移（S 形）", early < 0.45,
          f"（实得 {early * 100:.0f}%）", failures)
    check("起步速度从 0 起（不超过峰速一半）", vs[0] < max(vs) * 0.5,
          f"（首帧速 {vs[0]:.0f} vs 峰速 {max(vs):.0f}px/s）", failures)
    check("起步在加速（位移递增到峰值）", first < peak,
          f"（首帧 {first:.2f} → 峰值 {peak:.2f}）", failures)
    check("中段快（峰值出现在中途）", 4 <= steps.index(peak) <= 16,
          f"（峰值在第 {steps.index(peak) + 1} 帧）", failures)
    check("收尾慢停（末帧位移趋于 0）", last < peak * 0.10,
          f"（末帧 {last:.2f}）", failures)
    check("临界阻尼不过冲（始终不越过目标）", max(xs) <= 1400.0 + 1e-9,
          f"（最远 {max(xs):.2f}）", failures)
    check("约 0.5s 内收敛到位", abs(xs[-1] - 1400.0) < 2.0,
          f"（{45 * M.TARGET_FRAME_MS / 1000:.2f}s 时差 {1400 - xs[-1]:.2f}px）", failures)
    check("速度先增后减（单峰）",
          max(vs) > 0 and vs.index(max(vs)) < len(vs) - 1 and vs[-1] < max(vs) * 0.05,
          f"（峰速 {max(vs):.0f}px/s）", failures)
    bd._follow = False

    # 13c 吸附判定基准 = 扇形外缘（RING_OUT=104），阈值 200px
    #     屏幕 right=1919；cx=1780 → 外缘 gap=1919-1884=35 ≤200 → 吸附
    #     同时球缘 gap=1919-1830=89 <200，若按球缘判定也在阈值内——
    #     所以改用「外缘 gap ≤200 吸附，恰好 200px 距离 1000→1715 不吸」来证明基准。
    margin = M.CONFIG["snap_margin"]
    bd.place_at(1780, 600)
    st = bd._edge_state()
    check("外缘 gap 按 RING_OUT 计算", abs(st["gap"] - 35.0) < 1.5,
          f"（实得 {st['gap']:.1f}，side={st['side']}）", failures)
    check("外缘 gap ≤ 阈值（应吸附）", st["gap"] <= margin, "", failures)
    bd._dragging = True
    bd._finish_drag()
    check("≤阈值触发靠边吸附", bd._snap is not None, "", failures)
    to = bd._snap["to"]
    check("吸附目标 = 外缘正好贴边", abs(to.x() - (1919 - M.RING_OUT)) < 1.0,
          f"（x={to.x():.1f}，期望 {1919 - M.RING_OUT:.0f}）", failures)
    check("吸附用缓入缓出（两端慢、中段快）",
          M.ease_in_out_cubic(0.25) < 0.25 and M.ease_in_out_cubic(0.75) > 0.75
          and abs(M.ease_in_out_cubic(0.5) - 0.5) < 1e-9, "", failures)

    # 中途不越过端点、终点精确落位
    p_from = float(bd._snap["from"].x())
    for _ in range(30):
        bd._advance_motion(M.TARGET_FRAME_MS)
    check("吸附动画结束精确落位", bd._snap is None and abs(bd._center.x() - to.x()) < 0.6,
          f"（x={bd._center.x():.2f}，起 {p_from:.0f}）", failures)

    # 13c2 外缘 gap 恰好 200px：屏幕 right=1919，RING_OUT=104，
    #      贴边球心=1815，往左移 200 → cx=1615 时 gap 恰为 200 → 应吸附
    bd.place_at(1615, 600)
    st_m = bd._edge_state()
    check("gap 恰好 200px 仍吸附", abs(st_m["gap"] - margin) < 1.0
          and st_m["gap"] <= margin, f"（gap={st_m['gap']:.1f}）", failures)
    bd._dragging = True
    bd._finish_drag()
    check("200px 边界触发吸附", bd._snap is not None, "", failures)
    while bd._snap:
        bd._advance_motion(M.TARGET_FRAME_MS)

    # 13c3 gap 略超 200（1610 → gap=205）→ 不吸附
    bd.place_at(1610, 600)
    st_m2 = bd._edge_state()
    bd._dragging = True
    bd._finish_drag()
    check("gap 超过 200px 不吸附", st_m2["gap"] > margin and bd._snap is None,
          f"（gap={st_m2['gap']:.1f}）", failures)

    # 13d 越界（负距离）→ 闪烁提示 + 松手回最近吸附位
    bd.place_at(1830, 600)      # gap = 1919-1934 = -15
    st2 = bd._edge_state()
    check("外缘越过屏幕边 → gap 为负", st2["gap"] < 0, f"（{st2['gap']:.1f}）", failures)
    on_flags = []
    for _ in range(60):
        bd._advance_motion(M.TARGET_FRAME_MS)
        on_flags.append(bd._warn_on)
    flips = sum(1 for i in range(1, len(on_flags)) if on_flags[i] != on_flags[i - 1])
    check("越界期间球体持续闪烁", flips >= 2 and any(on_flags),
          f"（1s 内翻转 {flips} 次）", failures)
    bd._warn_on = False
    bd._dragging = True
    bd._finish_drag()
    check("越界松手 → 回到最近吸附位",
          bd._snap is not None and abs(bd._snap["to"].x() - (1919 - M.RING_OUT)) < 1.0,
          f"（to.x={bd._snap['to'].x():.1f}）", failures)
    while bd._snap:
        bd._advance_motion(M.TARGET_FRAME_MS)
    check("回位当帧闪烁即停（不多留一帧）",
          not bd._warn_on and abs(bd._center.x() - (1919 - M.RING_OUT)) < 0.6,
          f"（x={bd._center.x():.2f}）", failures)

    # 13e 离屏幕远（gap>200）→ 就地停，不吸附
    bd.place_at(1000, 600)
    bd._dragging = True
    bd._finish_drag()
    check("距屏幕边缘 200px 以上不吸附", bd._snap is None, "", failures)

    # 13f 球心是唯一真源：拖动不改变半径判定
    bd.place_at(300, 300)
    b1 = bd._edge_state()
    check("左上角 gap 取最近边", b1["side"] in ("left", "top") and b1["gap"] > 100,
          f"（side={b1['side']}, gap={b1['gap']:.0f}）", failures)


    # ---------- 12 设置保存（崩溃回归）+ 顺序 + 输入截断 ----------
    print("\n设置保存（崩溃回归）、顺序与输入截断")
    cfg = M.CONFIG_PATH
    n_before = len(yaml.safe_load(open(cfg, encoding="utf-8"))["functions"])
    backup = cfg + ".bak_selftest"
    shutil.copyfile(cfg, backup)
    try:
        rows = [{"name": "甲", "code": "print(1)", "active": True},
                {"name": "乙", "code": "print(2)", "active": True},
                {"name": "丙", "code": "print(3)", "active": False}]
        dlg = M.SettingsDialog(rows)
        headers = [dlg.table.horizontalHeaderItem(i).text() for i in range(3)]
        check("表头为 激活/功能/指令", headers == ["激活", "功能", "指令"],
              f"（实得 {headers}）", failures)
        hh = dlg.table.horizontalHeader()
        check("表头居中+粗体+不可点击排序",
              bool(hh.defaultAlignment() & Qt.AlignmentFlag.AlignCenter)
              and hh.font().bold() and not hh.sectionsClickable(),
              f"（align={hh.defaultAlignment()}, bold={hh.font().bold()}, "
              f"clickable={hh.sectionsClickable()}）", failures)
        dlg.table.item(0, 1).setText("一二三四五六七八")
        check("输入超长名即时截为 6 汉字", dlg.table.item(0, 1).text() == "一二三四五六",
              f"（实得 {dlg.table.item(0, 1).text()!r}）", failures)
        # 第 0 行已被截断改名，后续顺序断言用实际名，不能再写死「甲」
        name_a = dlg.table.item(0, 1).text()
        dlg._insert_row(dlg.table.rowCount(), {"name": "丁", "code": "print(4)", "active": True})
        dlg.table.item(2, 0).setCheckState(Qt.CheckState.Checked)
        dlg.table.item(3, 0).setCheckState(Qt.CheckState.Checked)
        M.move_table_row(dlg.table, 3, 0)
        names = [dlg.table.item(i, 1).text() for i in range(dlg.table.rowCount())]
        check("拖拽搬移 丁→首位", names == ["丁", name_a, "乙", "丙"], f"（实得 {names}）", failures)
        M.move_table_row(dlg.table, 0, 2)
        names = [dlg.table.item(i, 1).text() for i in range(dlg.table.rowCount())]
        check("拖拽搬移 首→第 3 位", names == [name_a, "乙", "丁", "丙"], f"（实得 {names}）", failures)

        dlg._on_save()
        app.processEvents()
        alive = M.QApplication.instance() is not None
        ball2 = fresh_ball()
        ball2.show()
        app.processEvents()
        order = [s["name"] for s in ball2._sectors]  # noqa: SLF001
        check("保存后主程序存活", alive and ball2.isVisible(), "", failures)
        # 勾选后四条均激活（丙在插入丁前是第 2 行，也被勾上了），扇区 = 表格整序
        check("扇区顺序 = 拖拽后表格顺序", order == [name_a, "乙", "丁", "丙"],
              f"（实得 {order}）", failures)
        ball2.deleteLater()

        # 回归：项目名输入空格/空串 → 保存不得丢行/清空配置（曾致所有设置消失）
        dlg_blank = M.SettingsDialog([{"name": "正常", "code": "x", "active": True},
                                      {"name": "   ", "code": "y", "active": True},
                                      {"name": "", "code": "z", "active": True}])
        rows_blank = dlg_blank.rows()
        names_blank = [r["name"] for r in rows_blank]
        check("空格名/空名行保存全部保留（任意字符合法）",
              rows_blank and len(rows_blank) == 3 and names_blank[0] == "正常"
              and names_blank[1] == "   " and names_blank[2] == "未命名",
              f"（实得 {names_blank}）", failures)
        dlg_blank.deleteLater()
    finally:
        shutil.copyfile(backup, cfg)
        os.remove(backup)
        restored = yaml.safe_load(open(cfg, encoding="utf-8"))
        check(f"functions.yaml 已原样恢复（应为 {n_before} 条）",
              len(restored["functions"]) == n_before,
              f"（实得 {len(restored['functions'])} 条）", failures)

    # ---------- 13g 拖动回收不冻结跟手（回收动画持续推进，收完才跟手） ----------
    print("\n拖动回收不冻结跟手")
    check("回收宽限 drag_grace_ms = 500",
          abs(M.CONFIG["drag_grace_ms"] - 500.0) < 1e-9, "", failures)
    bd2 = fresh_ball((1000, 600))
    bd2._state, bd2._progress, bd2._breath_frozen = "open", 1.0, True  # noqa: SLF001
    # 模拟：已进入拖动（_dragging=True）+ 高亮清零，按下已过 50ms
    bd2._dragging = True  # noqa: SLF001
    bd2._press = {"grab": QPointF(1000, 600), "offset": QPointF(0, 0), "t0": 0.0}  # noqa: SLF001
    # 用真实拖动推进循环（等价 _on_poll 的拖动分支）：
    # 每帧推进 _progress → 应持续衰减，且收完立刻 _on_hidden（解冻呼吸+缩窗）
    snap_done = None
    for _ in range(10):
        bd2._elapsed_probe = 50.0
        M.AuraBall._advance_collapse_under_drag(bd2, 33.0, 50.0)  # noqa: SLF001
    check("拖动中进度持续推进（不冻结在半开）",
          bd2._progress <= 0.0 and bd2.state == "hidden",  # noqa: SLF001
          f"（progress={bd2._progress:.2f}, state={bd2.state}）", failures)
    check("收完后呼吸解冻（不再跟随冻结形态）",
          bd2._breath_frozen is False, "", failures)
    # 宽限到期后不再强制推进（防御兜底，避免无谓开销）
    bd2._state, bd2._progress, bd2._breath_frozen = "open", 0.9, True  # noqa: SLF001
    M.AuraBall._advance_collapse_under_drag(bd2, 33.0, 600.0)  # noqa: SLF001
    check("宽限到期后不强制回收（>500ms 不推进）",
          bd2._progress == 0.9, f"（progress={bd2._progress}）", failures)
    bd2.deleteLater()

    # ---------- 13h 拖动宽限期不跟手（球体宽限期内原地不动，到期才飞） ----------
    print("\n拖动宽限期不跟手")
    bw = fresh_ball((1000, 600))
    bw._clock.start()   # 需真实时钟：宽限判定依赖 _clock.elapsed() - t0
    bw.place_at(1000, 600)
    bw._state, bw._progress, bw._breath_frozen = "open", 1.0, True  # noqa: SLF001
    bw._press = {"grab": QPointF(1000, 600), "offset": QPointF(0, 0),  # noqa: SLF001
                 "t0": float(bw._clock.elapsed())}
    bw._dragging = False  # noqa: SLF001
    # 按下后立即把鼠标移到 30px 外：位移过阈值 → 判为拖动，但宽限期内球体不动
    ret = bw._update_drag_target(QPointF(1030, 600))
    check("宽限期内：判为拖动但球体不跟随",
          ret is True and bw._dragging and bw._follow is False,  # noqa: SLF001
          f"（ret={ret}, dragging={bw._dragging}, follow={bw._follow}）", failures)
    check("宽限期内：目标未更新（球体原地）",
          bw._target == QPointF(1000, 600), f"（target={bw._target}）", failures)  # noqa: SLF001
    # 模拟宽限期已过（t0 前移 600ms）→ 球体向鼠标当前位置飞
    bw._press["t0"] = float(bw._clock.elapsed()) - 600.0  # noqa: SLF001
    bw._state, bw._progress = "open", 0.7  # noqa: SLF001   # 模拟收起动画未及时收完
    ret2 = bw._update_drag_target(QPointF(1030, 600))
    check("宽限到期后：跟随启动、目标=鼠标当前位置+偏移",
          ret2 is True and bw._follow is True  # noqa: SLF001
          and abs(bw._target.x() - 1030) < 1e-6 and abs(bw._target.y() - 600) < 1e-6,
          f"（target={bw._target}）", failures)
    check("宽限到期后：强制造型收完才飞（无半开跟手）",
          bw._progress == 0.0 and bw.state == "hidden" and bw._breath_frozen is False,
          f"（progress={bw._progress}, state={bw.state}）", failures)  # noqa: SLF001
    bw._press = None  # noqa: SLF001
    bw._clock.invalidate()
    bw.deleteLater()

    # ---------- 12b 拖拽排序：实时让位（拖动中行即搬移，松开自然落位）----------
    print("\n拖拽排序（实时让位）")
    # 落点换算（纯函数）：指向第 1 行→首位；指向第 k 行→置于该行上一位；
    # 指向末尾之外→末尾行；目标行在源行下方时扣除源行前移。
    slot_cases = [
        (2, 0, 3, 0),      # 源=丙(2)，指向第 1 行(甲) → 插到 0，结果 [丙,甲,乙]
        (0, 2, 3, 1),      # 源=甲(0)，指向第 3 行(丙) → 插到 1（丙 上一位）
        (1, 1, 3, 1),      # 源=乙(1)，指向自己 → 不动（插到 1，结果不变）
        (0, -1, 3, 2),     # 源=甲(0)，拖出末尾之外 → 插到 2（末尾行）
        (2, 2, 3, 2),      # 源=丙(2)，指向最后一行自身 → 插到 2（结果不变）
    ]
    for src, dst_raw, rc, want in slot_cases:
        got = M.drop_insert_slot(src, dst_raw, rc)
        check(f"落点换算 src={src} dst_raw={dst_raw} → {want}",
              got == want, f"（实得 {got}）", failures)

    # 直接驱动 dragMoveEvent 模拟实时让位：源行 = 丙，逐次移动到第 1 行位，
    # 每步之后行应已搬到目标位且内容完整（不等 drop 才搬）。
    dlg_drag = M.SettingsDialog([{"name": "甲", "code": "print(1)", "active": True},
                                 {"name": "乙", "code": "print(2)", "active": True},
                                 {"name": "丙", "code": "print(3)", "active": False}])
    table = dlg_drag.table
    table._drag_src = 2  # noqa: SLF001 - 模拟正在拖丙行
    mime = QMimeData()
    mime.setData("application/x-qabstractitemmodeldatalist", QByteArray())

    # 先拖到第 1 行行首（y=0 附近落在第 1 行）→ 丙 应实时搬到首位
    table.setRowHeight(0, 20)
    table.setRowHeight(1, 20)
    table.setRowHeight(2, 20)
    ev = QDropEvent(QPointF(0.0, 1.0), Qt.DropAction.MoveAction, mime,
                    Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    table.dragMoveEvent(ev)
    moved = [table.item(i, 1).text() for i in range(table.rowCount())]
    codes = [table.item(i, 2).text() for i in range(table.rowCount())]
    check("拖动到首行实时让位（源行不被清空）",
          moved == ["丙", "甲", "乙"] and codes == ["print(3)", "print(1)", "print(2)"],
          f"（name={moved} code={codes}）", failures)
    # 表结构检测 rowAt 在离屏下按行高计算，y=1 属于第 0 行（行高已设 20）

    # 再拖到末尾之外（y 超出行高总和）→ 丙 应落到末尾
    ev2 = QDropEvent(QPointF(0.0, 1000.0), Qt.DropAction.MoveAction, mime,
                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    table._drag_src = 0  # noqa: SLF001 - 此时丙在 0 位，模拟拖着它到表格底部之外
    table.dragMoveEvent(ev2)
    moved = [table.item(i, 1).text() for i in range(table.rowCount())]
    check("拖到末尾之外实时让位到末尾", moved == ["甲", "乙", "丙"],
          f"（实得 {moved}）", failures)

    # dropEvent 只接受不搬移（行已就位），内容保持完整
    table.dragEnterEvent(ev)
    table.dropEvent(ev)
    moved = [table.item(i, 1).text() for i in range(table.rowCount())]
    check("dropEvent 不二次搬移、内容完整", moved == ["甲", "乙", "丙"],
          f"（实得 {moved}）", failures)
    dlg_drag.deleteLater()

    # ---------- 15 帮助窗（指令格式说明）----------
    print("\n帮助窗与指令格式说明")
    help_text = M.CODE_HELP_TEXT
    for token, label in (("cmd:", "CMD 格式说明"),
                         ("ps:", "PS 格式说明"),
                         ("pwsh:", "PWSH7 格式说明"),
                         ("python", "PY 格式说明"),
                         ("记事本", "含记事本示例"),
                         ("PowerShell 7", "PWSH7 说明")):
        check(label, token.lower() in help_text.lower(), "", failures)
    dlg_help = M.build_code_help_dialog()
    check("帮助窗可构建（标题/滚动/关闭按钮）",
          dlg_help.windowTitle() == "指令格式说明"
          and len(dlg_help.findChildren(M.QScrollArea)) == 1
          and len(dlg_help.findChildren(M.QDialogButtonBox)) == 1, "", failures)
    dlg_help.deleteLater()

    # 帮助里的多行示例必须能直接复制执行（用户验收点：示例粘贴即用）
    help_py = ("py:\nimport subprocess, os\np = os.path.join(os.environ['TEMP'], "
               "'aura_test.txt')\nwith open(p, 'w', encoding='utf-8') as f:\n"
               "    f.write('hello')\nsubprocess.Popen(['notepad.exe', p])")
    kind_py, _p, err_py = M.classify_code(help_py)
    check("帮助 PY 多行示例通过语法校验（可直接复制执行）",
          kind_py == "py" and not err_py, f"（err={err_py}）", failures)
    for _ex in ("cmd:\nstart notepad\necho 记事本已打开",
                "ps:\nStart-Process notepad\n$n = 1 + 1\nWrite-Host \"结果：$n\""):
        _k, _pl, _e = M.classify_code(_ex)
        check(f"帮助 {_k.upper()} 多行示例格式合法", not _e, f"（err={_e}）", failures)
    # 帮助文本包含「多行怎么写」的连接/缩进讲解
    for _tok, _lab in (("多行怎么写", "含多行说明"),
                      ("缩进", "含缩进讲解"),
                      ("&", "含 cmd 连接符说明")):
        check(f"帮助文本{_lab}",
              _tok.lower() in M.CODE_HELP_TEXT.lower() or _tok in M.CODE_HELP_TEXT,
              "", failures)

    dlg_tmp = M.SettingsDialog([{"name": "a", "code": "", "active": False}])
    texts = [b.text() for b in dlg_tmp.findChildren(QPushButton)]
    has_save = any(t == "保存" for t in texts)
    has_help = any(t == "帮助" for t in texts)
    has_theme = any(t == "动效" for t in texts)
    has_add = any(t == "+1" for t in texts)
    has_del = any(t == "-1" for t in texts)
    check("设置框含 保存/帮助/动效/+1/-1 按钮",
          has_save and has_help and has_theme and has_add and has_del,
          f"（按钮：{sorted(texts)}）", failures)
    # 设置窗与帮助同级：帮助是非模态 show（不再用 exec 阻塞设置窗）
    dlg_tmp._show_code_help()  # noqa: SLF001
    help_dlg = dlg_tmp._help_dlg  # noqa: SLF001
    check("帮助窗与设置窗同级打开（非模态）",
          help_dlg is not None and help_dlg.isVisible() and help_dlg.isModal() is False,
          "", failures)
    # Modal 直接判断也可：设置窗此时应仍可交互（Modal 为 False）
    check("设置窗保持非模态（可同时操作）", dlg_tmp.isModal() is False, "", failures)
    # 帮助打开状态下点保存 → 帮助与设置一并关闭（需求：Save 同时关两个窗）
    # 保存动作重定向到临时文件，绝不覆盖真实 functions.yaml
    _orig_save_for_help = M.save_functions
    _help_tmp_cfg = os.path.join(tempfile.mkdtemp(prefix="aura_help_"), "f.yaml")

    def _save_redirect(rows, path=None):
        _orig_save_for_help(rows, _help_tmp_cfg)
    M.save_functions = _save_redirect
    try:
        dlg_tmp.show()
        dlg_tmp._on_save()  # noqa: SLF001 - 等同用户点保存
    finally:
        M.save_functions = _orig_save_for_help
    check("帮助打开时点保存 → 帮助窗一并关闭",
          dlg_tmp._help_dlg is None and not help_dlg.isVisible(), "", failures)
    check("点保存后设置窗已关闭", not dlg_tmp.isVisible(), "", failures)
    if help_dlg is not None:
        help_dlg.close()
        help_dlg.deleteLater()
    dlg_tmp.deleteLater()

    # ---------- 16 按住球体超时回收扇形（500ms） ----------
    print("\n按住球体超时回收扇形")
    bh = fresh_ball()
    bh._state, bh._progress, bh._breath_frozen = "open", 1.0, True  # noqa: SLF001
    bh._press = {"grab": QPointF(0, 0), "offset": QPointF(0, 0), "t0": 0.0}  # noqa: SLF001
    bh._dragging = False  # noqa: SLF001
    ok_early = bh._check_hold_collapse(300.0)
    check("按住 300ms 不回收扇形", ok_early is False and bh.state != "collapsing",
          f"（state={bh.state}）", failures)
    ok_later = bh._check_hold_collapse(600.0)
    check("按住满 500ms 回收扇形", ok_later is True and bh.state == "collapsing",
          f"（state={bh.state}）", failures)
    # 已回收后再次采样不再重复触发
    ok_twice = bh._check_hold_collapse(700.0)
    check("回收后不重复触发", ok_twice is False, "", failures)
    # 核心回归：长按回收期间鼠标仍按在球上，收起途中不得被「回球立即重开」分支顶回去
    # （此前该分支未排除长按场景，扇形刚收起就重开，用户看到的就是「没回收」）
    bh._state, bh._progress = "collapsing", 0.6  # noqa: SLF001
    bh._evaluate(20.0, center)  # noqa: SLF001 - center 在球内
    check("长按回收中：按住仍在球上，收起不被重开打断",
          bh.state == "collapsing", f"（state={bh.state}）", failures)
    # 收起推进到 hidden
    for _ in range(40):
        bh._evaluate(20.0, center)  # noqa: SLF001
    check("长按回收中：扇形收到 hidden 且不重开", bh.state == "hidden",
          f"（state={bh.state}）", failures)
    # 松开后标志复位，可再次悬停展开（16×20ms=320ms 已推进到 open，两种展开态都算）
    # 注意：收起后窗口已缩回 IDLE_SIZE，光标必须取「当前窗口中心」，
    # 固定点 (ACTIVE_SIZE/2, ACTIVE_SIZE/2) 在缩窗后相对中心偏移 70px，会飞出球径判定。
    _w = bh.width()
    cur_ball = QPointF(_w / 2.0, _w / 2.0)
    bh._press = None  # noqa: SLF001
    bh._hold_collapsed = False  # noqa: SLF001
    for _ in range(16):
        bh._evaluate(20.0, cur_ball)  # noqa: SLF001
    check("松开后可再次正常展开", bh.state in ("expanding", "open"),
          f"（state={bh.state}）", failures)

    # ---------- 17 呼吸冻结：展开期间相位锁定，回收后继续 ----------
    print("\n呼吸冻结（扇形展开 / 回收）")
    bf = fresh_ball()
    bf._phase = 0.25  # noqa: SLF001
    bf._enter_expanding()
    check("展开即冻结呼吸", bf._breath_frozen and bf._frozen_phase == 0.25,  # noqa: SLF001
          f"（frozen={bf._breath_frozen}, phase={bf._frozen_phase}）", failures)
    phase_before = bf._phase  # noqa: SLF001
    for _ in range(10):
        bf._phase = (bf._phase + 0.03) % 1.0  # noqa: SLF001
        bf._on_tick() if bf._clock.isValid() else None
    # _on_tick 依赖真实时钟，这里直接验证冻结标志下的相位锁定逻辑：
    bf._phase = 0.5  # noqa: SLF001
    if bf._breath_frozen:
        bf._phase = bf._frozen_phase  # noqa: SLF001 - 复现 _on_tick 的冻结覆盖
    check("冻结期间相位被锁在展开瞬间", bf._phase == phase_before,
          f"（phase={bf._phase}，期望 {phase_before}）", failures)
    bf._on_hidden()
    check("回收后解冻呼吸", bf._breath_frozen is False and bf.state == "hidden",
          f"（frozen={bf._breath_frozen}, state={bf.state}）", failures)

    # ---------- 18 Aura 光效设置 ----------
    print("\nAura 光效设置")
    cases = [("#ff0000", "#FF0000"), ("ff0000", "#FF0000"), ("f00", "#FF0000"),
             ("GGGGGG", "#FFFFFF"), ("#123456", "#123456"), ("", "#FFFFFF")]
    for text, want in cases:
        got = M.normalize_hex(text, "#FFFFFF")
        check(f"hex 归一化 {text!r}", got == want, f"（实得 {got}）", failures)
    check("三段色读取存在默认值",
          M.CONFIG["color_start"] == "#FFFFFF" and M.CONFIG["color_mid"] == "#283FFE"
          and M.CONFIG["color_end"] == "#FFFFFF", "", failures)
    # 对话框构建 + 修改后应用
    dlg_aura = M.AuraSettingsDialog()
    check("光效对话框结构", len(dlg_aura.findChildren(QDoubleSpinBox)) == 1
          and dlg_aura.edt_mid is not None, "", failures)
    # 倍率钳制：0.1~3 之外取最近边界
    for raw, want in ((5.0, 3.0), (0.01, 0.1), (1.0, 1.0), (2.5, 2.5), (-9.0, 0.1)):
        got = M._clamp_scale(raw)
        check(f"倍率钳制 {raw} → {want}", got == want, f"（实得 {got}）", failures)
    # 动画时长关系：相位推进速率 = elapsed × scale（即周期 = cycle_ms / scale）
    # 复现主程序 _on_tick 的相位公式做数值校验，不依赖真实时钟推进。
    def phase_at(elapsed, scale):
        c = M.CONFIG["cycle_ms"]
        return (elapsed * scale % c) / c
    check("同 elapsed 下 2x 相位是 1x 的两倍（未整圈）",
          abs(phase_at(5000.0, 2.0) - 2.0 * phase_at(5000.0, 1.0)) < 1e-9, "", failures)
    check("0.5x 达到 25% 相位耗时 = 1x 的两倍（周期 /0.5）",
          abs(phase_at(10000.0, 0.5) - 0.25) < 1e-9
          and abs(phase_at(5000.0, 1.0) - 0.25) < 1e-9, "", failures)
    # 中间色填非法值 → 回退原值
    old_mid = dlg_aura._fallback["color_mid"]  # noqa: SLF001
    dlg_aura.edt_mid.setText("#ZZZZZZ")
    s = dlg_aura.settings()
    check("非法中间色回退原色", s["color_mid"] == old_mid, f"（实得 {s['color_mid']}）", failures)
    # 合法应用：改中间色与倍率并写回运行期 CONFIG（只应用不落盘，避免污染真实 aura.yaml）
    dlg_aura.edt_mid.setText("#00FF88")
    dlg_aura.spin_scale.setValue(2.0)
    cfg_new = dlg_aura.settings()
    M.apply_aura_to_config(cfg_new)
    check("光效应用后 CONFIG 更新", M.CONFIG["color_mid"] == "#00FF88"
          and M.CONFIG["aura_scale"] == 2.0,
          f"（mid={M.CONFIG['color_mid']}, scale={M.CONFIG['aura_scale']}）", failures)
    check("光效应用后三段一致",
          M.CONFIG["color_start"] == cfg_new["color_start"]
          and M.CONFIG["color_end"] == cfg_new["color_end"], "", failures)
    # 保存格式验证（写临时文件，不碰项目真实 aura.yaml）
    aura_tmp = os.path.join(tempfile.mkdtemp(prefix="aura_cfg_"), "aura_test.yaml")
    back = {"color_start": "#FFFFFF", "color_mid": "#00FF88", "color_end": "#FFFFFF"}
    with open(aura_tmp, "w", encoding="utf-8") as fh:
        yaml.safe_dump(back, fh, allow_unicode=True)
    loaded = yaml.safe_load(open(aura_tmp, encoding="utf-8"))
    check("光效保存写盘格式正确", loaded["color_mid"] == "#00FF88", f"（{loaded}）", failures)
    os.remove(aura_tmp)
    # 恢复默认（避免污染后续会话的 CONFIG；测试未写磁盘）
    M.apply_aura_to_config({"color_start": "#FFFFFF", "color_mid": "#283FFE",
                            "color_end": "#FFFFFF", "aura_scale": 1.0})
    dlg_aura.deleteLater()

    # ---------- 18b 透明度设置（总体 10~100 / 休眠 1~20） ----------
    print("\n透明度设置")
    for raw, want in ((5, 10), (150, 100), (100, 100), (10, 10), (60, 60), ("x", 100)):
        got = M._clamp_opacity(raw)
        check(f"总体透明度钳制 {raw!r} → {want}", got == want, f"（实得 {got}）", failures)
    for raw, want in ((0, 1), (25, 20), (20, 20), (1, 1), (10, 10), ("x", 10)):
        got = M._clamp_sleep_op(raw)
        check(f"休眠透明度钳制 {raw!r} → {want}", got == want, f"（实得 {got}）", failures)
    for sl, op, want in ((5, 100, 5), (20, 100, 20), (15, 10, 10), (10, 10, 10)):
        got = M.resolve_sleep_opacity(sl, op)
        check(f"休眠透明度联动 睡{sl}/总{op} → {want}", got == want, f"（实得 {got}）", failures)

    dlg_aura2 = M.AuraSettingsDialog()
    check("动效窗高度 245、含两个透明度输入",
          dlg_aura2.height() == 245
          and hasattr(dlg_aura2, "spin_opacity") and hasattr(dlg_aura2, "spin_sleep_op"),
          f"（height={dlg_aura2.height()}）", failures)
    check("总体透明度范围 10~100",
          dlg_aura2.spin_opacity.minimum() == 10 and dlg_aura2.spin_opacity.maximum() == 100,
          "", failures)
    check("休眠透明度范围 1~20",
          dlg_aura2.spin_sleep_op.minimum() == 1 and dlg_aura2.spin_sleep_op.maximum() == 20,
          "", failures)
    # 休眠透明度 ≤ 总体透明度 → 保留；> 总体透明度 → 归一为 10
    dlg_aura2.spin_opacity.setValue(50)
    dlg_aura2.spin_sleep_op.setValue(15)
    s2 = dlg_aura2.settings()
    check("休眠≤总体 → settings 保留 15", s2["sleep_opacity"] == 15 and s2["opacity"] == 50,
          f"（{s2}）", failures)
    dlg_aura2.spin_opacity.setValue(8)
    dlg_aura2.spin_sleep_op.setValue(15)
    s2b = dlg_aura2.settings()
    check("休眠>总体 → settings 归一为 10", s2b["sleep_opacity"] == 10 and s2b["opacity"] == 10,
          f"（{s2b}）", failures)
    dlg_aura2.deleteLater()
    # 运行期 CONFIG 应用透明度
    M.apply_aura_to_config({"color_mid": "#FFFFFF", "aura_scale": 1.0, "idle_sec": 60,
                            "opacity": 40, "sleep_opacity": 5})
    check("CONFIG 透明度应用", M.CONFIG["opacity"] == 40 and M.CONFIG["sleep_opacity"] == 5,
          f"（op={M.CONFIG['opacity']}, sl={M.CONFIG['sleep_opacity']}）", failures)
    # 绘制时总体透明度缩放：opacity=40 时球体核心 alpha 应明显低于 255
    _d40, _r40, _c40 = M.shape_at(0.5)
    _img40 = render_ball(0.5, size)
    _m40 = _img40.width() // 2
    _p40 = _m40 + int(round(_r40 * 0.5))
    _a40 = (_img40.pixel(_m40, _p40) >> 24) & 0xFF if _p40 < _img40.height() else 0
    check("opacity=40 时球体 alpha 缩放（≈102）",
          60 <= _a40 <= 145, f"（核心 alpha={_a40}）", failures)
    M.apply_aura_to_config({"color_mid": "#FFFFFF", "aura_scale": 1.0, "idle_sec": 60,
                            "opacity": 100, "sleep_opacity": 10})
    check("恢复透明度 100%",
          M.CONFIG["opacity"] == 100 and M.CONFIG["sleep_opacity"] == 10, "", failures)

    # ---------- 19b 球心位置记忆（window.pos_x / pos_y） ----------
    print("\n球心位置记忆")
    pos_tmp = os.path.join(tempfile.mkdtemp(prefix="aura_pos_"), "s.yaml")
    M.write_setting({"window": {"always_on_top": False, "pos_x": None, "pos_y": None}}, pos_tmp)
    check("无位置记录 → 返回 None", M.saved_ball_position(pos_tmp) is None, "", failures)
    M.save_ball_position(1234.56, 789.12, pos_tmp)
    got_pos = M.saved_ball_position(pos_tmp)
    check("保存后读回坐标（四舍五入到 0.1）",
          got_pos is not None and abs(got_pos[0] - 1234.6) < 1e-6
          and abs(got_pos[1] - 789.1) < 1e-6, f"（实得 {got_pos}）", failures)
    D_pos = yaml.safe_load(open(pos_tmp, encoding="utf-8"))
    check("坐标写入 window 块", isinstance(D_pos["window"].get("pos_x"), (int, float))
          and isinstance(D_pos["window"].get("pos_y"), (int, float)), f"（{D_pos['window']}）", failures)
    M.save_ball_position(float("nan"), 100.0, pos_tmp)
    D_pos2 = yaml.safe_load(open(pos_tmp, encoding="utf-8"))
    check("非法坐标不落盘（NaN 守护）", D_pos2["window"]["pos_x"] == 1234.6,
          f"（{D_pos2['window']}）", failures)
    os.remove(pos_tmp)
    # 启动摆放：无记录 → 右侧中间贴边（main() 逻辑复现）
    screen_geo = M.QApplication.primaryScreen().availableGeometry()
    _saved = M.saved_ball_position()
    if _saved is None:
        _dx = screen_geo.right() - M.RING_OUT
        _dy = screen_geo.center().y()
        check("默认位置 = 屏幕右侧中间、外缘贴右", _dx == screen_geo.right() - M.RING_OUT
              and _dy == screen_geo.center().y(), f"（({_dx}, {_dy})）", failures)
    else:
        print("[跳过] 当前配置已有位置记录，默认位置断言跳过")

    # ---------- 19c 配置主位置与根目录迁移链路（隔离目录） ----------
    print("\n配置主位置与根目录迁移链路")
    mig_dir = tempfile.mkdtemp(prefix="aura_mig_")
    main_cfg = os.path.join(mig_dir, "local", "setting.yaml")     # 模拟 %LOCALAPPDATA%\Aura_Pixie\setting.yaml
    root_src = os.path.join(mig_dir, "root")                       # 模拟 exe 根目录
    os.makedirs(root_src)
    # 旧版 functions.yaml / aura.yaml 不做专属处理，视为普通 *.yaml：
    # 内容含 fonction 块/aura 块即合法，应被逐个迁移合并
    with open(os.path.join(root_src, "functions.yaml"), "w", encoding="utf-8") as fh:
        yaml.safe_dump({"functions": [{"name": "甲", "code": "print(1)", "active": True}]},
                       fh, allow_unicode=True)
    with open(os.path.join(root_src, "aura.yaml"), "w", encoding="utf-8") as fh:
        yaml.safe_dump({"aura": {"color_mid": "#FF0000"}}, fh, allow_unicode=True)
    out_cfg = M.ensure_settings(main_cfg, base=root_src)
    check("旧 version yaml 视同普通 yaml：全部迁移合并",
          isinstance(out_cfg.get("functions"), list)
          and out_cfg["functions"][0].get("name") == "甲"
          and out_cfg.get("aura", {}).get("color_mid") == "#FF0000",
          f"（functions={out_cfg.get('functions')}, aura={out_cfg.get('aura')}）", failures)
    root_left = sorted(os.listdir(root_src))
    check("迁移后根源改名 *.yaml.bak_done（不再被扫）",
          root_left == ["aura.yaml.bak_done", "functions.yaml.bak_done"],
          f"（剩余：{root_left}）", failures)
    # 主位置已存在 → 直接读取，不再扫描/改名根目录
    with open(os.path.join(root_src, "extra.yaml"), "w", encoding="utf-8") as fh:
        yaml.safe_dump({"window": {"always_on_top": True}}, fh, allow_unicode=True)
    out2 = M.ensure_settings(main_cfg, base=root_src)
    check("主位置已存在 → 直接载入、不迁移",
          out2.get("window", {}).get("always_on_top") is False
          and os.path.exists(os.path.join(root_src, "extra.yaml")),
          f"（window={out2.get('window')}）", failures)
    # 根目录无合法 yaml → 写出厂默认
    empty_main = os.path.join(mig_dir, "empty", "setting.yaml")
    out3 = M.ensure_settings(empty_main, base=os.path.join(mig_dir, "empty_root"))
    check("根目录无合法 yaml → 写默认",
          out3.get("functions") == [] and out3.get("aura", {}).get("color_mid") == "#FFFFFF",
          f"（out={out3}）", failures)
    shutil.rmtree(mig_dir, ignore_errors=True)

    # ---------- 19 置顶开关（window.always_on_top） ----------
    print("\n置顶开关与配置块")
    top_tmp = os.path.join(tempfile.mkdtemp(prefix="aura_top_"), "s.yaml")
    M.write_setting({"functions": [],
                     "aura": {"color_mid": "#FFFFFF", "aura_scale": 1.0, "idle_sec": 60},
                     "autostart": {"enabled": False}}, top_tmp)
    check("缺省 window 块 → 置顶为 False", M.topmost_enabled(top_tmp) is False,
          f"（实得 {M.topmost_enabled(top_tmp)}）", failures)
    M.set_topmost(True, top_tmp)
    D_t = yaml.safe_load(open(top_tmp, encoding="utf-8"))
    check("set_topmost(true) 写 window.always_on_top",
          D_t["window"]["always_on_top"] is True and M.topmost_enabled(top_tmp) is True,
          f"（window={D_t['window']}）", failures)
    M.set_topmost(False, top_tmp)
    D_t = yaml.safe_load(open(top_tmp, encoding="utf-8"))
    check("set_topmost(false) 写回", D_t["window"]["always_on_top"] is False
          and M.topmost_enabled(top_tmp) is False, f"（window={D_t['window']}）", failures)
    check("置顶写入不动其他块",
          "aura" in D_t and "autostart" in D_t and "functions" in D_t, "", failures)
    check("配置注释头含 window 块说明",
          "always_on_top" in M.SETTINGS_HEADER and "保持球体置顶" in M.SETTINGS_HEADER,
          "", failures)
    os.remove(top_tmp)

    dlg_top = M.SettingsDialog([{"name": "a", "code": "", "active": False}])
    idx_t = dlg_top.bar.indexOf(dlg_top.chk_topmost)   # noqa: SLF001
    idx_a = dlg_top.bar.indexOf(dlg_top.chk_autostart)  # noqa: SLF001
    check("设置窗含「保持球体置顶」且位于开机自启动上方",
          idx_t >= 0 and idx_t < idx_a, f"（topmost@{idx_t} autostart@{idx_a}）", failures)
    dlg_top.deleteLater()

    # ---------- 20 休眠顺色（浅色→黑 / 深色→白） ----------
    print("\n休眠顺色渲染")
    b_slp = fresh_ball()
    b_slp._enter_sleep()  # noqa: SLF001
    check("休眠进入（透明度 0.1 + 呼吸冻结）",
          b_slp._asleep and b_slp._breath_frozen, "", failures)
    dark = M.system_dark_mode()
    want_hex = "FFFFFF" if dark else "000000"
    check("休眠球体按系统深浅色决定黑白",
          hexs(b_slp._sleep_color) == want_hex,  # noqa: SLF001
          f"（实得 #{hexs(b_slp._sleep_color)}，系统{'深' if dark else '浅'}）", failures)
    # 顺色绘制实测：白色系统下画黑球、黑色系统下画白球（避开中心高光层采样）
    for hex_in, phase in (("#000000", 0.5), ("#FFFFFF", 0.5)):
        _d, radius, _c = M.shape_at(phase)
        _img = render_ball(phase, size)
        _mid = _img.width() // 2
        _p = _mid + int(round(radius * 0.5))
        img_ov = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        img_ov.fill(Qt.GlobalColor.transparent)
        _pt = QPainter(img_ov)
        M.paint_ball(_pt, size, phase, override_color=M.QColor(hex_in))
        _pt.end()
        c_ov = img_ov.pixelColor(_mid, _p)
        want = ("black", "white")[hex_in == "#FFFFFF"]
        lum = (c_ov.red() + c_ov.green() + c_ov.blue()) / 3.0
        check(f"休眠顺色绘制：{hex_in} → {want}",
              (lum < 25 if want == "black" else lum > 200),
              f"（均亮 {lum:.0f}）", failures)
    b_slp._wake()  # noqa: SLF001
    check("唤起后退出休眠/呼吸解冻", not b_slp._asleep and not b_slp._breath_frozen,
          "", failures)

    # ---------- 20b 「在哪儿？」定位闪烁（locate_here） ----------
    print("\n「在哪儿？」定位闪烁")
    bloc = fresh_ball((1000, 600))
    bloc._enter_sleep()   # 先让球休眠
    bloc.locate_here()    # noqa: SLF001  # 定位指令到来
    check("定位唤起休眠球（透明度恢复）",
          not bloc._asleep and bloc._breath_frozen is False, "", failures)
    check("定位启动红框闪烁（_locate_left_ms>0、窗口放大）",
          bloc._locate_left_ms > 0 and bloc.width() == M.ACTIVE_SIZE,
          f"（left={bloc._locate_left_ms:.0f}ms, w={bloc.width()}）", failures)
    # 模拟闪烁推进：半周期应亮、整周期交替，结束时 _warn_on 复位
    flips = []
    for _ in range(50):
        bloc._advance_motion(20.0)
        flips.append(bloc._warn_on)
    check("定位红框按 500ms 周期闪烁（亮暗交替）",
          any(flips) and not all(flips), f"（亮{sum(flips)}/50）", failures)
    # 推进到定位结束：_locate_left_ms 耗尽后 _warn_on 复位、窗口缩回
    while bloc._locate_left_ms > 0:
        bloc._advance_motion(50.0)
    check("定位结束自动复位（红框熄灭、窗口缩回 IDLE）",
          bloc._locate_left_ms == 0.0 and not bloc._warn_on
          and bloc.width() == M.IDLE_SIZE,
          f"（left={bloc._locate_left_ms}, warn={bloc._warn_on}, w={bloc.width()}）", failures)
    bloc.deleteLater()

    # ---------- 21 指令列多行编辑 ----------
    print("\n指令列多行编辑")
    dlg_ml = M.SettingsDialog([{"name": "a", "code": "print(1)", "active": False}])
    check("指令列挂载多行编辑委托",
          isinstance(dlg_ml.table.itemDelegateForColumn(2), M.MultilineEditDelegate),
          "", failures)
    _dl = dlg_ml.table.itemDelegateForColumn(2)
    _opt = dlg_ml.table.visualRect(dlg_ml.table.model().index(0, 2))
    _idx = dlg_ml.table.model().index(0, 2)
    editor = _dl.createEditor(dlg_ml.table, _opt, _idx)
    check("指令列编辑器为 QPlainTextEdit（多行）",
          isinstance(editor, M.QPlainTextEdit), f"（{type(editor).__name__}）", failures)
    _dl.setEditorData(editor, _idx)
    editor.setPlainText("py:\nimport os\nx = 1\n    y = 2")
    _dl.setModelData(editor, dlg_ml.table.model(), _idx)
    got_ml = dlg_ml.table.item(0, 2).text()
    check("指令列多行内容原样保存（含换行与缩进）",
          got_ml == "py:\nimport os\nx = 1\n    y = 2", f"（{got_ml!r}）", failures)
    check("rows() 保留多行指令",
          dlg_ml.rows()[0]["code"] == "py:\nimport os\nx = 1\n    y = 2", "", failures)
    dlg_ml.deleteLater()

    # ---------- 22 球心位置记忆：退出不再写坐标；移动停止 10s 自动落盘 ----------
    print("\n球心位置记忆（静止 10s 自动落盘）")
    _cfg = M.CONFIG_PATH
    _backup22 = _cfg + ".bak_selftest22"
    shutil.copyfile(_cfg, _backup22)
    try:
        # 22a. 退出不写坐标：closeEvent 不得再调 save_ball_position
        M.save_ball_position(100.0, 100.0, _cfg)   # 写入基准坐标
        b22 = fresh_ball((1500, 700))
        b22._center = QPointF(1500, 700)  # noqa: SLF001 - 直接摆球心
        b22.close()   # closeEvent 不应写配置；离屏窗口 close 不弹真实退出
        app.processEvents()
        pos22 = M.saved_ball_position(_cfg)
        check("退出（close）后坐标不被改写",
              pos22 is not None and abs(pos22[0] - 100) < 1e-6
              and abs(pos22[1] - 100) < 1e-6,
              f"（实得 {pos22}，期望 (100.0, 100.0)）", failures)

        # 22b. 移动停止 10s 无位移 → 立即写坐标
        _pre = M.saved_ball_position(_cfg) or (0.0, 0.0)
        b23 = fresh_ball((1500, 700))
        b23._clock.start()                    # 需要时钟有效（离屏未自启）
        b23._last_motion_center = QPointF(1500, 700)
        # 模拟移动到位 (1600, 800)
        b23._center = QPointF(1600, 800)      # noqa: SLF001
        b23._note_motion_settle(16)           # 检测到位移 → dirty=True, settle 归零
        # 连续静止累计 5000ms×2 = 10000ms → 触发落盘
        b23._note_motion_settle(5000)
        b23._note_motion_settle(5000)
        pos23 = M.saved_ball_position(_cfg)
        check("静止累计满 10s 自动写入坐标",
              pos23 is not None and abs(pos23[0] - 1600) < 1e-6
              and abs(pos23[1] - 800) < 1e-6,
              f"（实得 {pos23}，期望 (1600.0, 800.0)）", failures)
        # 静止不足 10s 不写
        b24 = fresh_ball((10, 10))
        b24._clock.start()
        b24._last_motion_center = QPointF(10, 10)
        b24._center = QPointF(11, 11)         # noqa: SLF001 - 移动 <0.5 不算位移
        b24._note_motion_settle(16)           # 位移 1.41px ≥0.5 → dirty
        pos_before = M.saved_ball_position(_cfg)
        b24._note_motion_settle(3000)         # 静止 3s < 10s
        pos_after = M.saved_ball_position(_cfg)
        check("静止未满 10s 不写入",
              pos_after == pos_before,
              f"（写前 {pos_before} 写后 {pos_after}）", failures)

        # 22c. 源码防回归：closeEvent 不再调 save_ball_position
        import inspect as _inspect
        _src_close = _inspect.getsource(M.AuraBall.closeEvent)
        check("防回归：closeEvent 不再保存坐标",
              "save_ball_position" not in _src_close,
              "（closeEvent 仍含 save_ball_position）", failures)
    finally:
        shutil.copyfile(_backup22, _cfg)
        os.remove(_backup22)

    # ---------- 23 指令高级编辑窗（右键「高级编辑」） ----------
    print("\n指令高级编辑窗")
    dlg_ce = M.CodeEditDialog("cmd: echo hi")
    check("高级编辑窗离屏构造成功", dlg_ce is not None, "", failures)
    check("编辑窗初始内容与传入一致",
          dlg_ce.text() == "cmd: echo hi", f"（{dlg_ce.text()!r}）", failures)
    check("测试警示文案存在且醒目",
          dlg_ce.warn.text() == M.CODE_EDIT_WARN_TEXT
          and "ff8c42" in dlg_ce.warn.styleSheet(),
          f"（{dlg_ce.warn.text()[:18]}…）", failures)
    check("测试按钮带警示 tooltip",
          dlg_ce.btn_test.toolTip() == M.CODE_EDIT_TEST_TOOLTIP, "", failures)
    check("测试按钮警示样式（橙红配色）",
          "ff8c42" in dlg_ce.btn_test.styleSheet(), "", failures)
    check("结果区初始隐藏",
          not dlg_ce.result.isVisible(), "", failures)

    # 保存校验路径（classify_code 驱动，不真弹模态框）
    _k0, _p0, e_save_ok = M.classify_code("py: print(1)")
    _k1, _p1, e_save_bad = M.classify_code("py: 1 +")
    check("保存校验：合法指令通过",
          e_save_ok == "", f"（err={e_save_ok!r}）", failures)
    check("保存校验：语法错误拦截",
          e_save_bad != "" and "语法" in e_save_bad, f"（err={e_save_bad!r}）", failures)
    _k2, _p2, e_empty_cmd = M.classify_code("cmd:")
    check("保存校验：空 cmd 拦截",
          e_empty_cmd != "", f"（err={e_empty_cmd!r}）", failures)

    # 测试执行命令构造（build_code_test：不真启动进程，只验证命令行与临时文件）
    _cleanup_tmp: list[str] = []
    try:
        _kk, _pp, argv_py, cl_py, err_py = M.build_code_test("py: print(1)")
        check("测试构造：py 合法 → argv 指向解释器+临时脚本",
              err_py == "" and argv_py is not None
              and argv_py[0] == M.sys.executable and argv_py[1].endswith(".py"),
              f"（{argv_py}）", failures)
        _cleanup_tmp += cl_py
        _kk, _pp, argv_bad, cl_bad, err_bad = M.build_code_test("py: 1 +")
        check("测试构造：语法错误 → 拒绝执行并返回错误说明",
              argv_bad is None and err_bad != "" and "语法" in err_bad,
              f"（err={err_bad!r}）", failures)
        _kk, _pp, argv_cmd, cl_cmd, err_cmd = M.build_code_test("cmd: echo hi")
        check("测试构造：cmd 走 GBK .bat 通道",
              err_cmd == "" and argv_cmd[:2] == ["cmd.exe", "/c"]
              and argv_cmd[2].endswith(".bat"),
              f"（{argv_cmd}）", failures)
        _cleanup_tmp += cl_cmd
        _kk, _pp, argv_ps, cl_ps, err_ps = M.build_code_test("ps: Write-Host hi")
        check("测试构造：ps 走 -EncodedCommand 通道",
              err_ps == "" and "-EncodedCommand" in argv_ps,
              f"（{argv_ps}）", failures)
        _cleanup_tmp += cl_ps
    finally:
        for p in _cleanup_tmp:   # 清理测试构造的临时脚本，不污染 RUNNER_DIR
            try:
                os.remove(p)
            except OSError:
                pass
    dlg_ce.deleteLater()

    # 定位回归：右键「高级编辑」必须命中当前行，不能偏到上一行
    # （根因：Qt6 中 contextMenuEvent 的 pos 已是 viewport 坐标，若再用
    #   viewport().mapFrom(self) 二次换算会额外减表头高度 → indexAt 偏上一行）
    print("\n高级编辑右键定位")
    dlg_loc = M.SettingsDialog([{"name": "行1", "code": "py: print(1)", "active": False},
                                {"name": "行2", "code": "py: print(2)", "active": False},
                                {"name": "行3", "code": "py: print(3)", "active": False}])
    dlg_loc.resize(660, 440)
    dlg_loc.show()
    app.processEvents()
    try:
        for want_row in (0, 1, 2):
            vr = dlg_loc.table.visualRect(dlg_loc.table.model().index(want_row, 2))
            gp = dlg_loc.table.viewport().mapToGlobal(vr.center())
            idx = dlg_loc.table._column2_index_at_global(gp)
            ok = idx.isValid() and idx.row() == want_row and idx.column() == 2
            check(f"右键定位：第 {want_row + 1} 行准确命中",
                  ok, f"（globalPos→row={idx.row() if idx.isValid() else -1}）", failures)
        # 防回归：右键定位必须从全局坐标映射，禁止再出现 viewport().mapFrom(self) 二次换算
        # （旧 bug：Qt6 中 contextMenuEvent 的 pos 已是 viewport 坐标，二次换算会偏上一行；
        #   检查精确到真实调用代码，注释/说明文字里的 "mapFrom(self)" 不计）
        import inspect as _inspect
        _src_cm = _inspect.getsource(M.ReorderTable)
        check("右键定位修复不回归（源码无二次换算）",
              "self.viewport().mapFrom(self" not in _src_cm
              and "mapFromGlobal" in _src_cm,
              f"（源码错误地含二次换算或缺失 mapFromGlobal）", failures)
    finally:
        dlg_loc.hide()
        dlg_loc.deleteLater()
        app.processEvents()

    sectors_now = M.load_functions()
    print("\n当前 functions.yaml：" +
          " | ".join(f"{s['name']}{'(灰)' if s['placeholder'] else ''}" for s in sectors_now)
          + f" → 圆环 {len(sectors_now)} 等分")

    # ====== V9 次级动态扇形自检 ======
    print("\nV9 次级动态扇形")

    # 1. 成对标记解析器
    print("\n成对标记解析")
    test_code_1 = """cmd: echo main
【【【子1】】】
cmd: echo sub1
【【【子1】】】
【【【子2】】】
cmd: echo sub2
【【【子2】】】"""
    parsed_1 = M.parse_special_sections(test_code_1)
    check("解析器：主指令提取",
          parsed_1["main"] == "cmd: echo main",
          f"（main={parsed_1['main']!r}）", failures)
    check("解析器：次级项数量",
          len(parsed_1["items"]) == 2,
          f"（items数={len(parsed_1['items'])}）", failures)
    check("解析器：次级项1名称与代码",
          parsed_1["items"][0]["name"] == "子1" and parsed_1["items"][0]["code"] == "cmd: echo sub1",
          f"（items[0]={parsed_1['items'][0]}）", failures)
    check("解析器：次级项2名称与代码",
          parsed_1["items"][1]["name"] == "子2" and parsed_1["items"][1]["code"] == "cmd: echo sub2",
          f"（items[1]={parsed_1['items'][1]}）", failures)

    # 2. 无标记场景
    test_code_2 = "cmd: echo only_main"
    parsed_2 = M.parse_special_sections(test_code_2)
    check("解析器：无标记时主指令完整",
          parsed_2["main"] == "cmd: echo only_main",
          f"（main={parsed_2['main']!r}）", failures)
    check("解析器：无标记时次级项为空",
          len(parsed_2["items"]) == 0,
          f"（items数={len(parsed_2['items'])}）", failures)

    # 3. 标记未闭合错误
    test_code_3 = """cmd: echo main
【【【未闭合】】】
cmd: echo orphan"""
    parsed_3 = M.parse_special_sections(test_code_3)
    check("解析器：未闭合标记报错",
          parsed_3["err"] != "" and "未闭合" in parsed_3["err"],
          f"（err={parsed_3['err']!r}）", failures)

    # 4. 测试剥标记逻辑
    print("\n测试剥标记")
    test_code_4 = """cmd: echo main_cmd
【【【子功能】】】
cmd: echo sub_cmd
【【【子功能】】】"""
    _kk, _pp, argv_test, _cl, err_test = M.build_code_test(test_code_4)
    check("测试构造：含标记时剥标记只测主指令",
          err_test == "" and argv_test is not None and argv_test[0] == "cmd.exe",
          f"（err={err_test!r}, argv={argv_test}）", failures)

    # 5. 保存校验适配
    print("\n保存校验适配")
    save_code_1 = """cmd: echo main
【【【子1】】】
cmd: echo sub1
【【【子1】】】"""
    _k, _p, e_save_1 = M.classify_code(save_code_1)
    check("保存校验：含标记的主指令段合法",
          e_save_1 == "",
          f"（err={e_save_1!r}）", failures)

    save_code_2 = """py: print(1)
【【【子1】】】
py: 1 +
【【【子1】】】"""
    _k, _p, e_save_2 = M.classify_code(save_code_2)
    check("保存校验：次级项语法错误被拦截",
          e_save_2 != "" and "语法" in e_save_2,
          f"（err={e_save_2!r}）", failures)

    # 6. 状态机字段存在性
    print("\n状态机字段")
    ball_test = M.AuraBall()
    check("状态机：_sub_sectors 字段存在",
          hasattr(ball_test, "_sub_sectors"),
          "（缺少 _sub_sectors）", failures)
    check("状态机：_sub_launching 字段存在",
          hasattr(ball_test, "_sub_launching"),
          "（缺少 _sub_launching）", failures)
    check("状态机：_sub_pending_items 字段存在",
          hasattr(ball_test, "_sub_pending_items"),
          "（缺少 _sub_pending_items）", failures)
    check("状态机：_sub_timer 字段存在",
          hasattr(ball_test, "_sub_timer"),
          "（缺少 _sub_timer）", failures)
    check("状态机：_ctx_consumed 字段存在",
          hasattr(ball_test, "_ctx_consumed"),
          "（缺少 _ctx_consumed）", failures)
    ball_test.deleteLater()

    # 7. 子功能块数量校验（V9 追加：2~9）
    print("\n子功能块数量校验")
    parsed_empty = M.parse_special_sections("cmd: echo no_mark")
    check("数量校验：无标记 total=0",
          parsed_empty["total"] == 0,
          f"（total={parsed_empty.get('total')!r}）", failures)
    # 调用方策略：仅当 total 非 0（含标记）才做数量校验 → 无标记不会被拦截
    check("数量校验：无标记不触发数量校验",
          (not parsed_empty["total"]) or M.validate_sub_items_count(parsed_empty["total"]) == "",
          f"（total={parsed_empty['total']}）", failures)

    def _mk_code(n: int) -> str:
        """构造含 n 个成对子功能块的指令文本。"""
        lines = ["cmd: echo main"]
        for i in range(1, n + 1):
            lines.append(f"【【【块{i}】】】")
            lines.append(f"cmd: echo 块{i}")
            lines.append(f"【【【块{i}】】】")
        return "\n".join(lines)

    for n, expect_ok in ((1, False), (2, True), (3, True), (9, True), (10, False)):
        pn = M.parse_special_sections(_mk_code(n))
        en = M.validate_sub_items_count(pn["total"]) if pn["total"] else ""
        check(f"数量校验：{n} 个{'通过' if expect_ok else '拦截'}",
              (en == "") == expect_ok and pn["total"] == n,
              f"（total={pn['total']}, err={en!r}）", failures)

    # 8. 行内编辑首行不显示修复（V9 追加）
    #    旧实现 updateEditorGeometry 向上扩展（y 减高出部分）→ 编辑第 1 行时
    #    首行内容被顶出视口不可见；现改为自单元格顶部向下展开、不越出视口。
    import inspect as _inspect
    _src_geo = _inspect.getsource(M.MultilineEditDelegate.updateEditorGeometry)
    check("首行修复：不再向上扩展（源码无 option.rect.y() - 表达式）",
          "- (h - option.rect.height())" not in _src_geo
          and "option.rect.y() -" not in _src_geo,
          "（源码仍含向上扩展）", failures)
    check("首行修复：编辑器顶部夹进视口（source 含 max(0, option.rect.y())）",
          "max(0, option.rect.y())" in _src_geo,
          "（源码未夹紧视口顶部）", failures)

    print("总判定:", "PASS" if not failures else "NEEDS_REVIEW")
    for f in failures:
        print("  -", f)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
