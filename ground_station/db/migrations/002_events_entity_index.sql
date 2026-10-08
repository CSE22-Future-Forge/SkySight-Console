-- /victims needs each victim's latest event seq (Victim.updated_seq).
CREATE INDEX IF NOT EXISTS ix_events_entity ON events(entity_id, seq);
