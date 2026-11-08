// Local Agora bridge used only to listen to a room and record temporary chunks for transcription.
// Run under Electron 12 (ABI 87); the SDK addon is in third_party/agora-electron-sdk.
const path = require('path');
const SDK_DIR = process.env.CLUBDECK_SDK_DIR || path.join(
  __dirname, 'third_party', 'agora-electron-sdk'
);
const addon = path.join(SDK_DIR, 'build', 'Release', 'agora_node_ext.node');

const args = process.argv.slice(2);
const [appId, channel, token, uidStr] = args;
const DEVICE = args.includes('--device') ? args[args.indexOf('--device') + 1] : null;
if (!appId || !channel || !token || !uidStr) {
  console.log('usage: agora_bridge.js <appId> <channel> <token> <uid> [--listen] [--device <name>]');
  process.exit(1);
}
const uid = parseInt(uidStr, 10);
process.env.PATH = path.join(SDK_DIR, 'build', 'Release') + path.delimiter + process.env.PATH;

const mod = require(addon);
const engine = new mod.NodeRtcEngine();
const ev = (name, fn) => {
  try { engine.onEvent(name, fn); }
  catch (e) { console.log('onEvent ' + name + ' failed:', e.message); }
};
ev('joinchannel', (ch, joinedUid) => console.log('[EVT] JOINED ch=' + ch + ' uid=' + joinedUid));
ev('rejoinchannel', (ch, joinedUid) => console.log('[EVT] REJOINED ch=' + ch + ' uid=' + joinedUid));
ev('error', (err, msg) => {
  console.log('[EVT] ERROR ' + err + ' ' + msg);
  try {
    const desc = engine.getErrorDescription ? engine.getErrorDescription(err) : null;
    if (desc) console.log('[EVT] ERROR_DESC ' + desc);
  } catch (_) {}
});
ev('warning', (warning, msg) => console.log('[EVT] WARN ' + warning + ' ' + msg));
ev('audiovolumeindication', (speakers) => {
  if (speakers && speakers.length) {
    const values = speakers.map(x => (x.uid === uid ? 'ME' : x.uid) + ':' + Math.round(x.volume)).join(' ');
    console.log('[EVT] VOLUME ' + values);
  }
});
ev('connectionstatechanged', (state, reason) => console.log('[EVT] CONN_STATE ' + state + ' reason=' + reason));

console.log('initialize:', engine.initialize(appId, 0xFFFFFFFF, undefined));
if (DEVICE) {
  const devices = engine.getAudioPlaybackDevices() || [];
  const query = DEVICE.toLowerCase();
  const selected = devices.find(x => String(x.deviceid || '').toLowerCase() === query) ||
    devices.find(x => String(x.devicename || '').toLowerCase().includes(query));
  if (!selected) {
    console.log('[EVT] ERROR playback device not found: ' + DEVICE);
    process.exit(2);
  }
  const result = engine.setAudioPlaybackDevice(selected.deviceid);
  console.log('[EVT] PLAYBACK_DEVICE name=' + selected.devicename + ' ret=' + result);
  if (result !== 0) process.exit(2);
}

engine.setChannelProfile(1);
engine.setClientRole(1);
engine.enableAudio();
engine.disableVideo();
try { engine.setAudioProfile(5, 3); } catch (e) { console.log('setAudioProfile err:', e.message); }
try { engine.enableAudioVolumeIndication(500, 3, false); }
catch (e) { console.log('enableAudioVolumeIndication err:', e.message); }
engine.setDefaultMuteAllRemoteAudioStreams(false);
engine.muteAllRemoteAudioStreams(false);
engine.muteLocalAudioStream(true);
engine.enableLocalAudio(true);
console.log('[CTRL] mic=MUTED');
console.log('joinChannel ret:', engine.joinChannel(token, channel, '', uid));

let recording = false;
let shuttingDown = false;
function shutdown(reason) {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log('[CTRL] shutdown reason=' + reason + ' recording=' + (recording ? 'ON' : 'OFF'));
  if (recording) {
    try { engine.stopAudioRecording(); } catch (_) {}
    recording = false;
  }
  try { engine.leaveChannel(); } catch (_) {}
  try {
    if (typeof engine.release === 'function') engine.release(true);
  } catch (e) { console.log('[EVT] ERROR release ' + e.message); }
  process.exit(0);
}

process.stdin.setEncoding('utf8');
process.stdin.on('end', () => shutdown('stdin-end'));
process.stdin.on('close', () => shutdown('stdin-close'));
process.stdin.on('data', (chunk) => {
  for (const command of chunk.trim().split(/\s+/)) {
    if (command.startsWith('r64:')) {
      try {
        const body = command.slice(4);
        let encodedPath = body;
        let position = 0;
        const sep = body.lastIndexOf(':');
        if (sep > 0 && /^[0-2]$/.test(body.slice(sep + 1))) {
          encodedPath = body.slice(0, sep);
          position = parseInt(body.slice(sep + 1), 10);
        }
        const filePath = Buffer.from(encodedPath, 'base64').toString('utf8');
        const result = engine.startAudioRecording(filePath, 48000, 2, position);
        recording = result === 0;
        console.log('[CTRL] recording=ON ret=' + result + ' pos=' + position);
      } catch (e) {
        console.log('[EVT] ERROR startAudioRecording ' + e.message);
      }
    } else if (command === 'x') {
      try {
        const result = engine.stopAudioRecording();
        if (result === 0) recording = false;
        console.log('[CTRL] recording=OFF ret=' + result);
      } catch (e) {
        console.log('[EVT] ERROR stopAudioRecording ' + e.message);
      }
    } else if (command === 'q') {
      shutdown('command');
    }
  }
});
process.on('SIGINT', () => shutdown('sigint'));
