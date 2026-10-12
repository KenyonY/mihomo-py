# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# Build the distribution inside the target platform. This makes the wheel
# contain the matching x86_64 or aarch64 mihomo executable.
FROM ${PYTHON_IMAGE} AS wheels
ARG TARGETARCH
ARG INSTALL_WEB=false
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}
WORKDIR /build
RUN set -eux; \
    case "$TARGETARCH" in \
        amd64|arm64) ;; \
        *) echo "unsupported Docker architecture: $TARGETARCH" >&2; exit 2 ;; \
    esac; \
    python -m pip install --no-cache-dir "build>=1.2,<2" "hatchling>=1.26,<2"; \
    python -m build --version
COPY pyproject.toml hatch_build.py README.md ./
COPY src/ ./src/
COPY docs/ ./docs/
COPY scripts/ ./scripts/
COPY tests/ ./tests/
COPY web/ ./web/
RUN --network=none set -eux; \
    case "$TARGETARCH" in \
        amd64) export MIHOMO_BUILD_ARCH=x86_64 ;; \
        arm64) export MIHOMO_BUILD_ARCH=aarch64 ;; \
        *) echo "unsupported Docker architecture: $TARGETARCH" >&2; exit 2 ;; \
    esac; \
    python -m build --no-isolation --wheel --outdir /wheels; \
    if [ "$INSTALL_WEB" = "true" ]; then \
        python -m build --no-isolation --wheel --outdir /wheels web; \
    fi
# Resolve all runtime dependencies into a local wheelhouse. The runtime stage
# never needs the source tree or a package index.
RUN set -eux; \
    if [ "$INSTALL_WEB" = "true" ]; then \
        for package_wheel in /wheels/mihomo_py-*.whl; do \
            python -m pip download --only-binary=:all: --find-links=/wheels \
                --dest /wheels "${package_wheel}[web]"; \
        done; \
    else \
        python -m pip download --only-binary=:all: --dest /wheels \
            /wheels/mihomo_py-*.whl; \
    fi

# Install only the locally built wheel and its downloaded dependencies. The
# image intentionally keeps root as the default user: Docker's NET_ADMIN
# capability must survive the Python interpreter exec, and /dev/net/tun may
# have restrictive device-node ownership on some hosts.
FROM ${PYTHON_IMAGE} AS installed
ARG INSTALL_WEB=false
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    MIHOMO_PY_HOME=/data
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates iproute2 tzdata \
    && ln -snf /usr/share/zoneinfo/${TZ} /etc/localtime \
    && dpkg-reconfigure -f noninteractive tzdata \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
RUN --network=none --mount=type=bind,from=wheels,source=/wheels,target=/wheels \
    set -eux; \
    python -m pip install --no-cache-dir --no-index --find-links=/wheels mihomo-py; \
    if [ "$INSTALL_WEB" = "true" ]; then \
        python -m pip install --no-cache-dir --no-index --find-links=/wheels 'mihomo-py[web]'; \
    fi; \
    python -m pip check
WORKDIR /data
VOLUME ["/data"]
EXPOSE 7897 9090 9091

# Keep the existing offline installation check available without making it the
# default image command. Build it with: docker buildx build --target verify ...
FROM installed AS verify
COPY scripts/check_install.py /opt/mihomo-py/check_install.py
WORKDIR /tmp
ENTRYPOINT []
CMD ["python", "/opt/mihomo-py/check_install.py"]

# Default image: use `docker run -it ...` for the TUI, or pass a mihomo-py
# subcommand such as `tun doctor` or `core status` after the image name.
FROM installed AS runtime
ENTRYPOINT ["mihomo-py"]
CMD ["tui"]
