import sys
import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageEnhance

_PROJECT_ROOT = Path(__file__).resolve().parent

image_path = _PROJECT_ROOT / "storage/temp/S01E024/images/scene_06.png"
output_path = _PROJECT_ROOT / "storage/output/S01E024/S01E024_cover.jpg"

if not image_path.exists():
    print(f"Error: {image_path} not found.")
    sys.exit(1)

# Ensure output dir exists
output_path.parent.mkdir(parents=True, exist_ok=True)

# Open image
img = Image.open(image_path).convert("RGB")

# Apply dark/horror filter (decrease brightness, increase contrast)
img = ImageEnhance.Brightness(img).enhance(0.65)
img = ImageEnhance.Contrast(img).enhance(1.4)
img = ImageEnhance.Color(img).enhance(0.5)

draw = ImageDraw.Draw(img)
w, h = img.size

# Find a good font
font_paths = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/Library/Fonts/Arial Unicode.ttf"
]
font_path = None
for p in font_paths:
    if os.path.exists(p):
        font_path = p
        break

if not font_path:
    print("Could not find a suitable font.")
    sys.exit(1)

# Title: "第 9 集 | 双生档案"
title = "第九集 | 双生档案"
subtitle = "▶ 真假双生子！"

# Calculate font sizes that fit
# We want the text width to be max 90% of image width (576 * 0.9 = 518)
title_font_size = 50
title_font = ImageFont.truetype(font_path, title_font_size)

# Calculate title width/height (using getbbox for Pillow >= 8.0)
title_bbox = draw.textbbox((0, 0), title, font=title_font)
title_w = title_bbox[2] - title_bbox[0]
title_h = title_bbox[3] - title_bbox[1]

subtitle_font_size = 45
subtitle_font = ImageFont.truetype(font_path, subtitle_font_size)
subtitle_bbox = draw.textbbox((0, 0), subtitle, font=subtitle_font)
sub_w = subtitle_bbox[2] - subtitle_bbox[0]
sub_h = subtitle_bbox[3] - subtitle_bbox[1]

# Draw shadow for title
tx = (w - title_w) // 2
ty = h // 2 - 250
draw.text((tx+4, ty+4), title, font=title_font, fill=(150, 0, 0))
# Draw title
draw.text((tx, ty), title, font=title_font, fill=(255, 255, 255))

# Draw shadow for subtitle
sx = (w - sub_w) // 2
sy = h // 2 - 80
draw.text((sx+3, sy+3), subtitle, font=subtitle_font, fill=(0, 0, 0))
# Draw subtitle
draw.text((sx, sy), subtitle, font=subtitle_font, fill=(255, 255, 0))

img.save(output_path, quality=95)
print(f"Custom cover generated successfully at {output_path}")

# copy it to brain artifact directory
import shutil
brain_dir = Path("/Users/mac/.gemini/antigravity-ide/brain/aed83a3b-1de6-4a95-9326-2a29711d8646")
shutil.copy(output_path, brain_dir / "S01E024_cover_ep9_v3.jpg")
