#!/usr/bin/env python3
"""Build the JSON config that `train_remote.py` reads on the Colab VM.

`colab exec` transmits the script to the remote VM, where local environment
variables do not exist - so configuration travels WITH the script as
/content/diq_config.json. This generator is run locally by `make colab-train`.

Usage: python scripts/colab/make_config.py /tmp/diq_config.json

Reads (optional) env vars: REPO_URL, REPO_REF, TEST_START, SEED, STAGES,
DATA_ROOT, CKPT_DIR. Unset values fall back to train_remote.py's defaults.
"""

from __future__ import annotations

import json
import os
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    stages = [s.strip() for s in os.environ.get("STAGES", "").split(",") if s.strip()]
    cfg: dict = {
        "repo_url": os.environ.get("REPO_URL"),
        "repo_ref": os.environ.get("REPO_REF"),
        "test_start": os.environ.get("TEST_START"),
        "seed": os.environ.get("SEED"),
        "stages": stages or None,
        "data_root": os.environ.get("DATA_ROOT"),
        "ckpt_dir": os.environ.get("CKPT_DIR"),
    }
    cfg = {k: v for k, v in cfg.items() if v not in (None, "")}
    with open(sys.argv[1], "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    print(f"config -> {sys.argv[1]}: {json.dumps(cfg)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
