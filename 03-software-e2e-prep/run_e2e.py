#!/usr/bin/env python3

from pathlib import Path
import sys

EXIT_CODE = 2


def main() -> int:
    prep_dir = Path(__file__).resolve().parent
    software_e2e_dir = prep_dir.parent / "software-e2e"

    message = f"""
Deprecated entrypoint: 03-software-e2e-prep/run_e2e.py

Use the canonical TypeScript runner instead:

  cd {software_e2e_dir}
  npm run check:phase3

Or run one prep fixture:

  npm run run:fixture -- ../03-software-e2e-prep/fixtures/<fixture>.yaml

The Python prototype under 03-software-e2e-prep/ is kept only as historical reference.
"""

    print(message.strip())
    return EXIT_CODE


if __name__ == "__main__":
    sys.exit(main())
