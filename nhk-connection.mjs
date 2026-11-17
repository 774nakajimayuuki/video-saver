// App-owned session -> anonymous stdin -> bounded CDM host. No browser profile reads.
/*
 * ソース整理時点の範囲: このモジュールは認証確認までです。
 * 番組データの取得・復号・MP4保存への接続は実装していません。
 * 認証の成功結果だけを返し、ホストは一時セッションを終了します。
 * 将来の保存処理が必要とする「保存計画」「読み取り可能なメディア素材」は、
 * このモジュールの出力に含まれません。状態や利用可能な鍵の件数だけを返します。
 * 保存担当は別の部品とし、素材・進捗・中止・失敗を扱うインターフェースが必要です。
 * 正規の仕組みでMP4書き出し用データを得られるかは、配信ごとに別途確認が必要です。
 * 具体的な責務と既存MP4Planへの接続案はSOURCE_INDEX.txtの第4節を参照してください。
 */
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import https from 'node:https';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import { pathToFileURL } from 'node:url';

export const endpoint = 'https://licence.hsk.st.nhk/widevine/license';
const limit = 1024 * 1024;
const expectedCDM = 'e1e9f6b0c77f4251542a2f49dda419c85eda88c5d9ef2721d5d594dc195a6e5f';
const support = path.join(os.homedir(), 'Library/Application Support/local.videosaver.app');
const marker = path.join(support, 'own-session-license-attempt-v1.json');

export function usableToken(token, now = Date.now() / 1000) {
  if (typeof token !== 'string' || token.length > 65536 || !/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(token)) return false;
  try {
    const { exp } = JSON.parse(Buffer.from(token.split('.')[1], 'base64url'));
    return typeof exp === 'number' && Number.isFinite(exp) && exp > now + 30;
  } catch { return false; }
}

// Exactly one explicit request; Node https does not follow redirects or retry requests.
export async function sendLicenseOnce(challenge, token, attemptPath, requestImpl = https.request) {
  if (!usableToken(token) || !Buffer.isBuffer(challenge) || !challenge.length || challenge.length > limit) {
    return { status: 0, bytes: 0, body: Buffer.alloc(0), error: 'invalid_input', attempted: false };
  }
  fs.mkdirSync(path.dirname(attemptPath), { recursive: true, mode: 0o700 });
  let fd;
  try { fd = fs.openSync(attemptPath, 'wx', 0o600); }
  catch { return { status: 0, bytes: 0, body: Buffer.alloc(0), error: 'attempt_already_recorded', attempted: false }; }
  try {
    fs.writeFileSync(fd, JSON.stringify({ endpoint, max_requests: 1, attempted_at: new Date().toISOString() }) + '\n');
    fs.fsyncSync(fd);
  } finally { fs.closeSync(fd); }
  return new Promise(resolve => {
    let complete = false;
    let request;
    const done = result => {
      if (complete) return;
      complete = true;
      clearTimeout(deadline);
      resolve({ attempted: true, ...result });
    };
    const deadline = setTimeout(() => {
      done({ status: 0, bytes: 0, body: Buffer.alloc(0), error: 'network_timeout' });
      request?.destroy();
    }, 15000);
    try {
      request = requestImpl(endpoint, {
        method: 'POST', agent: false,
        headers: {
          Authorization: `Bearer ${token}`, 'Content-Type': 'application/octet-stream',
          'Content-Length': challenge.length, Origin: 'https://www.web.nhk',
          Referer: 'https://www.web.nhk/', 'User-Agent': 'Mozilla/5.0'
        }
      }, response => {
        const status = response.statusCode ?? 0;
        if (status !== 200) {
          done({ status, bytes: 0, body: Buffer.alloc(0), error: 'http_rejected' });
          response.destroy();
          return;
        }
        let bytes = 0;
        const chunks = [];
        response.on('data', chunk => {
          if (complete) return;
          bytes += chunk.length;
          if (bytes > limit) {
            done({ status, bytes, body: Buffer.alloc(0), error: 'oversized_response' });
            response.destroy();
          } else chunks.push(chunk);
        });
        response.on('end', () => done({ status, bytes, body: Buffer.concat(chunks), error: bytes ? null : 'empty_response' }));
        response.on('error', () => done({ status, bytes, body: Buffer.alloc(0), error: 'response_interrupted' }));
        response.on('aborted', () => done({ status, bytes, body: Buffer.alloc(0), error: 'response_interrupted' }));
      });
      request.on('error', () => done({ status: 0, bytes: 0, body: Buffer.alloc(0), error: 'network_error' }));
      request.end(challenge);
    } catch {
      done({ status: 0, bytes: 0, body: Buffer.alloc(0), error: 'network_error' });
      request?.destroy();
    }
  });
}

