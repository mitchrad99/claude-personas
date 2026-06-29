# Data Model

```mermaid
erDiagram
    contacts {
        int      id                  PK
        string   name
        string   organization
        string   title
        string   email
        string   phone
        string   warmth
        string   category
        date     last_contact_date
        text     notes
        datetime last_email_date
        string   last_email_subject
        string   last_email_direction
        datetime last_synced_at
        datetime created_at
        datetime updated_at
    }

    funders {
        int    id                          PK
        string organization
        string type
        text   focus_areas
        string program_officer_name
        int    program_officer_contact_id  FK
        int    ask_amount
        string status
        date   deadline
        text   notes
        datetime created_at
        datetime updated_at
    }

    tasks {
        int      id                 PK
        string   title
        text     description
        date     due_date
        string   priority
        string   status
        int      linked_contact_id  FK
        int      linked_funder_id   FK
        datetime created_at
        datetime updated_at
    }

    dc_orgs {
        int      id              PK
        string   name
        string   type
        string   priority
        int      key_contact_id  FK
        text     notes
        datetime created_at
        datetime updated_at
    }

    opportunities {
        int      id            PK
        string   title
        string   organization
        string   type
        string   status
        date     deadline
        string   salary_range
        text     notes
        datetime created_at
        datetime updated_at
    }

    inbox_recommendations {
        int      id                     PK
        string   sender_name
        string   sender_email
        string   email_subject
        datetime email_date
        text     email_snippet
        string   recommendation_type
        text     recommendation_json
        text     recommendation_summary
        string   status
        datetime created_at
    }

    contacts  ||--o{ tasks    : "linked_contact_id"
    contacts  ||--o{ funders  : "program_officer_contact_id"
    contacts  ||--o{ dc_orgs  : "key_contact_id"
    funders   ||--o{ tasks    : "linked_funder_id"
```

## Field notes

**contacts**
- `warmth` — `cold | warm | hot` (manually set)
- `category` — `advocacy | funder | government | media | peer_org | dc_network | other`
- `last_email_*` / `last_synced_at` — written by `gmail_sync.py`; never edited manually

**funders**
- `status` — `research | identified | outreach | meeting_scheduled | proposal_submitted | funded | declined | dormant`
- `program_officer_contact_id` — optional FK to contacts; the same person may appear in both tables

**tasks**
- `priority` — `low | medium | high`
- `status` — `pending | done`
- Can be linked to a contact, a funder, both, or neither

**inbox_recommendations**
- `recommendation_type` — `new_contact | new_task`
- `recommendation_json` — JSON object with Claude-suggested field values, used to pre-fill the Inbox tab form
- `status` — `pending | accepted | dismissed`; accepted rows trigger a contact or task insert
```
