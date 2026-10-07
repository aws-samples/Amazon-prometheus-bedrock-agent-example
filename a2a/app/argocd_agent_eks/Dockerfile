FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=9000

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY app/argocd_agent_eks/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app/__init__.py /app/app/__init__.py
COPY app/argocd_agent_eks/ /app/app/argocd_agent_eks/

RUN useradd -m -u 1000 agent
USER agent

EXPOSE 9000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:9000/.well-known/agent-card.json || exit 1

CMD ["python", "-m", "app.argocd_agent_eks.main"]
