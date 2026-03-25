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
# PCLOUD AUTH
# Correct flow (per official docs + PyCloud lib):
#   Step 1: GET /getdigest            → { digest }
#   Step 2: GET /userinfo?passworddigest=SHA1(pass + SHA1(lower(user)) + digest)
# ─────────────────────────────────────────────
def _pcloud_try_login(base: str, username: str, password: str):
    """
    Try login against one base URL.
    Returns (session_dict, debug_info) on success,
            (None, debug_info) on wrong server,
            raises RuntimeError on bad password.
    """
    # Step 1 — get fresh digest
    r1 = requests.get(f"{base}/getdigest", timeout=15)
    d1 = r1.json()
    debug = {"base": base, "getdigest_result": d1.get("result"), "has_digest": "digest" in d1}

    if d1.get("result") != 0 or "digest" not in d1:
        return None, debug

    digest = d1["digest"]
    debug["digest_prefix"] = digest[:20]

    # Step 2 — compute password digest
    # Official PyCloud formula: SHA1( pw_bytes + SHA1(lower(user))_hexbytes + digest_bytes )
    sha_user  = hashlib.sha1(username.lower().encode("utf-8")).hexdigest().encode("utf-8")
    pw_digest = hashlib.sha1(
        password.encode("utf-8") + sha_user + digest.encode("utf-8")
    ).hexdigest()
    debug["pw_digest_prefix"] = pw_digest[:10]

    # Step 3 — authenticate
    r2 = requests.get(
        f"{base}/userinfo",
        params={
            "getauth":        1,
            "logout":         1,
            "username":       username,
            "digest":         digest,
            "passworddigest": pw_digest,
            "authexpire":     0,
        },
        timeout=15,
    )
    d2 = r2.json()
    debug["userinfo_result"] = d2.get("result")
    debug["userinfo_error"]  = d2.get("error", "none")
    debug["userinfo_keys"]   = list(d2.keys())

    if d2.get("result") == 0:
        # pCloud returns "token" or "auth" depending on server/account type
        token = d2.get("token") or d2.get("auth")
        if token:
            # Verify token actually works
            verify = requests.get(
                f"{base}/userinfo",
                params={"auth": token},
                timeout=10,
            ).json()
            debug["verify_result"] = verify.get("result")
            debug["verify_error"]  = verify.get("error", "none")
            if verify.get("result") == 0:
                return {
                    "token":     token,
                    "email":     verify.get("email", d2.get("email", username)),
                    "quota":     verify.get("quota", d2.get("quota", 0)),
                    "usedquota": verify.get("usedquota", d2.get("usedquota", 0)),
                }, debug

    if d2.get("result") == 2000:
        raise RuntimeError(f"❌ Sai email hoặc mật khẩu pCloud.\n\nDebug: {debug}")

    return None, debug


def pcloud_login(username: str, password: str) -> dict:
    """
    Auto-detect US vs EU datacenter, return session dict.
    Raises RuntimeError on auth failure.
    """
    all_debug = []
    for eu, base in [(False, "https://api.pcloud.com"),
                     (True,  "https://eapi.pcloud.com")]:
        try:
            result, dbg = _pcloud_try_login(base, username, password)
            all_debug.append(dbg)
        except RuntimeError:
            # Wrong password — but only stop if we haven't succeeded on another DC
            all_debug.append({"base": base, "bad_password": True})
            continue
        except Exception as ex:
            all_debug.append({"base": base, "exception": str(ex)})
            continue

        if result is not None:
            result["eu"] = eu
            return result

    # Check if any DC returned result=0 but we missed the token
    for dbg in all_debug:
        if dbg.get("userinfo_result") == 0:
            raise RuntimeError(
                "❌ pCloud login thành công nhưng không lấy được token.\n"
                f"Keys nhận được: {dbg.get('userinfo_keys', [])}"
            )

    # Check if we got bad_password signal
    if any(d.get("bad_password") for d in all_debug):
        raise RuntimeError("❌ Sai email hoặc mật khẩu pCloud.")

    raise RuntimeError(
        "❌ Không kết nối được pCloud (đã thử US + EU). Kiểm tra lại mạng."
    )


# ─────────────────────────────────────────────
# PCLOUD FILE API
# ─────────────────────────────────────────────
def _base(eu): return "https://eapi.pcloud.com" if eu else "https://api.pcloud.com"

def _auth_params(token: str) -> dict:
    """
    pCloud getauth login returns an 'auth' session token (not OAuth access_token).
    Must be passed as 'auth' param, NOT 'access_token'.
    """
    return {"auth": token}

def pcloud_list_folder(token, folder_id=0, path=None, eu=False):
    params = _auth_params(token)
    if path: params["path"]     = path
    else:    params["folderid"] = folder_id
    return requests.get(f"{_base(eu)}/listfolder", params=params, timeout=30).json()

def pcloud_get_file_link(token, file_id, eu=False):
    params = {**_auth_params(token), "fileid": file_id}
    d = requests.get(f"{_base(eu)}/getfilelink", params=params, timeout=30).json()
    return f"https://{d['hosts'][0]}{d['path']}" if d.get("result") == 0 else None

def pcloud_upload_file(token, folder_id, local_path, filename, eu=False):
    params = {**_auth_params(token), "folderid": folder_id, "filename": filename}
    with open(local_path, "rb") as f:
        return requests.post(
            f"{_base(eu)}/uploadfile",
            params=params,
            files={"file": (filename, f)},
            timeout=600,
        ).json()

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

