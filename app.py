"""
app.py — Inworld Voice Assistant trên Streamlit
Chat text → AI phản hồi text + phát giọng nói (TTS)

Chạy:
    streamlit run app.py
"""

import base64
import json
import os
import time
from pathlib import Path

import requests
import streamlit as st
from dotenv import load_dotenv

# ─── Helpers ──────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

CHAT_URL = "https://api.inworld.ai/v1/chat/completions"
TTS_URL  = "https://api.inworld.ai/tts/v1/voice"

AVAILABLE_VOICES = [
    "Dennis", "Aria", "Jordan", "Nova", "Echo",
    "Fable", "Onyx", "Shimmer", "Alloy",
]

AVAILABLE_MODELS = [
    "auto",
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "anthropic/claude-3-5-sonnet",
    "google/gemini-2.0-flash",
]

TTS_MODELS = [
    "inworld-tts-1.5-max",
    "inworld-tts-1.5-mini",
    "inworld-tts-1-max",
    "inworld-tts-1",
]


def get_api_key() -> str:
    """Lấy API key từ session state hoặc env."""
    return st.session_state.get("api_key") or os.environ.get("INWORLD_API_KEY", "")


def auth_header(api_key: str) -> dict:
    return {"Authorization": f"Basic {api_key}", "Content-Type": "application/json"}


def chat_completion(
    messages: list[dict],
    model: str,
    system_prompt: str,
    api_key: str,
    temperature: float = 0.8,
    max_tokens: int = 512,
) -> str:
    """Gọi Inworld Chat Completion API, trả về text phản hồi."""
    payload_messages = []
    if system_prompt.strip():
        payload_messages.append({"role": "system", "content": system_prompt.strip()})
    payload_messages.extend(messages)

    payload = {
        "model": model,
        "messages": payload_messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }

    resp = requests.post(CHAT_URL, headers=auth_header(api_key), json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def synthesize_tts(text: str, voice_id: str, tts_model: str, api_key: str) -> bytes | None:
    """Gọi Inworld TTS API, trả về bytes WAV."""
    payload = {
        "text": text[:2000],
        "voiceId": voice_id,
        "modelId": tts_model,
        "audioConfig": {
            "audioEncoding": "LINEAR16",
            "sampleRateHertz": 22050,
        },
        "applyTextNormalization": "ON",
    }
    try:
        resp = requests.post(TTS_URL, headers=auth_header(api_key), json=payload, timeout=30)
        resp.raise_for_status()
        audio_b64 = resp.json().get("audioContent", "")
        return base64.b64decode(audio_b64) if audio_b64 else None
    except Exception as e:
        st.warning(f"TTS lỗi: {e}")
        return None


# ─── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Inworld Voice Assistant",
    page_icon="🎙️",
    layout="wide",
)

# ─── Custom CSS ───────────────────────────────────────────────────────────────

st.markdown("""
<style>
/* Tổng thể */
[data-testid="stAppViewContainer"] { background: #0f172a; }
[data-testid="stSidebar"] { background: #1e293b; border-right: 1px solid #334155; }
h1, h2, h3, label, p, .stMarkdown { color: #e2e8f0 !important; }

/* Chat messages */
.chat-user {
    background: #1e40af;
    color: #e0f2fe;
    padding: 12px 16px;
    border-radius: 16px 16px 4px 16px;
    margin: 6px 0 6px auto;
    max-width: 75%;
    width: fit-content;
    font-size: 0.95rem;
    line-height: 1.5;
}
.chat-ai {
    background: #1e293b;
    color: #e2e8f0;
    padding: 12px 16px;
    border-radius: 16px 16px 16px 4px;
    margin: 6px auto 6px 0;
    max-width: 75%;
    width: fit-content;
    font-size: 0.95rem;
    line-height: 1.5;
    border: 1px solid #334155;
}
.chat-meta { font-size: 0.72rem; color: #64748b; margin-top: 4px; }
.chat-wrap { display: flex; flex-direction: column; gap: 4px; padding: 8px 0; }

/* Typing indicator */
.typing { color: #7dd3fc; font-style: italic; animation: pulse 1.5s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }

/* Input */
[data-testid="stTextInput"] input {
    background: #1e293b !important;
    color: #e2e8f0 !important;
    border: 1px solid #475569 !important;
    border-radius: 10px !important;
}
[data-testid="stTextInput"] input:focus { border-color: #7dd3fc !important; }

/* Buttons */
.stButton > button {
    background: #3b82f6 !important;
    color: white !important;
    border: none !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
}
.stButton > button:hover { background: #2563eb !important; }

/* Sidebar inputs */
[data-testid="stSidebar"] select,
[data-testid="stSidebar"] input,
[data-testid="stSidebar"] textarea {
    background: #0f172a !important;
    color: #e2e8f0 !important;
    border: 1px solid #475569 !important;
}

/* Status badge */
.badge-ok  { background:#166534; color:#86efac; padding:3px 10px; border-radius:20px; font-size:.8rem; }
.badge-err { background:#7f1d1d; color:#fca5a5; padding:3px 10px; border-radius:20px; font-size:.8rem; }
.badge-na  { background:#374151; color:#9ca3af; padding:3px 10px; border-radius:20px; font-size:.8rem; }

/* Divider */
hr { border-color: #334155 !important; }
</style>
""", unsafe_allow_html=True)

# ─── Session state init ───────────────────────────────────────────────────────

if "messages" not in st.session_state:
    st.session_state.messages = []          # list of {"role","content","time"}
if "tts_enabled" not in st.session_state:
    st.session_state.tts_enabled = True
if "api_key" not in st.session_state:
    st.session_state.api_key = os.environ.get("INWORLD_API_KEY", "")


