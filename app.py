"""
app.py
SecureVote -- Face-authenticated voting system for college elections.
FastAPI + MySQL version.

Routes (unchanged in shape from the Flask version):
  Voter-facing:
    GET  /                  Landing page
    GET  /register          Registration form (capture face once)
    POST /api/register      Store encrypted face encoding for a new voter
    GET  /vote               Enter voter ID, then capture a liveness burst
    POST /api/authenticate  Liveness check + face match -> opens a short-lived
                             "authenticated" session, single use
    GET  /ballot             Candidate list (only reachable post-auth)
    POST /api/cast-vote      Cast an anonymized ballot, burn the session

  Admin-facing:
    GET/POST /admin/login
    GET      /admin/dashboard   Voter roll, live tally, audit log
    POST     /admin/candidates  Add a candidate
    GET      /admin/logout

Run with:  uvicorn app:app --reload
(or just `python app.py`, which does the same thing -- see bottom of file)
"""

import os
import time
import datetime
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
load_dotenv()  # must run before importing database -- it reads DB_* env vars at import time

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import database as db
import face_engine
import auth
from schemas import RegisterIn, AuthenticateIn, CastVoteIn

AUTH_WINDOW_SECONDS = 120  # how long a face-authenticated session is valid to cast a vote


def bootstrap():
    db.init_db()
    admin_username = os.environ.get("SECUREVOTE_ADMIN_USERNAME", "admin")
    if not db.get_admin(admin_username):
        default_pw = os.environ.get("SECUREVOTE_ADMIN_PASSWORD", "ChangeMe123!")
        db.create_admin(admin_username, auth.hash_password(default_pw))
        print(f"[SecureVote] Created admin user '{admin_username}' / '{default_pw}' -- change this.")
    if not db.list_candidates():
        db.add_candidate("Anil Mishra (Lok Vikas Party)", "MLA - Lucknow Cantt, Uttar Pradesh")
        db.add_candidate("Sunita Yadav (Nyay Morcha)", "MLA - Lucknow Cantt, Uttar Pradesh")
        db.add_candidate("Rajeev Tripathi (Pragati Dal)", "MLA - Lucknow Cantt, Uttar Pradesh")
        print("[SecureVote] Seeded sample candidates -- replace via admin dashboard.")
    face_engine.warm_up()


@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap()
    yield


app = FastAPI(title="SecureVote", lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SECUREVOTE_SESSION_SECRET", "dev-session-secret-change-me"),
    session_cookie="securevote_session",
    https_only=os.environ.get("SECUREVOTE_HTTPS_ONLY", "true").lower() == "true",
)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def _client_ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


# ---------------- Voter-facing ----------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse(request, "register.html")


@app.post("/api/register")
def api_register(payload: RegisterIn, request: Request):
    voter_id = payload.voter_id.strip()
    name = payload.name.strip()
    email = (payload.email or "").strip()

    if not voter_id or not name or not payload.image:
        return JSONResponse(
            {"ok": False, "error": "Voter ID, name, and a captured photo are required."}, status_code=400
        )

    if db.get_voter(voter_id):
        return JSONResponse({"ok": False, "error": "This Voter ID is already registered."}, status_code=409)

    image_rgb = face_engine.decode_base64_image(payload.image)
    encoding, error = face_engine.extract_encoding(image_rgb)
    if error:
        return JSONResponse({"ok": False, "error": error}, status_code=400)

    duplicate_voter_id, duplicate_distance = face_engine.find_duplicate(encoding, db.list_all_encodings())
    if duplicate_voter_id:
        # Don't reveal the matching voter_id to the caller -- that's exactly
        # the kind of info that helps someone probe who else is registered.
        # It IS logged for officers reviewing the audit log, though.
        db.log_event(
            voter_id,
            "register_duplicate_face",
            detail=f"matches existing registration {duplicate_voter_id} (distance={duplicate_distance:.3f})",
            ip_address=_client_ip(request),
        )
        if os.environ.get("SECUREVOTE_STORE_PHOTOS", "false").lower() == "true":
            db.create_flagged_duplicate(
                attempted_voter_id=voter_id,
                attempted_name=name,
                matched_voter_id=duplicate_voter_id,
                distance=duplicate_distance,
                photo_base64=payload.image,
            )
        return JSONResponse(
            {"ok": False, "error": "This face is already registered under a different Voter ID."},
            status_code=409,
        )

    encrypted = face_engine.encrypt_encoding(encoding)

    # Off by default on purpose -- storing the photo trades away the "raw
    # photo is never persisted" privacy property documented in the README.
    # Enable only for your own testing/admin verification convenience.
    store_photos = os.environ.get("SECUREVOTE_STORE_PHOTOS", "false").lower() == "true"
    photo_to_store = payload.image if store_photos else None

    db.create_voter(voter_id, name, email, encrypted, photo_base64=photo_to_store)
    db.log_event(voter_id, "register", ip_address=_client_ip(request))
    return {"ok": True}


