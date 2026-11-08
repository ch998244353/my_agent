ALTER TABLE memories
    ADD COLUMN supersedes_id uuid
        REFERENCES memories(id) ON DELETE SET NULL,
    ADD CONSTRAINT memories_supersedes_not_self
        CHECK (id <> supersedes_id);

CREATE INDEX memories_supersedes_id_idx
    ON memories (supersedes_id);
