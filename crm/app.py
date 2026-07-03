import os
import json
from datetime import date, datetime, timedelta
from flask import Flask, jsonify, request, render_template
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import or_

from models import init_db, get_session, Contact, Funder, Task, DCOrg, Opportunity, InboxRecommendation, Interaction, ContactNote, ContactRelationship, TaskRecommendation

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-me')

_TEST_MODE = os.environ.get('TEST_MODE', '').lower() in ('1', 'true', 'yes')

auth = HTTPBasicAuth()

_CRM_USERNAME = os.environ.get('CRM_USERNAME', 'admin')
_CRM_PASSWORD = os.environ.get('CRM_PASSWORD')
if not _CRM_PASSWORD:
    raise RuntimeError("CRM_PASSWORD environment variable is not set.")
_USERS = {_CRM_USERNAME: generate_password_hash(_CRM_PASSWORD)}

@auth.verify_password
def verify_password(username, password):
    if username in _USERS and check_password_hash(_USERS[username], password):
        return username

init_db()

@app.before_request
def require_auth():
    return auth.login_required(lambda: None)()

# Lazy-load ChatEngine so Flask starts even if ANTHROPIC_API_KEY isn't set yet
_chat_engine = None

def get_chat_engine():
    global _chat_engine
    if _chat_engine is None:
        from chat import ChatEngine
        _chat_engine = ChatEngine()
    return _chat_engine


# ── helpers ──────────────────────────────────────────────────────────────────

def bad(msg, code=400):
    return jsonify({'error': msg}), code

def _parse_date(s):
    return date.fromisoformat(s) if s else None


# ── root ─────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html', test_mode=_TEST_MODE)


# ── contacts ─────────────────────────────────────────────────────────────────

@app.route('/api/contacts', methods=['GET'])
def list_contacts():
    session = get_session()
    try:
        q = session.query(Contact)
        if request.args.get('warmth'):
            q = q.filter(Contact.warmth == request.args['warmth'])
        if request.args.get('category'):
            q = q.filter(Contact.category == request.args['category'])
        if request.args.get('stale_days'):
            cutoff = date.today() - timedelta(days=int(request.args['stale_days']))
            q = q.filter((Contact.last_contact_date <= cutoff) | (Contact.last_contact_date == None))
        contacts = q.order_by(Contact.name).all()
        result = []
        for c in contacts:
            d = c.to_dict()
            next_task = (session.query(Task)
                         .filter(Task.linked_contact_id == c.id, Task.status == 'pending')
                         .order_by(Task.due_date)
                         .first())
            d['next_task'] = next_task.title if next_task else None
            d['next_task_due'] = next_task.due_date.isoformat() if next_task and next_task.due_date else None
            result.append(d)
        return jsonify(result)
    finally:
        session.close()


@app.route('/api/contacts', methods=['POST'])
def create_contact():
    session = get_session()
    try:
        data = request.json or {}
        if not data.get('name'):
            return bad('name is required')

        email = data.get('email')

        # 1. Exact email match
        if email:
            existing = session.query(Contact).filter(Contact.email.ilike(email)).first()
            if existing:
                return jsonify({
                    'duplicate': True,
                    'existing_contact': existing.to_dict(),
                    'message': 'A contact with this email already exists',
                }), 409

        # 2. Fuzzy name match (only when no email provided)
        if not email:
            tokens = [t for t in data['name'].split() if len(t) > 1]
            if tokens:
                candidates = (session.query(Contact)
                              .filter(or_(*[Contact.name.ilike(f'%{t}%') for t in tokens]))
                              .limit(5).all())
                if candidates:
                    return jsonify({
                        'possible_duplicates': [c.to_dict() for c in candidates],
                        'message': 'Similar contacts found — confirm before creating',
                    })

        c = Contact(
            name=data['name'],
            organization=data.get('organization'),
            title=data.get('title'),
            email=email,
            phone=data.get('phone'),
            warmth=data.get('warmth', 'cold'),
            category=data.get('category', 'other'),
            notes=data.get('notes'),
            last_contact_date=_parse_date(data.get('last_contact_date')),
        )
        session.add(c)
        session.commit()
        return jsonify(c.to_dict()), 201
    finally:
        session.close()