@app.get("/vote", response_class=HTMLResponse)
def vote_page(request: Request):
    return templates.TemplateResponse(request, "vote.html")


@app.post("/api/authenticate")
def api_authenticate(payload: AuthenticateIn, request: Request):
    voter_id = payload.voter_id.strip()
    frames_b64 = payload.frames or []
    ip = _client_ip(request)

    voter = db.get_voter(voter_id)
    if not voter:
        db.log_event(voter_id, "auth_fail", detail="unknown voter_id", ip_address=ip)
        return JSONResponse({"ok": False, "error": "Voter ID not found. Have you registered?"}, status_code=404)

    if db.is_locked(voter):
        db.log_event(voter_id, "auth_fail", detail="locked out", ip_address=ip)
        return JSONResponse(
            {"ok": False, "error": "Too many failed attempts. Try again later or see an election officer."},
            status_code=423,
        )

    if voter["has_voted"]:
        return JSONResponse({"ok": False, "error": "This Voter ID has already cast a ballot."}, status_code=403)

    if len(frames_b64) < face_engine.MIN_FRAMES_FOR_BLINK:
        return JSONResponse(
            {"ok": False, "error": "Capture a short burst of frames (look at the camera for 2 seconds)."},
            status_code=400,
        )

    frames_rgb = [face_engine.decode_base64_image(f) for f in frames_b64]

    is_live, liveness_reason, liveness_debug = face_engine.check_liveness(frames_rgb)
    if not is_live:
        db.record_failed_attempt(voter_id)
        db.log_event(voter_id, "liveness_fail", detail=liveness_debug, ip_address=ip)
        return JSONResponse({"ok": False, "error": liveness_reason}, status_code=400)

    # Use the sharpest/middle frame of the burst for the actual identity match
    match_frame = frames_rgb[len(frames_rgb) // 2]
    encoding, error = face_engine.extract_encoding(match_frame)
    if error:
        db.record_failed_attempt(voter_id)
        db.log_event(voter_id, "auth_fail", detail=error, ip_address=ip)
        return JSONResponse({"ok": False, "error": error}, status_code=400)

    is_match, distance = face_engine.match(encoding, voter["face_encoding"])
    if not is_match:
        db.record_failed_attempt(voter_id)
        db.log_event(voter_id, "auth_fail", detail=f"distance={distance:.3f}", ip_address=ip)
        return JSONResponse(
            {"ok": False, "error": "Face does not match our records for this Voter ID."}, status_code=401
        )

    db.reset_failed_attempts(voter_id)
    db.log_event(voter_id, "auth_success", detail=f"distance={distance:.3f}", ip_address=ip)

    request.session["authenticated_voter_id"] = voter_id
    request.session["auth_expires"] = time.time() + AUTH_WINDOW_SECONDS
    return {"ok": True, "candidates": db.list_candidates()}


def _current_authenticated_voter(request: Request) -> Optional[str]:
    voter_id = request.session.get("authenticated_voter_id")
    expires = request.session.get("auth_expires", 0)
    if not voter_id or time.time() > expires:
        return None
    return voter_id


@app.get("/ballot", response_class=HTMLResponse)
def ballot_page(request: Request):
    voter_id = _current_authenticated_voter(request)
    if not voter_id:
        return RedirectResponse("/vote", status_code=303)
    return templates.TemplateResponse(request, "ballot.html", {"candidates": db.list_candidates()})


@app.post("/api/cast-vote")
def api_cast_vote(payload: CastVoteIn, request: Request):
    voter_id = _current_authenticated_voter(request)
    if not voter_id:
        return JSONResponse({"ok": False, "error": "Session expired. Please re-authenticate."}, status_code=401)

    voter = db.get_voter(voter_id)
    if not voter or voter["has_voted"]:
        return JSONResponse({"ok": False, "error": "This Voter ID has already cast a ballot."}, status_code=403)

    candidate_ids = {c["id"] for c in db.list_candidates()}
    if payload.candidate_id not in candidate_ids:
        return JSONResponse({"ok": False, "error": "Invalid candidate."}, status_code=400)

    # Ballot is stored with NO reference to voter_id -- this is the anonymity boundary.
    db.cast_ballot(payload.candidate_id)
    db.mark_voted(voter_id)
    db.log_event(voter_id, "vote_cast", ip_address=_client_ip(request))  # records THAT they voted, not WHO for

    request.session.pop("authenticated_voter_id", None)
    request.session.pop("auth_expires", None)
    return {"ok": True}


# ---------------- Admin-facing ----------------

def _require_admin(request: Request) -> Optional[str]:
    token = request.cookies.get("admin_token")
    if not token:
        return None
    return auth.verify_token(token)


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request):
    return templates.TemplateResponse(request, "admin_login.html")


