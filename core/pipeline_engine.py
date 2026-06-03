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
from concurrent.futures import ThreadPoolExecutor, as_completed

class VideoPipelineEngine:
    def __init__(self, episode_script: dict, episode_tag: str, clip_manifest: dict = None):
        self.raw_script = episode_script
        self.episode_tag = episode_tag
        self.clip_manifest = clip_manifest or {}
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

            scene_id = scene.get("scene_index", idx + 1)
            # 动态绑定真实视频源，兼容字典回退
            video_source = self.clip_manifest.get(scene_id)
            if not video_source:
                video_source = f"./storage/temp/{self.episode_tag}/clips/scene_{scene_id:02d}.mp4"

            node = {
                "scene_id": scene_id,
                "role": scene.get("character", scene.get("speaker", "")),
                "text": scene.get("dialogue", ""),
                "emotion": scene.get("emotion", "neutral").lower(),
                "sfx": sfx_list,
                "action_timestamp": float(scene.get("action_timestamp", 0.0)),
                "video_source": video_source
            }
            schema.append(node)
            
        logger.info(f"Pipeline Engine parsed {len(schema)} scenes into standardized JSON schema.")
        return schema

    def execute_pipeline(self):
        """
        重构后的全自动流水线主入口：纯资产调度 + 移交全局渲染。
        彻底解决“音效投毒Wav2Lip”与“串行性能瓶颈”问题。
        """
        logger.info(f"=== Starting Auto-Video Pipeline for {self.episode_tag} ===")
        
        from core.tts_engine import DynamicTTSEngine
        from core.ffmpeg_compiler import compile_video
        from pathlib import Path
        import os
        
        tts = DynamicTTSEngine()
        
        # 定义资产清单 (Manifests) 传给下游工业渲染器
        clip_manifest = {}
        audio_manifest = {}
        image_manifest = {} # 如果有封面需求，把图片路径塞进这里
        
        # 1. 生成基础资产 (TTS) 并构建清单
        logger.info("--- Step 1: Preparing Assets (TTS & Paths) ---")
        
        for scene in self.pipeline_schema:
            idx = scene['scene_id']
            logger.info(f"Preparing assets for Scene {idx}...")
            
            # 1.1 生成纯净干声 (干声才能喂给 Wav2Lip，绝不能混入 SFX)
            raw_voice_path = Path(f"./storage/temp/{self.episode_tag}/audio/raw_scene_{idx:02d}.wav")
            raw_voice_path.parent.mkdir(parents=True, exist_ok=True)
            
            if scene['text']:
                try:
                    tts.generate(scene['role'], scene['emotion'], scene['text'], raw_voice_path)
                    voice_str = str(raw_voice_path)
                except Exception as e:
                    logger.error(f"[Pipeline] TTS generation failed for Scene {idx}: {e}. Falling back to empty voice.")
                    voice_str = ""
            else:
                voice_str = ""
                logger.info(f"[Pipeline] Scene {idx} has no dialogue, leaving voice path empty.")
            
            # 1.2 匹配音效路径 (请根据你原本的逻辑获取具体的音效文件路径)
            # 这里简单占位，例如：sfx_path = resolve_sfx_path(scene['sfx'])
            sfx_path = scene['sfx'] 
            
            # 1.3 修复致命硬编码：确保 video_source 是从实际的 clips 目录读取，而不是 images
            # 你的 video_gen.py 生成在 clips 文件夹，这里必须对应
            correct_video_source = str(Path(f"./storage/temp/{self.episode_tag}/clips/scene_{idx:02d}.mp4"))
            
            # 1.4 写入数据清单
            clip_manifest[idx] = correct_video_source
            audio_manifest[idx] = {
                "voice": voice_str,  # 只有纯净人声会被送到 Wav2Lip
                "sfx": sfx_path      # 音效会在后期 ffmpeg_compiler 里被安全混合
            }
        
        # 2. 移交工业级渲染器 (统一合并、打口型、侧链压缩混音)
        logger.info("--- Step 2: Handing over to Industrial FFmpeg Compiler ---")
        
        try:
            # 组装分支数据 (如果有)
            next_branches = {
                "branch_a_teaser": self.raw_script.get("branch_a", ""),
                "branch_b_teaser": self.raw_script.get("branch_b", "")
            }
            
            # 直接调用你写好的神器
            final_video_paths = compile_video(
                scenes=self.raw_script.get("scenes", []),
                clip_manifest=clip_manifest,
                audio_manifest=audio_manifest,
                image_manifest=image_manifest,
                episode_tag=self.episode_tag,
                theme_key="hospital_horror",  # 或者根据剧本动态读取
                render_mode="all",            # 抖音/快手/全球三轨齐发
                next_branches=next_branches,
                banner_text=self.raw_script.get("title", "")
            )
            logger.success(f"=== Pipeline COMPLETED! Outputs: {final_video_paths} ===")
            return list(final_video_paths.values())
            
        except Exception as e:
            logger.error(f"FFmpeg Compilation failed: {e}")
            raise
