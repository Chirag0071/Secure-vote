import os
from dotenv import load_dotenv
load_dotenv()

import pymysql

HOST = os.environ.get("SECUREVOTE_DB_HOST", "localhost")
PORT = int(os.environ.get("SECUREVOTE_DB_PORT", "3306"))
USER = os.environ.get("SECUREVOTE_DB_USER", "root")
PASSWORD = os.environ.get("SECUREVOTE_DB_PASSWORD", "Vikram@711")
DB_NAME = os.environ.get("SECUREVOTE_DB_NAME", "securevote")

print(f"Connecting to MySQL at {HOST}:{PORT} as '{USER}'...")

try:
    # Step 1: connect with no database selected, just to prove the server/login work
    conn = pymysql.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, charset="utf8mb4")
    with conn.cursor() as cur:
        cur.execute("SELECT VERSION()")
        version = cur.fetchone()[0]
    print(f"Connected. MySQL server version: {version}")

    # Step 2: create the project database if it doesn't exist yet (same thing init_db() does)
    with conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}`")
    conn.commit()
    print(f"Database '{DB_NAME}' exists (created it if it wasn't there already).")

    # Step 3: connect again, this time INTO that database, and list tables
    conn.close()
    conn = pymysql.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, database=DB_NAME, charset="utf8mb4")
    with conn.cursor() as cur:
        cur.execute("SHOW TABLES")
        tables = [row[0] for row in cur.fetchall()]
    print(f"Tables in '{DB_NAME}': {tables if tables else '(none yet -- run the main app once to create them)'}")

    conn.close()
    print("\nConnection test passed. You're good to run: uvicorn app:app --reload")

except pymysql.err.OperationalError as e:
    print(f"\nConnection FAILED: {e}")
    print("Checklist:")
    print("  - Is MySQL Server actually running? (check Services on Windows, or `brew services list` on Mac)")
    print("  - Does SECUREVOTE_DB_PASSWORD in .env match your real MySQL root password?")
    print("  - Is SECUREVOTE_DB_HOST/PORT correct? (defaults: localhost / 3306)")