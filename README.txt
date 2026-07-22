AI TRADING DESK — FRONTEND
==========================

What this is
------------
A one-file website (index.html) for your trading API at:
https://ai-trading-platform-1-5ymn.onrender.com

It reads your API's own map (/openapi.json), finds the advice,
portfolio and trade endpoints by itself, and shows them as a
dashboard: type a ticker, get a stamped BUY / SELL / HOLD.


STEP 1 — Give your backend permission (one-time, required)
----------------------------------------------------------
Browsers block websites from calling APIs on other domains unless
the API allows it (this is called CORS). Add the permission:

1. On GitHub, open the backend folder and find the Python file
   that contains the line:  app = FastAPI(
   (it is probably backend/app/main.py or backend/main.py)
2. Click the pencil icon to edit it.
3. On a new line DIRECTLY AFTER the app = FastAPI(...) line,
   paste this block:

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

4. Click "Commit changes". Render redeploys by itself (~3 min).

If you skip this step, the dashboard will load but show a red
"browser was blocked" notice — with these same instructions.


STEP 2 — Put the frontend online (2 minutes)
--------------------------------------------
1. Go to:  https://app.netlify.com/drop
2. Sign in (GitHub login is fine).
3. Drag this ZIP file onto the page.
   (If it complains, unzip first and drag the folder instead.)
4. Netlify gives you a link like  something.netlify.app
   — that is your website. Open it in Chrome. Done.

You can rename the link later: Site settings -> Change site name.


Good to know
------------
* First visit after 15 quiet minutes takes up to a minute —
  the free Render server is waking up. The page says so while
  it waits; that is normal.
* If your Render URL ever changes, either edit the API_BASE line
  near the top of index.html, or click the gear (top-right) on
  the site, or open the site as:
  yoursite.netlify.app/?api=https://new-url.onrender.com
* Paper money only. Not financial advice.
