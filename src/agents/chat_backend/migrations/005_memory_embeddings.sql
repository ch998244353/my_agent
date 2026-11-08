ALTER TABLE memories
    ADD COLUMN embedding vector(1024),
    ADD COLUMN embedding_model text,
    ADD COLUMN embedded_at timestamptz,
    ADD CONSTRAINT memories_embedding_state_check
        CHECK (
            (
                embedding IS NULL
                AND embedding_model IS NULL
                AND embedded_at IS NULL
            )
            OR
            (
                embedding IS NOT NULL
                AND embedding_model IS NOT NULL
                AND btrim(embedding_model) <> ''
                AND embedded_at IS NOT NULL
            )
        );
