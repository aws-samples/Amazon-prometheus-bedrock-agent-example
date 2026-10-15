# Base image pulled from Amazon ECR Public (mirror of the official Docker
# Library Python image) rather than ghcr.io / Docker Hub, per BuilderHub
# container guidelines. uv is installed via pip below (the ghcr.io/astral-sh/uv
# image is an external registry and not permitted).
FROM public.ecr.aws/docker/library/python:3.10-slim-bookworm
WORKDIR /app

# AWS_REGION is read from the environment at build time (falls back to
# us-east-1 only if not provided), and is always overridable at container
# launch time via --env AWS_REGION=... (see deploy-argocd-agentcore.sh).
ARG AWS_REGION=us-east-1

# All environment variables in one layer
ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DOCKER_CONTAINER=1 \
    AWS_REGION=${AWS_REGION} \
    AWS_DEFAULT_REGION=${AWS_REGION}

# Install uv (the fast Python package installer) via pip, since we no longer
# base this image on the external ghcr.io/astral-sh/uv image.
RUN pip install --no-cache-dir uv

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