async function readInput() {
  let bytes = 0;
  const chunks = [];
  const timeout = setTimeout(() => process.stdin.destroy(new Error('input_timeout')), 5000);
  try {
    for await (const chunk of process.stdin) {
      bytes += chunk.length;
      if (bytes > 128 * 1024) throw new Error('oversized_input');
      chunks.push(chunk);
    }
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } finally { clearTimeout(timeout); }
}

async function run(mode, input) {
  const resources = path.resolve(import.meta.dirname, '..');
  const library = path.join(resources, 'WidevineCdm/_platform_specific/mac_arm64/libwidevinecdm.dylib');
  const pssh = path.join(resources, 'NHKTrial/init-data.bin');
  const host = path.resolve(resources, '../Helpers/nhk-license-probe');
  const result = {
    checked_at: new Date().toISOString(), mode, phase: 'failed',
    own_login_input_available: usableToken(input.token),
    cdm_sha256: '', challenge_bytes: 0, response_bytes: 0, http_status: 0,
    request_attempts: 0, usable_key_count: 0, initialized: false,
    sensitive_payloads_saved: false, media_downloaded: false, error_code: null
  };
  if (!result.own_login_input_available) return { ...result, error_code: 'login_required' };
  if (mode === 'send-once' && fs.existsSync(marker)) return { ...result, error_code: 'attempt_already_recorded' };
  result.cdm_sha256 = crypto.createHash('sha256').update(fs.readFileSync(library)).digest('hex');
  if (result.cdm_sha256 !== expectedCDM) return { ...result, error_code: 'component_changed' };
  const child = spawn(host, [library, pssh, '3'], {
    stdio: ['ignore', 'pipe', 'ignore', 'pipe'],
    env: { PATH: '/usr/bin:/bin', HOME: os.homedir(), TMPDIR: os.tmpdir(), LANG: 'en_US.UTF-8' }
  });
  const channel = child.stdio[3];
  let buffer = Buffer.alloc(0);
  let exchanged = false;
  let log = '';
  let logBytes = 0;
  let exchangeWork = Promise.resolve();
  const abortReply = () => { if (!channel.destroyed) channel.end(Buffer.alloc(4)); };
  channel.on('error', () => {});
  child.stdout.on('data', chunk => {
    logBytes += chunk.length;
    if (logBytes > 65536) { result.error_code = 'host_output_limit'; child.kill('SIGKILL'); return; }
    log += chunk.toString('utf8');
  });
  channel.on('data', chunk => {
    if (exchanged) { abortReply(); return; }
    buffer = Buffer.concat([buffer, chunk]);
    if (buffer.length < 4) return;
    const size = buffer.readUInt32LE();
    if (!size || size > limit || buffer.length > size + 4) {
      exchanged = true; result.error_code = 'invalid_challenge'; abortReply(); return;
    }
    if (buffer.length < size + 4) return;
    exchanged = true;
    result.challenge_bytes = size;
    const challenge = buffer.subarray(4);
    buffer = Buffer.alloc(0);
    if (mode === 'prepare') { abortReply(); return; }
    exchangeWork = (async () => {
      const response = await sendLicenseOnce(challenge, input.token, marker);
      result.request_attempts = response.attempted ? 1 : 0;
      result.http_status = response.status;
      result.response_bytes = response.bytes;
      result.error_code = response.error;
      if (!channel.destroyed) {
        const size = Buffer.alloc(4);
        size.writeUInt32LE(response.body.length);
        channel.write(Buffer.concat([size, response.body]));
      }
    })().catch(() => { result.error_code = 'broker_error'; abortReply(); });
  });
  const timer = setTimeout(() => { result.error_code = 'host_timeout'; child.kill('SIGKILL'); }, 35000);
  const exit = await new Promise(resolve => {
    child.on('error', () => { result.error_code = 'host_launch_failed'; });
    child.on('close', code => resolve(code));
  });
  clearTimeout(timer);
  await exchangeWork;
  channel.destroy();
  for (const line of log.split('\n')) {
    if (!line.startsWith('CDM_PROBE ')) continue;
    try {
      const { event, value } = JSON.parse(line.slice(10));
      if (!Number.isSafeInteger(value)) continue;
      if (event === 'initialized') result.initialized = value === 1;
      if (event === 'usable_key_count') result.usable_key_count = value;
    } catch { /* Never export unrecognised output. */ }
  }
  result.host_exit_code = exit;
  if (!result.error_code && result.initialized && result.challenge_bytes > 0) {
    if (mode === 'prepare' && exit === 44) result.phase = 'ready';
    if (mode === 'send-once' && exit === 0 && result.http_status === 200 && result.usable_key_count > 0) result.phase = 'authenticated';
  }
  input.token = null;
  return result;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  let result;
  try {
    const mode = process.argv[2];
    if (!['prepare', 'send-once'].includes(mode)) throw new Error('invalid_mode');
    result = await run(mode, await readInput());
  } catch { result = { phase: 'failed', error_code: 'setup_failed', sensitive_payloads_saved: false }; }
  process.stdout.write(JSON.stringify(result) + '\n');
}
