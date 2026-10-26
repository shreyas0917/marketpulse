// Market-open burst (09:15 IST simulation): `make burst` or
//   k6 run -e BASE_URL=... -e PRODUCER_URL=http://localhost:19200 load-tests/burst.js
// Quote traffic runs at BASE_RATE, jumps to BASE_RATE x MULTIPLIER for BURST, then returns.
// If PRODUCER_URL is set, setup() also switches the producer into burst mode for the same period,
// so ingestion sees the 10x event rate too.
import http from 'k6/http';
import { check } from 'k6';

const BASE = __ENV.BASE_URL || 'http://localhost:18000';
const HOST = __ENV.HOST_HEADER || '';
const PRODUCER = __ENV.PRODUCER_URL || '';
const BASE_RATE = parseInt(__ENV.BASE_RATE || '20', 10);
const MULTIPLIER = parseInt(__ENV.MULTIPLIER || '10', 10);
const PRE = __ENV.PRE || '1m';
const BURST = __ENV.BURST || '5m';
const POST = __ENV.POST || '3m';
const SYMBOLS = (__ENV.SYMBOLS ||
  'RELIANCE,TCS,INFY,HDFCBANK,ICICIBANK,SBIN,BHARTIARTL,ITC,LT,KOTAKBANK').split(',');

function seconds(d) {
  const m = /^(\d+)(s|m)$/.exec(d);
  return m[2] === 'm' ? parseInt(m[1], 10) * 60 : parseInt(m[1], 10);
}

const scenarios = {
  market_open: {
    executor: 'ramping-arrival-rate', exec: 'quotes',
    startRate: BASE_RATE, timeUnit: '1s', preAllocatedVUs: 50, maxVUs: 1000,
    stages: [
      { target: BASE_RATE, duration: PRE },
      { target: BASE_RATE * MULTIPLIER, duration: '10s' },
      { target: BASE_RATE * MULTIPLIER, duration: BURST },
      { target: BASE_RATE, duration: '10s' },
      { target: BASE_RATE, duration: POST },
    ],
  },
};
if (PRODUCER) {
  // One request that switches the producer to burst mode when the quote burst starts.
  scenarios.producer_burst = {
    executor: 'per-vu-iterations', exec: 'triggerBurst', vus: 1, iterations: 1, startTime: PRE,
  };
}

export const options = {
  discardResponseBodies: true,
  scenarios,
  summaryTrendStats: ['avg', 'p(50)', 'p(90)', 'p(99)', 'max'],
};

function params() {
  return { headers: HOST ? { Host: HOST } : {}, timeout: '5s' };
}

export function triggerBurst() {
  const res = http.post(`${PRODUCER}/burst?multiplier=${MULTIPLIER}&duration=${seconds(BURST)}`);
  check(res, { 'producer burst started': (r) => r.status === 200 });
}

export function quotes() {
  const sym = SYMBOLS[Math.floor(Math.random() * SYMBOLS.length)];
  const res = http.get(`${BASE}/v1/quotes/${sym}`, params());
  check(res, {
    'no transport error': (r) => r.status !== 0,
    'not 5xx': (r) => r.status < 500,
  });
}
