"""
face_engine.py
Biometric logic, backed by dlib/face_recognition -- reverted from the
ArcFace/InsightFace backend specifically because of memory. InsightFace's
lightest viable pack (buffalo_s, with det_size reduced and single-threaded
BLAS execution forced) still hit `Out of memory (used over 512Mi)` on
Render's free tier. dlib is a fundamentally lighter pipeline: one
self-contained model, no onnxruntime session overhead, no separate
detection/recognition/landmark models loaded as independent ONNX sessions.
This trades away ArcFace's better raw accuracy for actually fitting in
512MB -- see DEPLOY.md for the honest tradeoff writeup.

A real, concrete upside of this revert: dlib's pretrained models are
bundled into the `face_recognition_models` package at install time, not
downloaded over the network at runtime. No more "may download on first
run" cold-start behavior at all.

Every function here keeps the same name/signature work that's accumulated
since the original version -- extract_encoding, match, find_duplicate,
check_liveness, encrypt_encoding, decode_base64_image, MIN_FRAMES_FOR_BLINK
-- so app.py and database.py need zero changes for this swap, same as the
ArcFace swap before it.

Embeddings: dlib gives 128-d (not L2-normalized) embeddings. Two embeddings
are compared via face_recognition's own Euclidean distance helper --
smaller distance means more alike, same convention used throughout this
project regardless of which embedding model is underneath.

Liveness detection (identical math to the ArcFace version, different
landmark source): a short burst of frames is captured client-side over ~2
seconds, and the Eye Aspect Ratio (EAR) is tracked per frame looking for a
relative dip from the burst's own open-eye baseline -- that's a real blink.
This relative-drop approach (rather than a fixed absolute EAR threshold)
was itself a real bug fix earlier in this project: the classic EAR
threshold from the original research paper (0.21) was calibrated for
dlib's specific landmark geometry and never actually needed retuning here,
since this project went straight from dlib to InsightFace and back --
but the relative-drop math is backend-agnostic by design, so it carries
over unchanged regardless.

Honest limitation (see README): this does NOT defeat a high-quality
pre-recorded video of the real person blinking, or a deepfake. That needs
depth sensors/IR or challenge-response checks, out of scope here.
"""

import base64
import io
import os
import numpy as np
import face_recognition
from PIL import Image
from cryptography.fernet import Fernet

KEY_PATH = os.path.join(os.path.dirname(__file__), "secret.key")

# dlib/face_recognition's own Euclidean distance over 128-d embeddings.
# These are the values established (and real-world tested against actual
# lockout/duplicate-detection behavior) before this project ever moved to
# ArcFace -- restored here as-is, not re-guessed.
MATCH_TOLERANCE = 0.5        # voting-time 1:1 check
DUPLICATE_TOLERANCE = 0.6    # registration-time 1:N check, deliberately looser

# Relative drop required somewhere in the liveness burst to count as a real
# blink (8% dip from the burst's own open-eye baseline). Backend-agnostic --
# doesn't care which landmark source produced the EAR values.
RELATIVE_DROP_THRESHOLD = 0.08
MIN_FRAMES_FOR_BLINK = 3


def _get_or_create_key():
    env_key = os.environ.get("SECUREVOTE_FERNET_KEY")
    if env_key:
        return env_key.encode()

    if os.path.exists(KEY_PATH):
        with open(KEY_PATH, "rb") as f:
            return f.read()

    key = Fernet.generate_key()
    with open(KEY_PATH, "wb") as f:
        f.write(key)
    print(
        "[SecureVote] No SECUREVOTE_FERNET_KEY set -- generated one at "
        f"{KEY_PATH}. On Render (or any host with an ephemeral filesystem), "
        "set SECUREVOTE_FERNET_KEY explicitly or this key -- and every "
        "registered voter's face data -- will be lost on the next redeploy."
    )
    return key


_fernet = Fernet(_get_or_create_key())


def warm_up():
    """
    Forces dlib's models to load now rather than on whatever request needs
    them first. Much less critical than it was for InsightFace (no network
    download, no multi-hundred-MB pack) but kept for consistency and so the
    very first real request isn't the one paying dlib's one-time model-load
    cost either.
    """
    blank = np.zeros((10, 10, 3), dtype=np.uint8)
    face_recognition.face_locations(blank, model="hog")


def decode_base64_image(b64_string):
    """Accepts a data URL or raw base64 string, returns an RGB numpy array."""
    if "," in b64_string:
        b64_string = b64_string.split(",", 1)[1]
    img_bytes = base64.b64decode(b64_string)
    image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    return np.array(image)


