"""
app.py — Inworld Text-to-Speech Studio
Nhập text → chọn giọng → tạo audio → nghe & tải về

Chạy:
    streamlit run app.py
"""

import base64
import os
import time
from pathlib import Path

import requests
import streamlit as st
from dotenv import load_dotenv

# ─── Load env ─────────────────────────────────────────────────────────────────
load_dotenv(Path(__file__).parent / ".env")

TTS_URL        = "https://api.inworld.ai/tts/v1/voice"
LIST_VOICE_URL = "https://api.inworld.ai/tts/v1/voices"

TTS_MODELS = {
    "TTS 1.5 Max (chất lượng cao nhất)": "inworld-tts-1.5-max",
    "TTS 1.5 Mini (nhanh hơn)":          "inworld-tts-1.5-mini",
    "TTS 1 Max":                          "inworld-tts-1-max",
    "TTS 1":                              "inworld-tts-1",
}

AUDIO_FORMATS = {
    "WAV (LINEAR16)": ("LINEAR16", "audio/wav",  ".wav"),
    "MP3":            ("MP3",      "audio/mpeg", ".mp3"),
    "OGG Opus":       ("OGG_OPUS", "audio/ogg",  ".ogg"),
}

# Danh sách giọng dự phòng nếu API không load được
FALLBACK_VOICES = [
    ("Alex",    "Nam · Năng động, biểu cảm"),
    ("Ashley",  "Nữ · Ấm áp, tự nhiên"),
    ("Dennis",  "Nam · Điềm tĩnh, thân thiện"),
    ("Jordan",  "Trung tính · Chuyên nghiệp"),
    ("Nova",    "Nữ · Trẻ trung, tươi sáng"),
    ("Echo",    "Nam · Sâu lắng"),
    ("Fable",   "Nữ · Kể chuyện"),
    ("Onyx",    "Nam · Uy quyền"),
    ("Shimmer", "Nữ · Nhẹ nhàng"),
    ("Alloy",   "Trung tính · Cân bằng"),
]

# ─── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Inworld TTS Studio",
    page_icon="🔊",
    layout="wide",
)

