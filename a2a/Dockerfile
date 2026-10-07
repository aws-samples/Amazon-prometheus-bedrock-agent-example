FROM ghcr.io/astral-sh/uv:python3.10-bookworm-slim
WORKDIR /app

# AWS_REGION is read from the environment at build time (falls back to
# us-east-1 only if not provided), and is always overridable at container
# launch time via --env AWS_REGION=... (see deploy.sh).
ARG AWS_REGION=us-east-1

# All environment variables in one layer
ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DOCKER_CONTAINER=1 \
    AWS_REGION=${AWS_REGION} \
    AWS_DEFAULT_REGION=${AWS_REGION}



COPY requirements.txt requirements.txt
# Install from requirements file
RUN uv pip install -r requirements.txt




RUN uv pip install aws-opentelemetry-distro>=0.10.1

# Create non-root user
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

EXPOSE 8080
EXPOSE 8000

# Copy entire project (respecting .dockerignore)
COPY . .

# Use the full module path

CMD ["opentelemetry-instrument", "python", "-m", "app.argocd_agent.main_a2a"]
