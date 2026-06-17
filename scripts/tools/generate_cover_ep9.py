import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJECT_ROOT))

from core.ffmpeg_compiler import generate_cover

image_path = _PROJECT_ROOT / "storage/temp/S01E024/images/scene_06.png"
output_path = _PROJECT_ROOT / "storage/output/S01E024/S01E024_cover.jpg"

if not image_path.exists():
    print(f"Error: {image_path} not found.")
else:
    # Ensure output dir exists
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    title = "第 9 集 | 双生档案"
    sub_title = "真假双生子！"
    
    print(f"Generating cover: {title}")
    generate_cover(image_path, title, sub_title, output_path)
    print(f"Cover generated successfully at {output_path}")
