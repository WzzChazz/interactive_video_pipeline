#!/bin/zsh
# 每日自动出片(E3)：launchd 每天 15:00 触发 → 产出当日治愈短片 → 晚上你人工审核后发布。
# 日志: storage/logs/daily_produce.log ；失败(如火山欠费)会记录在日志里,不影响次日再跑。
cd /Users/mac/project/interactive_video_pipeline || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"   # ffmpeg/ffprobe 在 homebrew
mkdir -p storage/logs
echo "===== daily_produce $(date '+%F %T') =====" >> storage/logs/daily_produce.log
/usr/bin/env python3 main.py --run-now --theme capybara_healing >> storage/logs/daily_produce.log 2>&1
echo "===== exit=$? $(date '+%F %T') =====" >> storage/logs/daily_produce.log
