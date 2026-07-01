"""
给前后对比图贴上清晰的中文大字（标题 + 随手拍/AI改完 角标）。
读 Desktop 原图 + storage/nail_demo/after_N.jpg，输出 compare_labeled_N.jpg。
封面(第1张)额外加顶部大标题。
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

FONT = "/System/Library/Fonts/STHeiti Medium.ttc"
OUT = Path("storage/nail_demo")

PAIRS = [
    ("/Users/mac/Desktop/微信图片_20260701160401_6078_29.jpg", OUT / "after_1.jpg", "同一双手，差在哪？"),
    ("/Users/mac/Desktop/微信图片_20260701160426_6081_29.jpg", OUT / "after_2.jpg", None),
]


def f(size):
    return ImageFont.truetype(FONT, size, index=0)


def text_with_shadow(draw, xy, text, font, fill="white", shadow="black", off=3):
    x, y = xy
    for dx in (-off, off):
        for dy in (-off, off):
            draw.text((x + dx, y + dy), text, font=font, fill=shadow)
    draw.text((x, y), text, font=font, fill=fill)


def badge(draw, cx, y, text, font, pad=24):
    # 居中的半透明黑底白字角标
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    tw, th = r - l, b - t
    x0, y0 = cx - tw / 2 - pad, y
    x1, y1 = cx + tw / 2 + pad, y + th + pad * 2
    draw.rounded_rectangle([x0, y0, x1, y1], radius=(th + pad * 2) // 2, fill=(0, 0, 0, 180))
    draw.text((cx - tw / 2, y0 + pad - t), text, font=font, fill="white")


def build(before, after, title, idx):
    a = Image.open(before).convert("RGB")
    b = Image.open(after).convert("RGB")
    h = 1600
    a = a.resize((int(a.width * h / a.height), h))
    b = b.resize((int(b.width * h / b.height), h))
    W = a.width + b.width
    canvas = Image.new("RGBA", (W, h), "white")
    canvas.paste(a, (0, 0))
    canvas.paste(b, (a.width, 0))
    d = ImageDraw.Draw(canvas, "RGBA")

    # 底部角标
    badge(d, a.width // 2, h - 150, "随手拍", f(70))
    badge(d, a.width + b.width // 2, h - 150, "AI改完", f(70))

    # 封面顶部大标题
    if title:
        tf = f(96)
        l, t, r, bb = d.textbbox((0, 0), title, font=tf)
        text_with_shadow(d, ((W - (r - l)) // 2, 50 - t), title, tf)

    out = OUT / f"compare_labeled_{idx}.jpg"
    canvas.convert("RGB").save(out, quality=92)
    print(f"→ {out}")


if __name__ == "__main__":
    for i, (bef, aft, title) in enumerate(PAIRS, 1):
        build(bef, aft, title, i)
    print("完成")
