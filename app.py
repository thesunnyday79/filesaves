import streamlit as st
import requests, hashlib, os, tempfile, subprocess, json, base64, time
from pathlib import Path

st.set_page_config(page_title="Video Caption & Tools", page_icon="🎬", layout="wide")
st.markdown("""<style>
  .block-container{padding-top:1.5rem;max-width:1200px}
  .log-box{background:#0f1117;border-left:4px solid #6C63FF;padding:.6rem 1rem;
            border-radius:6px;font-family:monospace;font-size:.82rem;color:#cdd6f4;margin:3px 0}
  .log-success{border-left-color:#a6e3a1;color:#a6e3a1}
  .log-error  {border-left-color:#f38ba8;color:#f38ba8}
  .log-warn   {border-left-color:#f9e2af;color:#f9e2af}
  .video-card{background:#1e1e2e;border-radius:10px;padding:.8rem 1rem;
              margin-bottom:.4rem;border:1px solid #2a2a3e}
  .login-card{background:#1e1e2e;border-radius:14px;padding:2rem 2.2rem;border:1px solid #2a2a3e}
  .user-badge{background:#1e2e1e;border:1px solid #a6e3a1;border-radius:8px;
              padding:.5rem 1rem;color:#a6e3a1;font-size:.85rem;display:inline-block}
</style>""", unsafe_allow_html=True)

# ─────────────────────────────────────────────
# DROPBOX STORAGE API
# ─────────────────────────────────────────────
DBX_API     = "https://api.dropboxapi.com/2"
DBX_CONTENT = "https://content.dropboxapi.com/2"

def dbx_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

def dbx_verify_token(token):
    """Check token validity. Returns user info dict or raises."""
    r = requests.post(f"{DBX_API}/users/get_current_account",
                      headers={"Authorization": f"Bearer {token}"},
                      timeout=15)
    if r.status_code == 401:
        raise RuntimeError("❌ Access Token không hợp lệ hoặc đã hết hạn.")
    if r.status_code != 200:
        raise RuntimeError(f"❌ Dropbox error {r.status_code}: {r.text[:200]}")
    return r.json()

def dbx_list_folder(token, path="", recursive=True):
    """List folder contents. path="" = root."""
    payload = {"path": path if path != "/" else "",
               "recursive": recursive,
               "include_media_info": False,
               "include_deleted": False}
    r = requests.post(f"{DBX_API}/files/list_folder",
                      headers=dbx_headers(token), json=payload, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"list_folder error: {r.text[:300]}")
    return r.json()

def dbx_list_folder_continue(token, cursor):
    r = requests.post(f"{DBX_API}/files/list_folder/continue",
                      headers=dbx_headers(token),
                      json={"cursor": cursor}, timeout=30)
    return r.json()

def collect_videos(token, folder_path="/"):
    """Collect all video files under folder_path."""
    EXTS = {".mp4",".mov",".avi",".mkv",".webm",".m4v"}
    dbx_path = "" if folder_path in ("/","") else folder_path.rstrip("/")
    result = dbx_list_folder(token, dbx_path, recursive=True)
    entries = result.get("entries", [])
    # Handle pagination
    while result.get("has_more"):
        result = dbx_list_folder_continue(token, result["cursor"])
        entries.extend(result.get("entries", []))
    videos = []
    for e in entries:
        if e.get(".tag") == "file":
            ext = Path(e["name"]).suffix.lower()
            if ext in EXTS:
                # Get parent folder path
                parent = "/".join(e["path_lower"].split("/")[:-1]) or "/"
                videos.append({
                    "name":        e["name"],
                    "path":        e["path_display"],
                    "path_lower":  e["path_lower"],
                    "size":        e.get("size", 0),
                    "parent_path": parent,
                    "id":          e.get("id",""),
                })
    return videos

def dbx_get_temp_link(token, path):
    """Get temporary download URL for a file."""
    r = requests.post(f"{DBX_API}/files/get_temporary_link",
                      headers=dbx_headers(token),
                      json={"path": path}, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"get_temp_link error: {r.text[:200]}")
    return r.json()["link"]

def dbx_upload_file(token, local_path, dropbox_path):
    """Upload file to Dropbox. Overwrites if exists."""
    import json as _json
    arg = _json.dumps({
        "path": dropbox_path,
        "mode": "overwrite",
        "autorename": False,
        "mute": False,
    })
    with open(local_path, "rb") as f:
        r = requests.post(
            f"{DBX_CONTENT}/files/upload",
            headers={
                "Authorization":   f"Bearer {token}",
                "Dropbox-API-Arg": arg,
                "Content-Type":    "application/octet-stream",
            },
            data=f,
            timeout=600,
        )
    if r.status_code != 200:
        raise RuntimeError(f"upload error {r.status_code}: {r.text[:300]}")
    return r.json()

def dbx_upload_bytes(token, img_bytes, dropbox_path):
    """Upload bytes (e.g. image) to Dropbox."""
    import json as _json
    arg = _json.dumps({"path": dropbox_path, "mode": "overwrite", "mute": False})
    r = requests.post(
        f"{DBX_CONTENT}/files/upload",
        headers={
            "Authorization":   f"Bearer {token}",
            "Dropbox-API-Arg": arg,
            "Content-Type":    "application/octet-stream",
        },
        data=img_bytes, timeout=120,
    )
    if r.status_code != 200:
        raise RuntimeError(f"upload_bytes error {r.status_code}: {r.text[:300]}")
    return r.json()

def download_video(url, dest, cb=None):
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        total=int(r.headers.get("content-length",0)); done=0
        with open(dest,"wb") as f:
            for chunk in r.iter_content(chunk_size=512*1024):
                f.write(chunk); done+=len(chunk)
                if cb and total: cb(done/total)

# ─────────────────────────────────────────────
# GROQ WHISPER
# ─────────────────────────────────────────────
GROQ_STT = "https://api.groq.com/openai/v1/audio/transcriptions"

def extract_audio(video, audio, start=None, dur=None):
    cmd = ["ffmpeg","-y"]
    if start is not None: cmd += ["-ss",str(start)]
    cmd += ["-i",video]
    if dur is not None: cmd += ["-t",str(dur)]
    cmd += ["-vn","-ar","16000","-ac","1","-c:a","mp3","-b:a","64k",audio]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0: raise RuntimeError(f"FFmpeg audio:\n{r.stderr[-1000:]}")