@app.route('/api/contacts/<int:cid>', methods=['PUT'])
def update_contact(cid):
    session = get_session()
    try:
        c = session.query(Contact).filter_by(id=cid).first()
        if not c:
            return bad('not found', 404)
        data = request.json or {}
        for f in ['name', 'organization', 'title', 'email', 'phone', 'warmth', 'category', 'notes']:
            if f in data:
                setattr(c, f, data[f])
        if 'last_contact_date' in data:
            c.last_contact_date = _parse_date(data['last_contact_date'])
        c.updated_at = datetime.utcnow()
        session.commit()
        return jsonify(c.to_dict())
    finally:
        session.close()


@app.route('/api/contacts/<int:cid>', methods=['DELETE'])
def delete_contact(cid):
    session = get_session()
    try:
        c = session.query(Contact).filter_by(id=cid).first()
        if not c:
            return bad('not found', 404)
        session.delete(c)
        session.commit()
        return jsonify({'ok': True})
    finally:
        session.close()


@app.route('/api/contacts/<int:cid>/interactions', methods=['GET'])
def contact_interactions(cid):
    session = get_session()
    try:
        if not session.query(Contact).filter_by(id=cid).first():
            return bad('not found', 404)
        items = (session.query(Interaction)
                 .filter(Interaction.contact_id == cid)
                 .order_by(Interaction.date.desc())
                 .all())
        return jsonify([i.to_dict() for i in items])
    finally:
        session.close()


@app.route('/api/contacts/<int:cid>/notes', methods=['GET'])
def contact_notes_for_contact(cid):
    session = get_session()
    try:
        if not session.query(Contact).filter_by(id=cid).first():
            return bad('not found', 404)
        notes = (session.query(ContactNote)
                 .filter(ContactNote.contact_id == cid)
                 .order_by(ContactNote.created_at.desc())
                 .all())
        return jsonify([n.to_dict() for n in notes])
    finally:
        session.close()


@app.route('/api/contacts/<int:cid>/relationships', methods=['GET'])
def contact_relationships_for_contact(cid):
    session = get_session()
    try:
        if not session.query(Contact).filter_by(id=cid).first():
            return bad('not found', 404)
        rels = (session.query(ContactRelationship)
                .filter(
                    (ContactRelationship.from_contact_id == cid) |
                    (ContactRelationship.to_contact_id == cid)
                )
                .order_by(ContactRelationship.created_at.desc())
                .all())
        result = []
        for r in rels:
            d = r.to_dict()
            # Frontend needs to_contact's warmth to apply pending-intro display rule
            d['to_contact_warmth'] = r.to_contact.warmth if r.to_contact else None
            result.append(d)
        return jsonify(result)
    finally:
        session.close()


# ── funders ───────────────────────────────────────────────────────────────────

@app.route('/api/funders', methods=['GET'])
def list_funders():
    session = get_session()
    try:
        q = session.query(Funder)
        if request.args.get('status'):
            q = q.filter(Funder.status == request.args['status'])
        return jsonify([f.to_dict() for f in q.order_by(Funder.organization).all()])
    finally:
        session.close()


@app.route('/api/funders', methods=['POST'])
def create_funder():
    session = get_session()
    try:
        data = request.json or {}
        if not data.get('organization'):
            return bad('organization is required')
        f = Funder(
            organization=data['organization'],
            type=data.get('type'),
            focus_areas=data.get('focus_areas'),
            program_officer_name=data.get('program_officer_name'),
            program_officer_contact_id=data.get('program_officer_contact_id'),
            ask_amount=data.get('ask_amount'),
            status=data.get('status', 'research'),
            notes=data.get('notes'),
            deadline=_parse_date(data.get('deadline')),
        )
        session.add(f)
        session.commit()
        return jsonify(f.to_dict()), 201
    finally:
        session.close()


@app.route('/api/funders/<int:fid>', methods=['PUT'])
def update_funder(fid):
    session = get_session()
    try:
        f = session.query(Funder).filter_by(id=fid).first()
        if not f:
            return bad('not found', 404)
        data = request.json or {}
        for field in ['organization', 'type', 'focus_areas', 'program_officer_name',
                       'program_officer_contact_id', 'ask_amount', 'status', 'notes']:
            if field in data:
                setattr(f, field, data[field])
        if 'deadline' in data:
            f.deadline = _parse_date(data['deadline'])
        f.updated_at = datetime.utcnow()
        session.commit()
        return jsonify(f.to_dict())
    finally:
        session.close()


# ── tasks ─────────────────────────────────────────────────────────────────────

@app.route('/api/tasks', methods=['GET'])
def list_tasks():
    session = get_session()
    try:
        q = session.query(Task)
        if request.args.get('status'):
            q = q.filter(Task.status == request.args['status'])
        if request.args.get('due_before'):
            q = q.filter(Task.due_date <= _parse_date(request.args['due_before']))
        return jsonify([t.to_dict() for t in q.order_by(Task.due_date).all()])
    finally:
        session.close()


