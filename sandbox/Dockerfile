# Chromium + OS deps + Node LTS from the official Playwright image, plus the
# Angular CLI preinstalled so `ng build`/`ng test` and headless-Chrome audits
# run without a network round-trip inside each ephemeral container. The
# fixture itself (and its node_modules, as a named volume - see
# adapters/sandbox/docker_backend.py) is bind-mounted at runtime, not baked in.
FROM mcr.microsoft.com/playwright:v1.55.0-noble

# python3 is required by deepagents' BaseSandbox: read/glob/grep/edit all
# shell out to a server-side `python3 -c "..."` script (see
# deepagents/backends/sandbox.py) - not guaranteed present on every base image.
RUN apt-get update && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @angular/cli@22

WORKDIR /workspace

CMD ["sleep", "infinity"]
