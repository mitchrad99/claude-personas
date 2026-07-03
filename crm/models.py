import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, Date, DateTime, Boolean, ForeignKey, CheckConstraint, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEST_MODE = os.environ.get('TEST_MODE', '').lower() in ('1', 'true', 'yes')

if TEST_MODE:
    DATABASE_URL = f"sqlite:///{os.path.join(BASE_DIR, 'local_test.db')}"
else:
    _raw = os.environ.get('DATABASE_URL')
    if not _raw:
        raise RuntimeError(
            "DATABASE_URL is not set and TEST_MODE is not enabled — "
            "refusing to start against an undefined database"
        )
    DATABASE_URL = _raw
    # Render (and legacy Heroku) inject postgres:// which SQLAlchemy 1.4+ rejects
    if DATABASE_URL.startswith('postgres://'):
        DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

_connect_args = {'check_same_thread': False} if DATABASE_URL.startswith('sqlite') else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)
Base = declarative_base()
SessionLocal = sessionmaker(bind=engine)


def get_session():
    return SessionLocal()


def init_db():
    Base.metadata.create_all(engine)


class Contact(Base):
    __tablename__ = 'contacts'

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    organization = Column(String(255))
    title = Column(String(255))
    email = Column(String(255))
    phone = Column(String(50))
    warmth = Column(String(10), default='cold')       # cold, warm, hot
    category = Column(String(20), default='other')    # advocacy, funder, government, media, peer_org, dc_network, mentor, other
    last_contact_date = Column(Date)
    notes = Column(Text)
    last_email_date = Column(DateTime)
    last_email_subject = Column(String(500))
    last_email_direction = Column(String(10))   # 'inbound' or 'outbound'
    last_synced_at = Column(DateTime)
    slack_user_id = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    tasks = relationship('Task', foreign_keys='Task.linked_contact_id', back_populates='contact')
    funders = relationship('Funder', foreign_keys='Funder.program_officer_contact_id', back_populates='program_officer')
    dc_orgs = relationship('DCOrg', foreign_keys='DCOrg.key_contact_id', back_populates='key_contact')
    interactions = relationship('Interaction', foreign_keys='Interaction.contact_id', back_populates='contact')
    contact_notes = relationship('ContactNote', foreign_keys='ContactNote.contact_id', back_populates='contact')

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'organization': self.organization,
            'title': self.title,
            'email': self.email,
            'phone': self.phone,
            'warmth': self.warmth,
            'category': self.category,
            'last_contact_date': self.last_contact_date.isoformat() if self.last_contact_date else None,
            'notes': self.notes,
            'last_email_date': self.last_email_date.isoformat() if self.last_email_date else None,
            'last_email_subject': self.last_email_subject,
            'last_email_direction': self.last_email_direction,
            'last_synced_at': self.last_synced_at.isoformat() if self.last_synced_at else None,
            'slack_user_id': self.slack_user_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class Funder(Base):
    __tablename__ = 'funders'

    id = Column(Integer, primary_key=True)
    organization = Column(String(255), nullable=False)
    type = Column(String(20))    # foundation, corporate, government, individual
    focus_areas = Column(Text)
    program_officer_name = Column(String(255))
    program_officer_contact_id = Column(Integer, ForeignKey('contacts.id'))
    ask_amount = Column(Integer)
    status = Column(String(30), default='research')   # research, identified, outreach, meeting_scheduled, proposal_submitted, funded, declined, dormant
    deadline = Column(Date)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    program_officer = relationship('Contact', foreign_keys=[program_officer_contact_id], back_populates='funders')
    tasks = relationship('Task', foreign_keys='Task.linked_funder_id', back_populates='funder')

    def to_dict(self):
        return {
            'id': self.id,
            'organization': self.organization,
            'type': self.type,
            'focus_areas': self.focus_areas,
            'program_officer_name': self.program_officer_name,
            'program_officer_contact_id': self.program_officer_contact_id,
            'ask_amount': self.ask_amount,
            'status': self.status,
            'deadline': self.deadline.isoformat() if self.deadline else None,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class Task(Base):
    __tablename__ = 'tasks'

    id = Column(Integer, primary_key=True)
    title = Column(String(255), nullable=False)
    description = Column(Text)
    due_date = Column(Date)
    priority = Column(String(10), default='medium')   # low, medium, high
    status = Column(String(10), default='pending')    # pending, done
    category = Column(String(30))   # outreach, intro_followup, fundraising, policy, admin, career, sabbatical_prep
    linked_contact_id = Column(Integer, ForeignKey('contacts.id'))
    linked_funder_id = Column(Integer, ForeignKey('funders.id'))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    contact = relationship('Contact', foreign_keys=[linked_contact_id], back_populates='tasks')
    funder = relationship('Funder', foreign_keys=[linked_funder_id], back_populates='tasks')

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'due_date': self.due_date.isoformat() if self.due_date else None,
            'priority': self.priority,
            'status': self.status,
            'category': self.category,
            'linked_contact_id': self.linked_contact_id,
            'linked_funder_id': self.linked_funder_id,
            'contact_name': self.contact.name if self.contact else None,
            'funder_name': self.funder.organization if self.funder else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class DCOrg(Base):
    __tablename__ = 'dc_orgs'

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    type = Column(String(20))     # think_tank, advocacy, congressional, agency, coalition, media
    priority = Column(String(10), default='medium')   # low, medium, high
    key_contact_id = Column(Integer, ForeignKey('contacts.id'))
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    key_contact = relationship('Contact', foreign_keys=[key_contact_id], back_populates='dc_orgs')

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'type': self.type,
            'priority': self.priority,
            'key_contact_id': self.key_contact_id,
            'key_contact_name': self.key_contact.name if self.key_contact else None,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class InboxRecommendation(Base):
    __tablename__ = 'inbox_recommendations'

    id                     = Column(Integer, primary_key=True)
    sender_name            = Column(String(255))
    sender_email           = Column(String(255))
    email_subject          = Column(String(500))
    email_date             = Column(DateTime)
    email_snippet          = Column(Text)
    recommendation_type    = Column(String(20))   # 'new_contact' or 'new_task'
    recommendation_json    = Column(Text)          # suggested_fields as JSON
    recommendation_summary = Column(Text)
    status                 = Column(String(20), default='pending')  # pending/accepted/dismissed
    created_at             = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id':                     self.id,
            'sender_name':            self.sender_name,
            'sender_email':           self.sender_email,
            'email_subject':          self.email_subject,
            'email_date':             self.email_date.isoformat() if self.email_date else None,
            'email_snippet':          self.email_snippet,
            'recommendation_type':    self.recommendation_type,
            'recommendation_json':    self.recommendation_json,
            'recommendation_summary': self.recommendation_summary,
            'status':                 self.status,
            'created_at':             self.created_at.isoformat() if self.created_at else None,
        }


