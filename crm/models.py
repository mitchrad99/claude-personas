import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, Date, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_db_default = f"sqlite:///{os.path.join(BASE_DIR, 'aao_crm.db')}"
DATABASE_URL = os.environ.get('DATABASE_URL', _db_default)

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
    category = Column(String(20), default='other')    # advocacy, funder, government, media, peer_org, dc_network, other
    last_contact_date = Column(Date)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    tasks = relationship('Task', foreign_keys='Task.linked_contact_id', back_populates='contact')
    funders = relationship('Funder', foreign_keys='Funder.program_officer_contact_id', back_populates='program_officer')
    dc_orgs = relationship('DCOrg', foreign_keys='DCOrg.key_contact_id', back_populates='key_contact')

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
