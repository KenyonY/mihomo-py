ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# Build each wheel on its target architecture so pip also selects matching dependencies.
FROM ${PYTHON_IMAGE} AS wheels
ARG TARGETARCH
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}
WORKDIR /build
RUN case "$TARGETARCH" in amd64|arm64) ;; *) exit 2 ;; esac \
    && python -m pip install --no-cache-dir "build>=1.2,<2" "hatchling>=1.26,<2"
COPY pyproject.toml hatch_build.py README.md ./
COPY src/ ./src/
COPY docs/ ./docs/
COPY scripts/ ./scripts/
COPY tests/ ./tests/
# Building the sdist and then its wheel must not download the core or geodata.
RUN --network=none python -m build --no-isolation --outdir /wheels
RUN python -m pip download --only-binary=:all: --dest /wheels /wheels/mihomo_py-*.whl

FROM ${PYTHON_IMAGE} AS verify
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1
# A clean image gets only the installed distribution, never the source tree or build tools.
RUN --network=none --mount=type=bind,from=wheels,source=/wheels,target=/wheels \
    python -m pip install --no-cache-dir --no-index --find-links=/wheels mihomo-py \
    && python -m pip check
COPY scripts/check_install.py /opt/mihomo-py/check_install.py
WORKDIR /tmp
CMD ["python", "/opt/mihomo-py/check_install.py"]
