"""
Local raw-HTML fetch server, paired with the "Raw HTML Fetcher" Edge extension.

This is the localhost relay used by the legacy extension flow:
- Streamlit app -> enqueue URLs here.
- Edge extension -> claims jobs, loads pages in the real browser session,
  sends raw HTML back here.
- Streamlit app -> reads HTML back by URL.

Important:
- This file is for the LOCAL relay flow only.
- Do not deploy this as your Streamlit Cloud app entrypoint.
- Your hosted Streamlit app should stay a Streamlit app, not a Flask app.

Run locally:
    pip install flask requests flask-cors
    python html_fetch_server.py
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib.parse import unquote

from flask import Flask, jsonify, request

try:
    from flask_cors import CORS
    HAS_CORS = True
except ImportError:
    HAS_CORS = False

DB_PATH = Path(__file__).resolve().parent / "fetched_pages.db"
app = Flask(__name__)

if HAS_CORS:
    CORS(app)
else:
    @app.after_request
    def add_cors_headers(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return response


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(db_connect()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                label TEXT,
                retailer TEXT,
                status TEXT DEFAULT 'queued',
                created_at TEXT,
                started_at TEXT,
                finished_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER,
                url TEXT NOT NULL,
                label TEXT,
                retailer TEXT,
                html TEXT,
                status TEXT,
                error_detail TEXT,
                html_length INTEGER,
                fetched_at TEXT,
                received_at TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_url_status ON jobs(url, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pages_url ON pages(url)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pages_job_id ON pages(job_id)")
        conn.commit()


def existing_success_page(conn: sqlite3.Connection, url: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, url, retailer, fetched_at
        FROM pages
        WHERE url=? AND status='ok'
        ORDER BY id DESC
        LIMIT 1
        """,
        (url,),
    ).fetchone()


def existing_open_job(conn: sqlite3.Connection, url: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, url, retailer, status
        FROM jobs
        WHERE url=? AND status IN ('queued', 'in_progress')
        ORDER BY id DESC
        LIMIT 1
        """,
        (url,),
    ).fetchone()


def normalize_rows(rows: Iterable[Dict[str, Any]]) -> list[dict[str, str]]:
    normalized = []
    for row in rows or []:
        url = str((row or {}).get("url", "") or "").strip()
        if not url:
            continue
        normalized.append(
            {
                "url": url,
                "label": str((row or {}).get("label", "") or "").strip(),
                "retailer": str((row or {}).get("retailer", "") or "").strip(),
            }
        )
    return normalized


@app.route("/enqueue", methods=["POST"])
def enqueue():
    """Body: {"rows": [{"url": "...", "label": "optional", "retailer": "CVS"}, ...]}"""
    data = request.get_json(force=True) or {}
    rows = normalize_rows(data.get("rows", []))
    if not rows:
        return jsonify({"error": "Missing or empty rows"}), 400

    created = 0
    skipped_existing_job = 0
    skipped_existing_page = 0
    created_job_ids: list[int] = []

    with closing(db_connect()) as conn:
        for row in rows:
            url = row["url"]
            label = row["label"]
            retailer = row["retailer"]

            if existing_open_job(conn, url):
                skipped_existing_job += 1
                continue
            if existing_success_page(conn, url):
                skipped_existing_page += 1
                continue

            cur = conn.execute(
                """
                INSERT INTO jobs (url, label, retailer, status, created_at)
                VALUES (?, ?, ?, 'queued', ?)
                """,
                (url, label, retailer, utc_now_iso()),
            )
            created += 1
            created_job_ids.append(int(cur.lastrowid))

        conn.commit()

    return jsonify(
        {
            "status": "ok",
            "created": created,
            "skipped_existing_job": skipped_existing_job,
            "skipped_existing_page": skipped_existing_page,
            "job_ids": created_job_ids,
        }
    ), 200


@app.route("/queue/set-active-retailer", methods=["POST"])
def set_active_retailer():
    """Body: {"retailer": "CVS"}. Cancels queued/in-progress jobs from other retailers."""
    data = request.get_json(force=True) or {}
    retailer = str(data.get("retailer", "") or "").strip()
    if not retailer:
        return jsonify({"error": "Missing retailer"}), 400

    with closing(db_connect()) as conn:
        cur = conn.execute(
            """
            DELETE FROM jobs
            WHERE status IN ('queued', 'in_progress')
              AND COALESCE(retailer, '') != ?
            """,
            (retailer,),
        )
        conn.commit()
        deleted = cur.rowcount if cur.rowcount is not None else 0

    return jsonify({"status": "ok", "active_retailer": retailer, "cancelled": deleted}), 200


@app.route("/refetch", methods=["POST"])
def refetch():
    """Body: {"url": "...", "retailer": "CVS", "label": "optional"}"""
    data = request.get_json(force=True) or {}
    url = str(data.get("url", "") or "").strip()
    retailer = str(data.get("retailer", "") or "").strip()
    label = str(data.get("label", "") or "").strip()
    if not url:
        return jsonify({"error": "Missing url"}), 400

    with closing(db_connect()) as conn:
        cur = conn.execute(
            """
            INSERT INTO jobs (url, label, retailer, status, created_at)
            VALUES (?, ?, ?, 'queued', ?)
            """,
            (url, label, retailer, utc_now_iso()),
        )
        conn.commit()
        job_id = int(cur.lastrowid)

    return jsonify({"status": "ok", "job_id": job_id}), 200


@app.route("/queue/status", methods=["GET"])
def queue_status():
    retailer_filter = str(request.args.get("retailer", "") or "").strip()

    base_sql = "SELECT status, COUNT(*) AS cnt FROM jobs"
    params: tuple[Any, ...] = ()
    if retailer_filter:
        base_sql += " WHERE COALESCE(retailer, '') = ?"
        params = (retailer_filter,)
    base_sql += " GROUP BY status"

    with closing(db_connect()) as conn:
        rows = conn.execute(base_sql, params).fetchall()
        counts = {"queued": 0, "in_progress": 0, "done": 0, "failed": 0}
        for row in rows:
            counts[str(row["status"])] = int(row["cnt"])

        by_retailer_rows = conn.execute(
            """
            SELECT COALESCE(retailer, '') AS retailer, status, COUNT(*) AS cnt
            FROM jobs
            GROUP BY COALESCE(retailer, ''), status
            ORDER BY COALESCE(retailer, ''), status
            """
        ).fetchall()

    by_retailer: dict[str, dict[str, int]] = {}
    for row in by_retailer_rows:
        retailer = str(row["retailer"])
        status = str(row["status"])
        cnt = int(row["cnt"])
        if retailer not in by_retailer:
            by_retailer[retailer] = {"queued": 0, "in_progress": 0, "done": 0, "failed": 0}
        by_retailer[retailer][status] = cnt

    return jsonify({"status": "ok", **counts, "by_retailer": by_retailer}), 200


@app.route("/queue/clear", methods=["POST"])
def queue_clear():
    with closing(db_connect()) as conn:
        cur = conn.execute("DELETE FROM jobs WHERE status IN ('queued', 'in_progress')")
        conn.commit()
        deleted = cur.rowcount if cur.rowcount is not None else 0
    return jsonify({"status": "ok", "cleared": deleted}), 200


@app.route("/job/next", methods=["GET"])
def next_job():
    with closing(db_connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT id, url, label, retailer
            FROM jobs
            WHERE status='queued'
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            conn.commit()
            return jsonify({"job": None}), 200

        conn.execute(
            "UPDATE jobs SET status='in_progress', started_at=? WHERE id=?",
            (utc_now_iso(), int(row["id"])),
        )
        conn.commit()

    return jsonify(
        {
            "job": {
                "id": int(row["id"]),
                "url": str(row["url"]),
                "label": str(row["label"] or ""),
                "retailer": str(row["retailer"] or ""),
            }
        }
    ), 200


@app.route("/job/complete", methods=["POST"])
def complete_job():
    data = request.get_json(force=True) or {}
    job_id = data.get("job_id")
    if not job_id:
        return jsonify({"error": "Missing job_id"}), 400

    html_text = str(data.get("html", "") or "")
    status = str(data.get("status", "ok") or "ok").strip().lower()
    if status not in {"ok", "error"}:
        status = "ok" if html_text else "error"
    error_detail = str(data.get("error_detail", "") or "").strip()
    fetched_at = str(data.get("fetched_at", "") or "").strip() or utc_now_iso()

    with closing(db_connect()) as conn:
        job = conn.execute(
            "SELECT id, url, label, retailer FROM jobs WHERE id=? LIMIT 1",
            (int(job_id),),
        ).fetchone()
        if not job:
            return jsonify({"error": "Unknown job_id"}), 404

        conn.execute(
            """
            INSERT INTO pages (job_id, url, label, retailer, html, status, error_detail, html_length, fetched_at, received_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(job["id"]),
                str(job["url"]),
                str(job["label"] or ""),
                str(job["retailer"] or ""),
                html_text,
                status,
                error_detail,
                len(html_text),
                fetched_at,
                utc_now_iso(),
            ),
        )
        conn.execute(
            "UPDATE jobs SET status=?, finished_at=? WHERE id=?",
            ("done" if status == "ok" else "failed", utc_now_iso(), int(job["id"])),
        )
        conn.commit()

    return jsonify({"status": "ok"}), 200


