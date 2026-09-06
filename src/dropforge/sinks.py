from __future__ import annotations

import json

from .models import Observation


def json_stdout(observation: Observation) -> None:
    print(json.dumps(observation.public_dict(), sort_keys=True), flush=True)
