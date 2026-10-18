// Una corrida de MoST: tasa de llegada constante contra un perfil.
//
// La lanza `load/most.py`; todos los parámetros entran por entorno:
//
//   TARGET   fr | calib
//   OP       verifyPair | detectFaces | searchGallery
//   MODEL    xs | s | base (verifyPair)
//   GALLERY  la galería sembrada (searchGallery), del entorno del contenedor
//   RATE     peticiones por segundo de la ventana medida
//   WINDOW   segundos de la ventana medida
//   WARMUP   segundos de calentamiento, al 50 % de RATE, que se descartan
//   SLICES   tramos en que se parte la ventana medida (3; 15 en la deriva)
//   OUT      fichero del resumen, bajo /load/results
//
// Dos escenarios: `warmup` y, a continuación, `measure`. El resumen que escribe
// `handleSummary` es el dato crudo que se versiona; las restricciones se evalúan
// después sobre él (load/criteria.py), nunca aquí.

import http from 'k6/http';
import exec from 'k6/execution';
import { Rate, Trend } from 'k6/metrics';

const RATE = Number(__ENV.RATE);
const WINDOW = Number(__ENV.WINDOW);
const WARMUP = Number(__ENV.WARMUP || 30);
const SLICES = Number(__ENV.SLICES || 3);
const OP = __ENV.OP || 'verifyPair';
const MODEL = __ENV.MODEL || 'xs';
const TARGET = __ENV.TARGET || 'fr';
const GALLERY = __ENV.GALLERY || '';
if (OP === 'searchGallery' && !GALLERY) throw new Error('searchGallery exige GALLERY');

// Con más peticiones en vuelo que tres segundos de llegadas, la latencia media
// pasa de 3 s (ley de Little): eso ya no es un servicio que da abasto. Con todos
// los VUs reservados de antemano, ninguna iteración se pierde por crear uno.
const MAX_VUS = Math.max(10, Math.ceil(3 * RATE));

// La tasa va en peticiones por 100 s: resolución de 0,01/s, que la búsqueda
// necesita por debajo de 1/s (1:N).
const per100s = (r) => Math.max(1, Math.round(r * 100));

const PATHS = {
  verifyPair: '/v1/fr/commercial/verification',
  detectFaces: '/v1/fr/commercial/detection',
  searchGallery: `/v1/fr/commercial/galleries/${GALLERY}/search`,
};
const URL = `http://${TARGET}:8000${PATHS[OP]}`;

const IMAGE1 = open('/load/fixtures/koch_2014_480x640.jpg', 'b');
const IMAGE2 = open('/load/fixtures/koch_2023_480x640.jpg', 'b');

// Las cabeceras que escribiría la pasarela. Sin pasarela (load/README.md).
const HEADERS = { 'X-Account-Id': 'load-most', 'X-Auth-Channel': 'api', 'X-Role': 'CUSTOMER' };

const ok2xx = new Rate('ok_2xx');
const inflight = new Trend('inflight');

const thresholds = {
  // Umbrales que no fallan nunca: solo existen para que k6 calcule y escriba
  // en el resumen los submétricos de la ventana medida y de cada tramo.
  'ok_2xx{scenario:measure}': ['rate>=0'],
  'dropped_iterations{scenario:measure}': ['count>=0'],
  'inflight{scenario:measure}': ['max>=0'],
  'http_req_duration{scenario:measure}': ['med>=0'],
};
// Los tercios son los de la restricción de latencia; los tramos, el detalle
// temporal (15 en la deriva, un minuto cada uno).
for (let t = 1; t <= 3; t++) {
  thresholds[`http_req_duration{scenario:measure,third:${t}}`] = ['med>=0'];
}
for (let s = 1; s <= SLICES; s++) {
  thresholds[`http_req_duration{scenario:measure,slice:${s}}`] = ['med>=0'];
  thresholds[`ok_2xx{scenario:measure,slice:${s}}`] = ['rate>=0'];
  thresholds[`inflight{scenario:measure,slice:${s}}`] = ['max>=0'];
}

const arrival = (rate, extra) => Object.assign({
  executor: 'constant-arrival-rate',
  rate: per100s(rate),
  timeUnit: '100s',
  preAllocatedVUs: MAX_VUS,
  maxVUs: MAX_VUS,
}, extra);

export const options = {
  discardResponseBodies: true,
  summaryTrendStats: ['min', 'med', 'avg', 'p(90)', 'p(95)', 'p(99)', 'max', 'count'],
  thresholds,
  scenarios: {
    warmup: arrival(RATE * 0.5, { duration: `${WARMUP}s` }),
    measure: arrival(RATE, { startTime: `${WARMUP}s`, duration: `${WINDOW}s` }),
  },
};

function body() {
  if (OP === 'detectFaces') return { image: http.file(IMAGE1, 'a.jpg', 'image/jpeg') };
  // Sin `threshold`: el umbral por omisión del modelo de la galería (xs). La
  // identidad enrolada es la de IMAGE2; la búsqueda tiene que coincidir.
  if (OP === 'searchGallery') return { image: http.file(IMAGE1, 'a.jpg', 'image/jpeg') };
  return {
    image1: http.file(IMAGE1, 'a.jpg', 'image/jpeg'),
    image2: http.file(IMAGE2, 'b.jpg', 'image/jpeg'),
    model: MODEL,
  };
}

export default function () {
  let slice = 'warmup';
  let third = 'warmup';
  if (exec.scenario.name === 'measure') {
    const elapsed = (Date.now() - exec.scenario.startTime) / 1000;
    slice = String(Math.min(SLICES, Math.floor(elapsed / (WINDOW / SLICES)) + 1));
    third = String(Math.min(3, Math.floor(elapsed / (WINDOW / 3)) + 1));
  }
  const tags = { slice, third };
  inflight.add(exec.instance.vusActive, tags);
  const res = http.post(URL, body(), { headers: HEADERS, tags, timeout: '60s' });
  ok2xx.add(res.status >= 200 && res.status < 300, tags);
}

export function handleSummary(data) {
  const params = { TARGET, OP, MODEL, RATE, WINDOW, WARMUP, SLICES, MAX_VUS, GALLERY };
  const m = data.metrics;
  const d = m['http_req_duration{scenario:measure}'];
  const line = `rate=${RATE} reqs=${d ? d.values.count : 0} med=${d ? d.values.med.toFixed(0) : '-'}ms\n`;
  return { [__ENV.OUT]: JSON.stringify({ params, k6: data }), stdout: line };
}