class Opportunity(Base):
    __tablename__ = 'opportunities'

    id = Column(Integer, primary_key=True)
    title = Column(String(255), nullable=False)
    organization = Column(String(255))
    type = Column(String(20))     # job, fellowship, board, consulting, speaking
    status = Column(String(20), default='identified')  # identified, applied, interviewing, offer, declined, closed
    deadline = Column(Date)
    salary_range = Column(String(100))
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'organization': self.organization,
            'type': self.type,
            'status': self.status,
            'deadline': self.deadline.isoformat() if self.deadline else None,
            'salary_range': self.salary_range,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class Interaction(Base):
    __tablename__ = 'interactions'

    id               = Column(Integer, primary_key=True)
    contact_id       = Column(Integer, ForeignKey('contacts.id'), nullable=False)
    date             = Column(Date, nullable=False)
    type             = Column(String(20))    # meeting, call, event, coffee, text, linkedin
    location         = Column(String(255))
    notes            = Column(Text)
    follow_up_needed = Column(Boolean, default=False)
    created_at       = Column(DateTime, default=datetime.utcnow)
    updated_at       = Column(DateTime, default=datetime.utcnow)

    contact = relationship('Contact', foreign_keys=[contact_id], back_populates='interactions')

    def to_dict(self):
        return {
            'id':               self.id,
            'contact_id':       self.contact_id,
            'contact_name':     self.contact.name if self.contact else None,
            'date':             self.date.isoformat() if self.date else None,
            'type':             self.type,
            'location':         self.location,
            'notes':            self.notes,
            'follow_up_needed': self.follow_up_needed,
            'created_at':       self.created_at.isoformat() if self.created_at else None,
            'updated_at':       self.updated_at.isoformat() if self.updated_at else None,
        }


