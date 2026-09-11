import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { config } from '../config/index.js';

export const ADMIN_PERMISSIONS = [
  'knowledge.view',
  'knowledge.manage',
  'records.view',
  'clients.manage',
  'avatar.manage',
  'publish.manage',
  'accounts.manage',
];

const ROLE_DEFAULTS = {
  viewer: ['knowledge.view', 'records.view'],
  operator: ['knowledge.view', 'records.view', 'clients.manage', 'avatar.manage'],
  reviewer: ['knowledge.view', 'knowledge.manage', 'records.view', 'publish.manage'],
  admin: ADMIN_PERMISSIONS.filter(permission => permission !== 'accounts.manage'),
  super_admin: ADMIN_PERMISSIONS,
};

function nowIso() {
  return new Date().toISOString();
}

function normalizeUsername(value) {
  return String(value || '').trim().toLowerCase();
}

function publicAccount(account) {
  if (!account) return null;
  return {
    id: account.id,
    username: account.username,
    displayName: account.displayName,
    role: account.role,
    permissions: [...account.permissions],
    enabled: account.enabled !== false,
    protected: account.protected === true,
    mustChangePassword: account.mustChangePassword === true,
    createdAt: account.createdAt,
    updatedAt: account.updatedAt,
  };
}

export class AdminAccountStore {
  constructor(options = {}) {
    this.filePath = options.filePath || path.join(config.DATA_DIR, 'admin-accounts.json');
    this.defaultPassword = options.defaultPassword || process.env.ADMIN_DEFAULT_PASSWORD || 'admin123456';
    this.sessionTtlMs = Number(options.sessionTtlMs || 12 * 60 * 60 * 1000);
    this.sessions = new Map();
    this.accounts = [];
    this._load();
  }

  get permissionCatalog() {
    return [...ADMIN_PERMISSIONS];
  }

  get roleDefaults() {
    return Object.fromEntries(Object.entries(ROLE_DEFAULTS).map(([role, permissions]) => [role, [...permissions]]));
  }

  login(username, password) {
    const account = this.accounts.find(item => item.username === normalizeUsername(username));
    if (!account || account.enabled === false || !this._verifyPassword(account, password)) {
      throw new Error('账号或密码错误');
    }
    const token = crypto.randomBytes(32).toString('base64url');
    this.sessions.set(token, { accountId: account.id, expiresAt: Date.now() + this.sessionTtlMs });
    return { token, expiresAt: Date.now() + this.sessionTtlMs, account: publicAccount(account) };
  }

  logout(token) {
    if (token) this.sessions.delete(token);
  }

  authenticate(token) {
    const session = this.sessions.get(String(token || ''));
    if (!session) return null;
    if (session.expiresAt <= Date.now()) {
      this.sessions.delete(String(token));
      return null;
    }
    const account = this.accounts.find(item => item.id === session.accountId && item.enabled !== false);
    return publicAccount(account);
  }

  require(token, permission) {
    const account = this.authenticate(token);
    if (!account) throw Object.assign(new Error('请先登录管理账号'), { statusCode: 401 });
    if (permission && !account.permissions.includes(permission)) {
      throw Object.assign(new Error('当前账号没有此操作权限'), { statusCode: 403 });
    }
    return account;
  }

  list(token) {
    this.require(token, 'accounts.manage');
    return this.accounts.map(publicAccount);
  }

  create(token, input = {}) {
    this.require(token, 'accounts.manage');
    const username = normalizeUsername(input.username);
    const password = String(input.password || '');
    if (!/^[a-z0-9_.-]{3,32}$/.test(username)) throw new Error('账号需为3-32位字母、数字、点、下划线或横线');
    if (this.accounts.some(item => item.username === username)) throw new Error('账号已存在');
    if (password.length < 6) throw new Error('密码至少需要6位');
    const role = ROLE_DEFAULTS[input.role] ? input.role : 'viewer';
    const account = {
      id: crypto.randomUUID(),
      username,
      displayName: String(input.displayName || username).trim().slice(0, 40),
      role,
      permissions: this._normalizePermissions(input.permissions || ROLE_DEFAULTS[role]),
      enabled: input.enabled !== false,
      protected: false,
      mustChangePassword: input.mustChangePassword !== false,
      createdAt: nowIso(),
      updatedAt: nowIso(),
      ...this._passwordFields(password),
    };
    this.accounts.push(account);
    this._save();
    return publicAccount(account);
  }

