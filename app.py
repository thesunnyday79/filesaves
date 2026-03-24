import streamlit as st
import requests
import os
import tempfile
import subprocess
import time
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
      padding: 1rem;
      margin-bottom: 0.5rem;
  }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────
# PCLOUD API
# ─────────────────────────────────────────────
def get_pcloud_base(eu: bool):
    return "https://eapi.pcloud.com" if eu else "https://api.pcloud.com"

def pcloud_list_folder(token, folder_id=0, path=None, eu=False):
    base = get_pcloud_base(eu)
    params = {"access_token": token}
    if path:
        params["path"] = path
    else:
        params["folderid"] = folder_id
    r = requests.get(f"{base}/listfolder", params=params, timeout=30)
    return r.json()

def pcloud_get_file_link(token, file_id, eu=False):
    base = get_pcloud_base(eu)
    r = requests.get(f"{base}/getfilelink",
                     params={"access_token": token, "fileid": file_id}, timeout=30)
    d = r.json()
    if d.get("result") == 0:
        return f"https://{d['hosts'][0]}{d['path']}"
    return None

def pcloud_upload_file(token, folder_id, local_path, filename, eu=False):
    base = get_pcloud_base(eu)
    with open(local_path, "rb") as f:
        r = requests.post(
            f"{base}/uploadfile",
            params={"access_token": token, "folderid": folder_id, "filename": filename},
            files={"file": (filename, f)},
            timeout=600,
        )
    return r.json()

def collect_videos(token, folder_id, path_prefix="/", eu=False):
    VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
    res = pcloud_list_folder(token, folder_id=folder_id, eu=eu)
    if res.get("result") != 0:
        return [], res.get("error", "Unknown error")
    videos = []
    for item in res["metadata"].get("contents", []):
        full_path = path_prefix.rstrip("/") + "/" + item["name"]
        if item.get("isfolder"):
            sub, _ = collect_videos(token, item["folderid"], full_path, eu)
            videos.extend(sub)
        elif Path(item["name"]).suffix.lower() in VIDEO_EXTS:
            videos.append({
                "name": item["name"],
                "path": full_path,
                "fileid": item["fileid"],
                "size": item.get("size", 0),
                "parentfolderid": item.get("parentfolderid", folder_id),
            })
    return videos, None

def download_video(url, dest, progress_cb=None):
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=512 * 1024):
                f.write(chunk)
                done += len(chunk)
                if progress_cb and total:
                    progress_cb(done / total)

# ─────────────────────────────────────────────
# GROQ WHISPER API
# ─────────────────────────────────────────────
GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MAX_MB   = 25  # Groq free limit per request

