import json
import sys
import time
from pathlib import Path

sys.path.append('/Users/mac/project/interactive_video_pipeline')
from database.db_session import get_session
from database.models import Episode
from core.image_gen import generate_single_image

# ─────────────────────────────────────────────────────────────────────────────
# 优化 1 (P0): 五官原子化拆解
# 来源：awesome-gpt-image-2 人物章节
# 原则："不要只写'很美的女孩'，大模型不知道你的审美标准，拆解成具体特征"
# ─────────────────────────────────────────────────────────────────────────────
APPEARANCE = {
    "skin": "pale, slightly translucent, no makeup, dry and fatigued",
    "eyes": "monolid single eyelids, slightly upward-slanting outer corners, dark brown iris, bloodshot, heavy dark circles",
    "nose": "small, straight, slightly upturned tip",
    "lips": "thin, pale, slightly chapped, neutral closed expression",
    "hair": "pure black, shoulder-length bob, slightly disheveled and damp from sweat",
    "overall": "25-year-old Chinese female doctor Lin Yue, exhausted and terrified expression"
}

# ─────────────────────────────────────────────────────────────────────────────
# 优化 2 (P0): 服装材质精细化
# 来源：awesome-gpt-image-2 "服装材质是灵魂，写清楚材质让角色立刻变立体"
# ─────────────────────────────────────────────────────────────────────────────
ATTIRE_LINYUE = "white cotton lab coat, slightly wrinkled and untucked, green surgical scrubs underneath, hospital ID badge clipped to left chest pocket"
ATTIRE_CLONE  = "identical white cotton lab coat, pristine and perfectly pressed, no badge, no stethoscope"

