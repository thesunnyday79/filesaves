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
# TEXT TO IMAGE  —  Hugging Face Inference API
# ─────────────────────────────────────────────
# ── OpenRouter Text-to-Image ──
# Uses /v1/chat/completions with modalities: ["image"]
OPENROUTER_T2I_API = "https://openrouter.ai/api/v1/chat/completions"

T2I_MODELS = {
    "FLUX.2 Schnell (Nhanh nhất, rẻ nhất)":          "black-forest-labs/flux-schnell",
    "FLUX.2 Flex (Linh hoạt, chất lượng tốt)":       "black-forest-labs/flux.2-flex",
    "FLUX.2 Pro (Chất lượng cao nhất)":               "black-forest-labs/flux.2-pro",
    "Gemini 2.5 Flash Image (Google)":                "google/gemini-2.5-flash-image-preview",
    "Gemini 3.1 Flash Image Preview (Google mới)":    "google/gemini-3.1-flash-image-preview",
}

ASPECT_RATIOS = {
    "16:9  Landscape": "16:9",
    "9:16  Portrait":  "9:16",
    "1:1   Square":    "1:1",
    "4:3   Classic":   "4:3",
}

def generate_images(model_id: str,
                    prompt: str,
                    negative_prompt: str,
                    aspect_ratio: str,
                    n_images: int = 2,
                    or_api_key: str = "",
                    seed_base: int = 42) -> list:
    """
    Generate images via OpenRouter API.
    Uses /v1/chat/completions with modalities: ["image"]
    Returns list of raw PNG/JPEG bytes.
    """
    import base64, time

    headers = {
        "Authorization":  f"Bearer {or_api_key}",
        "Content-Type":   "application/json",
        "HTTP-Referer":   "https://pcloud-autocaption.streamlit.app",
        "X-Title":        "pCloud Auto Caption",
    }

    results = []
    for i in range(n_images):
        payload = {
            "model": model_id,
            "messages": [
                {
                    "role":    "user",
                    "content": prompt,
                }
            ],
            "modalities": ["image"],
            "image_config": {
                "aspect_ratio": aspect_ratio,
            },
        }

        resp = requests.post(
            OPENROUTER_T2I_API,
            headers=headers,
            json=payload,
            timeout=120,
        )

        if resp.status_code != 200:
            raise RuntimeError(
                f"OpenRouter error {resp.status_code}: {resp.text[:400]}"
            )

        data = resp.json()

        # Extract image from response
        # OpenRouter returns image in content[].image_url or content[].data
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError(f"No choices in response: {str(data)[:300]}")

        content_parts = choices[0].get("message", {}).get("content", [])

        # content can be string or list
        if isinstance(content_parts, str):
            raise RuntimeError("Model returned text only — no image generated")

        img_bytes = None
        for part in content_parts:
            if isinstance(part, dict):
                if part.get("type") == "image_url":
                    img_url = part.get("image_url", {}).get("url", "")
                    if img_url.startswith("data:image"):
                        b64 = img_url.split(",", 1)[1]
                        img_bytes = base64.b64decode(b64)
                    elif img_url.startswith("http"):
                        dl = requests.get(img_url, timeout=60)
                        dl.raise_for_status()
                        img_bytes = dl.content
                elif part.get("type") == "image":
                    b64 = part.get("data", "")
                    if b64:
                        img_bytes = base64.b64decode(b64)

        if img_bytes is None:
            raise RuntimeError(
                f"Không tìm thấy ảnh trong response. Keys: {str(content_parts)[:300]}"
            )

        results.append(img_bytes)

    return results


def upload_image_to_pcloud(sess: dict, folder_id: int,
                            img_bytes: bytes, filename: str) -> dict:
    return requests.post(
        f"{_base(sess.get('eu', False))}/uploadfile",
        params={**_auth_params(sess), "folderid": folder_id, "filename": filename},
        files={"file": (filename, img_bytes, "image/png")},
        timeout=120,
    ).json()


# ─────────────────────────────────────────────
# ADD LOGO TO VIDEO
# ─────────────────────────────────────────────
def process_logo_with_pil(logo_path: str, out_png: str,
                           logo_width: int, remove_bg: bool = True):
    """
    Use Pillow to:
    1. Resize logo to logo_width px (keep aspect ratio)
    2. Convert to RGBA
    3. Optionally remove near-white/light background using corner sampling
    Save as PNG with transparency.
    """
    from PIL import Image
    import numpy as np

    img = Image.open(logo_path).convert("RGBA")
    # Resize keeping aspect ratio
    orig_w, orig_h = img.size
    new_h = int(logo_width * orig_h / orig_w)
    img = img.resize((logo_width, new_h), Image.LANCZOS)

    if remove_bg:
        data = np.array(img)
        # Sample 5x5 corner to get bg color
        corner = data[:5, :5, :3]
        bg_r = int(corner[:,:,0].mean())
        bg_g = int(corner[:,:,1].mean())
        bg_b = int(corner[:,:,2].mean())

        # For each pixel: if close to bg color → transparent
        r, g, b, a = data[:,:,0], data[:,:,1], data[:,:,2], data[:,:,3]
        dist = (r.astype(int)-bg_r)**2 + (g.astype(int)-bg_g)**2 + (b.astype(int)-bg_b)**2
        threshold = 50**2  # distance² threshold
        mask = dist < threshold
        data[mask, 3] = 0   # set alpha=0 (transparent)
        img = Image.fromarray(data)

    img.save(out_png, "PNG")
    return img.size  # (w, h)


