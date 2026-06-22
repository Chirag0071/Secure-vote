# SecureVote — Face-Authenticated College Election System

A working voting system for college/club/society-scale elections that uses
face recognition as a second authentication factor, with a blink-based
liveness check and an architecture designed so that votes cannot be traced
back to voters — even by someone with full database access.

This was deliberately built as more than the standard "OpenCV + face_recognition"
tutorial project. See **Design decisions** below for what's different and why,
and **Limitations** for an honest account of what it doesn't solve.

---

## Setup

Targets **Python 3.11.9** specifically (the `dlib` wheels behind `face_recognition`
are version-sensitive — if you're on a different Python, this is the first
place to check if installs fail).

```bash
# 1. Python 3.11.9 (via pyenv, or your system installer of choice)
pyenv install 3.11.9
pyenv local 3.11.9

# 2. Virtual env + deps
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 3. MySQL must be running and reachable. Either let the app create the
#    database on first run (it calls CREATE DATABASE IF NOT EXISTS), or
#    run schema.sql by hand:
mysql -u root -p < schema.sql

# 4. Point the app at your MySQL instance (defaults assume localhost/root/no password)
export SECUREVOTE_DB_HOST=localhost
export SECUREVOTE_DB_USER=root
export SECUREVOTE_DB_PASSWORD=yourpassword
export SECUREVOTE_DB_NAME=securevote

# 5. Run it
uvicorn app:app --reload
# (or: python app.py -- does the same thing)
```

Open `http://localhost:5000`. On first run it creates:
- A default admin account: `admin` / `ChangeMe123!` (override with the
  `SECUREVOTE_ADMIN_PASSWORD` env var before first run — **change this in any
  real deployment**)
- Two sample candidates (replace these from the admin dashboard)

Other env vars worth knowing about:
- `SECUREVOTE_SESSION_SECRET` — signs the voter's face-auth session cookie
  (Starlette's `SessionMiddleware`). Set this explicitly outside of local dev.
- `SECUREVOTE_JWT_SECRET` — signs admin JWTs (see `auth.py`).

> Note: `face_recognition` depends on `dlib`, which needs CMake and a C++
> compiler to build on most systems. On Windows, the easiest path is
> `pip install dlib-bin` before installing `face_recognition`. On macOS/Linux,
> `brew install cmake` or `apt install cmake build-essential` first.

### Demo flow
1. Go to **Register**, enter a voter ID + name, capture your face once.
2. Go to **Vote**, enter the same voter ID, look at the camera and blink
   naturally for ~2 seconds.
3. On success you're dropped onto the ballot — pick a candidate, cast.
4. Log into **Admin** to see the live tally, voter roll, and audit log.

---

## Architecture

```
Browser (webcam via getUserMedia)
   │  capture.js: single frame (register) / 10-frame burst (vote)
   ▼
FastAPI app (app.py)
   ├── /api/register     → face_engine.extract_encoding → encrypt → database.create_voter
   ├── /api/authenticate → face_engine.check_liveness (blink)
   │                      → face_engine.match (1:1 against THIS voter_id only)
   │                      → request.session["authenticated_voter_id"], 120s TTL, single use
   │                        (Starlette SessionMiddleware: signed cookie, same model as Flask's session)
   ├── /ballot            → only reachable with a live session
   └── /api/cast-vote     → database.cast_ballot (no voter_id stored) + mark_voted + burn session
   │
   ▼
MySQL (via PyMySQL) — see schema.sql
```

Request bodies for the JSON endpoints (`/api/register`, `/api/authenticate`,
`/api/cast-vote`) are validated by Pydantic models in `schemas.py` before the
route function runs — FastAPI rejects malformed payloads automatically,
which replaces the manual `request.get_json()` + key-checking the Flask
version did by hand.

It's intentionally a **1:1 verification** (does this face match the encoding
already on file for this claimed voter ID?), not 1:N identification (whose
face is this, out of everyone registered). 1:N search is what doesn't scale —
see Limitations.

