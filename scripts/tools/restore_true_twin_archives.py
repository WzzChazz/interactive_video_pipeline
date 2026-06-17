import json
from pathlib import Path
import sys

_PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJECT_ROOT))

from database.db_session import get_session
from database.models import Episode, EpisodeStatus

true_script = {
  "episode_title": "双生档案",
  "episode_summary": "林悦在档案室发现了一份绝密的实验记录，照片上的女人竟和自己一模一样！她意识到医院正在进行违规的克隆实验，而自己可能就是实验品。就在这时，另一个“林悦”推门而入……",
  "cover_teaser": "真假双生子！",
  "chosen_branch": "A",
  "scenes": [
    {
      "scene_index": 1,
      "dialogue": "这...这不是我吗？",
      "english_dialogue": "Is... isn't this me?",
      "speaker": "林悦",
      "emotion": "shocked",
      "visual_prompt": "Cinematic Masterpiece, Hyper-realistic, 8k resolution, Kodak 35mm film, grainy texture, high contrast low key lighting, terrifying horror movie aesthetic, highly detailed skin pores, realistic sweat drops, 1girl, Lin Yue, 25 years old asian female, sweat-soaked messy black hair sticking to cheeks, severe dark circles, pale skin, bloodshot wide terrified eyes with tears, wearing wrinkled dark blue medical scrubs under an unbuttoned stained white lab coat, extreme close-up on her face, holding a worn manila folder, looking down in horror, pitch black environment, only lit by a single flickering flashlight from below, terrifying shadows",
      "camera_note": "[Fast Zoom In]",
      "sfx_prompt": "buzz of fluorescent light, paper rustling, distant muffled footsteps",
      "is_climax": False
    },
    {
      "scene_index": 2,
      "dialogue": "实验体X-001... 项目代号：镜像... 这不可能是真的...",
      "english_dialogue": "Subject X-001... Project codename: Mirror... This can't be real...",
      "speaker": "林悦",
      "emotion": "fearful",
      "visual_prompt": "Cinematic Masterpiece, Hyper-realistic, 8k resolution, Kodak 35mm film, grainy texture, high contrast low key lighting, terrifying horror movie aesthetic, highly detailed skin pores, realistic sweat drops, 1girl, Lin Yue, 25 years old asian female, sweat-soaked messy black hair sticking to cheeks, severe dark circles, pale skin, bloodshot wide terrified eyes with tears, wearing wrinkled dark blue medical scrubs under an unbuttoned stained white lab coat, sitting at a cluttered metal desk with scattered papers, hands trembling, sweat on forehead, background rows of grey filing cabinets in pure darkness, pitch black environment, only lit by a single flickering flashlight from below, terrifying shadows",
      "camera_note": "[Slow pan]",
      "sfx_prompt": "paper crinkling, her rapid breathing, a single drip of water from a pipe",
      "is_climax": False
    },
    {
      "scene_index": 3,
      "dialogue": "谁？！",
      "english_dialogue": "Who's there?!",
      "speaker": "林悦",
      "emotion": "fearful",
      "visual_prompt": "Cinematic Masterpiece, Hyper-realistic, 8k resolution, Kodak 35mm film, grainy texture, high contrast low key lighting, terrifying horror movie aesthetic, highly detailed skin pores, realistic sweat drops, 1girl, Lin Yue, 25 years old asian female, sweat-soaked messy black hair sticking to cheeks, severe dark circles, pale skin, bloodshot wide terrified eyes with tears, wearing wrinkled dark blue medical scrubs under an unbuttoned stained white lab coat, turning head sharply in pure terror, half-hidden behind a grey metal filing cabinet, holding onto the cabinet edge, pure darkness, pitch black environment, only lit by a single flickering flashlight from below, terrifying shadows",
      "camera_note": "[Handheld shaky cam]",
      "sfx_prompt": "heavy footsteps approaching on linoleum floor, door handle rattling, her heart pounding",
      "is_climax": False
    },
    {
      "scene_index": 4,
      "dialogue": "姐姐，你在找这个吗？",
      "english_dialogue": "Sister, are you looking for this?",
      "speaker": "林悦（克隆）",
      "emotion": "neutral",
      "visual_prompt": "Cinematic Masterpiece, Hyper-realistic, 8k resolution, Kodak 35mm film, grainy texture, high contrast low key lighting, terrifying horror movie aesthetic, highly detailed skin pores, realistic sweat drops, 1girl, identical twin of Lin Yue, pristine flawless skin, perfectly combed symmetrical black hair, dead emotionless eerie uncanny valley eyes, wearing a glowing clean white lab coat fully buttoned, standing in the doorway, holding a yellow manila folder, clinical corridor behind in pure darkness, eerie symmetry, pitch black environment, only lit by a single flickering flashlight from below, terrifying shadows",
      "camera_note": "[Slow Zoom In]",
      "sfx_prompt": "door creaking open, her footsteps stop, a moment of dead silence",
      "is_climax": False
    },
    {
      "scene_index": 5,
      "dialogue": "你...你到底是...我？",
      "english_dialogue": "Are... are you... me?",
      "speaker": "林悦",
      "emotion": "terrified",
      "visual_prompt": "Cinematic Masterpiece, Hyper-realistic, 8k resolution, Kodak 35mm film, grainy texture, high contrast low key lighting, terrifying horror movie aesthetic, highly detailed skin pores, realistic sweat drops, 1girl, Lin Yue, 25 years old asian female, sweat-soaked messy black hair sticking to cheeks, severe dark circles, pale skin, bloodshot wide terrified eyes with tears, wearing wrinkled dark blue medical scrubs under an unbuttoned stained white lab coat, backing against filing cabinet, shaking her head, tears falling, clone advancing into room, two figures facing each other in tight frame, claustrophobic, pitch black environment, only lit by a single flickering flashlight from below, terrifying shadows",
      "camera_note": "[Medium shot]",
      "sfx_prompt": "sharp intake of breath, footstep on linoleum, heartbeat thumping",
      "is_climax": False
    },
    {
      "scene_index": 6,
      "dialogue": "你只是我的备用零件。爸爸的实验...需要你。",
      "english_dialogue": "You are just my spare parts. Dad's experiment... needs you.",
      "speaker": "林悦（克隆）",
      "emotion": "cold",
      "visual_prompt": "Cinematic Masterpiece, Hyper-realistic, 8k resolution, Kodak 35mm film, grainy texture, high contrast low key lighting, terrifying horror movie aesthetic, highly detailed skin pores, realistic sweat drops, 1girl, identical twin of Lin Yue, pristine flawless skin, perfectly combed symmetrical black hair, dead emotionless eerie uncanny valley eyes, wearing a glowing clean white lab coat fully buttoned, extreme close up, stepping close, reaching hand toward camera, dead stare, sterile white tiles background in pure darkness, no shadow, pitch black environment, only lit by a single flickering flashlight from below, terrifying shadows",
      "camera_note": "[Crash Zoom on mouth]",
      "sfx_prompt": "echoing voice, a low mechanical hum rising, then a sharp cut to black",
      "is_climax": True
    }
  ],
  "next_branches": {
    "branch_a_teaser": "面对步步紧逼的克隆体，支持林悦暴起反击扣1",
    "branch_b_teaser": "面对步步紧逼的克隆体，支持林悦趁乱逃离扣2",
    "english_branch_a_teaser": "Fight back against the clone",
    "english_branch_b_teaser": "Flee from the archive room",
    "douyin_branch_a": None,
    "douyin_branch_b": None,
    "kuaishou_branch_a": None,
    "kuaishou_branch_b": None
  }
}

with get_session() as session:
    ep = session.query(Episode).filter_by(episode_number=24).first()
    if ep:
        ep.script_json = json.dumps(true_script, ensure_ascii=False)
        ep.status = EpisodeStatus.GENERATING_IMAGES
        ep.title = "双生档案"
        ep.summary = true_script["episode_summary"]
        session.commit()
        print("Successfully restored the TRUE Twin Archives script!")
    else:
        print("Error: Episode 24 not found!")
