const { start } = require('../common/server');
start(3001, {
  'GET /': () => ({
    id: 'AH-4829017', name: 'Maya Thompson', initials: 'MT', plan: 'Aurevia Choice PPO',
    memberSince: '2022', deductible: { used: 840, total: 1500 }, outOfPocket: { used: 1260, total: 4500 }
  })
});

