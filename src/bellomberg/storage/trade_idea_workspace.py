"""Append-only research actions in the existing Trade Idea event archive."""
from hashlib import sha256
from contextlib import contextmanager
import json
from pathlib import Path
from threading import Lock, RLock
from uuid import UUID

_locks_guard = Lock()
_locks = {}


@contextmanager
def _request_lock(key):
    # Threads wait for the first result without holding a database transaction.
    # Cross-process exclusivity comes from the durable claim below.
    with _locks_guard:
        lock, users = _locks.get(key, (RLock(), 0))
        _locks[key] = (lock, users + 1)
    try:
        with lock:
            yield
    finally:
        with _locks_guard:
            _, users = _locks[key]
            if users == 1:
                del _locks[key]
            else:
                _locks[key] = (lock, users - 1)


def _artifact_seals(data):
    """Seal generated outputs, including their separately persisted sidecars."""
    seals = {}
    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            pairs = [('path', 'workbook_sha256' if value.get('workbook_sha256') else 'sha256'),
                     ('source_catalog_path', 'source_catalog_sha256')]
            for path_key, hash_key in pairs:
                if isinstance(value.get(path_key), str) and value.get(hash_key):
                    path = str(Path(value[path_key]).resolve())
                    if path in seals and seals[path] != value[hash_key]:
                        raise ValueError('Conflicting research artifact seals')
                    seals[path] = value[hash_key]
                    if value.get('_workspace_sidecar_sha256'):
                        seals[str(Path(path).with_suffix('.payload.json'))] = value['_workspace_sidecar_sha256']
            for item in value.values():
                visit(item)
    visit(data)
    return seals


def _verify_seals(seals):
    for path, digest in seals.items():
        try:
            actual = sha256(Path(path).read_bytes()).hexdigest()
        except OSError as exc:
            raise ValueError('Completed research artifact missing during recovery') from exc
        if actual != digest:
            raise ValueError('Completed research artifact changed before publication')


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


