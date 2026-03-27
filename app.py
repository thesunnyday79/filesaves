import streamlit as st
import requests
import hashlib
import os
import tempfile
import subprocess
import json
from pathlib import Path

# ─────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="pCloud Auto Caption",
    page_icon="🎬",
    layout="wide",
)

st.markdown("""
<style>
  .block-container { padding-top: 1.5rem; max-width: 1200px; }
  .log-box {
      background: #0f1117;
      border-left: 4px solid #6C63FF;
      padding: 0.6rem 1rem;
      border-radius: 6px;
      font-family: 'Courier New', monospace;
      font-size: 0.82rem;
      color: #cdd6f4;
      margin: 3px 0;
  }
  .log-success { border-left-color: #a6e3a1; color: #a6e3a1; }
  .log-error   { border-left-color: #f38ba8; color: #f38ba8; }
  .log-warn    { border-left-color: #f9e2af; color: #f9e2af; }
  .video-card {
      background: #1e1e2e;
      border-radius: 10px;
      padding: 0.8rem 1rem;
      margin-bottom: 0.4rem;
      border: 1px solid #2a2a3e;
  }
  .login-card {
      background: #1e1e2e;
      border-radius: 14px;
      padding: 2rem 2.2rem;
      border: 1px solid #2a2a3e;
  }
  .user-badge {
      background: #1e2e1e;
      border: 1px solid #a6e3a1;
      border-radius: 8px;
      padding: 0.5rem 1rem;
      color: #a6e3a1;
      font-size: 0.85rem;
      display: inline-block;
  }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────
# PCLOUD AUTH  —  username + password → working session
# Strategy: try multiple auth methods, use whichever works
# ─────────────────────────────────────────────
def pcloud_login(username: str, password: str) -> dict:
    """
    Try all known pCloud auth methods across US + EU datacenters.
    Returns session dict with working token + auth_type.
    """
    errors = []

    for eu, base in [(False, "https://api.pcloud.com"),
                     (True,  "https://eapi.pcloud.com")]:

        # ── Method 1: digest auth (SHA1) ──────────────────
        try:
            r1 = requests.get(f"{base}/getdigest", timeout=10)
            d1 = r1.json()
            if d1.get("result") == 0 and "digest" in d1:
                digest   = d1["digest"]
                sha_user = hashlib.sha1(username.lower().encode()).hexdigest().encode()
                pw_hash  = hashlib.sha1(
                    password.encode() + sha_user + digest.encode()
                ).hexdigest()
                r2 = requests.get(f"{base}/userinfo", params={
                    "getauth": 1, "logout": 1,
                    "username": username,
                    "digest": digest, "passworddigest": pw_hash,
                }, timeout=15)
                d2 = r2.json()
                if d2.get("result") == 0:
                    token = d2.get("token") or d2.get("auth")
                    if token:
                        # Verify works
                        v = requests.get(f"{base}/userinfo",
                                         params={"auth": token}, timeout=10).json()
                        if v.get("result") == 0:
                            return _build_session(v, token, "auth", eu, base)
                        v2 = requests.get(f"{base}/userinfo",
                                          params={"access_token": token}, timeout=10).json()
                        if v2.get("result") == 0:
                            return _build_session(v2, token, "access_token", eu, base)
                if d2.get("result") == 2000:
                    raise RuntimeError("❌ Sai email hoặc mật khẩu pCloud.")
        except RuntimeError:
            raise
        except Exception as e:
            errors.append(f"{base} digest: {e}")

        # ── Method 2: plain username+password ─────────────
        try:
            r3 = requests.get(f"{base}/userinfo", params={
                "getauth": 1, "logout": 1,
                "username": username, "password": password,
            }, timeout=15)
            d3 = r3.json()
            if d3.get("result") == 0:
                token = d3.get("token") or d3.get("auth")
                if token:
                    v = requests.get(f"{base}/userinfo",
                                     params={"auth": token}, timeout=10).json()
                    if v.get("result") == 0:
                        return _build_session(v, token, "auth", eu, base)
                    v2 = requests.get(f"{base}/userinfo",
                                      params={"access_token": token}, timeout=10).json()
                    if v2.get("result") == 0:
                        return _build_session(v2, token, "access_token", eu, base)
            if d3.get("result") == 2000:
                raise RuntimeError("❌ Sai email hoặc mật khẩu pCloud.")
        except RuntimeError:
            raise
        except Exception as e:
            errors.append(f"{base} plain: {e}")

    raise RuntimeError(
        "❌ Không đăng nhập được pCloud.\n"
        f"Chi tiết: {'; '.join(errors) if errors else 'Unknown error'}"
    )


def _build_session(userinfo: dict, token: str, token_param: str, eu: bool, base: str) -> dict:
    return {
        "token":       token,
        "token_param": token_param,   # "auth" or "access_token"
        "eu":          eu,
        "base":        base,
        "email":       userinfo.get("email", ""),
        "quota":       userinfo.get("quota", 0),
        "usedquota":   userinfo.get("usedquota", 0),
    }


# ─────────────────────────────────────────────
# PCLOUD FILE API
# ─────────────────────────────────────────────
def _base(eu): return "https://eapi.pcloud.com" if eu else "https://api.pcloud.com"

def _auth_params(sess: dict) -> dict:
    """Return correct auth param based on what worked at login time."""
    return {sess["token_param"]: sess["token"]}

def _eu(sess): return sess.get("eu", False)

def pcloud_list_folder(sess, folder_id=0, path=None):
    params = _auth_params(sess)
    if path: params["path"]     = path
    else:    params["folderid"] = folder_id
    return requests.get(f"{_base(_eu(sess))}/listfolder", params=params, timeout=30).json()

def pcloud_get_file_link(sess, file_id):
    params = {**_auth_params(sess), "fileid": file_id}
    d = requests.get(f"{_base(_eu(sess))}/getfilelink", params=params, timeout=30).json()
    return f"https://{d['hosts'][0]}{d['path']}" if d.get("result") == 0 else None

def pcloud_upload_file(sess, folder_id, local_path, filename):
    params = {**_auth_params(sess), "folderid": folder_id, "filename": filename}
    with open(local_path, "rb") as f:
        return requests.post(
            f"{_base(_eu(sess))}/uploadfile",
            params=params,
            files={"file": (filename, f)},
            timeout=600,
        ).json()

def collect_videos(sess, folder_id, path_prefix="/"):
    VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
    res = pcloud_list_folder(sess, folder_id=folder_id)
    if res.get("result") != 0:
        return [], res.get("error", "Unknown error")
    videos = []
    for item in res["metadata"].get("contents", []):
        full_path = path_prefix.rstrip("/") + "/" + item["name"]
        if item.get("isfolder"):
            sub, _ = collect_videos(sess, item["folderid"], full_path)
            videos.extend(sub)
        elif Path(item["name"]).suffix.lower() in VIDEO_EXTS:
            videos.append({
                "name":           item["name"],
                "path":           full_path,
                "fileid":         item["fileid"],
                "size":           item.get("size", 0),
                "parentfolderid": item.get("parentfolderid", folder_id),
            })
    return videos, None

def download_video(url, dest, progress_cb=None):
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done  = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=512 * 1024):
                f.write(chunk); done += len(chunk)
                if progress_cb and total:
                    progress_cb(done / total)

# ─────────────────────────────────────────────
# GROQ WHISPER
# ─────────────────────────────────────────────
GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

def extract_audio_chunk(video_path, audio_path, start_sec=None, duration_sec=None):
    cmd = ["ffmpeg", "-y"]
    if start_sec    is not None: cmd += ["-ss", str(start_sec)]
    cmd += ["-i", video_path]
    if duration_sec is not None: cmd += ["-t", str(duration_sec)]
    cmd += ["-vn", "-ar", "16000", "-ac", "1", "-c:a", "mp3", "-b:a", "64k", audio_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"FFmpeg audio extract:\n{r.stderr[-1500:]}")

def get_video_duration(video_path):
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", video_path],
        capture_output=True, text=True,
    )
    return float(json.loads(r.stdout)["format"]["duration"])

def transcribe_chunk(groq_key, audio_path, offset_sec=0.0):
    with open(audio_path, "rb") as f:
        resp = requests.post(
            GROQ_STT_URL,
            headers={"Authorization": f"Bearer {groq_key}"},
            files={"file": (os.path.basename(audio_path), f, "audio/mpeg")},
            data={"model": "whisper-large-v3-turbo",
                  "response_format": "verbose_json", "language": "en"},
            timeout=120,
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Groq API {resp.status_code}: {resp.text[:500]}")
    segs = resp.json().get("segments", [])
    for s in segs:
        s["start"] += offset_sec
        s["end"]   += offset_sec
    return segs

def transcribe_full(groq_key, video_path, log_fn):
    duration = get_video_duration(video_path)
    log_fn(f"⏱️  Thời lượng: {duration/60:.1f} phút")
    CHUNK = 1200  # 20-min chunks
    all_segs, starts = [], list(range(0, int(duration), CHUNK))
    for idx, start in enumerate(starts):
        dur = min(CHUNK, duration - start)
        log_fn(f"🎙️  Chunk {idx+1}/{len(starts)}: {start/60:.1f}–{(start+dur)/60:.1f} phút")
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
            atmp = tf.name
        try:
            extract_audio_chunk(video_path, atmp, start_sec=start, duration_sec=dur)
            mb = os.path.getsize(atmp) / 1e6
            log_fn(f"   Audio: {mb:.1f} MB")
            if mb > 25:
                half = dur // 2
                for ss, sd in [(start, half), (start + half, dur - half)]:
                    extract_audio_chunk(video_path, atmp, start_sec=ss, duration_sec=sd)
                    all_segs.extend(transcribe_chunk(groq_key, atmp, offset_sec=ss))
            else:
                all_segs.extend(transcribe_chunk(groq_key, atmp, offset_sec=start))
        finally:
            os.path.exists(atmp) and os.unlink(atmp)
    log_fn(f"✅ Tổng: {len(all_segs)} segments", "success")
    return all_segs

# ─────────────────────────────────────────────
# SRT + FFMPEG BURN-IN
# ─────────────────────────────────────────────
def to_srt(segments):
    def fmt(t):
        h, r = divmod(t, 3600); m, s = divmod(r, 60)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{round((s - int(s)) * 1000):03d}"
    lines = []
    for i, seg in enumerate(segments, 1):
        lines += [str(i), f"{fmt(seg['start'])} --> {fmt(seg['end'])}", seg["text"].strip(), ""]
    return "\n".join(lines)

# ASS Alignment map: position name → ASS alignment number
ALIGN_MAP = {
    "Dưới giữa":  2,
    "Dưới trái":  1,
    "Dưới phải":  3,
    "Giữa màn":   5,
    "Trên giữa":  8,
    "Trên trái":  7,
    "Trên phải":  9,
}

def hex_to_ass(hex_color: str) -> str:
    """Convert #RRGGBB to ASS &H00BBGGRR format."""
    hex_color = hex_color.lstrip("#")
    r = hex_color[0:2]; g = hex_color[2:4]; b = hex_color[4:6]
    return f"&H00{b}{g}{r}".upper()

def build_ass_style(font_name, font_size, primary_hex, outline_hex,
                    bold, outline_width, shadow, margin_v, alignment) -> str:
    primary = hex_to_ass(primary_hex)
    outline = hex_to_ass(outline_hex)
    bold_val = -1 if bold else 0
    align_num = ALIGN_MAP.get(alignment, 2)
    return (
        f"FontName={font_name},FontSize={font_size},"
        f"PrimaryColour={primary},"
        f"OutlineColour={outline},"
        f"BackColour=&H80000000,"
        f"Bold={bold_val},"
        f"Outline={outline_width},"
        f"Shadow={shadow},"
        f"MarginV={margin_v},"
        f"Alignment={align_num}"
    )

def burn_subtitles(video_path, srt_path, output_path, log_fn, style_str=None):
    safe = srt_path.replace("\\", "/").replace(":", "\\:")
    if style_str is None:
        style_str = build_ass_style(
            font_name="Arial", font_size=18,
            primary_hex="#FFFFFF", outline_hex="#000000",
            bold=False, outline_width=2, shadow=1,
            margin_v=35, alignment="Dưới giữa"
        )
    cmd = ["ffmpeg", "-y", "-i", video_path,
           "-vf", f"subtitles={safe}:force_style='{style_str}'",
           "-c:v", "libx264", "-crf", "22", "-preset", "fast",
           "-c:a", "copy", output_path]
    log_fn("🔥 FFmpeg burn-in đang chạy…")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"FFmpeg:\n{r.stderr[-2000:]}")
    log_fn(f"✅ Xong: {os.path.getsize(output_path)/1e6:.1f} MB", "success")