def get_duration(path):
    r = subprocess.run(["ffprobe","-v","quiet","-print_format","json","-show_format",path],
                       capture_output=True, text=True)
    return float(json.loads(r.stdout)["format"]["duration"])

def transcribe_chunk(groq_key, audio, offset=0.0):
    with open(audio,"rb") as f:
        resp = requests.post(GROQ_STT,
                             headers={"Authorization":f"Bearer {groq_key}"},
                             files={"file":(os.path.basename(audio),f,"audio/mpeg")},
                             data={"model":"whisper-large-v3-turbo","response_format":"verbose_json","language":"en"},
                             timeout=120)
    if resp.status_code != 200: raise RuntimeError(f"Groq {resp.status_code}: {resp.text[:400]}")
    segs = resp.json().get("segments",[])
    for s in segs: s["start"] += offset; s["end"] += offset
    return segs

def transcribe_full(groq_key, video, log_fn):
    dur = get_duration(video); log_fn(f"⏱️ Duration: {dur/60:.1f} min")
    CHUNK=1200; all_segs=[]
    for idx,start in enumerate(range(0,int(dur),CHUNK)):
        d = min(CHUNK,dur-start)
        log_fn(f"🎙️ Chunk {idx+1}: {start/60:.1f}–{(start+d)/60:.1f} min")
        with tempfile.NamedTemporaryFile(suffix=".mp3",delete=False) as tf: atmp=tf.name
        try:
            extract_audio(video,atmp,start,d); mb=os.path.getsize(atmp)/1e6
            if mb > 25:
                half=d//2
                for ss,sd in [(start,half),(start+half,d-half)]:
                    extract_audio(video,atmp,ss,sd); all_segs.extend(transcribe_chunk(groq_key,atmp,ss))
            else:
                all_segs.extend(transcribe_chunk(groq_key,atmp,start))
        finally: os.path.exists(atmp) and os.unlink(atmp)
    log_fn(f"✅ {len(all_segs)} segments","success")
    return all_segs

def to_srt(segs):
    def fmt(t):
        h,r=divmod(t,3600); m,s=divmod(r,60)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{round((s-int(s))*1000):03d}"
    lines=[]
    for i,seg in enumerate(segs,1):
        lines+=[str(i),f"{fmt(seg['start'])} --> {fmt(seg['end'])}",seg["text"].strip(),""]
    return "\n".join(lines)

# ─────────────────────────────────────────────
# CAPTION STYLE
# ─────────────────────────────────────────────
ALIGN_MAP={"Dưới giữa":2,"Dưới trái":1,"Dưới phải":3,"Giữa màn":5,"Trên giữa":8,"Trên trái":7,"Trên phải":9}

def hex_to_ass(h):
    h=h.lstrip("#"); return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}".upper()

def build_ass_style(font_name,font_size,primary_hex,outline_hex,bold,outline_w,shadow,margin_v,alignment):
    return (f"FontName={font_name},FontSize={font_size},"
            f"PrimaryColour={hex_to_ass(primary_hex)},OutlineColour={hex_to_ass(outline_hex)},"
            f"BackColour=&H80000000,Bold={-1 if bold else 0},"
            f"Outline={outline_w},Shadow={shadow},MarginV={margin_v},"
            f"Alignment={ALIGN_MAP.get(alignment,2)}")

def burn_subtitles(video, srt, output, log_fn, style_str=None):
    if style_str is None:
        style_str = build_ass_style("Arial",18,"#FFFFFF","#000000",False,2,1,35,"Dưới giữa")
    safe = srt.replace("\\","/").replace(":","\\:")
    cmd = ["ffmpeg","-y","-i",video,"-vf",f"subtitles={safe}:force_style='{style_str}'",
           "-c:v","libx264","-crf","22","-preset","fast","-c:a","copy",output]
    log_fn("🔥 FFmpeg burn-in…")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0: raise RuntimeError(f"FFmpeg:\n{r.stderr[-2000:]}")
    log_fn(f"✅ {os.path.getsize(output)/1e6:.1f} MB","success")

def _make_logger(log_ph):
    logs = []
    def log(msg, kind="info"):
        icon={"info":"▸","success":"✔","error":"✖","warn":"⚠"}[kind]
        css={"info":"","success":" log-success","error":" log-error","warn":" log-warn"}[kind]
        logs.append(f'<div class="log-box{css}">{icon} {msg}</div>')
        log_ph.markdown("".join(logs), unsafe_allow_html=True)
    return log

def process_video(sess, groq_key, video_info, log_ph, prog_ph, style_str=None):
    log = _make_logger(log_ph)
    def prog(v,t=""): prog_ph.progress(min(v,1.0),text=t)
    with tempfile.TemporaryDirectory() as tmp:
        stem=Path(video_info["name"]).stem; ext=Path(video_info["name"]).suffix.lower() or ".mp4"
        v_loc=os.path.join(tmp,video_info["name"]); s_loc=os.path.join(tmp,f"{stem}.srt")
        o_loc=os.path.join(tmp,f"{stem}_captioned{ext}")
        log(f"⬇️ Downloading {video_info['name']} ({video_info['size']/1e6:.1f} MB)")
        try:
            url=dbx_get_temp_link(sess["token"],video_info["path"])
            download_video(url,v_loc,lambda p:prog(p*0.20,f"Downloading {p*100:.0f}%"))
        except Exception as e: log(f"Download: {e}","error"); return False
        log(f"Downloaded {os.path.getsize(v_loc)/1e6:.1f} MB","success"); prog(0.22,"Transcribing…")
        try: segs=transcribe_full(groq_key,v_loc,lambda m,k="info":log(m,k))
        except Exception as e: log(f"Transcribe: {e}","error"); return False
        if not segs: log("No speech","warn"); return False
        srt_content=to_srt(segs)
        with open(s_loc,"w",encoding="utf-8") as f: f.write(srt_content)
        log(f"SRT: {len(segs)} segments","success"); prog(0.58,"Burning…")
        try: burn_subtitles(v_loc,s_loc,o_loc,lambda m,k="info":log(m,k),style_str)
        except Exception as e: log(f"Burn: {e}","error"); return False
        prog(0.80,"Uploading…")
        parent=video_info["parent_path"]
        for fpath,fname,label in [(o_loc,f"{stem}_captioned{ext}","Video"),(s_loc,f"{stem}.srt","SRT")]:
            dbx_dest = f"{parent}/{fname}" if parent != "/" else f"/{fname}"
            log(f"⬆️ Uploading {fname} → Dropbox…")
            try:
                dbx_upload_file(sess["token"],fpath,dbx_dest)
                log(f"{label} ✔","success")
            except Exception as e:
                log(f"{label} upload error: {e}","error")
                if label=="Video": return False
        prog(1.0,"Done! 🎉")
        st.expander(f"📄 SRT — {video_info['name']}").code(srt_content[:3000],language="text")
    return True

