import os
import datetime
import contextlib
import pymysql
import pymysql.cursors
 
DB_HOST = os.environ.get("SECUREVOTE_DB_HOST", "localhost")
DB_PORT = int(os.environ.get("SECUREVOTE_DB_PORT", "3306"))
DB_USER = os.environ.get("SECUREVOTE_DB_USER", "root")
DB_PASSWORD = os.environ.get("SECUREVOTE_DB_PASSWORD", "")
DB_NAME = os.environ.get("SECUREVOTE_DB_NAME", "securevote")
 
# Managed MySQL hosts (Aiven, PlanetScale, etc.) require TLS. Set
# SECUREVOTE_DB_SSL_CA to the path of the CA cert they give you (safe to
# commit -- it's a public cert, not a secret). Leave unset for plain local
# MySQL, e.g. on localhost during development.
DB_SSL_CA = os.environ.get("SECUREVOTE_DB_SSL_CA")
 
 
def _ssl_kwargs():
    if not DB_SSL_CA:
        return {}
    return {"ssl_ca": DB_SSL_CA, "ssl_verify_cert": True}
 
 
def get_conn(use_db=True):
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME if use_db else None,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        charset="utf8mb4",
        connect_timeout=10,
        **_ssl_kwargs(),
    )
 
 
def init_db():
    # Step 1: make sure the database itself exists (connect with no db selected).
    conn = pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        charset="utf8mb4",
        connect_timeout=10,
        **_ssl_kwargs(),
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
                f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        conn.commit()
    finally:
        conn.close()
 
    # Step 2: create tables (idempotent).
    with contextlib.closing(get_conn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS voters (
                    voter_id        VARCHAR(64) PRIMARY KEY,
                    name            VARCHAR(255) NOT NULL,
                    email           VARCHAR(255),
                    constituency    VARCHAR(255) NOT NULL DEFAULT '',
                    face_encoding   BLOB NOT NULL,
                    photo_base64    MEDIUMTEXT NULL,
                    has_voted       TINYINT(1) NOT NULL DEFAULT 0,
                    voted_at        DATETIME NULL,
                    registered_at   DATETIME NOT NULL,
                    failed_attempts INT NOT NULL DEFAULT 0,
                    locked_until    DATETIME NULL
                ) ENGINE=InnoDB
                """
            )
            # Migration: voted_at
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'voters' AND COLUMN_NAME = 'voted_at'",
                (DB_NAME,),
            )
            if cur.fetchone()["cnt"] == 0:
                cur.execute("ALTER TABLE voters ADD COLUMN voted_at DATETIME NULL")
            # Migration: photo_base64
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'voters' AND COLUMN_NAME = 'photo_base64'",
                (DB_NAME,),
            )
            if cur.fetchone()["cnt"] == 0:
                cur.execute("ALTER TABLE voters ADD COLUMN photo_base64 MEDIUMTEXT NULL")
            # Migration: constituency on voters
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'voters' AND COLUMN_NAME = 'constituency'",
                (DB_NAME,),
            )
            if cur.fetchone()["cnt"] == 0:
                cur.execute("ALTER TABLE voters ADD COLUMN constituency VARCHAR(255) NOT NULL DEFAULT ''")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS candidates (
                    id           INT AUTO_INCREMENT PRIMARY KEY,
                    name         VARCHAR(255) NOT NULL,
                    party        VARCHAR(255) NOT NULL DEFAULT '',
                    position     VARCHAR(255) NOT NULL,
                    constituency VARCHAR(255) NOT NULL DEFAULT ''
                ) ENGINE=InnoDB
                """
            )
            # Migration: constituency on candidates
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'candidates' AND COLUMN_NAME = 'constituency'",
                (DB_NAME,),
            )
            if cur.fetchone()["cnt"] == 0:
                cur.execute("ALTER TABLE candidates ADD COLUMN constituency VARCHAR(255) NOT NULL DEFAULT ''")
            # Migration: party on candidates
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'candidates' AND COLUMN_NAME = 'party'",
                (DB_NAME,),
            )
            if cur.fetchone()["cnt"] == 0:
                cur.execute("ALTER TABLE candidates ADD COLUMN party VARCHAR(255) NOT NULL DEFAULT ''")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS ballots (
                    id           INT AUTO_INCREMENT PRIMARY KEY,
                    candidate_id INT NOT NULL,
                    cast_at      DATETIME NOT NULL,
                    FOREIGN KEY (candidate_id) REFERENCES candidates(id)
                ) ENGINE=InnoDB
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    id         INT AUTO_INCREMENT PRIMARY KEY,
                    voter_id   VARCHAR(64),
                    event_type VARCHAR(64) NOT NULL,
                    detail     TEXT,
                    ip_address VARCHAR(64),
                    created_at DATETIME NOT NULL
                ) ENGINE=InnoDB
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS admins (
                    id            INT AUTO_INCREMENT PRIMARY KEY,
                    username      VARCHAR(255) UNIQUE NOT NULL,
                    password_hash VARCHAR(255) NOT NULL
                ) ENGINE=InnoDB
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS flagged_duplicates (
                    id                 INT AUTO_INCREMENT PRIMARY KEY,
                    attempted_voter_id VARCHAR(64) NOT NULL,
                    attempted_name     VARCHAR(255) NOT NULL,
                    matched_voter_id   VARCHAR(64) NOT NULL,
                    distance           FLOAT NOT NULL,
                    photo_base64       MEDIUMTEXT NULL,
                    flagged_at         DATETIME NOT NULL
                ) ENGINE=InnoDB
                """
            )
            # Single-row table holding the current election phase.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS election_settings (
                    id    INT PRIMARY KEY DEFAULT 1,
                    phase VARCHAR(32) NOT NULL DEFAULT 'registration'
                ) ENGINE=InnoDB
                """
            )
            cur.execute("SELECT COUNT(*) AS cnt FROM election_settings WHERE id = 1")
            if cur.fetchone()["cnt"] == 0:
                cur.execute("INSERT INTO election_settings (id, phase) VALUES (1, 'registration')")
        conn.commit()
 
 
