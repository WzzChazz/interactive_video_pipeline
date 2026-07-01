"""
美甲前后对比图生成器 —— 小红书笔记①素材一键产出

用法（在 interactive_video_pipeline 目录下）:
    python make_nail_before_after.py 原图1.jpg 原图2.jpg 原图3.jpg ...

产出（storage/nail_demo/）:
    after_N.jpg    每张原图的 AI 精修成品（"后"）
    compare_N.jpg  前后左右分屏（做封面 / 正文对比图，直接上传）
    grid.jpg       所有精修图拼的九宫格（做结尾"效果炸场"图）

原理: 复用现有 Seedream 4.0 图生图（core/image_gen._call_jimeng_seedream），
      把你随手拍的美甲照片当参考图喂进去，只精修画面、不改款式。
"""
import sys
from pathlib import Path

from core.image_gen import _call_jimeng_seedream, _download_image

OUT = Path("storage/nail_demo")
OUT.mkdir(parents=True, exist_ok=True)

# 关键：明确"保留款式、只精修画面"，否则 AI 会把甲片形状颜色都改了，就不是同一双手了
PROMPT = (
    "为美甲店制作高级感宣传海报。严格保留原图中的美甲款式、指甲形状、颜色、图案完全不变，"
    "只优化画面：背景换成干净纯色或柔和渐变，加入专业柔光与自然高光，手部皮肤通透真实，"
    "构图精致、居中留白，时尚杂志质感，商业摄影级清晰度，无文字、无水印。"
)


def refine(raw_path: str, idx: int) -> Path:
    print(f"[{idx}] 精修中: {raw_path} ...")
    url = _call_jimeng_seedream(PROMPT, ref_image_path=raw_path)
    out = OUT / f"after_{idx}.jpg"
    _download_image(url, out)
    print(f"    → 后: {out}")
    return out


def side_by_side(before: str, after: Path, idx: int) -> None:
    from PIL import Image, ImageDraw
    a = Image.open(before).convert("RGB")
    b = Image.open(after).convert("RGB")
    h = 1600
    a = a.resize((int(a.width * h / a.height), h))
    b = b.resize((int(b.width * h / b.height), h))
    canvas = Image.new("RGB", (a.width + b.width, h), "white")
    canvas.paste(a, (0, 0))
    canvas.paste(b, (a.width, 0))
    d = ImageDraw.Draw(canvas)
    d.text((30, 30), "店主随手拍", fill="white")
    d.text((a.width + 30, 30), "AI 改完", fill="white")
    out = OUT / f"compare_{idx}.jpg"
    canvas.save(out, quality=92)
    print(f"    → 对比: {out}")


def make_grid(afters: list[Path]) -> None:
    from PIL import Image
    if not afters:
        return
    cell = 900
    imgs = [Image.open(p).convert("RGB").resize((cell, cell)) for p in afters[:9]]
    grid = Image.new("RGB", (cell * 3, cell * 3), "white")
    for i, im in enumerate(imgs):
        grid.paste(im, ((i % 3) * cell, (i // 3) * cell))
    out = OUT / "grid.jpg"
    grid.save(out, quality=90)
    print(f"→ 九宫格: {out}")


def main() -> None:
    raws = sys.argv[1:]
    if not raws:
        print(__doc__)
        return
    afters: list[Path] = []
    for i, raw in enumerate(raws, 1):
        if not Path(raw).exists():
            print(f"[跳过] 找不到文件: {raw}")
            continue
        try:
            af = refine(raw, i)
            afters.append(af)
            side_by_side(raw, af, i)
        except Exception as e:
            print(f"[{i}] 失败: {e}")
    try:
        make_grid(afters)
    except Exception as e:
        print(f"九宫格跳过（装一下 Pillow?）: {e}")
    print(f"\n完成 → {OUT.resolve()}")


if __name__ == "__main__":
    main()
