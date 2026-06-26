import base64
import io
import os

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import numpy as np
import cv2
from PIL import Image
from cryptography.fernet import Fernet
from insightface.app import FaceAnalysis

KEY_PATH = os.path.join(os.path.dirname(__file__), "secret.key")

# Starting points -- see "Honest testing note" above. Smaller = stricter.
MATCH_TOLERANCE = 0.55       # voting-time 1:1 check (cosine similarity >= 0.45)
DUPLICATE_TOLERANCE = 0.62   # registration-time 1:N check, deliberately looser

RELATIVE_DROP_THRESHOLD = 0.08  # 8% dip from the burst's own open-eye baseline
MIN_FRAMES_FOR_BLINK = 3

# Standard 68-point (ibug/300-W) eye indices -- same convention dlib used.
RIGHT_EYE_IDX = list(range(36, 42))
LEFT_EYE_IDX = list(range(42, 48))

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
_face_app = None

def _get_face_app():
    global _face_app
    if _face_app is None:
        print("[SecureVote] Loading ArcFace (buffalo_s) models -- may download on first run...")
        _face_app = FaceAnalysis(
            name="buffalo_s",
            allowed_modules=["detection", "recognition", "landmark_3d_68"],
            providers=["CPUExecutionProvider"],
        )
    
        _face_app.prepare(ctx_id=0, det_size=(320, 320))
        print("[SecureVote] ArcFace models ready.")
    return _face_app

def warm_up():
    """
    Forces the ArcFace models to load now, rather than lazily on whatever
    request happens to need them first. Meant to be called once at server
    startup. Without this, a deployed instance's first registration/vote
    attempt after a cold start has to wait for model loading (and on a host
    with an ephemeral filesystem, possibly a ~300MB download) inline with
    the user's own request -- slow enough that a frontend fetch() with no
    error handling can sit on "Submitting..." indefinitely.
    """
    _get_face_app()

def decode_base64_image(b64_string):
    """Accepts a data URL or raw base64 string, returns an RGB numpy array."""
    if "," in b64_string:
        b64_string = b64_string.split(",", 1)[1]
    img_bytes = base64.b64decode(b64_string)
    image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    return np.array(image)

def _detect_faces(image_rgb):
    # InsightFace follows OpenCV convention and expects BGR, not RGB.
    image_bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
    return _get_face_app().get(image_bgr)

def extract_encoding(image_rgb):
    """
    Returns (encoding, error). encoding is a 512-d numpy array (ArcFace's
    normalized embedding) or None. error is a human-readable string if
    extraction failed.
    """
    faces = _detect_faces(image_rgb)
    if len(faces) == 0:
        return None, "No face detected. Make sure your face is clearly visible."
    if len(faces) > 1:
        return None, "Multiple faces detected. Only one person should be in frame."
    return faces[0].normed_embedding.astype(np.float64), None

def encrypt_encoding(encoding):
    raw_bytes = encoding.astype(np.float64).tobytes()
    return _fernet.encrypt(raw_bytes)

def decrypt_encoding(blob):
    raw_bytes = _fernet.decrypt(blob)
    return np.frombuffer(raw_bytes, dtype=np.float64)

def _distance(live_encoding, stored_encrypted_blob):
    stored_encoding = decrypt_encoding(stored_encrypted_blob)
    if stored_encoding.shape != live_encoding.shape:
        return 2.0  # max possible value of (1 - cosine similarity)
    cosine_similarity = float(np.dot(live_encoding, stored_encoding))
    return 1.0 - cosine_similarity

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
    EAR values computed -- meant for the audit log only, not the voter's
    browser, so it can stay verbose without being confusing or unprofessional
    in the UI.
    """
    if len(frame_burst_rgb) < MIN_FRAMES_FOR_BLINK:
        return False, "Not enough frames captured for liveness check.", "insufficient frames captured"

    ear_sequence = []
    for frame in frame_burst_rgb:
        faces = _detect_faces(frame)
        if not faces:
            continue
        landmarks = getattr(faces[0], "landmark_3d_68", None)
        if landmarks is None:
            continue
        landmarks_2d = np.asarray(landmarks)[:, :2]
        left_ear = _eye_aspect_ratio(landmarks_2d[LEFT_EYE_IDX])
        right_ear = _eye_aspect_ratio(landmarks_2d[RIGHT_EYE_IDX])
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