@app.route('/api/tasks', methods=['POST'])
def create_task():
    session = get_session()
    try:
        data = request.json or {}
        if not data.get('title'):
            return bad('title is required')
        t = Task(
            title=data['title'],
            description=data.get('description'),
            priority=data.get('priority', 'medium'),
            status=data.get('status', 'pending'),
            category=data.get('category'),
            linked_contact_id=data.get('linked_contact_id'),
            linked_funder_id=data.get('linked_funder_id'),
            due_date=_parse_date(data.get('due_date')),
        )
        session.add(t)
        session.commit()
        return jsonify(t.to_dict()), 201
    finally:
        session.close()


@app.route('/api/tasks/<int:tid>', methods=['PUT'])
def update_task(tid):
    session = get_session()
    try:
        t = session.query(Task).filter_by(id=tid).first()
        if not t:
            return bad('not found', 404)
        data = request.json or {}
        for f in ['title', 'description', 'priority', 'status', 'category', 'linked_contact_id', 'linked_funder_id']:
            if f in data:
                setattr(t, f, data[f])
        if 'due_date' in data:
            t.due_date = _parse_date(data['due_date'])
        t.updated_at = datetime.utcnow()
        session.commit()
        return jsonify(t.to_dict())
    finally:
        session.close()


# ── dc_orgs ───────────────────────────────────────────────────────────────────

@app.route('/api/dc_orgs', methods=['GET'])
def list_dc_orgs():
    session = get_session()
    try:
        orgs = session.query(DCOrg).order_by(DCOrg.name).all()
        return jsonify([o.to_dict() for o in orgs])
    finally:
        session.close()


@app.route('/api/dc_orgs', methods=['POST'])
def create_dc_org():
    session = get_session()
    try:
        data = request.json or {}
        if not data.get('name'):
            return bad('name is required')
        o = DCOrg(
            name=data['name'],
            type=data.get('type'),
            priority=data.get('priority', 'medium'),
            key_contact_id=data.get('key_contact_id'),
            notes=data.get('notes'),
        )
        session.add(o)
        session.commit()
        return jsonify(o.to_dict()), 201
    finally:
        session.close()


# ── opportunities ─────────────────────────────────────────────────────────────

@app.route('/api/opportunities', methods=['GET'])
def list_opportunities():
    session = get_session()
    try:
        opps = session.query(Opportunity).order_by(Opportunity.deadline).all()
        return jsonify([o.to_dict() for o in opps])
    finally:
        session.close()


@app.route('/api/opportunities', methods=['POST'])
def create_opportunity():
    session = get_session()
    try:
        data = request.json or {}
        if not data.get('title'):
            return bad('title is required')
        o = Opportunity(
            title=data['title'],
            organization=data.get('organization'),
            type=data.get('type'),
            status=data.get('status', 'identified'),
            salary_range=data.get('salary_range'),
            notes=data.get('notes'),
            deadline=_parse_date(data.get('deadline')),
        )
        session.add(o)
        session.commit()
        return jsonify(o.to_dict()), 201
    finally:
        session.close()


# ── summary ───────────────────────────────────────────────────────────────────

@app.route('/api/summary', methods=['GET'])
def get_summary():
    session = get_session()
    try:
        today = date.today()
        week_end = today + timedelta(days=7)
        stale_cutoff = today - timedelta(days=30)

        return jsonify({
            'tasks_due_this_week': session.query(Task).filter(
                Task.status == 'pending', Task.due_date >= today, Task.due_date <= week_end
            ).count(),
            'overdue_tasks': session.query(Task).filter(
                Task.status == 'pending', Task.due_date < today
            ).count(),
            'stale_contacts': session.query(Contact).filter(
                (Contact.last_contact_date <= stale_cutoff) | (Contact.last_contact_date == None)
            ).count(),
            'hot_contacts': session.query(Contact).filter(Contact.warmth == 'hot').count(),
            'hot_funders': session.query(Funder).filter(
                Funder.status.in_(['outreach', 'meeting_scheduled', 'proposal_submitted'])
            ).count(),
            'total_contacts': session.query(Contact).count(),
            'total_funders': session.query(Funder).count(),
            'pending_tasks': session.query(Task).filter(Task.status == 'pending').count(),
            'pending_inbox': (
                session.query(InboxRecommendation).filter_by(status='pending').count() +
                session.query(TaskRecommendation).filter_by(status='pending').count()
            ),
        })
    finally:
        session.close()


