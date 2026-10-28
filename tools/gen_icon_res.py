# -*- coding: utf-8 -*-
"""生成 icon_res.py：把 icon2.png 编码为 base64 内嵌资源模块。

用法：python tools/gen_icon_res.py
产出：项目根目录 icon_res.py（运行时从内存加载图标，不依赖外部文件）。
更换图标后重新运行本脚本即可。
"""
import base64
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "icon2.png")
OUT = os.path.join(BASE, "icon_res.py")

with open(SRC, "rb") as fh:
    b64 = base64.b64encode(fh.read()).decode("ascii")

chunks = "\n".join(f'    "{b64[i:i+96]}"' for i in range(0, len(b64), 96))
content = f'''# -*- coding: utf-8 -*-
"""内嵌应用图标（icon2.png base64 数据）：运行时从内存加载，不依赖外部文件。

由 tools/gen_icon_res.py 自动生成；更换图标后请重新生成。
"""
import base64

from PySide6.QtGui import QIcon, QPixmap

_ICON_PNG_BASE64 = (
{chunks}
)

def load_app_icon() -> QIcon:
    """从内存解码 PNG -> QIcon；失败返回空图标（调用方回退文件资源）。"""
    try:
        pm = QPixmap()
        if pm.loadFromData(base64.b64decode(_ICON_PNG_BASE64)):
            icon = QIcon(pm)
            if not icon.isNull():
                return icon
    except Exception:
        pass
    return QIcon()
'''

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write(content)
print(f"已生成 {OUT}（base64 长度 {len(b64)}）")