def extract_audio_chunk(video_path, audio_path, start_sec=None, duration_sec=None):
    cmd = ["ffmpeg", "-y"]
    if start_sec is not None:
        cmd += ["-ss", str(start_sec)]
    cmd += ["-i", video_path]
    if duration_sec is not None:
        cmd += ["-t", str(duration_sec)]
    cmd += ["-vn", "-ar", "16000", "-ac", "1", "-c:a", "mp3", "-b:a", "64k", audio_path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg audio extract error:\n{result.stderr[-1500:]}")

def get_video_duration(video_path):
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", video_path
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    import json
    data = json.loads(r.stdout)
    return float(data["format"]["duration"])

def transcribe_chunk(groq_key, audio_path, offset_sec=0.0):
    """Call Groq Whisper API, return segments with offset applied."""
    with open(audio_path, "rb") as f:
        response = requests.post(
            GROQ_STT_URL,
            headers={"Authorization": f"Bearer {groq_key}"},
            files={"file": (os.path.basename(audio_path), f, "audio/mpeg")},
            data={
                "model": "whisper-large-v3-turbo",
                "response_format": "verbose_json",
                "language": "en",
            },
            timeout=120,
        )
    if response.status_code != 200:
        raise RuntimeError(f"Groq API error {response.status_code}: {response.text[:500]}")
    data = response.json()
    segments = data.get("segments", [])
    # Apply time offset for chunked processing
    for seg in segments:
        seg["start"] += offset_sec
        seg["end"]   += offset_sec
    return segments

def transcribe_full(groq_key, video_path, log_fn):
    """
    Smart chunking: split video into ≤20-min chunks to stay under 25MB Groq limit.
    Returns merged segments list.
    """
    duration = get_video_duration(video_path)
    log_fn(f"⏱️  Video duration: {duration/60:.1f} min")

    CHUNK_SEC = 1200  # 20-minute chunks
    all_segments = []
    starts = [i for i in range(0, int(duration), CHUNK_SEC)]

    for idx, start in enumerate(starts):
        chunk_dur = min(CHUNK_SEC, duration - start)
        log_fn(f"🎙️  Transcribing chunk {idx+1}/{len(starts)} "
               f"({start/60:.1f}–{(start+chunk_dur)/60:.1f} min)…")
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
            audio_tmp = tf.name
        try:
            extract_audio_chunk(video_path, audio_tmp, start_sec=start, duration_sec=chunk_dur)
            size_mb = os.path.getsize(audio_tmp) / 1e6
            log_fn(f"   Audio chunk: {size_mb:.1f} MB")
            if size_mb > GROQ_MAX_MB:
                log_fn("⚠️  Chunk still >25MB, splitting further…", "warn")
                # halve the duration and retry
                half = chunk_dur // 2
                for sub_start, sub_dur in [(start, half), (start+half, chunk_dur-half)]:
                    extract_audio_chunk(video_path, audio_tmp, start_sec=sub_start, duration_sec=sub_dur)
                    segs = transcribe_chunk(groq_key, audio_tmp, offset_sec=sub_start)
                    all_segments.extend(segs)
            else:
                segs = transcribe_chunk(groq_key, audio_tmp, offset_sec=start)
                all_segments.extend(segs)
        finally:
            if os.path.exists(audio_tmp):
                os.unlink(audio_tmp)

    log_fn(f"✅ Total segments: {len(all_segments)}", "success")
    return all_segments

# ─────────────────────────────────────────────
# SRT + FFMPEG BURN-IN
# ─────────────────────────────────────────────
def segments_to_srt(segments):
    def fmt(t):
        h, rem = divmod(t, 3600)
        m, s = divmod(rem, 60)
        ms = round((s - int(s)) * 1000)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}"
    lines = []
    for i, seg in enumerate(segments, 1):
        lines += [str(i), f"{fmt(seg['start'])} --> {fmt(seg['end'])}", seg["text"].strip(), ""]
    return "\n".join(lines)

def burn_subtitles(video_path, srt_path, output_path, log_fn):
    safe_srt = srt_path.replace("\\", "/").replace(":", "\\:")
    font_style = (
        "FontName=Arial,FontSize=18,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BackColour=&H80000000,"
        "Outline=2,Shadow=1,MarginV=35,Alignment=2"
    )
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", f"subtitles={safe_srt}:force_style='{font_style}'",
        "-c:v", "libx264", "-crf", "22", "-preset", "fast",
        "-c:a", "copy",
        output_path
    ]
    log_fn("🔥 Running FFmpeg burn-in (this may take a while)…")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg burn-in error:\n{result.stderr[-2000:]}")
    log_fn(f"✅ Burn-in done: {os.path.getsize(output_path)/1e6:.1f} MB", "success")