# ─────────────────────────────────────────────
# BACKGROUND MUSIC
# ─────────────────────────────────────────────
def mix_background_music(video, music, output, vol, log_fn):
    dur = get_duration(video); fade = 3
    log_fn("🎵 Mixing music…")
    cmd=["ffmpeg","-y","-i",video,"-stream_loop","-1","-i",music,
         "-filter_complex",
         f"[1:a]volume={vol:.2f},apad[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=0,"
         f"atrim=0:{dur:.3f}[aout];[aout]afade=t=out:st={dur-fade:.3f}:d={fade}[afinal]",
         "-map","0:v","-map","[afinal]","-c:v","copy","-c:a","aac","-b:a","192k","-shortest",output]
    r=subprocess.run(cmd,capture_output=True,text=True)
    if r.returncode!=0: raise RuntimeError(f"FFmpeg mix:\n{r.stderr[-2000:]}")
    log_fn(f"✅ {os.path.getsize(output)/1e6:.1f} MB","success")

# ─────────────────────────────────────────────
# YOUTUBE SHORTS
# ─────────────────────────────────────────────
GROQ_CHAT = "https://api.groq.com/openai/v1/chat/completions"

def find_best_shorts(groq_key, segs, duration, n=3):
    lines=[f"[{int(s['start'])//60:02d}:{int(s['start'])%60:02d}] {s['text'].strip()}" for s in segs]
    prompt=(f"You are a YouTube Shorts editor. Find the {n} BEST moments (30-60s each, no overlap) "
            f"from this transcript. Video duration: {duration:.0f}s\n\nTRANSCRIPT:\n"
            +"\n".join(lines)[:8000]
            +'\n\nRespond ONLY with valid JSON array:\n[{"start":<sec>,"end":<sec>,"title":"<title>","reason":"<why>"}]')
    resp=requests.post(GROQ_CHAT,
                       headers={"Authorization":f"Bearer {groq_key}","Content-Type":"application/json"},
                       json={"model":"llama-3.3-70b-versatile","messages":[{"role":"user","content":prompt}],
                             "temperature":0.3,"max_tokens":1024},timeout=60)
    if resp.status_code!=200: raise RuntimeError(f"Groq Chat {resp.status_code}: {resp.text[:300]}")
    raw=resp.json()["choices"][0]["message"]["content"].strip().replace("```json","").replace("```","")
    clips=json.loads(raw); validated=[]; last=-1
    for c in sorted(clips,key=lambda x:x["start"]):
        s=max(0,float(c["start"])); e=min(duration,float(c["end"]))
        if e-s<30: e=min(duration,s+30)
        if e-s>60: e=s+60
        if s>=last: validated.append({"start":round(s,2),"end":round(e,2),
                                       "title":c.get("title","Clip"),"reason":c.get("reason","")}); last=e
    return validated[:n]

def crop_9_16(video, output, start, dur, log_fn):
    log_fn(f"✂️ {start:.0f}s–{start+dur:.0f}s → 9:16…")
    cmd=["ffmpeg","-y","-ss",str(start),"-i",video,"-t",str(dur),
         "-vf","crop=min(iw\\,ih*9/16):ih:(iw-min(iw\\,ih*9/16))/2:0,"
               "scale=1080:1920:force_original_aspect_ratio=decrease,"
               "pad=1080:1920:(1080-iw)/2:(1920-ih)/2:black",
         "-c:v","libx264","-crf","22","-preset","fast",
         "-c:a","aac","-b:a","192k","-movflags","+faststart",output]
    r=subprocess.run(cmd,capture_output=True,text=True)
    if r.returncode!=0: raise RuntimeError(f"FFmpeg crop:\n{r.stderr[-2000:]}")
    log_fn(f"✅ {os.path.getsize(output)/1e6:.1f} MB","success")

def process_shorts(sess, groq_key, video_info, n_shorts, log_ph, prog_ph):
    log=_make_logger(log_ph)
    def prog(v,t=""): prog_ph.progress(min(v,1.0),text=t)
    uploaded=[]
    with tempfile.TemporaryDirectory() as tmp:
        stem=Path(video_info["name"]).stem; ext=Path(video_info["name"]).suffix.lower() or ".mp4"
        v_loc=os.path.join(tmp,video_info["name"])
        log(f"⬇️ Downloading {video_info['name']} ({video_info['size']/1e6:.1f} MB)")
        try:
            url=dbx_get_temp_link(sess["token"],video_info["path"])
            download_video(url,v_loc,lambda p:prog(p*0.20,f"Downloading {p*100:.0f}%"))
        except Exception as e: log(f"Download: {e}","error"); return []
        dur=get_duration(v_loc); log(f"⏱️ {dur/60:.1f} min"); prog(0.22,"Transcribing…")
        try: segs=transcribe_full(groq_key,v_loc,lambda m,k="info":log(m,k))
        except Exception as e: log(f"Transcribe: {e}","error"); return []
        if not segs: log("No speech","warn"); return []
        prog(0.50,"AI analyzing…"); log(f"🤖 Finding {n_shorts} best moments…")
        try: clips=find_best_shorts(groq_key,segs,dur,n_shorts)
        except Exception as e: log(f"AI: {e}","error"); return []
        log(f"✅ {len(clips)} clips","success"); prog(0.60,"Cutting…"); fid=video_info["parent_path"]
        for i,clip in enumerate(clips):
            cname="".join(c for c in f"{stem}_short{i+1}_{clip['title'][:25]}.mp4" if c.isalnum() or c in "._-")
            out=os.path.join(tmp,cname); cdur=clip["end"]-clip["start"]
            log(f"\n✂️ Short #{i+1}: {clip['title']} ({cdur:.0f}s)")
            try: crop_9_16(v_loc,out,clip["start"],cdur,lambda m,k="info":log(m,k))
            except Exception as e: log(f"Crop: {e}","error"); continue
            prog(0.60+(i+1)/len(clips)*0.35,f"Uploading {i+1}/{len(clips)}…")
            dbx_dest=f'{video_info["parent_path"]}/{cname}'
            dbx_upload_file(sess["token"],out,dbx_dest)
            log(f"✔ {cname}","success"); uploaded.append({**clip,"filename":cname})
        prog(1.0,"Done! 🎉")
    return uploaded

