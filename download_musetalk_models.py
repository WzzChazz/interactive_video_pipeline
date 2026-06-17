import os
os.environ["HTTP_PROXY"] = "http://127.0.0.1:7897"
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7897"
from huggingface_hub import hf_hub_download, snapshot_download

comfy_models_dir = "/Users/mac/project/interactive_video_pipeline/local_models/ComfyUI/models"

def download_models():
    print("🚀 开始下载 MuseTalk 必需模型...")
    
    # 1. 下载 MuseTalk 权重包 (由于文件多，直接 snapshot_download)
    print("下载 MuseTalk 核心权重包...")
    musetalk_dir = os.path.join(comfy_models_dir, "musetalk")
    os.makedirs(musetalk_dir, exist_ok=True)
    snapshot_download(repo_id="TMElyralab/MuseTalk", local_dir=musetalk_dir, ignore_patterns=["*.md", ".git*"], max_workers=1, resume_download=True)
    
    # 2. 下载 SD-VAE
    print("下载 SD-VAE...")
    vae_dir = os.path.join(comfy_models_dir, "vae")
    os.makedirs(vae_dir, exist_ok=True)
    hf_hub_download(repo_id="stabilityai/sd-vae-ft-mse", filename="diffusion_pytorch_model.safetensors", local_dir=vae_dir)
    
    # 3. 下载 Whisper Tiny
    print("下载 Whisper Tiny...")
    whisper_dir = os.path.join(comfy_models_dir, "whisper")
    os.makedirs(whisper_dir, exist_ok=True)
    snapshot_download(repo_id="openai/whisper-tiny.en", local_dir=whisper_dir, max_workers=1, resume_download=True)

    print("✅ 所有模型下载完毕！")

if __name__ == "__main__":
    download_models()
