"""
config/themes.py
================
多题材矩阵的中心配置文件。定义了不同“宇宙”的世界观、主角、视觉/听觉风格及系统提示词锚点。
"""

THEMES = {
    "hospital_horror": {
        "name": "储物间的秘密 (Hospital Horror)",
        "genre": "Chinese Psychological Suspense / Clinical Liminal Space",
        "background": "The protagonist, Lin Yue (林悦), is subtly implied to be a young medical intern. The overarching mystery revolves around the 'Secrets of the Storage Room' (储物间的秘密) - a forbidden archive room holding the dark truth of the hospital. The terrifying hospital rules and white coats are metaphors for academic pressure, toxic workplace gaslighting, and the oppressive gaze of authority.",
        "negative_prompt": "DO NOT INCLUDE ANY Sci-Fi, Cyberpunk, AI, Data Cores, or Robots. This must be a pure, grounded psychological medical horror. No sci-fi jargon.",
        "visual_style": "Clinical Melancholy psychological horror aesthetic. The lighting and environment MUST dynamically match the scene's plot (e.g., use 'pitch black midnight, heavy claustrophobic shadows, illuminated only by a weak flashlight' for terrifying basement scenes, or 'cold, harsh fluorescent lights, sterile environment' for daytime lab scenes). Eerie emptiness, cinematic 35mm lens.",
        "character_prompt_lock": "1girl, Lin Yue, 25 years old asian female, short black bob hair, dark circles under eyes, pale skin, wearing a stained white lab coat over a dark grey turtleneck, highly detailed face, consistent character",
        "sfx_style": "layered ambient sounds, eerie clinical silence, fluorescent humming. NO human voices (NO 'screaming', 'talking').",
        "compliance": "- NO gore, NO blood, NO corpses, NO extreme violence, NO sexual content, NO ghosts/demons, NO superstition.\n- All horror must be psychological. Use 'Clinical Emptiness' and 'Claustrophobia'.",
        "voice_id": "cosyvoice-v3.5-plus-vd-bailian-f0f1b1bb3679400486ad031fc8bd2bed",
        "voice_map": {
            "旁白": "longlaotie",     # 磁性深沉老爷爷
            "系统": "longxiaoxia",    # 冰冷无情御姐音
            "李医生": "longcheng",    # 成熟稳重男医生
            "护士": "longxiaochun",   # 年轻女护士
            "院长": "longjue",        # 极具压迫感的反派男声
        },
        "audio_reverb_filter": "",  # 移除回声滤镜，保持语音清晰
        "collection_name": "储物间的秘密"
    },
    "deep_sea_survival": {
        "name": "极地深渊 (Deep Sea Survival)",
        "genre": "Hardcore Deep Sea Survival / Submarine Escape Room",
        "background": "The protagonist, Lei Nuo (雷诺), is a MALE deep-sea engineer trapped in a failing research submarine at the bottom of the Mariana Trench. The overarching threat is extreme water pressure, failing oxygen systems, and mysterious structural damage caused by 'something' outside the hull. The horror comes from isolation, claustrophobia, and the ticking clock of suffocation.",
        "negative_prompt": "DO NOT INCLUDE ghosts, demons, magic, or supernatural elements. The horror MUST be grounded in physics: crushing water pressure, freezing cold, oxygen deprivation, and mechanical failure.",
        "visual_style": "Deep Sea Claustrophobia aesthetic. Dark, cramped industrial submarine interiors, flashing red emergency lights, condensation on freezing metal, thick shadows. Example: 'cramped submarine corridor, flashing red emergency lights, sparks falling from broken pipes, dark shadows, claustrophobic cinematic 35mm lens'",
        "character_prompt_lock": "1boy, Lei Nuo, 30 years old asian male, messy wet black hair, stubble, intense fearful eyes, sweat on face, wearing a heavy dark blue industrial diving suit with metallic yellow stripes, highly detailed face, consistent character",
        "sfx_style": "heavy metallic groaning, deep underwater rumbling, hissing steam, harsh sonar pings, muffled echoing. NO human voices.",
        "compliance": "- NO gore, NO blood, NO corpses, NO ghosts/demons.\n- All fear must stem from the unforgiving environment (pressure, cold, dark) and mechanical failure.",
        "voice_id": "longshuo",
        "voice_map": {
            "旁白": "longlaotie",
            "系统": "longxiaoxia",
            "指挥中心": "longjue",
            "AI助手": "longwan"
        },
        "audio_reverb_filter": "aecho=0.8:0.88:15:0.5",  # 狭小金属舱共鸣闷响
        "collection_name": "极地深渊"
    },
    "capybara_healing": {
        "name": "水豚的治愈日常 (Capybara Healing)",
        "genre": "Cozy Healing / Slice-of-Life / Wholesome ASMR-style short loop",
        # 反差萌人设：高冷美女 × 佛系沙雕水豚。差异化卡位（猫已红海，水豚蓝海+自带喜感）
        "background": "A cool, elegant young woman named Lin Xi (林溪) lives a slow, cozy life with her zen, perpetually unbothered pet capybara 'Tuan Tuan' (团团). There is NO plot, NO conflict, NO danger — only warm, comforting, slightly funny everyday healing moments (morning coffee by a sunny window, a lazy afternoon nap, sharing snacks, a rainy day indoors). The charm comes from the 反差 (contrast): her cold elegant beauty vs. the capybara's derpy佛系 deadpan calm. Pure 治愈 (healing) + comfort + gentle humor.",
        "negative_prompt": "ABSOLUTELY NO horror, NO darkness, NO fear, NO tension, NO blood, NO sci-fi, NO sadness. NO suggestive or sexual posing. Keep everything warm, soft, cute, wholesome, and slow.",
        "visual_style": "Cozy healing (治愈系) ANIME ILLUSTRATION aesthetic — soft Japanese anime art style, gentle cel shading, warm pastel cream tones, soft golden glow, kawaii and wholesome. Settings: sunny windowsill, cozy cafe, blanket nest, autumn park, lakeside, warm hot spring. Everything soft, warm, slow and comforting. NO harsh shadows, NO dark scenes, NO photorealism (keep it illustrated anime style).",
        "character_prompt_lock": "soft Japanese anime illustration style, cel shading, cozy healing anime art, featuring two recurring characters — Lin Xi (a 20 years old youthful cute pretty asian girl, soft long wavy black hair, big bright lively sparkling eyes, sweet playful cheerful smile, fair clear skin, cozy cream HIGH-NECK knit sweater, modest fully covered wholesome outfit) and her pet Tuan Tuan (an EXTRA cute round chubby fluffy capybara with big adorable round eyes and a soft derpy cute face), kawaii, warm cozy healing aesthetic, characters naturally placed within the spacious scene (NOT always crammed close together), consistent character design",
        "sfx_style": "gentle healing ambience: soft warm lo-fi / piano music, birdsong, light rain, water trickling, pages turning, a teacup clinking, a soft breeze. Cozy, warm, ASMR-like. NO scary sounds.",
        "compliance": "- Wholesome, all-ages healing content ONLY. NO sexual or suggestive content, NO 擦边. Keep the female character elegant, tasteful, fully and modestly dressed. NO violence, NO fear.",
        # 新格式标志位：画外音旁白 + BGM + 字幕；不对口型、不连载、不投票
        "narration_mode": "voiceover_offscreen",  # 画外音旁白叙事，人物不正脸开口
        "use_tts": True,                     # 保留配音，但仅作画外音旁白（非角色对口型）
        "needs_lipsync": False,              # 唯一硬性禁用：不做对口型（杀恐怖谷嘴型）
        "is_serial": False,                  # 单条自包含，非连载
        "subtitle_on": True,                 # 配音 + 字幕双保险（静音党也能看）
        "voice_id": "longxiaochun",          # 默认/旁白：温柔年轻女声
        "voice_map": {
            "旁白": "longxiaochun",   # 温柔年轻女声旁白
            "林溪": "longxiaochun",   # 年轻活泼少女 → 清甜年轻女声
            "团团": "longlaotie",     # 蠢萌水豚 → 深沉老爷爷音（反差萌笑点核弹，保留）
        },
        "audio_reverb_filter": "",
        "collection_name": "水豚的治愈日常"
    }
}
