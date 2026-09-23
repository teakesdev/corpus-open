"""Custodian notices (RFC 0006). Local preparation and evidence recording; no transport.

Letter bodies use fixed request text plus explicitly selected assertion rows.
Only DOCUMENTED FACT and UNKNOWN enter the manifest. Exhibits are opaque source
bytes: their hash proves custody, not the truth or suitability of their contents.
"""
import hashlib
import json
import os
import shutil
import tempfile
from datetime import date

from . import ingest, store

KINDS = ('hold', 'mpra', 'foia', 'touhy', 'letter', 'other')
STATUSES = ('draft', 'signed', 'mailed', 'receipt-hashed', 'answered', 'refused')
OUTBOUND_LABELS = {'DOCUMENTED FACT', 'UNKNOWN'}
TRANSITIONS = {
    'draft': {'signed'},
    'signed': {'draft', 'mailed'},
    'mailed': {'receipt-hashed', 'answered', 'refused'},
    'receipt-hashed': {'answered', 'refused'},
    'answered': set(),
    'refused': set(),
}
REQUESTS = {
    'hold': ('Preservation notice',
             'Please preserve the records and electronically stored information '
             'identified in Exhibit A and confirm receipt of this request.'),
    'mpra': ('MPRA records request',
             'Please provide the records identified in Exhibit A and confirm receipt '
             'of this request. Please identify any unavailable or withheld records '
             'and the reason for withholding them.'),
    'foia': ('FOIA records request',
             'Please provide the records identified in Exhibit A and confirm receipt '
             'of this request. Please identify any unavailable or withheld records '
             'and the reason for withholding them.'),
    'touhy': ('Touhy request',
              'Please review the requested records or testimony identified in Exhibit A '
              'and advise what procedures or additional information are required.'),
    'letter': ('Custodian correspondence',
               'Please review Exhibit A and confirm receipt of this correspondence.'),
    'other': ('Custodian notice',
              'Please review Exhibit A and confirm receipt of this notice.'),
}
TAXONOMY = (
    'Custody verified ≠ assertion DOCUMENTED FACT.\n'
    '(A) Confirmed = DOCUMENTED FACT (needs a hashed source).\n'
    '(B) Lead = INFERENCE or UNKNOWN + a next ask; never a packet headline.\n'
    '(C) Hypothesis = HYPOTHESIS; omitted from outbound correspondence.'
)


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _required(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} is required')
    return value.strip()


def _notice(conn, notice_id):
    row = conn.execute('SELECT * FROM correspondence_notices WHERE id=?', (notice_id,)).fetchone()
    if row is None:
        raise ValueError(f'unknown notice: {notice_id}')
    return row


def _custodian(conn, party_id):
    row = conn.execute('SELECT * FROM parties WHERE id=?', (party_id,)).fetchone()
    if row is None or row['role'] not in ('agency', 'other') or row['capacity'] != 'custodian':
        raise ValueError('addressee must be an existing party with role=agency|other and capacity=custodian')
    return {k: row[k] for k in ('id', 'name', 'role', 'capacity')}


def _document(conn, matter_dir, ref, expected_sha=None):
    """Validate custody before using a source. Relative references never use cwd."""
    row = conn.execute('SELECT * FROM documents WHERE id=? OR rel_path=? OR sha256=?',
                       (ref, ref, ref)).fetchone()
    if row is None:
        raise ValueError(f'unregistered document: {ref}; ingest the source first')
    path = ingest.resolve_document_path(matter_dir, row['rel_path'])
    if path is None or not ingest.is_within_matter(os.path.realpath(path), os.path.realpath(matter_dir)):
        raise ValueError(f'ESCAPED document: {row["rel_path"]}')
    real_rel = os.path.relpath(os.path.realpath(path), os.path.realpath(matter_dir))
    if '.matter' in real_rel.split(os.sep):
        raise ValueError('derived .matter files cannot be used as source evidence')
    if not os.path.isfile(path):
        raise ValueError(f'MISSING document: {row["rel_path"]}')
    digest = ingest.sha256_file(path)
    if digest != row['sha256'] or (expected_sha and digest != expected_sha):
        raise ValueError(f'CHANGED document: {row["rel_path"]}')
    return {'document_id': row['id'], 'rel_path': row['rel_path'], 'sha256': digest}, path


def _history(conn, notice_id, before, after, issuer, evidence=None, approval_id=None):
    evidence = evidence or {}
    conn.execute(
        'INSERT INTO correspondence_history VALUES (?,?,?,?,?,?,?,?,?)',
        (store.new_id('ch_'), notice_id, before, after, evidence.get('document_id'),
         evidence.get('sha256'), approval_id, issuer, store.now_iso())
    )