# ─────────────────────────────────────────────
# TEXT TO IMAGE — OpenRouter
# ─────────────────────────────────────────────
OPENROUTER_T2I = "https://openrouter.ai/api/v1/chat/completions"
T2I_MODELS = {
    "FLUX Schnell Free (Miễn phí)":         "black-forest-labs/flux-schnell:free",
    "FLUX 1.1 Pro (Sắc nét)":               "black-forest-labs/flux-1.1-pro",
    "FLUX 1.1 Pro Ultra (Tốt nhất)":        "black-forest-labs/flux-1.1-pro:ultra",
    "Recraft V3 (Illustration)":            "recraft-ai/recraft-v3",
}
ASPECT_RATIOS = {"16:9 Landscape":"16:9","9:16 Portrait":"9:16","1:1 Square":"1:1"}

def generate_images(model_id, prompt, neg, aspect, n=2, api_key="", seed=42):
    hdrs={"Authorization":f"Bearer {api_key}","Content-Type":"application/json",
          "HTTP-Referer":"https://video-caption-tools.streamlit.app","X-Title":"Video Caption Tools"}
    results=[]
    for i in range(n):
        resp=requests.post(OPENROUTER_T2I,headers=hdrs,
                           json={"model":model_id,"messages":[{"role":"user","content":prompt}],
                                 "modalities":["image"],"image_config":{"aspect_ratio":aspect}},timeout=120)
        if resp.status_code!=200: raise RuntimeError(f"OpenRouter {resp.status_code}: {resp.text[:400]}")
        parts=resp.json().get("choices",[{}])[0].get("message",{}).get("content",[])
        if isinstance(parts,str): raise RuntimeError("No image returned")
        img=None
        for p in parts:
            if isinstance(p,dict):
                if p.get("type")=="image_url":
                    u=p.get("image_url",{}).get("url","")
                    img=base64.b64decode(u.split(",",1)[1]) if u.startswith("data:image") else requests.get(u,timeout=60).content
                elif p.get("type")=="image" and p.get("data"): img=base64.b64decode(p["data"])
        if img is None: raise RuntimeError(f"No image in response: {str(parts)[:200]}")
        results.append(img)
    return results

def upload_img_pcloud(sess, folder_id, img_bytes, filename):  # kept for compat — uses Dropbox
    return requests.post(f"{_base(_eu(sess))}/uploadfile",
                         params={**_auth(sess),"folderid":folder_id,"filename":filename},
                         files={"file":(filename,img_bytes,"image/png")},timeout=120).json()

# ─────────────────────────────────────────────
# ADD LOGO — PIL-based
# ─────────────────────────────────────────────
def process_logo_pil(logo_path, out_png, logo_width, remove_bg=True):
    from PIL import Image
    import numpy as np
    img=Image.open(logo_path).convert("RGBA")
    w,h=img.size; new_h=max(1,int(logo_width*h/w))
    img=img.resize((logo_width,new_h),Image.LANCZOS)
    if remove_bg:
        data=np.array(img)
        corner=data[:5,:5,:3]
        bg=[int(corner[:,:,c].mean()) for c in range(3)]
        r2,g2,b2=data[:,:,0],data[:,:,1],data[:,:,2]
        dist=(r2.astype(int)-bg[0])**2+(g2.astype(int)-bg[1])**2+(b2.astype(int)-bg[2])**2
        data[dist<50**2,3]=0
        img=Image.fromarray(data)
    img.save(out_png,"PNG")
    return img.size

def overlay_logo(video, logo_png, output, position, margin, log_fn):
    pos_map={"Góc trên trái":f"{margin}:{margin}","Góc trên phải":f"W-w-{margin}:{margin}",
             "Góc dưới trái":f"{margin}:H-h-{margin}","Góc dưới phải":f"W-w-{margin}:H-h-{margin}",
             "Giữa màn hình":"(W-w)/2:(H-h)/2"}
    xy=pos_map.get(position,f"{margin}:{margin}")
    log_fn(f"🏷️ Overlay at {position}…")
    cmd=["ffmpeg","-y","-i",video,"-i",logo_png,
         "-filter_complex",f"[0:v][1:v]overlay={xy}[out]",
         "-map","[out]","-map","0:a?","-c:v","libx264","-crf","22","-preset","fast","-c:a","copy",output]
    r=subprocess.run(cmd,capture_output=True,text=True)
    if r.returncode!=0: raise RuntimeError(f"FFmpeg overlay:\n{r.stderr[-2000:]}")
    log_fn(f"✅ {os.path.getsize(output)/1e6:.1f} MB","success")

