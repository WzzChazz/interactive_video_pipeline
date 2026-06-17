"""
core/pipeline_engine.py — 全自动视听合成流水线的主控引擎 (JSON 适配器)

重构记录 (配音问题深度分析 2026-06-11):
  P0-1: 消除 pipeline_engine 与 audio_gen 的双轨竞争
         统一使用 audio_gen.generate_audio() 作为唯一 TTS 入口
         消除 raw_scene_XX.wav 与 scene_XX_voice.mp3 的重复生成

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
            sfx_raw = scene.get("sfx_prompt", "")
            sfx_list = []
            if sfx_raw and sfx_raw.lower() not in ("ambient silence", "none", ""):
                sfx_list = [p.strip().replace(" ", "_").lower() for p in sfx_raw.split(",")]
                sfx_list = [s for s in sfx_list if s][:3]

            scene_id = scene.get("scene_index", idx + 1)
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
        全自动流水线主入口。

        P0-1 修复：统一使用 audio_gen.generate_audio() 作为 TTS/SFX 的唯一入口。
        原来 pipeline_engine 自己调用 DynamicTTSEngine 生成 raw_scene_XX.wav，
        同时 audio_gen 也在生成 scene_XX_voice.mp3，造成双倍 API 消耗。
        现在：pipeline_engine 直接调用 generate_audio()，
        generate_audio() 内部会自动路由到 DashScope（克隆/恐惧情感）或 edge-tts（其他）。
        """
        logger.info(f"=== Starting Auto-Video Pipeline for {self.episode_tag} ===")
        
        from core.audio_gen import generate_audio
        from core.ffmpeg_compiler import compile_video
        from pathlib import Path

        # ── Step 1: 统一生成所有配音 + 音效（单一权威入口）─────────────────
        logger.info("--- Step 1: Generating Audio via unified audio_gen (eliminating dual-track) ---")
        
        audio_manifest: dict = generate_audio(
            scenes=self.raw_script.get("scenes", []),
            episode_tag=self.episode_tag,
            theme_key="hospital_horror",
        )

        logger.info(
            f"Audio generation complete. "
            f"Voice files: {sum(1 for v in audio_manifest.values() if v.get('voice'))}, "
            f"SFX files: {sum(1 for v in audio_manifest.values() if v.get('sfx'))}"
        )

        # ── Step 2: 构建视频资产清单 ─────────────────────────────────────────
        clip_manifest: dict = {}
        image_manifest: dict = {}

        for scene in self.pipeline_schema:
            idx = scene['scene_id']
            # 优先使用外部传入的 clip_manifest（如 Kling 生成的视频路径）
            if idx in self.clip_manifest:
                clip_manifest[idx] = self.clip_manifest[idx]
            else:
                clip_manifest[idx] = str(
                    Path(f"./storage/temp/{self.episode_tag}/clips/scene_{idx:02d}.mp4")
                )

        # ── Step 3: 移交工业级渲染器 ─────────────────────────────────────────
        logger.info("--- Step 2: Handing over to Industrial FFmpeg Compiler ---")
        
        try:
            next_branches = {
                "branch_a_teaser": self.raw_script.get("branch_a", ""),
                "branch_b_teaser": self.raw_script.get("branch_b", "")
            }
            
            final_video_paths = compile_video(
                scenes=self.raw_script.get("scenes", []),
                clip_manifest=clip_manifest,
                audio_manifest=audio_manifest,
                image_manifest=image_manifest,
                episode_tag=self.episode_tag,
                theme_key="hospital_horror",
                render_mode="all",
                next_branches=next_branches,
                banner_text=self.raw_script.get("title", "")
            )
            logger.success(f"=== Pipeline COMPLETED! Outputs: {final_video_paths} ===")
            return list(final_video_paths.values())
            
        except Exception as e:
            logger.error(f"FFmpeg Compilation failed: {e}")
            raise