def create_notice(conn, matter_dir, *, kind, addressee, address, exhibit,
                  assertion_ids=(), confirm_due=None):
    if kind not in KINDS:
        raise ValueError(f'kind must be one of {KINDS}')
    address = _required(address, 'address')
    if confirm_due is not None:
        try:
            if date.fromisoformat(confirm_due).isoformat() != confirm_due:
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError('confirm_due must be a date in YYYY-MM-DD format') from None
    ids = list(dict.fromkeys(assertion_ids))
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        party = _custodian(conn, addressee)
        doc, _ = _document(conn, matter_dir, exhibit)
        for aid in ids:
            if not conn.execute('SELECT id FROM assertions WHERE id=?', (aid,)).fetchone():
                raise ValueError(f'unknown assertion: {aid}')
        nid = store.new_id('cn_')
        deadline_id = store.new_id('d_') if confirm_due else None
        now = store.now_iso()
        if deadline_id:
            conn.execute(
                'INSERT INTO deadlines (id,label,due_date,rule_source,confidence,verification_status) '
                'VALUES (?,?,?,?,?,?)',
                (deadline_id, f'{kind} confirmation — {party["name"]}', confirm_due,
                 f'Operator-requested confirmation for notice {nid}; not a verified legal deadline',
                 'working-estimate', 'unverified'))
        conn.execute(
            'INSERT INTO correspondence_notices '
            '(id,kind,addressee,address,exhibit_document_id,exhibit_sha256,confirm_due,deadline_id,created_at,updated_at) '
            'VALUES (?,?,?,?,?,?,?,?,?,?)',
            (nid, kind, addressee, address, doc['document_id'], doc['sha256'], confirm_due, deadline_id, now, now))
        conn.executemany('INSERT INTO correspondence_assertions VALUES (?,?,?)',
                         [(nid, aid, i) for i, aid in enumerate(ids)])
        _history(conn, nid, None, 'draft', 'preparation')
    return nid


def build_manifest(conn, matter_dir, notice_id):
    """Canonical exact addressee/packet/exhibit snapshot, always filtered afresh."""
    notice = _notice(conn, notice_id)
    party = _custodian(conn, notice['addressee'])
    exhibit, _ = _document(conn, matter_dir, notice['exhibit_document_id'], notice['exhibit_sha256'])
    exhibit['filename'] = 'Exhibit-A' + os.path.splitext(exhibit['rel_path'])[1]
    assertions = []
    rows = conn.execute(
        'SELECT a.* FROM correspondence_assertions s LEFT JOIN assertions a ON a.id=s.assertion_id '
        'WHERE s.notice_id=? ORDER BY s.position', (notice_id,)).fetchall()
    for row in rows:
        if row['id'] is None:
            raise ValueError('selected assertion is missing')
        if row['status'] not in OUTBOUND_LABELS:
            continue
        sources = []
        for src in conn.execute(
                'SELECT document_id,locator FROM assertion_sources WHERE assertion_id=? '
                'ORDER BY document_id,locator', (row['id'],)):
            doc, _ = _document(conn, matter_dir, src['document_id'])
            sources.append(dict(doc, locator=src['locator']))
        assertions.append({'id': row['id'], 'text': row['text'], 'status': row['status'], 'sources': sources})
    title, request = REQUESTS[notice['kind']]
    lines = [f'# {title}', '', 'Transport: nosend. Prepared for human review; software does not send.', '',
             TAXONOMY, '', 'HYPOTHESIS rows omitted. Only DOCUMENTED FACT and UNKNOWN assertions included.', '',
             f'To: {party["name"]}', notice['address'], '', request]
    if notice['confirm_due']:
        lines += ['', f'Please confirm by {notice["confirm_due"]} (requested date; working estimate).']
    if assertions:
        lines += ['', '## Selected assertions', '']
    for a in assertions:
        flag = '' if a['sources'] else ' **[UNSUPPORTED — no source attached]**'
        lines.append(f'- [{a["status"]}] {a["text"]}{flag}')
        for src in a['sources']:
            locator = f' @ {src["locator"]}' if src['locator'] else ''
            lines.append(f'  - Source: {src["rel_path"]}{locator}; SHA-256: {src["sha256"]}')
    lines += ['', '## Exhibit A', '', f'File: {exhibit["filename"]}',
              f'Source: {exhibit["rel_path"]}', f'SHA-256: {exhibit["sha256"]}', '',
              'Exhibit contents require human review; hashing establishes custody, not truth.', '']
    payload = {'version': 1, 'notice_id': notice_id, 'kind': notice['kind'], 'transport': 'nosend',
               'addressee': dict(party, address=notice['address']), 'confirm_due': notice['confirm_due'],
               'assertions': assertions, 'exhibit': exhibit, 'packet': '\n'.join(lines)}
    return {'sha256': _sha(_json(payload).encode('utf-8')), 'payload': payload}


def _approved_manifest(conn, matter_dir, notice_id):
    manifest = build_manifest(conn, matter_dir, notice_id)
    notice = _notice(conn, notice_id)
    if notice['approval_id']:
        approval = conn.execute('SELECT * FROM correspondence_approvals WHERE id=? AND notice_id=?',
                                (notice['approval_id'], notice_id)).fetchone()
        if approval is None or approval['sha256'] != manifest['sha256']:
            raise ValueError('approved packet changed; return an unmailed notice to draft and review again')
    return manifest


def render_packet(conn, matter_dir, notice_id):
    return _approved_manifest(conn, matter_dir, notice_id)['payload']['packet']


