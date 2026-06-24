-- schema.sql
-- Standalone schema for SecureVote. database.py's init_db() creates this
-- automatically on first run, but you can also run this by hand:
--   mysql -u root -p < schema.sql

CREATE DATABASE IF NOT EXISTS securevote
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE securevote;

-- Voter identity + encrypted face encoding. face_encoding is NEVER a raw
-- photo -- it's a 512-d ArcFace embedding, encrypted with Fernet before
-- storage. photo_base64 is NULL unless SECUREVOTE_STORE_PHOTOS=true is set
-- (opt-in only -- see README's security design notes for the tradeoff).
CREATE TABLE IF NOT EXISTS voters (
    voter_id        VARCHAR(64) PRIMARY KEY,
    name            VARCHAR(255) NOT NULL,
    email           VARCHAR(255),
    face_encoding   BLOB NOT NULL,
    photo_base64    MEDIUMTEXT NULL,
    has_voted       TINYINT(1) NOT NULL DEFAULT 0,
    registered_at   DATETIME NOT NULL,
    failed_attempts INT NOT NULL DEFAULT 0,
    locked_until    DATETIME NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS candidates (
    id       INT AUTO_INCREMENT PRIMARY KEY,
    name     VARCHAR(255) NOT NULL,
    position VARCHAR(255) NOT NULL
) ENGINE=InnoDB;

-- Deliberately has NO voter_id column. This is the vote-secrecy boundary:
-- there is no join path from a cast ballot back to the voter who cast it.
CREATE TABLE IF NOT EXISTS ballots (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    candidate_id INT NOT NULL,
    cast_at      DATETIME NOT NULL,
    FOREIGN KEY (candidate_id) REFERENCES candidates(id)
) ENGINE=InnoDB;

-- Records authentication EVENTS, never which candidate was chosen.
CREATE TABLE IF NOT EXISTS audit_log (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    voter_id   VARCHAR(64),
    event_type VARCHAR(64) NOT NULL,
    detail     TEXT,
    ip_address VARCHAR(64),
    created_at DATETIME NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS admins (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    username      VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL
) ENGINE=InnoDB;

-- Faces rejected at registration for matching an existing voter. Only
-- populated when SECUREVOTE_STORE_PHOTOS=true. matched_voter_id is
-- intentionally NOT a foreign key -- clearing the voters table to start
-- fresh should never be blocked by an old flagged-duplicate record.
CREATE TABLE IF NOT EXISTS flagged_duplicates (
    id                 INT AUTO_INCREMENT PRIMARY KEY,
    attempted_voter_id VARCHAR(64) NOT NULL,
    attempted_name     VARCHAR(255) NOT NULL,
    matched_voter_id   VARCHAR(64) NOT NULL,
    distance           FLOAT NOT NULL,
    photo_base64       MEDIUMTEXT NULL,
    flagged_at         DATETIME NOT NULL
) ENGINE=InnoDB;