# ── chat ──────────────────────────────────────────────────────────────────────

@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.json or {}
    message = (data.get('message') or '').strip()
    if not message:
        return bad('message is required')
    response, changes = get_chat_engine().chat(message)
    return jsonify({'response': response, 'changes': changes})


@app.route('/api/chat/reset', methods=['POST'])
def chat_reset():
    get_chat_engine().reset()
    return jsonify({'ok': True})


# ── inbox ─────────────────────────────────────────────────────────────────────

@app.route('/api/inbox', methods=['GET'])
def list_inbox():
    session = get_session()
    try:
        recs = (session.query(InboxRecommendation)
                .filter_by(status='pending')
                .order_by(InboxRecommendation.email_date.desc())
                .all())
        return jsonify([r.to_dict() for r in recs])
    finally:
        session.close()


@app.route('/api/inbox/<int:rid>/accept', methods=['POST'])
def accept_inbox(rid):
    session = get_session()
    try:
        rec = session.query(InboxRecommendation).filter_by(id=rid, status='pending').first()
        if not rec:
            return bad('not found or already processed', 404)

        data = request.json or {}

        if rec.recommendation_type == 'new_contact':
            obj = Contact(
                name         = data.get('name') or rec.sender_name or 'Unknown',
                organization = data.get('organization'),
                title        = data.get('title'),
                email        = data.get('email') or rec.sender_email,
                phone        = data.get('phone'),
                warmth       = data.get('warmth', 'cold'),
                category     = data.get('category', 'other'),
                notes        = data.get('notes'),
            )
            session.add(obj)
        elif rec.recommendation_type == 'new_task':
            obj = Task(
                title       = data.get('title') or f'Follow up with {rec.sender_name}',
                description = data.get('description'),
                priority    = data.get('priority', 'medium'),
                due_date    = _parse_date(data.get('due_date')),
            )
            session.add(obj)

        rec.status = 'accepted'
        session.commit()
        return jsonify({'ok': True})
    finally:
        session.close()


@app.route('/api/inbox/<int:rid>/dismiss', methods=['POST'])
def dismiss_inbox(rid):
    session = get_session()
    try:
        rec = session.query(InboxRecommendation).filter_by(id=rid, status='pending').first()
        if not rec:
            return bad('not found', 404)
        rec.status = 'dismissed'
        session.commit()
        return jsonify({'ok': True})
    finally:
        session.close()


# ── interactions ──────────────────────────────────────────────────────────────

@app.route('/api/interactions', methods=['GET'])
def list_interactions():
    session = get_session()
    try:
        q = session.query(Interaction)
        if request.args.get('contact_id'):
            q = q.filter(Interaction.contact_id == int(request.args['contact_id']))
        if request.args.get('type'):
            q = q.filter(Interaction.type == request.args['type'])
        if request.args.get('follow_up_needed'):
            q = q.filter(Interaction.follow_up_needed == True)
        return jsonify([i.to_dict() for i in q.order_by(Interaction.date.desc()).all()])
    finally:
        session.close()


@app.route('/api/interactions', methods=['POST'])
def create_interaction():
    session = get_session()
    try:
        data = request.json or {}
        if not data.get('contact_id'):
            return bad('contact_id is required')
        if not data.get('date'):
            return bad('date is required')
        i = Interaction(
            contact_id=data['contact_id'],
            date=_parse_date(data['date']),
            type=data.get('type'),
            location=data.get('location'),
            notes=data.get('notes'),
            follow_up_needed=bool(data.get('follow_up_needed', False)),
        )
        session.add(i)
        session.commit()
        return jsonify(i.to_dict()), 201
    finally:
        session.close()


@app.route('/api/interactions/<int:iid>', methods=['PUT'])
def update_interaction(iid):
    session = get_session()
    try:
        i = session.query(Interaction).filter_by(id=iid).first()
        if not i:
            return bad('not found', 404)
        data = request.json or {}
        for f in ['type', 'location', 'notes']:
            if f in data:
                setattr(i, f, data[f])
        if 'date' in data:
            i.date = _parse_date(data['date'])
        if 'follow_up_needed' in data:
            i.follow_up_needed = bool(data['follow_up_needed'])
        i.updated_at = datetime.utcnow()
        session.commit()
        return jsonify(i.to_dict())
    finally:
        session.close()


# ── contact_notes ─────────────────────────────────────────────────────────────