def process_logo_video(sess, video_info, logo_url, position, logo_width, margin, remove_bg, log_ph, prog_ph):
    log=_make_logger(log_ph)
    def prog(v,t=""): prog_ph.progress(min(v,1.0),text=t)
    with tempfile.TemporaryDirectory() as tmp:
        stem=Path(video_info["name"]).stem; ext=Path(video_info["name"]).suffix.lower() or ".mp4"
        v_loc=os.path.join(tmp,video_info["name"]); logo_raw=os.path.join(tmp,"logo_raw.jpg")
        logo_png=os.path.join(tmp,"logo_clean.png"); out_loc=os.path.join(tmp,f"{stem}_logo{ext}")
        log("⬇️ Downloading logo…")
        try:
            r=requests.get(logo_url,timeout=30,headers={"User-Agent":"Mozilla/5.0"})
            r.raise_for_status()
            with open(logo_raw,"wb") as f: f.write(r.content)
            log(f"Logo {len(r.content)//1024} KB","success")
        except Exception as e: log(f"Logo download: {e}","error"); return False
        prog(0.08,"Processing logo…")
        log(f"🖼️ Resizing to {logo_width}px…")
        try: w,h=process_logo_pil(logo_raw,logo_png,logo_width,remove_bg); log(f"Logo {w}×{h}px","success")
        except Exception as e: log(f"PIL: {e}","error"); return False
        prog(0.15,"Downloading video…")
        log(f"⬇️ Downloading {video_info['name']} ({video_info['size']/1e6:.1f} MB)")
        try:
            url=dbx_get_temp_link(sess["token"],video_info["path"])
            download_video(url,v_loc,lambda p:prog(0.15+p*0.55,f"Downloading {p*100:.0f}%"))
        except Exception as e: log(f"Download: {e}","error"); return False
        log(f"Downloaded {os.path.getsize(v_loc)/1e6:.1f} MB","success"); prog(0.72,"Adding logo…")
        try: overlay_logo(v_loc,logo_png,out_loc,position,margin,lambda m,k="info":log(m,k))
        except Exception as e: log(f"Overlay: {e}","error"); return False
        prog(0.87,"Uploading…"); out_name=f"{stem}_logo{ext}"
        log(f"⬆️ Uploading {out_name}…")
        dbx_dest=f'{video_info["parent_path"]}/{out_name}'
        dbx_upload_file(sess["token"],out_loc,dbx_dest)
        log("✔ Uploaded","success"); prog(1.0,"Done! 🎉")
    return True

# ═══════════════════════════════════════════════
# UI — LOGIN
# ═══════════════════════════════════════════════
st.title("🎬 Video Caption & Tools")
st.caption("Groq Whisper · FFmpeg · Dropbox · OpenRouter · 100% online")

if "session" not in st.session_state:
    _,col,_=st.columns([1,1.3,1])
    with col:
        st.markdown('<div class="login-card">',unsafe_allow_html=True)
        st.markdown("### 🔐 Kết nối Dropbox")
        dbx_token=st.text_input("🗂️ Dropbox Access Token",type="password",
                                  placeholder="sl.xxx…",
                                  help="Lấy tại dropbox.com/developers/apps")
        groq_key=st.text_input("🤖 Groq API Key",type="password",placeholder="gsk_…",
                                help="Miễn phí tại console.groq.com")
        if st.button("Kết nối",type="primary",use_container_width=True):
            if not all([dbx_token,groq_key]): st.error("Vui lòng điền đủ 2 trường.")
            else:
                with st.spinner("Đang xác thực Dropbox…"):
                    try:
                        info=dbx_verify_token(dbx_token.strip())
                        name=info.get("name",{}).get("display_name","User")
                        email=info.get("email","")
                        used=info.get("used",0); total_q=info.get("allocation",{}).get("allocated",0)
                        st.session_state["session"]={
                            "token":dbx_token.strip(),"groq_key":groq_key.strip(),
                            "name":name,"email":email,"used":used,"quota":total_q}
                        st.rerun()
                    except RuntimeError as e: st.error(str(e))
                    except Exception as e: st.error(f"Lỗi: {e}")
        with st.expander("📌 Lấy Dropbox Access Token"):
            st.markdown("""
1. Vào **[dropbox.com/developers/apps](https://www.dropbox.com/developers/apps)**
2. **"Create app"** → Scoped access → Full Dropbox
3. Vào tab **"Permissions"** → bật: `files.content.read`, `files.content.write`, `account_info.read`
4. Tab **"Settings"** → cuộn xuống **"Generated access token"** → **Generate** → Copy
            """)
        with st.expander("📌 Chưa có Groq API Key?"):
            st.markdown("1. Vào **[console.groq.com](https://console.groq.com)**\n2. Sign up miễn phí\n3. API Keys → Create new key")
        st.markdown('</div>',unsafe_allow_html=True)
    st.stop()

# ═══════════════════════════════════════════════
# UI — MAIN APP
# ═══════════════════════════════════════════════
sess=st.session_state["session"]; groq_key=sess["groq_key"]

c1,c2=st.columns([3,1])
with c1:
    used=sess.get("used",0)/1e9; total=sess.get("quota",1)/1e9
    st.markdown(f'<div class="user-badge">✅ {sess.get("name","User")} ({sess.get("email","")}) · Dropbox · {used:.1f}/{total:.0f} GB</div>',unsafe_allow_html=True)
with c2:
    if st.button("🚪 Đăng xuất",use_container_width=True):
        st.session_state.pop("session",None); st.session_state.pop("videos",None); st.rerun()

st.divider()

with st.expander("📁 Quét video từ Dropbox",expanded=True):
    cp1,cp2=st.columns([3,1])
    with cp1: folder_path=st.text_input("Thư mục Dropbox",value="/",label_visibility="collapsed",
                                          placeholder="/ = toàn bộ, hoặc /Videos")
    with cp2: scan_btn=st.button("🔍 Quét",use_container_width=True,type="primary")
    if scan_btn:
        with st.spinner("Đang quét Dropbox…"):
            try:
                fp = folder_path.strip()
                videos = collect_videos(sess["token"], fp)
                st.session_state["videos"] = videos
                st.success(f"Tìm thấy **{len(videos)}** video")
            except Exception as e: st.error(str(e))
    if st.session_state.get("videos"):
        for v in st.session_state["videos"]:
            mb=v["size"]/1e6; col=("#f38ba8" if mb>500 else "#a6e3a1")
            st.markdown(f'<div class="video-card"><b>📹 {v["name"]}</b><br><small style="color:#666">{v["path"]}</small><br><small style="color:{col}">💾 {mb:.1f} MB</small></div>',unsafe_allow_html=True)

st.divider()

tab_caption,tab_music,tab_shorts,tab_t2i,tab_logo=st.tabs(
    ["🎬 Auto Caption","🎵 Background Music","✂️ YouTube Shorts","🖼️ Text to Image","🏷️ Add Logo"])

