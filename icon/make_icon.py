# -*- coding: utf-8 -*-
"""生成 Chronos 闹钟的图标（深蓝科技风 + 闹钟造型）。

输出：
  icon/appicon.ico  —— 多分辨率（16~256），用于打包进 EXE 与窗口标题栏
  icon/appicon.png  —— 512x512 透明 PNG，用于 README 与托盘（托盘回退）

纯 PIL 绘制，无需外部素材；风格与 Chronos 深蓝设计系统一致。
"""
import os
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))

# 配色（与 Chronos 深蓝科技风一致）
BG_TOP = (12, 31, 58)       # 顶部深蓝
BG_BOTTOM = (5, 12, 26)     # 底部更深
CYAN = (34, 211, 238)       # 主青色 #22d3ee
CYAN_BRIGHT = (80, 226, 247)
FACE = (13, 30, 54)         # 表盘内填充


def rounded_gradient_tile(size, padding):
    """画一个圆角方形渐变底（深蓝科技风），返回 RGBA 图像。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    # 先画整张竖向渐变
    grad = Image.new("RGBA", (size, size), BG_TOP + (255,))
    bottom = BG_BOTTOM + (255,)
    for y in range(size):
        t = y / max(1, size - 1)
        r = int(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t)
        g = int(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t)
        b = int(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t)
        for x in range(size):
            grad.putpixel((x, y), (r, g, b, 255))
    # 圆角遮罩
    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    r = int(size * 0.22)
    md.rounded_rectangle([padding, padding, size - padding, size - padding], radius=r, fill=255)
    img.paste(grad, (0, 0), mask)
    return img


def draw_clock(draw, cx, cy, R, w):
    """在 (cx,cy) 处绘制闹钟造型（表盘 + 双铃 + 支脚 + 指针）。"""
    # 支脚（先画，置于表盘之下）
    leg = w * 0.7
    draw.line([(cx - R * 0.55, cy + R * 0.78), (cx - R * 0.92, cy + R * 1.18)], fill=CYAN, width=int(leg))
    draw.line([(cx + R * 0.55, cy + R * 0.78), (cx + R * 0.92, cy + R * 1.18)], fill=CYAN, width=int(leg))
    # 双铃（表盘上方两侧的圆环）
    bell_r = R * 0.42
    for sx in (-1, 1):
        bx, by = cx + sx * R * 0.82, cy - R * 0.92
        draw.ellipse([bx - bell_r, by - bell_r, bx + bell_r, by + bell_r],
                     outline=CYAN, width=int(w * 0.9))
    # 铃间小锤
    draw.line([(cx - R * 0.82, cy - R * 1.28), (cx + R * 0.82, cy - R * 1.28)],
              fill=CYAN, width=int(w * 0.7))
    # 表盘
    draw.ellipse([cx - R, cy - R, cx + R, cy + R], outline=CYAN, width=int(w))
    # 指针（经典 10 点 / 2 点姿态）
    # 时针 ~ 10:10 风格：时针指向 10，分针指向 2
    import math
    # 分针（较长，指向 2 点方向）
    a_min = math.radians(-60)  # 2 点
    lm = R * 0.62
    draw.line([(cx, cy), (cx + lm * math.cos(a_min), cy + lm * math.sin(a_min))],
              fill=CYAN_BRIGHT, width=int(w * 0.9))
    # 时针（较短，指向 10 点方向）
    a_hour = math.radians(-120)  # 10 点
    lh = R * 0.42
    draw.line([(cx, cy), (cx + lh * math.cos(a_hour), cy + lh * math.sin(a_hour))],
              fill=CYAN_BRIGHT, width=int(w * 1.1))
    # 中心点
    cr = max(2, int(w * 0.5))
    draw.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=CYAN_BRIGHT)


def make_icon(size):
    img = rounded_gradient_tile(size, int(size * 0.06))
    d = ImageDraw.Draw(img)
    pad = size * 0.17
    cx = cy = size / 2
    R = size / 2 - pad
    w = max(2, int(size * 0.05))
    draw_clock(d, cx, cy, R, w)
    return img


def main():
    # 多分辨率 ICO
    sizes = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
    images = [make_icon(s) for s in sizes]
    ico_path = os.path.join(HERE, "appicon.ico")
    images[0].save(ico_path, sizes=[(s, s) for s in sizes], append_images=images[1:],
                   format="ICO")
    # 大尺寸 PNG（README / 托盘回退）
    png = make_icon(512)
    png_path = os.path.join(HERE, "appicon.png")
    png.save(png_path)
    print("OK", ico_path, png_path)


if __name__ == "__main__":
    main()