class ContactNote(Base):
    __tablename__ = 'contact_notes'

    id         = Column(Integer, primary_key=True)
    contact_id = Column(Integer, ForeignKey('contacts.id'), nullable=False)
    note       = Column(Text, nullable=False)
    source     = Column(String(20), default='manual')   # manual, chat_debrief, ai_generated
    created_at = Column(DateTime, default=datetime.utcnow)

    contact = relationship('Contact', foreign_keys=[contact_id], back_populates='contact_notes')

    def to_dict(self):
        return {
            'id':           self.id,
            'contact_id':   self.contact_id,
            'contact_name': self.contact.name if self.contact else None,
            'note':         self.note,
            'source':       self.source,
            'created_at':   self.created_at.isoformat() if self.created_at else None,
        }


class ContactRelationship(Base):
    __tablename__ = 'contact_relationships'
    __table_args__ = (
        CheckConstraint('from_contact_id != to_contact_id', name='ck_contact_rel_no_self'),
        UniqueConstraint('from_contact_id', 'to_contact_id', 'type', name='uq_contact_rel'),
    )

    id              = Column(Integer, primary_key=True)
    from_contact_id = Column(Integer, ForeignKey('contacts.id'), nullable=False)
    to_contact_id   = Column(Integer, ForeignKey('contacts.id'), nullable=False)
    type            = Column(String(30))    # introduced_by, wants_to_connect, peer, mentor, referred_funder
    status          = Column(String(20), default='completed')   # completed, pending
    notes           = Column(Text)
    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, default=datetime.utcnow)

    from_contact = relationship('Contact', foreign_keys=[from_contact_id])
    to_contact   = relationship('Contact', foreign_keys=[to_contact_id])

    def to_dict(self):
        return {
            'id':                self.id,
            'from_contact_id':   self.from_contact_id,
            'from_contact_name': self.from_contact.name if self.from_contact else None,
            'to_contact_id':     self.to_contact_id,
            'to_contact_name':   self.to_contact.name if self.to_contact else None,
            'type':              self.type,
            'status':            self.status,
            'notes':             self.notes,
            'created_at':        self.created_at.isoformat() if self.created_at else None,
            'updated_at':        self.updated_at.isoformat() if self.updated_at else None,
        }


class ProcessedGmailMessage(Base):
    __tablename__ = 'processed_gmail_message_ids'

    id           = Column(Integer, primary_key=True)
    message_id   = Column(String(255), nullable=False, unique=True)
    processed_at = Column(DateTime, default=datetime.utcnow)


class TaskRecommendation(Base):
    __tablename__ = 'task_recommendations'

    id                = Column(Integer, primary_key=True)
    title             = Column(String(255), nullable=False)
    description       = Column(Text)
    due_date          = Column(Date)
    priority          = Column(String(10))
    linked_contact_id = Column(Integer, ForeignKey('contacts.id'))
    linked_funder_id  = Column(Integer, ForeignKey('funders.id'))
    category          = Column(String(30))
    source            = Column(String(20))       # gmail, slack, manual
    source_context    = Column(Text)
    ai_summary        = Column(Text)
    status            = Column(String(20), default='pending')  # pending, accepted, dismissed
    created_at        = Column(DateTime, default=datetime.utcnow)

    linked_contact = relationship('Contact', foreign_keys=[linked_contact_id])
    linked_funder  = relationship('Funder', foreign_keys=[linked_funder_id])

    def to_dict(self):
        return {
            'id':                  self.id,
            'title':               self.title,
            'description':         self.description,
            'due_date':            self.due_date.isoformat() if self.due_date else None,
            'priority':            self.priority,
            'linked_contact_id':   self.linked_contact_id,
            'linked_contact_name': self.linked_contact.name if self.linked_contact else None,
            'linked_funder_id':    self.linked_funder_id,
            'linked_funder_name':  self.linked_funder.organization if self.linked_funder else None,
            'category':            self.category,
            'source':              self.source,
            'source_context':      self.source_context,
            'ai_summary':          self.ai_summary,
            'status':              self.status,
            'created_at':          self.created_at.isoformat() if self.created_at else None,
        }