# ════════════════════════════════════════════════
# TAB 1: AUTO CAPTION
# ════════════════════════════════════════════════
with tab_caption:
    l1,r1=st.columns([1,1.6],gap="large")
    with l1:
        st.subheader("🎨 Caption Style")
        with st.expander("🔤 Font",expanded=True):
            FONTS=["Arial","Arial Black","Helvetica","Verdana","Impact","Georgia","Courier New"]
            fn=st.selectbox("Font",FONTS); fs=st.slider("Size",10,60,20); fb=st.checkbox("Bold")
        with st.expander("🎨 Màu sắc",expanded=True):
            ca,cb=st.columns(2)
            with ca: pc=st.color_picker("Màu chữ","#FFFFFF")
            with cb: oc=st.color_picker("Màu viền","#000000")
            ow=st.slider("Viền",0,5,2); sh=st.slider("Shadow",0,3,1)
        with st.expander("📍 Vị trí",expanded=True):
            POS=[["Trên trái","Trên giữa","Trên phải"],["","Giữa màn",""],["Dưới trái","Dưới giữa","Dưới phải"]]
            if "cap_pos" not in st.session_state: st.session_state["cap_pos"]="Dưới giữa"
            for row in POS:
                cols=st.columns(3)
                for ci,lbl in enumerate(row):
                    if not lbl: continue
                    if cols[ci].button(lbl,key=f"cp_{lbl}",type="primary" if st.session_state["cap_pos"]==lbl else "secondary",use_container_width=True):
                        st.session_state["cap_pos"]=lbl; st.rerun()
            mv=st.slider("MarginV",0,120,35)
        st.markdown("---"); st.subheader("📹 Chọn video")
        if not st.session_state.get("videos"): st.info("Quét thư mục ở trên trước.")
        else:
            vids=st.session_state["videos"]
            opts={f"{v['name']} ({v['size']/1e6:.0f}MB)":v for v in vids}
            sel=st.multiselect("Video:",list(opts.keys()),key="cap_sel")
            selected=[opts[k] for k in sel]
            if selected:
                st.info(f"**{len(selected)}** video")
                if st.button("🚀 Tạo Caption",type="primary",use_container_width=True):
                    st.session_state["cap_queue"]={"videos":selected,
                                                    "style":build_ass_style(fn,fs,pc,oc,fb,ow,sh,mv,st.session_state["cap_pos"])}
    with r1:
        st.subheader("⚡ Tiến trình")
        if "cap_queue" in st.session_state:
            cq=st.session_state.pop("cap_queue"); ok=0
            for i,v in enumerate(cq["videos"]):
                st.markdown(f"#### [{i+1}/{len(cq['videos'])}] `{v['name']}`")
                if process_video(sess,groq_key,v,st.empty(),st.empty(),cq["style"]): ok+=1
                st.markdown("---")
            (st.balloons() or st.success(f"🎉 {ok}/{len(cq['videos'])} done!")) if ok==len(cq["videos"]) else st.warning(f"⚠️ {ok}/{len(cq['videos'])}")
        else:
            st.markdown('<div style="text-align:center;padding:3rem;color:#555"><div style="font-size:3rem">🎬</div><div>Tuỳ chỉnh style → Chọn video → Tạo Caption</div></div>',unsafe_allow_html=True)

# ════════════════════════════════════════════════
# TAB 2: BACKGROUND MUSIC
# ════════════════════════════════════════════════
with tab_music:
    l2,r2=st.columns([1,1.6],gap="large")
    with l2:
        st.subheader("🎵 Background Music")
        murl=st.text_input("🔗 Link audio (.mp3/.wav/.m4a)")
        mvol=st.slider("🔊 Âm lượng nhạc",0.0,1.0,0.15,0.05,format="%.2f")
        st.caption(f"Nhạc nền **{int(mvol*100)}%** | Giọng gốc **100%**")
        st.markdown("---")
        if not st.session_state.get("videos"): st.info("Quét thư mục ở trên trước.")
        else:
            vids=st.session_state["videos"]
            opts2={f"{v['name']} ({v['size']/1e6:.0f}MB)":v for v in vids}
            sel2=[opts2[k] for k in st.multiselect("Video:",list(opts2.keys()),key="mus_sel")]
            if sel2:
                if not murl: st.warning("⚠️ Nhập link audio trước")
                elif st.button("🎵 Thêm nhạc nền",type="primary",use_container_width=True):
                    st.session_state["music_queue"]={"videos":sel2,"url":murl.strip(),"vol":mvol}
    with r2:
        st.subheader("⚡ Tiến trình")
        if "music_queue" in st.session_state:
            mq=st.session_state.pop("music_queue"); ok2=0
            for i,v in enumerate(mq["videos"]):
                st.markdown(f"#### [{i+1}/{len(mq['videos'])}] `{v['name']}`")
                ph_log2=st.empty(); ph_prog2=st.empty(); log2=_make_logger(ph_log2)
                def prog2(val,t=""): ph_prog2.progress(min(val,1.0),text=t)
                ok_this=True
                with tempfile.TemporaryDirectory() as tmp:
                    stem=Path(v["name"]).stem; ext=Path(v["name"]).suffix.lower() or ".mp4"
                    v_loc=os.path.join(tmp,v["name"]); aud=os.path.join(tmp,"bg.mp3")
                    out=os.path.join(tmp,f"{stem}_music{ext}")
                    log2(f"⬇️ Downloading video ({v['size']/1e6:.1f} MB)…")
                    try:
                        url=dbx_get_temp_link(sess["token"],v["path"])
                        download_video(url,v_loc,lambda p:prog2(p*0.25,f"Downloading {p*100:.0f}%"))
                    except Exception as e: log2(f"Download: {e}","error"); ok_this=False
                    if ok_this:
                        log2("⬇️ Downloading audio…")
                        try:
                            ar=requests.get(mq["url"],stream=True,timeout=120); ar.raise_for_status()
                            with open(aud,"wb") as f:
                                for chunk in ar.iter_content(chunk_size=256*1024): f.write(chunk)
                            log2(f"Audio {os.path.getsize(aud)/1e6:.1f} MB","success")
                        except Exception as e: log2(f"Audio: {e}","error"); ok_this=False
                    if ok_this:
                        prog2(0.45,"Mixing…")
                        try: mix_background_music(v_loc,aud,out,mq["vol"],lambda m,k="info":log2(m,k))
                        except Exception as e: log2(f"Mix: {e}","error"); ok_this=False
                    if ok_this:
                        prog2(0.80,"Uploading…"); dbx_path=f'{v["parent_path"]}/{stem}_music{ext}'
                        dbx_upload_file(sess["token"],out,dbx_path)
                        log2("✔ Uploaded","success"); prog2(1.0,"Done!"); ok2+=1
                st.markdown("---")
            (st.balloons() or st.success(f"🎉 {ok2}/{len(mq['videos'])} done!")) if ok2==len(mq["videos"]) else st.warning(f"⚠️ {ok2}/{len(mq['videos'])}")
        else:
            st.markdown('<div style="text-align:center;padding:3rem;color:#555"><div style="font-size:3rem">🎵</div><div>Dán link nhạc → Chọn video → Thêm nhạc</div></div>',unsafe_allow_html=True)

