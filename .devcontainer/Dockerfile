# Python version: 3, 3.10, 3.11, 3.12, ... 
# https://hub.docker.com/_/microsoft-devcontainers-python
ARG VARIANT="3.12"
FROM mcr.microsoft.com/devcontainers/python:${VARIANT}

# Setup devcontainer user
ARG USERNAME=vscode
ARG USER_UID=1000
ARG USER_GID=$USER_UID

# Update pip
RUN pip install --upgrade pip

# Install pkg dev dependencies
COPY requirements-dev.txt /tmp/requirements-dev.txt
RUN pip install -r /tmp/requirements-dev.txt && \
    rm /tmp/requirements-dev.txt