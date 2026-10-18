"""裁一块放大图，用来肉眼核对 UI 细节（光标是否落在按钮上、hover 高亮等）。

用法：
  py -3.11 driver/native/crop_png.py <输入png> <输出png> x0 y0 x1 y1 [放大倍数]
坐标是原图的像素（就是 cua.js shot 出来的那张图里的坐标）。
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    print("需要 Pillow：py -3.11 -m pip install pillow")
    raise SystemExit(2)


def main(argv: list[str]) -> int:
    if len(argv) < 7:
        print(__doc__)
        return 2
    src, dst = Path(argv[1]), Path(argv[2])
    x0, y0, x1, y1 = (int(v) for v in argv[3:7])
    scale = float(argv[7]) if len(argv) > 7 else 3.0
    img = Image.open(src)
    w, h = img.size
    x0, x1 = max(0, min(x0, x1)), min(w, max(x0, x1))
    y0, y1 = max(0, min(y0, y1)), min(h, max(y0, y1))
    crop = img.crop((x0, y0, x1, y1))
    crop = crop.resize(
        (max(1, int(crop.width * scale)), max(1, int(crop.height * scale))),
        Image.NEAREST,
    )
    crop.save(dst)
    print(f"{src.name} [{x0},{y0}..{x1},{y1}] → {dst.name} {crop.width}x{crop.height} (x{scale})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))