# ─────────────────────────────────────────────
# BACKGROUND MUSIC
# ─────────────────────────────────────────────
def download_audio_url(url: str, dest: str):
    """Download audio from direct URL (.mp3/.wav/.m4a etc)."""
    headers = {"User-Agent": "Mozilla/5.0"}
    with requests.get(url, stream=True, timeout=120, headers=headers) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=256 * 1024):
                f.write(chunk)

def mix_background_music(video_path: str, music_path: str, output_path: str,
                          music_volume: float, log_fn):
    """
    Mix background music into video:
    - Loop music to match video duration
    - Mix with original audio at given volume (0.0–1.0)
    - Fade out last 3 seconds
    """
    log_fn("🎵 Mixing background music with FFmpeg…")
    fade_sec = 3

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-stream_loop", "-1", "-i", music_path,   # loop music infinitely
        "-filter_complex",
        (
            f"[1:a]volume={music_volume:.2f},"         # set bg volume
            f"apad[bg];"                               # pad if needed
            f"[0:a][bg]amix=inputs=2:duration=first:"  # mix, duration = video length
            f"dropout_transition=0,"                   # no dropout
            f"atrim=0:{get_video_duration(video_path):.3f}[aout];"  # trim to video
            f"[aout]afade=t=out:st={get_video_duration(video_path)-fade_sec:.3f}:d={fade_sec}[afinal]"
        ),
        "-map", "0:v",
        "-map", "[afinal]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg mix error:\n{result.stderr[-2000:]}")
    log_fn(f"✅ Music mixed: {os.path.getsize(output_path)/1e6:.1f} MB", "success")