# ─────────────────────────────────────────────
# MAIN PROCESS PIPELINE
# ─────────────────────────────────────────────
def process_one_video(token, groq_key, video_info, eu, log_placeholder, prog_placeholder):
    logs = []

    def log(msg, kind="info"):
        icon = {"info": "▸", "success": "✔", "error": "✖", "warn": "⚠"}[kind]
        css  = {"info": "", "success": " log-success", "error": " log-error", "warn": " log-warn"}[kind]
        logs.append(f'<div class="log-box{css}">{icon} {msg}</div>')
        log_placeholder.markdown("".join(logs), unsafe_allow_html=True)

    def prog(val, label=""):
        prog_placeholder.progress(min(val, 1.0), text=label)

    with tempfile.TemporaryDirectory() as tmp:
        stem = Path(video_info["name"]).stem
        ext  = Path(video_info["name"]).suffix.lower() or ".mp4"
        video_local  = os.path.join(tmp, video_info["name"])
        srt_local    = os.path.join(tmp, f"{stem}.srt")
        output_local = os.path.join(tmp, f"{stem}_captioned{ext}")

        # 1. Download
        log(f"⬇️  Downloading: {video_info['name']} ({video_info['size']/1e6:.1f} MB)")
        try:
            url = pcloud_get_file_link(token, video_info["fileid"], eu)
            if not url:
                log("Cannot get download link from pCloud", "error"); return False
            download_video(url, video_local, lambda p: prog(p * 0.20, f"Downloading… {p*100:.0f}%"))
        except Exception as e:
            log(f"Download failed: {e}", "error"); return False
        log(f"Downloaded {os.path.getsize(video_local)/1e6:.1f} MB", "success")
        prog(0.22, "Transcribing with Groq Whisper…")

        # 2. Transcribe
        try:
            segments = transcribe_full(groq_key, video_local, lambda m, k="info": log(m, k))
        except Exception as e:
            log(f"Transcription failed: {e}", "error"); return False
        if not segments:
            log("No speech detected in video", "warn"); return False
        prog(0.55, "Generating SRT…")

        # 3. SRT
        srt_content = segments_to_srt(segments)
        with open(srt_local, "w", encoding="utf-8") as f:
            f.write(srt_content)
        log(f"SRT created: {len(segments)} segments", "success")
        prog(0.58, "Burning subtitles with FFmpeg…")

        # 4. Burn-in
        try:
            burn_subtitles(video_local, srt_local, output_local, lambda m, k="info": log(m, k))
        except Exception as e:
            log(f"FFmpeg burn-in failed: {e}", "error"); return False
        prog(0.80, "Uploading to pCloud…")

        # 5. Upload captioned video
        folder_id = video_info["parentfolderid"]
        log(f"⬆️  Uploading {stem}_captioned{ext} to pCloud…")
        r = pcloud_upload_file(token, folder_id, output_local, f"{stem}_captioned{ext}", eu)
        if r.get("result") != 0:
            log(f"Video upload failed: {r.get('error','')}", "error"); return False
        log(f"Captioned video uploaded ✔", "success")
        prog(0.92, "Uploading SRT…")

        # 6. Upload SRT
        r2 = pcloud_upload_file(token, folder_id, srt_local, f"{stem}.srt", eu)
        if r2.get("result") != 0:
            log(f"SRT upload failed: {r2.get('error','')}", "warn")
        else:
            log(f"SRT uploaded ✔", "success")
        prog(1.0, "Done!")

        # Preview
        st.expander(f"📄 SRT Preview — {video_info['name']}").code(
            srt_content[:3000] + ("…" if len(srt_content) > 3000 else ""), language="text"
        )
    return True

# ─────────────────────────────────────────────
# UI LAYOUT
# ─────────────────────────────────────────────
st.title("🎬 pCloud Auto Caption")
st.caption("Groq Whisper API · FFmpeg burn-in · pCloud storage — 100% online")

# ── Sidebar ──────────────────────────────────
with st.sidebar:
    st.header("🔑 API Keys & Config")

    pcloud_token = st.text_input(
        "pCloud Access Token", type="password",
        help="my.pcloud.com → Settings → Security → Access Tokens"
    )
    groq_key = st.text_input(
        "Groq API Key", type="password",
        help="console.groq.com → API Keys (free, no credit card needed)"
    )
    eu_dc = st.toggle("EU datacenter (eapi.pcloud.com)", value=False)
    folder_path = st.text_input("pCloud folder path", value="/",
                                 placeholder="/Videos hoặc /Projects/Course")

    st.divider()
    with st.expander("📖 Hướng dẫn lấy API keys"):
        st.markdown("""
**pCloud Token:**
1. Vào [my.pcloud.com](https://my.pcloud.com)
2. Settings → Security → Access Tokens
3. Create Token → Copy

**Groq API Key (miễn phí):**
1. Vào [console.groq.com](https://console.groq.com)
2. Đăng ký miễn phí (không cần thẻ)
3. API Keys → Create new key
        """)
    with st.expander("ℹ️ Giới hạn Groq miễn phí"):
        st.markdown("""
| Limit | Giá trị |
|-------|---------|
| Audio/request | 25 MB |
| Requests/phút | 20 |
| Audio/giờ | 7,200 giây |
| Audio/ngày | 28,800 giây (~8h) |

App tự động chia nhỏ video > 20 phút thành nhiều chunk.
        """)

