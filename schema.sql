CREATE DATABASE IF NOT EXISTS securevote
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE securevote;
CREATE TABLE IF NOT EXISTS voters (
    voter_id        VARCHAR(64) PRIMARY KEY,
    name            VARCHAR(255) NOT NULL,
    email           VARCHAR(255),
    face_encoding   BLOB NOT NULL,
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

CREATE TABLE IF NOT EXISTS ballots (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    candidate_id INT NOT NULL,
    cast_at      DATETIME NOT NULL,
    FOREIGN KEY (candidate_id) REFERENCES candidates(id)
) ENGINE=InnoDB;

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
