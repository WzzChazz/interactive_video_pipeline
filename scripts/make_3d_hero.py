"""
scripts/make_3d_hero.py — 生成 3D 画风的新定妆照(画风切换后必跑,替换旧动漫风 hero.png)。
用法: python3 scripts/make_3d_hero.py
产出: storage/ip_reference/hero3d_1.png ~ hero3d_4.png(4张候选)
然后你挑最萌的一张: cp storage/ip_reference/hero3d_N.png storage/ip_reference/hero.png
(hero.png = IP_REFERENCE_IMAGE,之后每张分镜都会用它做 Seedream 参考图锁角色一致性)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from loguru import logger


def main() -> int:
    from core.image_gen import _call_jimeng_seedream, _download_image
    from config.themes import THEMES

    lock = THEMES["capybara_healing"]["character_prompt_lock"]
    prompt = (
        f"{lock}, Tuan Tuan alone front-facing and fully visible, sitting cozily on a soft rug, "
        f"bright warm sunlit living room, character reference sheet quality, clean composition, "
        f"centered, vertical 9:16"
    )
    out_dir = Path("storage/ip_reference")
    out_dir.mkdir(parents=True, exist_ok=True)

    ok = 0
    for i in range(1, 5):
        try:
            logger.info(f"生成 3D 定妆照候选 {i}/4 ...")
            url = _call_jimeng_seedream(prompt)  # 不传参考图:全新画风,不被旧动漫风拖住
            _download_image(url, out_dir / f"hero3d_{i}.png")
            logger.success(f"  ✓ {out_dir}/hero3d_{i}.png")
            ok += 1
        except Exception as e:
            logger.warning(f"  ✗ 候选{i}失败: {str(e)[:120]}")

    if ok:
        logger.success(f"完成 {ok}/4。挑最萌的一张执行:")
        logger.success("  cp storage/ip_reference/hero3d_N.png storage/ip_reference/hero.png")
    else:
        logger.error("全部失败(检查火山Ark余额/JIMENG_API_KEY)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
