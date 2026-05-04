import uuid
import time
import logging
import json
import os
import math
import threading
from flask import Flask, request, jsonify
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

app = Flask(__name__)

# --- Structured JSON logger ---
class JsonFormatter(logging.Formatter):
    def format(self, record):
        log = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "message": record.getMessage(),
            "service": os.getenv("SERVICE_NAME", "sample-app"),
            "version": os.getenv("SERVICE_VERSION", "1.0.0"),
        }
        for key in ("trace_id", "message_id", "status_code", "duration_ms", "path", "alert_severity", "stress_level"):
            if hasattr(record, key):
                log[key] = getattr(record, key)
        return json.dumps(log)

handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger = logging.getLogger("sample-app")
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logger.propagate = False

# --- In-process memory leak bucket (grows on each alert) ---
# Intentional leak to simulate memory pressure correlated with alert volume
_memory_leak = []
_leak_lock = threading.Lock()

# --- Prometheus metrics ---
REQUEST_COUNT = Counter(
    "app_requests_total",
    "Total HTTP requests",
    ["method", "path", "status_code"]
)
REQUEST_LATENCY = Histogram(
    "app_request_duration_seconds",
    "HTTP request latency",
    ["method", "path"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5]
)
ACTIVE_REQUESTS = Gauge(
    "app_active_requests",
    "Currently active requests"
)
MESSAGES_PROCESSED = Counter(
    "app_messages_processed_total",
    "Total messages processed",
    ["message_type"]
)
ALERT_COUNTER = Counter(
    "app_alerts_total",
    "Total alerts fired",
    ["severity"]
)
MEMORY_LEAK_BYTES = Gauge(
    "app_simulated_memory_leak_bytes",
    "Simulated in-process memory leak size in bytes"
)
CPU_STRESS_SECONDS = Counter(
    "app_cpu_stress_seconds_total",
    "Total seconds spent in CPU stress loops"
)
STRESS_LEVEL = Gauge(
    "app_stress_level",
    "Current stress level 0-10"
)

# --- Request hooks ---
@app.before_request
def before_request():
    request.start_time = time.time()
    request.trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4()))
    request.message_id = request.headers.get("X-Message-Id", str(uuid.uuid4()))
    ACTIVE_REQUESTS.inc()

@app.after_request
def after_request(response):
    duration = time.time() - request.start_time
    REQUEST_COUNT.labels(method=request.method, path=request.path, status_code=response.status_code).inc()
    REQUEST_LATENCY.labels(method=request.method, path=request.path).observe(duration)
    ACTIVE_REQUESTS.dec()
    logger.info(
        f"{request.method} {request.path} {response.status_code}",
        extra={
            "trace_id": request.trace_id,
            "message_id": request.message_id,
            "status_code": response.status_code,
            "duration_ms": round(duration * 1000, 2),
            "path": request.path,
        }
    )
    return response

# --- Routes ---
@app.route("/")
def index():
    return jsonify({"status": "ok", "trace_id": request.trace_id, "message_id": request.message_id})

@app.route("/process")
def process():
    msg_type = request.args.get("type", "default")
    time.sleep(0.01)
    MESSAGES_PROCESSED.labels(message_type=msg_type).inc()
    logger.info(f"Processed message type={msg_type}", extra={
        "trace_id": request.trace_id, "message_id": request.message_id, "path": request.path,
    })
    return jsonify({"processed": True, "message_type": msg_type,
                    "trace_id": request.trace_id, "message_id": request.message_id})

@app.route("/alert")
def alert():
    """
    Simulates an alert firing. Each alert:
    - Logs at WARNING/ERROR level with severity tag
    - Increments alert counter metric
    - Leaks memory proportional to severity (critical > high > warning)
    - Spins CPU briefly to simulate alert processing overhead
    The correlation: trace_id + message_id ties the alert log to the metric spike.
    """
    severity = request.args.get("severity", "warning")  # warning | high | critical
    trace_id = request.trace_id
    message_id = request.message_id

    # Memory leak: grow in-process buffer — critical leaks most
    leak_sizes = {"warning": 10_000, "high": 50_000, "critical": 200_000}
    leak_bytes = leak_sizes.get(severity, 10_000)
    with _leak_lock:
        _memory_leak.append(b"x" * leak_bytes)
        total_leak = sum(len(b) for b in _memory_leak)
    MEMORY_LEAK_BYTES.set(total_leak)

    # CPU stress: more iterations for higher severity
    cpu_iterations = {"warning": 50_000, "high": 200_000, "critical": 500_000}
    iterations = cpu_iterations.get(severity, 50_000)
    t0 = time.time()
    _burn_cpu(iterations)
    cpu_time = time.time() - t0
    CPU_STRESS_SECONDS.inc(cpu_time)

    # Stress level gauge: rough estimate based on leak size
    stress = min(10, total_leak // 500_000)
    STRESS_LEVEL.set(stress)

    ALERT_COUNTER.labels(severity=severity).inc()

    log_level = logging.WARNING if severity == "warning" else logging.ERROR
    logger.log(log_level, f"Alert fired severity={severity}", extra={
        "trace_id": trace_id,
        "message_id": message_id,
        "alert_severity": severity,
        "stress_level": stress,
        "path": request.path,
    })

    return jsonify({
        "alert": True,
        "severity": severity,
        "trace_id": trace_id,
        "message_id": message_id,
        "memory_leak_bytes": total_leak,
        "stress_level": stress,
    })

@app.route("/reset")
def reset():
    """Drain the memory leak and reset stress — useful between test runs."""
    with _leak_lock:
        _memory_leak.clear()
    MEMORY_LEAK_BYTES.set(0)
    STRESS_LEVEL.set(0)
    logger.info("Stress reset", extra={"trace_id": request.trace_id, "message_id": request.message_id, "path": request.path})
    return jsonify({"reset": True})

@app.route("/health")
def health():
    return jsonify({"status": "healthy"})

@app.route("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}

def _burn_cpu(iterations):
    """Waste CPU cycles — pure math loop."""
    x = 0.0
    for i in range(1, iterations):
        x += math.sqrt(i)
    return x

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
