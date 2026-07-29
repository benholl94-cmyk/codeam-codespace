"""rollout-shield AI assistance package.

The "intelligence layer" — composed of:

- models.py         — registry of model implementations (mock + real)
- router.py         — parallel-lateral router (N models in parallel; outputs
                       combined laterally)
- benchmarks.py     — deterministic graders for offline scoring
- leaderboard.py    — persistent benchmark leaderboard
- self_cycle.py     — self-improvement cycle engine
- generator.py      — First-of-kind content generator

All modules are stdlib-only. No external API calls in the default
configuration; real-model drop-ins are future work.
"""

__version__ = "0.1.0"