def add_logo_to_video(video_path: str, logo_png: str, output_path: str,
                      position: str, margin: int, log_fn):
    """
    Overlay pre-processed transparent PNG logo onto video via FFmpeg.
    """
    log_fn(f"🏷️  Overlaying logo ({position}, margin={margin}px)…")

    pos_map = {
        "Góc trên trái":  f"x={margin}:y={margin}",
        "Góc trên phải":  f"x=W-w-{margin}:y={margin}",
        "Góc dưới trái":  f"x={margin}:y=H-h-{margin}",
        "Góc dưới phải":  f"x=W-w-{margin}:y=H-h-{margin}",
        "Giữa màn hình":  "x=(W-w)/2:y=(H-h)/2",
    }
    overlay = pos_map.get(position, f"x={margin}:y={margin}")

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", logo_png,
        "-filter_complex", f"[0:v][1:v]overlay={overlay}[out]",
        "-map", "[out]",
        "-map", "0:a?",
        "-c:v", "libx264", "-crf", "22", "-preset", "fast",
        "-c:a", "copy",
        output_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"FFmpeg overlay error:\n{r.stderr[-2000:]}")
    log_fn(f"✅ Done: {os.path.getsize(output_path)/1e6:.1f} MB", "success")


def process_logo_video(sess, video_info, logo_url,
                       position, logo_width, margin,
                       remove_bg, log_ph, prog_ph):
    logs = []
    def log(msg, kind="info"):
        icon = {"info":"▸","success":"✔","error":"✖","warn":"⚠"}[kind]
        css  = {"info":"","success":" log-success","error":" log-error","warn":" log-warn"}[kind]
        logs.append(f'<div class="log-box{css}">{icon} {msg}</div>')
        log_ph.markdown("".join(logs), unsafe_allow_html=True)
    def prog(v, t=""):
        prog_ph.progress(min(v, 1.0), text=t)

    with tempfile.TemporaryDirectory() as tmp:
        stem     = Path(video_info["name"]).stem
        ext      = Path(video_info["name"]).suffix.lower() or ".mp4"
        v_loc    = os.path.join(tmp, video_info["name"])
        logo_raw = os.path.join(tmp, "logo_raw" + Path(logo_url).suffix.split("?")[0] or ".jpg")
        logo_png = os.path.join(tmp, "logo_clean.png")
        out_loc  = os.path.join(tmp, f"{stem}_logo{ext}")

        # 1. Download logo
        log(f"⬇️  Downloading logo…")
        try:
            r = requests.get(logo_url, timeout=30, headers={"User-Agent":"Mozilla/5.0"})
            r.raise_for_status()
            with open(logo_raw, "wb") as f:
                f.write(r.content)
            log(f"Logo downloaded ({len(r.content)//1024} KB)", "success")
        except Exception as e:
            log(f"Download logo lỗi: {e}", "error"); return False
        prog(0.10, "Processing logo…")

        # 2. PIL: resize + remove bg → clean PNG
        log(f"🖼️  Processing logo: {logo_width}px, remove_bg={remove_bg}…")
        try:
            w, h = process_logo_with_pil(logo_raw, logo_png, logo_width, remove_bg)
            log(f"Logo processed: {w}×{h}px", "success")
        except Exception as e:
            log(f"PIL lỗi: {e}", "error"); return False
        prog(0.15, "Downloading video…")

        # 3. Download video
        log(f"⬇️  Downloading {video_info['name']} ({video_info['size']/1e6:.1f} MB)…")
        try:
            url = pcloud_get_file_link(sess, video_info["fileid"])
            if not url: log("Không lấy được link", "error"); return False
            download_video(url, v_loc, lambda p: prog(0.15 + p*0.55, f"Downloading {p*100:.0f}%"))
        except Exception as e:
            log(f"Download video lỗi: {e}", "error"); return False
        log(f"Downloaded {os.path.getsize(v_loc)/1e6:.1f} MB", "success")
        prog(0.72, "Adding logo…")

        # 4. FFmpeg overlay
        try:
            add_logo_to_video(v_loc, logo_png, out_loc, position, margin,
                              lambda m, k="info": log(m, k))
        except Exception as e:
            log(f"Overlay lỗi: {e}", "error"); return False
        prog(0.85, "Uploading to pCloud…")

        # 5. Upload
        out_name = f"{stem}_logo{ext}"
        log(f"⬆️  Uploading {out_name}…")
        r = pcloud_upload_file(sess, video_info["parentfolderid"], out_loc, out_name)
        if r.get("result") != 0:
            log(f"Upload lỗi: {r.get('error','')}", "error"); return False
        log(f"Uploaded ✔", "success")
        prog(1.0, "Hoàn tất! 🎉")
    return True


