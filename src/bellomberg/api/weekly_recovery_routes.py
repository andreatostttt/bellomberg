"""Read native weekly checkpoints and validate ordinary run/recovery options."""
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator


class WeeklyRunOptions(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    resume_memo_id: int | None = Field(default=None, ge=1)
    delivery_only: bool = False
    authorize_new_ai: bool = False
    send_email: bool = False
    # PM 04/10: conferma esplicita del possibile duplicato per ripetere un invio INCERTO.
    acknowledge_uncertain_email: bool = False

    @model_validator(mode='after')
    def exact_recovery(self):
        if self.delivery_only and self.resume_memo_id is None:
            raise ValueError('Selezionare un memo preciso per recuperare la consegna')
        if self.acknowledge_uncertain_email and (self.resume_memo_id is None or not self.send_email):
            raise ValueError("La conferma del re-invio incerto vale solo per un memo preciso e con l'invio email richiesto")
        if self.delivery_only and self.authorize_new_ai:
            raise ValueError('Il recupero dei file non autorizza richieste AI')
        return self


def read_worker_outcome(path, task_id, returncode):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('Outcome finale del worker assente o illeggibile; nessun completamento verificato') from exc
    if (not isinstance(value, dict) or value.get('task_id') != task_id
            or value.get('status') not in {'completed', 'incomplete', 'failed', 'cancelled', 'interrupted'}):
        raise ValueError('Outcome finale non valido o appartenente a un altro task')
    if value['status'] == 'completed' and (returncode != 0
            or value.get('analytical_status') != 'complete' or value.get('artifact_status') != 'available'
            or type(value.get('memo_id')) is not int or value['memo_id'] <= 0):
        raise ValueError('Outcome completo non confermato da processo, analisi, artefatti e memo')
    return value


def install_weekly_recovery_routes(app, require_session, db_provider):
    from bellomberg.storage.weekly_run_store import get_weekly_run_status, WeeklyRunBlocked
    router = APIRouter(prefix='/consigliere/runs', dependencies=[Depends(require_session)])

    @router.get('')
    def runs(limit: int = Query(30, ge=1, le=100)):
        db = db_provider()
        with db._conn() as conn:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='weekly_runs'").fetchone()
            if not exists:
                return {'runs': [], 'native_checkpoints_available': False,
                        'reason': 'Nessuna run con checkpoint nativi; i memo storici restano disponibili'}
            ids = [row[0] for row in conn.execute('SELECT memo_id FROM weekly_runs ORDER BY updated_at DESC LIMIT ?', (limit,))]
        results = []
        for memo_id in ids:
            try:
                results.append(get_weekly_run_status(db, memo_id))
            except WeeklyRunBlocked as exc:
                results.append({'memo_id': memo_id, 'status': 'blocked', 'reason': str(exc),
                                'resume_available': False, 'delivery_recovery_available': False})
        return {'runs': results, 'native_checkpoints_available': True}

    @router.get('/{memo_id}')
    def detail(memo_id: int):
        try:
            return get_weekly_run_status(db_provider(), memo_id)
        except WeeklyRunBlocked as exc:
            raise HTTPException(404, str(exc)) from exc

    app.include_router(router)
