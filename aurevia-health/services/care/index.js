const { start } = require('../common/server');
start(3004, {
  'GET /': () => ({
    healthScore: 82,
    tasks: [
      { id: 1, title: 'Schedule annual eye exam', detail: 'Covered at $0 in network', due: 'Recommended', complete: false },
      { id: 2, title: 'Review lab results', detail: 'New results from Jul 27', due: 'New', complete: false },
      { id: 3, title: 'Annual wellness visit', detail: 'Completed Aug 8', due: 'Complete', complete: true }
    ],
    medications: [{ name: 'Atorvastatin', detail: '20 mg · 30 day supply', refill: '12 days' }]
  })
});

