# -*- coding: utf-8 -*-
"""打包后清理：把一次性构建中间产物送回收站，复用物料原样保留。

正式工具，非一次性脚本。用法：

    python tools/post_pack_cleanup.py            # 真执行（送回收站）
    python tools/post_pack_cleanup.py --dry-run  # 只预检、列清单，不动文件

可直接串在打包命令后面自动执行，例如：

    python -m nuitka ... main.py && python tools/post_pack_cleanup.py

分类口径（与坑 17 / 89③ / 137 一致）：

  清理（一次性中间产物，不含任何可复用价值）
    build\\main.build\\          Nuitka C 编译中间目录
    build\\main.dist\\           standalone 展开目录（onefile 已内嵌其内容）
    build\\main.onefile-build\\  onefile 打包中间目录
    build\\_cmt_dbg\\            临时调试隔离目录
    build\\nuitka-crash-report.xml
    build\\_gen_iso\\            gen_config_docs.py 的 LOCALAPPDATA 隔离目录

  保留（复用物料 / 交付物 / 记录，绝不触碰）
    build_pkg\\                   打包输入：侧车 exe(72MB) + Qt 中文翻译 .qm
    aase001\\                     交付归档（exe + 两份 LICENSE + 真机清单）
    build\\_pack_log_*.txt        打包日志（含完整命令，可复现依据）
    build\\_pack_exit_*.txt       打包退出码记录
    build\\_nuitka_cache_v203\\   Nuitka ccache：保留可让下次编译增量加速；
                                  如要「全新无缓存编译」，加 --purge-cache 一并送回收站
    tools\\*.py                  自检与生成器脚本
    *.py / README.md / ...       项目源码与文档

  永不触碰（脚本内不构造这些路径）
    %LOCALAPPDATA%\\Nuitka\\...\\Cache\\downloads\\   gcc/depends 工具安装包
                                  —— 清掉会导致下次打包重新下载 254MB 且国内
                                  GitHub 直连大概率超时（坑 137）

安全约束：
  · 一律送回收站，不做永久删除（可恢复）；
  · 只处理本项目 build\\ 下白名单命中的目录，路径必须在本项目内（越界即跳过）；
  · 重解析点（符号链接/junction）跳过，避免顺着链接删到项目外；
  · 删除前只读预检生成精确清单，删除后逐项核实「已移除 / 未移除」并如实报告；
  · 任一目标报错即停止后续删除，不换方式重试。
"""
from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")

# 一次性中间产物：目录名（相对 build\）
DISPOSABLE_DIRS = (
    "main.build",
    "main.dist",
    "main.onefile-build",
    "_cmt_dbg",
    "_gen_iso",
)
# 一次性中间产物：文件名（相对 build\，支持后缀匹配）
DISPOSABLE_FILE_SUFFIXES = ("nuitka-crash-report.xml",)
# 可选清理：编译缓存（默认保留以便增量加速）
CACHE_DIRS = ("_nuitka_cache_v203",)

# 白名单保护：这些前缀命中即无条件跳过（双保险，防未来误加进 DISPOSABLE）
NEVER_TOUCH = (
    os.path.join(ROOT, "build_pkg"),
    os.path.join(ROOT, "aase001"),
    os.path.join(ROOT, "tools"),
    os.path.join(ROOT, "handover_documents"),
    os.path.join(ROOT, "collector"),
    os.path.join(ROOT, "msys2"),
    os.path.join(ROOT, ".venv"),
    os.path.join(ROOT, ".git"),
)

FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def human(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:.1f}{unit}" if unit != "B" else f"{int(f)}B"
        f /= 1024
    return f"{f:.1f}GB"


def dir_size(path: str) -> int:
    total = 0
    for base, dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(base, name))
            except OSError:
                pass
    return total


def is_reparse(path: str) -> bool:
    """重解析点（符号链接/junction）判定——命中即跳过，不顺着链接删。"""
    try:
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
    except Exception:
        return False
    if attrs == -1:
        return False
    return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)


def inside_project(path: str) -> bool:
    """越界防护：目标必须在本项目目录内。"""
    try:
        return os.path.commonpath([os.path.abspath(path), ROOT]) == ROOT
    except ValueError:
        return False


def protected(path: str) -> bool:
    ap = os.path.abspath(path)
    return any(ap == p or ap.startswith(p + os.sep) for p in NEVER_TOUCH)


