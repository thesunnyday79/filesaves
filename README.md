# 🎬 pCloud Auto Caption — Online (Streamlit Community Cloud)

Tự động tạo caption cho video trên pCloud — **100% online, không cài đặt gì**.

## Stack
| Thành phần | Công nghệ |
|---|---|
| Speech-to-text | **Groq Whisper large-v3-turbo** (miễn phí, ~300x realtime) |
| Subtitle burn-in | **FFmpeg** (cài tự động qua `packages.txt`) |
| Deploy | **Streamlit Community Cloud** (miễn phí) |
| Storage | **pCloud API** |

---

## 🚀 Deploy lên Streamlit Community Cloud (5 phút)

### Bước 1 — Upload code lên GitHub
1. Tạo repo GitHub mới (private hoặc public)
2. Upload 3 file: `app.py`, `requirements.txt`, `packages.txt`

### Bước 2 — Deploy
1. Vào [share.streamlit.io](https://share.streamlit.io)
2. Đăng nhập bằng GitHub
3. Nhấn **"New app"**
4. Chọn repo vừa tạo, branch `main`, file `app.py`
5. Nhấn **Deploy!**

### Bước 3 — Lấy API Keys

**Groq API Key (miễn phí, không cần thẻ):**
1. Vào [console.groq.com](https://console.groq.com)
2. Sign up miễn phí
3. API Keys → Create new key → Copy

**pCloud Access Token:**
1. Vào [my.pcloud.com](https://my.pcloud.com)
2. Settings → Security → Access Tokens
3. Create Token → Copy

---

## 📁 Cấu trúc file

```
your-repo/
├── app.py            ← App chính
├── requirements.txt  ← Python packages
└── packages.txt      ← System packages (ffmpeg)
```

---

## ⚙️ Cách app hoạt động

```
pCloud
  └─ Download video
        └─ FFmpeg extract audio (16kHz MP3)
              └─ Groq Whisper API → segments JSON
                    └─ Generate .SRT file
                          └─ FFmpeg burn subtitles → video mới
                                └─ Upload video_captioned.mp4 + .srt → pCloud
```

### Xử lý video dài (chunking tự động)
- Video ≤ 20 phút → 1 API call
- Video > 20 phút → tự động chia chunk 20 phút, merge kết quả
- Mỗi chunk audio compress MP3 64kbps → ~10MB/20min (dưới giới hạn 25MB Groq)

---

## 📊 Giới hạn Groq miễn phí

| Giới hạn | Giá trị |
|----------|---------|
| File size/request | 25 MB |
| Requests/phút | 20 |
| Audio/giờ | 7,200 giây (2h) |
| Audio/ngày | 28,800 giây (8h) |

App xử lý tuần tự, tự động delay giữa các chunk nếu cần.

---

## 📦 Output trên pCloud

Với mỗi `myvideo.mp4`:
```
/Videos/
  ├── myvideo.mp4              ← giữ nguyên
  ├── myvideo_captioned.mp4    ← video mới có subtitle burn-in
  └── myvideo.srt              ← file SRT dùng được mọi player
```
