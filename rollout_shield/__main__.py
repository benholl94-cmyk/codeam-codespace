"""Allow `python -m rollout_shield` to invoke the CLI."""

from .cli import main

raise SystemExit(main())
