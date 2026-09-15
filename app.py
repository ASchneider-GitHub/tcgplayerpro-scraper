import os
import json
import queue
import re
import signal
import subprocess
import threading
import logging
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template, request, Response, send_from_directory
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
# by default since the target stores actively ban scraper IPs (see README).
MAX_CONCURRENT_CARDS = int(os.environ.get("MAX_CONCURRENT_CARDS", 2))

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'), 'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/status')
def status():
    return "OK", 200

@app.route('/')
def index():
    return render_template('index.html')


# Matches the two genuine-failure lines invScrape.sh logs (curl failing, or
# a non-JSON/unexpected response body, e.g. a Cloudflare block page). The
# "catalog_items=0" stats line invScrape.sh also logs is a legitimate empty
# result, not an error, and intentionally doesn't match this.
_VENDOR_ERROR_RE = re.compile(r'^\[(?P<vendor>[\w.-]+)\] (?P<msg>curl failed on .*|unexpected .* response.*)$')


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
        scrape_log.warning(f"[{card}] stderr: {line}")
        m = _VENDOR_ERROR_RE.match(line)
        if m:
            q.put({
                "type": "vendor_error",
                "query": card,
                "vendor": m.group("vendor").split(".")[0],
                "message": m.group("msg"),
            })


def run_one_card(card, q, active_procs, active_procs_lock, cancel_event):
    if cancel_event.is_set():
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

        for card in cleaned:
            executor.submit(run_one_card, card, q, active_procs, active_procs_lock, cancel_event)

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
