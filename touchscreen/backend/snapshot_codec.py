"""Cache JSON sections and stream changes between the paired screen components.

One encoder belongs to one client. Retained values are independent copies so
in-place state changes cannot silently reuse stale JSON. Printer status is split
by object: temperatures must not re-encode the unchanged CFS material library.
Call while holding the service state lock.
"""

from copy import deepcopy
import json


def compact_json(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def same_value(left, right):
    """JSON booleans are distinct from numbers, unlike Python's True == 1."""
    if left is right:
        return True
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            same_value(item, right[key]) for key, item in left.items()
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(map(same_value, left, right))
    return left == right


class SectionEncoder:
    def __init__(self, split_status=True, immutable_status=False):
        self.split_status = split_status
        self.immutable_status = immutable_status
        self.values = {}
        self.encoded = {}
        self.status = None
        self.output = ""
        self.full_dirty = True
        self.references = {}

    def encode(self, value, immutable=()):
        changed = set()
        removed = self.encoded.keys() - value.keys()
        for key in removed:
            self.encoded.pop(key)
            self.values.pop(key, None)
            self.references.pop(key, None)
        for key, item in value.items():
            # Explicitly immutable sections are replaced by the owner on every
            # refresh. All other values retain mutation-safe comparisons.
            if key in immutable and key in self.references and self.references[key] is item:
                continue
            if self.split_status and key == "status" and isinstance(item, dict):
                self.values.pop(key, None)
                if self.status is None:
                    self.status = SectionEncoder(split_status=False)
                encoded = self.status.encode(
                    item, immutable=item.keys() if self.immutable_status else ()
                )
                if self.encoded.get(key) != encoded:
                    self.encoded[key] = encoded
                    changed.add(key)
            elif key not in self.values or not same_value(self.values[key], item):
                # Encode before retaining: unsupported values must not poison
                # the cache if json.dumps raises.
                encoded = compact_json(item)
                self.values[key] = deepcopy(item)
                self.encoded[key] = encoded
                changed.add(key)
            if key in immutable:
                self.references[key] = item
        self.changed, self.removed = changed, removed
        self.full_dirty |= bool(changed or removed)
        if self.full_dirty:
            self.output = "{" + ",".join(
                compact_json(key) + ":" + self.encoded[key] for key in value
            ) + "}"
            self.full_dirty = False
        return self.output


class SnapshotEncoder(SectionEncoder):
    """Each connection starts with a baseline, then receives ordered changes."""

    def __init__(self, immutable_status=False):
        super().__init__(immutable_status=immutable_status)
        self.sent = False

    def encode(self, value, immutable=()):
        full = super().encode(value, immutable=immutable)
        if not self.sent:
            self.sent = True
            return full
        if not self.changed and not self.removed:
            return None
        updates = ",".join(
            compact_json(key) + ":" + self.encoded[key]
            for key in value if key in self.changed
        )
        return (
            '{"_screen_patch":true,"session":'
            + self.encoded.get("session", "null")
            + ',"set":{' + updates + '},"remove":'
            + compact_json(sorted(self.removed)) + '}'
        )
