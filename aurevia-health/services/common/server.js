const http = require('http');

function start(port, routes) {
  const service = process.env.SERVICE_NAME || 'service';
  http.createServer((req, res) => {
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    if (req.method === 'GET' && req.url === '/health') {
      res.end(JSON.stringify({ status: 'healthy', service }));
      return;
    }
    const handler = routes[`${req.method} ${req.url.split('?')[0]}`];
    if (!handler) {
      res.statusCode = 404;
      res.end(JSON.stringify({ error: 'Not found' }));
      return;
    }
    try { res.end(JSON.stringify(handler(req))); }
    catch (error) {
      res.statusCode = 500;
      res.end(JSON.stringify({ error: 'Unexpected service error' }));
    }
  }).listen(port, '0.0.0.0', () => console.log(`${service} listening on ${port}`));
}

module.exports = { start };

