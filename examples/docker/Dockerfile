# Multi-stage build for rollout-shield.
#
# Stage 1 — build: runs scripts/install.sh to assemble the ~/usr/ prefix.
# Stage 2 — runtime: copies the prefix into a slim image and runs the
#           monitor as PID 1.

FROM python:3.11-slim AS build
WORKDIR /src
COPY . /src
RUN bash scripts/install.sh --prefix /opt/rollout-shield-prefix

FROM python:3.11-slim
RUN useradd -m -s /bin/bash box
USER box
WORKDIR /home/box
COPY --from=build /opt/rollout-shield-prefix /home/box/usr
ENV PATH=/home/box/usr/bin:$PATH
ENV ROLLOUT_SHIELD_STATE=/home/box/.rollout-shield
RUN mkdir -p /home/box/.rollout-shield && \
    rollout-shield install --state-root /home/box/.rollout-shield
EXPOSE 8765
CMD ["rollout-shield-monitor", "--daemon", "--foreground", "--interval", "60"]