def collect_targets(purge_cache: bool) -> list[tuple[str, int]]:
    """只读预检：返回 [(绝对路径, 体积字节)]，仅含确实存在且通过全部守卫的项。"""
    targets: list[tuple[str, int]] = []
    if not os.path.isdir(BUILD):
        return targets

    names = list(DISPOSABLE_DIRS)
    if purge_cache:
        names += list(CACHE_DIRS)

    for name in names:
        p = os.path.join(BUILD, name)
        if not os.path.exists(p):
            continue
        if not inside_project(p) or protected(p):
            print(f"[跳过·守卫] {p}")
            continue
        if is_reparse(p):
            print(f"[跳过·重解析点] {p}")
            continue
        targets.append((p, dir_size(p)))

    for entry in sorted(os.listdir(BUILD)):
        p = os.path.join(BUILD, entry)
        if not os.path.isfile(p):
            continue
        if not entry.endswith(DISPOSABLE_FILE_SUFFIXES):
            continue
        if not inside_project(p) or protected(p):
            print(f"[跳过·守卫] {p}")
            continue
        try:
            targets.append((p, os.path.getsize(p)))
        except OSError:
            pass
    return targets


def send_to_recycle_bin(path: str) -> tuple[bool, str]:
    """送回收站（可恢复）。返回 (是否成功, 说明)。"""
    ps = (
        "Add-Type -AssemblyName Microsoft.VisualBasic; "
        "$p = '{p}'; "
        "if (Test-Path -LiteralPath $p -PathType Container) {{ "
        "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory("
        "$p,'OnlyErrorDialogs','SendToRecycleBin') }} "
        "else {{ "
        "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("
        "$p,'OnlyErrorDialogs','SendToRecycleBin') }}".format(p=path.replace("'", "''"))
    )
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=600,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"调用失败: {exc}"
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "").strip().splitlines()
        return False, (detail[-1] if detail else f"rc={r.returncode}")
    return True, ""


def main() -> int:
    ap = argparse.ArgumentParser(description="打包后清理一次性构建中间产物（送回收站）")
    ap.add_argument("--dry-run", action="store_true", help="只预检列清单，不动文件")
    ap.add_argument("--purge-cache", action="store_true",
                    help="连 Nuitka ccache 一并送回收站（下次打包将全新编译，耗时长）")
    args = ap.parse_args()

    print("=" * 66)
    print("打包后清理 · 预检" + ("（--dry-run，不会删除任何文件）" if args.dry_run else ""))
    print("=" * 66)

    targets = collect_targets(args.purge_cache)
    if not targets:
        print("没有可清理的一次性中间产物，build\\ 已干净（或目录不存在）。")
        print("复用物料 build_pkg\\（侧车 + Qt 翻译）与交付归档 aase001\\ 始终保留。")
        return 0

    total = 0
    for p, size in targets:
        total += size
        print(f"  {human(size):>9}  {p}")
    print(f"  {'-' * 40}")
    print(f"  {human(total):>9}  合计 {len(targets)} 项")

    keep = [os.path.join(ROOT, "build_pkg"), os.path.join(ROOT, "aase001")]
    if not args.purge_cache:
        keep += [os.path.join(BUILD, c) for c in CACHE_DIRS]
    print("\n保留（复用物料 / 交付物）：")
    for k in keep:
        mark = "存在" if os.path.exists(k) else "不存在"
        extra = f"，{human(dir_size(k))}" if os.path.isdir(k) and mark == "存在" else ""
        print(f"  [{mark}] {k}{extra}")
    print("  [永不触碰] %LOCALAPPDATA%\\Nuitka\\...\\Cache\\downloads\\（gcc 工具包，坑137）")

    if args.dry_run:
        print("\n预检结束，未删除任何文件。去掉 --dry-run 即执行送回收站。")
        return 0

    print("\n执行送回收站（可恢复）...")
    done, failed = [], []
    for p, size in targets:
        ok, why = send_to_recycle_bin(p)
        # 独立核实：不看返回码，看路径是否真的没了
        gone = not os.path.exists(p)
        if ok and gone:
            done.append((p, size))
            print(f"  [已移除] {p}")
        else:
            failed.append((p, why or "路径仍存在"))
            print(f"  [未移除] {p} —— {why or '路径仍存在'}")
            # 任一目标报错即停止后续删除，不换方式重试
            print("  检测到失败，停止后续删除（不做兜底重试）。")
            break

    freed = sum(s for _, s in done)
    print(f"\n结果：已移除 {len(done)} 项，释放约 {human(freed)}；未移除 {len(failed)} 项。")

    print("\n保留项核实：")
    for k in keep:
        if os.path.exists(k):
            print(f"  [仍在] {k}")
        else:
            print(f"  [缺失] {k}  <- 请注意（本次未清理该项）")
    for guard in (os.path.join(ROOT, "main.py"),
                  os.path.join(ROOT, "tools", "check_config_compat.py")):
        print(f"  [{'仍在' if os.path.exists(guard) else '缺失'}] {guard}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