# ════════════════════════════════════════════════
# TAB 3: YOUTUBE SHORTS
# ════════════════════════════════════════════════
with tab_shorts:
    l3,r3=st.columns([1,1.6],gap="large")
    with l3:
        st.subheader("✂️ YouTube Shorts")
        st.caption("AI tự chọn đoạn hay nhất · Crop 9:16 · Upload Dropbox")
        n_shorts=st.radio("Số đoạn Short",[1,3,5],index=1,horizontal=True)
        st.markdown('<div style="background:#1e1e2e;border-left:4px solid #6C63FF;border-radius:8px;padding:.9rem 1rem"><b style="color:#cdd6f4">🤖 Pipeline</b><br><small style="color:#888">Whisper → Llama 3.3-70B → Cắt 30-60s → Crop 9:16 → Upload</small></div>',unsafe_allow_html=True)
        st.markdown("---")
        if not st.session_state.get("videos"): st.info("Quét thư mục ở trên trước.")
        else:
            vids=st.session_state["videos"]
            opts3={f"{v['name']} ({v['size']/1e6:.0f}MB)":v for v in vids}
            sel3=[opts3[k] for k in st.multiselect("Video:",list(opts3.keys()),key="sh_sel")]
            if sel3:
                st.info(f"**{len(sel3)}** video · ~{len(sel3)*n_shorts} Shorts")
                if st.button("🚀 Tạo YouTube Shorts",type="primary",use_container_width=True):
                    st.session_state["shorts_queue"]={"videos":sel3,"n":n_shorts}
    with r3:
        st.subheader("⚡ Tiến trình")
        if "shorts_queue" in st.session_state:
            sq=st.session_state.pop("shorts_queue"); all_clips=[]
            for i,v in enumerate(sq["videos"]):
                st.markdown(f"#### [{i+1}/{len(sq['videos'])}] `{v['name']}`")
                clips=process_shorts(sess,groq_key,v,sq["n"],st.empty(),st.empty())
                all_clips.extend(clips); st.markdown("---")
            if all_clips:
                st.balloons(); st.success(f"🎉 {len(all_clips)} Shorts!")
                for i,c in enumerate(all_clips):
                    st.markdown(f'<div class="video-card"><b>#{i+1} {c["title"]}</b><br><small style="color:#a6e3a1">✔ {c["filename"]}</small><br><small style="color:#888">{c["end"]-c["start"]:.0f}s · {c["reason"]}</small></div>',unsafe_allow_html=True)
        else:
            st.markdown('<div style="text-align:center;padding:3rem;color:#555"><div style="font-size:3rem">✂️</div><div>Chọn video → Tạo Shorts</div></div>',unsafe_allow_html=True)

