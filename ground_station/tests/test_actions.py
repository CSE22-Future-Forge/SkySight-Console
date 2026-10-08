"""Claim race, idempotency, visibility events and coordinator overrides (spec 5, 7.5, 12)."""

import threading

import pytest

from ground_station.core.services import ServiceError

from .conftest import add_device, cei, ingest


def _events(m, victim_id):
    return m.db.conn().execute(
        "SELECT type, scope_team_id, laptop_only, payload_json FROM events WHERE entity_id = ? ORDER BY seq",
        (victim_id,)).fetchall()


def _history(m, victim_id):
    return m.db.conn().execute("SELECT * FROM status_history WHERE victim_id = ? ORDER BY id",
                               (victim_id,)).fetchall()


def test_claim_race_has_exactly_one_winner(manifest):
    """Acceptance test: 2 phones x 20 trials -> exactly one winner each time."""
    a, _ = add_device(manifest, manifest.alpha)
    b, _ = add_device(manifest, manifest.bravo)
    for trial in range(20):
        vid = ingest(manifest, track_id=1000 + trial,
                     lat=7.2906 + trial * 0.01)["victim_id"]
        barrier = threading.Barrier(2)
        outcomes = {}

        def go(device, key):
            barrier.wait()
            try:
                manifest.victims.accept(vid, device, cei())
                outcomes[key] = "won"
            except ServiceError as exc:
                outcomes[key] = exc.code

        threads = [threading.Thread(target=go, args=(a, "a")), threading.Thread(target=go, args=(b, "b"))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(outcomes.values()) == ["already_claimed", "won"], outcomes
        assert manifest.victims.get(vid)["status"] == "ongoing"


def test_loser_learns_who_has_it(manifest):
    a, _ = add_device(manifest, manifest.alpha)
    b, _ = add_device(manifest, manifest.bravo)
    vid = ingest(manifest, track_id=1)["victim_id"]
    manifest.victims.accept(vid, a, cei())
    with pytest.raises(ServiceError) as exc:
        manifest.victims.accept(vid, b, cei())
    assert exc.value.code == "already_claimed" and exc.value.extra["team"]["name"] == "Alpha"
    rejected = [h for h in _history(manifest, vid) if not h["applied"]]
    assert len(rejected) == 1 and rejected[0]["reject_reason"] == "already_claimed"


def test_accept_events_hide_victim_from_other_teams(manifest):
    a, _ = add_device(manifest, manifest.alpha)
    vid = ingest(manifest, track_id=1)["victim_id"]
    manifest.victims.accept(vid, a, cei())
    ev = [(e["type"], e["scope_team_id"]) for e in _events(manifest, vid)]
    assert ("victim.created", None) in ev
    assert ("victim.updated", manifest.alpha["id"]) in ev
    assert ("victim.hidden", manifest.bravo["id"]) in ev
    assert ("victim.hidden", manifest.alpha["id"]) not in ev

    bravo_view = manifest.victims.visible_for_team(manifest.bravo["id"])["victims"]
    alpha_view = manifest.victims.visible_for_team(manifest.alpha["id"])["victims"]
    assert vid not in [v["id"] for v in bravo_view]
    assert vid in [v["id"] for v in alpha_view]


def test_offline_complete_is_idempotent(manifest):
    """Acceptance test: complete offline -> syncs once, no duplicate history row."""
    a, _ = add_device(manifest, manifest.alpha)
    vid = ingest(manifest, track_id=1)["victim_id"]
    manifest.victims.accept(vid, a, cei())
    key = cei()
    first = manifest.victims.complete(vid, a, key, people_count=2, note="two adults")
    again = manifest.victims.complete(vid, a, key, people_count=2, note="two adults")
    assert first["victim"]["status"] == again["victim"]["status"] == "completed"
    assert first["victim"]["version"] == again["victim"]["version"]
    assert sum(1 for h in _history(manifest, vid) if h["client_event_id"] == key) == 1
    assert manifest.victims.get(vid)["people_count"] == 2


def test_rejected_attempt_replays_same_error(manifest):
    a, _ = add_device(manifest, manifest.alpha)
    b, _ = add_device(manifest, manifest.bravo)
    vid = ingest(manifest, track_id=1)["victim_id"]
    manifest.victims.accept(vid, a, cei())
    key = cei()
    for _ in range(2):
        with pytest.raises(ServiceError) as exc:
            manifest.victims.accept(vid, b, key)
        assert exc.value.code == "already_claimed"
    assert sum(1 for h in _history(manifest, vid) if h["client_event_id"] == key) == 1


def test_second_phone_of_same_team_is_a_noop(manifest):
    a1, _ = add_device(manifest, manifest.alpha)
    a2, _ = add_device(manifest, manifest.alpha)
    vid = ingest(manifest, track_id=1)["victim_id"]
    manifest.victims.accept(vid, a1, cei())
    assert manifest.victims.accept(vid, a2, cei())["victim"]["assigned_team_id"] == manifest.alpha["id"]


def test_release_and_complete_need_assigned_team(manifest):
    a, _ = add_device(manifest, manifest.alpha)
    b, _ = add_device(manifest, manifest.bravo)
    vid = ingest(manifest, track_id=1)["victim_id"]
    manifest.victims.accept(vid, a, cei())
    for action in (manifest.victims.release, manifest.victims.complete):
        with pytest.raises(ServiceError) as exc:
            action(vid, b, cei())
        assert exc.value.code == "not_assigned"
    released = manifest.victims.release(vid, a, cei(), reason="need boat")
    assert released["victim"]["status"] == "open" and released["victim"]["assigned_team_id"] is None


def test_pending_victims_are_invisible_to_teams(manifest):
    manifest.missions.set_alert_mode("verify")
    a, _ = add_device(manifest, manifest.alpha)
    vid = ingest(manifest, track_id=1)["victim_id"]
    assert manifest.victims.visible_for_team(manifest.alpha["id"])["victims"] == []
    with pytest.raises(ServiceError) as exc:
        manifest.victims.accept(vid, a, cei())
    assert exc.value.code == "not_found"
    assert manifest.sync.events_for_team(manifest.alpha["id"], 0)["events"] == []


def test_dismiss_hides_from_everyone(manifest):
    vid = ingest(manifest, track_id=1)["victim_id"]
    manifest.victims.dismiss(vid, note="false positive: goat")
    hidden = [e for e in _events(manifest, vid) if e["type"] == "victim.hidden"]
    assert len(hidden) == 1 and hidden[0]["scope_team_id"] is None and '"dismissed"' in hidden[0]["payload_json"]


def test_coordinator_reassign_moves_visibility(manifest):
    a, _ = add_device(manifest, manifest.alpha)
    vid = ingest(manifest, track_id=1)["victim_id"]
    manifest.victims.accept(vid, a, cei())
    manifest.victims.assign(vid, manifest.bravo["id"])
    v = manifest.victims.get(vid)
    assert v["status"] == "ongoing" and v["assigned_team_id"] == manifest.bravo["id"]
    ev = [(e["type"], e["scope_team_id"]) for e in _events(manifest, vid)][-3:]
    assert ev == [("victim.updated", manifest.bravo["id"]), ("victim.hidden", manifest.alpha["id"]),
                  ("assignment.changed", manifest.bravo["id"])]
    with pytest.raises(ServiceError):  # Alpha can no longer finish it
        manifest.victims.complete(vid, a, cei())


def test_coordinator_full_lifecycle_and_invalid_moves(manifest):
    vid = ingest(manifest, track_id=1)["victim_id"]
    manifest.victims.assign(vid, manifest.alpha["id"])
    manifest.victims.mark_completed(vid, people_count=1)
    manifest.victims.reopen(vid)
    assert manifest.victims.get(vid)["status"] == "ongoing"
    manifest.victims.release_coordinator(vid)
    assert manifest.victims.get(vid)["status"] == "open"
    with pytest.raises(ServiceError) as exc:
        manifest.victims.reopen(vid)
    assert exc.value.code == "invalid_transition"
    assert _history(manifest, vid)[-1]["applied"] == 0
    manifest.victims.dismiss(vid)
    manifest.victims.restore(vid)
    assert manifest.victims.get(vid)["status"] == "open"


def test_version_bumps_on_every_change(manifest):
    a, _ = add_device(manifest, manifest.alpha)
    vid = ingest(manifest, track_id=1)["victim_id"]
    v1 = manifest.victims.get(vid)["version"]
    manifest.victims.accept(vid, a, cei())
    manifest.victims.add_note(vid, "bring stretcher")
    assert manifest.victims.get(vid)["version"] == v1 + 2


def test_receipts_counted(manifest):
    a, _ = add_device(manifest, manifest.alpha)
    b, _ = add_device(manifest, manifest.bravo)
    vid = ingest(manifest, track_id=1)["victim_id"]
    manifest.victims.add_receipt(vid, a, delivered_at=10)
    manifest.victims.add_receipt(vid, a, opened_at=20)
    manifest.victims.add_receipt(vid, a, delivered_at=99)  # later duplicate keeps the earliest
    manifest.victims.add_receipt(vid, b, delivered_at=11)
    assert manifest.victims.receipts(vid) == {"delivered": 2, "opened": 1, "devices": 2}
    row = manifest.db.conn().execute("SELECT * FROM alert_receipts WHERE device_id = ?", (a["id"],)).fetchone()
    assert row["delivered_at"] == 10 and row["opened_at"] == 20
