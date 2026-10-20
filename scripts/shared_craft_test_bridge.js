const {spawn} = require('node:child_process');
const readline = require('node:readline');
function bridge({visual=false}={}) {
  const child = spawn(process.env.SHARED_CRAFT_TEST_PYTHON || 'python3',
    ['-u', '-m', 'tests_toy.shared_craft_browser_bridge', ...(visual?['--visual']:[])], {stdio:['pipe','pipe','inherit']});
  const pending = new Map();
  let serial = 0, readyResolve;
  const ready = new Promise(resolve => readyResolve = resolve);
  readline.createInterface({input:child.stdout}).on('line', line => {
    const value = JSON.parse(line);
    if(value.ready) readyResolve();
    else { pending.get(value.id)?.(value); pending.delete(value.id); }
  });
  return {
    ready,
    request: async (path, method='GET', body, uid=1) => {
      await ready;
      return new Promise(resolve => {
        const id = ++serial; pending.set(id, resolve);
        child.stdin.write(JSON.stringify({id,path,method,body,uid})+'\n');
      });
    },
    close: () => new Promise(resolve => { child.once('exit',resolve); child.stdin.end(); })
  };
}
module.exports = {bridge};
