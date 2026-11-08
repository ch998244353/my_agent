
-- 创建一个记忆版本号/代数计数器
CREATE TABLE memory_epochs (
    name text PRIMARY KEY,  -- 每个名字只能有一条记录
    value bigint NOT NULL CHECK (value >= 0)
);

INSERT INTO memory_epochs (name, value) VALUES ('memory', 0);

-- 这表示在 conversation_messages 表中(conversation_id, id)必须唯一
ALTER TABLE conversation_messages
    ADD CONSTRAINT conversation_messages_conversation_id_id_key
    UNIQUE (conversation_id, id);  -- 每一个“会话编号 + 消息编号”的组合，都可以唯一定位一条消息。


CREATE TABLE memories (
    id uuid PRIMARY KEY,
    kind text NOT NULL
        CHECK (kind IN ('fact', 'preference', 'instruction')),
    scope text NOT NULL
        CHECK (scope IN ('global', 'conversation')),
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'disabled')),
    content text NOT NULL
        CHECK (
            char_length(btrim(content)) BETWEEN 1 AND 2000
            AND content ~ '[^[:space:]]'
        ),
    tags text[] NOT NULL DEFAULT '{}'::text[],
    confidence double precision NOT NULL DEFAULT 1.0
        CHECK (confidence BETWEEN 0.0 AND 1.0),
    fingerprint text NOT NULL
        CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    source_conversation_id uuid NOT NULL
        REFERENCES conversations(id) ON DELETE CASCADE,
    source_user_message_id uuid NOT NULL,
    source_assistant_message_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (source_user_message_id <> source_assistant_message_id),
    FOREIGN KEY (source_conversation_id, source_user_message_id)
        REFERENCES conversation_messages(conversation_id, id)
        ON DELETE CASCADE,
    FOREIGN KEY (source_conversation_id, source_assistant_message_id)
        REFERENCES conversation_messages(conversation_id, id)
        ON DELETE CASCADE
);


--  PostgreSQL 里的两个持久化“索引对象” -- 创建一个会禁止重复键的索引对象
--  1. 建库或升级阶段：执行一次 , 数据库迁移运行 CREATE INDEX，把索引长期留在 PostgreSQL 中。若表里已经有违反规则的重复数据，创建会失败。
--  2.  正常请求阶段：自动检查


-- global 长期记忆
CREATE UNIQUE INDEX memories_active_global_fingerprint_key
    ON memories (fingerprint)  -- 这个索引属于 memories 表 , 用指纹作为判重键。
    -- 只看 scope='global' AND status='active' 的行；
    -- 在这些行里，fingerprint 不准重复
    WHERE scope = 'global' AND status = 'active';


-- conversation 会话记忆
CREATE UNIQUE INDEX memories_active_conversation_fingerprint_key
    ON memories (source_conversation_id, fingerprint)  -- 把“会话编号 + 指纹”的组合当作判重键
    WHERE scope = 'conversation' AND status = 'active';
