"""Aura Pixie 用户配置模仿测试（只读校验，绝不真实执行指令）

用途
    在虚拟环境（.venv）下加载真实用户配置 `%LOCALAPPDATA%\\Aura_Pixie\\`，
    模拟「设置窗保存 / 高级编辑窗保存」会对每条指令做的全部校验，逐条给出
    PASS / NEEDS_REVIEW。适合用户改完配置后快速体检，也用于回归验证
    「子功能块数量 2~9」「成对标记闭合」「指令格式合法」等规则。

    v2.03 起功能指令存于 `command.yaml`（不再是 `setting.yaml.functions`）。
    本脚本走程序统一读取漏斗 `main.read_setting()` 取合并视图，不自拼单文件
    路径——配置结构再演进时自检自动跟随，不会像 v2.03 拆分那次一样因路径
    没跟进而恒定「无 functions 条目」跳过（坑134 同类：函数对、接线错）。

安全
    · 只调用 parse_special_sections / classify_code / validate_sub_items_count
      等纯解析函数，不调用 run_code / build_code_test / _spawn 等任何执行通道；
    · 不写临时脚本、不启动子进程、不读取注册表、不修改任何配置；
    · 有意不隔离 LOCALAPPDATA——本脚本就是检查真实配置的。

用法
    .venv\\Scripts\\activate
    python tools\\check_user_config.py
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402

import main as M  # noqa: E402


def _seg_label(kind: str, name: str) -> str:
    return {"main": "主指令", "sub": f"次级项「{name}」"}.get(kind, kind)


def _physical_functions() -> tuple[str, list]:
    """独立真相通道：不经程序漏斗，直接按固定文件名读 command.yaml 的 functions。

    存在的意义是交叉验证——`main._sibling_paths` 若把路径派生改错（v2.03 拆分
    结构再演进），`read_setting()` 会返回空 functions，本脚本就会把「读不到」误判成
    「用户没配功能」而静默 PASS（变异测试 M5 实测复现：漏斗指向 WRONG_cmd.yaml
    时本脚本仍 EXIT=0）。这里绕开漏斗读物理文件，两边条数不一致即打红。
    """
    cfg_dir = M.APP_CONFIG_DIR
    cmd = os.path.join(cfg_dir, "command.yaml")
    if not os.path.isfile(cmd):
        return cmd, []
    try:
        with open(cmd, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001 - 读不动属异常，返回空由调用方交叉判定
        print(f"[NG] 物理读取 command.yaml 失败：{exc!r}")
        return cmd, []
    return cmd, [f for f in (raw.get("functions") or []) if isinstance(f, dict)]


def main() -> int:
    # 主位置 setting.yaml + 同目录 command.yaml（functions）+ state.yaml 合并视图。
    # 用程序统一漏斗 read_setting()，不自拼单文件路径——拆分后 functions 已搬走，
    # 只读 setting.yaml 会恒定「无 functions 条目」跳过，形成虚假 PASS。
    path = M.SETTINGS_PATH
    cmd_path, _state_path = M._sibling_paths(path)
    phys_cmd, phys_items = _physical_functions()

    # 接线自检：漏斗派生出的 command.yaml 路径必须与固定物理名一致。
    # 不一致说明 _sibling_paths 派生逻辑与 v2.03 目录约定脱节（M5 类缺陷）。
    funnel_ok = os.path.normcase(os.path.abspath(cmd_path)) == \
        os.path.normcase(os.path.abspath(phys_cmd))
    failures: list[str] = []
    if not funnel_ok:
        failures.append("漏斗 command.yaml 路径与物理约定不一致")
        print(f"[NG] 接线：漏斗派生 {cmd_path}")
        print(f"     物理约定 {phys_cmd}")
    else:
        print(f"[OK] 接线：漏斗 command.yaml 路径与物理约定一致")

    if not os.path.isfile(path) and not os.path.isfile(phys_cmd):
        if failures:
            print("\n总判定: NEEDS_REVIEW")
            for f in failures:
                print("  -", f)
            return 1
        print(f"[跳过] 未找到用户配置（两者皆无）：{path} / {phys_cmd}")
        print("总判定: PASS（无配置可校验）")
        return 0

    data = M.read_setting(path) or {}
    items = [f for f in (data.get("functions") or []) if isinstance(f, dict)]

    # 交叉验证：漏斗读到的条数必须等于物理文件里的条数。
    # 物理有、漏斗空 = 接线断了（正是 v2.03 拆分那次踩的坑），绝不能当「无功能」放过。
    if len(items) != len(phys_items):
        failures.append(
            f"漏斗读到 {len(items)} 条 functions，物理 command.yaml 有 {len(phys_items)} 条")
        print(f"[NG] 交叉验证：漏斗 {len(items)} 条 vs 物理 {len(phys_items)} 条")
        print("     漏斗读不到而物理文件有内容 → 拆分/迁移或路径派生接线断了")
    else:
        print(f"[OK] 交叉验证：漏斗与物理均为 {len(items)} 条 functions")

    if not items:
        # 走到这里说明物理文件也确实没有 functions（两边都空，交叉一致）
        print(f"[跳过] 用户配置确实无 functions 条目：{phys_cmd}")
        if failures:
            print("\n总判定: NEEDS_REVIEW")
            for f in failures:
                print("  -", f)
            return 1
        print("总判定: PASS（无功能可校验）")
        return 0

    print(f"\n用户配置目录：{os.path.dirname(path)}")
    print(f"功能指令来源：{phys_cmd}")
    print(f"功能条目：{len(items)} 个\n")
    for idx, item in enumerate(items, 1):
        name = str(item.get("name", ""))
        code = str(item.get("code") or item.get("command") or "")
        print(f"—— 功能 {idx}「{name or '(未命名)'}」")

        # 1. 成对标记解析（闭合/顺序）
        parsed = M.parse_special_sections(code)
        if parsed["err"]:
            failures.append(f"「{name}」{parsed['err']}")
            print(f"  [NG] 特殊标记校验：{parsed['err']}")
            continue
        print("  [OK] 特殊标记闭合/配对正确" if parsed["total"]
              else "  [OK] 无特殊标记（普通指令）")

        # 2. 子功能块数量 2~9（含标记时）
        if parsed["total"]:
            err_count = M.validate_sub_items_count(parsed["total"])
            if err_count:
                failures.append(f"「{name}」{err_count}")
                print(f"  [NG] 子功能块数量：{err_count}")
            else:
                print(f"  [OK] 子功能块数量：{parsed['total']} 个（2~9 合法）")

        # 3. 逐段格式校验（主指令 + 每个次级项；空段跳过）
        segs: list[tuple[str, str, str]] = [("main", "主指令", parsed["main"])]
        segs += [("sub", it["name"], it["code"]) for it in parsed["items"]]
        if not parsed["total"]:
            segs = [("all", "整体", code)]   # 无标记：整段按一条指令校验
        for kind, sname, seg in segs:
            label = _seg_label(kind, sname)
            _k, _p, err = M.classify_code(seg)
            if not seg.strip():
                print(f"  [OK] {label}：空段（占位，不执行）")
                continue
            if err:
                failures.append(f"「{name}」{label}格式非法：{err}")
                print(f"  [NG] {label}格式非法：{err}")
            else:
                print(f"  [OK] {label}格式合法"
                      + (f"（{_k}）" if kind == "all" else ""))

    print("\n总判定:", "PASS" if not failures else "NEEDS_REVIEW")
    for f in failures:
        print("  -", f)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())