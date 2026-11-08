ALTER TABLE conversation_messages
    ADD COLUMN run_trace jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(run_trace) = 'array');