def export_packet(conn, matter_dir, notice_id):
    """Write one immutable review bundle: packet.md, Exhibit A, approval.json."""
    manifest = _approved_manifest(conn, matter_dir, notice_id)
    base = os.path.join(os.path.abspath(matter_dir), '.matter', 'out', 'correspondence')
    if not ingest.is_within_matter(os.path.realpath(base), os.path.realpath(matter_dir)):
        raise ValueError('output path escapes matter')
    os.makedirs(base, exist_ok=True)
    target = os.path.join(base, manifest['sha256'])
    if os.path.lexists(target):
        raise ValueError(f'packet already exists; refusing to overwrite: {target}')
    staging = tempfile.mkdtemp(prefix='.packet-', dir=base)
    try:
        exhibit = manifest['payload']['exhibit']
        _, path = _document(conn, matter_dir, exhibit['document_id'], exhibit['sha256'])
        copied = os.path.join(staging, exhibit['filename'])
        shutil.copyfile(path, copied)
        if ingest.sha256_file(copied) != exhibit['sha256']:
            raise ValueError('CHANGED exhibit during export')
        with open(os.path.join(staging, 'packet.md'), 'w', encoding='utf-8') as f:
            f.write(manifest['payload']['packet'])
        with open(os.path.join(staging, 'approval.json'), 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
            f.write('\n')
        os.rename(staging, target)
    finally:
        if os.path.isdir(staging):
            shutil.rmtree(staging)
    return {'path': target, 'sha256': manifest['sha256'], 'transport': 'nosend'}


def transition(conn, matter_dir, notice_id, status, *, issuer, expected_sha=None, receipt=None):
    """Record an operator's action, never perform it. Receipt files must be ingested.

    issuer is an explicit human attestation, not an authentication mechanism.
    MCP exposes no lifecycle writes. Approval history and receipt hashes survive.
    """
    issuer = _required(issuer, 'human issuer')
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        notice = _notice(conn, notice_id)
        before = notice['status']
        if status not in TRANSITIONS[before]:
            raise ValueError(f'invalid notice transition: {before} → {status}')
        approval_id = notice['approval_id']
        evidence = None
        if status in ('signed', 'mailed'):
            manifest = _approved_manifest(conn, matter_dir, notice_id)
            if not expected_sha or expected_sha != manifest['sha256']:
                raise ValueError('manifest sha mismatch; inspect the exact packet and supply --sha')
            if status == 'signed':
                approval_id = store.new_id('ca_')
                conn.execute('INSERT INTO correspondence_approvals VALUES (?,?,?,?,?,?)',
                             (approval_id, notice_id, manifest['sha256'], _json(manifest), issuer, store.now_iso()))
        if status in ('mailed', 'receipt-hashed', 'answered', 'refused'):
            if not receipt:
                raise ValueError(f'{status} requires a human-supplied receipt/evidence file; ingest it first')
            evidence, _ = _document(conn, matter_dir, receipt)
            # An earlier mailing receipt must still exist and match before later states.
            if notice['receipt_document_id']:
                _document(conn, matter_dir, notice['receipt_document_id'], notice['receipt_sha256'])
        elif receipt:
            raise ValueError('receipt evidence applies only to mailed or later states')
        if status == 'draft':
            approval_id = None
        receipt_id = evidence['document_id'] if status == 'mailed' else notice['receipt_document_id']
        receipt_sha = evidence['sha256'] if status == 'mailed' else notice['receipt_sha256']
        conn.execute('UPDATE correspondence_notices SET status=?,approval_id=?,updated_at=?, '
                     'receipt_document_id=?,receipt_sha256=? WHERE id=?',
                     (status, approval_id, store.now_iso(), receipt_id, receipt_sha, notice_id))
        _history(conn, notice_id, before, status, issuer, evidence, approval_id)
    return status


def ledger(conn):
    def cell(value):
        return str(value or '—').replace('|', '\\|').replace('\n', '<br>')
    lines = ['# Correspondence ledger', '', 'Transport: nosend. Humans send; statuses record supplied evidence.', '',
             '| Notice | Kind | Addressee | Status | Exhibit SHA-256 | Confirm due | Mailing receipt SHA-256 |',
             '|---|---|---|---|---|---|---|']
    for row in conn.execute('SELECT n.*,p.name FROM correspondence_notices n LEFT JOIN parties p ON p.id=n.addressee '
                            'ORDER BY n.created_at,n.id'):
        lines.append('| ' + ' | '.join(cell(row[k]) for k in
                     ('id', 'kind', 'name', 'status', 'exhibit_sha256', 'confirm_due', 'receipt_sha256')) + ' |')
    history = conn.execute(
        'SELECT * FROM correspondence_history WHERE evidence_sha256 IS NOT NULL ORDER BY created_at,rowid').fetchall()
    if history:
        lines += ['', '## Receipt / response evidence', '']
        for row in history:
            lines.append(f'- {row["notice_id"]}: {row["to_status"]} · {row["evidence_document_id"]} · '
                         f'SHA-256 {row["evidence_sha256"]} · recorded by {cell(row["recorded_by"])} '
                         f'at {row["created_at"]}')
    return '\n'.join(lines)
