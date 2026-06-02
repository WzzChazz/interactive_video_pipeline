"""
core/endcard_generator.py
=========================
生成打字机特效的交互式片尾（A/B选择）。
通过 PIL 逐帧绘制黑底白字，并使用 FFmpeg rawvideo pipe 合成 MP4。
"""

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
            # ImageFont.getlength or getbbox
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
    chars_per_sec: float = 12.0
) -> Path:
    """
    生成带有打字机特效和打字音效的 MP4 视频短片。
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # 准备字体
    font_size = 40
    font_path = "/System/Library/Fonts/Supplemental/Songti.ttc"
    try:
        font = ImageFont.truetype(font_path, font_size)
        title_font = ImageFont.truetype(font_path, 60)
    except IOError:
        logger.warning(f"Failed to load font from {font_path}. Using default.")
        font = ImageFont.load_default()
        title_font = font

    # 排版计算
    margin_x = 80
    max_text_width = VIDEO_WIDTH - margin_x * 2
    
    # 标题
    title = "做出你的选择："
    
    # 分支换行
    lines_a = wrap_text(branch_a, font, max_text_width)
    lines_b = wrap_text(branch_b, font, max_text_width)
    
    # 将文本结构化为带坐标的字符序列
    # (char, x, y, color)
    chars_to_draw = []
    
    # 初始 Y
    current_y = 300
    
    # 标题字符
    title_x = (VIDEO_WIDTH - title_font.getlength(title)) / 2 if hasattr(title_font, 'getlength') else margin_x
    cur_x = title_x
    for ch in title:
        chars_to_draw.append((ch, cur_x, current_y, "white", title_font))
        cur_x += title_font.getlength(ch) if hasattr(title_font, 'getlength') else 40
        
    current_y += 120
    
    # Branch A
    for line in lines_a:
        cur_x = margin_x
        for ch in line:
            chars_to_draw.append((ch, cur_x, current_y, "#FFAAAA", font))  # 浅红色
            cur_x += font.getlength(ch) if hasattr(font, 'getlength') else 20
        current_y += font_size + 15
        
    current_y += 60
    
    # Branch B
    for line in lines_b:
        cur_x = margin_x
        for ch in line:
            chars_to_draw.append((ch, cur_x, current_y, "#AAAAFF", font))  # 浅蓝色
            cur_x += font.getlength(ch) if hasattr(font, 'getlength') else 20
        current_y += font_size + 15
        
    total_chars = len(chars_to_draw)
    total_frames = int(duration_sec * fps)
    
    # 启动 FFmpeg subprocess 接收 raw video
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_vid:
        tmp_vid_path = tmp_vid.name
        
    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-s", f"{VIDEO_WIDTH}x{VIDEO_HEIGHT}",
        "-pix_fmt", "rgb24",
        "-r", str(fps),
        "-i", "-", # 从 stdin 读
        "-c:v", "libx264",
        "-preset", "ultrafast", # 因为是简单画面，压缩飞快
        "-pix_fmt", "yuv420p",
        tmp_vid_path
    ]
    
    logger.info("Starting typewriter video generation...")
    process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    
    base_img = Image.new('RGB', (VIDEO_WIDTH, VIDEO_HEIGHT), color=(10, 10, 15))
    draw = ImageDraw.Draw(base_img)
    
    typing_end_frame = 0
    
    try:
        for f in range(total_frames):
            # 当前应该显示多少个字符
            time_sec = f / fps
            chars_visible = int(time_sec * chars_per_sec)
            
            if chars_visible > total_chars:
                chars_visible = total_chars
                if typing_end_frame == 0:
                    typing_end_frame = f
                
            # 我们每次在上一帧的基础上画，以节省计算（或者全量重画，全量画对于文本来说也非常快）
            # 这里选择全量重画以保证抗锯齿不堆叠
            frame_img = Image.new('RGB', (VIDEO_WIDTH, VIDEO_HEIGHT), color=(10, 10, 15))
            f_draw = ImageDraw.Draw(frame_img)
            
            for i in range(chars_visible):
                ch, x, y, color, c_font = chars_to_draw[i]
                f_draw.text((x, y), ch, font=c_font, fill=color)
                
            # 绘制光标（闪烁效果）
            if chars_visible < total_chars:
                if (f // 10) % 2 == 0:  # 光标闪烁频率
                    last_ch = chars_to_draw[chars_visible] if chars_visible < total_chars else chars_to_draw[-1]
                    cursor_x = last_ch[1]
                    cursor_y = last_ch[2]
                    f_draw.rectangle([cursor_x, cursor_y + 5, cursor_x + 20, cursor_y + 40], fill="white")
            
            # 转为 bytes 写给 ffmpeg
            process.stdin.write(frame_img.tobytes())
            
        process.stdin.close()
        process.wait()
    except Exception as e:
        logger.error(f"Failed to stream to FFmpeg: {e}")
        process.kill()
        raise
        
    if process.returncode != 0:
        logger.error(f"FFmpeg error: {process.stderr.read().decode()}")
        raise RuntimeError("FFmpeg rawvideo failed")

    # 音效合成
    # 生成一个打字机音效，打字时长为 typing_duration
    typing_duration = typing_end_frame / fps if typing_end_frame > 0 else (total_chars / chars_per_sec)
    
    # 我们用一个短促的噪声包络模拟按键咔哒声: 类似 noise 然后做一个极短的 decay
    # 然后用一个叮的声音模拟打字结束的提示
    
    # 频率为 chars_per_sec (例如15次/秒) 也就是周期 1/15 = 0.066s
    click_rate = chars_per_sec
    # 使用 aevalsrc 产生快速的敲击声
    # exp(-300*mod(t, 1/rate)) 产生非常锐利的尖峰
    # 再加一个高频噪声
    sfx_filter = (
        f"aevalsrc='(0.4*random(0)+0.6*sin(2*PI*400*t))*exp(-150*mod(t, 1/{click_rate}))':d={typing_duration}[typewriter];"
        f"aevalsrc='sin(2*PI*880*t)*exp(-5*t)':d=2[ding];"
        f"[typewriter]adelay=0|0[a1];"
        f"[ding]adelay={int(typing_duration*1000)}|{int(typing_duration*1000)}[a2];"
        f"[a1][a2]amix=inputs=2:duration=longest[aout]"
    )
    
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_aud:
        tmp_aud_path = tmp_aud.name
        
    logger.info("Generating typewriter audio...")
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", # 凑个数
        "-filter_complex", sfx_filter,
        "-map", "[aout]",
        "-t", str(duration_sec),
        tmp_aud_path
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    logger.info("Merging audio and video for endcard...")
    subprocess.run([
        "ffmpeg", "-y",
        "-i", tmp_vid_path,
        "-i", tmp_aud_path,
        "-c:v", "copy",
        "-c:a", "aac",
        "-shortest",
        str(output_path)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # 清理临时文件
    Path(tmp_vid_path).unlink(missing_ok=True)
    Path(tmp_aud_path).unlink(missing_ok=True)
    
    logger.success(f"Typewriter endcard generated: {output_path}")
    return output_path

if __name__ == "__main__":
    # 独立测试运行
    generate_typewriter_endcard(
        "林悦发现自己也是克隆实验体。选择报警揭露医院罪行扣1，销毁证据自保扣2！",
        "真林悦突然现身。相信她联手逃出医院扣1，制服她独自逃跑扣2！",
        "test_typewriter.mp4",
        duration_sec=8.0,
        chars_per_sec=15.0
    )
