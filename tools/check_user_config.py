"""Aura Pixie 用户配置模仿测试（只读校验，绝不真实执行指令）

用途
    在虚拟环境（.venv）下加载真实用户配置 `%LOCALAPPDATA%\\Aura_Pixie\\setting.yaml`，
    模拟「设置窗保存 / 高级编辑窗保存」会对每条指令做的全部校验，逐条给出
    PASS / NEEDS_REVIEW。适合用户改完配置后快速体检，也用于回归验证
    「子功能块数量 2~9」「成对标记闭合」「指令格式合法」等规则。

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


def _config_path() -> str:
    root = os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(root, "Aura_Pixie", "setting.yaml")


def _seg_label(kind: str, name: str) -> str:
    return {"main": "主指令", "sub": f"次级项「{name}」"}.get(kind, kind)


def _validate_code_seg(text: str, label: str, failures: list[str]) -> None:
    """单段代码格式校验（等于保存时 classify_code 的判定，不执行）。"""
    if not text or not text.strip():
        return   # 空段 = 占位/仅含次级功能，合法
    _k, _p, err = M.classify_code(text)
    if err:
        failures.append(f"  {label}格式非法：{err}")


def main() -> int:
    path = _config_path()
    if not os.path.isfile(path):
        print(f"[跳过] 未找到用户配置：{path}")
        print("总判定: PASS（无配置可校验）")
        return 0
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    items = [f for f in (data.get("functions") or []) if isinstance(f, dict)]
    if not items:
        print(f"[跳过] 用户配置 {path} 无 functions 条目")
        print("总判定: PASS（无功能可校验）")
        return 0

    print(f"用户配置：{path}")
    print(f"功能条目：{len(items)} 个\n")
    failures: list[str] = []
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
        ok_seg = True
        for kind, sname, seg in segs:
            label = _seg_label(kind, sname)
            _k, _p, err = M.classify_code(seg)
            if not seg.strip():
                print(f"  [OK] {label}：空段（占位，不执行）")
                continue
            if err:
                ok_seg = False
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