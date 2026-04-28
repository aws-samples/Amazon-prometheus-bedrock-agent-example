# Sample Instrumented App — EKS + Datadog + Elasticsearch

## Structure

```
k8s-sample-app/
├── app/
│   ├── main.py           # Flask app with structured JSON logs + Prometheus metrics
│   ├── requirements.txt
│   └── Dockerfile
└── helm/
    ├── Chart.yaml
    ├── values.yaml
    └── templates/
        ├── deployment.yaml     # Pod annotations for DD log + metrics autodiscovery
        ├── service.yaml
        ├── load-generator.yaml # Simulated traffic deployment
        └── hpa.yaml
```

## How it works

### Correlation IDs
- Every request generates (or propagates) a `trace_id` and `message_id`
- Both are emitted in structured JSON logs AND as Prometheus metric labels
- Clients can pass `X-Trace-Id` / `X-Message-Id` headers to propagate their own IDs

### Logs (→ Elasticsearch)
- All logs are emitted to stdout as JSON — one object per line
- Fields: `timestamp`, `level`, `message`, `service`, `version`, `trace_id`, `message_id`, `status_code`, `duration_ms`, `path`
- Your log shipper (Fluentd/Fluent Bit/Filebeat) picks these up from the node and forwards to Elasticsearch

### Metrics (→ Datadog)
- `/metrics` exposes Prometheus-format metrics
- Datadog agent autodiscovery annotations on the pod tell the DD agent to scrape `/metrics` automatically — no extra config needed
- Metrics collected:
  - `sample_app.app_requests_total` (labels: method, path, status_code)
  - `sample_app.app_request_duration_seconds` (histogram)
  - `sample_app.app_active_requests` (gauge)
  - `sample_app.app_messages_processed_total` (labels: message_type, trace_id)

### Datadog Unified Service Tagging
- Pod labels `tags.datadoghq.com/service` and `tags.datadoghq.com/version` enable unified service tagging in Datadog

## Deploy

### 1. Build & push image
```bash
docker build -t your-ecr-repo/sample-app:1.0.0 ./app
docker push your-ecr-repo/sample-app:1.0.0
```

### 2. Install via Helm
```bash
helm install sample-app ./helm \
  --set image.repository=your-ecr-repo/sample-app \
  --set image.tag=1.0.0
```

### 3. Verify metrics scraping
```bash
kubectl port-forward svc/sample-app 8080:80
curl http://localhost:8080/metrics
```

## Prerequisites
- Datadog agent deployed with `datadog.logs.enabled=true` and `datadog.logs.containerCollectAll=true` (or per-pod annotation collection)
- Datadog agent with `datadog.prometheusScrape.enabled=true` OR the openmetrics check enabled (both work with the pod annotations above)
