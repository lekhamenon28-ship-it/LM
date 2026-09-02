const { start } = require('../common/server');
start(3002, {
  'GET /': () => ({ claims: [
    { id: 'CLM-10842', provider: 'Oak Street Primary Care', service: 'Annual wellness visit', date: 'Aug 8, 2026', status: 'Processed', billed: 245, youPay: 0 },
    { id: 'CLM-10791', provider: 'LabCorp Diagnostics', service: 'Preventive lab panel', date: 'Jul 27, 2026', status: 'Processed', billed: 186, youPay: 22.40 },
    { id: 'CLM-10633', provider: 'Northside Imaging', service: 'Diagnostic imaging', date: 'Jul 10, 2026', status: 'In review', billed: 680, youPay: null }
  ]})
});

