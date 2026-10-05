// Test-only runtime prelude. Inject into the isolated renderer before its first
// application script. No product source or real application process uses this.
function installRuntimeInstrumentation() {
  if (window.__bbPagesRuntime) return;
  const native = {
    setInterval: window.setInterval.bind(window), clearInterval: window.clearInterval.bind(window),
    clearTimeout: window.clearTimeout.bind(window),
    add: EventTarget.prototype.addEventListener,
    remove: EventTarget.prototype.removeEventListener,
  };
  const intervals = new Map(), listeners = new Map(), callbacks = new WeakMap();
  const events = [], faults = [];
  const clock = 'fixture-controlled intervals; native timeouts and input';
  const subscriptionOrigin = () => ({ createdAt: Date.now(), origin: new Error('fixture-subscription-origin').stack || '' });
  let nextInterval = 1000000000, nextCallback = 0;
  const callbackId = fn => {
    if (!callbacks.has(fn)) callbacks.set(fn, ++nextCallback);
    return callbacks.get(fn);
  };
  const context = () => ({ route: location.hash,
    mode: document.querySelector('[data-theme-choice="dark"]')?.getAttribute('aria-pressed') === 'true' ? 'modern' : 'classic' });
  const log = (kind, details) => events.push({ kind, ...details, ...context() });

  // Only interval scheduling is controlled. Timeouts, debounce, RAF, request
  // completion and the browser's native focus/default input remain unchanged.
  window.setInterval = function (callback, delay, ...args) {
    const id = ++nextInterval;
    const milliseconds = Number.isFinite(Number(delay)) ? Math.max(0, Number(delay)) : 0;
    intervals.set(id, { id, callback, args, delay: milliseconds, ticks: 0,
      callbackId: typeof callback === 'function' ? callbackId(callback) : null,
      created: context(), ...subscriptionOrigin() });
    log('interval-added', { id, delay: milliseconds });
    return id;
  };
  window.clearInterval = function (id) {
    if (intervals.delete(Number(id))) log('interval-removed', { id: Number(id) });
    else native.clearInterval(id);
  };
  window.clearTimeout = function (id) {
    // Browsers share timeout/interval cancellation semantics.
    if (intervals.delete(Number(id))) log('interval-removed', { id: Number(id) });
    else native.clearTimeout(id);
  };

  // Monitor global subscriptions, not detached canvas/DOM nodes or XHR
  // internals. Those use their own renderer lifetimes and are not page timers.
  const targetName = target => target === window ? 'window' : target === document ? 'document' : null;
  const captureOf = options => typeof options === 'boolean' ? options : !!options?.capture;
  EventTarget.prototype.addEventListener = function (type, callback, options) {
    const target = targetName(this);
    if (!target || !callback || !['function', 'object'].includes(typeof callback)) {
      return native.add.call(this, type, callback, options);
    }
    const capture = captureOf(options), id = callbackId(callback);
    const key = `${target}:${type}:${id}:${capture}`;
    let record = listeners.get(key);
    if (record?.signal?.aborted) { listeners.delete(key); record = null; }
    if (!record) {
      record = { target, type: String(type), callbackId: id, capture, once: !!options?.once,
        signal: typeof options === 'object' ? options?.signal : null, calls: 0, created: context(), ...subscriptionOrigin() };
      record.wrapper = function (event) {
        record.calls++;
        log('listener-called', { target, type: String(type), callbackId: id });
        if (record.once) listeners.delete(key);
        if (typeof callback === 'function') return callback.call(this, event);
        return callback.handleEvent.call(callback, event);
      };
      listeners.set(key, record);
      log('listener-added', { target, type: String(type), callbackId: id, origin: record.origin, createdAt: record.createdAt });
    }
    // The same wrapper is reused for duplicate registrations, exactly as the
    // native API deduplicates callback/capture pairs.
    return native.add.call(this, type, record.wrapper, options);
  };
  EventTarget.prototype.removeEventListener = function (type, callback, options) {
    const target = targetName(this);
    if (!target || !callback || !['function', 'object'].includes(typeof callback)) {
      return native.remove.call(this, type, callback, options);
    }
    const id = callbacks.get(callback), key = `${target}:${type}:${id}:${captureOf(options)}`;
    const record = listeners.get(key);
    if (record) {
      listeners.delete(key);
      log('listener-removed', { target, type: String(type), callbackId: id, origin: record.origin, createdAt: record.createdAt });
      return native.remove.call(this, type, record.wrapper, options);
    }
    return native.remove.call(this, type, callback, options);
  };

  window.__bbPagesRuntime = {
    clock,
    snapshot() {
      const liveListeners = [...listeners.values()].filter(item => !item.signal?.aborted);
      return {
        clock,
        intervals: [...intervals.values()].map(({ id, delay, ticks, callbackId, created, createdAt, origin }) => ({ id, delay, ticks, callbackId, created, createdAt, origin })),
        listeners: liveListeners.map(({ target, type, callbackId, capture, once, calls, created, createdAt, origin }) => ({ target, type, callbackId, capture, once, calls, created, createdAt, origin })),
        listenerCounts: liveListeners.reduce((out, item) => { const key = `${item.target}:${item.type}`; out[key] = (out[key] || 0) + 1; return out; }, {}),
        events: events.slice(), faults: faults.slice(),
      };
    },
    async tick(ids) {
      const selected = ids || [...intervals.keys()];
      const pending = [];
      for (const id of selected) {
        const item = intervals.get(Number(id));
        if (!item) continue;
        item.ticks++;
        log('interval-called', { id: item.id, delay: item.delay, tick: item.ticks });
        try {
          const value = typeof item.callback === 'function'
            ? item.callback.apply(window, item.args) : (0, eval)(String(item.callback));
          if (value && typeof value.then === 'function') pending.push(Promise.resolve(value).catch(error => {
            faults.push({ id: item.id, message: String(error?.message || error) }); throw error;
          }));
        } catch (error) {
          faults.push({ id: item.id, message: String(error?.message || error) }); throw error;
        }
      }
      await Promise.all(pending);
      return this.snapshot();
    },
  };
}
module.exports = { installRuntimeInstrumentation };
