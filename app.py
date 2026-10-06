import os
import base64
import hashlib
import json
import queue
import re
import signal
import sqlite3
import subprocess
import threading
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from flask import Flask, render_template, request, Response, send_from_directory, redirect, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
scrape_log = logging.getLogger('scrape')

# Keep your original health check silence logging
log = logging.getLogger('werkzeug')
class HealthCheckFilter(logging.Filter):
    def filter(self, record):
        return "/status" not in record.getMessage()
log.addFilter(HealthCheckFilter())

# invScrape.sh already fires 3 concurrent vendor requests per card, so this
# caps total concurrent vendor-API hits at MAX_CONCURRENT_CARDS * 3. Kept low
# by default since the target stores actively ban scraper IPs.
MAX_CONCURRENT_CARDS = int(os.environ.get("MAX_CONCURRENT_CARDS", 2))

# Cards that start together tend to finish together, so results would arrive
# in bursts of MAX_CONCURRENT_CARDS. Delaying each worker's first card by this
# many seconds offsets them; after that each worker starts its next card as
# soon as one finishes, so the offset carries through the search. Ideally
# about a typical card's search time divided by MAX_CONCURRENT_CARDS.
CARD_STAGGER_SECONDS = float(os.environ.get("CARD_STAGGER_SECONDS", 0.3))

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'), 'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/status')
def status():
    return "OK", 200

# Keeps well-behaved crawlers off the search endpoints and old ?q= links (which
# save a search on visit). ?s= share links stay allowed, since some link-preview
# bots (e.g. X's) obey robots.txt and would otherwise show no preview.
ROBOTS_TXT = """User-agent: *
Disallow: /search
Disallow: /share
Disallow: /?q=
"""

@app.route('/robots.txt')
def robots_txt():
    return Response(ROBOTS_TXT, mimetype='text/plain')

# Mirrors the quantity-prefix stripping in startSearch() (index.html).
QTY_PREFIX = re.compile(r"^\s*(?:\d+[xX]?\s*)?")

def preview_title(q):
    """Link-preview title for a shared search: first card plus remaining count."""
    cards = [c for c in (QTY_PREFIX.sub("", line).strip() for line in q.split("\n")) if c]
    if not cards:
        return "LGS Singles Search"
    if len(cards) == 1:
        return cards[0]
    return f"{cards[0]} + {len(cards) - 1} more"

# Shared searches are stored server-side so links stay short (?s=<id>) no
# matter how many cards are in the search. The DB lives in data/, which
# setup.sh mounts as a Docker volume so links survive redeploys.
SHARE_DB = os.path.join(app.root_path, 'data', 'shares.db')
SHARE_ID_LEN = 8
MAX_SHARE_BYTES = 50_000
MAX_SHARE_LINES = 500


def _share_db():
    return sqlite3.connect(SHARE_DB, timeout=10)


