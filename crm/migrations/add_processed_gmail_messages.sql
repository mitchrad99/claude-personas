CREATE TABLE IF NOT EXISTS processed_gmail_message_ids (
    id           SERIAL PRIMARY KEY,
    message_id   VARCHAR(255) NOT NULL UNIQUE,
    processed_at TIMESTAMP DEFAULT NOW()
);
