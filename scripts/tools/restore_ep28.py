import json
from pathlib import Path
import sys

_PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJECT_ROOT))

from database.db_session import get_session
from database.models import Episode, EpisodeStatus

script_json = """{
  "episode_title": "克隆觉醒",
  "episode_summary": "林悦在储物间深处发现了自己的克隆体培养舱，克隆体突然睁眼！面对与自己一模一样的生命，林悦必须做出选择：是理性摧毁还是保留人性？",
  "cover_teaser": "点击揭开真相！",
  "chosen_branch": "A",
  "scenes": [
    {
      "scene_index": 1,
      "dialogue": "",
      "english_dialogue": "",
      "speaker": "",
      "emotion": "shocked",
      "visual_prompt": "1girl, Lin Yue, 25 years old asian female, short black bob hair, dark circles under eyes, pale skin, wearing a stained white lab coat over a dark grey turtleneck, highly detailed face, consistent character, extreme close-up of her face with flashlight illuminating her wide eyes, mouth open in horror, reflection of a glowing green liquid in her pupils, pitch black background, clinical horror aesthetic",
      "camera_note": "[Extreme Close-up] [Shaky]",
      "sfx_prompt": "heavy breathing, heartbeat thumping, distant machine hum",
      "is_climax": false
    },
    {
      "scene_index": 2,
      "dialogue": "这...这是什么...",
      "english_dialogue": "What... what is this...",
      "speaker": "林悦",
      "emotion": "fearful",
      "visual_prompt": "1girl, Lin Yue, 25 years old asian female, short black bob hair, dark circles under eyes, pale skin, wearing a stained white lab coat over a dark grey turtleneck, highly detailed face, consistent character, standing in a cramped storage room, holding a weak flashlight pointed at a large glass cylinder filled with green liquid, a human silhouette floating inside, dim green glow, shadows on walls, claustrophobic, horror atmosphere",
      "camera_note": "[Medium shot] [Slow zoom out]",
      "sfx_prompt": "electric buzz, liquid bubbling",
      "is_climax": false
    },
    {
      "scene_index": 3,
      "dialogue": "不...不可能...是我？！",
      "english_dialogue": "No... impossible... it's me?!",
      "speaker": "林悦",
      "emotion": "shocked",
      "visual_prompt": "1girl, Lin Yue, 25 years old asian female, short black bob hair, dark circles under eyes, pale skin, wearing a stained white lab coat over a dark grey turtleneck, highly detailed face, consistent character, flashlight beam on cylinder, revealing a floating clone of herself with closed eyes, same hair and lab coat, green liquid, tubes connected, low angle shot, clinical liminal space",
      "camera_note": "[Low angle] [Fast zoom in on clone's face]",
      "sfx_prompt": "mechanical hiss, water bubble pop",
      "is_climax": true
    },
    {
      "scene_index": 4,
      "dialogue": "林悦...林悦...",
      "english_dialogue": "Lin Yue... Lin Yue...",
      "speaker": "林悦（克隆）",
      "emotion": "cold",
      "visual_prompt": "1girl, female clone of Lin Yue, 25 years old asian female, short black bob hair, pale wet skin, wearing a white lab coat soaked in green liquid, eyes suddenly snap open fully, glowing green pupils, staring directly at the camera, floating in cylinder, tubes attached, ambient green light, eerie stillness",
      "camera_note": "[From clone's POV] [Fast zoom in on her eyes]",
      "sfx_prompt": "glass cracking, heartbeat spike",
      "is_climax": false
    },
    {
      "scene_index": 5,
      "dialogue": "你...你是谁？！",
      "english_dialogue": "Who... who are you?!",
      "speaker": "林悦",
      "emotion": "terrified",
      "visual_prompt": "1girl, Lin Yue, 25 years old asian female, short black bob hair, dark circles under eyes, pale skin, wearing a stained white lab coat over a dark grey turtleneck, highly detailed face, consistent character, stumbling backward, flashlight dropping, hand covering mouth, wide-eyed terror, cylinder behind her in background, broken glass shards on floor",
      "camera_note": "[Dutch angle] [Crash zoom out]",
      "sfx_prompt": "glass shatter, scream, thud",
      "is_climax": false
    },
    {
      "scene_index": 6,
      "dialogue": "我是你...也是我...放我出去...",
      "english_dialogue": "I am you... and also me... let me out...",
      "speaker": "林悦（克隆）",
      "emotion": "cold",
      "visual_prompt": "1girl, female clone of Lin Yue, 25 years old asian female, short black bob hair, pale wet skin, green glowing eyes, hands pressing against the inside of the glass, mouth moving slowly, liquid swirling, green backlight, claustrophobic close-up",
      "camera_note": "[Close-up on clone's hands] [Tilt up]",
      "sfx_prompt": "distant echoey voice, low rumble",
      "is_climax": false
    },
    {
      "scene_index": 7,
      "dialogue": "我必须...关掉它...对不起...",
      "english_dialogue": "I have to... shut it down... I'm sorry...",
      "speaker": "林悦",
      "emotion": "determined",
      "visual_prompt": "1girl, Lin Yue, 25 years old asian female, short black bob hair, dark circles under eyes, pale skin, wearing a stained white lab coat over a dark grey turtleneck, highly detailed face, consistent character, reaching for a large red emergency shutoff switch on the wall, side lighting, dramatic shadows, expression of painful resolve",
      "camera_note": "[Over-the-shoulder] [Push in on hand]",
      "sfx_prompt": "button click, machinery powering down, alarm beep",
      "is_climax": false
    }
  ],
  "next_branches": {
    "branch_a_teaser": "按下开关，终结一切扣1",
    "branch_b_teaser": "放下手，赌一把人性扣2",
    "english_branch_a_teaser": "Pull the plug and destroy the clone press 1",
    "english_branch_b_teaser": "Talk to the clone and try to redeem her press 2",
    "douyin_branch_a": "直接拔电源，摧毁克隆体扣1",
    "douyin_branch_b": "和克隆体对话，试图感化扣2",
    "kuaishou_branch_a": "想起战友，决定拯救克隆体扣1",
    "kuaishou_branch_b": "独自逃命，让克隆体自生自灭扣2"
  }
}"""

with get_session() as session:
    # 修复刚刚错误生成或改动的状态，并将最新的28集强行植入，以便断点续传！
    session.query(Episode).filter(Episode.episode_number >= 25).delete()
    
    ep = Episode(
        season_id=1,
        episode_number=28,
        theme_key="hospital_horror",
        title="克隆觉醒",
        status=EpisodeStatus.GENERATING_ASSETS,
        chosen_branch="A",
        script_json=script_json
    )
    session.add(ep)
    session.commit()
    print("Successfully restored Episode 28 to database!")
