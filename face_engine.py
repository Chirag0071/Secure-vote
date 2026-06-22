import base64
import io
import os
import numpy as np
import face_recognition
from PIL import Image
from cryptography.fernet import Fernet

KEY_PATH = os.path.join(os.path.dirname(__file__), "secret.key")

MATCH_TOLERANCE = 0.5
# EAR thresholds for blink detection
EAR_CLOSED_THRESHOLD = 0.21
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

def match(live_encoding, stored_encrypted_blob):
    """Returns (is_match: bool, distance: float)."""
    stored_encoding = decrypt_encoding(stored_encrypted_blob)
    distance = float(face_recognition.face_distance([stored_encoding], live_encoding)[0])
    return distance <= MATCH_TOLERANCE, distance

def find_duplicate(live_encoding, all_encodings):
    """
    all_encodings: [(voter_id, encrypted_blob), ...] from database.list_all_encodings().

    Returns the voter_id this face is already registered under, or None.
    This is the 1:N check /api/register runs so the same person can't enroll
    twice under two different Voter IDs and then vote twice. (Authentication
    at vote time, by contrast, is deliberately 1:1 -- it only ever checks the
    claimed voter_id's own stored encoding.)
    """
    for voter_id, blob in all_encodings:
        is_match, _ = match(live_encoding, blob)
        if is_match:
            return voter_id
    return None

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
    Returns (is_live: bool, reason: str).
    """
    if len(frame_burst_rgb) < MIN_FRAMES_FOR_BLINK:
        return False, "Not enough frames captured for liveness check."

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

    if len(ear_sequence) < MIN_FRAMES_FOR_BLINK:
        return False, "Could not consistently detect eyes. Hold still and face the camera."

    saw_closed = False
    was_open_before = False
    for ear in ear_sequence:
        is_open = ear > EAR_CLOSED_THRESHOLD
        if not is_open and was_open_before:
            saw_closed = True
        if saw_closed and is_open:
            return True, "Blink detected."
        was_open_before = is_open

    return False, "No blink detected. This may be a static photo. Please blink naturally and retry."
