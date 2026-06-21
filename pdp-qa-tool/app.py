"""
Local raw-HTML fetch server, paired with the "Raw HTML Fetcher" Edge extension.

This replaces a paid HTML-extraction API (e.g. Apify's dataguru/html-extractor)
for sites that block direct HTTP requests. The extension fetches pages inside
your real, logged-in browser session (so it isn't blocked the way a bot/script
would be); this server just queues URLs and stores whatever HTML comes back.

It does NOT parse anything — that's left to your existing Streamlit app's
parsing logic. This server's job is purely: take a list of URLs in, hand raw
HTML out, in a shape your app can consume.

Run:
    pip install flask requests
    python html_fetch_server.py

Two ways your existing Streamlit app can get the HTML:
    1. Call GET /html/<job_id> or GET /html/by-url?url=... once status is "ok".
    2. Or just read straight from fetched_pages.db (SQLite) yourself —
       table `pages`, column `html` holds the full page source.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

from flask import Flask, request, jsonify

try:
    from flask_cors import CORS
    HAS_CORS = True
except ImportError:
    HAS_CORS = False

DB_PATH = Path(__file__).parent / "fetched_pages.db"

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


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            label TEXT,                      -- optional: your SKU or any tag you want carried through
            retailer TEXT,                   -- which retailer this URL belongs to (CVS, Walgreens, etc.)
            status TEXT DEFAULT 'queued',    -- queued, in_progress, done, failed
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER,
            url TEXT NOT NULL,
            label TEXT,
            retailer TEXT,
            html TEXT,
            status TEXT,           -- ok, error
            error_detail TEXT,
            html_length INTEGER,
            fetched_at TEXT,
            received_at TEXT
        )
    """)
    conn.commit()

    # Lightweight migration for existing databases created before the
    # `retailer` column existed, so upgrading doesn't require deleting
    # fetched_pages.db and losing prior history.
    for table in ("jobs", "pages"):
        cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
        if "retailer" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN retailer TEXT")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Enqueue / status (your Streamlit app -> here)
# ---------------------------------------------------------------------------

@app.route("/enqueue", methods=["POST"])
def enqueue():
    """Body: {"rows": [{"url": "...", "label": "optional-sku-or-tag", "retailer": "CVS"}, ...]}

    Dedupes against URLs that are already queued/in_progress, or already
    have a successful (status='ok') result in `pages` — so calling this
    repeatedly for the same URL (e.g. a Salsify URL shared by many rows,
    or concurrent threads racing each other) doesn't pile up duplicate
    jobs for the extension to redo.
    """
    rows = request.get_json(force=True).get("rows", [])
    if not rows:
        return jsonify({"error": "No rows provided"}), 400

    conn = sqlite3.connect(DB_PATH)
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    skipped = 0

    for row in rows:
        url = (row.get("url") or "").strip()
        if not url:
            continue

        already_pending = conn.execute(
            "SELECT 1 FROM jobs WHERE url=? AND status IN ('queued', 'in_progress') LIMIT 1",
            (url,),
        ).fetchone()
        already_succeeded = conn.execute(
            "SELECT 1 FROM pages WHERE url=? AND status='ok' LIMIT 1",
            (url,),
        ).fetchone()

        if already_pending or already_succeeded:
            skipped += 1
            continue

        conn.execute(
            "INSERT INTO jobs (url, label, retailer, status, created_at) VALUES (?, ?, ?, 'queued', ?)",
            (url, row.get("label", ""), row.get("retailer", ""), now),
        )
        inserted += 1

    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='queued'").fetchone()[0]
    conn.close()
    return jsonify({"status": "ok", "queued_total": count, "inserted": inserted, "skipped_duplicates": skipped}), 200


@app.route("/queue/set-active-retailer", methods=["POST"])
def set_active_retailer():
    """
    Body: {"retailer": "CVS"}

    Call this BEFORE enqueueing a new retailer's batch. It cancels any
    queued/in_progress jobs for OTHER retailers so the extension's
    auto-pilot only works on the currently selected retailer — this is
    what makes "select one retailer at a time" actually scoped end to end,
    not just in the Streamlit UI's filtering.

    Jobs already completed (done/failed) for other retailers are left
    alone — only pending work for other retailers is cancelled, so nothing
    already-fetched gets lost, and switching back to a previous retailer
    later won't need to refetch pages that already succeeded.
    """
    retailer = (request.get_json(force=True).get("retailer") or "").strip()
    if not retailer:
        return jsonify({"error": "Missing retailer"}), 400

    conn = sqlite3.connect(DB_PATH)
    cancelled = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE retailer != ? AND status IN ('queued', 'in_progress')",
        (retailer,),
    ).fetchone()[0]
    conn.execute(
        "DELETE FROM jobs WHERE retailer != ? AND status IN ('queued', 'in_progress')",
        (retailer,),
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "ok", "active_retailer": retailer, "cancelled_other_retailer_jobs": cancelled}), 200


