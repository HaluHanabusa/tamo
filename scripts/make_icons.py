"""ブラウザ拡張のアイコン生成器（依存ゼロ: struct+zlibで直接PNGを書く）。

デザイン: 濃紺の角丸地に白のタモ網（リング+メッシュ+柄）。
512pxマスターをスーパーサンプリング描画し、面積平均で各サイズへ縮小する。
再生成: python scripts/make_icons.py
"""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "browser-extension" / "icons"
MASTER = 512
SIZES = [16, 32, 48, 128]

BG = (18, 49, 82)        # 濃紺（海）
BG_HI = (24, 66, 108)    # 上方のわずかなグラデーション
NET = (240, 246, 250)    # 白（網）
FISH = (255, 176, 59)    # 揺れるひと粒（掬った文脈）


def _dist_seg(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    t = max(0.0, min(1.0, (wx * vx + wy * vy) / (vx * vx + vy * vy or 1.0)))
    dx, dy = px - (ax + t * vx), py - (ay + t * vy)
    return math.hypot(dx, dy)


def render_master() -> list[list[tuple[int, int, int, int]]]:
    s = MASTER
    cx, cy, r = s * 0.42, s * 0.40, s * 0.26          # 網のリング
    ring_w = s * 0.045
    mesh_w = s * 0.018
    hx1, hy1 = cx + r * 0.72, cy + r * 0.72           # 柄: リング縁→右下
    hx2, hy2 = s * 0.80, s * 0.82
    handle_w = s * 0.055
    fx, fy, fr = cx - r * 0.28, cy + r * 0.18, s * 0.055  # 網の中の粒
    corner = s * 0.22                                  # 角丸半径

    img: list[list[tuple[int, int, int, int]]] = []
    for y in range(s):
        row: list[tuple[int, int, int, int]] = []
        for x in range(s):
            # --- 角丸矩形の内外（外は透明） ---
            qx = max(abs(x - s / 2) - (s / 2 - corner), 0.0)
            qy = max(abs(y - s / 2) - (s / 2 - corner), 0.0)
            if math.hypot(qx, qy) > corner:
                row.append((0, 0, 0, 0))
                continue
            # 背景（縦方向にほんのり明るく）
            t = y / s
            col = tuple(int(BG_HI[i] + (BG[i] - BG_HI[i]) * t) for i in range(3))
            # --- 柄（網より下層） ---
            if _dist_seg(x, y, hx1, hy1, hx2, hy2) <= handle_w / 2:
                col = NET
            d = math.hypot(x - cx, y - cy)
            # --- メッシュ（リング内部のみ・格子） ---
            if d < r - ring_w * 0.3:
                gap = r * 0.52
                for off in (-gap, 0.0, gap):
                    if abs((x - cx) - off) <= mesh_w / 2 or abs((y - cy) - off) <= mesh_w / 2:
                        col = NET
                        break
                # 粒（メッシュの上に載せる）
                if math.hypot(x - fx, y - fy) <= fr:
                    col = FISH
            # --- リング（最上層） ---
            if abs(d - r) <= ring_w / 2:
                col = NET
            row.append((col[0], col[1], col[2], 255))
        img.append(row)
    return img


def downsample(img, size: int):
    s = len(img)
    out = []
    for y in range(size):
        row = []
        y0, y1 = y * s / size, (y + 1) * s / size
        for x in range(size):
            x0, x1 = x * s / size, (x + 1) * s / size
            acc = [0.0, 0.0, 0.0, 0.0]
            n = 0
            for yy in range(int(y0), min(s, int(math.ceil(y1)))):
                for xx in range(int(x0), min(s, int(math.ceil(x1)))):
                    p = img[yy][xx]
                    a = p[3] / 255.0
                    acc[0] += p[0] * a
                    acc[1] += p[1] * a
                    acc[2] += p[2] * a
                    acc[3] += p[3]
                    n += 1
            a = acc[3] / n / 255.0
            if a <= 0.004:
                row.append((0, 0, 0, 0))
            else:
                row.append((int(acc[0] / n / a), int(acc[1] / n / a), int(acc[2] / n / a),
                            int(acc[3] / n)))
        out.append(row)
    return out


def write_png(img, path: Path) -> None:
    h = len(img)
    w = len(img[0])
    raw = b"".join(b"\x00" + b"".join(struct.pack("4B", *px) for px in row) for row in img)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    path.write_bytes(png)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    master = render_master()
    for size in SIZES:
        p = OUT / f"icon{size}.png"
        write_png(downsample(master, size), p)
        print(f"-> {p} ({p.stat().st_size}B)")
