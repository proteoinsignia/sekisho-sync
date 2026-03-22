#!/usr/bin/env python3
"""
Palm Memo Viewer
Part of: Sekisho Sync v1.2.2
License: MIT
"""

import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from flask import Flask, jsonify, render_template_string, request

# ----------------------------
# Configuration
# ----------------------------

@dataclass(frozen=True)
class AppConfig:
    memos_dir: Path
    host: str
    port: int
    debug: bool
    default_page_size: int
    max_page_size: int
    preview_chars: int
    cache_ttl: int
    max_file_size: int
    extract_script: Optional[str]  # abs path to palm_memo_extract; None = button hidden
    extract_args: str              # extra CLI args forwarded verbatim
    extract_timeout: int           # seconds before killing the subprocess

def _env_bool(key: str, default: bool = False) -> bool:
    v = os.getenv(key, "1" if default else "0").strip().lower()
    return v in {"1", "true", "yes", "on"}

def _clamp_int(v: Any, default: int, mn: int, mx: int) -> int:
    try: return max(mn, min(mx, int(v)))
    except: return default

def load_config() -> AppConfig:
    """
    Load viewer config via shared config contract (config.py).
    Fails fast if any present variable is malformed — no silent clamps.
    All 12 variables including PAGE_SIZE, MAX_PAGE_SIZE, PREVIEW_CHARS
    are part of the contract and validated in load_viewer_config().
    """
    from config import load_viewer_config, ConfigError

    # ConfigError propagates to __main__ — no sys.exit() in the loader layer.
    vcfg = load_viewer_config()

    memos_dir = vcfg.memos_dir

    # Ensure memos_dir exists (create if needed — not a config error)
    if not memos_dir.exists():
        print(f"WARNING: {memos_dir} does not exist")
        print(f"   Creating directory automatically...")
        try:
            memos_dir.mkdir(parents=True, exist_ok=True)
            print(f"Directory created: {memos_dir}")
        except Exception as e:
            print(f"✗  ERROR: No se pudo crear {memos_dir}: {e}")
            raise

    return AppConfig(
        memos_dir=memos_dir,
        host=vcfg.host,
        port=vcfg.port,
        debug=_env_bool("DEBUG", False),
        default_page_size=vcfg.page_size,
        max_page_size=vcfg.max_page_size,
        preview_chars=vcfg.preview_chars,
        cache_ttl=60,
        max_file_size=10 * 1024 * 1024,
        extract_script=str(vcfg.extract_script) if vcfg.extract_script else None,
        extract_args=vcfg.extract_args,
        extract_timeout=vcfg.extract_timeout,
    )

# Initialized in __main__ to ensure ConfigError is catcheable
CFG = None

# ----------------------------
# Servicio de Memos (Optimizado)
# ----------------------------

