const { start } = require('../common/server');
start(3003, {
  'GET /': () => ({ providers: [
    { name: 'Dr. Priya Raman', specialty: 'Primary Care', distance: '1.2 mi', rating: 4.9, available: 'Today, 3:30 PM' },
    { name: 'Dr. Daniel Cho', specialty: 'Internal Medicine', distance: '2.4 mi', rating: 4.8, available: 'Tomorrow, 9:00 AM' },
    { name: 'Willow Creek Clinic', specialty: 'Urgent Care', distance: '3.1 mi', rating: 4.7, available: 'Walk-ins open' }
  ]})
});

