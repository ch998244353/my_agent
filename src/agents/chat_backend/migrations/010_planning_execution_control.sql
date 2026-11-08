DROP INDEX planning_runs_active_conversation_key;


ALTER TABLE planning_runs
    DROP CONSTRAINT planning_runs_status_check,
    DROP CONSTRAINT planning_runs_check1,
    ADD CONSTRAINT planning_runs_status_check
        CHECK (status IN (
            'drafting',
            'awaiting_approval',
            'running',
            'awaiting_tool_approval',
            'paused',
            'completed',
            'failed',
            'cancelled',
            'superseded'
        )),
    ADD CONSTRAINT planning_runs_state_check
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
                status = 'paused'
                AND definition IS NOT NULL
                AND task_events <> '[]'::jsonb
                AND run_state_snapshot IS NULL
                AND last_error IS NOT NULL
                AND last_error ~ '[^[:space:]]'
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
                AND run_state_snapshot IS NULL
                AND last_error IS NULL
            )
        );


CREATE UNIQUE INDEX planning_runs_active_conversation_key
    ON planning_runs (conversation_id)
    WHERE status IN (
        'drafting',
        'awaiting_approval',
        'running',
        'awaiting_tool_approval',
        'paused'
    );
