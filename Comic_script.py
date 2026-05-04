import json
import time
import threading
import requests
from flask import Flask, render_template, request, jsonify, Response
from groq import Groq

app = Flask(__name__)

client = Groq(api_key="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")

# Only 1 image fetched from Pollinations at a time — prevents 429
pollinations_lock = threading.Semaphore(1)

ART_STYLES = {
    "superhero":  "Marvel comic book style, bold ink lines, vibrant colors, action hero art",
    "manga":      "black and white manga style, detailed ink, expressive faces, speed lines",
    "cartoon":    "colorful cartoon animation style, flat colors, Pixar-like characters, cute",
    "watercolor": "soft watercolor illustration, painterly style, gentle colors, storybook art",
}

def call_with_retry(fn, max_retries=4):
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            msg = str(e).lower()
            if "429" in msg or "quota" in msg or "rate_limit" in msg:
                if attempt < max_retries - 1:
                    wait = 5 * (2 ** attempt)
                    print(f"Rate limit. Retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    raise
            else:
                raise

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/proxy-image')
def proxy_image():
    url = request.args.get('url')
    if not url:
        return "No URL", 400

    headers = {"User-Agent": "Mozilla/5.0"}

    with pollinations_lock:          # ← Only 1 request at a time
        time.sleep(3)                # ← 3s gap between each image request
        for attempt in range(5):
            try:
                print(f"Fetching image (attempt {attempt+1}/5)...")
                resp = requests.get(url, headers=headers, timeout=120)
                resp.raise_for_status()
                if len(resp.content) < 500:
                    raise ValueError("Empty image")
                ctype = resp.headers.get("Content-Type", "image/jpeg")
                print(f"Image fetched successfully ({len(resp.content)} bytes)")
                return Response(resp.content, content_type=ctype)
            except Exception as e:
                print(f"Image attempt {attempt+1} failed: {e}")
                if attempt < 4:
                    time.sleep(10)

    return "Image failed after 5 attempts", 503

@app.route('/chat', methods=['POST'])
def chat():
    data     = request.json
    user_msg = data.get('message')
    history  = data.get('history', [])

    formatted = []
    for msg in history:
        role = "user" if msg['role'] == 'user' else "assistant"
        formatted.append({"role": role, "content": msg['text']})

    try:
        def do_chat():
            msgs = [{"role": "system", "content": "You are a comic assistant. Help brainstorm a 4-panel comic. Keep it short. End with: IDEA READY: <one line summary>"}]
            msgs.extend(formatted)
            msgs.append({"role": "user", "content": user_msg})
            r = client.chat.completions.create(model="llama-3.3-70b-versatile", messages=msgs, max_tokens=500)
            return r.choices[0].message.content

        reply = call_with_retry(do_chat)
        idea  = None
        if "IDEA READY:" in reply:
            idx  = reply.find("IDEA READY:") + len("IDEA READY:")
            idea = reply[idx:].strip().split("\n")[0]
        return jsonify({"reply": reply, "idea": idea})

    except Exception as e:
        print(f"Chat error: {e}")
        return jsonify({"reply": "AI is busy. Try again.", "idea": None})

@app.route('/generate', methods=['POST'])
def generate():
    data         = request.json
    story_prompt = data.get('prompt')
    style_key    = data.get('style', 'superhero')
    style_prompt = ART_STYLES.get(style_key, ART_STYLES['superhero'])

    prompt = f"""Return ONLY valid JSON, no extra text:
{{
  "title": "COMIC TITLE",
  "panels": [
    {{"caption": "caption", "image_prompt": "detailed visual scene description", "dialogue": "speech"}},
    {{"caption": "caption", "image_prompt": "detailed visual scene description", "dialogue": "speech"}},
    {{"caption": "caption", "image_prompt": "detailed visual scene description", "dialogue": "speech"}},
    {{"caption": "caption", "image_prompt": "detailed visual scene description", "dialogue": "speech"}}
  ]
}}
Story: {story_prompt}"""

    try:
        def do_gen():
            r = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1000
            )
            return r.choices[0].message.content

        raw   = call_with_retry(do_gen)
        clean = raw.replace("```json","").replace("```","").strip()
        s, e  = clean.find('{'), clean.rfind('}')
        script = json.loads(clean[s:e+1])
    except Exception as ex:
        print(f"Script error: {ex}")
        return jsonify({"error": "Script generation failed. Try again."}), 500

    panels_data = []
    for i, p in enumerate(script.get("panels", [])):
        img_prompt  = p.get('image_prompt', 'comic scene')
        full        = f"{img_prompt}, {style_prompt}, comic panel, no text, high quality"
        encoded     = requests.utils.quote(full)
        poll_url    = f"https://image.pollinations.ai/prompt/{encoded}?width=512&height=400&nologo=true&seed={i*42+7}"
        proxy_url   = f"/proxy-image?url={requests.utils.quote(poll_url)}"
        panels_data.append({
            "caption":   p.get("caption", ""),
            "dialogue":  p.get("dialogue", ""),
            "image_url": proxy_url
        })

    return jsonify({"title": script.get("title", "MY COMIC"), "panels": panels_data})

if __name__ == '__main__':
    print("Starting server at http://127.0.0.1:5000")
    app.run(debug=True, threaded=True)