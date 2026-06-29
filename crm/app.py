import os
import json
from datetime import date, datetime, timedelta
from flask import Flask, jsonify, request, render_template
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import generate_password_hash, check_password_hash

from models import init_db, get_session, Contact, Funder, Task, DCOrg, Opportunity, InboxRecommendation

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-me')

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
    return render_template('index.html')


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
        c = Contact(
            name=data['name'],
            organization=data.get('organization'),
            title=data.get('title'),
            email=data.get('email'),
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
        for f in ['title', 'description', 'priority', 'status', 'linked_contact_id', 'linked_funder_id']:
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
            'pending_inbox': session.query(InboxRecommendation).filter_by(status='pending').count(),
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


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, port=port)
