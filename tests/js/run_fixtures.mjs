import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
const P = createRequire(import.meta.url)('../../eggy_parser.js');
const cases = JSON.parse(readFileSync(process.argv[2], 'utf8'));
let failures = 0;
for (const c of cases) {
  try {
    let lines = P.filterTrackerLines(P.splitLines(P.decodeBytes(readFileSync(c.path))));
    if (c.lobby) lines = P.splitLobbies(lines)[c.lobby - 1];
    let got;
    try { got = {payload: P.cupJsonPayload(P.parseCupLog(lines, c.exclude, c.opts), 0, 'TBD')}; }
    catch (e) { if (!(e instanceof P.ParseError)) throw e; got = {error: e.message}; }
    got.outcome = P.detectOutcome(lines, c.exclude);
    if (JSON.stringify(got) !== JSON.stringify(c.expected)) {
      failures++;
      console.error(`FAIL ${c.name}:\n got ${JSON.stringify(got)}\n expected ${JSON.stringify(c.expected)}`);
      const a = got.payload?.players ?? [], b = c.expected.payload?.players ?? [];
      const i = Array.from({length: Math.max(a.length, b.length)}, (_, i) => i)
        .find(i => JSON.stringify(a[i]) !== JSON.stringify(b[i]));
      console.error(i === undefined ? 'First differing player: none (error or outcome differs)' :
        `First differing player ${i + 1}: ${JSON.stringify(a[i])} != ${JSON.stringify(b[i])}`);
    }
  } catch (e) { failures++; console.error(`FAIL ${c.name}: ${e.stack}`); }
}
console.log(`${cases.length} cases, ${failures} mismatches`);
process.exitCode = failures ? 1 : 0;
