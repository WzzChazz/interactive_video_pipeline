"""
core/pipeline_engine.py — 全自动视听合成流水线的主控引擎 (JSON 适配器)

职责：
1. 从数据库中抽取大模型生成的复杂剧本 JSON
2. 将其转化为极简、高内聚的流水线标准结构 (List[dict])
3. 负责向 TTS、SFX、LipSync 和 FFmpeg 渲染器分发任务
"""

import json
from loguru import logger
from typing import List, Dict

class VideoPipelineEngine:
    def __init__(self, episode_script: dict, episode_tag: str):
        self.raw_script = episode_script
        self.episode_tag = episode_tag
        self.pipeline_schema = self._parse_to_schema()

    def _parse_to_schema(self) -> List[Dict]:
        """将复杂的剧情剧本解析为严谨的流水线 JSON 结构"""
        schema = []
        scenes = self.raw_script.get("scenes", [])
        
        for idx, scene in enumerate(scenes):
            # 处理音效：提取为列表
            sfx_raw = scene.get("sfx_prompt", "")
            sfx_list = []
            if sfx_raw and sfx_raw.lower() not in ("ambient silence", "none", ""):
                # 简单的下划线格式化，提取前三个关键音效
                sfx_list = [p.strip().replace(" ", "_").lower() for p in sfx_raw.split(",")]
                sfx_list = [s for s in sfx_list if s][:3]

            node = {
                "scene_id": scene.get("scene_index", idx + 1),
                "role": scene.get("character", scene.get("speaker", "")),
                "text": scene.get("dialogue", ""),
                "emotion": scene.get("emotion", "neutral").lower(),
                "sfx": sfx_list,
                "action_timestamp": float(scene.get("action_timestamp", 0.0)),
                # 预设视频原素材的相对路径
                "video_source": f"./storage/temp/{self.episode_tag}/images/scene_{scene.get('scene_index', idx+1):02d}.mp4"
            }
            schema.append(node)
            
        logger.info(f"Pipeline Engine parsed {len(schema)} scenes into standardized JSON schema.")
        return schema

    def execute_pipeline(self):
        """执行全自动流水线的主入口"""
        logger.info(f"=== Starting Auto-Video Pipeline for {self.episode_tag} ===")
        
        # 延迟导入以防止循环依赖
        from core.tts_engine import DynamicTTSEngine
        from core.sfx_mixer import SFXMixer
        from core.lip_sync_engine import LipSyncEngine
        from core.ffmpeg_renderer import FFmpegRenderer
        from pathlib import Path
        
        tts = DynamicTTSEngine()
        mixer = SFXMixer()
        lipsync = LipSyncEngine()
        renderer = FFmpegRenderer(work_dir=f"./storage/temp/{self.episode_tag}/render")
        
        final_video_paths = []
        
        for scene in self.pipeline_schema:
            idx = scene['scene_id']
            logger.info(f"--- Processing Scene {idx} ---")
            
            # 1. 动态生成情感 TTS
            raw_voice_path = Path(f"./storage/temp/{self.episode_tag}/audio/raw_scene_{idx:02d}.wav")
            if scene['text']:
                tts.generate(scene['role'], scene['emotion'], scene['text'], raw_voice_path)
            else:
                raw_voice_path = None
                logger.info(f"[Pipeline] Scene {idx} is a Reaction Shot (No dialogue). Skipping TTS.")
            
            # 2. 混合音频与音效 (精确锚点打点)
            mixed_audio_path = Path(f"./storage/temp/{self.episode_tag}/audio/mixed_scene_{idx:02d}.aac")
            mixer.mix_scene_audio(
                voice_path=str(raw_voice_path) if raw_voice_path else None,
                sfx_names=scene['sfx'],
                action_timestamp=scene['action_timestamp'],
                output_path=str(mixed_audio_path)
            )
            
            # 3. 本地面部驱动与高清修复
            lipsync_video_path = Path(f"./storage/temp/{self.episode_tag}/video/lipsync_scene_{idx:02d}.mp4")
            lipsync_video_path.parent.mkdir(parents=True, exist_ok=True)
            
            if scene['text']:
                lipsync.generate_talking_head(
                    video_source=scene['video_source'],
                    audio_source=str(mixed_audio_path),
                    final_out=str(lipsync_video_path)
                )
                bypassed_video = str(lipsync_video_path)
            else:
                logger.info(f"[Pipeline] Scene {idx} bypassing LipSync to preserve raw visual terror.")
                bypassed_video = scene['video_source']
            
            # 4. 压制最终成片 (包含冻结帧与字幕硬编码)
            final_scene_path = Path(f"./storage/temp/{self.episode_tag}/render/final_scene_{idx:02d}.mp4")
            renderer.render_scene(
                video_path=str(bypassed_video),
                mixed_audio_path=str(mixed_audio_path),
                dialogue_text=scene['text'],
                output_path=str(final_scene_path)
            )
            
            final_video_paths.append(str(final_scene_path))
            logger.success(f"Scene {idx} completed successfully!")
            
        logger.success(f"=== Pipeline completed for all {len(final_video_paths)} scenes! ===")
        return final_video_paths
