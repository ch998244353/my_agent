CREATE TABLE knowledge_epoch (
    id smallint PRIMARY KEY CHECK (id = 1),
    value bigint NOT NULL CHECK (value >= 0)
);

INSERT INTO knowledge_epoch (id, value) VALUES (1, 0);

CREATE TABLE knowledge_sources (
    id uuid PRIMARY KEY,
    title text NOT NULL CHECK (
        char_length(title) BETWEEN 1 AND 200
        AND title = btrim(title)
    ),
    text text NOT NULL CHECK (
        char_length(text) BETWEEN 1 AND 200000
        AND text ~ '[^[:space:]]'
    ),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(metadata) = 'object'),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'indexed')),
    embedding_model text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    indexed_at timestamptz,
    CHECK (
        (
            status = 'pending'
            AND embedding_model IS NULL
            AND indexed_at IS NULL
        )
        OR
        (
            status = 'indexed'
            AND embedding_model IS NOT NULL
            AND btrim(embedding_model) <> ''
            AND indexed_at IS NOT NULL
        )
    )
);

CREATE TABLE knowledge_chunks (
    id uuid PRIMARY KEY,
    source_id uuid NOT NULL REFERENCES knowledge_sources(id) ON DELETE CASCADE,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    start_char integer NOT NULL CHECK (start_char >= 0),
    end_char integer NOT NULL,
    content text NOT NULL CHECK (content ~ '[^[:space:]]'),
    embedding vector(1024) NOT NULL,
    embedding_model text NOT NULL CHECK (btrim(embedding_model) <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (end_char > start_char),
    UNIQUE (source_id, ordinal)
);
