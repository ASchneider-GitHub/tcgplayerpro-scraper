# Production server settings, loaded automatically by `gunicorn app:app`.
import logging

bind = "0.0.0.0:5000"

# /search streams results for up to a few minutes and holds a thread the whole
# time, so use threaded workers. With gthread the worker's main loop keeps
# checking in while requests run on other threads, so a long search doesn't
# trip the worker timeout.
worker_class = "gthread"
workers = 2
threads = 16

accesslog = "-"
access_log_format = '%(t)s %(h)s "%(r)s" %(s)s %(b)s'


class HealthCheckFilter(logging.Filter):
    def filter(self, record):
        return "/status" not in record.getMessage()


def post_fork(server, worker):
    # Keep health checks out of the access log, as app.py does for the
    # Flask development server.
    logging.getLogger("gunicorn.access").addFilter(HealthCheckFilter())
