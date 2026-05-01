import os
import subprocess
import logging
from flask import Flask, render_template, request, Response, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

# Keep your original health check silence logging
log = logging.getLogger('werkzeug')
class HealthCheckFilter(logging.Filter):
    def filter(self, record):
        return "/status" not in record.getMessage()
log.addFilter(HealthCheckFilter())

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'), 'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/status')
def status():
    return "OK", 200

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/search', methods=['POST'])
def search():
    # Accepting JSON now to make the frontend 'fetch' easier
    card_list = request.json.get('cards', [])

    def run_scripts():
        for card in card_list:
            card = card.strip()
            if not card: continue

            # Calling our new script
            process = subprocess.Popen(['bash', 'invScrape.sh', card],
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE,
                                     text=True)

            for line in process.stdout:
                if line.strip():
                    # Format as Server-Sent Event (SSE)
                    yield f"data: {line.strip()}\n\n"
            process.wait()

    response = Response(run_scripts(), mimetype='text/event-stream')
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Cache-Control'] = 'no-cache'
    return response

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
