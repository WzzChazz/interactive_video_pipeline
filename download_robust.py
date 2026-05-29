import os
import time
import requests
from pathlib import Path
from loguru import logger

# HuggingFace 官方源和国内镜像源
HF_MIRROR = "https://hf-mirror.com"
GH_MIRROR = "https://mirror.ghproxy.com/"

DOWNLOAD_TASKS = [
    {
        "name": "Wav2Lip GAN",
        "url": f"{HF_MIRROR}/camenduru/Wav2Lip/resolve/main/checkpoints/wav2lip_gan.pth",
        "dest": "local_models/Wav2Lip/checkpoints/wav2lip_gan.pth"
    },
    {
        "name": "Wav2Lip S3FD",
        "url": f"{HF_MIRROR}/camenduru/Wav2Lip/resolve/main/face_detection/detection/sfd/s3fd.pth",
        "dest": "local_models/Wav2Lip/face_detection/detection/sfd/s3fd.pth"
    },
    {
        "name": "CodeFormer Model",
        "url": f"{GH_MIRROR}https://github.com/sczhou/CodeFormer/releases/download/v0.1.0/codeformer.pth",
        "dest": "local_models/CodeFormer/weights/CodeFormer/codeformer.pth"
    }
]

def download_file(url: str, dest: Path, name: str, max_retries: int = 5):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 100 * 1024 * 1024:  # 大于 100MB 认为已下载
        logger.info(f"[Cache] {name} already exists at {dest}, skipping.")
        return

    for attempt in range(max_retries):
        try:
            logger.info(f"Downloading {name} (Attempt {attempt+1}/{max_retries})...")
            # 流式下载以支持大文件
            response = requests.get(url, stream=True, timeout=15)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded_size = 0
            
            start_time = time.time()
            with open(dest, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded_size += len(chunk)
                        
                        # 每下载 10MB 打印一次日志
                        if downloaded_size % (10 * 1024 * 1024) < 8192 and total_size > 0:
                            percent = (downloaded_size / total_size) * 100
                            speed = downloaded_size / (1024 * 1024) / (time.time() - start_time)
                            logger.debug(f"{name}: {percent:.1f}% ({downloaded_size//1024//1024}/{total_size//1024//1024} MB) - {speed:.1f} MB/s")
                            
            if total_size > 0 and downloaded_size < total_size:
                raise Exception("Incomplete file downloaded.")
                
            logger.success(f"Successfully downloaded {name} to {dest}")
            return
        except Exception as e:
            logger.warning(f"Download {name} failed: {e}")
            if dest.exists():
                dest.unlink() # 删除损坏的文件
            time.sleep(3)
            
    logger.error(f"Failed to download {name} after {max_retries} attempts.")
    raise Exception(f"Download Failed for {name}")

def main():
    base_dir = Path(__file__).parent.absolute()
    for task in DOWNLOAD_TASKS:
        dest_path = base_dir / task["dest"]
        download_file(task["url"], dest_path, task["name"])
        
    logger.success("All deep learning weights downloaded successfully!")

if __name__ == "__main__":
    main()