def init_share_db():
    os.makedirs(os.path.dirname(SHARE_DB), exist_ok=True)
    with closing(_share_db()) as db, db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS shares ("
            " id TEXT PRIMARY KEY,"
            " query TEXT NOT NULL,"
            " created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )


init_share_db()


def normalize_query(text):
    """Trims each line and drops blank ones, so cosmetic whitespace differences
    in the same card list map to the same share ID."""
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def save_share(text):
    """Stores a search and returns its ID: a prefix of the base64url SHA-256 of
    the normalized text, so the same search always gets the same link. Returns
    None if the search is empty or over the size limits."""
    query = normalize_query(text)
    if not query or len(query.encode()) > MAX_SHARE_BYTES or query.count("\n") >= MAX_SHARE_LINES:
        return None
    digest = base64.urlsafe_b64encode(hashlib.sha256(query.encode()).digest()).decode().rstrip("=")
    with closing(_share_db()) as db, db:
        # A different search already holding the 8-char prefix is vanishingly
        # unlikely, but lengthen the ID rather than overwrite it if it happens.
        for length in range(SHARE_ID_LEN, len(digest) + 1):
            share_id = digest[:length]
            row = db.execute("SELECT query FROM shares WHERE id = ?", (share_id,)).fetchone()
            if row is None:
                db.execute("INSERT INTO shares (id, query) VALUES (?, ?)", (share_id, query))
                return share_id
            if row[0] == query:
                return share_id
    return None


def load_share(share_id):
    with closing(_share_db()) as db:
        row = db.execute("SELECT query FROM shares WHERE id = ?", (share_id,)).fetchone()
    return row[0] if row else None


@app.route('/')
def index():
    q = request.args.get('q')
    if q is not None:
        # Old-style ?q= links: store the search and redirect to the short form.
        # If it can't be stored (e.g. over the size limit), serve it as is.
        share_id = save_share(q)
        if share_id:
            args = request.args.to_dict()
            del args['q']
            args['s'] = share_id
            return redirect(url_for('index', **args))
        query = q
        missing = False
    else:
        share_id = request.args.get('s')
        query = load_share(share_id) if share_id else None
        missing = bool(share_id) and query is None
    return render_template(
        'index.html',
        preview_title=preview_title(query or ''),
        shared_query=query,
        shared_missing=missing,
    )


@app.route('/share', methods=['POST'])
def share():
    text = (request.get_json(silent=True) or {}).get('text')
    if not isinstance(text, str):
        return {"error": "text must be a string"}, 400
    share_id = save_share(text)
    if share_id is None:
        return {"error": "search must be non-empty and under the size limit"}, 413
    return {"id": share_id}


# Matches the two genuine-failure lines invScrape.sh logs (curl failing, or
# a non-JSON/unexpected response body, e.g. a Cloudflare block page). The
# "catalog_items=0" stats line invScrape.sh also logs is a legitimate empty
# result, not an error, and intentionally doesn't match this.
_VENDOR_ERROR_RE = re.compile(r'^\[(?P<vendor>[\w.-]+)\] (?P<msg>curl failed on .*|unexpected .* response.*)$')

# invScrape.sh's routine per-vendor timing/count line. Logged at INFO; any
# other stderr output (failures, bash/jq errors) stays at WARNING.
_VENDOR_STATS_RE = re.compile(r"^\[[\w.-]+\] query='.*' catalog_items=\d+")


def _drain_stderr(card, stderr, q):
    # invScrape.sh's curl/jq calls fail silently on their own (no error
    # checking in the script), so any stderr output here is the only trace
    # of a vendor request going wrong (blocked, rate-limited, malformed
    # response, etc). Logged so it shows up in `docker logs` instead of just
    # vanishing as a missing result with no explanation. Genuine failures are
    # also pushed to the SSE stream so the UI can distinguish "vendor errored"
    # from "vendor had no matches".
    for line in stderr:
        line = line.strip()
        if not line:
            continue
        level = logging.INFO if _VENDOR_STATS_RE.match(line) else logging.WARNING
        scrape_log.log(level, f"[{card}] stderr: {line}")
        m = _VENDOR_ERROR_RE.match(line)
        if m:
            q.put({
                "type": "vendor_error",
                "query": card,
                "vendor": m.group("vendor").split(".")[0],
                "message": m.group("msg"),
            })


def run_one_card(card, q, active_procs, active_procs_lock, cancel_event, start_delay=0):
    # wait() rather than sleep() so a cancelled search doesn't sit out the delay.
    if cancel_event.wait(start_delay) or cancel_event.is_set():
        return
    q.put({"type": "card_start", "query": card})
    proc = subprocess.Popen(
        ['bash', 'invScrape.sh', card],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        preexec_fn=os.setsid,
    )
    key = object()
    with active_procs_lock:
        active_procs[key] = proc

    stderr_thread = threading.Thread(target=_drain_stderr, args=(card, proc.stderr, q), daemon=True)
    stderr_thread.start()

    count = 0
    try:
        for line in proc.stdout:
            if cancel_event.is_set():
                break
            line = line.strip()
            if line:
                q.put({"type": "result_line", "raw": line})
                count += 1
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    finally:
        with active_procs_lock:
            active_procs.pop(key, None)
        stderr_thread.join(timeout=2)
        q.put({"type": "card_done", "query": card, "count": count})


@app.route('/search', methods=['POST'])
def search():
    card_list = request.json.get('cards', [])

    def run_scripts():
        cleaned = [c.strip() for c in card_list if c.strip()]
        total = len(cleaned)
        if total == 0:
            return

        q = queue.Queue()
        active_procs = {}
        active_procs_lock = threading.Lock()
        cancel_event = threading.Event()
        executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_CARDS)

        for i, card in enumerate(cleaned):
            start_delay = i * CARD_STAGGER_SECONDS if i < MAX_CONCURRENT_CARDS else 0
            executor.submit(run_one_card, card, q, active_procs, active_procs_lock, cancel_event, start_delay)

        finished = 0
        try:
            while finished < total:
                try:
                    item = q.get(timeout=1.0)
                except queue.Empty:
                    # Heartbeat: also gives the generator a yield point so an
                    # aborted client is noticed within ~1s instead of only on
                    # the next real result.
                    yield ":\n\n"
                    continue

                if item["type"] == "result_line":
                    yield f"data: {item['raw']}\n\n"
                else:
                    if item["type"] == "card_done":
                        finished += 1
                    yield f"data: {json.dumps(item)}\n\n"

            yield f"data: {json.dumps({'type': 'all_done'})}\n\n"
        finally:
            # Runs on normal completion too (harmless: nothing left to kill),
            # and on client disconnect/abort (GeneratorExit at the yield above)
            # to stop wasting vendor-API budget on abandoned searches.
            cancel_event.set()
            executor.shutdown(wait=False)
            with active_procs_lock:
                procs = list(active_procs.values())
            for p in procs:
                try:
                    os.killpg(os.getpgid(p.pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
            for p in procs:
                try:
                    p.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    response = Response(run_scripts(), mimetype='text/event-stream')
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Cache-Control'] = 'no-cache'
    return response

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)
