# -*- coding: utf-8 -*-
"""生成 ImageFinder 应用图标 app.ico：蓝紫渐变圆角方块 + 白色线条（图片框+放大镜）
渐变在小画布逐像素画（精确），图形在 4x 放大画布上绘制保证平滑，最后输出多尺寸 ico。
"""
import os
from PIL import Image, ImageDraw

S = 256            # 渐变画布
BIG = 1024         # 图形绘制画布（4x 超采样）
C1 = (91, 140, 255)   # #5B8CFF
C2 = (139, 92, 246)   # #8B5CF6

# 1) 对角线性渐变
grad = Image.new("RGB", (S, S))
px = grad.load()
for y in range(S):
    for x in range(S):
        t = (x + y) / (2 * (S - 1))
        px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(C1, C2))
img = grad.resize((BIG, BIG), Image.BILINEAR)

# 2) 圆角裁剪（squircle 感）
R = int(BIG * 0.225)
mask = Image.new("L", (BIG, BIG), 0)
ImageDraw.Draw(mask).rounded_rectangle([0, 0, BIG - 1, BIG - 1], radius=R, fill=255)
canvas = Image.new("RGBA", (BIG, BIG), (0, 0, 0, 0))
canvas.paste(img, (0, 0), mask)
d = ImageDraw.Draw(canvas)

W = 54            # 线宽
WHITE = (255, 255, 255, 255)

# 3) 图片框（左上）：圆角矩形 + 山形 + 圆点
bx0, by0, bx1, by1 = 200, 240, 640, 650
d.rounded_rectangle([bx0, by0, bx1, by1], radius=95, outline=WHITE, width=W)
# 太阳点
d.ellipse([bx0+95, by0+95, bx0+95+62, by0+95+62], fill=WHITE)
# 山形折线：单个对称山峰（16px 小尺寸下更清晰）
d.line([(bx0+70, by1-110), (bx0+225, by1-330)], fill=WHITE, width=W, joint="curve")
d.line([(bx0+225, by1-330), (bx0+385, by1-110)], fill=WHITE, width=W, joint="curve")

# 4) 放大镜（右下，压在图片框角上，带背景色描边增加层次）
ring_bg = (91, 140, 255, 255)  # 与渐变近似色做背景圈，形成"断开"效果
cx, cy, r = 655, 655, 175
# 先画一圈背景色粗描边把图片框线条"顶开"
d.ellipse([cx-r-46, cy-r-46, cx+r+46, cy+r+46], outline=ring_bg, width=46)
d.ellipse([cx-r, cy-r, cx+r, cy+r], outline=WHITE, width=W)
# 镜柄
d.line([(cx+r*0.72, cy+r*0.72), (cx+r*1.32, cy+r*1.32)], fill=WHITE, width=int(W*1.15))

# 5) 输出多尺寸 ico 与预览 png
out_dir = os.path.dirname(os.path.abspath(__file__))
ico_path = os.path.join(out_dir, "app.ico")
canvas.resize((256, 256), Image.LANCZOS).save(
    ico_path,
    sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
canvas.resize((512, 512), Image.LANCZOS).save(os.path.join(out_dir, "icon_preview.png"))
print("OK:", ico_path, os.path.getsize(ico_path), "bytes")