class MemoService:
    def __init__(self, config: AppConfig):
        self.config = config
        self._cache: List[Dict[str, Any]] = []
        self._last_refresh = 0.0
        # Regex flexible: acepta r00001 o cualquier texto
        self.palm_re = re.compile(r"^(?:(?P<datestr>\d{4}-\d{2}-\d{2})_)?r(?P<rec>\d+)(?:_(?P<title>.+))?$")

    def _read_file_safe(self, p: Path) -> Tuple[str, Optional[str]]:
        """Read file with encoding fallback and size limit."""
        try:
            # Check size limit
            size = p.stat().st_size
            if size > self.config.max_file_size:
                return "", f"File too large: {size / 1024 / 1024:.1f}MB (max: {self.config.max_file_size / 1024 / 1024:.0f}MB)"
            
            # Try UTF-8 first, fallback to latin-1
            try:
                return p.read_text(encoding="utf-8"), None
            except UnicodeDecodeError:
                return p.read_text(encoding="latin-1"), None
        except Exception as e:
            return "", f"Error leyendo archivo: {e}"

    def refresh_cache(self, force: bool = False):
        now = time.time()
        if not force and (now - self._last_refresh < self.config.cache_ttl) and self._cache:
            return

        if not self.config.memos_dir.exists():
            print(f"CRITICAL: Directory {self.config.memos_dir} does not exist.")
            return

        new_cache = []
        files = list(self.config.memos_dir.glob("*.txt"))
        print(f"Refreshing: {len(files)} files found in {self.config.memos_dir}")

        for p in files:
            m = self.palm_re.match(p.stem)
            if m:
                # Palm format: r00001_title.txt or 2026-02-27_r00001_title.txt
                datestr = m.group("datestr")
                rec = int(m.group("rec") or "0")
                title_raw = (m.group("title") or "").replace("_", " ").strip()
                title_raw = re.sub(r"\s+", " ", title_raw)  # Clean multiple spaces
                title = title_raw if title_raw else f"Record {rec}"
            else:
                # Non-Palm format: use filename
                datestr = None
                rec = 0
                title = p.stem.replace("_", " ")

            content, err = self._read_file_safe(p)
            if err:
                # File too large or error, skip but log
                print(f"Skipping {p.name}: {err}")
                continue
            
            # Create preview and search index
            preview = re.sub(r"\s+", " ", content).strip()[:self.config.preview_chars]
            
            # Date sorting: Palm format uses date prefix, others use mtime
            if datestr:
                try:
                    date_sort = int(datestr.replace("-", ""))
                except:
                    date_sort = 0
            else:
                # Use file modification time as YYYYMMDD
                from datetime import datetime
                mtime = p.stat().st_mtime
                date_sort = int(datetime.fromtimestamp(mtime).strftime("%Y%m%d"))

            new_cache.append({
                "filename": p.name,
                "date": datestr or "No date",
                "record_no": rec,
                "title": title,
                "preview": preview + "..." if len(content) > self.config.preview_chars else preview,
                "_sort": date_sort,
                "_search": f"{title} {p.name} {preview}".lower(),  # Full-text search
            })

        # Sort: Most recent first
        new_cache.sort(key=lambda x: x["_sort"], reverse=True)
        self._cache = new_cache
        self._last_refresh = now
        print(f"✓ Cache updated: {len(new_cache)} memos")

    def search(self, query: str, page: int, size: int) -> Tuple[List[Dict[str, Any]], int]:
        self.refresh_cache()
        q = query.lower().strip()
        filtered = [m for m in self._cache if q in m["_search"]] if q else self._cache
        start = (page - 1) * size
        return filtered[start : start + size], len(filtered)

    def get_content(self, filename: str) -> Tuple[Optional[str], Optional[str]]:
        # Path traversal protection
        safe_path = (self.config.memos_dir / filename).resolve()
        if not str(safe_path).startswith(str(self.config.memos_dir)):
            return None, "Prohibido: Path traversal detectado"
        if not safe_path.exists():
            return None, "Archivo no encontrado"
        
        content, err = self._read_file_safe(safe_path)
        if err:
            return None, err
        return content, None

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about memos."""
        self.refresh_cache()
        
        if not self._cache:
            return {
                "total_memos": 0,
                "total_size": 0,
                "oldest_date": None,
                "newest_date": None,
            }
        
        total_size = sum(
            (self.config.memos_dir / m["filename"]).stat().st_size 
            for m in self._cache
        )
        
        dates = [m["_sort"] for m in self._cache if m["_sort"]]
        
        return {
            "total_memos": len(self._cache),
            "total_size": total_size,
            "total_size_mb": round(total_size / 1024 / 1024, 2),
            "oldest_date": min(dates) if dates else None,
            "newest_date": max(dates) if dates else None,
        }

SERVICE = None

import logging as _logging
logger_extract = _logging.getLogger("palm_extract_trigger")

# ----------------------------
# Extraction Service
# ----------------------------

class ExtractService:
    """Runs palm_memo_extract as a subprocess, one at a time."""

    def __init__(self, config: AppConfig):
        self.config = config
        self._lock = threading.Lock()
        self._running = False
        self._last_result: Dict[str, Any] = {}

    @property
    def available(self) -> bool:
        return self.config.extract_script is not None

    def status(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "running": self._running,
            **self._last_result,
        }

    def trigger(self) -> Dict[str, Any]:
        if not self.available:
            return {"ok": False, "error": "EXTRACT_SCRIPT no configurado"}

        if not self._lock.acquire(blocking=False):
            return {"ok": False, "error": "Extraction already in progress"}

        self._running = True
        self._last_result = {}
        try:
            # Build command: script + --out <memos_dir> + extra args
            extra = self.config.extract_args.split() if self.config.extract_args else []
            cmd = [self.config.extract_script, "--out", str(self.config.memos_dir)] + extra
            logger_extract.info(f"Ejecutando: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.config.extract_timeout,
            )

            ok = result.returncode == 0
            self._last_result = {
                "ok": ok,
                "returncode": result.returncode,
                "stdout": result.stdout[-4000:],   # last 4KB
                "stderr": result.stderr[-2000:],
            }
            if ok:
                logger_extract.info("Extraction completed OK")
                # Force cache refresh so new memos appear immediately
                SERVICE.refresh_cache(force=True)
            else:
                logger_extract.warning(f"Extraction failed (rc={result.returncode})")
            return self._last_result

        except subprocess.TimeoutExpired:
            self._last_result = {"ok": False, "error": f"Timeout ({self.config.extract_timeout}s)"}
            logger_extract.error("Extraction cancelled: timeout")
            return self._last_result
        except Exception as e:
            self._last_result = {"ok": False, "error": str(e)}
            logger_extract.error(f"Error launching extraction: {e}")
            return self._last_result
        finally:
            self._running = False
            self._lock.release()

EXTRACT = None
app = Flask(__name__)

# ----------------------------
# Template (Apple-ish con mejoras)
# ----------------------------

HTML = """<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Palm Memo Viewer</title>
  <style>
    :root{
      --bg0:#f5f5f7; --bg1:#ffffff; --bg2:rgba(255,255,255,.75);
      --text0:#1d1d1f; --text1:#6e6e73; --border:rgba(0,0,0,.1);
      --accent:#007aff; --accent2:rgba(0,122,255,0.1);
      --radius:16px;
    }
    @media (prefers-color-scheme: dark){
      :root{
        --bg0:#000000; --bg1:#1c1c1e; --bg2:rgba(28,28,30,0.8);
        --text0:#ffffff; --text1:#8e8e93; --border:rgba(255,255,255,0.15);
      }
    }
    *{box-sizing:border-box; margin:0; padding:0;}
    body{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; background:var(--bg0); color:var(--text0); height:100vh; overflow:hidden; }
    .app-grid{ display:grid; grid-template-columns: 350px 1fr; height:100%; }
    .sidebar{ border-right: 1px solid var(--border); display:flex; flex-direction:column; background:var(--bg0); }
    .search-box{ padding:15px; }
    .search-box input{ width:100%; padding:10px; border-radius:10px; border:none; background:var(--border); color:var(--text0); outline:none; }
    .memo-list{ flex:1; overflow-y:auto; padding:10px; }
    .memo-item{ padding:15px; border-radius:12px; cursor:pointer; margin-bottom:5px; transition:0.2s; }
    .memo-item:hover{ background:var(--accent2); }
    .memo-item.active{ background:var(--accent); color:white; }
    .memo-item.active .preview{ color:rgba(255,255,255,0.8); }
    .title{ font-weight:600; font-size:14px; margin-bottom:4px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .preview{ font-size:12px; color:var(--text1); display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
    .viewer{ background:var(--bg1); display:flex; flex-direction:column; }
    .viewer-header{ padding:20px; border-bottom:1px solid var(--border); display:flex; justify-content:space-between; align-items:center; }
    .viewer-content{ flex:1; overflow-y:auto; padding:40px; }
    .paper{ max-width:800px; margin:0 auto; white-space:pre-wrap; line-height:1.6; font-size:16px; font-family:ui-monospace,SFMono-Regular,monospace; }
    .btn{ padding:8px 16px; border-radius:8px; border:none; background:var(--accent); color:white; cursor:pointer; font-weight:500; }
    .empty-state{ height:100%; display:flex; align-items:center; justify-content:center; color:var(--text1); }
    .stats{ padding:10px 15px; font-size:12px; color:var(--text1); border-top:1px solid var(--border); }
    .sidebar-header{ padding:15px 15px 0; display:flex; align-items:center; justify-content:space-between; }
    .sidebar-header h1{ font-size:13px; font-weight:600; color:var(--text1); letter-spacing:.02em; text-transform:uppercase; }
    .sync-btn{ display:flex; align-items:center; gap:5px; padding:6px 12px; border-radius:8px; border:none;
      background:var(--accent); color:white; cursor:pointer; font-size:12px; font-weight:500; transition:.15s; }
    .sync-btn:hover{ opacity:.85; }
    .sync-btn:disabled{ opacity:.4; cursor:not-allowed; }
    .sync-btn.hidden{ display:none; }
    @keyframes spin{ to{ transform:rotate(360deg); } }
    .spin{ display:inline-block; animation:spin .8s linear infinite; }
  </style>
</head>
<body>
  <div class="app-grid">
    <aside class="sidebar">
      <div class="sidebar-header">
        <h1>Palm Memos</h1>
        <button id="syncBtn" class="sync-btn hidden" onclick="triggerExtract()" title="Extraer memos ahora">
          <span id="syncIcon">↻</span> Sync
        </button>
      </div>
      <div class="search-box"><input id="q" placeholder="Search memos..." autocomplete="off"></div>
      <div class="memo-list" id="list"></div>
      <div class="stats" id="stats">Loading...</div>
    </aside>
    <main class="viewer">
      <div id="v-head" class="viewer-header" style="display:none;">
        <h2 id="v-title"></h2>
        <button class="btn" onclick="copyContent()">Copy</button>
      </div>
      <div class="viewer-content">
        <div id="v-empty" class="empty-state">Select a note to start</div>
        <div id="v-paper" class="paper"></div>
      </div>
    </main>
  </div>

<script>
  let state = { q: '', memos: [], active: -1 };
  let contentCache = {};  // Cache for already-loaded content
  
  async function loadStats() {
    try {
      const res = await fetch('/api/stats');
      const data = await res.json();
      document.getElementById('stats').textContent = 
        data.total_memos + ' memos - ' + data.total_size_mb + ' MB';
    } catch(e) {
      document.getElementById('stats').textContent = 'Stats unavailable';
    }
  }
  
  async function loadList() {
    const res = await fetch(`/api/memos?q=${encodeURIComponent(state.q)}`);
    const data = await res.json();
    state.memos = data.memos;
    renderList();
  }

  function renderList() {
    var html = '';
    for (var i = 0; i < state.memos.length; i++) {
      var m = state.memos[i];
      var active = (i === state.active) ? ' active' : '';
      html += '<div class="memo-item' + active + '" onclick="showMemo(' + i + ')">'
            + '<div class="title">' + escapeHtml(m.title) + '</div>'
            + '<div class="preview">' + escapeHtml(m.preview) + '</div>'
            + '</div>';
    }
    document.getElementById('list').innerHTML = html || '<p style="text-align:center;padding:20px;color:gray;">No notes</p>';
  }

  async function showMemo(idx) {
    state.active = idx;
    renderList();
    const memo = state.memos[idx];
    
    // Check cache first
    if (contentCache[memo.filename]) {
      renderContent(memo.title, contentCache[memo.filename]);
      return;
    }
    
    // Fetch from API
    const res = await fetch(`/api/memo/${encodeURIComponent(memo.filename)}`);
    const data = await res.json();
    
    if (data.error) {
      alert('Error: ' + data.error);
      return;
    }
    
    // Cache content
    contentCache[memo.filename] = data.content;
    renderContent(memo.title, data.content);
  }

  function renderContent(title, content) {
    document.getElementById('v-empty').style.display = 'none';
    document.getElementById('v-head').style.display = 'flex';
    document.getElementById('v-title').textContent = title;
    document.getElementById('v-paper').textContent = content;
  }

  function copyContent() {
    const text = document.getElementById('v-paper').textContent;
    navigator.clipboard.writeText(text);
    alert('Copied to clipboard');
  }

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  let searchTimeout;
  document.getElementById('q').oninput = (e) => {
    state.q = e.target.value;
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(loadList, 300);  // Debounce
  };

  // Check if extract button should be shown
  async function initExtractButton() {
    try {
      const res = await fetch('/api/extract/status');
      const data = await res.json();
      if (data.available) {
        document.getElementById('syncBtn').classList.remove('hidden');
      }
    } catch(e) {}
  }

  async function triggerExtract() {
    const btn = document.getElementById('syncBtn');
    const icon = document.getElementById('syncIcon');
    btn.disabled = true;
    icon.className = 'spin';
    icon.textContent = '\u21bb';
    try {
      const res = await fetch('/api/extract', { method: 'POST' });
      const data = await res.json();
      if (data.ok) {
        icon.className = '';
        icon.textContent = '\u2713';
        await loadList();
        await loadStats();
        setTimeout(function() { icon.textContent = '\u21bb'; btn.disabled = false; }, 2000);
      } else {
        icon.className = '';
        icon.textContent = '\u2717';
        var msg = data.error || data.stderr || 'Unknown error';
        alert('Extraction failed: ' + msg.slice(0, 300));
        setTimeout(function() { icon.textContent = '\u21bb'; btn.disabled = false; }, 2000);
      }
    } catch(e) {
      icon.className = '';
      icon.textContent = '\u2717';
      alert('Network error: ' + e.message);
      setTimeout(function() { icon.textContent = '\u21bb'; btn.disabled = false; }, 2000);
    }
  }

  // Initial load
  loadList();
  loadStats();
  initExtractButton();
</script>
</body>
</html>
"""

# ----------------------------
# Rutas
# ----------------------------

@app.route("/")
def index(): 
    return render_template_string(HTML)

@app.route("/api/memos")
def api_memos():
    q = request.args.get("q", "")
    page = _clamp_int(request.args.get("page"), 1, 1, 1000)
    items, total = SERVICE.search(q, page, CFG.default_page_size)
    return jsonify({"memos": items, "total": total})

@app.route("/api/memo/<path:filename>")
def api_memo(filename: str):
    content, err = SERVICE.get_content(filename)
    if err: 
        return jsonify({"error": err}), 404
    return jsonify({"content": content, "size": len(content)})

@app.route("/api/stats")
def api_stats():
    return jsonify(SERVICE.get_stats())


@app.route("/api/extract", methods=["POST"])
def api_extract():
    """Trigger palm_memo_extract synchronously. Returns when done."""
    result = EXTRACT.trigger()
    status_code = 200 if result.get("ok") else 500
    return jsonify(result), status_code

@app.route("/api/extract/status")
def api_extract_status():
    return jsonify(EXTRACT.status())

if __name__ == "__main__":
    import sys
    from config import ConfigError

    try:
        CFG     = load_config()
        SERVICE = MemoService(CFG)
        EXTRACT = ExtractService(CFG)

        print("═══════════════════════════════════════════════════════════")
        print(f"  Palm Memo Viewer — Sekisho Sync v1.2.2")
        print("═══════════════════════════════════════════════════════════")
        print(f"  Memos dir: {CFG.memos_dir}")
        print(f"  Port:      {CFG.port}")
        print("═══════════════════════════════════════════════════════════")

        SERVICE.refresh_cache(force=True)
        app.run(host=CFG.host, port=CFG.port, debug=CFG.debug)

    except ConfigError as e:
        print(f"[CONFIG ERROR] {e}", file=sys.stderr)
        sys.exit(1)
