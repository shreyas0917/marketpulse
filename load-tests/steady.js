// Steady quote load: `make load` (defaults) or
//   k6 run -e BASE_URL=http://localhost:18000 -e RATE=50 -e DURATION=10m load-tests/steady.js
// The first 10 s are tagged as synthetic warm-up (X-Traffic-Class: warmup) and are excluded
// from every SLI by the API.
import http from 'k6/http';
import { check } from 'k6';

const BASE = __ENV.BASE_URL || 'http://localhost:18000';
const HOST = __ENV.HOST_HEADER || '';
const RATE = parseInt(__ENV.RATE || '50', 10);
const DURATION = __ENV.DURATION || '10m';
const SYMBOLS = (__ENV.SYMBOLS ||
  'RELIANCE,TCS,INFY,HDFCBANK,ICICIBANK,SBIN,BHARTIARTL,ITC,LT,KOTAKBANK').split(',');

export const options = {
  discardResponseBodies: true,
  scenarios: {
    warmup: {
      executor: 'constant-arrival-rate', exec: 'warmup',
      rate: 10, timeUnit: '1s', duration: '10s', preAllocatedVUs: 10, maxVUs: 20,
    },
    steady: {
      executor: 'constant-arrival-rate', exec: 'steady', startTime: '10s',
      rate: RATE, timeUnit: '1s', duration: DURATION, preAllocatedVUs: 20, maxVUs: 400,
    },
  },
  summaryTrendStats: ['avg', 'p(50)', 'p(90)', 'p(99)', 'max'],
  // STRICT=1: fail the run (non-zero exit) if any request errors or returns 5xx.
  thresholds: __ENV.STRICT ? { 'checks{scenario:steady}': ['rate==1.0'] } : {},
};

function params(extraHeaders) {
  const headers = Object.assign({}, extraHeaders);
  if (HOST) headers.Host = HOST;
  return { headers, timeout: '5s' };
}

function symbol() {
  return SYMBOLS[Math.floor(Math.random() * SYMBOLS.length)];
}

export function warmup() {
  http.get(`${BASE}/v1/quotes/${symbol()}`, params({ 'X-Traffic-Class': 'warmup' }));
}

export function steady() {
  const res = http.get(`${BASE}/v1/quotes/${symbol()}`, params({}));
  check(res, {
    'no transport error': (r) => r.status !== 0,
    'not 5xx': (r) => r.status < 500,
  });
}
