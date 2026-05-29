# 🎬 Interactive Video Automation Pipeline

> 全自动交互式剧情短视频生产线 · Python 3.10+

---

## 📌 系统概述

本系统每天定时：
1. 抓取抖音视频评论区 A/B 投票 → 决定今日剧情分支
2. 调用 Claude 生成结构化分镜剧本 JSON
3. 并行调用 Flux + ElevenLabs + Kling/Runway 生成图、音、视频资产
4. FFmpeg 自动合片 + 字幕硬烧录
5. DrissionPage 自动发布至抖音创作者平台

---

## 🏗️ 目录结构

```
interactive_video_pipeline/
├── config/
│   └── settings.py          # 所有 API Keys 与全局配置
├── database/
│   ├── models.py            # SQLAlchemy ORM（Episodes 表）
│   └── db_session.py        # Session 管理 + init_db()
├── core/
│   ├── llm_agent.py         # Claude/DeepSeek 剧本生成（Phase 2）
│   ├── image_gen.py         # Flux 批量生图（Phase 3）
│   ├── video_gen.py         # Kling/Runway 图生视频（Phase 3）
│   ├── audio_gen.py         # ElevenLabs TTS + SFX（Phase 3）
│   └── ffmpeg_compiler.py   # FFmpeg 合片（Phase 3）
├── automation/
│   ├── scraper.py           # 抖音评论抓取（Phase 4）
│   └── publisher.py         # 抖音自动发布（Phase 4）
├── storage/
│   ├── temp/                # 临时资产（图片、音频、视频片段）
│   └── outputs/             # 最终合成成品
├── main.py                  # 流水线调度核心
└── requirements.txt
```

---

## ⚙️ 快速开始

### 1. 安装依赖

```bash
cd interactive_video_pipeline
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 配置密钥

```bash
cp .env.example .env
# 编辑 .env，填入所有 API Keys
```

### 3. 初始化数据库

```bash
python main.py --stage scrape   # 测试数据库初始化 + 单阶段调试
```

### 4. 立即运行完整流水线

```bash
python main.py --run-now
```

### 5. 守护模式（每日自动触发）

```bash
python main.py
```

---

## 🔑 环境变量说明

| 变量名 | 说明 | 必填 |
|--------|------|------|
| `ANTHROPIC_API_KEY` | Claude API Key | ✅ |
| `DEEPSEEK_API_KEY` | DeepSeek API Key（备用 LLM） | ⚪ |
| `FLUX_API_KEY` | Flux.1 Pro 生图 API Key | ✅ |
| `KLING_API_KEY` | 可灵 AI 图生视频 API Key | ✅ |
| `RUNWAY_API_KEY` | Runway Gen-3 API Key（备用） | ⚪ |
| `ELEVENLABS_API_KEY` | ElevenLabs TTS + SFX | ✅ |
| `ELEVENLABS_VOICE_ID` | 主角声音 ID | ✅ |
| `BROWSER_USER_DATA_DIR` | Chrome 持久化 Profile 路径（保持抖音登录） | ✅ |
| `DOUYIN_TARGET_VIDEO_URL` | 上一集视频 URL（用于抓取投票） | ✅ |
| `DAILY_RUN_TIME` | 每日触发时间（默认 `08:00`） | ⚪ |
| `VIDEO_PROVIDER` | 视频生成服务：`kling` / `runway` | ⚪ |

---

## 📊 Episode 状态机

```
VOTING → GENERATING → COMPLETED → PUBLISHED
              ↓
            FAILED
```

---

## 🚀 开发进度

- [x] **Phase 1** · 项目初始化 + 数据库 ORM + 配置管理
- [ ] **Phase 2** · LLM 剧本生成（`core/llm_agent.py`）
- [ ] **Phase 3** · 资产生成 + FFmpeg 合片
- [ ] **Phase 4** · 抖音自动化抓取 + 发布