# ─────────────────────────────────────────────
# YOUTUBE SHORTS
# ─────────────────────────────────────────────
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
SHORT_MAX_SEC = 60   # YouTube Shorts max duration
SHORT_MIN_SEC = 30   # minimum meaningful clip

def find_best_shorts(groq_key: str, transcript_segments: list,
                     video_duration: float, n_shorts: int = 3) -> list:
    """
    Send transcript to Groq LLM, ask it to pick the N best moments for Shorts.
    Returns list of {start, end, title, reason} dicts sorted by start time.
    """
    # Build transcript text with timestamps
    lines = []
    for seg in transcript_segments:
        t = int(seg["start"])
        lines.append(f"[{t//60:02d}:{t%60:02d}] {seg['text'].strip()}")
    transcript_text = "\n".join(lines)

    prompt = f"""You are a YouTube Shorts editor. Analyze this video transcript and find the {n_shorts} BEST moments to make into YouTube Shorts (vertical 9:16 clips).

RULES:
- Each clip must be {SHORT_MIN_SEC}–{SHORT_MAX_SEC} seconds long
- Pick moments that are: engaging, self-contained, have a clear point, emotionally resonant, or contain a key insight
- Clips must NOT overlap
- Video total duration: {video_duration:.0f} seconds ({video_duration/60:.1f} minutes)

TRANSCRIPT:
{transcript_text[:8000]}

Respond ONLY with valid JSON array, no markdown, no explanation:
[
  {{"start": <seconds>, "end": <seconds>, "title": "<short catchy title>", "reason": "<why this is great for Shorts>"}},
  ...
]"""

    resp = requests.post(
        GROQ_CHAT_URL,
        headers={"Authorization": f"Bearer {groq_key}",
                 "Content-Type": "application/json"},
        json={
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 1024,
        },
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Groq Chat API {resp.status_code}: {resp.text[:400]}")

    raw = resp.json()["choices"][0]["message"]["content"].strip()
    # Strip markdown fences if present
    raw = raw.replace("```json", "").replace("```", "").strip()
    clips = json.loads(raw)

    # Validate & clamp each clip
    validated = []
    for c in clips:
        start = max(0, float(c["start"]))
        end   = min(video_duration, float(c["end"]))
        dur   = end - start
        if dur < SHORT_MIN_SEC:
            end = min(video_duration, start + SHORT_MIN_SEC)
        if dur > SHORT_MAX_SEC:
            end = start + SHORT_MAX_SEC
        validated.append({
            "start":  round(start, 2),
            "end":    round(end,   2),
            "title":  c.get("title",  f"Clip {len(validated)+1}"),
            "reason": c.get("reason", ""),
        })

    # Sort by start time, remove overlaps
    validated.sort(key=lambda x: x["start"])
    final = []
    last_end = -1
    for c in validated:
        if c["start"] >= last_end:
            final.append(c)
            last_end = c["end"]
    return final[:n_shorts]


def crop_9_16(video_path: str, output_path: str,
              start: float, duration: float, log_fn):
    """
    Cut clip and crop to 9:16 vertical format (center crop).
    Uses FFmpeg crop filter: crop=in_h*9/16:in_h
    """
    log_fn(f"✂️  Cutting & cropping 9:16 ({start:.1f}s → {start+duration:.1f}s)…")
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", video_path,
        "-t", str(duration),
        # Center-crop to 9:16
        "-vf", "crop=min(iw\\,ih*9/16):ih:(iw-min(iw\\,ih*9/16))/2:0,scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(1080-iw)/2:(1920-ih)/2:black",
        "-c:v", "libx264", "-crf", "22", "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"FFmpeg crop error:\n{r.stderr[-2000:]}")
    log_fn(f"✅ Short ready: {os.path.getsize(output_path)/1e6:.1f} MB", "success")


def process_shorts(sess, groq_key, video_info, n_shorts, log_ph, prog_ph):
    """Full pipeline: download → transcribe → AI pick → crop → upload."""
    logs = []
    def log(msg, kind="info"):
        icon = {"info":"▸","success":"✔","error":"✖","warn":"⚠"}[kind]
        css  = {"info":"","success":" log-success","error":" log-error","warn":" log-warn"}[kind]
        logs.append(f'<div class="log-box{css}">{icon} {msg}</div>')
        log_ph.markdown("".join(logs), unsafe_allow_html=True)
    def prog(v, t=""):
        prog_ph.progress(min(v, 1.0), text=t)

    uploaded_clips = []

    with tempfile.TemporaryDirectory() as tmp:
        stem  = Path(video_info["name"]).stem
        ext   = Path(video_info["name"]).suffix.lower() or ".mp4"
        v_loc = os.path.join(tmp, video_info["name"])

        # 1. Download
        log(f"⬇️  Downloading {video_info['name']} ({video_info['size']/1e6:.1f} MB)…")
        try:
            url = pcloud_get_file_link(sess, video_info["fileid"])
            if not url: log("Không lấy được link", "error"); return []
            download_video(url, v_loc, lambda p: prog(p*0.20, f"Downloading {p*100:.0f}%"))
        except Exception as e:
            log(f"Download lỗi: {e}", "error"); return []
        log(f"Downloaded {os.path.getsize(v_loc)/1e6:.1f} MB", "success")

        duration = get_video_duration(v_loc)
        log(f"⏱️  Thời lượng: {duration/60:.1f} phút")
        prog(0.22, "Transcribing…")

        # 2. Transcribe (reuse existing function)
        try:
            segments = transcribe_full(groq_key, v_loc, lambda m, k="info": log(m, k))
        except Exception as e:
            log(f"Transcription lỗi: {e}", "error"); return []
        if not segments:
            log("Không phát hiện giọng nói", "warn"); return []
        prog(0.50, "AI đang phân tích transcript…")

        # 3. AI pick best moments
        log(f"🤖 Groq AI đang tìm {n_shorts} đoạn hay nhất…")
        try:
            clips = find_best_shorts(groq_key, segments, duration, n_shorts)
        except Exception as e:
            log(f"AI phân tích lỗi: {e}", "error"); return []

        log(f"✅ AI chọn được {len(clips)} đoạn:", "success")
        for i, c in enumerate(clips):
            log(f"   #{i+1} [{c['start']:.0f}s-{c['end']:.0f}s] {c['title']}")
        prog(0.60, "Cutting & cropping shorts…")

        # 4. Cut + crop each clip & upload
        fid = video_info["parentfolderid"]
        for i, clip in enumerate(clips):
            clip_name = f"{stem}_short{i+1}_{clip['title'][:30].replace(' ','_')}.mp4"
            clip_name = "".join(c for c in clip_name if c.isalnum() or c in "._-")
            out_loc   = os.path.join(tmp, clip_name)
            clip_dur  = clip["end"] - clip["start"]

            log(f"\n✂️  Short #{i+1}: {clip['title']} ({clip_dur:.0f}s)")
            try:
                crop_9_16(v_loc, out_loc, clip["start"], clip_dur,
                          lambda m, k="info": log(m, k))
            except Exception as e:
                log(f"Crop lỗi: {e}", "error"); continue

            prog(0.60 + (i+1)/len(clips)*0.35, f"Uploading short {i+1}/{len(clips)}…")
            log(f"⬆️  Uploading {clip_name}…")
            r = pcloud_upload_file(sess, fid, out_loc, clip_name)
            if r.get("result") != 0:
                log(f"Upload lỗi: {r.get('error','')}", "error")
            else:
                log(f"Uploaded ✔ → {clip_name}", "success")
                uploaded_clips.append({**clip, "filename": clip_name})

        prog(1.0, "Hoàn tất! 🎉")
    return uploaded_clips


# ─────────────────────────────────────────────
# PIPELINE
# ─────────────────────────────────────────────
def process_video(sess, groq_key, video_info, log_ph, prog_ph, style_str=None):
    logs = []
    def log(msg, kind="info"):
        icon = {"info": "▸", "success": "✔", "error": "✖", "warn": "⚠"}[kind]
        css  = {"info": "", "success": " log-success", "error": " log-error", "warn": " log-warn"}[kind]
        logs.append(f'<div class="log-box{css}">{icon} {msg}</div>')
        log_ph.markdown("".join(logs), unsafe_allow_html=True)
    def prog(v, t=""):
        prog_ph.progress(min(v, 1.0), text=t)

    with tempfile.TemporaryDirectory() as tmp:
        stem  = Path(video_info["name"]).stem
        ext   = Path(video_info["name"]).suffix.lower() or ".mp4"
        v_loc = os.path.join(tmp, video_info["name"])
        s_loc = os.path.join(tmp, f"{stem}.srt")
        o_loc = os.path.join(tmp, f"{stem}_captioned{ext}")

        # 1. Download
        log(f"⬇️  Downloading {video_info['name']} ({video_info['size']/1e6:.1f} MB)")
        try:
            url = pcloud_get_file_link(sess, video_info["fileid"])
            if not url: log("Không lấy được link download", "error"); return False
            download_video(url, v_loc, lambda p: prog(p * 0.20, f"Downloading {p*100:.0f}%"))
        except Exception as e:
            log(f"Download lỗi: {e}", "error"); return False
        log(f"Downloaded {os.path.getsize(v_loc)/1e6:.1f} MB", "success")
        prog(0.22, "Transcribing…")

        # 2. Transcribe
        try:
            segs = transcribe_full(groq_key, v_loc, lambda m, k="info": log(m, k))
        except Exception as e:
            log(f"Transcription lỗi: {e}", "error"); return False
        if not segs:
            log("Không phát hiện giọng nói", "warn"); return False
        prog(0.55, "Tạo SRT…")

        # 3. SRT
        srt_content = to_srt(segs)
        with open(s_loc, "w", encoding="utf-8") as f:
            f.write(srt_content)
        log(f"SRT: {len(segs)} segments", "success")
        prog(0.58, "Burn subtitle…")

        # 4. Burn-in
        try:
            burn_subtitles(v_loc, s_loc, o_loc, lambda m, k="info": log(m, k), style_str=style_str)
        except Exception as e:
            log(f"FFmpeg lỗi: {e}", "error"); return False
        prog(0.80, "Upload pCloud…")

        # 5 & 6. Upload
        fid = video_info["parentfolderid"]
        for fpath, fname, label in [
            (o_loc, f"{stem}_captioned{ext}", "Video"),
            (s_loc, f"{stem}.srt",            "SRT"),
        ]:
            log(f"⬆️  Uploading {fname}…")
            r = pcloud_upload_file(sess, fid, fpath, fname)
            if r.get("result") != 0:
                log(f"{label} upload lỗi: {r.get('error', '')}", "error" if label == "Video" else "warn")
                if label == "Video": return False
            else:
                log(f"{label} uploaded ✔", "success")

        prog(1.0, "Hoàn tất! 🎉")
        st.expander(f"📄 SRT — {video_info['name']}").code(
            srt_content[:3000] + ("…" if len(srt_content) > 3000 else ""), language="text"
        )
    return True

# ─────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────
st.title("🎬 pCloud Auto Caption")
st.caption("Groq Whisper · FFmpeg burn-in · pCloud — 100% online")

# ── LOGIN ─────────────────────────────────────
if "session" not in st.session_state:
    _, col, _ = st.columns([1, 1.3, 1])
    with col:
        st.markdown('<div class="login-card">', unsafe_allow_html=True)
        st.markdown("### 🔐 Đăng nhập")

        email    = st.text_input("📧 Email pCloud",    placeholder="you@example.com")
        password = st.text_input("🔑 Mật khẩu pCloud", type="password")
        groq_key = st.text_input(
            "🤖 Groq API Key", type="password", placeholder="gsk_…",
            help="Miễn phí tại console.groq.com — không cần thẻ",
        )

        if st.button("Đăng nhập", type="primary", use_container_width=True):
            if not all([email, password, groq_key]):
                st.error("Vui lòng điền đầy đủ 3 trường.")
            else:
                with st.spinner("Đang xác thực với pCloud…"):
                    try:
                        sess = pcloud_login(email.strip(), password)
                        sess["groq_key"] = groq_key.strip()
                        st.session_state["session"] = sess
                        st.rerun()
                    except RuntimeError as e:
                        st.error(str(e))
                    except Exception as e:
                        st.error(f"Lỗi không xác định: {e}")

        with st.expander("📌 Chưa có Groq API Key?"):
            st.markdown("""
1. Vào **[console.groq.com](https://console.groq.com)**
2. Sign up miễn phí — không cần thẻ tín dụng
3. **API Keys → Create new key** → Copy và paste vào đây
            """)
        st.markdown('</div>', unsafe_allow_html=True)
    st.stop()

# ── MAIN APP ──────────────────────────────────
sess     = st.session_state["session"]
groq_key = sess["groq_key"]

# Top bar
c1, c2 = st.columns([3, 1])
with c1:
    used  = sess["usedquota"] / 1e9
    total = sess["quota"] / 1e9
    eu    = sess.get("eu", False)
    st.markdown(
        f'<div class="user-badge">✅ {sess["email"]} &nbsp;·&nbsp; '
        f'{"EU 🇪🇺" if eu else "US 🇺🇸"} &nbsp;·&nbsp; '
        f'{used:.1f} / {total:.0f} GB</div>',
        unsafe_allow_html=True,
    )
with c2:
    if st.button("🚪 Đăng xuất", use_container_width=True):
        st.session_state.pop("session", None)
        st.session_state.pop("videos",  None)
        st.rerun()

st.divider()

# ── Shared: folder browser (used by both tabs) ─
with st.expander("📁 Quét video từ pCloud", expanded=True):
    col_fp, col_btn = st.columns([3, 1])
    with col_fp:
        folder_path = st.text_input(
            "Thư mục pCloud", value="/", label_visibility="collapsed",
            placeholder="/ hoặc /Videos hoặc /Courses",
            help="Để / để quét toàn bộ. Quét đệ quy tất cả sub-folder.",
        )
    with col_btn:
        scan_btn = st.button("🔍 Quét", use_container_width=True, type="primary")

    if scan_btn:
        with st.spinner("Đang quét thư mục…"):
            try:
                fp = folder_path.strip()
                if fp.lstrip("/").isdigit():
                    fid = int(fp.lstrip("/"))
                    videos, err = collect_videos(sess, fid, f"/id:{fid}")
                elif fp and fp != "/":
                    res = pcloud_list_folder(sess, path=fp)
                    if res.get("result") != 0:
                        st.error(f"pCloud: {res.get('error', res)}"); st.stop()
                    videos, err = collect_videos(sess, res["metadata"]["folderid"], fp)
                else:
                    videos, err = collect_videos(sess, 0, "/")
                if err: st.error(f"Lỗi: {err}")
                else:
                    st.session_state["videos"] = videos
                    st.success(f"Tìm thấy **{len(videos)}** video")
            except Exception as e:
                st.error(str(e))

    if "videos" in st.session_state:
        vids = st.session_state["videos"]
        if vids:
            for v in vids:
                mb    = v["size"] / 1e6
                color = "#f38ba8" if mb > 500 else "#a6e3a1"
                st.markdown(
                    f'<div class="video-card">'
                    f'<b>📹 {v["name"]}</b><br>'
                    f'<small style="color:#666">{v["path"]}</small><br>'
                    f'<small style="color:{color}">💾 {mb:.1f} MB</small>'
                    f'</div>', unsafe_allow_html=True,
                )

st.divider()

# ── Two feature tabs ───────────────────────────
tab_caption, tab_music, tab_shorts = st.tabs(["🎬 Auto Caption", "🎵 Background Music", "✂️ YouTube Shorts"])

# ════════════════════════════════════════════════
# TAB 1: AUTO CAPTION
# ════════════════════════════════════════════════
with tab_caption:
    left, right = st.columns([1, 1.6], gap="large")

    with left:
        st.subheader("🎨 Tuỳ chỉnh Caption")

        # ── Font ─────────────────────────────────
        with st.expander("🔤 Font chữ", expanded=True):
            FONTS = ["Arial", "Arial Black", "Helvetica", "Verdana", "Tahoma",
                     "Georgia", "Times New Roman", "Courier New",
                     "Impact", "Comic Sans MS", "Trebuchet MS"]
            font_name = st.selectbox("Font", FONTS, index=0)
            font_size = st.slider("Cỡ chữ", 10, 60, 20)
            font_bold = st.checkbox("In đậm (Bold)", value=False)

        # ── Màu sắc ──────────────────────────────
        with st.expander("🎨 Màu chữ & viền", expanded=True):
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown("**Màu chữ**")
                primary_color = st.color_picker("Màu chữ", "#FFFFFF", label_visibility="collapsed")
                st.markdown(
                    f'<div style="background:{primary_color};border-radius:6px;'
                    f'padding:6px 12px;text-align:center;font-weight:bold;'
                    f'color:{("#000" if primary_color in ["#FFFFFF","#FFFF00","#00FFFF"] else "#FFF")}">'
                    f'Aa {primary_color}</div>', unsafe_allow_html=True
                )
            with col_b:
                st.markdown("**Màu viền**")
                outline_color = st.color_picker("Màu viền", "#000000", label_visibility="collapsed")
                st.markdown(
                    f'<div style="background:{outline_color};border-radius:6px;'
                    f'padding:6px 12px;text-align:center;font-weight:bold;color:#FFF">'
                    f'Viền {outline_color}</div>', unsafe_allow_html=True
                )
            outline_width = st.slider("Độ dày viền", 0, 5, 2)
            shadow        = st.slider("Bóng đổ (Shadow)", 0, 3, 1)

        # ── Vị trí ───────────────────────────────
        with st.expander("📍 Vị trí caption", expanded=True):
            # Visual 3x3 position picker
            st.markdown("**Chọn vị trí:**")
            POS_GRID = [
                ["Trên trái",  "Trên giữa",  "Trên phải"],
                ["",           "Giữa màn",   ""],
                ["Dưới trái",  "Dưới giữa",  "Dưới phải"],
            ]
            if "cap_position" not in st.session_state:
                st.session_state["cap_position"] = "Dưới giữa"

            for row in POS_GRID:
                cols = st.columns(3)
                for ci, pos_label in enumerate(row):
                    if not pos_label:
                        continue
                    is_sel = st.session_state["cap_position"] == pos_label
                    btn_style = "primary" if is_sel else "secondary"
                    if cols[ci].button(pos_label, key=f"pos_{pos_label}",
                                       type=btn_style, use_container_width=True):
                        st.session_state["cap_position"] = pos_label
                        st.rerun()

            position  = st.session_state["cap_position"]
            margin_v  = st.slider("Khoảng cách mép (MarginV)", 0, 120, 35,
                                   help="Pixel cách mép trên/dưới")

        # ── Preview box ───────────────────────────
        st.markdown("**👁️ Preview style:**")
        bg_preview = "#222" if primary_color == "#000000" else "#111"
        st.markdown(
            f'<div style="background:{bg_preview};border-radius:10px;padding:1.5rem;'
            f'text-align:center;margin:0.5rem 0;">' 
            f'<span style="font-family:{font_name};font-size:{min(font_size,28)}px;'
            f'color:{primary_color};'
            f'font-weight:{"bold" if font_bold else "normal"};'
            f'-webkit-text-stroke: {outline_width}px {outline_color};'
            f'text-shadow: {shadow}px {shadow}px {shadow*2}px {outline_color};">' 
            f'Hello, this is a caption preview!' 
            f'</span></div>',
            unsafe_allow_html=True,
        )
        st.caption(f"📍 {position} · Font: {font_name} {font_size}px")

        st.markdown("---")

        # ── Video selector ────────────────────────
        st.subheader("📹 Chọn video")
        if "videos" not in st.session_state or not st.session_state["videos"]:
            st.info("Quét thư mục pCloud ở trên trước.")
        else:
            vids = st.session_state["videos"]
            opts = {f"{v['name']}  ({v['size']/1e6:.0f} MB)": v for v in vids}
            sel_keys = st.multiselect("Chọn video:", list(opts.keys()), key="cap_sel")
            selected = [opts[k] for k in sel_keys]
            if selected:
                total_mb = sum(v["size"]/1e6 for v in selected)
                st.info(f"Đã chọn **{len(selected)}** video · ~**{total_mb:.0f} MB**")
                if total_mb > 1500:
                    st.warning("⚠️ Dung lượng lớn — Streamlit Cloud giới hạn disk ~2 GB.")
                if st.button("🚀 Tạo Caption", type="primary", use_container_width=True):
                    st.session_state["cap_queue"] = {
                        "videos": selected,
                        "style":  build_ass_style(
                            font_name=font_name, font_size=font_size,
                            primary_hex=primary_color, outline_hex=outline_color,
                            bold=font_bold, outline_width=outline_width,
                            shadow=shadow, margin_v=margin_v, alignment=position,
                        ),
                    }

    with right:
        st.subheader("⚡ Tiến trình Caption")
        if "cap_queue" in st.session_state:
            cq       = st.session_state.pop("cap_queue")
            queue    = cq["videos"]
            cap_style = cq["style"]
            ok_cnt   = 0
            for i, video in enumerate(queue):
                st.markdown(f"#### [{i+1}/{len(queue)}] `{video['name']}`")
                ph_prog = st.empty()
                ph_log  = st.empty()
                if process_video(sess, groq_key, video, ph_log, ph_prog, style_str=cap_style):
                    ok_cnt += 1
                st.markdown("---")
            if ok_cnt == len(queue):
                st.balloons()
                st.success(f"🎉 {ok_cnt}/{len(queue)} video captioned & uploaded!")
            else:
                st.warning(f"⚠️ {ok_cnt}/{len(queue)} thành công.")
        else:
            st.markdown("""
            <div style="text-align:center;padding:3rem 1rem;color:#555">
                <div style="font-size:3rem">🎬</div>
                <div style="margin-top:0.8rem">Tuỳ chỉnh style, chọn video<br>và nhấn <b>Tạo Caption</b></div>
            </div>""", unsafe_allow_html=True)

# ════════════════════════════════════════════════
# TAB 2: BACKGROUND MUSIC
# ════════════════════════════════════════════════
with tab_music:
    left2, right2 = st.columns([1, 1.6], gap="large")

    with left2:
        st.subheader("Cài đặt nhạc nền")

        music_url = st.text_input(
            "🔗 Link audio nhạc nền",
            placeholder="https://example.com/music.mp3",
            help="Link trực tiếp đến file .mp3 / .wav / .m4a / .ogg",
        )

        music_vol = st.slider(
            "🔊 Âm lượng nhạc nền",
            min_value=0.0, max_value=1.0, value=0.15, step=0.05,
            format="%.2f",
            help="0.0 = tắt tiếng, 1.0 = full volume. Khuyến nghị: 0.10–0.20",
        )

        st.caption(f"Âm lượng nhạc nền: **{int(music_vol*100)}%** · Âm thanh gốc: **100%**")

        st.markdown("---")

        if "videos" not in st.session_state or not st.session_state["videos"]:
            st.info("Quét thư mục pCloud ở trên trước.")
        else:
            vids  = st.session_state["videos"]
            opts2 = {f"{v['name']}  ({v['size']/1e6:.0f} MB)": v for v in vids}
            sel2  = st.multiselect("Chọn video:", list(opts2.keys()), key="mus_sel")
            selected2 = [opts2[k] for k in sel2]

            if selected2:
                total_mb2 = sum(v["size"]/1e6 for v in selected2)
                st.info(f"Đã chọn **{len(selected2)}** video · ~**{total_mb2:.0f} MB**")

                if not music_url.strip():
                    st.warning("⚠️ Nhập link audio nhạc nền trước.")
                else:
                    if st.button("🎵 Thêm nhạc nền", type="primary", use_container_width=True):
                        st.session_state["music_queue"] = {
                            "videos":    selected2,
                            "music_url": music_url.strip(),
                            "music_vol": music_vol,
                        }

    with right2:
        st.subheader("⚡ Tiến trình Music")

        if "music_queue" in st.session_state:
            mq         = st.session_state.pop("music_queue")
            m_videos   = mq["videos"]
            m_url      = mq["music_url"]
            m_vol      = mq["music_vol"]
            ok_cnt2    = 0

            for i, video in enumerate(m_videos):
                st.markdown(f"#### [{i+1}/{len(m_videos)}] `{video['name']}`")
                ph_prog2 = st.empty()
                ph_log2  = st.empty()

                logs2 = []
                def log2(msg, kind="info"):
                    icon = {"info":"▸","success":"✔","error":"✖","warn":"⚠"}[kind]
                    css  = {"info":"","success":" log-success","error":" log-error","warn":" log-warn"}[kind]
                    logs2.append(f'<div class="log-box{css}">{icon} {msg}</div>')
                    ph_log2.markdown("".join(logs2), unsafe_allow_html=True)
                def prog2(v, t=""):
                    ph_prog2.progress(min(v, 1.0), text=t)

                with tempfile.TemporaryDirectory() as tmp:
                    stem     = Path(video["name"]).stem
                    ext      = Path(video["name"]).suffix.lower() or ".mp4"
                    v_loc    = os.path.join(tmp, video["name"])
                    aud_loc  = os.path.join(tmp, "bgmusic" + Path(m_url).suffix or ".mp3")
                    out_loc  = os.path.join(tmp, f"{stem}_music{ext}")

                    ok = True

                    # 1. Download video
                    log2(f"⬇️  Downloading video ({video['size']/1e6:.1f} MB)…")
                    try:
                        url = pcloud_get_file_link(sess, video["fileid"])
                        if not url: log2("Không lấy được link", "error"); ok = False
                        else: download_video(url, v_loc, lambda p: prog2(p*0.25, f"Downloading video {p*100:.0f}%"))
                    except Exception as e:
                        log2(f"Download video lỗi: {e}", "error"); ok = False

                    if ok:
                        log2(f"Downloaded {os.path.getsize(v_loc)/1e6:.1f} MB", "success")
                        prog2(0.28, "Downloading audio…")

                        # 2. Download audio
                        log2(f"⬇️  Downloading nhạc nền…")
                        try:
                            download_audio_url(m_url, aud_loc)
                            log2(f"Audio: {os.path.getsize(aud_loc)/1e6:.1f} MB", "success")
                        except Exception as e:
                            log2(f"Download audio lỗi: {e}", "error"); ok = False

                    if ok:
                        prog2(0.45, "Mixing audio…")

                        # 3. Mix
                        try:
                            mix_background_music(v_loc, aud_loc, out_loc, m_vol, lambda m, k="info": log2(m, k))
                        except Exception as e:
                            log2(f"Mix lỗi: {e}", "error"); ok = False

                    if ok:
                        prog2(0.80, "Uploading to pCloud…")

                        # 4. Upload
                        out_name = f"{stem}_music{ext}"
                        log2(f"⬆️  Uploading {out_name}…")
                        r = pcloud_upload_file(sess, video["parentfolderid"], out_loc, out_name)
                        if r.get("result") != 0:
                            log2(f"Upload lỗi: {r.get('error','')}", "error"); ok = False
                        else:
                            log2(f"Uploaded ✔ → pCloud/{video['path']}", "success")
                            prog2(1.0, "Hoàn tất! 🎉")
                            ok_cnt2 += 1

                st.markdown("---")

            if ok_cnt2 == len(m_videos):
                st.balloons()
                st.success(f"🎉 {ok_cnt2}/{len(m_videos)} video đã thêm nhạc nền & upload!")
            else:
                st.warning(f"⚠️ {ok_cnt2}/{len(m_videos)} thành công.")

        else:
            st.markdown("""
            <div style="text-align:center;padding:3rem 1rem;color:#555">
                <div style="font-size:3rem">🎵</div>
                <div style="margin-top:0.8rem">
                    Dán link nhạc nền, chọn video<br>và nhấn <b>Thêm nhạc nền</b>
                </div>
                <div style="margin-top:1rem;font-size:0.82rem;color:#444">
                    Mix giữ nguyên âm thanh gốc · Loop nhạc · Fade out cuối video
                </div>
            </div>""", unsafe_allow_html=True)

# ════════════════════════════════════════════════
# TAB 3: YOUTUBE SHORTS
# ════════════════════════════════════════════════
with tab_shorts:
    left3, right3 = st.columns([1, 1.6], gap="large")

    with left3:
        st.subheader("✂️ YouTube Shorts")
        st.caption("AI phân tích transcript → tìm đoạn hay nhất → crop 9:16 → upload pCloud")

        n_shorts = st.radio(
            "Số đoạn Short cần tạo",
            options=[1, 3, 5],
            index=1,
            horizontal=True,
        )

        st.markdown("---")

        # AI explanation card
        st.markdown("""
        <div style="background:#1e1e2e;border-left:4px solid #6C63FF;
             border-radius:8px;padding:0.9rem 1rem;margin-bottom:0.8rem">
            <b style="color:#cdd6f4">🤖 AI sẽ làm gì?</b><br>
            <small style="color:#888">
            1. Transcribe toàn bộ video bằng Groq Whisper<br>
            2. Gửi transcript cho Llama 3.3-70B phân tích<br>
            3. AI chọn đoạn có: insight hay, cảm xúc mạnh, self-contained<br>
            4. Cắt clip 30–60s, crop 9:16 (1080×1920)<br>
            5. Upload từng Short lên pCloud
            </small>
        </div>
        """, unsafe_allow_html=True)

        if "videos" not in st.session_state or not st.session_state["videos"]:
            st.info("Quét thư mục pCloud ở trên trước.")
        else:
            vids  = st.session_state["videos"]
            opts3 = {f"{v['name']}  ({v['size']/1e6:.0f} MB)": v for v in vids}
            sel3  = st.multiselect("Chọn video:", list(opts3.keys()), key="sh_sel")
            selected3 = [opts3[k] for k in sel3]

            if selected3:
                total_mb3 = sum(v["size"]/1e6 for v in selected3)
                st.info(f"Đã chọn **{len(selected3)}** video · ~**{total_mb3:.0f} MB**")

                # Estimate output
                est_clips = len(selected3) * n_shorts
                st.caption(f"Ước tính: ~{est_clips} Short clips sẽ được tạo")

                if st.button("🚀 Tạo YouTube Shorts", type="primary", use_container_width=True):
                    st.session_state["shorts_queue"] = {
                        "videos":   selected3,
                        "n_shorts": n_shorts,
                    }

    with right3:
        st.subheader("⚡ Tiến trình Shorts")

        if "shorts_queue" in st.session_state:
            sq       = st.session_state.pop("shorts_queue")
            s_videos = sq["videos"]
            s_n      = sq["n_shorts"]
            all_clips = []

            for i, video in enumerate(s_videos):
                st.markdown(f"#### [{i+1}/{len(s_videos)}] `{video['name']}`")
                ph_prog3 = st.empty()
                ph_log3  = st.empty()

                clips = process_shorts(sess, groq_key, video, s_n, ph_log3, ph_prog3)
                all_clips.extend(clips)
                st.markdown("---")

            if all_clips:
                st.balloons()
                st.success(f"🎉 Tạo được **{len(all_clips)}** YouTube Short clips!")
                st.markdown("### 📋 Danh sách Shorts đã upload:")
                for i, c in enumerate(all_clips):
                    dur = c["end"] - c["start"]
                    st.markdown(
                        f'<div class="video-card">' 
                        f'<b>#{i+1} {c["title"]}</b><br>' 
                        f'<small style="color:#a6e3a1">✔ {c["filename"]}</small><br>' 
                        f'<small style="color:#888">⏱ {dur:.0f}s &nbsp;·&nbsp; ' 
                        f'{c["start"]:.0f}s–{c["end"]:.0f}s từ video gốc</small><br>' 
                        f'<small style="color:#6C63FF;font-style:italic">{c["reason"]}</small>' 
                        f'</div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.warning("Không tạo được clip nào. Kiểm tra log bên trên.")

        else:
            st.markdown("""
            <div style="text-align:center;padding:3rem 1rem;color:#555">
                <div style="font-size:3rem">✂️</div>
                <div style="margin-top:0.8rem;font-size:1.05rem">
                    Chọn video và nhấn<br><b>Tạo YouTube Shorts</b>
                </div>
                <div style="margin-top:1rem;font-size:0.82rem;color:#444">
                    AI tự tìm đoạn hay nhất · Crop 9:16 · Upload pCloud
                </div>
            </div>""", unsafe_allow_html=True)
