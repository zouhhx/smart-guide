import './src/index.js';

setTimeout(async () => {
  try {
    const response = await fetch('http://127.0.0.1:3995/api/screens');
    const data = await response.json();
    console.log(JSON.stringify(data));
    process.exit(0);
  } catch (err) {
    console.error(err);
    process.exit(1);
  }
}, 1000);