# ════════════════════════════════════════════════
# TAB 4: TEXT TO IMAGE
# ════════════════════════════════════════════════
with tab_t2i:
    l4,r4=st.columns([1,1.4],gap="large")
    with l4:
        st.subheader("🖼️ Text to Image")
        or_key=st.text_input("🔑 OpenRouter API Key",type="password",placeholder="sk-or-…")
        ml=st.selectbox("🤖 Model",list(T2I_MODELS.keys())); mid=T2I_MODELS[ml]
        if "free" in mid: st.success("✅ Miễn phí!")
        rl=st.radio("📐 Tỷ lệ",list(ASPECT_RATIOS.keys()),horizontal=True)
        with st.expander("⚙️ Cài đặt"): npr=st.radio("Ảnh/prompt",[1,2],index=1,horizontal=True); sb=st.number_input("Seed",value=42)
        neg=st.text_area("🚫 Negative","blurry, low quality, watermark",height=60)
        st.markdown("---"); st.caption("Mỗi dòng = 1 prompt.")
        bt=st.text_area("Prompts",placeholder="A mountain landscape\nPortrait...",height=200,label_visibility="collapsed")
        rp=[p.strip() for p in bt.strip().splitlines() if p.strip()]; np2=len(rp)
        if np2>0: st.info(f"**{np2}** prompts · **{np2*npr}** ảnh")
        st.markdown("---")
        dfid=st.session_state["videos"][0]["parent_path"] if st.session_state.get("videos") else "/"
        sfid=st.text_input("📁 Dropbox folder để lưu ảnh",value=dfid,help="VD: /AI_Images"); ipfx=st.text_input("Tiền tố","ai_image")
        can=bool(or_key and np2>0)
        if not or_key: st.warning("⚠️ Cần OpenRouter API Key")
        go=st.button(f"🎨 Generate {np2*npr} ảnh",type="primary",use_container_width=True,disabled=not can)
        with st.expander("📌 OpenRouter Key"): st.markdown("1. [openrouter.ai/keys](https://openrouter.ai/keys)\n2. Sign up → free credits\n3. Create Key → Copy")
    with r4:
        st.subheader("⚡ Batch Progress")
        if go and can:
            fid=int(sfid.strip()) if sfid.strip().isdigit() else 0
            if "t2i_cnt" not in st.session_state: st.session_state["t2i_cnt"]=1
            total=np2*npr; done=0; errs=[]; gal=[]
            obar=st.progress(0.0,text=f"0/{total} ảnh"); ostat=st.empty()
            slots=[st.empty() for _ in range(np2)]; st.markdown("---")
            for pi,prompt in enumerate(rp):
                sp=prompt[:70]+("…" if len(prompt)>70 else "")
                slots[pi].markdown(f'<div class="log-box">⏳ [{pi+1}/{np2}] {sp}</div>',unsafe_allow_html=True)
                try:
                    imgs=generate_images(mid,prompt,neg,ASPECT_RATIOS[rl],npr,or_key,int(sb)+pi*100)
                    saved=[]
                    for img in imgs:
                        cnt=st.session_state["t2i_cnt"]; fn2=f"{ipfx}_{cnt:03d}.png"; st.session_state["t2i_cnt"]+=1
                        dbx_img_path=f"{sfid.rstrip('/')}/{fn2}" if sfid.strip("/") else f"/{fn2}"
                        dbx_upload_bytes(sess["token"],img,dbx_img_path)
                        saved.append(fn2); gal.append((fn2,img)); done+=1
                    slots[pi].markdown(f'<div class="log-box log-success">✔ [{pi+1}/{np2}] {sp}<br><small>💾 {", ".join(saved)}</small></div>',unsafe_allow_html=True)
                except Exception as e:
                    slots[pi].markdown(f'<div class="log-box log-error">✖ [{pi+1}/{np2}] {sp}<br><small>{str(e)[:120]}</small></div>',unsafe_allow_html=True)
                    errs.append(str(e)[:120])
                obar.progress((pi+1)/np2,text=f"{done}/{total} ảnh · Prompt {pi+1}/{np2}")
                if gal:
                    rec=gal[-6:]; gc=st.columns(min(3,len(rec)))
                    for gi,(gn,gb) in enumerate(rec): gc[gi%3].image(gb,caption=gn,use_container_width=True)
            obar.progress(1.0,"Done!")
            if not errs: st.balloons(); ostat.success(f"🎉 {done}/{total} ảnh đã lưu!")
            else: ostat.warning(f"⚠️ {done}/{total} ảnh · {len(errs)} lỗi")
            if errs:
                with st.expander(f"❌ {len(errs)} lỗi"):
                    for e in errs: st.text(e)
        else:
            st.markdown('<div style="text-align:center;padding:4rem;color:#555;border:2px dashed #333;border-radius:12px"><div style="font-size:4rem">🖼️</div><div style="margin-top:1rem">Dán prompts → Generate</div></div>',unsafe_allow_html=True)

# ════════════════════════════════════════════════
# TAB 5: ADD LOGO
# ════════════════════════════════════════════════
with tab_logo:
    l5,r5=st.columns([1,1.6],gap="large")
    with l5:
        st.subheader("🏷️ Add Logo / Watermark")
        logo_url=st.text_input("🔗 Link URL logo (.png/.jpg)",placeholder="https://example.com/logo.png")
        if logo_url.strip():
            try: st.image(logo_url.strip(),width=120,caption="Preview")
            except: st.warning("Không load được ảnh")
        st.markdown("---")
        logo_width=st.number_input("Enter width of logo or watermark image (px)",
                                    min_value=10,max_value=500,value=30,step=5)
        st.markdown("**📍 Vị trí**")
        POS_LOGO=[["Góc trên trái","","Góc trên phải"],["","Giữa màn hình",""],["Góc dưới trái","","Góc dưới phải"]]
        if "logo_pos" not in st.session_state: st.session_state["logo_pos"]="Góc trên trái"
        for row in POS_LOGO:
            cp=st.columns(3)
            for ci,lbl in enumerate(row):
                if not lbl: continue
                if cp[ci].button(lbl,key=f"lp_{lbl}",type="primary" if st.session_state["logo_pos"]==lbl else "secondary",use_container_width=True):
                    st.session_state["logo_pos"]=lbl; st.rerun()
        lpos=st.session_state["logo_pos"]; st.caption(f"📍 {lpos}")
        lmargin=st.slider("Khoảng cách mép (px)",0,80,0,2)
        lrmbg=st.toggle("Tự động xoá nền logo",value=True,help="Phát hiện màu nền từ góc logo và xoá → trong suốt")
        st.markdown("---"); st.markdown("**📹 Chọn video**")
        if not st.session_state.get("videos"): st.info("Quét thư mục ở trên trước.")
        else:
            vids=st.session_state["videos"]
            opts5={f"{v['name']} ({v['size']/1e6:.0f}MB)":v for v in vids}
            sel5=[opts5[k] for k in st.multiselect("Video:",list(opts5.keys()),key="logo_sel")]
            if sel5:
                st.info(f"**{len(sel5)}** video")
                if not logo_url.strip(): st.warning("⚠️ Nhập URL logo trước")
                elif st.button("🏷️ Thêm Logo",type="primary",use_container_width=True):
                    st.session_state["logo_queue"]={"videos":sel5,"url":logo_url.strip(),
                                                     "pos":lpos,"width":int(logo_width),
                                                     "margin":lmargin,"remove_bg":lrmbg}
    with r5:
        st.subheader("⚡ Tiến trình")
        if "logo_queue" in st.session_state:
            lq=st.session_state.pop("logo_queue"); okl=0
            for i,v in enumerate(lq["videos"]):
                st.markdown(f"#### [{i+1}/{len(lq['videos'])}] `{v['name']}`")
                ok=process_logo_video(sess,v,lq["url"],lq["pos"],lq["width"],lq["margin"],lq["remove_bg"],st.empty(),st.empty())
                if ok: okl+=1
                st.markdown("---")
            (st.balloons() or st.success(f"🎉 {okl}/{len(lq['videos'])} video đã thêm logo!")) if okl==len(lq["videos"]) else st.warning(f"⚠️ {okl}/{len(lq['videos'])}")
        else:
            st.markdown('<div style="text-align:center;padding:4rem;color:#555"><div style="font-size:3.5rem">🏷️</div><div style="margin-top:1rem">Nhập URL logo · chọn vị trí<br>chọn video · nhấn <b>Thêm Logo</b><br><small style="color:#444">Width mặc định 30px · sát góc · tự xoá nền</small></div></div>',unsafe_allow_html=True)
