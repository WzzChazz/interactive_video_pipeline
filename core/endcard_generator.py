import math
import subprocess
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from loguru import logger

from config.settings import VIDEO_WIDTH, VIDEO_HEIGHT, SUBTITLE_FONT_PATH

def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """按像素宽度对中文字符串进行自动换行"""
    lines = []
    for p_line in text.split('\n'):
        words = list(p_line)
        current_line = ""
        for word in words:
            test_line = current_line + word
            length = font.getlength(test_line) if hasattr(font, 'getlength') else font.getbbox(test_line)[2]
            if length <= max_width:
                current_line = test_line
            else:
                lines.append(current_line)
                current_line = word
        if current_line:
            lines.append(current_line)
    return lines

def generate_typewriter_endcard(
    branch_a: str,
    branch_b: str,
    output_path: str | Path,
    fps: int = 30,
    duration_sec: float = 6.0,
    chars_per_sec: float = 12.0,
    background_image_path: str | None = None
) -> Path:
    
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    font_size = 40
    font_path = "/System/Library/Fonts/Supplemental/Songti.ttc"
    try:
        font = ImageFont.truetype(font_path, font_size)
        title_font = ImageFont.truetype(font_path, 60)
    except IOError:
        logger.warning(f"Failed to load font from {font_path}. Using default.")
        font = ImageFont.load_default()
        title_font = font

    margin_x = 80
    max_text_width = VIDEO_WIDTH - margin_x * 2
    title = "做出你的选择："
    
    lines_a = wrap_text(branch_a, font, max_text_width)
    lines_b = wrap_text(branch_b, font, max_text_width)
    
    chars_to_draw = []
    current_y = 300
    
    title_x = (VIDEO_WIDTH - title_font.getlength(title)) / 2 if hasattr(title_font, 'getlength') else margin_x
    cur_x = title_x
    for ch in title:
        chars_to_draw.append((ch, cur_x, current_y, "white", title_font))
        cur_x += title_font.getlength(ch) if hasattr(title_font, 'getlength') else 40
        
    current_y += 120
    for line in lines_a:
        cur_x = margin_x
        for ch in line:
            chars_to_draw.append((ch, cur_x, current_y, "#FFAAAA", font))
            cur_x += font.getlength(ch) if hasattr(font, 'getlength') else 20
        current_y += font_size + 15
        
    current_y += 60
    for line in lines_b:
        cur_x = margin_x
        for ch in line:
            chars_to_draw.append((ch, cur_x, current_y, "#AAAAFF", font))
            cur_x += font.getlength(ch) if hasattr(font, 'getlength') else 20
        current_y += font_size + 15
        
    total_chars = len(chars_to_draw)
    
    # ================= 【核心修复 1：动态时长计算】 =================
    # 强制保证视频时长足以打完所有字，并且结尾留有 2 秒的停顿时间
    required_sec = (total_chars / chars_per_sec) + 2.0
    if duration_sec < required_sec:
        logger.info(f"[Endcard] 时长过短，自动延长至 {required_sec:.1f} 秒以完整展示文字。")
        duration_sec = required_sec
        
    total_frames = int(duration_sec * fps)
    
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_vid:
        tmp_vid_path = tmp_vid.name
        
    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-s", f"{VIDEO_WIDTH}x{VIDEO_HEIGHT}",
        "-pix_fmt", "rgb24",
        "-framerate", str(fps), 
        "-i", "-", 
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        tmp_vid_path
    ]
    
    logger.info(f"Starting typewriter video generation... background_image_path={background_image_path}")
    process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    
    from PIL import ImageEnhance
    
    # ================= 【核心修复：背景图加载与亮度控制】 =================
    if background_image_path:
        bg_path = Path(background_image_path)
        if bg_path.exists():
            try:
                raw_bg = Image.open(bg_path).convert('RGB')
                raw_bg = raw_bg.resize((VIDEO_WIDTH, VIDEO_HEIGHT))
                enhancer = ImageEnhance.Brightness(raw_bg)
                # 【修改点】：将 0.2 改为 0.5。原视频如果是暗调，0.5 刚好能隐约看到人脸，不会死黑
                base_img = enhancer.enhance(0.5) 
                logger.info(f"[Endcard] 成功加载并压暗了背景图片: {bg_path}")
            except Exception as e:
                logger.error(f"[Endcard] 读取背景图失败: {e}。退化为纯黑背景。")
                base_img = Image.new('RGB', (VIDEO_WIDTH, VIDEO_HEIGHT), color=(10, 0, 0))
        else:
            # 【增加强力警告】：如果路径传了但找不到文件，立刻在终端标红报错！
            logger.error(f"[Endcard] 找不到背景图路径: {bg_path} ！！请检查流水线上一道工序是否正确提取了最后一帧。")
            base_img = Image.new('RGB', (VIDEO_WIDTH, VIDEO_HEIGHT), color=(10, 0, 0))
    else:
        logger.warning("[Endcard] 未传入 background_image_path 参数，将使用默认纯黑背景。")
        base_img = Image.new('RGB', (VIDEO_WIDTH, VIDEO_HEIGHT), color=(10, 0, 0))
    # =================================================================
    
    typing_end_frame = 0
    
    try:
        for f in range(total_frames):
            time_sec = f / fps
            chars_visible = int(time_sec * chars_per_sec)
            
            if chars_visible >= total_chars:
                chars_visible = total_chars
                if typing_end_frame == 0:
                    typing_end_frame = f
                
            pulse = math.sin(time_sec * 4) 
            bg_r = int(25 + 15 * pulse)
            frame_img = base_img.copy()
            tint = Image.new('RGB', (VIDEO_WIDTH, VIDEO_HEIGHT), color=(bg_r, 0, 0))
            frame_img = Image.blend(frame_img, tint, 0.3)
            f_draw = ImageDraw.Draw(frame_img)
            
            jitter_x, jitter_y = 0, 0
            if chars_visible == total_chars and (f % 4 == 0):
                jitter_x = int(math.sin(f) * 2)
                jitter_y = int(math.cos(f) * 2)
            
            for i in range(chars_visible):
                ch, x, y, color, c_font = chars_to_draw[i]
                if c_font == title_font:
                    color = "#FF0000" if (f % 30 < 25) else "#990000"
                f_draw.text((x + jitter_x, y + jitter_y), ch, font=c_font, fill=color)
                
            if chars_visible < total_chars:
                if (f // 5) % 2 == 0: 
                    last_ch = chars_to_draw[chars_visible] if chars_visible < total_chars else chars_to_draw[-1]
                    cursor_x, cursor_y = last_ch[1], last_ch[2]
                    f_draw.rectangle([cursor_x, cursor_y + 5, cursor_x + 20, cursor_y + 40], fill="#FF0000")
            
            process.stdin.write(frame_img.tobytes())
            
        process.stdin.close()
        process.wait()
    except Exception as e:
        logger.error(f"Failed to stream to FFmpeg: {e}")
        process.kill()
        raise
        
    if process.returncode != 0:
        raise RuntimeError("FFmpeg rawvideo failed")

    # ================= 【核心修复 2：48kHz立体声音频重构】 =================
    import numpy as np
    from scipy.io import wavfile
    
    # 强制使用视频工业标准：48000Hz 采样率 + 双声道 (避免拼接时 FFmpeg 时钟错乱)
    sample_rate = 48000
    total_samples = int(duration_sec * sample_rate)
    audio = np.zeros((total_samples, 2), dtype=np.float32) # [samples, channels]
    
    for i in range(1, total_chars + 1):
        char_time = i / chars_per_sec
        if char_time >= duration_sec:
            break
            
        sample_idx = int(char_time * sample_rate)
        click_length = int(0.015 * sample_rate) # 15ms 极度清脆
        
        if sample_idx + click_length <= total_samples:
            t_click = np.linspace(0, 0.015, click_length, False)
            
            # 高频机械咔哒声
            noise = np.random.uniform(-1.0, 1.0, click_length)
            env_noise = np.exp(-t_click * 500)
            
            # 加入带有轻微随机波动的清脆按键音（青轴感）
            freq = np.random.uniform(2800, 3600)
            tone = np.sin(2 * np.pi * freq * t_click)
            env_tone = np.exp(-t_click * 200)
            
            click_mono = (noise * env_noise * 0.7) + (tone * env_tone * 0.35)
            
            # 写入双声道
            audio[sample_idx:sample_idx+click_length, 0] += click_mono
            audio[sample_idx:sample_idx+click_length, 1] += click_mono

    # 营造封闭压抑感：底层低频 Drone 嗡嗡声
    t_array = np.linspace(0, duration_sec, total_samples, False)
    drone_mono = 0.2 * np.sin(2 * np.pi * 40 * t_array) + 0.1 * np.sin(2 * np.pi * 55 * t_array)
    audio[:, 0] += drone_mono
    audio[:, 1] += drone_mono
    
    # 电影级结尾低频轰鸣 (Sub-bass Drop)：在字打完的瞬间爆炸
    thud_time = total_chars / chars_per_sec
    thud_idx = int(thud_time * sample_rate)
    thud_length = int(3.5 * sample_rate) # 持续 3.5 秒的余音
    
    if thud_idx < total_samples:
        if thud_idx + thud_length > total_samples:
            thud_length = total_samples - thud_idx
            
        t_thud = np.linspace(0, 3.5, thud_length, False)
        
        # 让频率从 90Hz 极速下潜坠落至 20Hz（制造失重感）
        freqs = np.linspace(90, 20, thud_length)
        phase = 2 * np.pi * np.cumsum(freqs) / sample_rate
        sub_drop = 1.2 * np.sin(phase) * np.exp(-t_thud * 1.5)
        
        # 叠加生锈金属摩擦的爆裂声
        scrape = np.random.uniform(-1.0, 1.0, thud_length) * np.exp(-t_thud * 4.0) * 0.2
        
        audio[thud_idx:thud_idx+thud_length, 0] += (sub_drop + scrape)
        audio[thud_idx:thud_idx+thud_length, 1] += (sub_drop + scrape)
        
    # 音量归一化防爆音
    max_val = np.max(np.abs(audio))
    if max_val > 0:
        audio /= max_val
        
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_aud:
        tmp_aud_path = tmp_aud.name
        
    # 保存为标准的立体声 WAV
    wavfile.write(tmp_aud_path, sample_rate, (audio * 32767).astype(np.int16))
    
    logger.info("Merging audio and video for endcard...")
    subprocess.run([
        "ffmpeg", "-y",
        "-i", tmp_vid_path,
        "-i", tmp_aud_path,
        "-c:v", "copy",
        "-c:a", "aac",
        "-ar", "48000", # 强制重采样保障
        "-ac", "2",     # 强制双声道保障
        "-shortest",
        str(output_path)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    Path(tmp_vid_path).unlink(missing_ok=True)
    Path(tmp_aud_path).unlink(missing_ok=True)
    
    logger.success(f"Typewriter endcard generated: {output_path}")
    return output_path