# ─── CSS ──────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
[data-testid="stAppViewContainer"] { background: #0f172a; }
[data-testid="stSidebar"]          { background: #1e293b; border-right: 1px solid #334155; }

h1, h2, h3, h4, label, p, div, span { color: #e2e8f0; }
.stMarkdown p { color: #e2e8f0 !important; }

/* Text area */
textarea {
    background: #1e293b !important;
    color: #f1f5f9 !important;
    border: 1px solid #475569 !important;
    border-radius: 10px !important;
    font-size: 1rem !important;
    line-height: 1.6 !important;
}
textarea:focus { border-color: #38bdf8 !important; box-shadow: 0 0 0 2px #0ea5e933 !important; }

/* Sidebar inputs */
[data-testid="stSidebar"] input,
[data-testid="stSidebar"] select,
[data-testid="stSidebar"] textarea {
    background: #0f172a !important;
    color: #e2e8f0 !important;
    border: 1px solid #475569 !important;
}

/* Buttons */
.stButton > button {
    background: linear-gradient(135deg, #0ea5e9, #6366f1) !important;
    color: white !important;
    border: none !important;
    border-radius: 10px !important;
    font-size: 1.05rem !important;
    font-weight: 700 !important;
    padding: 12px 32px !important;
    width: 100% !important;
    transition: opacity .2s !important;
}
.stButton > button:hover   { opacity: .88 !important; }
.stButton > button:disabled { opacity: .4 !important; }

/* Char counter */
.char-ok  { color: #86efac; font-size: .82rem; }
.char-warn { color: #fbbf24; font-size: .82rem; }
.char-over { color: #f87171; font-size: .82rem; font-weight: 700; }

/* Voice card */
.voice-card {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 12px;
    padding: 14px 16px;
    margin-bottom: 10px;
    cursor: pointer;
    transition: border-color .2s;
}
.voice-card:hover  { border-color: #38bdf8; }
.voice-card.active { border-color: #0ea5e9; background: #0c4a6e33; }
.voice-name  { font-weight: 700; color: #e2e8f0; font-size: .95rem; }
.voice-desc  { color: #94a3b8; font-size: .8rem; margin-top: 2px; }
.voice-tags  { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 4px; }
.tag { background: #334155; color: #94a3b8; padding: 2px 8px; border-radius: 20px; font-size: .72rem; }

/* Result box */
.result-box {
    background: #1e293b;
    border: 1px solid #0ea5e9;
    border-radius: 12px;
    padding: 20px;
    margin-top: 16px;
}
.result-title { color: #38bdf8; font-weight: 700; font-size: 1rem; margin-bottom: 10px; }

/* Badges */
.badge { padding: 3px 12px; border-radius: 20px; font-size: .78rem; font-weight: 600; }
.badge-ok  { background: #14532d; color: #86efac; }
.badge-err { background: #7f1d1d; color: #fca5a5; }
.badge-na  { background: #1e293b; color: #64748b; border: 1px solid #334155; }

/* History item */
.history-item {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 10px;
    padding: 12px 14px;
    margin-bottom: 8px;
}
.history-text { color: #cbd5e1; font-size: .88rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.history-meta { color: #475569; font-size: .72rem; margin-top: 4px; }

hr { border-color: #334155 !important; }
</style>
""", unsafe_allow_html=True)

# ─── Session state ────────────────────────────────────────────────────────────
if "api_key"      not in st.session_state: st.session_state.api_key      = os.environ.get("INWORLD_API_KEY", "")
if "voices"       not in st.session_state: st.session_state.voices       = []
if "history"      not in st.session_state: st.session_state.history      = []   # [{text, voice, audio, time}]
if "last_audio"   not in st.session_state: st.session_state.last_audio   = None
if "last_fmt_ext" not in st.session_state: st.session_state.last_fmt_ext = ".wav"


# ─── Helpers ──────────────────────────────────────────────────────────────────

def auth_header(key: str) -> dict:
    return {"Authorization": f"Basic {key}", "Content-Type": "application/json"}


@st.cache_data(ttl=300, show_spinner=False)
def fetch_voices(api_key: str) -> list[dict]:
    """Tải danh sách giọng từ API (cache 5 phút)."""
    try:
        r = requests.get(LIST_VOICE_URL, headers={"Authorization": f"Basic {api_key}"}, timeout=10)
        r.raise_for_status()
        return r.json().get("voices", [])
    except Exception:
        return []


def synthesize(text: str, voice_id: str, model_id: str, encoding: str, speed: float, api_key: str) -> bytes:
    """Gọi TTS API, trả về bytes audio."""
    payload = {
        "text": text,
        "voiceId": voice_id,
        "modelId": model_id,
        "audioConfig": {
            "audioEncoding": encoding,
            "sampleRateHertz": 22050,
            "speakingRate": speed,
        },
        "applyTextNormalization": "ON",
    }
    r = requests.post(TTS_URL, headers=auth_header(api_key), json=payload, timeout=40)
    r.raise_for_status()
    b64 = r.json().get("audioContent", "")
    if not b64:
        raise ValueError("API không trả về audio content.")
    return base64.b64decode(b64)


# ─── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ Cấu hình")
    st.divider()

    # API Key
    st.markdown("### 🔑 API Key")
    key_input = st.text_input(
        "Inworld API Key",
        value=st.session_state.api_key,
        type="password",
        placeholder="Dán Base64 API Key...",
        label_visibility="collapsed",
    )
    if key_input:
        st.session_state.api_key = key_input

    api_key = st.session_state.api_key
    if api_key:
        st.markdown('<span class="badge badge-ok">✓ Đã nhập API Key</span>', unsafe_allow_html=True)
        # Load voices
        if not st.session_state.voices:
            with st.spinner("Đang tải danh sách giọng..."):
                loaded = fetch_voices(api_key)
                st.session_state.voices = loaded if loaded else [
                    {"voiceId": v[0], "displayName": v[0], "description": v[1], "tags": []}
                    for v in FALLBACK_VOICES
                ]
    else:
        st.markdown('<span class="badge badge-na">✗ Chưa có API Key</span>', unsafe_allow_html=True)

    st.divider()

    # TTS Model
    st.markdown("### 🤖 Model TTS")
    model_label = st.selectbox("Model", list(TTS_MODELS.keys()), index=0, label_visibility="collapsed")
    selected_model = TTS_MODELS[model_label]

    st.divider()

    # Audio format
    st.markdown("### 🎵 Định dạng Audio")
    fmt_label = st.selectbox("Format", list(AUDIO_FORMATS.keys()), index=0, label_visibility="collapsed")
    audio_encoding, audio_mime, audio_ext = AUDIO_FORMATS[fmt_label]

    st.divider()

    # Tốc độ
    st.markdown("### ⚡ Tốc độ giọng nói")
    speed = st.slider("Speed", 0.5, 2.0, 1.0, 0.05, label_visibility="collapsed",
                      format="%.2fx")

    st.divider()

    # Lịch sử
    st.markdown("### 📋 Lịch sử tạo")
    if st.session_state.history:
        for i, h in enumerate(reversed(st.session_state.history[-6:])):
            st.markdown(
                f"<div class='history-item'>"
                f"<div class='history-text'>🔊 {h['text'][:60]}{'…' if len(h['text'])>60 else ''}</div>"
                f"<div class='history-meta'>{h['voice']} · {h['time']}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
        if st.button("🗑️ Xóa lịch sử", use_container_width=True):
            st.session_state.history = []
            st.rerun()
    else:
        st.markdown("<div style='color:#475569;font-size:.82rem'>Chưa có audio nào.</div>", unsafe_allow_html=True)

    st.markdown(
        "<div style='margin-top:24px;color:#334155;font-size:.72rem;text-align:center'>"
        "Inworld AI TTS · <a href='https://docs.inworld.ai/tts/tts' target='_blank' style='color:#38bdf8'>Docs</a></div>",
        unsafe_allow_html=True,
    )


# ─── Main ─────────────────────────────────────────────────────────────────────
st.markdown(
    "<h1 style='text-align:center;background:linear-gradient(90deg,#38bdf8,#818cf8);-webkit-background-clip:text;"
    "-webkit-text-fill-color:transparent;margin-bottom:4px'>🔊 Inworld TTS Studio</h1>"
    "<p style='text-align:center;color:#64748b;margin-bottom:0'>Nhập văn bản · Chọn giọng · Tạo audio</p>",
    unsafe_allow_html=True,
)
st.divider()

col_left, col_right = st.columns([3, 2], gap="large")

# ── Cột trái: Nhập text + tạo audio ──────────────────────────────────────────
with col_left:
    st.markdown("### ✍️ Văn bản")

    text_input = st.text_area(
        "Nhập văn bản cần chuyển thành giọng nói",
        value="Xin chào! Tôi là trợ lý giọng nói được tạo bởi Inworld AI. Rất vui được gặp bạn.",
        height=180,
        max_chars=2000,
        label_visibility="collapsed",
        placeholder="Nhập văn bản tại đây... (tối đa 2000 ký tự)",
    )

    # Char counter
    char_count = len(text_input)
    if char_count < 1500:
        cls, icon = "char-ok", "✓"
    elif char_count < 2000:
        cls, icon = "char-warn", "⚠"
    else:
        cls, icon = "char-over", "✗"
    st.markdown(f'<div class="{cls}">{icon} {char_count} / 2000 ký tự</div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Chọn giọng
    st.markdown("### 🎙️ Chọn giọng nói")

    voices = st.session_state.voices
    if not voices:
        voices = [{"voiceId": v[0], "displayName": v[0], "description": v[1], "tags": []} for v in FALLBACK_VOICES]

    # Lọc giọng
    col_search, col_filter = st.columns([2, 1])
    with col_search:
        search_q = st.text_input("🔍 Tìm giọng", placeholder="Tên giọng...", label_visibility="collapsed")
    with col_filter:
        gender_filter = st.selectbox("Lọc", ["Tất cả", "male", "female"], label_visibility="collapsed")

    filtered_voices = [
        v for v in voices
        if (search_q.lower() in v.get("displayName", "").lower() or not search_q)
        and (gender_filter == "Tất cả" or gender_filter in v.get("tags", []))
    ]

    # Danh sách giọng dạng radio đẹp
    voice_options = [v["voiceId"] for v in filtered_voices]
    voice_labels  = {
        v["voiceId"]: f"**{v.get('displayName', v['voiceId'])}** — {v.get('description', '')[:60]}"
        for v in filtered_voices
    }

    if voice_options:
        selected_voice = st.radio(
            "Giọng",
            options=voice_options,
            format_func=lambda x: voice_labels.get(x, x),
            label_visibility="collapsed",
            horizontal=False,
        )
    else:
        st.warning("Không tìm thấy giọng phù hợp.")
        selected_voice = "Dennis"

    st.markdown("<br>", unsafe_allow_html=True)

    # Nút tạo
    can_generate = bool(api_key) and bool(text_input.strip()) and char_count <= 2000
    generate_btn = st.button(
        "🎙️ Tạo giọng nói" if can_generate else ("⚠️ Nhập API Key trước" if not api_key else "✍️ Nhập văn bản"),
        disabled=not can_generate,
    )

# ── Cột phải: Kết quả ────────────────────────────────────────────────────────
with col_right:
    st.markdown("### 🎧 Kết quả")

    result_placeholder = st.empty()

    # Hiển thị audio gần nhất nếu có
    if st.session_state.last_audio:
        with result_placeholder.container():
            st.markdown(
                "<div class='result-box'><div class='result-title'>✅ Audio sẵn sàng</div></div>",
                unsafe_allow_html=True,
            )
            st.audio(st.session_state.last_audio, format=audio_mime)
            st.download_button(
                label=f"⬇️ Tải về ({audio_ext})",
                data=st.session_state.last_audio,
                file_name=f"inworld_tts_{int(time.time())}{st.session_state.last_fmt_ext}",
                mime=audio_mime,
                use_container_width=True,
            )
    else:
        result_placeholder.markdown(
            "<div style='text-align:center;color:#334155;padding:60px 20px'>"
            "<div style='font-size:3rem'>🔇</div>"
            "<div style='margin-top:10px;color:#475569'>Audio sẽ xuất hiện ở đây</div>"
            "</div>",
            unsafe_allow_html=True,
        )

    # Thông tin model đang chọn
    st.divider()
    st.markdown("**📊 Cấu hình hiện tại**")
    st.markdown(f"""
| Thông số | Giá trị |
|---|---|
| Model | `{selected_model}` |
| Giọng | `{selected_voice if voice_options else 'Dennis'}` |
| Tốc độ | `{speed:.2f}x` |
| Định dạng | `{audio_ext}` |
""")


# ─── Xử lý tạo audio ─────────────────────────────────────────────────────────
if generate_btn:
    with st.spinner("⏳ Đang tạo giọng nói..."):
        try:
            audio_bytes = synthesize(
                text=text_input.strip(),
                voice_id=selected_voice,
                model_id=selected_model,
                encoding=audio_encoding,
                speed=speed,
                api_key=api_key,
            )

            st.session_state.last_audio   = audio_bytes
            st.session_state.last_fmt_ext = audio_ext

            # Lưu lịch sử
            st.session_state.history.append({
                "text":  text_input.strip(),
                "voice": selected_voice,
                "model": selected_model,
                "audio": audio_bytes,
                "time":  time.strftime("%H:%M"),
            })

            st.success(f"✅ Tạo thành công! {len(audio_bytes):,} bytes · giọng **{selected_voice}**")
            st.rerun()

        except requests.HTTPError as e:
            code = e.response.status_code
            if code == 401:
                st.error("❌ API Key không hợp lệ (401). Kiểm tra lại trong sidebar.")
            elif code == 429:
                st.error("❌ Đã vượt rate limit (429). Vui lòng thử lại sau.")
            else:
                st.error(f"❌ Lỗi API {code}: {e.response.text[:200]}")
        except Exception as e:
            st.error(f"❌ Lỗi: {e}")
