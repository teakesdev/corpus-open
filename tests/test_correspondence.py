"""RFC 0006: synthetic custodian notices, immutable packets, evidence-gated state."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from matterkit import correspondence as co, ingest, packet, store

KIT = Path(__file__).resolve().parents[1]


class CorrespondenceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='correspondence-synthetic-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.conn = store.connect(str(self.root))
        self.addCleanup(self.conn.close)
        self.conn.execute("INSERT INTO parties VALUES ('p_custodian','Example Records Office',"
                          "'agency','custodian',NULL)")
        self.conn.commit()
        self.exhibit = self.source('Evidence/schedule.txt', 'SYNTHETIC: preserve the example system logs.\n')
        self.source('Evidence/record.txt', 'SYNTHETIC: the log records an inspection.\n')
        self.assertions = []
        for i, status in enumerate(sorted(store.VALID_STATUS)):
            aid = f'a_{i}'
            self.assertions.append(aid)
            self.conn.execute('INSERT INTO assertions VALUES (?,?,?,?,?,?)',
                              (aid, f'SYNTHETIC {status} CLAIM SENTINEL', status, None, 'test', store.now_iso()))
            if status == 'DOCUMENTED FACT':
                self.conn.execute('INSERT INTO assertion_sources VALUES (?,?,?)',
                                  (aid, self.document('Evidence/record.txt')['id'], 'page 1'))
        self.conn.commit()

    def source(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        ingest.ingest_paths(self.conn, str(self.root), [str(path)])
        return path

    def document(self, rel):
        return self.conn.execute('SELECT * FROM documents WHERE rel_path=?', (rel,)).fetchone()

    def notice(self, **overrides):
        args = dict(kind='hold', addressee='p_custodian', address='100 Example Street\nExample City',
                    exhibit='Evidence/schedule.txt', assertion_ids=self.assertions, confirm_due='2026-10-01')
        args.update(overrides)
        return co.create_notice(self.conn, str(self.root), **args)

    def row(self, nid):
        return self.conn.execute('SELECT * FROM correspondence_notices WHERE id=?', (nid,)).fetchone()

    def change(self, nid, status, **kwargs):
        return co.transition(self.conn, str(self.root), nid, status, issuer='Synthetic Human', **kwargs)

    def sign(self, nid):
        sha = co.build_manifest(self.conn, str(self.root), nid)['sha256']
        self.change(nid, 'signed', expected_sha=sha)
        return sha

    def mail(self, nid):
        sha = self.sign(nid)
        self.source('Correspondence/mailing.txt', 'SYNTHETIC mailing receipt')
        self.change(nid, 'mailed', expected_sha=sha, receipt='Correspondence/mailing.txt')

    def cli(self, *args):
        return subprocess.run([sys.executable, str(KIT / 'matter.py'), *args],
                              cwd='/tmp', capture_output=True, text=True, timeout=30)

    def test_notice_deadline_and_no_counsel_recipient_reuse(self):
        nid = self.notice()
        row = self.row(nid)
        self.assertEqual(row['status'], 'draft')
        self.assertEqual(row['transport'], 'nosend')
        deadline = self.conn.execute('SELECT * FROM deadlines WHERE id=?', (row['deadline_id'],)).fetchone()
        self.assertEqual(deadline['due_date'], '2026-10-01')
        self.assertEqual(deadline['confidence'], 'working-estimate')
        self.assertEqual(deadline['verification_status'], 'unverified')
        self.assertIn(nid, deadline['rule_source'])
        self.assertIn('draft: 1', packet.status_report(self.conn, str(self.root)))
        tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn('outreach_recipients', tables)
        self.assertEqual(self.conn.execute("SELECT role FROM parties WHERE id='p_custodian'").fetchone()[0], 'agency')

    def test_every_kind_and_optional_confirmation_date(self):
        for kind in co.KINDS:
            nid = self.notice(kind=kind, confirm_due=None)
            self.assertIsNone(self.row(nid)['deadline_id'])
            self.assertIn(co.REQUESTS[kind][0], co.render_packet(self.conn, str(self.root), nid))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM deadlines').fetchone()[0], 0)

    def test_bad_input_leaves_no_partial_notice_or_deadline(self):
        for changes in ({'kind': 'outreach'}, {'confirm_due': '2026-02-30'},
                        {'confirm_due': '20261001'}, {'address': '  '},
                        {'assertion_ids': ['not_an_assertion']}, {'exhibit': 'unknown'},
                        {'addressee': 'outreach_recipient_id'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.notice(**changes)
        for table in ('correspondence_notices', 'deadlines', 'correspondence_history'):
            self.assertEqual(self.conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)

    def test_custodian_role_and_capacity_fail_closed_at_create_and_render(self):
        for role, capacity in [('defendant', 'custodian'), ('agency', None), ('counsel', 'custodian')]:
            self.conn.execute('UPDATE parties SET role=?,capacity=?', (role, capacity))
            self.conn.commit()
            with self.assertRaisesRegex(ValueError, 'custodian'):
                self.notice()
        self.conn.execute("UPDATE parties SET role='other',capacity='custodian'")
        self.conn.commit()
        nid = self.notice()
        self.conn.execute("UPDATE parties SET role='defendant'")
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, 'custodian'):
            co.render_packet(self.conn, str(self.root), nid)

    def test_body_manifest_and_export_exclude_hypothesis_even_when_selected_and_sourced(self):
        hyp = self.conn.execute("SELECT id FROM assertions WHERE status='HYPOTHESIS'").fetchone()[0]
        self.conn.execute('INSERT INTO assertion_sources VALUES (?,?,?)',
                          (hyp, self.document('Evidence/record.txt')['id'], 'page 1'))
        self.conn.commit()
        nid = self.notice()
        manifest = co.build_manifest(self.conn, str(self.root), nid)
        serialized = json.dumps(manifest)
        for status in ('HYPOTHESIS', 'INFERENCE', 'ALLEGATION'):
            self.assertNotIn(f'SYNTHETIC {status} CLAIM SENTINEL', serialized)
        for status in ('DOCUMENTED FACT', 'UNKNOWN'):
            self.assertIn(f'SYNTHETIC {status} CLAIM SENTINEL', serialized)
        self.assertIn('UNSUPPORTED', serialized)  # the selected UNKNOWN has no source
        self.assertIn('HYPOTHESIS rows omitted.', serialized)
        self.assertEqual(len(manifest['payload']['assertions']), 2)
        self.assertNotIn('Counsel packet', serialized)
        with patch('socket.socket', side_effect=AssertionError('no network')):
            result = co.export_packet(self.conn, str(self.root), nid)
        bundle = Path(result['path'])
        self.assertEqual((bundle / 'Exhibit-A.txt').read_bytes(), self.exhibit.read_bytes())
        self.assertEqual((bundle / 'packet.md').read_text(), manifest['payload']['packet'])
        self.assertEqual(json.loads((bundle / 'approval.json').read_text()), manifest)
        self.assertEqual(self.row(nid)['status'], 'draft')
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            co.export_packet(self.conn, str(self.root), nid)
        self.assertEqual(ingest.verify_integrity(self.conn, str(self.root))[1:3], (0, 0))

    def test_empty_selection_does_not_pull_in_other_matter_assertions(self):
        text = co.render_packet(self.conn, str(self.root), self.notice(assertion_ids=[]))
        self.assertNotIn('CLAIM SENTINEL', text)

    def test_approval_sha_covers_addressee_address_exhibit_and_allowed_assertions(self):
        nid = self.notice()
        manifest = co.build_manifest(self.conn, str(self.root), nid)
        canonical = json.dumps(manifest['payload'], sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        self.assertEqual(manifest['sha256'], hashlib.sha256(canonical.encode()).hexdigest())
        self.conn.execute("UPDATE parties SET name='Changed Example Office'")
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, 'sha mismatch'):
            self.change(nid, 'signed', expected_sha=manifest['sha256'])
        self.assertEqual(self.row(nid)['status'], 'draft')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM correspondence_approvals').fetchone()[0], 0)
        self.sign(nid)
        self.conn.execute("UPDATE assertions SET status='HYPOTHESIS' WHERE status='DOCUMENTED FACT'")
        self.conn.commit()
        for fn in (co.render_packet, co.export_packet):
            with self.assertRaisesRegex(ValueError, 'approved packet changed'):
                fn(self.conn, str(self.root), nid)
        self.change(nid, 'draft')
        self.assertNotIn('DOCUMENTED FACT CLAIM SENTINEL', co.render_packet(self.conn, str(self.root), nid))
        self.sign(nid)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM correspondence_approvals').fetchone()[0], 2)

    def test_missing_changed_and_escaped_exhibits_and_sources_block_packets(self):
        nid = self.notice()
        source = self.root / 'Evidence/record.txt'
        original = source.read_bytes()
        source.write_text('changed')
        with self.assertRaisesRegex(ValueError, 'CHANGED'):
            co.render_packet(self.conn, str(self.root), nid)
        source.write_bytes(original)
        source.unlink()
        with self.assertRaisesRegex(ValueError, 'MISSING'):
            self.sign(nid)
        source.write_bytes(original)
        self.exhibit.write_text('changed schedule')
        with self.assertRaisesRegex(ValueError, 'CHANGED'):
            co.export_packet(self.conn, str(self.root), nid)
        self.conn.execute("UPDATE documents SET rel_path='../escape' WHERE rel_path='Evidence/schedule.txt'")
        self.conn.commit()
        with patch.object(ingest, 'sha256_file', side_effect=AssertionError('must not read outside matter')):
            with self.assertRaisesRegex(ValueError, 'ESCAPED'):
                co.render_packet(self.conn, str(self.root), nid)

    def test_receipt_cannot_be_derived_outbox_or_escape_via_symlink(self):
        nid = self.notice()
        sha = self.sign(nid)
        self.source('.matter/out/fake.txt', 'SYNTHETIC derived output')
        with self.assertRaisesRegex(ValueError, 'derived'):
            self.change(nid, 'mailed', expected_sha=sha, receipt='.matter/out/fake.txt')
        self.source('Correspondence/receipt.txt', 'SYNTHETIC receipt')
        receipt = self.root / 'Correspondence/receipt.txt'
        receipt.unlink()
        receipt.symlink_to(KIT / 'README.md')
        with self.assertRaisesRegex(ValueError, 'ESCAPED'):
            self.change(nid, 'mailed', expected_sha=sha, receipt='Correspondence/receipt.txt')
        self.assertEqual(self.row(nid)['status'], 'signed')

    def test_mail_requires_review_and_receipt_and_preserves_working_deadline(self):
        nid = self.notice()
        with self.assertRaisesRegex(ValueError, 'invalid notice transition'):
            self.change(nid, 'mailed')
        sha = self.sign(nid)
        for kwargs in ({}, {'expected_sha': sha}, {'expected_sha': sha, 'receipt': 'missing.txt'}):
            with self.assertRaises(ValueError):
                self.change(nid, 'mailed', **kwargs)
            self.assertEqual(self.row(nid)['status'], 'signed')
            self.assertIsNone(self.row(nid)['receipt_sha256'])
        receipt = self.source('Correspondence/receipt.txt', 'SYNTHETIC mailing receipt')
        receipt.write_text('CHANGED receipt')
        with self.assertRaisesRegex(ValueError, 'CHANGED'):
            self.change(nid, 'mailed', expected_sha=sha, receipt='Correspondence/receipt.txt')
        receipt.write_text('SYNTHETIC mailing receipt')
        self.change(nid, 'mailed', expected_sha=sha, receipt='Correspondence/receipt.txt')
        row = self.row(nid)
        self.assertEqual(row['receipt_sha256'], ingest.sha256_file(str(receipt)))
        self.assertEqual(row['transport'], 'nosend')
        self.assertEqual(self.conn.execute('SELECT confidence FROM deadlines WHERE id=?',
                                          (row['deadline_id'],)).fetchone()[0], 'working-estimate')
        self.assertIn(row['receipt_sha256'], co.ledger(self.conn))
        with self.assertRaisesRegex(ValueError, 'invalid notice transition'):
            self.change(nid, 'draft')

    def test_delivery_response_and_refusal_keep_separate_evidence_history(self):
        for terminal in ('answered', 'refused'):
            nid = self.notice()
            self.mail(nid)
            with self.assertRaisesRegex(ValueError, 'requires'):
                self.change(nid, 'receipt-hashed')
            delivery = self.source(f'Correspondence/delivery-{terminal}.txt', f'SYNTHETIC delivery {terminal}')
            self.change(nid, 'receipt-hashed', receipt=str(delivery.relative_to(self.root)))
            response = self.source(f'Correspondence/{terminal}.txt', f'SYNTHETIC {terminal} response')
            self.change(nid, terminal, receipt=str(response.relative_to(self.root)))
            rows = self.conn.execute('SELECT * FROM correspondence_history WHERE notice_id=? '
                                     'AND evidence_sha256 IS NOT NULL ORDER BY rowid', (nid,)).fetchall()
            self.assertEqual([r['to_status'] for r in rows], ['mailed', 'receipt-hashed', terminal])
            self.assertEqual(len({r['evidence_sha256'] for r in rows}), 3)
            self.assertEqual(self.row(nid)['receipt_sha256'], rows[0]['evidence_sha256'])
            with self.assertRaises(ValueError):
                self.change(nid, terminal, receipt=str(response.relative_to(self.root)))

    def test_transaction_rollback_on_history_failure(self):
        nid = self.notice()
        sha = co.build_manifest(self.conn, str(self.root), nid)['sha256']
        with patch.object(co, '_history', side_effect=RuntimeError('synthetic write failure')):
            with self.assertRaises(RuntimeError):
                self.change(nid, 'signed', expected_sha=sha)
        self.assertEqual(self.row(nid)['status'], 'draft')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM correspondence_approvals').fetchone()[0], 0)

    def test_export_refuses_symlinked_output_directory(self):
        nid = self.notice()
        with tempfile.TemporaryDirectory() as outside:
            (self.root / '.matter/out').symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'escapes'):
                co.export_packet(self.conn, str(self.root), nid)
            self.assertEqual(os.listdir(outside), [])

    def test_failed_export_cleans_staging_and_keeps_draft(self):
        nid = self.notice()
        with patch.object(co.shutil, 'copyfile', side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError):
                co.export_packet(self.conn, str(self.root), nid)
        self.assertEqual(list((self.root / '.matter/out/correspondence').iterdir()), [])
        self.assertEqual(self.row(nid)['status'], 'draft')

    def test_human_issuer_required_and_stale_manifest_cannot_record_mail(self):
        nid = self.notice()
        sha = self.sign(nid)
        with self.assertRaisesRegex(ValueError, 'human issuer'):
            co.transition(self.conn, str(self.root), nid, 'draft', issuer='')
        self.source('Correspondence/mailing.txt', 'SYNTHETIC mailing evidence')
        self.conn.execute("UPDATE correspondence_notices SET address='Changed address' WHERE id=?", (nid,))
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, 'approved packet changed'):
            self.change(nid, 'mailed', expected_sha=sha, receipt='Correspondence/mailing.txt')
        self.assertEqual(self.row(nid)['status'], 'signed')

    def test_sql_constraints_refuse_sent_state_without_receipt_and_non_nosend(self):
        nid = self.notice()
        self.sign(nid)
        for sql in ("UPDATE correspondence_notices SET status='mailed' WHERE id=?",
                    "UPDATE correspondence_notices SET transport='smtp' WHERE id=?"):
            with self.assertRaises(sqlite3.IntegrityError), self.conn:
                self.conn.execute(sql, (nid,))

    def test_reopen_existing_store_is_additive_and_idempotent(self):
        nid = self.notice()
        self.mail(nid)
        for _ in range(2):
            conn = store.connect(str(self.root))
            try:
                self.assertEqual(conn.execute('SELECT status FROM correspondence_notices WHERE id=?',
                                              (nid,)).fetchone()[0], 'mailed')
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM assertions').fetchone()[0], 5)
            finally:
                conn.close()

    def test_upgrade_from_pre_correspondence_schema_preserves_legacy_rows(self):
        with tempfile.TemporaryDirectory() as old:
            os.makedirs(os.path.join(old, '.matter'))
            conn = sqlite3.connect(store.db_path(old))
            conn.executescript(store.SCHEMA.split('CREATE TABLE IF NOT EXISTS correspondence_notices')[0])
            conn.execute("INSERT INTO parties VALUES ('legacy','Synthetic Legacy Agency','agency','custodian',NULL)")
            conn.commit()
            conn.close()
            for _ in range(2):
                conn = store.connect(old)
                self.assertEqual(conn.execute("SELECT name FROM parties WHERE id='legacy'").fetchone()[0],
                                 'Synthetic Legacy Agency')
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM correspondence_notices').fetchone()[0], 0)
                conn.close()

    def test_cli_workflow_from_foreign_cwd(self):
        args = ('correspondence', 'add', str(self.root), '--kind', 'hold', '--addressee', 'p_custodian',
                '--address', '100 Example Street', '--exhibit', 'Evidence/schedule.txt',
                '--confirm-due', '2026-10-01')
        result = self.cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        nid = result.stdout.strip()
        rendered = self.cli('correspondence', 'render', str(self.root), nid)
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        self.assertIn('HYPOTHESIS rows omitted.', rendered.stdout)
        result = self.cli('correspondence', 'export', str(self.root), nid)
        self.assertEqual(result.returncode, 0, result.stderr)
        exported = json.loads(result.stdout)
        result = self.cli('correspondence', 'status', str(self.root), nid, 'signed',
                          '--issuer', 'Synthetic Human', '--sha', exported['sha256'])
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.cli('correspondence', 'status', str(self.root), nid, 'mailed',
                          '--issuer', 'Synthetic Human', '--sha', exported['sha256'])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('receipt', result.stderr)
        result = self.cli('deadline', 'list', str(self.root))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('working-estimate', result.stdout)
        result = self.cli('verify', str(self.root))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        missing = self.root / 'does-not-exist'
        result = self.cli('correspondence', 'list', str(missing))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(missing.exists())

    def test_mcp_render_filters_claims_and_has_no_lifecycle_or_send_tools(self):
        nid = self.notice()
        requests = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
             'params': {'name': 'matter.hold_render', 'arguments': {'notice_id': nid}}},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
             'params': {'name': 'matter.hold_render', 'arguments': {'notice_id': 'missing'}}},
            {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
             'params': {'name': 'matter.correspondence_list', 'arguments': {}}},
        ]
        result = subprocess.run([sys.executable, str(KIT / 'matter-mcp.py')], cwd='/tmp',
                                env=dict(os.environ, MATTER_DIR=str(self.root)),
                                input='\n'.join(json.dumps(r) for r in requests) + '\n',
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        responses = [json.loads(line)['result'] for line in result.stdout.splitlines()]
        tools = {tool['name'] for tool in responses[0]['tools']}
        self.assertIn('matter.hold_render', tools)
        self.assertFalse(any('send' in name or 'approve' in name or 'mailed' in name for name in tools))
        self.assertNotIn('SYNTHETIC HYPOTHESIS CLAIM SENTINEL', str(responses[1]))
        self.assertIn('SYNTHETIC DOCUMENTED FACT CLAIM SENTINEL', str(responses[1]))
        self.assertTrue(responses[2]['isError'])
        self.assertIn(nid, str(responses[3]))
        self.assertEqual(self.row(nid)['status'], 'draft')


if __name__ == '__main__':
    unittest.main()
