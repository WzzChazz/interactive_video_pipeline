"""
webui.py
========
Streamlit-based Control Dashboard for the Interactive Video Pipeline.
"""

import streamlit as st
import pandas as pd
import json
import subprocess
from pathlib import Path
from database.db_session import get_session
from database.models import Episode, EpisodeStatus
from config.themes import THEMES
from config.settings import STORAGE_OUTPUT_DIR

st.set_page_config(page_title="AI Video Pipeline", page_icon="🎬", layout="wide")

st.title("🎬 互动短剧 AI 全自动生产线控制台")
st.markdown("基于 MoneyPrinterTurbo 架构优化，自带 Pexels 容灾、电影级音频闪避、多角色声纹、以及精准字幕同步。")

# 侧边栏：配置与控制
with st.sidebar:
    st.header("⚙️ 全局控制")
    selected_theme = st.selectbox("🎬 选择短剧宇宙 (Theme)", list(THEMES.keys()))
    
    st.markdown("---")
    st.subheader("🚀 触发流水线")
    if st.button("▶️ 立即运行流水线 (Run Pipeline)"):
        with st.spinner("流水线已在后台启动... 请查看终端日志。"):
            try:
                # 使用 subprocess 后台启动，避免阻塞 UI
                subprocess.Popen(["python3", "main.py", "--run-now", "--theme", selected_theme])
                st.success("流水线已触发！")
            except Exception as e:
                st.error(f"启动失败: {e}")

# 主面板：展示剧集状态
st.header(f"📊 {THEMES[selected_theme].get('collection_name', '当前宇宙')} - 剧集状态")

with get_session() as session:
    episodes = session.query(Episode).filter(Episode.theme_key == selected_theme).order_by(Episode.episode_number.desc()).all()

if not episodes:
    st.info("当前宇宙暂无数据。点击左侧按钮启动生成！")
else:
    # 构造 DataFrame
    data = []
    for ep in episodes:
        data.append({
            "S/E": f"S{ep.season_id:02d}E{ep.episode_number:02d}",
            "Title": ep.title or "N/A",
            "Status": ep.status.value,
            "Chosen Branch": ep.chosen_branch or "N/A",
            "Error": ep.error_message or ""
        })
    df = pd.DataFrame(data)
    
    # 用不同的颜色展示状态
    def color_status(val):
        color = 'white'
        if val == 'PUBLISHED': color = '#28a745'
        elif val == 'COMPLETED': color = '#17a2b8'
        elif val == 'FAILED': color = '#dc3545'
        elif val == 'PENDING_REVIEW': color = '#ffc107'
        return f'color: {color}; font-weight: bold'
        
    st.dataframe(df.style.map(color_status, subset=['Status']), use_container_width=True)

# 最新剧集详情展示
st.markdown("---")
latest_ep = episodes[0] if episodes else None

if latest_ep:
    st.subheader(f"🔍 最新集详情: {latest_ep.episode_tag}")
    
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**当前状态**: `{latest_ep.status.value}`")
        if latest_ep.status == EpisodeStatus.PENDING_REVIEW:
            st.warning("⚠️ 剧本生成完毕，等待审核发布中。后台定时任务将自动处理。")
            if st.button("✅ 一键过审并生成视频"):
                with get_session() as session:
                    db_ep = session.get(Episode, latest_ep.id)
                    db_ep.status = EpisodeStatus.GENERATING_ASSETS
                    session.commit()
                st.success("审核通过！请点击左侧重新触发流水线。")
                st.rerun()
                
        if latest_ep.error_message:
            st.error(f"错误信息: {latest_ep.error_message}")
            
    with col2:
        if latest_ep.script_json:
            with st.expander("📝 剧本内容 (Script)", expanded=False):
                try:
                    script_data = json.loads(latest_ep.script_json)
                    st.json(script_data)
                except:
                    st.text(latest_ep.script_json)
                    
    # 如果已生成视频，提供预览
    if latest_ep.video_output_path and Path(latest_ep.video_output_path).exists():
        st.markdown("### 🎥 最终视频预览 (Douyin)")
        st.video(latest_ep.video_output_path)
    elif latest_ep.video_global_path and Path(latest_ep.video_global_path).exists():
        st.markdown("### 🎥 最终视频预览 (Global)")
        st.video(latest_ep.video_global_path)
