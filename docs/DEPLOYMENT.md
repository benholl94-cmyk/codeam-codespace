# Deployment

Three deployment topologies — all share the same runtime, differing
in how the CLI is shipped and how the daemon is supervised.

## 1. systemd --user (recommended for single-machine)

Best for: a single host, the operator wants the daemon to live across
shell sessions.

```bash
# install
scripts/install.sh

# enable + start the daemon as a user-level systemd service
mkdir -p ~/.config/systemd/user
cp ~/usr/etc/rollout-shield/rollout-shield.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now rollout-shield

# check
systemctl --user status rollout-shield
journalctl --user -u rollout-shield -f
```

The unit file shipped with the install uses `%h/usr` which expands to
the user's home — so it follows the user when `$HOME` is shared via
ephemeral mounts.

## 2. Docker (recommended for ephemeral / sandboxed runners)

See `examples/docker/Dockerfile` for a multi-stage build that:

1. Builds the CLI in a build stage
2. Copies the assembled `~/usr/` prefix into a slim runtime image
3. Runs the monitor as PID 1

```bash
docker build -f examples/docker/Dockerfile -t rollout-shield:latest .
docker run -d --name rollout-shield \
  -v rollout-shield-state:/home/box/.rollout-shield \
  rollout-shield:latest
```

## 3. Kubernetes (recommended for fleet orchestration)

The `App-controlled Space` (controller_policy=device-only) scenario
maps to a sidecar deployment:

- main container: the user's workload
- sidecar: `rollout-shield monitor --daemon` with the device's
  hardware-anchored key mounted from a `Secret` (or a CSI volume for
  TPM/HSM-backed keys)

A minimal manifest lives in `examples/k8s/`.

## controller policy per deployment

| deployment | recommended policy |
|---|---|
| local dev | `shared` |
| CI smoke tests | `shared` |
| production App-controlled Space | `device-only` |
| dev sandbox with no device | `human-only` |

Switch with:

```bash
rollout-shield space set-policy device-only --yes
rollout-shield space validate
```

## upgrade

The install script is idempotent. Re-running it overwrites the prefix
from the repo source. State at `~/.rollout-shield/` is preserved.

```bash
git pull
scripts/install.sh
scripts/verify-install.sh
```

## rollback

The `INSTALL_REPO_SOURCE` marker at `~/usr/REPO_SOURCE` records the
repo commit the install was built from. To roll back:

```bash
git checkout <previous-commit>
scripts/install.sh
```

## state backup

`~/.rollout-shield/` is the only state. Back up:

```bash
tar czf rollout-shield-$(date +%Y%m%d).tar.gz \
  -C "$HOME" .rollout-shield
```

Restore to a fresh host:

```bash
tar xzf rollout-shield-*.tar.gz -C "$HOME"
scripts/install.sh   # install the CLI
```

The `scripts/export-state.sh` script produces a portable JSON bundle
that can be re-imported elsewhere.
