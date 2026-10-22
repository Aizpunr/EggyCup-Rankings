import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {runInNewContext} from 'node:vm';
import {readFileSync} from 'node:fs';
const P = createRequire(import.meta.url)('../../eggy_parser.js');
const L = s => `[Info:COTDTracker] ${s}\n`;
const round = (times, eliminated) => [L('Doing eliminations with leaderboard'),
  ...times.map(([n, t]) => L(`Player ${n}: Time: ${t}`)),
  ...eliminated.map(n => L(`Eliminating DNF: ${n}`))];
let count = 0;
function test(name, fn) { fn(); count++; console.log(`ok ${name}`); }
const final = round([['W', '40,5'], ['X', '42']], ['X']);
const marker = round([['W', '1']], ['W']);
test('marker winner skips marker time, fastest includes marker', () => {
  const p = P.parseCupLog([...final, ...marker]);
  assert.equal(p.winner, 'W'); assert.equal(p.winnerTime, '40,5');
  assert.equal(p.nRounds, 2); assert.equal(p.fastestTime, 1); assert.equal(p.fastestRound, 2);
  assert.deepEqual(p.leaderboard, [['W', '40,5', null, 1], ['X', '42', 1, 2]]);
  assert.deepEqual(P.detectOutcome([...final, ...marker]), {kind: 'marker', podium: ['W', 'X']});
  assert.equal(P.parseCupLog([...final, ...marker], ['W'], {manualWinner: 'X'}).winner, 'X');
});
const double = [...round([['A', '40'], ['B', '41'], ['C', '43'], ['D', '44']], ['D', 'C']),
  ...round([['A', 'DNF'], ['B', 'DNF']], ['A', 'B'])];
test('double DNF with third finisher', () => {
  assert.deepEqual(P.detectOutcome(double), {kind: 'double_dnf_final', finalists: ['A', 'B'], third: 'C'});
  assert.throws(() => P.parseCupLog(double), {name: 'ParseError', message: 'Could not determine winner.'});
});
const survivors = round([['B', '42'], ['A', '40,1'], ['C', 'DNF']], ['C']);
test('survivors error and finishByTimes', () => {
  assert.throws(() => P.parseCupLog(survivors), {name: 'ParseError', message:
    '2 survivors (B, A) — cup never reached a final. Pass --finish-by-times to rank them by their last-round times, or --winner to pick one.'});
  const p = P.parseCupLog(survivors, [], {finishByTimes: true});
  assert.deepEqual(p.survivors, ['A', 'B']);
  assert.deepEqual(p.leaderboard, [['A', '40,1', null, 1], ['B', '42', null, 2], ['C', 'DNF', 1, 3]]);
  assert.deepEqual(P.detectOutcome(survivors), {kind: 'survivors_left', ranking: [['A', '40,1'], ['B', '42']]});
});
test('manualWinner removes all their eliminations', () => {
  const p = P.parseCupLog(double, [], {manualWinner: 'A'});
  assert.equal(p.leaderboard.filter(e => e[0] === 'A').length, 1);
  assert.equal(p.winnerTime, 'DNF');
});
test('comma decimals, reversed DNF ties and stable finisher ties', () => {
  const log = round([['W', '40,1'], ['A', '45,5'], ['B', '44,5'], ['C', 'DNF'], ['D', 'DNF']], ['A', 'B', 'C', 'D']);
  const p = P.parseCupLog(log);
  assert.deepEqual(p.leaderboard, [['W', '40,1', null, 1], ['B', '44,5', 1, 2], ['A', '45,5', 1, 3], ['D', 'DNF', 1, 4], ['C', 'DNF', 1, 4]]);
  assert.equal(p.fastestTime, 40.1);
});
test('CRLF, CR, BOM, invalid UTF8 and universal newlines', () => {
  for (const sep of ['\r\n', '\r', '\n']) {
    const text = '\ufeff' + final.join('').replace(/\n/g, sep);
    assert.deepEqual(P.parseCupText(P.decodeBytes(Buffer.from(text))), P.parseCupLog(final));
  }
  assert.equal(P.decodeBytes(new Uint8Array([239, 187, 191, 255])), '\ufeff\ufffd');
  assert.deepEqual(P.splitLines('a\r\nb\rc\n\n'), ['a', 'b', 'c', '']);
  assert.deepEqual(P.splitLines('a\u2028b'), ['a\u2028b']);
});
test('multiple starts drops empty starts', () => {
  const start = L('Starting cup with exclude list');
  const log = [start, start, ...final, start, ...final, start];
  assert.equal(P.splitLobbies(log).length, 2);
  assert.deepEqual(P.detectOutcome(log), {kind: 'multi_start', lobbies: [{rounds: 1, players: 2}, {rounds: 1, players: 2}]});
});
test('steam IDs skip whitespace names, preserve IDs and replace duplicates', () => {
  const ids = P.steamIds([L('Checking joined player   with steamID 123'),
    L('Checking joined player X with steamID 000123'), L('Checking joined player X with steamID 456'),
    L('Checking joined player __proto__ with steamID 789')]);
  assert.deepEqual(Object.entries(ids), [['X', '456'], ['__proto__', '789']]);
});
test('Python whitespace, regex search and dictionary insertion order', () => {
  assert.equal(P.pyStrip('\x1c X\x85'), 'X'); assert.equal(P.pyStrip('\ufeffX'), '\ufeffX');
  const log = round([['__proto__', '40'], ['2', '42'], ['1', '43']], ['2', '1']);
  assert.deepEqual(P.namedPlayers(P.splitRounds(log)), ['__proto__', '2', '1']);
  assert.equal(P.parseCupLog(log).winner, '__proto__');
  assert.deepEqual([...P.roundInfo([L('prefix Player A: Time: 40\nPlayer B: Time: 1')])[0]], [['A', '40']]);
});
test('payload key order, labels, errors and browser wrapper', () => {
  const p = P.parseCupLog(final), payload = P.cupJsonPayload(p, 0, 'TBD', 'Special');
  assert.equal(Object.keys(payload).join(','), 'cup,cup_num,mapper,players');
  assert.equal(Object.keys(payload.players[0]).join(','), 'pos,name,time,round');
  assert.equal(payload.cup, 'Special');
  assert.throws(() => P.parseCupLog([]), {name: 'ParseError', message: 'No COTDTracker lines found in log file.'});
  assert.throws(() => P.parseCupLog([L('hello')]), {name: 'ParseError', message: 'No elimination rounds found in log.'});
  const context = {}; runInNewContext(readFileSync(new URL('../../eggy_parser.js', import.meta.url), 'utf8'), context);
  assert.equal(context.EggyParser.parseCupLog(final).winner, 'W');
});


test('Python float accepts Unicode digits, underscores and special values', () => {
  assert.equal(P.pyFloat('\u0661\u0662.\uff15'), 12.5);
  assert.equal(P.pyFloat('\u{1d7d9}_\u{1d7da}.\u{1d7dd}'), 12.5);
  assert.equal(P.pyFloat(' +Infinity '), Infinity);
  assert.ok(Number.isNaN(P.pyFloat('nan')));
  for (const bad of ['12abc', '0x10', '1__0', '\x1c40', '\ufeff40']) {
    assert.throws(() => P.pyFloat(bad), {name: 'ValueError'});
  }
  assert.equal(P.parseCupLog(round([['W', '\u0661\u0662.\uff15'], ['X', '20']], ['X'])).fastestTime, 12.5);
});

console.log(`${count} rule tests passed`);
