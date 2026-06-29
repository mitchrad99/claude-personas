CREATE TABLE IF NOT EXISTS task_recommendations (
    id                SERIAL PRIMARY KEY,
    title             VARCHAR(255) NOT NULL,
    description       TEXT,
    due_date          DATE,
    priority          VARCHAR(10),
    linked_contact_id INTEGER REFERENCES contacts(id),
    linked_funder_id  INTEGER REFERENCES funders(id),
    category          VARCHAR(30),
    source            VARCHAR(20),
    source_context    TEXT,
    ai_summary        TEXT,
    status            VARCHAR(20) DEFAULT 'pending',
    created_at        TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
