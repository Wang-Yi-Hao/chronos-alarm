# -*- coding: utf-8 -*-
"""生成 Chronos 闹钟的应用图标（深蓝科技风 · 环与指针）。

设计规格（与设计稿「Chronos 应用图标 · 方案 A」一致，均按画布比例计算）：
  画布      正方形，圆角 22%
  底色      竖向渐变 #16275A → #070E22，左上 30%/16% 处径向青色光晕
  主图形    青色圆环，中心线直径 58%（即半径 29%）
  指针      分针指向 12（长 23%），时针指向 7（长 19.9%），圆头
  中心点    半径 3% 实心青点

关键：**不是把大图缩小**，而是每个尺寸按自己的比例重绘，并按三档做简化：
  >= 128  完整图形，含环上高光弧
  48–96   去掉高光弧，指针加粗到 5.5% / 6.0%
  <  48   环加粗到 13%，去掉中心点，只留两根针

输出：
  icon/appicon.ico  —— 多分辨率（16~256），用于打包进 EXE 与窗口标题栏
  icon/appicon.png  —— 512x512 透明 PNG，用于 README 与托盘（托盘回退）

纯 PIL 绘制，无需外部素材。
"""
import io
import math
import os
import struct

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))

# —— 配色（与 Chronos 深蓝科技风一致）——
BG_TOP = (22, 39, 90)        # #16275A
BG_BOTTOM = (7, 14, 34)      # #070E22
CYAN = (0, 212, 255)         # #00D4FF 主环
CYAN_HI = (156, 243, 255)    # #9CF3FF 环上高光弧
WHITE = (238, 244, 255)      # #EEF4FF 指针

# —— 形状比例（全部相对画布边长）——
R_TILE = 0.22        # 圆角
R_RING = 0.29        # 圆环中心线半径（直径 58%）
L_MIN = 0.230        # 分针长度
L_HOUR = 0.199       # 时针长度
BLOOM_XY = (0.30, 0.16)   # 光晕中心
BLOOM_R = 0.80            # 光晕半径
BLOOM_A = 0.22            # 光晕最大不透明度

# —— 三档简化： (最小尺寸, 环宽, 分针宽, 时针宽, 中心点半径, 是否高光弧) ——
TIERS = (
    (128, 0.075, 0.040, 0.048, 0.030, True),
    (48, 0.085, 0.055, 0.060, 0.032, False),
    (0, 0.130, 0.090, 0.100, 0.000, False),
)


def _tier(size):
    for min_size, ring_w, min_w, hour_w, dot_r, hi in TIERS:
        if size >= min_size:
            return ring_w, min_w, hour_w, dot_r, hi


