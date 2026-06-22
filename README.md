# SecureVote

A face-authenticated voting system for college, society elections and club-scale elections. Voters verify their identity with their face instead of (or alongside) a password before casting a ballot, and the vote itself is stored in a way that can't be traced back to the voter who cast it.

## What it helps with

- Replacing manual ID checks at small-scale elections (student body, club, society votes) with face-based verification
- Preventing one person from voting twice — both directly (same Voter ID can't vote twice) and indirectly (same face can't register under two different Voter IDs)
- Keeping vote choices anonymous even from whoever administers the system
- Demonstrating a complete authentication + liveness + secrecy pipeline rather than a bare face-match demo

## How it works

**Registration**
1. Voter enters a Voter ID and name, then captures one photo via webcam.
2. The photo is converted into a 128-dimension face encoding using `face_recognition`.
3. That new encoding is compared against every already-registered voter's encoding. If it matches an existing one, registration is rejected — this is what stops the same person enrolling under two different identities.
4. If no match is found, the encoding is encrypted (Fernet/AES) and stored. The raw photo itself is discarded; only the encrypted encoding persists.

**Voting**
1. Voter enters their Voter ID and looks at the camera. The frontend captures a burst of ~10 frames over roughly 2 seconds.
2. Liveness check: eye landmarks are extracted from each frame and the Eye Aspect Ratio (EAR) is tracked across the burst, looking for an open → closed → open pattern (a real blink). This filters out static photos or a phone screen held up to the camera.
3. Identity check: the middle frame of the burst is encoded and compared (1-to-1) against the encryption-decrypted encoding stored for that specific Voter ID.
4. If both checks pass, a short-lived, single-use server-side session is opened and the voter is shown the ballot.

**Casting a vote**
1. The voter selects a candidate and submits.
2. The vote is written to a `ballots` table that has no voter-identifying column at all — not nulled, not hidden, structurally absent. There's no query that joins a cast vote back to a voter.
3. The voter's record is flagged as having voted, and the session is invalidated (single use).

**Admin**
- Separate authentication path: username + password (bcrypt-hashed), session via JWT.
- Dashboard shows the live tally, the voter roll, and an audit log of authentication attempts (register, auth success/fail, liveness fail, vote cast). The audit log records that an event happened, never which candidate was selected.
- Candidates can be added or removed. Removing a candidate who already has votes is blocked at the database level (foreign key constraint), so a tally can't be silently erased.

## Technologies used

| Layer | Tech |
|---|---|
| Web framework | FastAPI, Uvicorn |
| Request validation | Pydantic |
| Database | MySQL, accessed via PyMySQL |
| Face detection/encoding | face_recognition (dlib) |
| Image handling | OpenCV, Pillow |
| Encryption at rest | cryptography (Fernet / AES) |
| Admin auth | bcrypt (password hashing), PyJWT (sessions) |
| Sessions (voter side) | Starlette SessionMiddleware (signed cookie) |
| Frontend | Vanilla HTML/CSS/JS, browser `getUserMedia` API |
| Runtime | Python 3.11.9 |

No external/cloud APIs are called — face matching, encryption, and token signing all run locally on whichever machine hosts the app.

## Project structure

```
secure-vote/
├── app.py              FastAPI routes
├── database.py         MySQL access layer
├── face_engine.py       Face encoding, matching, liveness, duplicate detection
├── auth.py              Admin password hashing + JWT
├── schemas.py           Pydantic request models
├── schema.sql           Standalone DB schema
├── requirements.txt
├── test_connection.py   Standalone MySQL connectivity check
├── templates/            Jinja2 HTML pages
└── static/css, static/js
```

## Errors during face capture

| Error shown | Cause | Fix |
|---|---|---|
| "No face detected" | Face not in frame, too far from camera, or lighting too dark/bright | Center the face, improve lighting, move closer |
| "Multiple faces detected" | More than one face in frame (second person, poster, photo in background) | Make sure only one face is visible |
| "Could not extract face features" | Face at a steep angle, motion blur, or low-contrast lighting | Face the camera directly, hold still, improve lighting |
| "No blink detected. This may be a static photo." | Liveness check (voting step only) found no open-closed-open eye pattern across the captured frames | Look at the camera and blink naturally during the ~2 second capture window; this also triggers on a printed photo or phone screen held up to the camera |
| "Face does not match our records for this Voter ID" | Live face doesn't match the stored encoding for the claimed Voter ID closely enough | Recapture with better lighting/angle; if it persists, the registered photo may have been low quality |
| "This face is already registered under a different Voter ID" | The 1-to-N duplicate check at registration matched an existing voter | Each person can only register once; this is by design |
| "Too many failed attempts. Try again later." | Repeated failed match/liveness attempts on one Voter ID | Temporary lockout (default 5 minutes after 5 failures); resets automatically |

The face-match threshold is controlled by `MATCH_TOLERANCE` in `face_engine.py` (default `0.5`). Lower values are stricter (fewer false matches, more false rejections); higher values are looser. `face_recognition`'s own default is `0.6`, so `0.5` is already on the stricter side — relevant if rejections seem more frequent than expected on a typical laptop webcam.

## Security design notes

- **Encrypted encodings, not photos.** Only a 128-d vector is stored, encrypted with Fernet before it touches the database. Raw photos are never persisted.
- **Vote secrecy is structural.** No column or table links a cast ballot to a voter ID.
- **1-to-1 vs 1-to-N.** Voting authentication is 1-to-1 (does this face match the claimed Voter ID's stored encoding). Registration is 1-to-N (does this face match *any* existing voter). This split is what prevents duplicate enrollment without making every vote a full database scan.
- **Liveness via blink detection (EAR).** Effective against printed photos and screens. Not effective against pre-recorded video of the real person blinking — that requires depth sensors or challenge-response checks, which this project doesn't implement.
- **Lockout on repeated failure.** Prevents unlimited brute-force attempts against the match threshold for a given Voter ID.

## Limitations

- Cannot reliably distinguish identical twins or very close lookalikes — a fundamental limit of face recognition, not a software bug.
- Duplicate-face checking at registration is O(N) — it compares against every existing voter. Workable at college scale (hundreds to low thousands); not how this would be implemented for a national-scale system (would require an indexed vector search, e.g. FAISS).
- Liveness detection is webcam-only and does not defeat video replay or deepfakes.
- Not designed for legally-binding elections (state/national). That scale requires audited secret-ballot guarantees, accessibility accommodations, supervised capture environments, and avoids centralizing biometric data the way this project does for convenience at small scale.

## Setup

Requires Python 3.11.9 and a running MySQL server.

```bash
git clone https://github.com/Chirag0071/Secure-vote.git
cd Secure-vote
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # fill in real DB/admin credentials
python test_connection.py        # verify MySQL connectivity
uvicorn app:app --reload
```

Tables and the admin account are created automatically on first run, using whatever is set in `.env`.

## Deployment

See [`DEPLOY.md`](./DEPLOY.md) for the Render (app hosting) + Aiven (managed MySQL) deployment steps, including the build/start commands and required environment variables.
