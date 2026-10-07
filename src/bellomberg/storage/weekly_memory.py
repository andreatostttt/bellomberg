"""Read-only eligibility for operational weekly memory; archive is never rewritten."""
from hashlib import sha256
import json
from .weekly_run_store import WeeklyRunStore, digest
from bellomberg.core.llm_refusal import REFUSAL_TAG


def usable_text(value):
    head = str(value or '').strip()[:200]
    return bool(head) and not head.upper().startswith(('[IN PROGRESS]', '[ERROR', '[CAPO ERROR', '# MEMO INCOMPLETO', '[COLLASSO ANNUNCIO-SENZA-TOOL')) and 'No output produced in round' not in head and REFUSAL_TAG not in head


class WeeklyMemoryReader:
    """One read operation's cache; never persists a completion decision."""
    def __init__(self, db):
        self.db, self.cache, self.desk_gaps = db, {}, {}
        with db._conn() as conn:
            self.rows = {int(r['id']): dict(r) for r in conn.execute('SELECT * FROM memos ORDER BY id DESC')}
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='weekly_runs'").fetchone()
            self.native_ids = {int(r[0]) for r in conn.execute('SELECT memo_id FROM weekly_runs')} if exists else set()

    def completion(self, memo_id):
        if memo_id in self.cache:
            return self.cache[memo_id]
        from .memory_db import e_duplicato
        row = self.rows.get(memo_id)
        result = None
        if row and not e_duplicato(row) and not str(row.get('notes') or '').startswith('trade_idea:') and usable_text(row.get('full_markdown')):
            if memo_id not in self.native_ids:
                result = 'legacy_untracked'
            else:
                try:
                    store = WeeklyRunStore(self.db, memo_id)
                    state = store.status()
                    snapshot = json.loads(store._row()['snapshot_json'])
                    payload = snapshot.get('payload', {})
                    valid_snapshot = not snapshot or snapshot.get('sha256') == digest(payload)
                    data = payload.get('data') if isinstance(payload.get('data'), dict) else {}
                    gaps = set(data.get('_desk_gaps') or {}) | ({'red_team'} if data.get('_red_team_gap') else set())
                    self.desk_gaps[memo_id] = gaps
                    # Same incomplete-report contract as WeeklyRunStore.status(); historical
                    # book changes alone do not invalidate a completed published memo.
                    incomplete_report = any(cp.get('status') in ('failed', 'truncated')
                        for key, cp in payload.get('specialist_checkpoints', {}).items()
                        if isinstance(cp, dict) and str(key).split(':', 1)[0] not in gaps)
                    if (state.get('status') == 'completed' and state.get('analytical_status') == 'complete'
                            and state.get('artifact_status') == 'available' and valid_snapshot and not incomplete_report
                            and not state.get('worker_active')
                            and not [x for x in state.get('remaining_work', []) if x != 'optional_email_delivery']):
                        # Verify every saved checkpoint through the native hash checker.
                        for stage in state['completed_stages']:
                            store.get(stage)
                        validated = store.get('memo_validated') or {}
                        bundle = store.get('artifact_bundle') or {}
                        artifacts = state.get('artifacts') or []
                        if (validated.get('memo') == row['full_markdown']
                                and store.get('decisions_finalized') is not None
                                and artifacts == bundle.get('artifacts')
                                and {'Markdown', 'PDF'} <= {a.get('kind') for a in artifacts}
                                and any(a.get('kind') == 'Markdown' and a.get('sha256') == sha256(row['full_markdown'].encode()).hexdigest() for a in artifacts)):
                            result = 'native_completed'
                except Exception as exc:
                    print('[MEMORY] Native weekly evidence unavailable, memo ' + str(memo_id) + ': ' + type(exc).__name__)
        self.cache[memo_id] = result
        return result

    def memos(self, n=None):
        out = []
        for memo_id, row in self.rows.items():
            completion = self.completion(memo_id)
            if completion:
                out.append({**row, 'memory_completion': completion})
                if n is not None and len(out) >= n:
                    break
        return out if n is None or n > 0 else []

    def report_eligible(self, row):
        if not usable_text(row.get('content')) or not self.completion(row.get('memo_id')):
            return False
        if row['memo_id'] not in self.native_ids:
            return True
        if row['specialist'] in self.desk_gaps.get(row['memo_id'], set()):
            return False
        try:
            saved = WeeklyRunStore(self.db, row['memo_id']).get('desk:' + row['specialist'] + ':2') or {}
            return saved.get('status') == 'complete' and saved.get('report_sha256') == sha256(row['content'].encode()).hexdigest()
        except Exception as exc:
            print('[MEMORY] R2 checkpoint unavailable: ' + type(exc).__name__)
            return False