def extract_encoding(image_rgb):
    """
    Returns (encoding, error). encoding is a 128-d numpy array or None.
    error is a human-readable string if extraction failed.
    """
    face_locations = face_recognition.face_locations(image_rgb, model="hog")
    if len(face_locations) == 0:
        return None, "No face detected. Make sure your face is clearly visible."
    if len(face_locations) > 1:
        return None, "Multiple faces detected. Only one person should be in frame."

    encodings = face_recognition.face_encodings(image_rgb, known_face_locations=face_locations)
    if not encodings:
        return None, "Could not extract face features. Try better lighting."
    return encodings[0], None


def encrypt_encoding(encoding):
    raw_bytes = encoding.astype(np.float64).tobytes()
    return _fernet.encrypt(raw_bytes)


def decrypt_encoding(blob):
    raw_bytes = _fernet.decrypt(blob)
    return np.frombuffer(raw_bytes, dtype=np.float64)


def _distance(live_encoding, stored_encrypted_blob):
    stored_encoding = decrypt_encoding(stored_encrypted_blob)
    if stored_encoding.shape != live_encoding.shape:
        # Most likely cause: this row was registered under a previous
        # embedding model (different vector size, e.g. ArcFace's 512-d)
        # and never re-registered. Treat as "definitely not the same
        # person" rather than crashing the whole request.
        return 999.0
    return float(face_recognition.face_distance([stored_encoding], live_encoding)[0])


def match(live_encoding, stored_encrypted_blob):
    """Returns (is_match: bool, distance: float)."""
    distance = _distance(live_encoding, stored_encrypted_blob)
    return distance <= MATCH_TOLERANCE, distance


def find_duplicate(live_encoding, all_encodings):
    """
    all_encodings: [(voter_id, encrypted_blob), ...] from database.list_all_encodings().
    Returns (voter_id, distance) for the CLOSEST existing registration within
    DUPLICATE_TOLERANCE, else (None, None).
    """
    best_voter_id = None
    best_distance = None
    for voter_id, blob in all_encodings:
        distance = _distance(live_encoding, blob)
        if distance <= DUPLICATE_TOLERANCE and (best_distance is None or distance < best_distance):
            best_voter_id, best_distance = voter_id, distance
    return best_voter_id, best_distance


def _eye_aspect_ratio(eye_points):
    eye_points = np.array(eye_points)
    p1, p2, p3, p4, p5, p6 = eye_points
    vertical_1 = np.linalg.norm(p2 - p6)
    vertical_2 = np.linalg.norm(p3 - p5)
    horizontal = np.linalg.norm(p1 - p4)
    if horizontal == 0:
        return 0.3  # neutral fallback, avoids div-by-zero
    return (vertical_1 + vertical_2) / (2.0 * horizontal)


def check_liveness(frame_burst_rgb):
    """
    frame_burst_rgb: list of RGB numpy arrays captured over ~2 seconds.
    Returns (is_live: bool, user_reason: str, debug_detail: str).
    user_reason is safe to show the voter. debug_detail includes the actual
    EAR values computed -- meant for the audit log only.
    """
    if len(frame_burst_rgb) < MIN_FRAMES_FOR_BLINK:
        return False, "Not enough frames captured for liveness check.", "insufficient frames captured"

    ear_sequence = []
    for frame in frame_burst_rgb:
        landmarks_list = face_recognition.face_landmarks(frame)
        if not landmarks_list:
            continue
        landmarks = landmarks_list[0]
        if "left_eye" not in landmarks or "right_eye" not in landmarks:
            continue
        left_ear = _eye_aspect_ratio(landmarks["left_eye"])
        right_ear = _eye_aspect_ratio(landmarks["right_eye"])
        ear_sequence.append((left_ear + right_ear) / 2.0)

    ear_debug = "[" + ", ".join(f"{v:.2f}" for v in ear_sequence) + "]"

    if len(ear_sequence) < MIN_FRAMES_FOR_BLINK:
        return (
            False,
            "Could not consistently detect eyes. Hold still and face the camera.",
            f"eyes detected in {len(ear_sequence)}/{len(frame_burst_rgb)} frames",
        )

    baseline = max(ear_sequence)
    lowest = min(ear_sequence)
    relative_drop = (baseline - lowest) / baseline if baseline > 0 else 0.0

    if relative_drop >= RELATIVE_DROP_THRESHOLD:
        return True, "Blink detected.", f"EAR values: {ear_debug}, relative drop: {relative_drop:.1%}"

    return (
        False,
        "No blink detected. This may be a static photo. Please blink naturally and retry.",
        f"EAR values: {ear_debug}, relative drop: {relative_drop:.1%} (need >= {RELATIVE_DROP_THRESHOLD:.0%})",
    )