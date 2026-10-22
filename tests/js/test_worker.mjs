import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
// Data URL keeps the copied package metadata unchanged and loads the Worker as ESM.
const source = readFileSync(new URL('../../submit-worker/src/index.js', import.meta.url), 'utf8');
const {default: worker} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
if (!globalThis.crypto) globalThis.crypto = webcrypto;
const originalFetch = globalThis.fetch;
globalThis.fetch = async () => Response.json({success: true, hostname: 'localhost'});
class KV {
  data = new Map();
  async get(k) { return this.data.get(k)?.value ?? null; }
  async put(k, value, metadata) { this.data.set(k, {value, metadata}); }
}
function env() {
  return {SUBMISSIONS: new KV(), TURNSTILE_SECRET: 'test-placeholder', POLLER_TOKEN: 'test-placeholder',
    ALLOWED_ORIGINS: 'http://localhost:9004', TURNSTILE_HOSTNAMES: 'localhost'};
}
async function submit(ruling, e = env(), cup = '110') {
  const form = new FormData();
  form.set('file', new Blob(['COTDTracker Doing eliminations with leaderboard']), 'sample.log');
  for (const [k, v] of Object.entries({cup, mapper: 'Example', map: '',
    date: new Date().toISOString().slice(0, 10), 'cf-turnstile-response': 'fake',
    ruling: typeof ruling === 'string' ? ruling : JSON.stringify(ruling)})) form.set(k, v);
  return worker.fetch(new Request('https://example.test/submit', {method: 'POST', headers: {Origin: 'http://localhost:9004'}, body: form}), e);
}
try {
  let e = env();
  let r = await submit({lobby: null, outcome: 'confirmed'}, e);
  assert.equal(r.status, 200); assert.equal((await r.json()).status, 'received');
  e = env();
  r = await submit({lobby: null, outcome: 'review', note: 'Please check'}, e);
  assert.equal(r.status, 200); assert.equal((await r.json()).status, 'review');
  r = await worker.fetch(new Request('https://example.test/status'), e);
  assert.equal((await r.json()).submissions[0].ruling.outcome, 'review');
  r = await worker.fetch(new Request('https://example.test/pending', {headers: {Authorization: 'Bearer test-placeholder'}}), e);
  assert.deepEqual((await r.json()).submissions, []);
  for (const ruling of ['{bad', {lobby: null, outcome: 'third_wins'},
    {lobby: null, outcome: 'third_wins', winner: 'A,B'},
    {lobby: true, outcome: 'confirmed'}, {lobby: null, outcome: 'confirmed', winner: 'A'},
    {lobby: null, outcome: 'review', note: '--bad'}]) {
    assert.equal((await submit(ruling)).status, 400);
  }
  // An identical file is still recorded, as a duplicate pointing at the first.
  e = env();
  const first = await (await submit({lobby: null, outcome: 'confirmed'}, e)).json();
  const again = await (await submit({lobby: null, outcome: 'confirmed'}, e)).json();
  assert.equal(again.status, 'duplicate'); assert.notEqual(again.id, first.id);
  assert.equal(again.same_file_as, first.id);
  const otherCup = await (await submit({lobby: null, outcome: 'confirmed'}, e, '111')).json();
  assert.equal(otherCup.status, 'duplicate');
  r = await worker.fetch(new Request('https://example.test/status'), e);
  const rows = (await r.json()).submissions;
  assert.equal(rows.length, 3);
  const flagged = rows.find(x => x.id === otherCup.id);
  assert.ok(flagged.note.startsWith('REVIEW: same file as ' + first.id + ', which was sent as Eggy 110'), flagged.note);
  assert.equal(rows.find(x => x.id === again.id).note, 'same file as ' + first.id);
  r = await worker.fetch(new Request('https://example.test/pending', {headers: {Authorization: 'Bearer test-placeholder'}}), e);
  assert.deepEqual((await r.json()).submissions.map(x => x.id), [first.id]);
  console.log('Worker submit/status/ruling tests passed');
} finally { globalThis.fetch = originalFetch; }
