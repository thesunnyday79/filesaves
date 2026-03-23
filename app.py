"""
app.py — Inworld TTS Studio
API Key được đọc từ .env hoặc Streamlit Secrets — không hiển thị trong UI.

Chạy local:
    streamlit run app.py

Deploy Streamlit Cloud:
    Thêm secret: INWORLD_API_KEY = "your_key"
"""

import base64
import os
import time
from pathlib import Path

import requests
import streamlit as st
from dotenv import load_dotenv

# ─── Config ───────────────────────────────────────────────────────────────────
load_dotenv(Path(__file__).parent / ".env")

TTS_URL        = "https://api.inworld.ai/tts/v1/voice"
LIST_VOICE_URL = "https://api.inworld.ai/tts/v1/voices"

TTS_MODELS = {
    "⚡ TTS 1.5 Max — Chất lượng cao nhất": "inworld-tts-1.5-max",
    "🚀 TTS 1.5 Mini — Nhanh hơn":          "inworld-tts-1.5-mini",
    "🎯 TTS 1 Max":                          "inworld-tts-1-max",
    "🎵 TTS 1":                              "inworld-tts-1",
}

AUDIO_FORMATS = {
    "WAV": ("LINEAR16", "audio/wav",  ".wav"),
    "MP3": ("MP3",      "audio/mpeg", ".mp3"),
    "OGG": ("OGG_OPUS", "audio/ogg",  ".ogg"),
}

FALLBACK_VOICES = [
    {"voiceId": "Alex",    "displayName": "Alex",    "description": "Năng động, biểu cảm",   "tags": ["male"]},
    {"voiceId": "Ashley",  "displayName": "Ashley",  "description": "Ấm áp, tự nhiên",        "tags": ["female"]},
    {"voiceId": "Dennis",  "displayName": "Dennis",  "description": "Điềm tĩnh, thân thiện",  "tags": ["male"]},
    {"voiceId": "Jordan",  "displayName": "Jordan",  "description": "Chuyên nghiệp",           "tags": []},
    {"voiceId": "Nova",    "displayName": "Nova",    "description": "Trẻ trung, tươi sáng",    "tags": ["female"]},
    {"voiceId": "Echo",    "displayName": "Echo",    "description": "Sâu lắng",                "tags": ["male"]},
    {"voiceId": "Fable",   "displayName": "Fable",   "description": "Kể chuyện",               "tags": ["female"]},
    {"voiceId": "Onyx",    "displayName": "Onyx",    "description": "Uy quyền",                "tags": ["male"]},
    {"voiceId": "Shimmer", "displayName": "Shimmer", "description": "Nhẹ nhàng, dịu dàng",    "tags": ["female"]},
    {"voiceId": "Alloy",   "displayName": "Alloy",   "description": "Cân bằng, trung tính",   "tags": []},
]

# ─── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Inworld TTS Studio",
    page_icon="🎙️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── CSS ──────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
* { font-family: 'Inter', sans-serif; }