# ── Main ─────────────────────────────────────
if not pcloud_token or not groq_key:
    st.info("👈 Nhập **pCloud Token** và **Groq API Key** ở sidebar để bắt đầu.")
    st.stop()

col_left, col_right = st.columns([1, 1.6], gap="large")

with col_left:
    st.subheader("📁 Chọn video")
    if st.button("🔍 Quét video từ pCloud", use_container_width=True, type="primary"):
        with st.spinner("Đang quét…"):
            try:
                if folder_path and folder_path != "/":
                    res = pcloud_list_folder(pcloud_token, path=folder_path, eu=eu_dc)
                    if res.get("result") != 0:
                        st.error(f"Lỗi pCloud: {res.get('error', res)}")
                        st.stop()
                    fid = res["metadata"]["folderid"]
                    videos, err = collect_videos(pcloud_token, fid, folder_path, eu_dc)
                else:
                    videos, err = collect_videos(pcloud_token, 0, "/", eu_dc)
                if err:
                    st.error(f"Lỗi: {err}")
                else:
                    st.session_state["videos"] = videos
            except Exception as e:
                st.error(f"Lỗi kết nối pCloud: {e}")

    if "videos" in st.session_state:
        videos = st.session_state["videos"]
        if not videos:
            st.warning("Không tìm thấy video nào trong thư mục này.")
        else:
            st.success(f"Tìm thấy **{len(videos)}** video")
            st.markdown("---")
            for v in videos:
                size_mb = v["size"] / 1e6
                size_color = "#f38ba8" if size_mb > 500 else "#a6e3a1"
                st.markdown(
                    f'<div class="video-card">'
                    f'<b>📹 {v["name"]}</b><br>'
                    f'<small style="color:#888">{v["path"]}</small><br>'
                    f'<small style="color:{size_color}">{size_mb:.1f} MB</small>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            options = {f"{v['name']} ({v['size']/1e6:.0f} MB)": v for v in videos}
            selected_keys = st.multiselect(
                "Chọn video cần tạo caption:",
                list(options.keys()),
                help="Có thể chọn nhiều video — app xử lý tuần tự"
            )
            selected = [options[k] for k in selected_keys]

            total_mb = sum(v["size"]/1e6 for v in selected)
            if selected:
                st.info(f"Đã chọn **{len(selected)}** video · Tổng ~**{total_mb:.0f} MB**")
                if total_mb > 1500:
                    st.warning("⚠️ Tổng dung lượng lớn — Streamlit Community Cloud có giới hạn disk tạm ~2GB.")

                if st.button("🚀 Bắt đầu tạo Caption", type="primary", use_container_width=True):
                    st.session_state["queue"] = selected

with col_right:
    st.subheader("⚡ Tiến trình xử lý")

    if "queue" in st.session_state:
        queue = st.session_state.pop("queue")
        total = len(queue)
        done_ok = 0

        for i, video in enumerate(queue):
            st.markdown(f"#### [{i+1}/{total}] `{video['name']}`")
            prog_ph = st.empty()
            log_ph  = st.empty()
            ok = process_one_video(
                pcloud_token, groq_key, video, eu_dc, log_ph, prog_ph
            )
            if ok:
                done_ok += 1
            st.markdown("---")

        if done_ok == total:
            st.balloons()
            st.success(f"🎉 Hoàn tất! {done_ok}/{total} video đã được caption và upload lên pCloud.")
        else:
            st.warning(f"⚠️ {done_ok}/{total} video thành công. Kiểm tra log bên trên.")
    else:
        st.markdown("""
        <div style="text-align:center; padding:4rem 1rem; color:#555">
            <div style="font-size:3.5rem">🎬</div>
            <div style="font-size:1.1rem; margin-top:1rem">
                Quét video ở bên trái<br>chọn video và nhấn <b>Bắt đầu tạo Caption</b>
            </div>
            <div style="margin-top:2rem; font-size:0.85rem; color:#444">
                Groq Whisper large-v3-turbo · FFmpeg burn-in · pCloud upload
            </div>
        </div>
        """, unsafe_allow_html=True)
