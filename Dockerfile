FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates ffmpeg libmagic1 tzdata postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Static docker CLI for the `sandbox` tool; the daemon stays on the host (mounted socket).
ARG DOCKER_CLI_VERSION=27.5.1
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && curl --proto '=https' --proto-redir '=https' -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" \
       | tar -xz --strip-components=1 -C /usr/local/bin docker/docker \
    && chmod +x /usr/local/bin/docker \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# uv installs the locked dependency set (pinned versions and hashes), not a free pip resolve.
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /uvx /bin/

WORKDIR /app

# The capability spine (Mem0 + embedder) ships in the core dependencies; only transports are
# extras. Gateways and plugins are installed and enabled later, never baked in (ADR-0027/0028):
#   docker build --build-arg EXTRAS="--extra <transport>" .
ARG EXTRAS=""

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-build --no-install-project $EXTRAS

COPY monoagens ./monoagens
# The Alembic chain is data the image must carry: `monoagens db upgrade` reads it from here.
# `engine.db` configures Alembic programmatically; the ini only names the chain for tooling.
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv sync --locked --no-build $EXTRAS

# HF_HOME stays outside /app/.monoagens: compose bind-mounts the host data dir over that
# path, which would otherwise shadow the model weights baked into the image (470MB).
ENV PATH="/app/.venv/bin:$PATH" \
    HF_HOME=/home/monoagens/.cache/huggingface

RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin monoagens \
    && mkdir -p /app/.monoagens \
    && chown -R monoagens:monoagens /app

# Must come AFTER useradd: the docker group follows the socket gid on the host.
ARG DOCKER_GID=994
RUN groupadd -g "${DOCKER_GID}" docker || true \
    && usermod -aG docker monoagens

USER monoagens
# The Mem0 embedder is downloaded once at build time into the monoagens user cache.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"

STOPSIGNAL SIGINT
# `monoagens run` creates the schema, seeds the documents, then acquires the primary lease.
CMD ["python", "-m", "monoagens", "run"]