@app.route('/api/contact_notes', methods=['GET'])
def list_contact_notes():
    session = get_session()
    try:
        if not request.args.get('contact_id'):
            return bad('contact_id is required')
        notes = (session.query(ContactNote)
                 .filter(ContactNote.contact_id == int(request.args['contact_id']))
                 .order_by(ContactNote.created_at.desc())
                 .all())
        return jsonify([n.to_dict() for n in notes])
    finally:
        session.close()


@app.route('/api/contact_notes', methods=['POST'])
def create_contact_note():
    session = get_session()
    try:
        data = request.json or {}
        if not data.get('contact_id'):
            return bad('contact_id is required')
        if not data.get('note'):
            return bad('note is required')
        n = ContactNote(
            contact_id=data['contact_id'],
            note=data['note'],
            source=data.get('source', 'manual'),
        )
        session.add(n)
        session.commit()
        return jsonify(n.to_dict()), 201
    finally:
        session.close()


# ── contact_relationships ─────────────────────────────────────────────────────

@app.route('/api/contact_relationships', methods=['GET'])
def list_contact_relationships():
    session = get_session()
    try:
        q = session.query(ContactRelationship)
        if request.args.get('contact_id'):
            cid = int(request.args['contact_id'])
            q = q.filter(
                (ContactRelationship.from_contact_id == cid) |
                (ContactRelationship.to_contact_id == cid)
            )
        if request.args.get('type'):
            q = q.filter(ContactRelationship.type == request.args['type'])
        return jsonify([r.to_dict() for r in q.order_by(ContactRelationship.created_at.desc()).all()])
    finally:
        session.close()


@app.route('/api/contact_relationships', methods=['POST'])
def create_contact_relationship():
    session = get_session()
    try:
        data = request.json or {}
        if not data.get('from_contact_id'):
            return bad('from_contact_id is required')
        if not data.get('to_contact_id'):
            return bad('to_contact_id is required')
        if data.get('from_contact_id') == data.get('to_contact_id'):
            return bad('from_contact_id and to_contact_id must be different')
        r = ContactRelationship(
            from_contact_id=data['from_contact_id'],
            to_contact_id=data['to_contact_id'],
            type=data.get('type'),
            status=data.get('status', 'completed'),
            notes=data.get('notes'),
        )
        session.add(r)
        session.commit()
        return jsonify(r.to_dict()), 201
    finally:
        session.close()


@app.route('/api/contact_relationships/<int:rid>', methods=['PUT'])
def update_contact_relationship(rid):
    session = get_session()
    try:
        r = session.query(ContactRelationship).filter_by(id=rid).first()
        if not r:
            return bad('not found', 404)
        data = request.json or {}
        for f in ['type', 'status', 'notes']:
            if f in data:
                setattr(r, f, data[f])
        r.updated_at = datetime.utcnow()
        session.commit()
        return jsonify(r.to_dict())
    finally:
        session.close()


# ── task_recommendations ──────────────────────────────────────────────────────

@app.route('/api/task_recommendations', methods=['GET'])
def list_task_recommendations():
    session = get_session()
    try:
        recs = (session.query(TaskRecommendation)
                .filter_by(status='pending')
                .order_by(TaskRecommendation.created_at.desc())
                .all())
        return jsonify([r.to_dict() for r in recs])
    finally:
        session.close()


@app.route('/api/task_recommendations/<int:rid>/accept', methods=['POST'])
def accept_task_recommendation(rid):
    session = get_session()
    try:
        rec = session.query(TaskRecommendation).filter_by(id=rid, status='pending').first()
        if not rec:
            return bad('not found or already processed', 404)
        data = request.json or {}
        t = Task(
            title             = data.get('title') or rec.title,
            description       = data.get('description', rec.description),
            priority          = data.get('priority') or rec.priority or 'medium',
            due_date          = _parse_date(data.get('due_date')),
            category          = data.get('category') or rec.category,
            linked_contact_id = rec.linked_contact_id,
            linked_funder_id  = rec.linked_funder_id,
        )
        session.add(t)
        rec.status = 'accepted'
        session.commit()
        return jsonify({'ok': True, 'task': t.to_dict()})
    finally:
        session.close()


@app.route('/api/task_recommendations/<int:rid>/dismiss', methods=['POST'])
def dismiss_task_recommendation(rid):
    session = get_session()
    try:
        rec = session.query(TaskRecommendation).filter_by(id=rid, status='pending').first()
        if not rec:
            return bad('not found', 404)
        rec.status = 'dismissed'
        session.commit()
        return jsonify({'ok': True})
    finally:
        session.close()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, port=port)
