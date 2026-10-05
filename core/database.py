"""Person database with feature vectors and duplicate detection.

Persistence is a single JSON document plus PNG crops on disk — no server, no
extra dependency, and the file stays human-inspectable for field debugging.

Duplicate handling is the point of this module: every stored person carries an
embedding, and :meth:`PersonDatabase.match_candidates` ranks new captures
against the existing roster so the operator is warned before filing the same
subject twice.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime

import cv2
import numpy as np

from .features import cosine_similarity, similarity_label

SCHEMA_VERSION = 1

# Duplicate thresholds.
#
# These are calibrated against the measured score distribution of the
# placeholder descriptor in core/features.py, where same-subject pairs land
# around 0.99 and different-subject pairs top out near 0.95. Re-measure with
# :func:`core.features.suggest_thresholds` once the real re-ID embedding is
# wired in — a learned embedding changes the scale completely, and reusing
# these numbers would either flag everything or nothing.
WARN_AT = 0.96
DANGER_AT = 0.985


@dataclass
class Capture:
    """One filed observation of a person."""

    thumb: str
    source: str = "rgb"
    score: float = 0.0
    temp_c: float | None = None
    camera_id: int | None = None
    captured_at: str = ""

    def short_time(self):
        try:
            return datetime.fromisoformat(self.captured_at).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return self.captured_at


@dataclass
class Person:
    person_id: str
    created_at: str
    embedding: list
    captures: list = field(default_factory=list)
    notes: str = ""
    tags: list = field(default_factory=list)

    @property
    def capture_count(self):
        return len(self.captures)

    @property
    def sources(self):
        return sorted({c.source for c in self.captures})

    @property
    def best_temp(self):
        vals = [c.temp_c for c in self.captures if c.temp_c is not None]
        return max(vals) if vals else None

    @property
    def first_seen(self):
        return self.created_at.replace("T", " ")[:19]

    def thumb_path(self, thumb_dir):
        """Absolute path to a capture thumbnail.

        ``thumb_dir`` is the *thumbnail* directory (PersonDatabase.thumb_dir),
        not the database root — crops live in the ``thumbs`` subfolder.
        """
        if not self.captures or not self.captures[0].thumb:
            return ""
        return os.path.join(thumb_dir, self.captures[0].thumb)


@dataclass
class MatchCandidate:
    person: Person
    score: float

    @property
    def label(self):
        return similarity_label(self.score, WARN_AT, DANGER_AT)

    @property
    def is_duplicate(self):
        return self.score >= WARN_AT


@dataclass
class NewPersonDraft:
    """A review-grid selection awaiting confirmation."""

    crop_bgr: np.ndarray
    embedding: list
    source: str = "rgb"
    score: float = 0.0
    temp_c: float | None = None
    camera_id: int | None = None
    track_id: int | None = None


class PersonDatabase:
    def __init__(self, root="data", warn_at=WARN_AT, danger_at=DANGER_AT):
        self.root = os.path.abspath(root)
        self.thumb_dir = os.path.join(self.root, "thumbs")
        self.path = os.path.join(self.root, "persons.json")
        self.warn_at = warn_at
        self.danger_at = danger_at
        self._persons: list[Person] = []
        os.makedirs(self.thumb_dir, exist_ok=True)
        self.load()

    # ---------------------------------------------------------- persistence --
    def load(self):
        self._persons = []
        if not os.path.exists(self.path):
            return self._persons
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                blob = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return self._persons

        for entry in blob.get("persons", []):
            try:
                person = Person(
                    person_id=entry["person_id"],
                    created_at=entry.get("created_at", ""),
                    embedding=[float(v) for v in entry.get("embedding", [])],
                    captures=[Capture(**c) for c in entry.get("captures", [])],
                    notes=entry.get("notes", ""),
                    tags=list(entry.get("tags", [])),
                )
            except (KeyError, TypeError):
                continue
            self._persons.append(person)
        return self._persons

    def save(self):
        blob = {
            "schema": SCHEMA_VERSION,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "warn_at": self.warn_at,
            "danger_at": self.danger_at,
            "persons": [asdict(p) for p in self._persons],
        }
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(blob, fh, indent=1)
        os.replace(tmp, self.path)

    # ------------------------------------------------------------- accessors --
    def all(self):
        return sorted(self._persons, key=lambda p: p.person_id)

    def get(self, person_id):
        return next((p for p in self._persons if p.person_id == person_id), None)

    def count(self):
        return len(self._persons)

    def capture_count(self):
        return sum(p.capture_count for p in self._persons)

    def next_id(self):
        highest = 0
        for p in self._persons:
            tail = p.person_id.split("-")[-1]
            if tail.isdigit():
                highest = max(highest, int(tail))
        return f"P-{highest + 1:04d}"

    # ---------------------------------------------------------------- writes --
    def add_draft(self, draft: NewPersonDraft) -> Person:
        person_id = self.next_id()
        now = datetime.now().isoformat(timespec="seconds")

        thumb_name = ""
        if draft.crop_bgr is not None and draft.crop_bgr.size:
            thumb_name = f"{person_id}_001.png"
            thumb_path = os.path.join(self.thumb_dir, thumb_name)
            try:
                cv2.imwrite(thumb_path, draft.crop_bgr)
            except cv2.error:
                thumb_name = ""

        person = Person(
            person_id=person_id,
            created_at=now,
            embedding=list(draft.embedding),
            captures=[
                Capture(
                    thumb=thumb_name,
                    source=draft.source,
                    score=round(float(draft.score), 4),
                    temp_c=draft.temp_c,
                    camera_id=draft.camera_id,
                    captured_at=now,
                )
            ],
        )
        self._persons.append(person)
        self.save()
        return person

    def add_drafts(self, drafts):
        return [self.add_draft(d) for d in drafts]

    def append_capture(self, person_id, draft: NewPersonDraft):
        person = self.get(person_id)
        if person is None:
            return None
        idx = len(person.captures) + 1
        thumb_name = ""
        if draft.crop_bgr is not None and draft.crop_bgr.size:
            thumb_name = f"{person_id}_{idx:03d}.png"
            try:
                cv2.imwrite(os.path.join(self.thumb_dir, thumb_name), draft.crop_bgr)
            except cv2.error:
                thumb_name = ""
        person.captures.append(
            Capture(
                thumb=thumb_name,
                source=draft.source,
                score=round(float(draft.score), 4),
                temp_c=draft.temp_c,
                camera_id=draft.camera_id,
                captured_at=datetime.now().isoformat(timespec="seconds"),
            )
        )
        self.save()
        return person

    def delete(self, person_id):
        person = self.get(person_id)
        if person is None:
            return False
        for capture in person.captures:
            if capture.thumb:
                thumb_path = os.path.join(self.thumb_dir, capture.thumb)
                if os.path.exists(thumb_path):
                    try:
                        os.remove(thumb_path)
                    except OSError:
                        pass
        self._persons.remove(person)
        self.save()
        return True

    def clear(self):
        for person_id in [p.person_id for p in self._persons]:
            self.delete(person_id)

    # ------------------------------------------------------------ similarity --
    def match_candidates(self, embedding, limit=3, threshold=0.0):
        """Rank existing people against one embedding, best first."""
        scored = [
            MatchCandidate(p, cosine_similarity(embedding, p.embedding))
            for p in self._persons
        ]
        scored.sort(key=lambda m: m.score, reverse=True)
        if threshold > 0:
            scored = [m for m in scored if m.score >= threshold]
        return scored[:limit]

    def best_match(self, embedding):
        ranked = self.match_candidates(embedding, limit=1)
        return ranked[0] if ranked else None

    def duplicate_report(self):
        """All within-roster pairs above the warning threshold.

        Returns a list of ``(person_a, person_b, score)`` sorted worst-first so
        the UI can surface the most convincing duplicates at the top.
        """
        pairs = []
        people = self.all()
        for i in range(len(people)):
            for j in range(i + 1, len(people)):
                score = cosine_similarity(people[i].embedding, people[j].embedding)
                if score >= self.warn_at:
                    pairs.append((people[i], people[j], score))
        pairs.sort(key=lambda t: t[2], reverse=True)
        return pairs

    def flagged_ids(self):
        ids = set()
        for a, b, _score in self.duplicate_report():
            ids.add(a.person_id)
            ids.add(b.person_id)
        return ids

    def export_json(self, path):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "schema": SCHEMA_VERSION,
                    "exported_at": datetime.now().isoformat(timespec="seconds"),
                    "persons": [asdict(p) for p in self.all()],
                },
                fh,
                indent=1,
            )
        return path

    def load_image(self, person: Person, index=0):
        if not person.captures:
            return None
        index = int(np.clip(index, 0, len(person.captures) - 1))
        name = person.captures[index].thumb
        if not name:
            return None
        path = os.path.join(self.thumb_dir, name)
        if not os.path.exists(path):
            return None
        return cv2.imread(path, cv2.IMREAD_COLOR)