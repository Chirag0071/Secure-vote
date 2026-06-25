import os
import time
import jwt
import bcrypt

JWT_SECRET = os.environ.get("SECUREVOTE_JWT_SECRET", "dev-secret-change-in-production")
JWT_ALGO = "HS256"
TOKEN_TTL_SECONDS = 60 * 60 * 4  # 4 hours

def hash_password(plain_password):
    return bcrypt.hashpw(plain_password.encode(), bcrypt.gensalt()).decode()

def verify_password(plain_password, password_hash):
    return bcrypt.checkpw(plain_password.encode(), password_hash.encode())

def issue_token(username):
    payload = {"sub": username, "iat": int(time.time()), "exp": int(time.time()) + TOKEN_TTL_SECONDS}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)

def verify_token(token):
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        return payload["sub"]
    except jwt.PyJWTError:
        return None
