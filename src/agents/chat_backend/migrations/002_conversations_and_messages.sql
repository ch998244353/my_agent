

-- 创建 conversations 表用于记录每一个对话
CREATE TABLE conversations (
    id uuid PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    next_message_sequence bigint NOT NULL DEFAULT 1
        CHECK (next_message_sequence > 0)
);


-- 创建 conversation_messages 表 保存每一条具体消息
CREATE TABLE conversation_messages (
    id uuid PRIMARY KEY,
    conversation_id uuid NOT NULL -- 这条消息属于哪个对话
        REFERENCES conversations(id) ON DELETE CASCADE,  -- 表示这是一个外键，它必须指向 conversations 表中已经存在的对话 , 如果被引用的对话删除，那么属于这个对话的消息也自动删除
    sequence bigint NOT NULL CHECK (sequence > 0),
    role text NOT NULL  -- 这条消息是什么类型
        CHECK (role IN (
            'system',
            'user',
            'assistant',
            'tool_call',
            'tool_response'
        )),
    content text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, sequence)
);