@app.post("/admin/login")
def admin_login(request: Request, username: str = Form(...), password: str = Form(...)):
    admin = db.get_admin(username)
    if not admin or not auth.verify_password(password, admin["password_hash"]):
        return templates.TemplateResponse(
            request, "admin_login.html", {"error": "Invalid credentials"}, status_code=401
        )

    token = auth.issue_token(username)
    resp = RedirectResponse("/admin/dashboard", status_code=303)
    resp.set_cookie(
        "admin_token",
        token,
        httponly=True,
        samesite="lax",
        secure=os.environ.get("SECUREVOTE_HTTPS_ONLY", "true").lower() == "true",
        max_age=auth.TOKEN_TTL_SECONDS,
    )
    return resp


@app.get("/admin/logout")
def admin_logout():
    resp = RedirectResponse("/admin/login", status_code=303)
    resp.delete_cookie("admin_token")
    return resp


@app.get("/admin/dashboard", response_class=HTMLResponse)
def admin_dashboard(request: Request, error: Optional[str] = None):
    if not _require_admin(request):
        return RedirectResponse("/admin/login", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin_dashboard.html",
        {
            "voters": db.list_voters(),
            "tally": db.get_tally(),
            "audit_log": db.get_audit_log(),
            "candidates": db.list_candidates(),
            "flagged_duplicates": db.list_flagged_duplicates(),
            "error": error,
            "now": datetime.datetime.utcnow(),
        },
    )


@app.post("/admin/candidates")
def admin_add_candidate(request: Request, name: str = Form(...), position: str = Form(...)):
    if not _require_admin(request):
        return RedirectResponse("/admin/login", status_code=303)
    if name.strip() and position.strip():
        db.add_candidate(name.strip(), position.strip())
    return RedirectResponse("/admin/dashboard", status_code=303)


@app.post("/admin/candidates/{candidate_id}/delete")
def admin_delete_candidate(candidate_id: int, request: Request):
    if not _require_admin(request):
        return RedirectResponse("/admin/login", status_code=303)
    ok, error = db.delete_candidate(candidate_id)
    if not ok:
        return RedirectResponse(f"/admin/dashboard?error={error}", status_code=303)
    return RedirectResponse("/admin/dashboard", status_code=303)


@app.post("/admin/voters/{voter_id}/unlock")
def admin_unlock_voter(voter_id: str, request: Request):
    if not _require_admin(request):
        return RedirectResponse("/admin/login", status_code=303)
    db.reset_failed_attempts(voter_id)
    return RedirectResponse("/admin/dashboard", status_code=303)


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 5000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)