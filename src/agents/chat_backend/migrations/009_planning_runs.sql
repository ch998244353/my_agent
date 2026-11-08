ALTER TABLE conversations
    ADD COLUMN mode text NOT NULL DEFAULT 'default'
        CHECK (mode IN ('default', 'plan'));


-- 每一行保存同一会话中的一版计划，步骤状态仍由 TaskEvent 表达
CREATE TABLE planning_runs (
    id uuid PRIMARY KEY,
    conversation_id uuid NOT NULL
        REFERENCES conversations(id) ON DELETE CASCADE,
    version bigint NOT NULL CHECK (version > 0),
    row_revision bigint NOT NULL DEFAULT 1 CHECK (row_revision > 0),
    status text NOT NULL
        CHECK (status IN (
            'drafting',
            'awaiting_approval',
            'running',
            'awaiting_tool_approval',
            'completed',
            'failed',
            'cancelled',
            'superseded'
        )),
    objective text NOT NULL CHECK (objective ~ '[^[:space:]]'),
    supersedes_version bigint,
    revision_note text
        CHECK (
            revision_note IS NULL
            OR revision_note ~ '[^[:space:]]'
        ),
    last_error text,
    definition jsonb
        CHECK (
            definition IS NULL
            OR jsonb_typeof(definition) = 'object'
        ),
    task_events jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(task_events) = 'array'),
    run_state_snapshot jsonb
        CHECK (
            run_state_snapshot IS NULL
            OR jsonb_typeof(run_state_snapshot) = 'object'
        ),
    model_snapshot jsonb NOT NULL
        CHECK (jsonb_typeof(model_snapshot) = 'object'),
    workspace_snapshot jsonb NOT NULL
        CHECK (jsonb_typeof(workspace_snapshot) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, version),
    CHECK (
        supersedes_version IS NULL
        OR (
            supersedes_version > 0
            AND supersedes_version < version
        )
    ),
    CHECK (
        (
            status = 'drafting'
            AND definition IS NULL
            AND task_events = '[]'::jsonb
            AND run_state_snapshot IS NULL
            AND last_error IS NULL
        )
        OR (
            status = 'awaiting_approval'
            AND definition IS NOT NULL
            AND task_events = '[]'::jsonb
            AND run_state_snapshot IS NULL
            AND last_error IS NULL
        )
        OR (
            status = 'running'
            AND definition IS NOT NULL
            AND task_events <> '[]'::jsonb
            AND run_state_snapshot IS NULL
            AND last_error IS NULL
        )
        OR (
            status = 'awaiting_tool_approval'
            AND definition IS NOT NULL
            AND task_events <> '[]'::jsonb
            AND run_state_snapshot IS NOT NULL
            AND last_error IS NULL
        )
        OR (
            status = 'completed'
            AND definition IS NOT NULL
            AND task_events <> '[]'::jsonb
            AND run_state_snapshot IS NULL
            AND last_error IS NULL
        )
        OR (
            status = 'failed'
            AND definition IS NOT NULL
            AND task_events <> '[]'::jsonb
            AND run_state_snapshot IS NULL
            AND last_error IS NOT NULL
            AND last_error ~ '[^[:space:]]'
        )
        OR (
            status = 'cancelled'
            AND run_state_snapshot IS NULL
            AND last_error IS NULL
        )
        OR (
            status = 'superseded'
            AND definition IS NOT NULL
            AND task_events = '[]'::jsonb
            AND run_state_snapshot IS NULL
            AND last_error IS NULL
        )
    )
);


CREATE UNIQUE INDEX planning_runs_active_conversation_key
    ON planning_runs (conversation_id)
    WHERE status IN (
        'drafting',
        'awaiting_approval',
        'running',
        'awaiting_tool_approval'
    );


CREATE INDEX planning_runs_recoverable_running_idx
    ON planning_runs (updated_at, id)
    WHERE status = 'running';