def _supersample(size):
    """小尺寸需要更多超采样，才能保住圆环边缘。"""
    return max(2, min(8, 1024 // size))


def _vertical_gradient(w, top, bottom):
    img = Image.new("RGBA", (1, w))
    px = img.load()
    for y in range(w):
        t = y / max(1, w - 1)
        px[0, y] = (
            int(top[0] + (bottom[0] - top[0]) * t),
            int(top[1] + (bottom[1] - top[1]) * t),
            int(top[2] + (bottom[2] - top[2]) * t),
            255,
        )
    return img.resize((w, w), Image.Resampling.BILINEAR)


def _bloom(w, cx, cy, radius, color, amax):
    """径向光晕：先在 96² 的小图上按距离算 alpha，再放大，避免逐像素画大图。"""
    small = 96
    mask = Image.new("L", (small, small), 0)
    px = mask.load()
    k = small / w
    for y in range(small):
        for x in range(small):
            d = math.hypot((x + 0.5) / k - cx, (y + 0.5) / k - cy) / radius
            if d < 1:
                px[x, y] = int(255 * amax * (1 - d) ** 2)
    mask = mask.resize((w, w), Image.Resampling.BICUBIC)
    layer = Image.new("RGBA", (w, w), color + (0,))
    layer.putalpha(mask)
    return layer


def _ring_box(cx, cy, radius, width):
    """PIL 的描边是从外向内画的，所以外框半径 = 中心线半径 + 半个线宽。"""
    o = radius + width / 2
    return [cx - o, cy - o, cx + o, cy + o]


def _hand(draw, cx, cy, length, width, angle_deg):
    """从圆心按「12 点顺时针」的角度画一根圆头指针。"""
    rad = math.radians(angle_deg)
    x2 = cx + math.sin(rad) * length
    y2 = cy - math.cos(rad) * length
    draw.line([(cx, cy), (x2, y2)], fill=WHITE + (255,), width=width)
    r = width / 2
    for x, y in ((cx, cy), (x2, y2)):
        draw.ellipse([x - r, y - r, x + r, y + r], fill=WHITE + (255,))


def _mix(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def _ring_highlight(draw, box, start, end, width, segments=28):
    """环上高光：分段绘制并让两端淡回主环色，避免出现生硬的切口。"""
    step = (end - start) / segments
    for i in range(segments):
        # 中间最亮、两端归零，形成柔和的明暗过渡
        w = math.sin(math.pi * (i + 0.5) / segments) ** 0.8
        a0 = start + i * step
        draw.arc(box, a0, a0 + step + 1.2,      # 略微重叠，消除分段缝隙
                 fill=_mix(CYAN, CYAN_HI, w) + (255,), width=width)


def render(size):
    ring_w, min_w, hour_w, dot_r, hi = _tier(size)
    k = _supersample(size)
    w = size * k
    c = w / 2.0

    img = Image.new("RGBA", (w, w), (0, 0, 0, 0))
    img.paste(_vertical_gradient(w, BG_TOP, BG_BOTTOM), (0, 0))
    img.alpha_composite(
        _bloom(w, BLOOM_XY[0] * w, BLOOM_XY[1] * w, BLOOM_R * w, CYAN, BLOOM_A)
    )

    d = ImageDraw.Draw(img)
    r_ring = R_RING * w
    sw = max(1, round(ring_w * w))
    box = _ring_box(c, c, r_ring, sw)

    # 圆环
    d.ellipse(box, outline=CYAN + (255,), width=sw)

    # 环上高光弧：200° → 320°（左上经顶部到右上），只在大尺寸保留
    if hi:
        _ring_highlight(d, box, 200, 320, sw)

    # 指针：分针指 12，时针指 7
    _hand(d, c, c, L_MIN * w, max(1, round(min_w * w)), 0)
    _hand(d, c, c, L_HOUR * w, max(1, round(hour_w * w)), 210)

    # 中心点
    if dot_r > 0:
        r_dot = dot_r * w
        d.ellipse([c - r_dot, c - r_dot, c + r_dot, c + r_dot], fill=CYAN + (255,))

    # 圆角裁切（放在最后，光晕也被一起裁掉）
    mask = Image.new("L", (w, w), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, w - 1, w - 1], radius=int(R_TILE * w), fill=255
    )
    img.putalpha(mask)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def _dib_frame(im):
    """把 RGBA 图编码成 ICO 里的 DIB 帧（32bpp + AND 掩码）。

    小于 256 的帧用 DIB 而不是 PNG：老一点的资源管理器、任务栏缓存和部分打包器
    对 PNG 帧支持不完整，DIB 是各版本 Windows 都吃的格式。
    """
    im = im.convert("RGBA")
    w, h = im.size
    px = im.load()

    xor = bytearray()
    for y in range(h - 1, -1, -1):                 # DIB 是自下而上存的
        for x in range(w):
            r, g, b, a = px[x, y]
            xor += bytes((b, g, r, a))

    stride = ((w + 31) // 32) * 4                  # 1bpp，行按 4 字节对齐
    mask = bytearray()
    for y in range(h - 1, -1, -1):
        row = bytearray(stride)
        for x in range(w):
            if px[x, y][3] < 128:                  # 透明处置 1
                row[x // 8] |= 0x80 >> (x % 8)
        mask += row

    header = struct.pack("<IiiHHIIiiII",
                         40, w, h * 2,             # biHeight 要乘 2（图像 + 掩码）
                         1, 32, 0, len(xor) + len(mask), 0, 0, 0, 0)
    return header + bytes(xor) + bytes(mask)


def _write_ico(path, images):
    """手写 ICO 容器：PIL 的 ICO 写入器只会把基图缩放，喂不进「每档单独绘制」的帧。"""
    frames = []
    for im in images:
        if im.size[0] >= 256:
            buf = io.BytesIO()
            im.save(buf, format="PNG", optimize=True)   # 256 用 PNG 压缩省体积
            frames.append((im.size[0], buf.getvalue()))
        else:
            frames.append((im.size[0], _dib_frame(im)))

    head = struct.pack("<HHH", 0, 1, len(frames))          # reserved, type=icon, count
    offset = 6 + 16 * len(frames)
    entries, body = b"", b""
    for size, data in frames:
        edge = 0 if size >= 256 else size                  # 256 在 ICO 里记 0
        entries += struct.pack("<BBBBHHII", edge, edge, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        body += data

    with open(path, "wb") as fp:
        fp.write(head + entries + body)


def main():
    sizes = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
    images = [render(s) for s in sizes]
    ico_path = os.path.join(HERE, "appicon.ico")
    _write_ico(ico_path, images)

    png_path = os.path.join(HERE, "appicon.png")
    render(512).save(png_path)

    # 自检：读回 ico，确认帧尺寸齐全
    with Image.open(ico_path) as ico:
        got = sorted(s for s, _ in ico.ico.sizes())
    print("sizes in ico:", got)
    print("OK", ico_path, png_path)
    assert set(got) == set(sizes), "ico 帧尺寸不完整"


if __name__ == "__main__":
    main()
