ALTER TABLE conversations
    ADD COLUMN title text NOT NULL DEFAULT 'New conversation'
        CHECK (
            char_length(title) BETWEEN 1 AND 200
            AND title !~ '^[[:space:]]'
            AND title !~ '[[:space:]]$'
        ),
    ADD COLUMN status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'archived'));