@app.route("/refetch", methods=["POST"])
def refetch():
    """
    Force re-queue a URL even if a successful page already exists —
    use this for a manual retry (e.g. after fixing a selector or if a
    page was fetched while logged out). Body: {"url": "...", "retailer": "CVS"}
    """
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Missing url"}), 400

    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO jobs (url, label, retailer, status, created_at) VALUES (?, '', ?, 'queued', ?)",
        (url, data.get("retailer", ""), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"}), 200


@app.route("/queue/status", methods=["GET"])
def queue_status():
    """
    Optional ?retailer=CVS query param scopes counts to one retailer.
    Without it, returns overall counts plus a by_retailer breakdown so the
    dashboard can show "what's actually queued right now" at a glance.
    """
    retailer_filter = (request.args.get("retailer") or "").strip()

    conn = sqlite3.connect(DB_PATH)
    counts = {}
    for status in ("queued", "in_progress", "done", "failed"):
        if retailer_filter:
            counts[status] = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status=? AND retailer=?", (status, retailer_filter)
            ).fetchone()[0]
        else:
            counts[status] = conn.execute("SELECT COUNT(*) FROM jobs WHERE status=?", (status,)).fetchone()[0]

    by_retailer = {}
    rows = conn.execute(
        "SELECT COALESCE(retailer, ''), status, COUNT(*) FROM jobs GROUP BY retailer, status"
    ).fetchall()
    for retailer, status, count in rows:
        by_retailer.setdefault(retailer or "(unspecified)", {}).setdefault(status, 0)
        by_retailer[retailer or "(unspecified)"][status] = count

    conn.close()
    counts["by_retailer"] = by_retailer
    return jsonify(counts), 200


@app.route("/queue/clear", methods=["POST"])
def queue_clear():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM jobs WHERE status IN ('queued', 'in_progress')")
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"}), 200


# ---------------------------------------------------------------------------
# Worker endpoints (extension <-> here)
# ---------------------------------------------------------------------------

@app.route("/job/next", methods=["GET"])
def next_job():
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT id, url, label, retailer FROM jobs WHERE status='queued' ORDER BY id ASC LIMIT 1"
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({"job": None}), 200

    job_id = row[0]
    conn.execute("UPDATE jobs SET status='in_progress' WHERE id=?", (job_id,))
    conn.commit()
    conn.close()
    return jsonify({"job": {"id": job_id, "url": row[1], "label": row[2], "retailer": row[3]}}), 200


@app.route("/job/complete", methods=["POST"])
def complete_job():
    data = request.get_json(force=True)
    job_id = data.get("job_id")
    if not job_id:
        return jsonify({"error": "Missing job_id"}), 400

    conn = sqlite3.connect(DB_PATH)
    job = conn.execute("SELECT url, label, retailer FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        conn.close()
        return jsonify({"error": "Unknown job_id"}), 404

    url, label, retailer = job
    status = data.get("status", "error")
    html = data.get("html", "")

    conn.execute(
        """
        INSERT INTO pages (job_id, url, label, retailer, html, status, error_detail, html_length, fetched_at, received_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id, data.get("url", url), label, retailer, html, status,
            data.get("errorDetail", ""), len(html), data.get("fetchedAt", ""),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.execute("UPDATE jobs SET status=? WHERE id=?", ("done" if status == "ok" else "failed", job_id))
    conn.commit()
    conn.close()

    print(f"Job {job_id} [{status}] ({retailer or '?'}): {url} ({len(html):,} chars)")
    return jsonify({"status": "ok"}), 200


# ---------------------------------------------------------------------------
# Read endpoints (your Streamlit app <- here)
# ---------------------------------------------------------------------------

@app.route("/html/<int:job_id>", methods=["GET"])
def get_html_by_job(job_id):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT url, label, html, status, error_detail, fetched_at FROM pages WHERE job_id=? ORDER BY id DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "No page found for that job_id"}), 404
    return jsonify({
        "url": row[0], "label": row[1], "html": row[2],
        "status": row[3], "error_detail": row[4], "fetched_at": row[5],
    }), 200


@app.route("/html/by-url", methods=["GET"])
def get_html_by_url():
    url = unquote(request.args.get("url", ""))
    if not url:
        return jsonify({"error": "Missing url query param"}), 400
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT url, label, html, status, error_detail, fetched_at FROM pages WHERE url=? ORDER BY id DESC LIMIT 1",
        (url,),
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "No page found for that url"}), 404
    return jsonify({
        "url": row[0], "label": row[1], "html": row[2],
        "status": row[3], "error_detail": row[4], "fetched_at": row[5],
    }), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "running"}), 200


if __name__ == "__main__":
    init_db()
    print(f"Storing fetched pages in {DB_PATH}")
    print("Listening on http://localhost:8765 ... leave this running.")
    app.run(host="localhost", port=8765, debug=False)