prompts = [
    # ── Scene 1: 发现档案 ─────────────────────────────────────────────────────
    {
        # 优化 3 (P1): 精准镜头语言 —— "Profile, Dutch angle, low exposure"
        "type": "Low-angle profile shot, 35mm anamorphic lens",
        "character": {
            "identity": "25-year-old Chinese female doctor Lin Yue",
            "appearance": APPEARANCE,
            "attire": ATTIRE_LINYUE
        },
        # 优化 4 (P1): 动词事件驱动 —— "正在做某事" 而非静态描述
        "pose": "Mid-action: her trembling hands are actively unfolding a yellowed manila folder, eyes moving across the page, head tilted sharply downward. She is definitively NOT facing the camera.",
        "environment": "Pitch-dark hospital archive room, rows of dusty grey metal filing cabinets, single flashlight beam aimed downward onto the documents",
        "style": "Cinematic suspense thriller, teal and black color grading, deep shadows, high contrast",
        # 优化 5 (P2): 摄影参数层（FLUX 有效，万相部分有效）
        "camera_spec": {
            "lens": "35mm anamorphic",
            "aperture": "f/2.0, moderate depth of field, subject sharp, background soft",
            "film_emulation": "Kodak Vision3 500T, subtle cyan shadows"
        },
        "constraints": "NO blood, NO frontal face view, ONLY one person, flashlight illuminates documents only, NOT the face"
    },

    # ── Scene 2: 心理崩溃 ─────────────────────────────────────────────────────
    {
        "type": "Medium shot, slightly high angle looking down",
        "character": {
            "identity": "Lin Yue",
            "appearance": APPEARANCE,
            "attire": ATTIRE_LINYUE
        },
        # 动词事件驱动：正在颤抖、正在大汗
        "pose": "Seated and actively recoiling backward in her chair, both hands gripping the desk edge as her body convulses with trembling, sweat visibly dripping from her forehead, mouth slightly open in a silent gasp",
        "environment": "Messy metal desk overflowing with yellowed case files and scattered documents, single bare fluorescent tube overhead flickering",
        "style": "Cinematic horror, pitch black background, cold blue-white light casting deep oppressive shadows under her eyes and chin",
        "camera_spec": {
            "lens": "50mm",
            "aperture": "f/1.8, subject sharp, background blurred",
            "film_emulation": "desaturated with pushed contrast"
        },
        "constraints": "NO blood, exactly ONE person, photorealistic, no bright warm light"
    },

    # ── Scene 3: 听到动静 ─────────────────────────────────────────────────────
    {
        # 精准镜头：Dutch angle 增加戏剧感
        "type": "Wide shot, Dutch angle (camera tilted 15 degrees)",
        "character": {
            "identity": "Lin Yue",
            "appearance": APPEARANCE,
            "attire": ATTIRE_LINYUE
        },
        # 动词事件：正在转头
        "pose": "Caught mid-turn: her body is pressed flat against a filing cabinet, one hand bracing the metal surface, head snapping sharply toward the off-screen doorway, pupils dilated with terror, mouth half-open",
        "environment": "Narrow corridor between tall grey metal filing cabinets, floor-level cold fog, a single flashlight beam slicing through darkness from the left",
        "style": "Cinematic horror, extreme low-key lighting, cold teal tones, handheld-camera feel",
        "camera_spec": {
            "lens": "24mm wide angle",
            "aperture": "f/2.8",
            "film_emulation": "high grain, Kodak 3200, dark and noisy"
        },
        "constraints": "NO blood, full body must be visible, NO warm light, Dutch angle mandatory"
    },

    # ── Scene 4: 克隆体现身 ───────────────────────────────────────────────────
    {
        "type": "Slow zoom medium shot",
        "character": {
            "identity": "A clone woman physically identical to Lin Yue but with subtle uncanny valley differences",
            "appearance": {
                "skin": "flawless, porcelain-smooth, no pores, slightly too perfect",
                "eyes": "monolid, identical to Lin Yue but completely emotionless, dead fish eyes, no micro-expressions",
                "nose": "small and straight, identical to Lin Yue",
                "lips": "thin, pale, perfectly still, never moving",
                "hair": "pure black shoulder-length bob, perfectly combed, unnaturally neat",
                "overall": "25-year-old Chinese woman, physically identical to Lin Yue but radiates an uncanny valley stillness"
            },
            "attire": ATTIRE_CLONE
        },
        # 动词事件：正在走入，而非站在那里
        "pose": "Stepping slowly through the archive room doorway mid-stride, holding a yellow manila folder in one outstretched hand offering it forward, head perfectly level, gaze fixed straight ahead with zero emotion",
        "environment": "Hospital archive room doorway, pale cold backlight silhouetting her figure from behind",
        "style": "Uncanny valley horror, clinical cold white lighting, eerie stillness, hyper-realistic",
        "camera_spec": {
            "lens": "85mm portrait lens",
            "aperture": "f/2.0, subject sharp, doorframe slightly soft"
        },
        "constraints": "NO blood, exactly ONE person, uncanny valley effect mandatory, NO warm lighting"
    },

    # ── Scene 5: 双生对峙 ─────────────────────────────────────────────────────
    {
        # 精准镜头：对称构图强化双生恐惧感
        "type": "Wide shot, symmetrical composition, eye-level",
        "character": {
            "identity": "TWO physically identical women facing each other in a narrow corridor",
            "left_woman": {
                "identity": "Lin Yue (the real one)",
                "appearance": APPEARANCE,
                "attire": ATTIRE_LINYUE,
                "state": "actively pressing her back against filing cabinet, tears streaming down her face, hands raised defensively"
            },
            "right_woman": {
                "identity": "Clone Lin Yue",
                "appearance": "Identical face but flawless skin, dead emotionless eyes",
                "attire": ATTIRE_CLONE,
                "state": "advancing one slow deliberate step forward, hand slightly extended, expression completely blank"
            }
        },
        "pose": "Dynamic confrontation: left woman actively recoiling and pressing into the metal cabinet while right woman is mid-step walking toward her. They are facing each other. The space between them is closing.",
        "environment": "Extremely narrow gap between towering grey metal filing cabinets, overhead emergency light casting split shadows",
        "style": "Cinematic horror, split cold/warm lighting: cold blue on clone, warm sickly yellow on real Lin Yue",
        "camera_spec": {
            "lens": "35mm",
            "aperture": "f/4.0, both women must be sharp"
        },
        "constraints": "NO blood, EXACTLY two women with identical faces, real one shows fear, clone is expressionless, must show both full bodies"
    },

    # ── Scene 6: 打破第四面墙 ─────────────────────────────────────────────────
    {
        # 精准镜头：Crash zoom, ECU (Extreme Close-Up)
        "type": "Extreme close-up (ECU), crash zoom perspective, low angle",
        "character": {
            "identity": "The clone woman",
            "appearance": {
                "skin": "flawless, porcelain, slightly too smooth to be real",
                "eyes": "dead, glassy, emotionless, staring directly into camera lens, pupils slightly too large",
                "lips": "thin, pale, sealed shut",
                "hair": "pure black bob, immaculate",
                "overall": "uncanny valley perfection, sub-human stillness"
            },
            "attire": ATTIRE_CLONE
        },
        # 动词事件：正在伸手
        "pose": "Active motion: her right hand is mid-reach thrusting toward the camera lens, fingers splayed, wrist approaching and filling lower half of frame. Her face occupies the upper half, staring dead-eyed directly into the lens. Breaking the fourth wall.",
        "environment": "Pure pitch black void, zero background detail",
        "style": "Cinematic horror, single harsh key light from directly above casting dramatic shadows under eyes and nose, extreme contrast",
        "camera_spec": {
            "lens": "28mm wide angle, distortion on outstretched hand creates scale dread",
            "aperture": "f/2.8",
            "film_emulation": "high contrast B&W desaturation push"
        },
        "constraints": "NO blood, ONLY one person, hand must reach toward camera filling lower frame, face in upper frame, fourth wall breaking"
    }
]

def run():
    with get_session() as session:
        ep = session.query(Episode).filter_by(season_id=1, episode_number=24).first()
        script = json.loads(ep.script_json)
        for i, scene in enumerate(script.get('scenes', [])):
            if i < len(prompts):
                scene['visual_prompt'] = prompts[i]
        ep.script_json = json.dumps(script, ensure_ascii=False)
        session.commit()
        print("Database updated with structured JSON templates.")

    for i in range(1, 7):
        save_path = Path(f'/Users/mac/project/interactive_video_pipeline/storage/temp/S01E024/images/scene_0{i}.png')
        print(f"Regenerating Scene {i}...")
        try:
            generate_single_image(i, prompts[i-1], save_path)
            print(f"Scene {i} success.")
        except Exception as e:
            print(f"Scene {i} failed: {e}")
        
        # 严格遵守 API Rate Limit
        print("Sleeping 3 seconds to prevent rate limit...")
        time.sleep(3)

    print("All scenes completed.")

if __name__ == "__main__":
    run()
