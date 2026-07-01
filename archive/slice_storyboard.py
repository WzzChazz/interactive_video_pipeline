import sys
from pathlib import Path
from PIL import Image

def slice_storyboard(input_path: str, episode_tag: str):
    img = Image.open(input_path)
    width, height = img.size
    
    # Grid is 3 columns, 2 rows
    cell_width = width // 3
    cell_height = height // 2
    
    out_dir = Path(f"storage/temp/{episode_tag}/images")
    out_dir.mkdir(parents=True, exist_ok=True)
    
    idx = 1
    for row in range(2):
        for col in range(3):
            left = col * cell_width
            upper = row * cell_height
            right = (col + 1) * cell_width
            lower = (row + 1) * cell_height
            
            # Crop the cell
            cell = img.crop((left, upper, right, lower))
            
            # No subtitles to crop out anymore! Keep the full cell.
            # Resize to 9:16 (576x1024)
            cell = cell.resize((576, 1024), Image.Resampling.LANCZOS)
            
            out_path = out_dir / f"scene_{idx:02d}.png"
            cell.save(out_path)
            print(f"Saved {out_path}")
            idx += 1

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 slice_storyboard.py <path_to_image>")
        sys.exit(1)
    
    slice_storyboard(sys.argv[1], "S01E024")