class WorkspaceEvents:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _decode(row):
        payload = json.loads(row["payload_json"])
        return {"id": row["id"], "kind": row["kind"].split(".", 1)[1],
                "at": row["at"], "run_id": row["run_id"], **payload}

    def list(self, run_id):
        with self.store._connect(read_only=True) as conn:
            self.store._row(conn, run_id)
            return [self._decode(row) for row in conn.execute(
                "SELECT * FROM trade_idea_events WHERE run_id=? AND kind LIKE 'workspace.%' ORDER BY id", (run_id,))]

    def get(self, run_id, event_id):
        if not isinstance(event_id, int) or isinstance(event_id, bool):
            raise ValueError("research event id must be an integer")
        for event in self.list(run_id):
            if event["id"] == event_id:
                return event
        raise ValueError("research event absent from selected run")

    def pending(self, run_id):
        """Only operational summaries are exposed; internal result payloads stay private."""
        with self.store._connect(read_only=True) as conn:
            self.store._row(conn,run_id)
            grouped = {}
            for row in conn.execute("SELECT * FROM trade_idea_events WHERE run_id=? AND "
                    "(kind LIKE 'research.%' OR kind LIKE 'workspace.%') ORDER BY id",(run_id,)):
                value=json.loads(row['payload_json']); key=value.get('request_id')
                if row['kind']=='research.claimed':
                    grouped[key]={'request_id':key,'generation_id':value['generation_id'],
                        'kind':value['action'],'at':row['at'],'status':'pending_or_interrupted'}
                elif key in grouped:
                    if row['kind'].startswith('workspace.'):
                        del grouped[key]
                    elif row['kind']=='research.ready':
                        grouped[key]['status']='ready_to_recover'
                    elif row['kind']=='research.failed':
                        grouped[key].update(status='failed',reason=value['reason'])
            return list(grouped.values())

    def recover(self, run_id, request_id):
        """Publish a completed durable receipt only; never enter the computation path."""
        request_id=str(UUID(request_id))
        key=(str(Path(self.store.db_path).resolve()),run_id,request_id)
        with _request_lock(key), self.store._connect(read_only=True) as conn:
            rows=[(row,json.loads(row['payload_json'])) for row in conn.execute(
                "SELECT * FROM trade_idea_events WHERE run_id=? AND "
                "(kind LIKE 'research.%' OR kind LIKE 'workspace.%') ORDER BY id",(run_id,))]
            rows=[(row,value) for row,value in rows if value.get('request_id')==request_id]
            claim=next((value for row,value in rows if row['kind']=='research.claimed'),None)
            if claim is None:
                raise ValueError('Research recovery claim absent')
            for row,value in rows:
                if value.get('request_sha256')!=claim['request_sha256']:
                    raise ValueError('Research recovery identity changed')
                if row['kind'].startswith('workspace.'):
                    return self._decode(row)
            ready=next((value for row,value in rows if row['kind']=='research.ready'),None)
            if ready is None:
                raise ValueError('Research request interrupted without a durable result; no automatic recomputation')
            _verify_seals(ready['artifact_seals'])
            return self._publish(run_id,claim['action'],ready)

    def apply(self, run_id, kind, request, compute, *, request_id, generation_id):
        """Claim once, compute outside SQLite, then publish a durable exact result.

        A crash before the ready receipt remains explicitly interrupted. It never
        authorizes another calculation under the same request identity. A crash
        after that receipt reuses its artifacts without regenerating them.
        """
        request_id = str(UUID(request_id))
        digest = sha256(encode({"kind":kind,"request":request,"generation_id":generation_id}).encode()).hexdigest()
        identity = {"request_id":request_id,"request_sha256":digest,"generation_id":generation_id}
        key = (str(Path(self.store.db_path).resolve()), run_id, request_id)
        with _request_lock(key):
            prior, ready = self._claim(run_id, kind, identity)
            if prior is not None:
                return prior
            if ready is None:
                try:
                    data = compute(request_id)
                    ready = {**identity,"data":data,"artifact_seals":_artifact_seals(data)}
                    _verify_seals(ready['artifact_seals'])
                    encoded = encode(ready)
                except Exception as exc:
                    with self.store._connect() as conn:
                        self.store._event(conn,run_id,'research.failed',
                            {**identity,'reason':str(exc)[:2000]},self.store._at())
                    raise
                # Durable audit receipt is not a visible completed action. The
                # following publication and proposal invalidation are atomic.
                with self.store._connect() as conn:
                    conn.execute('INSERT INTO trade_idea_events(run_id,kind,payload_json,at) VALUES(?,?,?,?)',
                                 (run_id,'research.ready',encoded,self.store._at()))
            _verify_seals(ready['artifact_seals'])
            return self._publish(run_id,kind,ready)

    @staticmethod
    def _request_rows(conn, run_id, identity):
        found = []
        for row in conn.execute("SELECT * FROM trade_idea_events WHERE run_id=? AND "
                "(kind LIKE 'workspace.%' OR kind LIKE 'research.%') ORDER BY id", (run_id,)):
            value = json.loads(row['payload_json'])
            if value.get('request_id') == identity['request_id']:
                if value.get('request_sha256') != identity['request_sha256']:
                    raise ValueError('Idempotency request identity differs')
                found.append((row,value))
        return found

    def _claim(self, run_id, kind, identity):
        with self.store._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            try:
                current = self.store._row(conn,run_id)
                rows = self._request_rows(conn,run_id,identity)
                for row,value in rows:
                    if row['kind'].startswith('workspace.'):
                        conn.execute('COMMIT')
                        return self._decode(row),None
                if current['technical_status'] in ('accepted','running'):
                    raise ValueError('research actions require a stopped or finished committee')
                for row,value in rows:
                    if row['kind']=='research.failed':
                        raise ValueError('Prior research request failed: '+value['reason'])
                    if row['kind']=='research.ready':
                        conn.execute('COMMIT')
                        return None,value
                if rows:
                    raise ValueError('Research request pending or interrupted before its durable result; '
                                     'automatic recomputation is blocked')
                self.store._event(conn,run_id,'research.claimed',{**identity,'action':kind},self.store._at())
                conn.execute('COMMIT')
                return None,None
            except BaseException:
                if conn.in_transaction:
                    conn.execute('ROLLBACK')
                raise

    def _publish(self, run_id, kind, ready):
        with self.store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = self.store._row(conn, run_id)
                for prior,_value in self._request_rows(conn,run_id,ready):
                    if prior['kind'].startswith('workspace.'):
                        answer = self._decode(prior)
                        conn.execute("COMMIT")
                        return answer
                if row["technical_status"] in ("accepted", "running"):
                    raise ValueError("research actions require a stopped or finished committee")
                data = ready['data']
                payload = {key:ready[key] for key in ('request_id','request_sha256','generation_id','data')}
                at = self.store._at()
                cursor = conn.execute("INSERT INTO trade_idea_events(run_id,kind,payload_json,at) VALUES(?,?,?,?)",
                                      (run_id,"workspace."+kind,encode(payload),at))
                if data.get("invalidates_conclusion"):
                    # Keep result, sealed package and every PM intervention as history.
                    # The trade guard checks status, so the old DCN is no longer usable.
                    conn.execute("UPDATE trade_idea_runs SET technical_status='incomplete',phase='review_required',"
                                 "reason=?,updated_at=? WHERE id=?", (data["reason"],at,run_id))
                conn.execute("COMMIT")
                return {"id":cursor.lastrowid,"kind":kind,"at":at,"run_id":run_id,**payload}
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def pm_history(self, ticker):
        """Stable objection IDs for subsequent runs; responses never erase originals."""
        with self.store._connect(read_only=True) as conn:
            rows = [self._decode(row) for row in conn.execute(
                "SELECT e.* FROM trade_idea_events e JOIN trade_idea_runs r ON r.id=e.run_id "
                "WHERE r.ticker=? AND e.kind IN ('workspace.objection','workspace.objection_reply') ORDER BY e.id", (ticker,))]
        objections = {r["id"]:{"id":r["id"],"run_id":r["run_id"],"generation_id":r["generation_id"],
            "at":r["at"],"objection":r["data"]["text"],"driver":r["data"].get("driver"),
            "status":"open","responses":[]} for r in rows if r["kind"]=="objection"}
        for row in rows:
            if row["kind"] == "objection_reply" and row["data"]["objection_id"] in objections:
                target = objections[row["data"]["objection_id"]]
                target["responses"].append({"id":row["id"],"at":row["at"],**row["data"]})
                target["status"] = row["data"]["status"]
        return list(objections.values())
