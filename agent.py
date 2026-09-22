"""Convenience entrypoint so `python agent.py console|dev` works from the repo root.

Equivalent to `python -m resto_agent.agent`. Requires the agent extras:
`pip install -e ".[agent]"`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from resto_agent.agent import main  # noqa: E402

if __name__ == "__main__":
    main()
