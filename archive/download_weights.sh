#!/bin/bash
set -e

echo "=== Downloading Wav2Lip Weights ==="
cd /Users/mac/project/interactive_video_pipeline/local_models/Wav2Lip
mkdir -p checkpoints face_detection/detection/sfd
curl -L -o checkpoints/wav2lip_gan.pth https://huggingface.co/camenduru/Wav2Lip/resolve/main/checkpoints/wav2lip_gan.pth
curl -L -o face_detection/detection/sfd/s3fd.pth https://huggingface.co/camenduru/Wav2Lip/resolve/main/face_detection/detection/sfd/s3fd.pth

echo "=== Downloading CodeFormer Weights ==="
cd /Users/mac/project/interactive_video_pipeline/local_models/CodeFormer
mkdir -p weights/CodeFormer
curl -L -o weights/CodeFormer/codeformer.pth https://github.com/sczhou/CodeFormer/releases/download/v0.1.0/codeformer.pth

echo "=== All Weights Downloaded ==="
