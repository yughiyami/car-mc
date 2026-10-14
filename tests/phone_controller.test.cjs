// Deterministic browser-contract tests; no sensor hardware or broker required.
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = readFileSync('carritos/web/phone_controller.html', 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
function setup({permission = 'granted', secure = true, storedId = 'bad/id'} = {}) {
  const elements = new Map(), listeners = {}, intervals = new Map(), timeouts = new Map(), requests = [];
  let now = 1000, seq = 0;
  const element = id => {
    if (!elements.has(id)) elements.set(id, {textContent: '', dataset: {}, style: {}, disabled: id !== 'start', clientWidth: 320,
      addEventListener(type, fn) { listeners[`${id}:${type}`] = fn; }});
    return elements.get(id);
  };
  const context = vm.createContext({
    document: {getElementById: element, hidden: false, addEventListener(type, fn) {listeners[type] = fn;}},
    window: {isSecureContext: secure, DeviceOrientationEvent: {requestPermission: async () => permission},
      addEventListener(type, fn) {listeners[type] = fn;}, removeEventListener(type) {delete listeners[type];}},
    location: {search: ''}, URLSearchParams, Math, Number, JSON, Error, AbortController,
    performance: {now: () => now}, navigator: {},
    localStorage: {getItem: () => storedId, setItem: (_, value) => {storedId = value;}},
    setInterval(fn) {const id = ++seq; intervals.set(id, fn); return id;}, clearInterval(id) {intervals.delete(id);},
    setTimeout(fn) {const id = ++seq; timeouts.set(id, fn); return id;}, clearTimeout(id) {timeouts.delete(id);},
    fetch: async (_, options) => {requests.push(options); return {ok: true};},
  });
  vm.runInContext(script, context);
  return {context, element, listeners, intervals, timeouts, requests, advance(ms) {now += ms;},
    eval(code) {return vm.runInContext(code, context);}, async start() {await listeners['start:click']();}};
}
(async () => {
  let app = setup();
  assert.match(app.element('identity').textContent, /^phone-/); // Reject corrupt persisted topic IDs.
  await app.start();
  assert.equal(app.intervals.size, 1);
  assert.equal(app.element('zero').disabled, true);
  app.listeners.deviceorientation({beta: null, gamma: NaN});
  await app.eval('send()'); assert.equal(app.requests.length, 0);
  app.listeners.deviceorientation({beta: 10, gamma: 5});
  await app.eval('send()');
  assert.equal(JSON.parse(app.requests.at(-1).body).roll, 0); // First pose automatically neutralized.
  app.listeners.deviceorientation({beta: -10, gamma: 20});
  await app.eval('send()');
  assert.equal(JSON.parse(app.requests.at(-1).body).pitch, 20);
  app.advance(600); await app.eval('send()');
  assert.equal(JSON.parse(app.requests.at(-1).body).pitch, 0); // Never replay stale throttle.
  assert.equal(app.element('status').dataset.state, 'error');
  app.listeners['stop:click']();
  assert.equal(app.intervals.size, 0);
  assert.equal(app.listeners.deviceorientation, undefined);
  assert.equal(JSON.parse(app.requests.at(-1).body).roll, 0);
  await app.start(); assert.equal(app.intervals.size, 1);
  app.context.document.hidden = true; app.listeners.visibilitychange();
  assert.equal(app.intervals.size, 0);
  app = setup({permission: 'denied'}); await app.start();
  assert.equal(app.intervals.size, 0); assert.equal(app.element('start').disabled, false);
  assert.match(app.element('status').textContent, /denied/);
  app = setup({secure: false}); await app.start();
  assert.equal(app.intervals.size, 0); assert.match(app.element('status').textContent, /HTTPS/);
  // A late response from a stopped session must not change the status.
  app = setup(); let finish;
  app.context.fetch = (_, options) => new Promise(resolve => {finish = resolve;});
  await app.start(); app.listeners.deviceorientation({beta: 0, gamma: 0});
  const pending = app.eval('send()'); const completeOld = finish;
  app.listeners['stop:click'](); completeOld({ok: true}); await pending;
  assert.match(app.element('status').textContent, /stopped/);
  console.log('Phone controller: lifecycle, validation, stale neutral, permission and response-race checks passed.');
})().catch(error => {console.error(error); process.exitCode = 1;});
