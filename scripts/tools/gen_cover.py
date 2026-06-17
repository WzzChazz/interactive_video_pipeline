import sys
from pathlib import Path
from core.ffmpeg_compiler import generate_cover

img_dir = Path("storage/temp/S01E028/images")
if img_dir.exists():
    imgs = sorted(list(img_dir.glob("*.png")))
    if imgs:
        climax_img = imgs[-1]
        out_path = Path("/Users/mac/.gemini/antigravity-ide/brain/aed83a3b-1de6-4a95-9326-2a29711d8646/S01E028_custom_cover.jpg")
        generate_cover(climax_img, "双生档案", "真假林悦！", out_path)
        print(f"Generated at {out_path}")
