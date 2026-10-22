/* JavaScript port of eggy_parser.py; Python is the source of truth. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.EggyParser = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var TRACKER_TAG = 'COTDTracker';

  // Python '.' matches anything but '\n'; [^\n] is that exactly.
  var RE_PLAYER_TIME = /Player ([^\n]+?): Time: ([^\n]+)/;
  var RE_PLAYER_NAMED = /Player ([^\n]+?): Time:/;
  var RE_ELIMINATED = /Eliminating (?:DNF|on time): ([^\n]+)/;
  var RE_HAS_ELIM = /Eliminating (?:DNF|on time):/;

  function ParseError(message) {
    var e = new Error(message);
    Object.setPrototypeOf(e, ParseError.prototype);
    return e;
  }
  ParseError.prototype = Object.create(Error.prototype);
  ParseError.prototype.constructor = ParseError;
  ParseError.prototype.name = 'ParseError';

  function PyValueError(message) {
    var e = new Error(message);
    Object.setPrototypeOf(e, PyValueError.prototype);
    return e;
  }
  PyValueError.prototype = Object.create(Error.prototype);
  PyValueError.prototype.constructor = PyValueError;
  PyValueError.prototype.name = 'ValueError';

  // ── Python primitives ──────────────────────────────────────────────

  // Characters for which Python's str.isspace() is true.
  var PY_WS = '\\t\\n\\x0b\\x0c\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000';
  var RE_LSTRIP = new RegExp('^[' + PY_WS + ']+');
  var RE_RSTRIP = new RegExp('[' + PY_WS + ']+$');

  function pyStrip(s) {
    return String(s).replace(RE_LSTRIP, '').replace(RE_RSTRIP, '');
  }

  var RE_PY_FLOAT = /^[+-]?(?:\d(?:_?\d)*(?:\.(?:\d(?:_?\d)*)?)?|\.\d(?:_?\d)*)(?:[eE][+-]?\d(?:_?\d)*)?$/;
  var RE_PY_SPECIAL = /^([+-]?)(inf|infinity|nan)$/i;

  // Python float(str): surrounding whitespace allowed, underscores between
  // digits allowed, inf/nan accepted, anything else raises ValueError.
  function pyFloat(s) {
    // float() accepts Unicode decimal digits, but not strip()'s U+001C?001F.
    var raw = String(s);
    var t = pyStrip(raw);
    if (/[\x1c-\x1f]/.test(raw)) {
      throw PyValueError('could not convert string to float: ' + pyRepr(raw));
    }
    t = t.replace(/\p{Decimal_Number}/gu, function (digit) {
      var cp = digit.codePointAt(0), start = cp;
      while (start > 0 && /\p{Decimal_Number}/u.test(String.fromCodePoint(start - 1))) start--;
      return String((cp - start) % 10);
    });
    var sp = RE_PY_SPECIAL.exec(t);
    if (sp) {
      if (sp[2].toLowerCase() === 'nan') return NaN;
      return sp[1] === '-' ? -Infinity : Infinity;
    }
    if (!RE_PY_FLOAT.test(t)) {
      throw PyValueError('could not convert string to float: ' + pyRepr(String(s)));
    }
    return Number(t.replace(/_/g, ''));
  }

  // Python round(x) for a float: round half to even, on the exact double.
  function pyRound(x) {
    if (x !== x) throw PyValueError('cannot convert float NaN to integer');
    if (x === Infinity || x === -Infinity) throw PyValueError('cannot convert float infinity to integer');
    var f = Math.floor(x);
    var d = x - f;               // exact for |x| < 2**52
    if (d < 0.5) return f;
    if (d > 0.5) return f + 1;
    return (f % 2 === 0) ? f : f + 1;
  }

  // Python str ordering: lexicographic by code point.
  function cmpCodePoints(a, b) {
    var i = 0, j = 0;
    while (i < a.length && j < b.length) {
      var ca = a.codePointAt(i), cb = b.codePointAt(j);
      if (ca !== cb) return ca < cb ? -1 : 1;
      i += ca > 0xffff ? 2 : 1;
      j += cb > 0xffff ? 2 : 1;
    }
    if (i < a.length) return 1;
    if (j < b.length) return -1;
    return 0;
  }

  function pySorted(iterable) {
    return Array.from(iterable).sort(cmpCodePoints);
  }

  // Python repr() of a str, for the warning text. Covers quote choice and
  // ASCII/Latin-1 control escapes; other non-printable Unicode (which Python
  // would also escape) is passed through. Display text only, not in any
  // golden output.
  function pyRepr(s) {
    var quote = (s.indexOf("'") !== -1 && s.indexOf('"') === -1) ? '"' : "'";
    var out = '';
    for (var k = 0; k < s.length; k++) {
      var ch = s[k], c = s.charCodeAt(k);
      if (ch === '\\') out += '\\\\';
      else if (ch === quote) out += '\\' + quote;
      else if (ch === '\n') out += '\\n';
      else if (ch === '\r') out += '\\r';
      else if (ch === '\t') out += '\\t';
      else if (c < 0x20 || (c >= 0x7f && c <= 0xa0) || c === 0xad) {
        out += '\\x' + (c < 16 ? '0' : '') + c.toString(16);
      } else out += ch;
    }
    return quote + out + quote;
  }

  // Python text-mode readlines(): split on \r\n, \r or \n. The terminator is
  // dropped (every consumer strips or regex-matches up to it anyway).
  function splitLines(text) {
    if (text === '') return [];
    var parts = String(text).split(/\r\n|\r|\n/);
    if (parts.length && parts[parts.length - 1] === '') parts.pop();
    return parts;
  }

  // Decode file bytes the way open(encoding='utf-8', errors='replace') does:
  // a BOM is kept as U+FEFF, invalid bytes become U+FFFD.
  function decodeBytes(bytes) {
    return new TextDecoder('utf-8', { ignoreBOM: true, fatal: false }).decode(bytes);
  }

  // ── Port of cotd_parser.py ─────────────────────────────────────────

  function filterTrackerLines(lines) { return lines.filter(l => l.includes(TRACKER_TAG)); }
  function splitRounds(lines) {
    const rounds = []; let current = [];
    for (const line of lines) {
      if (line.includes('Doing eliminations with leaderboard')) {
        if (current.length) rounds.push(current);
        current = [];
      } else if (line.includes('Eliminating ') || line.includes('Player ')) current.push(line);
    }
    if (current.length) rounds.push(current);
    return rounds;
  }
  function roundInfo(rnd) {
    const times = new Map(), eliminated = [];
    for (const line of rnd) {
      const m = RE_PLAYER_TIME.exec(line), e = RE_ELIMINATED.exec(line);
      if (m) times.set(pyStrip(m[1]), pyStrip(m[2]));
      if (e && !eliminated.includes(pyStrip(e[1]))) eliminated.push(pyStrip(e[1]));
    }
    return [times, eliminated];
  }
  function isMarker(times, eliminated) {
    return times.size === 1 && eliminated.length === 1 && times.keys().next().value === eliminated[0];
  }
  function splitLobbies(lines) {
    const segments = [[]];
    for (const line of lines) {
      if (line.includes('Starting cup with exclude list')) segments.push([]);
      segments[segments.length - 1].push(line);
    }
    return segments.filter(s => splitRounds(s).some(r => roundInfo(r)[1].length));
  }
  function namedPlayers(rounds) {
    const names = new Set();
    for (const rnd of rounds) for (const line of rnd) {
      const m = RE_PLAYER_NAMED.exec(line);
      if (m) names.add(pyStrip(m[1]));
    }
    return [...names];
  }
  function steamIds(lines) {
    const out = Object.create(null);
    for (const line of lines) {
      const m = /Checking joined player ([^\n]+?) with steamID (\p{Decimal_Number}+)/u.exec(line);
      if (m && pyStrip(m[1])) out[pyStrip(m[1])] = m[2];
    }
    return out;
  }
  const getTime = (times, name) => times.has(name) ? times.get(name) : 'DNF';
  function timeKey(t) {
    try { return [0, pyFloat(t.replace(/,/g, '.'))]; }
    catch (e) { if (!(e instanceof PyValueError || e instanceof TypeError)) throw e; return [1, 0]; }
  }
  const cmpNum = (a, b) => a < b ? -1 : a > b ? 1 : 0;
  const cmpTime = (a, b) => cmpNum(a[0], b[0]) || cmpNum(a[1], b[1]);
  function eliminations(rounds, excluded) {
    const order = []; let actual = 0, marker = null, times = new Map(), last = [];
    for (const rnd of rounds) {
      const [pt, eliminated] = roundInfo(rnd);
      if (!eliminated.length) continue;
      if (isMarker(pt, eliminated)) { marker = eliminated[0]; continue; }
      actual++; times = pt; last = [];
      for (const name of eliminated) if (!excluded.has(name)) {
        const t = getTime(pt, name);
        order.push([name, t, actual, t === 'DNF']); last.push(name);
      }
    }
    return [order, marker, times, last];
  }
  function buildLeaderboard(winner, winnerTime, ranks, times, order) {
    order = [...order].reverse();
    const leaderboard = [[winner, winnerTime, null, 1]]; let pos = 2, i = 0;
    for (const name of ranks) leaderboard.push([name, getTime(times, name), null, pos++]);
    while (i < order.length) {
      const rnd = order[i][2], finishers = [], dnfs = [];
      while (i < order.length && order[i][2] === rnd) {
        const e = order[i++];
        if (e[3]) dnfs.push(e);
        else {
          try { finishers.push([...e.slice(0, 3), pyFloat(String(e[1]).replace(/,/g, '.'))]); }
          catch (err) { if (!(err instanceof PyValueError || err instanceof TypeError)) throw err; dnfs.push(e); }
        }
      }
      finishers.sort((a, b) => cmpNum(a[3], b[3]));
      for (const e of finishers) leaderboard.push([...e.slice(0, 3), pos++]);
      for (const e of dnfs) leaderboard.push([...e.slice(0, 3), pos]);
      pos += dnfs.length;
    }
    return leaderboard;
  }
  function parseCupLog(lines, excluded = [], opts = {}) {
    lines = filterTrackerLines(lines);
    if (!lines.length) throw ParseError('No COTDTracker lines found in log file.');
    const rounds = splitRounds(lines);
    if (!rounds.length) throw ParseError('No elimination rounds found in log.');
    excluded = new Set(excluded);
    let [order, marker, times] = eliminations(rounds, excluded);
    const elimSet = new Set(order.map(e => e[0]));
    let winner, ranks = [], survivors = [];
    if (opts.manualWinner) {
      winner = opts.manualWinner; order = order.filter(e => e[0] !== winner);
    } else if (marker && !excluded.has(marker)) winner = marker;
    else {
      const winners = namedPlayers(rounds).filter(n => !elimSet.has(n) && !excluded.has(n));
      if (!winners.length) throw ParseError('Could not determine winner.');
      if (winners.length > 1) {
        if (!opts.finishByTimes) throw ParseError(`${winners.length} survivors (${winners.join(', ')}) \u2014 cup never reached a final. Pass --finish-by-times to rank them by their last-round times, or --winner to pick one.`);
        winners.sort((a, b) => cmpTime(timeKey(getTime(times, a)), timeKey(getTime(times, b))));
        ranks = winners.slice(1); survivors = [...winners];
      }
      winner = winners[0];
    }
    let winnerTime = null;
    for (const rnd of [...rounds].reverse()) {
      const [pt, elim] = roundInfo(rnd);
      if (isMarker(pt, elim)) continue;
      if (pt.has(winner)) { winnerTime = pt.get(winner); break; }
    }
    const leaderboard = buildLeaderboard(winner, winnerTime, ranks, times, order);
    let fastestTime = null, fastestName = null, fastestRound = null, actual = 0;
    for (const rnd of rounds) {
      const hasElim = rnd.some(line => RE_HAS_ELIM.test(line));
      if (hasElim) actual++;
      const rn = hasElim ? actual : 0;
      for (const line of rnd) {
        const m = RE_PLAYER_TIME.exec(line);
        if (m && pyStrip(m[2]) !== 'DNF') {
          const t = pyFloat(pyStrip(m[2]).replace(/,/g, '.'));
          if (fastestTime === null || t < fastestTime) {
            fastestTime = t; fastestName = pyStrip(m[1]); fastestRound = rn;
          }
        }
      }
    }
    return {leaderboard, winner, winnerTime, fastestTime, fastestName, fastestRound, nRounds: rounds.length, survivors};
  }
  function parseCupText(text, excluded, opts) { return parseCupLog(filterTrackerLines(splitLines(text)), excluded, opts); }
  function detectOutcome(lines, excluded = []) {
    lines = filterTrackerLines(lines);
    const lobbies = splitLobbies(lines);
    if (lobbies.length > 1) return {kind: 'multi_start', lobbies: lobbies.map(s => {
      const comp = splitRounds(s).filter(r => roundInfo(r)[1].length);
      return {rounds: comp.length, players: comp.length ? roundInfo(comp[0])[0].size : 0};
    })};
    excluded = new Set(excluded);
    const rounds = splitRounds(lines);
    if (!rounds.length) return {kind: 'no_winner'};
    const [order, marker, times, lastElims] = eliminations(rounds, excluded);
    if (marker && !excluded.has(marker)) return {kind: 'marker', podium: parseCupLog(lines, excluded).leaderboard.slice(0, 3).map(e => e[0])};
    const elimSet = new Set(order.map(e => e[0]));
    const survivors = namedPlayers(rounds).filter(n => !elimSet.has(n) && !excluded.has(n));
    if (survivors.length === 1) return {kind: 'single', podium: parseCupLog(lines, excluded).leaderboard.slice(0, 3).map(e => e[0])};
    if (survivors.length > 1) {
      survivors.sort((a, b) => cmpTime(timeKey(getTime(times, a)), timeKey(getTime(times, b))));
      return {kind: 'survivors_left', ranking: survivors.map(n => [n, getTime(times, n)])};
    }
    const last = order.length ? order.filter(e => lastElims.includes(e[0]) && e[2] === order[order.length - 1][2]) : [];
    if (last.length >= 2 && last.every(e => e[3])) {
      const prev = order.filter(e => e[2] === last[0][2] - 1);
      const finishers = prev.filter(e => !e[3]).sort((a, b) => cmpTime(timeKey(a[1]), timeKey(b[1])));
      return {kind: 'double_dnf_final', finalists: last.map(e => e[0]), third: finishers.length ? finishers[0][0] : prev.length === 1 ? prev[0][0] : null};
    }
    return {kind: 'no_winner'};
  }
  function cupJsonPayload(parsed, cupNum, mapper, label) {
    return {cup: label || `Eggy ${cupNum}`, cup_num: cupNum, mapper,
      players: parsed.leaderboard.map(([name, time, round, pos]) => ({pos, name, time, round}))};
  }
  return {ParseError, PyValueError, decodeBytes, splitLines, filterTrackerLines, splitRounds,
    splitLobbies, roundInfo, isMarker, namedPlayers, steamIds, parseCupLog, parseCupText,
    detectOutcome, cupJsonPayload, pyStrip, pyFloat, pyRound, pySorted, cmpCodePoints, pyRepr};
});
