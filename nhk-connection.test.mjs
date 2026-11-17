import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { sendLicenseOnce, usableToken, endpoint } from './nhk-connection.mjs';

const token = `synthetic.${Buffer.from(JSON.stringify({exp: Date.now()/1000 + 3600})).toString('base64url')}.synthetic`;
const challenge = Buffer.from('SYNTHETIC_CHALLENGE');
const license = Buffer.from('SYNTHETIC_RESPONSE');
function mock(status, chunks = [license], fail = false) {
  const calls = [];
  return { calls, request(url, options, callback) {
    const req = new EventEmitter();
    req.destroy = () => {};
    req.end = data => {
      calls.push({url, options, data});
      queueMicrotask(() => {
        if (fail) { req.emit('error', new Error('synthetic')); return; }
        const response = new EventEmitter();
        response.statusCode = status;
        response.destroyed = false;
        response.destroy = () => { response.destroyed = true; };
        callback(response);
        for (const chunk of chunks) if (!response.destroyed) response.emit('data', chunk);
        if (!response.destroyed) response.emit('end');
      });
    };
    return req;
  }};
}
async function fixture(run) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'nhk-broker-test-'));
  try { await run(path.join(dir, 'attempt.json')); }
  finally { fs.rmSync(dir, {recursive:true, force:true}); }
}

test('token input rejects expired/malformed/header-injection values', () => {
  assert.equal(usableToken(token), true);
  assert.equal(usableToken(token, Date.now()/1000 + 4000), false);
  assert.equal(usableToken('x.e30.x'), false);
  assert.equal(usableToken(token + '\r\nCookie: private'), false);
});
test('one fixed-endpoint request, no cookies, exclusive marker has no payloads', async () => fixture(async marker => {
  const m = mock(200);
  const result = await sendLicenseOnce(challenge, token, marker, m.request);
  assert.equal(result.status, 200);
  assert.deepEqual(result.body, license);
  assert.equal(m.calls.length, 1);
  assert.equal(m.calls[0].url, endpoint);
  assert.equal(m.calls[0].options.headers.Authorization, `Bearer ${token}`);
  assert.equal(Object.keys(m.calls[0].options.headers).some(k => k.toLowerCase() === 'cookie'), false);
  const saved = fs.readFileSync(marker, 'utf8');
  for (const secret of [token, challenge.toString(), license.toString()]) assert.equal(saved.includes(secret), false);
  const repeat = await sendLicenseOnce(challenge, token, marker, m.request);
  assert.equal(repeat.attempted, false);
  assert.equal(m.calls.length, 1);
}));
test('redirect response is rejected without following or retrying', async () => fixture(async marker => {
  const m = mock(302);
  const result = await sendLicenseOnce(challenge, token, marker, m.request);
  assert.equal(result.error, 'http_rejected');
  assert.equal(result.body.length, 0);
  assert.equal(m.calls.length, 1);
}));
test('uncertain network failure consumes the one-attempt marker', async () => fixture(async marker => {
  const m = mock(0, [], true);
  const result = await sendLicenseOnce(challenge, token, marker, m.request);
  assert.equal(result.error, 'network_error');
  assert.equal(result.attempted, true);
  const repeat = await sendLicenseOnce(challenge, token, marker, m.request);
  assert.equal(repeat.attempted, false);
  assert.equal(m.calls.length, 1);
}));
test('oversized license is never delivered to the CDM', async () => fixture(async marker => {
  const m = mock(200, [Buffer.alloc(1024*1024+1)]);
  const result = await sendLicenseOnce(challenge, token, marker, m.request);
  assert.equal(result.error, 'oversized_response');
  assert.equal(result.body.length, 0);
}));
test('invalid token stops before opening a connection or creating a marker', async () => fixture(async marker => {
  const m = mock(200);
  const result = await sendLicenseOnce(challenge, 'invalid', marker, m.request);
  assert.equal(result.attempted, false);
  assert.equal(m.calls.length, 0);
  assert.equal(fs.existsSync(marker), false);
}));
