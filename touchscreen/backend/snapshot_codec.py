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
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(map(same_value, left, right))
    return left == right


class SectionEncoder:
    def __init__(self, split_status=True, immutable_status=False, build_output=True):
        self.build_output = build_output
        self.split_status = split_status
        self.immutable_status = immutable_status
        self.values = {}
        self.encoded = {}
        self.status = None
        self.output = ""
        self.full_dirty = True
        self.references = {}
        self.revisions = {}

    def encode(self, value, immutable=(), revisions=None):
        revisions = revisions or {}
        changed = set()
        removed = self.encoded.keys() - value.keys()
        for key in removed:
            self.encoded.pop(key)
            self.values.pop(key, None)
            self.references.pop(key, None)
            self.revisions.pop(key, None)
            if key == "status":
                self.status = None
        for key, item in value.items():
            if key not in revisions:
                self.revisions.pop(key, None)
            if key in revisions and key in self.revisions and self.revisions[key] == revisions[key]:
                continue
            # Explicitly immutable sections are replaced by the owner on every
            # refresh. All other values retain mutation-safe comparisons.
            if key in immutable and key in self.references and self.references[key] is item:
                continue
            if self.split_status and key == "status" and isinstance(item, dict):
                self.values.pop(key, None)
                if self.status is None:
                    self.status = SectionEncoder(split_status=False, build_output=self.build_output)
                encoded = self.status.encode(
                    item, immutable=item.keys() if self.immutable_status else ()
                )
                if key not in self.encoded or self.status.changed or self.status.removed:
                    self.encoded[key] = encoded
                    changed.add(key)
            elif key not in self.values or not same_value(self.values[key], item):
                if key == "status":
                    self.status = None
                # Encode before retaining: unsupported values must not poison
                # the cache if json.dumps raises.
                encoded = compact_json(item)
                self.values[key] = deepcopy(item)
                self.encoded[key] = encoded
                changed.add(key)
            if key in immutable:
                self.references[key] = item
            if key in revisions:
                self.revisions[key] = revisions[key]
            else:
                self.revisions.pop(key, None)
        self.changed, self.removed = changed, removed
        self.full_dirty |= bool(changed or removed)
        if self.full_dirty and self.build_output:
            self.output = self.full_json(value)
            self.full_dirty = False
        return self.output

    def full_json(self, order=None):
        return "{" + ",".join(
            compact_json(key) + ":" + (
                self.status.full_json() if key == "status" and self.status is not None
                else self.encoded[key]
            ) for key in (order if order is not None else self.encoded)
        ) + "}"


class SnapshotEncoder(SectionEncoder):
    """Each connection starts with a baseline, then receives ordered changes."""

    def __init__(self, immutable_status=False):
        super().__init__(immutable_status=immutable_status, build_output=False)
        self.sent = False

    def encode(self, value, immutable=(), revisions=None):
        previous_status_object = self.status is not None
        super().encode(value, immutable=immutable, revisions=revisions)
        if not self.sent:
            self.sent = True
            return self.full_json(value)
        if not self.changed and not self.removed:
            return None
        object_patch = previous_status_object and self.status is not None
        updates = ",".join(
            compact_json(key) + ":" + (
                self.status.full_json() if key == "status" and self.status is not None
                else self.encoded[key]
            ) for key in value if key in self.changed and not (key == "status" and object_patch)
        )
        status_patch = ""
        if object_patch and "status" in self.changed:
            changed = ",".join(
                compact_json(key) + ":" + self.status.encoded[key]
                for key in value["status"] if key in self.status.changed
            )
            status_patch = ',"status_set":{' + changed + '},"status_remove":' + compact_json(sorted(self.status.removed))
        return (
            '{"_screen_patch":true,"session":'
            + self.encoded.get("session", "null")
            + ',"set":{' + updates + '},"remove":'
            + compact_json(sorted(self.removed)) + status_patch + '}'
        )