# ─── Sidebar: Cấu hình ────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## ⚙️ Cấu hình")
    st.divider()

    # API Key
    st.markdown("### 🔑 API Key")
    api_key_input = st.text_input(
        "Inworld API Key (Basic)",
        value=st.session_state.api_key,
        type="password",
        placeholder="Dán API Key vào đây...",
    )
    if api_key_input:
        st.session_state.api_key = api_key_input

    # Trạng thái kết nối
    api_key = get_api_key()
    if api_key:
        st.markdown('<span class="badge-ok">✓ API Key đã nhập</span>', unsafe_allow_html=True)
    else:
        st.markdown('<span class="badge-na">✗ Chưa có API Key</span>', unsafe_allow_html=True)

    st.divider()

    # System prompt
    st.markdown("### 🧠 System Instructions")
    system_prompt = st.text_area(
        "Mô tả vai trò AI",
        value="Bạn là trợ lý AI thông minh, thân thiện. Trả lời ngắn gọn, tự nhiên bằng tiếng Việt.",
        height=110,
        label_visibility="collapsed",
    )

    st.divider()

    # LLM settings
    st.markdown("### 🤖 Mô hình LLM")
    llm_model = st.selectbox("Model", AVAILABLE_MODELS, index=0)
    temperature = st.slider("Temperature", 0.0, 2.0, 0.8, 0.05)
    max_tokens  = st.slider("Max Tokens", 64, 1024, 512, 32)

    st.divider()

    # TTS settings
    st.markdown("### 🔊 Giọng nói (TTS)")
    tts_enabled = st.toggle("Bật giọng nói", value=st.session_state.tts_enabled)
    st.session_state.tts_enabled = tts_enabled

    if tts_enabled:
        voice_id  = st.selectbox("Giọng", AVAILABLE_VOICES, index=0)
        tts_model = st.selectbox("TTS Model", TTS_MODELS, index=0)
    else:
        voice_id  = "Dennis"
        tts_model = TTS_MODELS[0]

    st.divider()

    # Clear chat
    if st.button("🗑️ Xóa lịch sử chat", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

    st.markdown(
        "<div style='margin-top:20px;color:#475569;font-size:.75rem;text-align:center'>"
        "Inworld AI · <a href='https://docs.inworld.ai' target='_blank' style='color:#7dd3fc'>Docs</a></div>",
        unsafe_allow_html=True,
    )


# ─── Main area ────────────────────────────────────────────────────────────────

st.markdown(
    "<h1 style='text-align:center;color:#7dd3fc;margin-bottom:4px'>🎙️ Inworld Voice Assistant</h1>"
    "<p style='text-align:center;color:#64748b;margin-bottom:0'>Chat text · AI trả lời · Phát giọng nói</p>",
    unsafe_allow_html=True,
)
st.divider()

# ── Hiển thị lịch sử chat ───────────────────────────────────────────────────
chat_container = st.container()

with chat_container:
    if not st.session_state.messages:
        st.markdown(
            "<div style='text-align:center;color:#475569;padding:60px 0'>"
            "💬 Bắt đầu cuộc trò chuyện bên dưới...</div>",
            unsafe_allow_html=True,
        )
    else:
        for msg in st.session_state.messages:
            ts = msg.get("time", "")
            if msg["role"] == "user":
                st.markdown(
                    f"<div class='chat-wrap'>"
                    f"<div class='chat-user'>{msg['content']}</div>"
                    f"<div class='chat-meta' style='text-align:right'>Bạn · {ts}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"<div class='chat-wrap'>"
                    f"<div class='chat-ai'>{msg['content']}</div>"
                    f"<div class='chat-meta'>AI · {ts}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                # Audio player nếu có
                if msg.get("audio"):
                    st.audio(msg["audio"], format="audio/wav", autoplay=False)

st.divider()

# ── Input box ───────────────────────────────────────────────────────────────
col_input, col_btn = st.columns([5, 1])

with col_input:
    user_input = st.text_input(
        "Nhập tin nhắn...",
        key="user_input_field",
        placeholder="Gõ câu hỏi và nhấn Gửi (hoặc Enter)...",
        label_visibility="collapsed",
    )

with col_btn:
    send_clicked = st.button("Gửi ➤", use_container_width=True)

# ── Xử lý gửi tin nhắn ─────────────────────────────────────────────────────
if (send_clicked or user_input) and user_input.strip():
    api_key = get_api_key()
    if not api_key:
        st.error("⚠️ Vui lòng nhập Inworld API Key trong sidebar trước!")
        st.stop()

    user_text = user_input.strip()
    now_str = time.strftime("%H:%M")

    # Thêm tin nhắn user vào lịch sử
    st.session_state.messages.append({
        "role": "user",
        "content": user_text,
        "time": now_str,
    })

    # Chuẩn bị messages payload (không bao gồm audio field)
    api_messages = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages
    ]

    # Gọi LLM
    with st.spinner("🤔 AI đang suy nghĩ..."):
        try:
            ai_text = chat_completion(
                messages=api_messages,
                model=llm_model,
                system_prompt=system_prompt,
                api_key=api_key,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except requests.HTTPError as e:
            st.error(f"❌ Lỗi API ({e.response.status_code}): {e.response.text[:300]}")
            st.stop()
        except Exception as e:
            st.error(f"❌ Lỗi: {e}")
            st.stop()

    # TTS
    audio_bytes = None
    if st.session_state.tts_enabled:
        with st.spinner("🔊 Đang tạo giọng nói..."):
            audio_bytes = synthesize_tts(ai_text, voice_id, tts_model, api_key)

    # Lưu phản hồi AI
    st.session_state.messages.append({
        "role": "assistant",
        "content": ai_text,
        "time": now_str,
        "audio": audio_bytes,
    })

    st.rerun()
