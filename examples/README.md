# examples sub-workspace

Example configs + scripts + deployment templates. Reference material
for other agents / operators.

## layout

```
examples/
├── config/
│   ├── space-shared.json      # default policy
│   ├── space-device-only.json # App-controlled Space
│   └── space-human-only.json  # dev/test
├── scripts/
│   ├── run-self-test.sh       # one-shot smoke test
│   └── bench-all.sh           # run the full benchmark suite
├── systemd/
│   └── rollout-shield.service # user-level systemd unit
├── docker/
│   └── Dockerfile             # multi-stage build
└── k8s/
    └── sidecar.yaml           # k8s sidecar deployment
```

## using

```bash
# apply the device-only config
cp examples/config/space-device-only.json ~/.rollout-shield/config.json

# run a one-shot smoke test
bash examples/scripts/run-self-test.sh

# run the full benchmark suite
bash examples/scripts/bench-all.sh
```
