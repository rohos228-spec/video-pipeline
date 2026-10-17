// Local test relay only. The application and every app child remain network-none.
import net from 'node:net';
import {spawn} from 'node:child_process';
const [container, config] = process.argv.slice(2);
if (!/^vp-observe-test-[a-zA-Z0-9-]+$/.test(container) || !config) throw Error('Explicit test container/config required');
const server = net.createServer(socket => {
  const child = spawn('C:/Program Files/Docker/Docker/resources/bin/docker.exe',
    ['--config', config, '--host', 'npipe:////./pipe/dockerDesktopLinuxEngine', 'exec','-i',container,
      'python','/observe-source/tests/observe_v1/socket_bridge.py'],
    {windowsHide:true,env:{SystemRoot:process.env.SystemRoot,PATH:'C:/Windows/System32'}});
  socket.pipe(child.stdin); child.stdout.pipe(socket);
  child.stderr.resume();
  child.stdin.on('error',()=>socket.destroy()); child.on('error',()=>socket.destroy());
  socket.on('error',()=>child.stdin.destroy());
  socket.on('close',()=>child.stdin.destroy()); child.on('exit',()=>socket.destroy());
});
server.listen(18767,'127.0.0.1',()=>console.log('Synthetic observe UI: http://127.0.0.1:18767'));