@app.route("/html/<int:job_id>", methods=["GET"])
def get_html_by_job(job_id: int):
    with closing(db_connect()) as conn:
        row = conn.execute(
            """
            SELECT url, label, retailer, html, status, error_detail, fetched_at
            FROM pages
            WHERE job_id=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()

    if not row:
        return jsonify({"error": "No page found for that job_id"}), 404

    return jsonify(
        {
            "url": str(row["url"]),
            "label": str(row["label"] or ""),
            "retailer": str(row["retailer"] or ""),
            "html": str(row["html"] or ""),
            "status": str(row["status"] or ""),
            "error_detail": str(row["error_detail"] or ""),
            "fetched_at": str(row["fetched_at"] or ""),
        }
    ), 200


@app.route("/html/by-url", methods=["GET"])
def get_html_by_url():
    url = unquote(str(request.args.get("url", "") or "")).strip()
    if not url:
        return jsonify({"error": "Missing url query param"}), 400

    with closing(db_connect()) as conn:
        row = conn.execute(
            """
            SELECT url, label, retailer, html, status, error_detail, fetched_at
            FROM pages
            WHERE url=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (url,),
        ).fetchone()

    if not row:
        return jsonify({"error": "No page found for that url"}), 404

    return jsonify(
        {
            "url": str(row["url"]),
            "label": str(row["label"] or ""),
            "retailer": str(row["retailer"] or ""),
            "html": str(row["html"] or ""),
            "status": str(row["status"] or ""),
            "error_detail": str(row["error_detail"] or ""),
            "fetched_at": str(row["fetched_at"] or ""),
        }
    ), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "running", "db_path": str(DB_PATH)}), 200


if __name__ == "__main__":
    init_db()
    print(f"Storing fetched pages in: {DB_PATH}")
    print("Listening on http://localhost:8765 ... leave this running.")
    app.run(host="localhost", port=8765, debug=False)
