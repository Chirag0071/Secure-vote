"""
database.py
MySQL data access layer for SecureVote, via PyMySQL.

Connection settings come from environment variables (see README), with
local defaults for development:
  SECUREVOTE_DB_HOST      default: localhost
  SECUREVOTE_DB_PORT      default: 3306
  SECUREVOTE_DB_USER      default: root
  SECUREVOTE_DB_PASSWORD  default: "" (empty)
  SECUREVOTE_DB_NAME      default: securevote

Design note on vote secrecy (unchanged from the SQLite version):
- `voters` holds identity + encrypted face encoding + has_voted flag.
- `ballots` holds ONLY candidate_id + timestamp -- there is no voter_id
  column on this table at all, so there's no join path from a cast ballot
  back to a voter, even for someone with full DB access.
- `audit_log` records *authentication* events, never which candidate was
  chosen.

See schema.sql for the same DDL as a standalone file, useful if you'd
rather provision the database by hand (e.g. via the mysql CLI or a GUI)
instead of letting init_db() create it.
"""

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
        **_ssl_kwargs(),
    )


def init_db():
    # Step 1: make sure the database itself exists (connect with no db selected).
    conn = pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, charset="utf8mb4", **_ssl_kwargs()
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
                    face_encoding   BLOB NOT NULL,
                    has_voted       TINYINT(1) NOT NULL DEFAULT 0,
                    registered_at   DATETIME NOT NULL,
                    failed_attempts INT NOT NULL DEFAULT 0,
                    locked_until    DATETIME NULL
                ) ENGINE=InnoDB
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS candidates (
                    id       INT AUTO_INCREMENT PRIMARY KEY,
                    name     VARCHAR(255) NOT NULL,
                    position VARCHAR(255) NOT NULL
                ) ENGINE=InnoDB
                """
            )
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
        conn.commit()


def now():
    return datetime.datetime.utcnow()


# ---------- Voters ----------

def create_voter(voter_id, name, email, encrypted_encoding):
    with contextlib.closing(get_conn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO voters (voter_id, name, email, face_encoding, registered_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (voter_id, name, email, encrypted_encoding, now()),
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
            cur.execute("UPDATE voters SET has_voted = 1 WHERE voter_id = %s", (voter_id,))
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
                "SELECT voter_id, name, email, has_voted, registered_at FROM voters "
                "ORDER BY registered_at DESC"
            )
            return cur.fetchall()


# ---------- Candidates ----------

def add_candidate(name, position):
    with contextlib.closing(get_conn()) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO candidates (name, position) VALUES (%s, %s)", (name, position))
        conn.commit()


def list_candidates():
    with contextlib.closing(get_conn()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM candidates ORDER BY position, name")
            return cur.fetchall()


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
                SELECT c.id, c.name, c.position, COUNT(b.id) AS votes
                FROM candidates c
                LEFT JOIN ballots b ON b.candidate_id = c.id
                GROUP BY c.id, c.name, c.position
                ORDER BY c.position, votes DESC
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