  update(token, id, input = {}) {
    const actor = this.require(token, 'accounts.manage');
    const account = this.accounts.find(item => item.id === String(id || ''));
    if (!account) throw Object.assign(new Error('账号不存在'), { statusCode: 404 });
    if (account.protected && input.enabled === false) throw new Error('超级管理员账号不能停用');
    if (account.protected && input.role && input.role !== 'super_admin') throw new Error('超级管理员角色不能修改');
    if (input.displayName !== undefined) account.displayName = String(input.displayName || account.username).trim().slice(0, 40);
    if (input.role !== undefined && !account.protected) account.role = ROLE_DEFAULTS[input.role] ? input.role : account.role;
    if (input.permissions !== undefined && !account.protected) account.permissions = this._normalizePermissions(input.permissions);
    if (input.enabled !== undefined && !account.protected) account.enabled = input.enabled !== false;
    if (input.password !== undefined && String(input.password)) {
      if (String(input.password).length < 6) throw new Error('密码至少需要6位');
      Object.assign(account, this._passwordFields(String(input.password)), { mustChangePassword: false });
      if (actor.id !== account.id) this._revokeAccountSessions(account.id);
    }
    account.updatedAt = nowIso();
    this._save();
    return publicAccount(account);
  }

  delete(token, id) {
    const actor = this.require(token, 'accounts.manage');
    const index = this.accounts.findIndex(item => item.id === String(id || ''));
    if (index < 0) throw Object.assign(new Error('账号不存在'), { statusCode: 404 });
    const account = this.accounts[index];
    if (account.protected) throw new Error('超级管理员账号不能删除');
    if (account.id === actor.id) throw new Error('不能删除当前登录账号');
    this.accounts.splice(index, 1);
    this._revokeAccountSessions(account.id);
    this._save();
    return { ok: true };
  }

  _load() {
    try {
      const parsed = JSON.parse(fs.readFileSync(this.filePath, 'utf8'));
      this.accounts = Array.isArray(parsed.accounts) ? parsed.accounts : [];
    } catch {
      this.accounts = [];
    }
    if (!this.accounts.some(account => account.protected && account.role === 'super_admin')) {
      const createdAt = nowIso();
      this.accounts.unshift({
        id: crypto.randomUUID(),
        username: 'admin',
        displayName: '超级管理员',
        role: 'super_admin',
        permissions: [...ADMIN_PERMISSIONS],
        enabled: true,
        protected: true,
        mustChangePassword: true,
        createdAt,
        updatedAt: createdAt,
        ...this._passwordFields(this.defaultPassword),
      });
      this._save();
    }
  }

  _save() {
    fs.mkdirSync(path.dirname(this.filePath), { recursive: true });
    const temporary = `${this.filePath}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify({ version: 1, accounts: this.accounts }, null, 2), 'utf8');
    fs.renameSync(temporary, this.filePath);
  }

  _normalizePermissions(values) {
    const input = Array.isArray(values) ? values : [];
    return ADMIN_PERMISSIONS.filter(permission => input.includes(permission));
  }

  _passwordFields(password) {
    const salt = crypto.randomBytes(16).toString('hex');
    return { passwordSalt: salt, passwordHash: crypto.scryptSync(password, salt, 64).toString('hex') };
  }

  _verifyPassword(account, password) {
    try {
      const actual = crypto.scryptSync(String(password || ''), account.passwordSalt, 64);
      const expected = Buffer.from(account.passwordHash, 'hex');
      return actual.length === expected.length && crypto.timingSafeEqual(actual, expected);
    } catch {
      return false;
    }
  }

  _revokeAccountSessions(accountId) {
    for (const [token, session] of this.sessions) {
      if (session.accountId === accountId) this.sessions.delete(token);
    }
  }
}

export const adminAccountStore = new AdminAccountStore();
export default adminAccountStore;
