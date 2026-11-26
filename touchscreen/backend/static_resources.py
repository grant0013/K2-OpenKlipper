"""Atomic, versioned files for data outside the live telemetry stream."""

import hashlib
import json
import os
from pathlib import Path


class ResourceFile:
    def __init__(self, path):
        self.path = Path(path)
        self.descriptor = None

    def publish(self, value):
        body = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
        revision = hashlib.sha256(body).hexdigest()
        if self.descriptor and revision == self.descriptor["revision"]:
            return self.descriptor
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.new")
        with temporary.open("wb") as stream:
            os.chmod(temporary, 0o600)
            stream.write(body)
        temporary.replace(self.path)
        self.descriptor = {"path": str(self.path), "revision": revision}
        return self.descriptor