[data-testid="stAppViewContainer"] {
    background: linear-gradient(135deg, #0a0f1e 0%, #0f172a 55%, #0d1526 100%);
}
[data-testid="stSidebar"] {
    background: rgba(10,15,30,.98) !important;
    border-right: 1px solid rgba(99,102,241,.2) !important;
}
[data-testid="stSidebar"]::before {
    content:'';position:absolute;top:0;left:0;right:0;height:3px;
    background:linear-gradient(90deg,#6366f1,#0ea5e9,#8b5cf6);
}

h1,h2,h3,h4,h5,p,div,span,label { color:#e2e8f0; }
.stMarkdown p { color:#cbd5e1 !important; }

/* Header */
.app-title {
    font-size:2.5rem; font-weight:800; line-height:1.2;
    background:linear-gradient(135deg,#38bdf8 0%,#818cf8 50%,#a78bfa 100%);
    -webkit-background-clip:text; -webkit-text-fill-color:transparent; background-clip:text;
}

/* Cards */
.card {
    background:rgba(20,30,50,.7);
    border:1px solid rgba(99,102,241,.18);
    border-radius:18px; padding:22px 24px; margin-bottom:14px;
    backdrop-filter:blur(12px);
}
.card-title {
    font-size:.72rem; font-weight:700; text-transform:uppercase;
    letter-spacing:.14em; color:#6366f1; margin-bottom:14px;
}

/* Textarea */
textarea {
    background:rgba(10,15,30,.9) !important;
    color:#f1f5f9 !important;
    border:1px solid rgba(99,102,241,.28) !important;
    border-radius:12px !important;
    font-size:.95rem !important; line-height:1.7 !important;
}
textarea:focus {
    border-color:#6366f1 !important;
    box-shadow:0 0 0 3px rgba(99,102,241,.12) !important;
}

/* Char bar */
.char-bar-wrap { display:flex; align-items:center; gap:10px; margin-top:7px; }
.char-bar-bg   { flex:1; height:3px; background:rgba(99,102,241,.12); border-radius:4px; overflow:hidden; }
.char-bar-fill { height:100%; border-radius:4px; transition:width .4s,background .4s; }
.char-text     { font-size:.75rem; color:#475569; white-space:nowrap; }

/* Buttons */
.stButton > button {
    background:linear-gradient(135deg,#6366f1,#0ea5e9) !important;
    color:#fff !important; border:none !important;
    border-radius:12px !important; font-size:.95rem !important;
    font-weight:700 !important; padding:13px 24px !important;
    width:100% !important;
    box-shadow:0 4px 18px rgba(99,102,241,.3) !important;
    transition:all .25s !important;
}
.stButton > button:hover:not(:disabled) {
    transform:translateY(-2px) !important;
    box-shadow:0 8px 28px rgba(99,102,241,.45) !important;
}
.stButton > button:disabled { opacity:.32 !important; box-shadow:none !important; }

/* Download button */
[data-testid="stDownloadButton"] button {
    background:rgba(14,165,233,.1) !important;
    border:1px solid rgba(14,165,233,.3) !important;
    color:#38bdf8 !important; border-radius:10px !important;
    font-weight:600 !important; box-shadow:none !important;
}
[data-testid="stDownloadButton"] button:hover {
    background:rgba(14,165,233,.18) !important; transform:none !important;
}

/* Radio */
[data-testid="stRadio"] label { color:#cbd5e1 !important; font-size:.88rem !important; }
[data-testid="stRadio"] > div { gap:2px !important; }

/* Search input */
[data-testid="stTextInput"] input {
    background:rgba(10,15,30,.8) !important; color:#e2e8f0 !important;
    border:1px solid rgba(99,102,241,.22) !important; border-radius:8px !important;
}

/* Selectbox */
[data-testid="stSidebar"] .stSelectbox div[data-baseweb="select"] > div {
    background:rgba(10,15,30,.8) !important;
    border:1px solid rgba(99,102,241,.25) !important;
    border-radius:8px !important; color:#e2e8f0 !important;
}

/* Sidebar label */
.sb-label {
    font-size:.68rem; font-weight:700; text-transform:uppercase;
    letter-spacing:.13em; color:#334155; margin:14px 0 7px;
}

/* Status */
.status-ok  { display:inline-flex;align-items:center;gap:6px;
               background:rgba(34,197,94,.08);border:1px solid rgba(34,197,94,.25);
               color:#86efac;padding:5px 14px;border-radius:20px;font-size:.75rem;font-weight:600; }
.status-err { display:inline-flex;align-items:center;gap:6px;
               background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.25);
               color:#fca5a5;padding:5px 14px;border-radius:20px;font-size:.75rem;font-weight:600; }

/* Stats chips */
.chips { display:flex; flex-wrap:wrap; gap:7px; margin-top:10px; }
.chip  { background:rgba(99,102,241,.1); border:1px solid rgba(99,102,241,.2);
         border-radius:20px; padding:4px 13px; font-size:.75rem; color:#a5b4fc; }

/* Result ready */
.result-ready {
    background:rgba(14,165,233,.05);
    border:1px solid rgba(14,165,233,.22);
    border-radius:14px; padding:18px; margin-bottom:12px;
}
.result-ready-hdr {
    font-size:.72rem; font-weight:700; text-transform:uppercase;
    letter-spacing:.1em; color:#38bdf8; margin-bottom:12px;
}

/* History */
.hist { display:flex;align-items:center;gap:9px;
        background:rgba(10,15,30,.4);border:1px solid rgba(99,102,241,.12);
        border-radius:9px;padding:9px 12px;margin-bottom:6px; }
.hist-dot  { width:7px;height:7px;background:#6366f1;border-radius:50%;flex-shrink:0; }
.hist-text { font-size:.78rem;color:#64748b;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap; }
.hist-meta { font-size:.68rem;color:#1e293b;flex-shrink:0; }

/* Steps */
.step { display:flex;align-items:center;gap:12px;margin-bottom:12px; }
.step-n { width:26px;height:26px;background:rgba(99,102,241,.15);border-radius:50%;
           display:flex;align-items:center;justify-content:center;
           font-size:.72rem;font-weight:800;color:#818cf8;flex-shrink:0; }
.step-t { color:#64748b;font-size:.83rem; }

hr { border-color:rgba(99,102,241,.12) !important; margin:10px 0 !important; }
</style>
""", unsafe_allow_html=True)

# ─── Session state ────────────────────────────────────────────────────────────
for k, v in {
    "voices": [], "history": [], "last_audio": None,
    "last_fmt_ext": ".wav", "last_audio_mime": "audio/wav",
    "fmt_choice": "WAV",
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_api_key() -> str:
    try:
        return st.secrets["INWORLD_API_KEY"]
    except Exception:
        return os.environ.get("INWORLD_API_KEY", "")

def auth_header(key: str) -> dict:
    return {"Authorization": f"Basic {key}", "Content-Type": "application/json"}

@st.cache_data(ttl=300, show_spinner=False)
def fetch_voices(api_key: str) -> list[dict]:
    try:
        r = requests.get(LIST_VOICE_URL, headers={"Authorization": f"Basic {api_key}"}, timeout=10)
        r.raise_for_status()
        return r.json().get("voices", [])
    except Exception:
        return []

def synthesize(text: str, voice_id: str, model_id: str, encoding: str, speed: float, api_key: str) -> bytes:
    payload = {
        "text": text, "voiceId": voice_id, "modelId": model_id,
        "audioConfig": {"audioEncoding": encoding, "sampleRateHertz": 22050, "speakingRate": speed},
        "applyTextNormalization": "ON",
    }
    r = requests.post(TTS_URL, headers=auth_header(api_key), json=payload, timeout=40)
    r.raise_for_status()
    b64 = r.json().get("audioContent", "")
    if not b64:
        raise ValueError("API không trả về audio.")
    return base64.b64decode(b64)

# ─── Load API key & voices ────────────────────────────────────────────────────
api_key = get_api_key()

if api_key and not st.session_state.voices:
    loaded = fetch_voices(api_key)
    st.session_state.voices = loaded if loaded else FALLBACK_VOICES

voices_list = st.session_state.voices or FALLBACK_VOICES

# ─── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("""
    <div style='text-align:center;padding:22px 0 12px'>
        <div style='font-size:2.2rem'>🎙️</div>
        <div style='font-weight:800;font-size:1.05rem;
                    background:linear-gradient(90deg,#6366f1,#0ea5e9);
                    -webkit-background-clip:text;-webkit-text-fill-color:transparent'>TTS Studio</div>
        <div style='color:#1e293b;font-size:.68rem;margin-top:2px'>Powered by Inworld AI</div>
    </div>
    """, unsafe_allow_html=True)

    st.divider()

    st.markdown('<div class="sb-label">🔒 Trạng thái kết nối</div>', unsafe_allow_html=True)
    if api_key:
        st.markdown('<span class="status-ok">● API đã kết nối</span>', unsafe_allow_html=True)
    else:
        st.markdown('<span class="status-err">● Chưa cấu hình</span>', unsafe_allow_html=True)
        st.caption("Thêm `INWORLD_API_KEY` vào `.env` hoặc Streamlit Secrets.")

    st.divider()

    st.markdown('<div class="sb-label">🤖 Model TTS</div>', unsafe_allow_html=True)
    model_label    = st.selectbox("Model", list(TTS_MODELS.keys()), index=0, label_visibility="collapsed")
    selected_model = TTS_MODELS[model_label]

    st.divider()

    st.markdown('<div class="sb-label">🎵 Định dạng xuất</div>', unsafe_allow_html=True)
    fmt_cols = st.columns(3)
    for i, fk in enumerate(AUDIO_FORMATS):
        with fmt_cols[i]:
            active = st.session_state.fmt_choice == fk
            if st.button(
                fk,
                key=f"fmt_{fk}",
                type="primary" if active else "secondary",
            ):
                st.session_state.fmt_choice = fk
                st.rerun()

    audio_encoding, audio_mime, audio_ext = AUDIO_FORMATS[st.session_state.fmt_choice]

    st.divider()

    st.markdown('<div class="sb-label">⚡ Tốc độ giọng nói</div>', unsafe_allow_html=True)
    speed = st.slider("Speed", 0.5, 2.0, 1.0, 0.05, format="%.2fx", label_visibility="collapsed")
    st.caption(f"{speed:.2f}× — {'Chậm' if speed<0.8 else ('Nhanh' if speed>1.3 else 'Bình thường')}")

    st.divider()

    st.markdown('<div class="sb-label">📋 Lịch sử gần đây</div>', unsafe_allow_html=True)
    if st.session_state.history:
        for h in reversed(st.session_state.history[-5:]):
            st.markdown(
                f"<div class='hist'><div class='hist-dot'></div>"
                f"<div class='hist-text'>{h['text'][:44]}{'…' if len(h['text'])>44 else ''}</div>"
                f"<div class='hist-meta'>{h['time']}</div></div>",
                unsafe_allow_html=True,
            )
        if st.button("🗑 Xóa lịch sử", use_container_width=True):
            st.session_state.history = []
            st.rerun()
    else:
        st.markdown("<div style='color:#1e293b;font-size:.78rem;padding:4px 0'>Chưa có audio nào.</div>",
                    unsafe_allow_html=True)

    st.markdown("""
    <div style='margin-top:28px;padding-top:14px;border-top:1px solid rgba(99,102,241,.08);
                text-align:center;font-size:.68rem'>
        <a href='https://docs.inworld.ai/tts/tts' target='_blank'
           style='color:#4338ca;text-decoration:none'>📖 Docs</a>
        &ensp;·&ensp;
        <a href='https://studio.inworld.ai' target='_blank'
           style='color:#4338ca;text-decoration:none'>🔑 Portal</a>
    </div>
    """, unsafe_allow_html=True)

# ─── Main ─────────────────────────────────────────────────────────────────────

# Header
st.markdown("""
<div style='text-align:center;padding:28px 0 6px'>
    <div class='app-title'>🎙️ Inworld TTS Studio</div>
    <div style='color:#334155;font-size:.95rem;margin-top:8px;letter-spacing:.03em'>
        Nhập văn bản &nbsp;·&nbsp; Chọn giọng &nbsp;·&nbsp; Tạo audio ngay lập tức
    </div>
</div>
""", unsafe_allow_html=True)

if not api_key:
    st.warning("⚠️ Chưa tìm thấy **INWORLD_API_KEY**. Thêm vào file `.env` hoặc Streamlit **Secrets** để sử dụng.", icon="🔒")

st.markdown("<br>", unsafe_allow_html=True)

col_left, col_right = st.columns([11, 9], gap="large")

# ══ CỘT TRÁI ══════════════════════════════════════════════════════════════════
with col_left:

    # Card: Văn bản
    st.markdown('<div class="card"><div class="card-title">✍️ &nbsp;Văn bản cần chuyển đổi</div>', unsafe_allow_html=True)
    text_input = st.text_area(
        "text", label_visibility="collapsed",
        value="Xin chào! Tôi là trợ lý giọng nói được tạo bởi Inworld AI. Rất vui được gặp bạn hôm nay.",
        height=155, max_chars=2000,
        placeholder="Nhập văn bản tại đây… (tối đa 2 000 ký tự)",
    )
    char_count = len(text_input)
    pct        = char_count / 2000
    bar_color  = "#22c55e" if pct < .75 else ("#f59e0b" if pct < 1.0 else "#ef4444")
    st.markdown(f"""
    <div class="char-bar-wrap">
        <div class="char-bar-bg">
            <div class="char-bar-fill" style="width:{min(pct*100,100):.1f}%;background:{bar_color}"></div>
        </div>
        <span class="char-text" style="color:{bar_color}">{char_count:,} / 2 000 ký tự</span>
    </div>
    """, unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

    # Card: Chọn giọng
    st.markdown('<div class="card"><div class="card-title">🎙️ &nbsp;Chọn giọng nói</div>', unsafe_allow_html=True)

    col_s, col_g = st.columns([3, 1])
    with col_s:
        search_q = st.text_input("s", placeholder="🔍  Tìm tên giọng…", label_visibility="collapsed")
    with col_g:
        gender_opt = st.selectbox("g", ["Tất cả", "👨 Nam", "👩 Nữ"], label_visibility="collapsed")

    gender_map    = {"Tất cả": None, "👨 Nam": "male", "👩 Nữ": "female"}
    gender_filter = gender_map[gender_opt]

    filtered = [
        v for v in voices_list
        if (not search_q or search_q.lower() in v.get("displayName","").lower())
        and (not gender_filter or gender_filter in v.get("tags", []))
    ]

    if filtered:
        voice_ids = [v["voiceId"] for v in filtered]
        def fmt_voice(vid):
            v = next((x for x in filtered if x["voiceId"] == vid), {})
            tags = v.get("tags", [])
            icon = "👨" if "male" in tags else ("👩" if "female" in tags else "🎤")
            return f"{icon}  **{v.get('displayName', vid)}**  —  {v.get('description','')[:52]}"

        selected_voice = st.radio("voices", options=voice_ids, format_func=fmt_voice, label_visibility="collapsed")
    else:
        st.info("Không tìm thấy giọng phù hợp.")
        selected_voice = "Dennis"

    st.markdown('</div>', unsafe_allow_html=True)

    # Nút tạo
    can_generate = bool(api_key) and bool(text_input.strip()) and char_count <= 2000
    btn_label = (
        "🔒  Cần cấu hình API Key" if not api_key else
        "✍️  Nhập văn bản trước"   if not text_input.strip() else
        "✂️  Văn bản quá dài (> 2000)"  if char_count > 2000 else
        "🎙️  Tạo giọng nói"
    )
    generate_btn = st.button(btn_label, disabled=not can_generate)

# ══ CỘT PHẢI ══════════════════════════════════════════════════════════════════
with col_right:

    # Card: Kết quả
    st.markdown('<div class="card"><div class="card-title">🎧 &nbsp;Kết quả audio</div>', unsafe_allow_html=True)
    result_slot = st.empty()

    if st.session_state.last_audio:
        with result_slot.container():
            st.markdown('<div class="result-ready"><div class="result-ready-hdr">✅ &nbsp;Audio sẵn sàng</div>', unsafe_allow_html=True)
            st.audio(st.session_state.last_audio, format=st.session_state.last_audio_mime)
            st.markdown('</div>', unsafe_allow_html=True)
            st.download_button(
                label=f"⬇️  Tải về  ({st.session_state.last_fmt_ext.upper()})",
                data=st.session_state.last_audio,
                file_name=f"tts_{int(time.time())}{st.session_state.last_fmt_ext}",
                mime=st.session_state.last_audio_mime,
                use_container_width=True,
            )
    else:
        result_slot.markdown("""
        <div style='text-align:center;padding:44px 0;'>
            <div style='width:60px;height:60px;background:rgba(99,102,241,.1);border-radius:50%;
                        display:flex;align-items:center;justify-content:center;
                        font-size:1.7rem;margin:0 auto 12px'>🔇</div>
            <div style='color:#1e293b;font-size:.85rem'>Audio sẽ xuất hiện ở đây</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)

    # Card: Cấu hình hiện tại
    st.markdown('<div class="card"><div class="card-title">📊 &nbsp;Cấu hình hiện tại</div>', unsafe_allow_html=True)
    sel_info = next((v for v in voices_list if v["voiceId"] == selected_voice), {})
    sel_tags = sel_info.get("tags", [])
    g_icon   = "👨" if "male" in sel_tags else ("👩" if "female" in sel_tags else "🎤")
    st.markdown(f"""
    <div class="chips">
        <span class="chip">🤖 {selected_model.replace('inworld-','')}</span>
        <span class="chip">{g_icon} {selected_voice}</span>
        <span class="chip">⚡ {speed:.2f}×</span>
        <span class="chip">🎵 {st.session_state.fmt_choice}</span>
    </div>
    """, unsafe_allow_html=True)
    if sel_info.get("description"):
        st.markdown(
            f"<div style='margin-top:11px;color:#475569;font-size:.78rem;font-style:italic'>"
            f""{sel_info['description']}"</div>",
            unsafe_allow_html=True,
        )
    st.markdown('</div>', unsafe_allow_html=True)

    # Card: Hướng dẫn (khi chưa có audio)
    if not st.session_state.last_audio:
        st.markdown("""
        <div class="card">
            <div class="card-title">💡 &nbsp;Hướng dẫn</div>
            <div class="step"><div class="step-n">1</div>
                <div class="step-t">Nhập văn bản muốn chuyển thành giọng nói</div></div>
            <div class="step"><div class="step-n">2</div>
                <div class="step-t">Chọn giọng đọc, model và tốc độ</div></div>
            <div class="step"><div class="step-n">3</div>
                <div class="step-t">Nhấn <b style="color:#e2e8f0">Tạo giọng nói</b> — nghe và tải về</div></div>
        </div>
        """, unsafe_allow_html=True)

# ─── Xử lý tạo audio ──────────────────────────────────────────────────────────
if generate_btn:
    with st.spinner("🎙️  Đang tổng hợp giọng nói…"):
        try:
            audio_bytes = synthesize(
                text=text_input.strip(), voice_id=selected_voice,
                model_id=selected_model, encoding=audio_encoding,
                speed=speed, api_key=api_key,
            )
            st.session_state.last_audio      = audio_bytes
            st.session_state.last_fmt_ext    = audio_ext
            st.session_state.last_audio_mime = audio_mime
            st.session_state.history.append({
                "text": text_input.strip(), "voice": selected_voice,
                "model": selected_model,    "time": time.strftime("%H:%M"),
            })
            st.toast(f"✅ Tạo thành công · giọng {selected_voice} · {len(audio_bytes):,} bytes", icon="🎉")
            st.rerun()

        except requests.HTTPError as e:
            code = e.response.status_code
            msgs = {
                401: "❌ API Key không hợp lệ (401) — kiểm tra `.env` hoặc Streamlit Secrets.",
                429: "❌ Đã vượt rate limit (429) — thử lại sau vài giây.",
                400: f"❌ Yêu cầu không hợp lệ (400): {e.response.text[:200]}",
            }
            st.error(msgs.get(code, f"❌ Lỗi API {code}: {e.response.text[:200]}"))
        except Exception as e:
            st.error(f"❌ Lỗi: {e}")