# ════════════════════════════════════════════════
# TAB 5: ADD LOGO
# ════════════════════════════════════════════════
with tab_logo:
    st.subheader("🏷️ Add Logo / Watermark")
    st.caption("Thêm logo nhỏ vào góc video · Tự động xoá nền · Lưu pCloud")

    left_l, right_l = st.columns([1, 1.6], gap="large")

    with left_l:
        # ── Logo URL ──────────────────────────────
        logo_url = st.text_input(
            "🔗 Link URL logo",
            placeholder="https://example.com/logo.png",
            help="PNG có nền trong suốt tốt nhất. JPG/PNG có nền màu cũng được — tự xoá nền.",
        )
        if logo_url.strip():
            try:
                st.image(logo_url.strip(), width=120, caption="Preview")
            except:
                st.warning("Không load được ảnh")

        st.markdown("---")

        # ── Size ─────────────────────────────────
        st.markdown("**📏 Kích thước logo**")
        logo_width = st.number_input(
            "Width (px)", min_value=10, max_value=500,
            value=30, step=5,
            help="Chiều rộng logo tính bằng pixel. 30 = nhỏ gọn, 60 = trung bình",
        )

        # ── Position ─────────────────────────────
        st.markdown("**📍 Vị trí**")
        POS_GRID_LOGO = [
            ["Góc trên trái",  "",              "Góc trên phải"],
            ["",               "Giữa màn hình", ""             ],
            ["Góc dưới trái",  "",              "Góc dưới phải"],
        ]
        if "logo_pos" not in st.session_state:
            st.session_state["logo_pos"] = "Góc trên trái"

        for row in POS_GRID_LOGO:
            cols_p = st.columns(3)
            for ci, label in enumerate(row):
                if not label: continue
                is_sel = st.session_state["logo_pos"] == label
                if cols_p[ci].button(
                    label, key=f"lp_{label}",
                    type="primary" if is_sel else "secondary",
                    use_container_width=True,
                ):
                    st.session_state["logo_pos"] = label
                    st.rerun()

        logo_pos = st.session_state["logo_pos"]
        margin   = st.slider("Khoảng cách mép (px)", 0, 80, 0, 2)

        # ── Options ───────────────────────────────
        st.markdown("**⚙️ Tuỳ chọn**")
        remove_bg = st.toggle(
            "Tự động xoá nền logo",
            value=True,
            help="Phát hiện màu nền từ góc logo và xoá → trong suốt",
        )

        st.markdown("---")

        # ── Video selector ────────────────────────
        st.markdown("**📹 Chọn video**")
        if "videos" not in st.session_state or not st.session_state["videos"]:
            st.info("Quét thư mục pCloud ở trên trước.")
        else:
            vids  = st.session_state["videos"]
            opts  = {f"{v['name']}  ({v['size']/1e6:.0f} MB)": v for v in vids}
            sel_l = st.multiselect("Chọn video:", list(opts.keys()), key="logo_sel")
            selected_l = [opts[k] for k in sel_l]

            if selected_l:
                st.info(f"Đã chọn **{len(selected_l)}** video")
                if not logo_url.strip():
                    st.warning("⚠️ Nhập URL logo trước")
                elif st.button("🏷️ Thêm Logo", type="primary", use_container_width=True):
                    st.session_state["logo_queue"] = {
                        "videos":     selected_l,
                        "logo_url":   logo_url.strip(),
                        "position":   logo_pos,
                        "width":      int(logo_width),
                        "margin":     margin,
                        "remove_bg":  remove_bg,
                    }

    with right_l:
        st.subheader("⚡ Tiến trình")

        if "logo_queue" in st.session_state:
            lq     = st.session_state.pop("logo_queue")
            ok_cnt = 0

            for i, video in enumerate(lq["videos"]):
                st.markdown(f"#### [{i+1}/{len(lq['videos'])}] `{video['name']}`")
                ph_prog = st.empty()
                ph_log  = st.empty()
                ok = process_logo_video(
                    sess, video,
                    lq["logo_url"],
                    lq["position"],
                    lq["width"],
                    lq["margin"],
                    lq["remove_bg"],
                    ph_log, ph_prog,
                )
                if ok: ok_cnt += 1
                st.markdown("---")

            if ok_cnt == len(lq["videos"]):
                st.balloons()
                st.success(f"🎉 {ok_cnt}/{len(lq['videos'])} video đã thêm logo!")
            else:
                st.warning(f"⚠️ {ok_cnt}/{len(lq['videos'])} thành công.")
        else:
            st.markdown("""
            <div style="text-align:center;padding:4rem 1rem;color:#555">
                <div style="font-size:3.5rem">🏷️</div>
                <div style="margin-top:1rem;font-size:1.05rem">
                    Nhập URL logo · chọn vị trí<br>
                    chọn video · nhấn <b>Thêm Logo</b>
                </div>
                <div style="margin-top:1rem;font-size:0.82rem;color:#444">
                    Width mặc định 30px · sát góc · tự xoá nền
                </div>
            </div>""", unsafe_allow_html=True)
