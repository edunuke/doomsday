/**
 * Zero-dependency static file server for the StatArb deck.
 * Uses only Node.js built-in `http`/`fs` modules — no npm packages.
 * All presentation HTML files are self-contained and load libraries from CDN.
 */
const http = require('http');
const path = require('path');
const fs = require('fs');

const PORT = 8080;
const STATIC = path.join(__dirname, 'static');

// Route => static file (all self-contained HTML, libs via CDN)
const routes = {
  '/':            'presentation_v3.html',
  '/v1':          'presentation_v1.html',
  '/v2':          'presentation_v2.html',
  '/v3':          'presentation_v3.html',
  '/checklist':   'statarb_checklist.html',
  '/report/v3':   'report_v3.html',
  '/report/v3.2': 'report_v3.2.html',
  '/distress/v3': 'distress_report.html',
  '/mdd/v3':      'mdd_report.html',
};

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css':  'text/css; charset=utf-8',
  '.js':   'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png':  'image/png',
  '.jpg':  'image/jpeg',
  '.svg':  'image/svg+xml',
  '.ico':  'image/x-icon',
};

function send(res, status, contentType, body) {
  res.writeHead(status, { 'Content-Type': contentType });
  res.end(body);
}

const server = http.createServer((req, res) => {
  const urlPath = decodeURIComponent(req.url.split('?')[0]);

  // Health check
  if (urlPath === '/health') {
    send(res, 200, 'application/json', JSON.stringify({ status: 'ok' }));
    return;
  }

  // Map route to a static HTML file (basename only — no path traversal)
  const file = routes[urlPath];
  if (!file) {
    send(res, 404, 'text/plain', 'Not found');
    return;
  }

  const filePath = path.join(STATIC, path.basename(file));
  fs.readFile(filePath, (err, data) => {
    if (err) {
      send(res, 404, 'text/plain', 'Not found');
      return;
    }
    const ext = path.extname(filePath).toLowerCase();
    send(res, 200, MIME[ext] || 'application/octet-stream', data);
  });
});

server.listen(PORT, () => {
  console.log('StatArb Deck running at http://localhost:' + PORT);
  for (const route of Object.keys(routes)) {
    console.log('  http://localhost:' + PORT + route);
  }
});