import dlib
import numpy as np
import face_recognition_models

_face_detector = dlib.get_frontal_face_detector()
_pose_predictor_68 = dlib.shape_predictor(face_recognition_models.pose_predictor_model_location())
_face_encoder = dlib.face_recognition_model_v1(face_recognition_models.face_recognition_model_location())

# Standard 68-point (ibug/300-W) eye indices -- same convention used
# elsewhere in this project.
_RIGHT_EYE_IDX = list(range(36, 42))
_LEFT_EYE_IDX = list(range(42, 48))


def face_locations(img, model="hog"):
    """Returns a list of dlib.rectangle face bounding boxes."""
    return list(_face_detector(img, 1))


def _raw_landmarks(img, locations):
    return [_pose_predictor_68(img, loc) for loc in locations]


def face_encodings(img, known_face_locations=None, num_jitters=1):
    if known_face_locations is None:
        known_face_locations = face_locations(img)
    raw = _raw_landmarks(img, known_face_locations)
    return [np.array(_face_encoder.compute_face_descriptor(img, shape, num_jitters)) for shape in raw]


def face_landmarks(img):
    """
    Returns a list (one per detected face) of dicts with 'left_eye' and
    'right_eye' keys -- the only parts of face_recognition's richer
    landmark dict this project ever used.
    """
    locations = face_locations(img)
    raw = _raw_landmarks(img, locations)
    results = []
    for shape in raw:
        points = [(p.x, p.y) for p in shape.parts()]
        results.append(
            {
                "right_eye": [points[i] for i in _RIGHT_EYE_IDX],
                "left_eye": [points[i] for i in _LEFT_EYE_IDX],
            }
        )
    return results


def face_distance(known_encodings, face_to_compare):
    if len(known_encodings) == 0:
        return np.empty(0)
    return np.linalg.norm(np.array(known_encodings) - face_to_compare, axis=1)