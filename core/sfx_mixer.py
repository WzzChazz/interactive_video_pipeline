"""
core/sfx_mixer.py — 自动化 Foley 音效与混音系统 (Automated SFX & Mixer)

职责：
1. 从 sfx_library 读取音效，若无则尝试 fallback 生成
2. 强制将所有输入音频重采样至 44100Hz (防变调)
3. 按照 action_timestamp 进行精确的 Foley 打点 (Audio Ducking/Positioning)
4. 多轨合流 (Voice + Foley + Ambient) 导出最终 aac
"""

import subprocess
from pathlib import Path
from loguru import logger
import os

class AudioMixerError(Exception):
    pass

class SFXMixer:
    def __init__(self, sfx_library_dir: str = "./sfx_library"):
        self.sfx_lib = Path(sfx_library_dir)
        self.sfx_lib.mkdir(parents=True, exist_ok=True)
        
    def _find_sfx(self, sfx_name: str) -> Path:
        """从本地库匹配音效，暂时找不到返回None（未来可接API动态生成）"""
        target = self.sfx_lib / f"{sfx_name}.wav"
        if target.exists():
            return target
        target_mp3 = self.sfx_lib / f"{sfx_name}.mp3"
        if target_mp3.exists():
            return target_mp3
        return None

    def mix_scene_audio(self, 
                        voice_path: str, 
                        sfx_names: list, 
                        action_timestamp: float, 
                        output_path: str):
        """
        核心混音引擎：利用 FFmpeg filter_complex 实现：
        1. 强制重采样到 44100
        2. 人声：音量 +2dB
        3. 环境音(Ambient)：循环播放，-15dB
        4. 动作音效(Foley)：精确延迟 (adelay) 到 action_timestamp
        """
        voice_path = Path(voice_path)
        output_path = Path(output_path)
        
        if not voice_path.exists():
            raise AudioMixerError(f"Voice file missing: {voice_path}")
            
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        inputs = ["-i", str(voice_path)]
        filter_complex = []
        mix_inputs = []
        
        # 1. 处理人声轨 (强制重采样 + 提音量)
        filter_complex.append(f"[0:a]aresample=44100,volume=1.2[v_out];")
        mix_inputs.append("[v_out]")
        
        input_idx = 1
        
        # 2. 解析音效轨
        for sfx in sfx_names:
            sfx_path = self._find_sfx(sfx)
            if not sfx_path:
                logger.warning(f"SFX '{sfx}' not found in library, skipping.")
                continue
                
            inputs.extend(["-i", str(sfx_path)])
            
            # 判断是环境音还是 Foley 动作音
            is_ambient = "ambient" in sfx.lower() or "buzz" in sfx.lower()
            
            if is_ambient:
                # 环境音：循环播放，降低音量 -15dB
                # aresample 防止采样率冲突，aloop 无限循环
                fc = f"[{input_idx}:a]aresample=44100,aloop=loop=-1:size=2e9,volume=0.18[sfx{input_idx}];"
            else:
                # 动作音：根据 action_timestamp 打点延迟
                delay_ms = int(action_timestamp * 1000)
                fc = f"[{input_idx}:a]aresample=44100,adelay={delay_ms}|{delay_ms},volume=0.8[sfx{input_idx}];"
                
            filter_complex.append(fc)
            mix_inputs.append(f"[sfx{input_idx}]")
            input_idx += 1
            
        # 3. 合并所有轨
        num_inputs = len(mix_inputs)
        mix_input_str = "".join(mix_inputs)
        
        if num_inputs > 1:
            # 使用 amix 混合，dropout_transition=0 防止人声结束后音量骤降
            filter_complex.append(f"{mix_input_str}amix=inputs={num_inputs}:duration=longest:dropout_transition=0:normalize=0[final_mix]")
            map_arg = "-map '[final_mix]'"
        else:
            # 只有人声
            map_arg = "-map '[v_out]'"
            
        filter_str = "".join(filter_complex).strip(";")
        
        cmd = [
            "ffmpeg", "-y",
        ] + inputs + [
            "-filter_complex", filter_str,
            "-map", "[final_mix]" if num_inputs > 1 else "[v_out]",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            str(output_path)
        ]
        
        logger.debug(f"[Mixer] Running FFmpeg for {output_path.name}")
        
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            logger.info(f"Successfully mixed audio to {output_path}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Mixer FFmpeg failed: {e.stderr}")
            raise AudioMixerError(f"Mixer failed: {e.stderr}")
            
        return output_path
