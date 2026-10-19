import os

bind = "0.0.0.0:8000"
workers = int(os.getenv("WEB_WORKERS", "2"))
threads = int(os.getenv("WEB_THREADS", "2"))
timeout = 120
graceful_timeout = 30
accesslog = "-"
errorlog = "-"
access_log_format = '%(h)s %(m)s %(U)s %(s)s %(L)s'
max_requests = 2000
max_requests_jitter = 200
