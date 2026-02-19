# Chat Transit — Render Deployment Guide

## Folder Structure to Push to GitHub

```
chat-transit/                   ← your GitHub repo root
├── render.yaml                 ← Render blueprint (auto-configures both services)
├── backend/
│   ├── main.py
│   ├── requirements.txt
│   └── build.sh
└── frontend/
    ├── index.html
    ├── manifest.json
    └── sw.js
```

---

## Step 1 — Push to GitHub

```bash
# Create a new repo on github.com first, then:

git init
git add .
git commit -m "initial: chat transit"
git remote add origin https://github.com/YOURUSERNAME/chat-transit.git
git push -u origin main
```

---

## Step 2 — Deploy Backend on Render

1. Go to **https://render.com** → Sign up / Log in
2. Click **"New +"** → **"Web Service"**
3. Connect your GitHub account → Select your `chat-transit` repo
4. Fill in these settings:

   | Field | Value |
   |-------|-------|
   | Name | `chat-transit-api` |
   | Root Directory | `backend` |
   | Runtime | `Python 3` |
   | Build Command | `bash build.sh` |
   | Start Command | `uvicorn main:app --host 0.0.0.0 --port $PORT` |
   | Plan | **Free** |

5. Click **"Create Web Service"**
6. Wait for build to complete (~5–8 minutes first time — Chromium is large)
7. Copy your backend URL → looks like: `https://chat-transit-api.onrender.com`

---

## Step 3 — Update Frontend with Backend URL

Open `frontend/index.html` and find this line (near bottom, in the `<script>` tag):

```javascript
const API_BASE = 'https://chat-transit-api.onrender.com';
```

Replace `chat-transit-api` with your actual Render service name if different.

Then commit and push:
```bash
git add frontend/index.html
git commit -m "fix: set render backend url"
git push
```

---

## Step 4 — Deploy Frontend on Render

1. Click **"New +"** → **"Static Site"**
2. Select the same `chat-transit` repo
3. Fill in these settings:

   | Field | Value |
   |-------|-------|
   | Name | `chat-transit-frontend` |
   | Root Directory | `frontend` |
   | Build Command | *(leave empty)* |
   | Publish Directory | `.` |
   | Plan | **Free** |

4. Click **"Create Static Site"**
5. Done — your frontend URL: `https://chat-transit-frontend.onrender.com`

---

## Step 5 — Verify It Works

1. Open `https://chat-transit-frontend.onrender.com`
2. The terminal header should show `● DEMO` (backend in demo mode = working)
3. Paste any ChatGPT share link and click **RUN**

**First request after inactivity will be slow (30–50s)** — that's normal on free tier.
Render spins down the backend after 15 minutes of no traffic.

---

## Troubleshooting

### Build fails at "playwright install-deps"
Add this environment variable in Render dashboard:
```
Key:   PLAYWRIGHT_BROWSERS_PATH
Value: /opt/render/project/.playwright
```

### "No messages found" error
The ChatGPT share link is private or expired. Try a different link.

### Backend shows 502/504
Free tier memory crunch. Wait 30 seconds and retry — Chromium sometimes needs
a second attempt on cold start.

### CORS error in browser console
Make sure `API_BASE` in `index.html` exactly matches your Render backend URL
(no trailing slash).

---

## Environment Variables (Optional)

Set in Render dashboard under your web service → Environment:

| Key | Value | Purpose |
|-----|-------|---------|
| `PLAYWRIGHT_BROWSERS_PATH` | `/opt/render/project/.playwright` | Browser path |
| `PYTHON_VERSION` | `3.11.0` | Pin Python version |

---

## After Deploy — Your URLs

| Service | URL |
|---------|-----|
| Frontend | `https://chat-transit-frontend.onrender.com` |
| Backend API | `https://chat-transit-api.onrender.com` |
| Health Check | `https://chat-transit-api.onrender.com/health` |
| API Docs | `https://chat-transit-api.onrender.com/docs` |

---

## Free Tier Limits

| Limit | Value | Impact |
|-------|-------|--------|
| RAM | 512MB | Chromium may OOM on large chats |
| Sleep | 15min inactivity | Cold start 30–50s |
| Build time | 500 min/mo | ~60 deploys/mo |
| Bandwidth | 100GB/mo | Plenty |
| Static hosting | Unlimited | Frontend always fast |

---

Good luck — if it crashes, just retry. That's the free tier life. 🚀