def now():
    return datetime.datetime.utcnow()
 
 
# ---------- Voters ----------
 
def create_voter(voter_id, name, email, constituency, encrypted_encoding, photo_base64=None):
    with contextlib.closing(get_conn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO voters (voter_id, name, email, constituency, face_encoding, photo_base64, registered_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (voter_id, name, email, constituency, encrypted_encoding, photo_base64, now()),
            )
        conn.commit()
 
 
def get_voter(voter_id):
    with contextlib.closing(get_conn()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM voters WHERE voter_id = %s", (voter_id,))
            row = cur.fetchone()
            return row
 
 
def mark_voted(voter_id):
    with contextlib.closing(get_conn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE voters SET has_voted = 1, voted_at = %s WHERE voter_id = %s",
                (now(), voter_id),
            )
        conn.commit()
 
 
def record_failed_attempt(voter_id, lockout_minutes=5, max_attempts=5):
    with contextlib.closing(get_conn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE voters SET failed_attempts = failed_attempts + 1 WHERE voter_id = %s",
                (voter_id,),
            )
            cur.execute("SELECT failed_attempts FROM voters WHERE voter_id = %s", (voter_id,))
            row = cur.fetchone()
            if row and row["failed_attempts"] >= max_attempts:
                lock_until = datetime.datetime.utcnow() + datetime.timedelta(minutes=lockout_minutes)
                cur.execute(
                    "UPDATE voters SET locked_until = %s WHERE voter_id = %s", (lock_until, voter_id)
                )
        conn.commit()
 
 
def reset_failed_attempts(voter_id):
    with contextlib.closing(get_conn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE voters SET failed_attempts = 0, locked_until = NULL WHERE voter_id = %s",
                (voter_id,),
            )
        conn.commit()
 
 
def is_locked(voter):
    if not voter.get("locked_until"):
        return False
    return datetime.datetime.utcnow() < voter["locked_until"]
 
 
def list_all_encodings():
    """
    Returns [(voter_id, encrypted_face_encoding), ...] for every registered
    voter. Used at registration time to check whether a new face already
    belongs to someone registered under a different voter_id.
 
    Note: this is an O(N) scan against every registered voter, run once per
    new registration -- fine at college scale (hundreds to low thousands of
    voters), not how you'd do this at national scale (that needs an indexed
    vector search, e.g. FAISS -- see README's Limitations section).
    """
    with contextlib.closing(get_conn()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT voter_id, face_encoding FROM voters")
            return [(r["voter_id"], r["face_encoding"]) for r in cur.fetchall()]
 
 
def list_voters():
    with contextlib.closing(get_conn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT voter_id, name, email, constituency, has_voted, registered_at, photo_base64, "
                "failed_attempts, locked_until FROM voters "
                "ORDER BY registered_at DESC"
            )
            return cur.fetchall()
 
 
# ---------- Candidates ----------
 
def add_candidate(name, party, position, constituency):
    with contextlib.closing(get_conn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO candidates (name, party, position, constituency) VALUES (%s, %s, %s, %s)",
                (name, party, position, constituency)
            )
        conn.commit()
 
 
def delete_candidate(candidate_id):
    """Returns (ok, error). Fails safely if the candidate already has votes."""
    with contextlib.closing(get_conn()) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute("DELETE FROM candidates WHERE id = %s", (candidate_id,))
                conn.commit()
                return True, None
            except pymysql.err.IntegrityError:
                conn.rollback()
                return False, "Can't remove this candidate -- they've already received votes."
 
 
def list_candidates(constituency=None):
    with contextlib.closing(get_conn()) as conn:
        with conn.cursor() as cur:
            if constituency:
                cur.execute(
                    "SELECT * FROM candidates WHERE constituency = %s ORDER BY name",
                    (constituency,)
                )
            else:
                cur.execute("SELECT * FROM candidates ORDER BY constituency, name")
            return cur.fetchall()
 
 
def list_constituencies():
    with contextlib.closing(get_conn()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT constituency FROM candidates ORDER BY constituency")
            return [r["constituency"] for r in cur.fetchall()]
 
 
# ---------- Ballots (anonymized) ----------
 
def cast_ballot(candidate_id):
    with contextlib.closing(get_conn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ballots (candidate_id, cast_at) VALUES (%s, %s)",
                (candidate_id, now()),
            )
        conn.commit()
 
 
def get_tally():
    with contextlib.closing(get_conn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.party, COUNT(b.id) AS votes
                FROM candidates c
                LEFT JOIN ballots b ON b.candidate_id = c.id
                GROUP BY c.party
                ORDER BY votes DESC
                """
            )
            return cur.fetchall()
 
 
# ---------- Audit log ----------
 
def log_event(voter_id, event_type, detail=None, ip_address=None):
    with contextlib.closing(get_conn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO audit_log (voter_id, event_type, detail, ip_address, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (voter_id, event_type, detail, ip_address, now()),
            )
        conn.commit()
 
 
def get_audit_log(limit=200):
    with contextlib.closing(get_conn()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT %s", (limit,))
            return cur.fetchall()
 
 
# ---------- Admins ----------
 
def create_admin(username, password_hash):
    with contextlib.closing(get_conn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO admins (username, password_hash) VALUES (%s, %s)",
                (username, password_hash),
            )
        conn.commit()
 
 
def get_admin(username):
    with contextlib.closing(get_conn()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM admins WHERE username = %s", (username,))
            return cur.fetchone()
 
 
# ---------- Flagged duplicate attempts (admin review) ----------
 
def create_flagged_duplicate(attempted_voter_id, attempted_name, matched_voter_id, distance, photo_base64):
    with contextlib.closing(get_conn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO flagged_duplicates "
                "(attempted_voter_id, attempted_name, matched_voter_id, distance, photo_base64, flagged_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (attempted_voter_id, attempted_name, matched_voter_id, distance, photo_base64, now()),
            )
        conn.commit()
 
 
def get_voter_stats():
    """Total registered voters, total votes cast, and turnout % -- safe to show
    publicly since it never reveals who voted for whom, just headcounts."""
    with contextlib.closing(get_conn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS total, SUM(has_voted) AS voted FROM voters"
            )
            row = cur.fetchone()
            total = row["total"] or 0
            voted = row["voted"] or 0
            turnout = round((voted / total) * 100, 1) if total else 0.0
            return {"total_registered": total, "total_voted": voted, "turnout_pct": turnout}
 
 
# ---------- Election phase ----------
 
VALID_PHASES = ("registration", "voting", "results")
 
 
def get_election_phase():
    with contextlib.closing(get_conn()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT phase FROM election_settings WHERE id = 1")
            row = cur.fetchone()
            return row["phase"] if row else "registration"
 
 
def set_election_phase(phase):
    if phase not in VALID_PHASES:
        raise ValueError(f"Invalid phase: {phase}")
    with contextlib.closing(get_conn()) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE election_settings SET phase = %s WHERE id = 1", (phase,))
        conn.commit()
 
 
def list_flagged_duplicates(limit=50):
    """
    Joins in the matched voter's own stored photo (if they have one and
    still exist) so the admin dashboard can show both photos side by side.
    matched_voter_id is intentionally NOT a foreign key -- deleting a voter
    (e.g. clearing the table to start fresh) should never be blocked by an
    old flagged-duplicate record referencing them.
    """
    with contextlib.closing(get_conn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    fd.id, fd.attempted_voter_id, fd.attempted_name, fd.matched_voter_id,
                    fd.distance, fd.photo_base64 AS attempted_photo, fd.flagged_at,
                    v.photo_base64 AS matched_photo
                FROM flagged_duplicates fd
                LEFT JOIN voters v ON v.voter_id = fd.matched_voter_id
                ORDER BY fd.id DESC
                LIMIT %s
                """,
                (limit,),
            )
            return cur.fetchall()