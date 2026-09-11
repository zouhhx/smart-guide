import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { AdminAccountStore, ADMIN_PERMISSIONS } from '../src/auth/AdminAccountStore.js';

function makeStore() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'admin-accounts-'));
  return new AdminAccountStore({
    filePath: path.join(directory, 'accounts.json'),
    defaultPassword: 'initial-pass',
    sessionTtlMs: 60_000,
  });
}

test('bootstraps a protected super administrator with hashed password', () => {
  const store = makeStore();
  const session = store.login('admin', 'initial-pass');
  assert.equal(session.account.role, 'super_admin');
  assert.deepEqual(session.account.permissions, ADMIN_PERMISSIONS);
  assert.equal(session.account.protected, true);
  assert.equal(JSON.stringify(store.accounts).includes('initial-pass'), false);
});

test('super administrator can create, update and delete an account', () => {
  const store = makeStore();
  const token = store.login('admin', 'initial-pass').token;
  const created = store.create(token, {
    username: 'operator1',
    displayName: '现场运维',
    password: 'operator-pass',
    role: 'operator',
  });
  assert.equal(store.login('operator1', 'operator-pass').account.displayName, '现场运维');
  assert.equal(created.permissions.includes('avatar.manage'), true);

  const updated = store.update(token, created.id, {
    displayName: '二号屏运维',
    permissions: ['knowledge.view', 'clients.manage'],
    password: 'changed-pass',
  });
  assert.equal(updated.displayName, '二号屏运维');
  assert.deepEqual(updated.permissions, ['knowledge.view', 'clients.manage']);
  assert.throws(() => store.login('operator1', 'operator-pass'), /账号或密码错误/);
  assert.equal(store.login('operator1', 'changed-pass').account.id, created.id);

  assert.deepEqual(store.delete(token, created.id), { ok: true });
  assert.throws(() => store.login('operator1', 'changed-pass'), /账号或密码错误/);
});

test('non account administrators cannot manage accounts', () => {
  const store = makeStore();
  const superToken = store.login('admin', 'initial-pass').token;
  store.create(superToken, {
    username: 'reviewer1',
    password: 'reviewer-pass',
    role: 'reviewer',
  });
  const reviewerToken = store.login('reviewer1', 'reviewer-pass').token;
  assert.throws(() => store.list(reviewerToken), /没有此操作权限/);
  assert.throws(() => store.delete(superToken, store.accounts[0].id), /不能删除/);
});
