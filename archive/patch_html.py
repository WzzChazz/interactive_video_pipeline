with open("dashboard/static/index.html", "r") as f:
    content = f.read()

# 1. Update setInterval
content = content.replace(
    "setInterval(() => { loadStats(); pollPipeline(); pollProgress(); }, 5000);",
    "setInterval(() => { loadStats(); pollPipeline(); pollProgress(); }, 5000);\n    connectProgressWS();"
)

# 2. Add WebSocket logic for progress
ws_code = """
  // ── Synthesis progress (WebSocket) ────────────────────
  let progressWs = null;
  function connectProgressWS() {
    if (progressWs) return;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/ws/progress`;
    progressWs = new WebSocket(wsUrl);
    
    progressWs.onmessage = (e) => {
      try {
        const p = JSON.parse(e.data);
        const wrap = document.getElementById('progressWrap');
        if (p.active) {
          wrap.classList.add('visible');
          document.getElementById('progressLabel').textContent = p.step_name;
          document.getElementById('progressBar').style.width = p.pct + '%';
          document.getElementById('progressPct').textContent = p.pct + '%';
          const epEl = document.getElementById('progressEp');
          if (epEl && p.episode) epEl.textContent = p.episode;
        } else {
          wrap.classList.remove('visible');
        }
      } catch (err) {}
    };

    progressWs.onclose = () => {
      progressWs = null;
      setTimeout(connectProgressWS, 3000); // auto reconnect
    };
  }

"""

# We still keep pollProgress() doing the initial fetch, but the WS handles live updates.
# Wait, pollProgress() makes an HTTP GET to /api/pipeline/progress. I can just leave it to run every 5s to catch edge cases, and let WS override the UI instantly. Yes, keeping both is robust!

# Let's write it out
with open("dashboard/static/index.html", "w") as f:
    f.write(content.replace("  // ── Synthesis progress ────────────────────────────────", ws_code + "  // ── Synthesis progress ────────────────────────────────"))