def burn_subtitles(video_path, srt_path, output_path, log_fn):
    safe  = srt_path.replace("\\", "/").replace(":", "\\:")
    style = ("FontName=Arial,FontSize=18,PrimaryColour=&H00FFFFFF,"
             "OutlineColour=&H00000000,BackColour=&H80000000,"
             "Outline=2,Shadow=1,MarginV=35,Alignment=2")
    cmd = ["ffmpeg", "-y", "-i", video_path,
           "-vf", f"subtitles={safe}:force_style='{style}'",
           "-c:v", "libx264", "-crf", "22", "-preset", "fast",
           "-c:a", "copy", output_path]
    log_fn("🔥 FFmpeg burn-in đang chạy…")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"FFmpeg:\n{r.stderr[-2000:]}")
    log_fn(f"✅ Xong: {os.path.getsize(output_path)/1e6:.1f} MB", "success")

# ─────────────────────────────────────────────
# PIPELINE
# ─────────────────────────────────────────────
def process_video(token, groq_key, video_info, eu, log_ph, prog_ph):
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
            url = pcloud_get_file_link(token, video_info["fileid"], eu)
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
            burn_subtitles(v_loc, s_loc, o_loc, lambda m, k="info": log(m, k))
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
            r = pcloud_upload_file(token, fid, fpath, fname, eu)
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
token    = sess["token"]
eu       = sess["eu"]
groq_key = sess["groq_key"]

# Top bar
c1, c2 = st.columns([3, 1])
with c1:
    used  = sess["usedquota"] / 1e9
    total = sess["quota"] / 1e9
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
left, right = st.columns([1, 1.6], gap="large")

# ── LEFT: Browse & select ─────────────────────
with left:
    st.subheader("📁 Chọn video")
    folder_path = st.text_input(
        "Thư mục pCloud", value="/",
        help="Nhập path VD: /Videos  hoặc để / để quét toàn bộ. Không cần nhập folder ID số.",
    )

    if st.button("🔍 Quét video", use_container_width=True, type="primary"):
        with st.spinner("Đang quét…"):
            try:
                fp = folder_path.strip()
                # Auto-detect: numeric string = folderid, otherwise = path
                if fp.lstrip("/").isdigit():
                    # User entered a numeric folder ID directly
                    fid = int(fp.lstrip("/"))
                    videos, err = collect_videos(token, fid, f"/id:{fid}", eu)
                elif fp and fp != "/":
                    res = pcloud_list_folder(token, path=fp, eu=eu)
                    if res.get("result") != 0:
                        st.error(f"pCloud: {res.get('error', res)}")
                        st.stop()
                    videos, err = collect_videos(token, res["metadata"]["folderid"], fp, eu)
                else:
                    videos, err = collect_videos(token, 0, "/", eu)

                if err: st.error(f"Lỗi: {err}")
                else:   st.session_state["videos"] = videos
            except Exception as e:
                st.error(str(e))

    if "videos" in st.session_state:
        vids = st.session_state["videos"]
        if not vids:
            st.warning("Không tìm thấy video nào.")
        else:
            st.success(f"Tìm thấy **{len(vids)}** video")
            st.markdown("---")
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
            st.markdown("---")
            opts     = {f"{v['name']}  ({v['size']/1e6:.0f} MB)": v for v in vids}
            sel_keys = st.multiselect("Chọn video cần caption:", list(opts.keys()))
            selected = [opts[k] for k in sel_keys]

            if selected:
                total_mb = sum(v["size"] / 1e6 for v in selected)
                st.info(f"Đã chọn **{len(selected)}** video · ~**{total_mb:.0f} MB**")
                if total_mb > 1500:
                    st.warning("⚠️ Dung lượng lớn — Streamlit Cloud giới hạn disk tạm ~2 GB.")
                if st.button("🚀 Bắt đầu tạo Caption", type="primary", use_container_width=True):
                    st.session_state["queue"] = selected

# ── RIGHT: Progress ───────────────────────────
with right:
    st.subheader("⚡ Tiến trình")

    if "queue" in st.session_state:
        queue  = st.session_state.pop("queue")
        ok_cnt = 0
        for i, video in enumerate(queue):
            st.markdown(f"#### [{i+1}/{len(queue)}] `{video['name']}`")
            ph_prog = st.empty()
            ph_log  = st.empty()
            if process_video(token, groq_key, video, eu, ph_log, ph_prog):
                ok_cnt += 1
            st.markdown("---")
        if ok_cnt == len(queue):
            st.balloons()
            st.success(f"🎉 Xong! {ok_cnt}/{len(queue)} video đã upload lên pCloud.")
        else:
            st.warning(f"⚠️ {ok_cnt}/{len(queue)} thành công.")
    else:
        st.markdown("""
        <div style="text-align:center;padding:4rem 1rem;color:#555">
            <div style="font-size:3.5rem">🎬</div>
            <div style="margin-top:1rem;font-size:1.05rem">
                Quét và chọn video ở bên trái<br>
                rồi nhấn <b>Bắt đầu tạo Caption</b>
            </div>
            <div style="margin-top:1.5rem;font-size:0.82rem;color:#444">
                Groq Whisper large-v3-turbo · FFmpeg burn-in · pCloud upload
            </div>
        </div>""", unsafe_allow_html=True)
