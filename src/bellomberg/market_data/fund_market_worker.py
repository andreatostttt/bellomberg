"""Backend-owned market observations; imports and Fund reads never start work."""
from copy import deepcopy
from datetime import datetime, timezone
import logging
from pathlib import Path
from threading import Event, Lock, Thread

log = logging.getLogger(__name__)


def refresh_followed_market_data(*args, **kwargs):
    from .fund_market_refresh import refresh_followed_market_data as refresh
    return refresh(*args, **kwargs)


class FundMarketWorker:
    """One serial acquisition loop; the refresher owns per-symbol due times."""

    def __init__(self, db_path, cache_dir, *, interval_seconds=60):
        self.db_path = Path(db_path)
        self.cache_dir = Path(cache_dir)
        self.interval_seconds = max(0.01, interval_seconds)
        self._stop = Event()
        self._lock = Lock()
        self._thread = None
        self._state = {'status': 'starting', 'last_completed_at': None,
                       'last_result': None, 'error': None, 'next_retry_at': None}

    def status(self):
        with self._lock:
            return deepcopy(self._state)

    def start(self):
        with self._lock:
            if self._thread is not None:
                return self
            self._thread = Thread(target=self._run, name='fund-market-data', daemon=True)
            self._thread.start()
        return self

    def stop(self, timeout=5):
        self._stop.set()
        with self._lock:
            if self._state['status'] != 'stopped':
                self._state['status'] = 'stopping'
            thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        if thread is None or not thread.is_alive():
            with self._lock:
                self._state['status'] = 'stopped'

    def _run(self):
        try:
            while not self._stop.is_set():
                with self._lock:
                    self._state['status'] = 'refreshing'
                try:
                    result = refresh_followed_market_data(self.db_path,
                        cache_dir=self.cache_dir, stop_event=self._stop)
                    if not isinstance(result, dict) or not result.get('status'):
                        raise ValueError('market acquisition returned no status')
                    waiting = result['status'] == 'backoff' or bool(result.get('provider_backoff_until'))
                    error = (result.get('error') or 'market_update_' + result['status']) if (
                        result['status'] in {'error', 'unavailable', 'partial'}) else None
                    with self._lock:
                        self._state.update(status='waiting' if waiting else 'error' if error else 'idle',
                            last_completed_at=datetime.now(timezone.utc).isoformat(),
                            last_result=result, error=None if waiting else error,
                            # Notices are not failures, but they stay visible (no silent gaps).
                            notices=list(result.get('notices') or [])[:50],
                            next_retry_at=result.get('retry_after') if waiting else None)
                except Exception as exc:
                    message = type(exc).__name__ + ': ' + str(exc)[:300]
                    log.warning('Fund market acquisition failed: %s', message)
                    with self._lock:
                        self._state.update(status='error', error=message)
                if self._stop.wait(self.interval_seconds):
                    break
        finally:
            with self._lock:
                self._state['status'] = 'stopped'


def start_market_updates(db_path, cache_dir):
    return FundMarketWorker(db_path, cache_dir).start()