---

## Design decisions (the part worth discussing in an interview)

**Liveness detection, not just a photo match.** Eye Aspect Ratio (EAR) is
computed across a frame burst to detect a real blink before the photo is
even compared. Defeats the most common low-effort spoof — holding up a
printed photo or a phone screen — without needing depth/IR hardware. The
honest limitation: it does not defeat a pre-recorded video of the real
person blinking. Production systems use challenge-response (e.g. "turn your
head left now") or depth sensors for that; out of scope here, documented as
a known gap rather than ignored.

**Encrypted biometric storage, not raw photos or plaintext vectors.**
Captured photos are never persisted — only a 128-d face encoding, encrypted
with Fernet (AES-128-CBC + HMAC) before it touches disk. The encryption key
lives outside the repo (`secret.key`, generated on first run, gitignored).

**Vote secrecy by schema, not by policy.** The `ballots` table has no
`voter_id` column at all — not "nulled out," structurally absent. There is no
join path from a cast vote back to a voter. The `audit_log` records that
voter STU1 authenticated and that a vote was cast, never which candidate.
This means even an admin with full DB access can see *who voted* and *the
totals*, but never *who voted for whom*.

**Abuse resistance.** Repeated failed face matches lock a voter ID out
temporarily (`record_failed_attempt` / `is_locked` in `database.py`) rather
than allowing unlimited brute-force attempts against the match threshold.

**Auth separation.** Voters authenticate with face + voter ID (no password —
that's the point of the project). Admins get a real password (bcrypt-hashed)
and a JWT session, because the admin panel exposes the voter roll and audit
log and deserves a stronger, more conventional credential.

---

## Limitations (be upfront about these)

- **Twins / siblings / very close lookalikes** can produce false matches at
  any face-similarity threshold loose enough to tolerate normal lighting
  variance.
- **Liveness is webcam-only**, not video-attack-proof — see above.
- **Doesn't scale to 1:N identification.** This system only ever asks "does
  this face match the one encoding for this claimed voter ID?" Scaling to
  "whose face is this, out of 50,000 registered voters?" needs a vector index
  (e.g. FAISS) and the false-positive math gets much worse as N grows.
- **Not suitable for state/national elections as designed.** That scale
  introduces problems this project doesn't attempt to solve: legally
  mandated secret-ballot guarantees under audit, accessibility for voters
  who can't use a webcam reliably, no controlled/supervised capture
  environment for remote voting, and the catastrophic blast radius of a
  centralized biometric database breach. The realistic version of this idea
  at national scale is face match used only as an ID check at a supervised
  physical polling booth — never as the sole basis for casting a vote, and
  never remote.

---

## Deploying

See [`DEPLOY.md`](./DEPLOY.md) for a step-by-step Render + Aiven (MySQL) guide,
including why Render's free tier needs `dlib-bin` instead of building `dlib`
from source, and how the Fernet encryption key needs to be an explicit env
var (not the local file fallback) once the filesystem isn't persistent.

## Stack

FastAPI · Uvicorn · OpenCV · face_recognition (dlib) · MySQL (PyMySQL) ·
cryptography (Fernet) · PyJWT · bcrypt · Pydantic · vanilla JS
(`getUserMedia`, no frontend framework) · Python 3.11.9

## A note on this version vs. testing

This was migrated from an original Flask + SQLite version. The Flask version
was tested end-to-end (full register → authenticate → vote → admin flow) in
a sandboxed environment. This FastAPI + MySQL version could not be run
end-to-end in that same sandbox (no network access to install FastAPI/PyMySQL,
no MySQL server available) — all `.py` files are syntax-checked and every
template was render-tested against real Jinja2 with realistic data (including
the empty-tally and datetime-object edge cases), and the route/DB logic is a
direct, carefully reviewed port of the tested Flask/SQLite logic. Treat your
first local run as the actual integration test, and open an issue with you
(i.e., just flag it back to me) if `uvicorn app:app --reload` throws
anything on startup.
