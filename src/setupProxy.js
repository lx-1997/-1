// Development only: keep image/download URLs on the same origin as the UI.
// The local API may be a development backend or an explicitly opened SSH tunnel.
const { createProxyMiddleware } = require('http-proxy-middleware');
module.exports = function setupProxy(app) {
  app.use('/api', createProxyMiddleware({ target: 'http://127.0.0.1:8300', changeOrigin: true, ws: false }));
};
