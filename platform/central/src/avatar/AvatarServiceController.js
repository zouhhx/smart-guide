import { execFile as execFileCallback } from 'node:child_process';

const DEFAULT_TASK_NAME = 'ExhibitionMofaAvatarService';
const DEFAULT_SERVICE_URL = 'http://127.0.0.1:19200';

function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

export class AvatarServiceController {
  constructor(options = {}) {
    this.taskName = options.taskName || process.env.AVATAR_CONTROL_TASK_NAME || DEFAULT_TASK_NAME;
    this.serviceUrl = String(options.serviceUrl || process.env.AVATAR_SERVICE_URL || DEFAULT_SERVICE_URL).replace(/\/+$/, '');
    this.platform = options.platform || process.platform;
    this.execFile = options.execFile || execFileCallback;
    this.fetch = options.fetch || globalThis.fetch;
  }

  async status() {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 1800);
    try {
      const response = await this.fetch(`${this.serviceUrl}/health`, {
        method: 'GET',
        cache: 'no-store',
        signal: controller.signal,
      });
      const health = await response.json().catch(() => ({}));
      if (!response.ok || health?.ok === false) {
        throw new Error(health?.error || `HTTP ${response.status}`);
      }
      return {
        ok: true,
        running: true,
        clientOnline: health?.online === true,
        taskName: this.taskName,
        health,
      };
    } catch (error) {
      return {
        ok: true,
        running: false,
        clientOnline: false,
        taskName: this.taskName,
        error: error?.name === 'AbortError' ? '数字人控制服务响应超时' : String(error?.message || error),
      };
    } finally {
      clearTimeout(timer);
    }
  }

  async control(action) {
    if (!['start', 'stop'].includes(action)) throw new Error('unsupported avatar service action');
    if (this.platform !== 'win32') throw new Error('avatar service control is only available on Windows');

    if (action === 'start') {
      const script = [
        `$taskName = '${this.taskName.replace(/'/g, "''")}'`,
        'Enable-ScheduledTask -TaskName $taskName | Out-Null',
        'Start-ScheduledTask -TaskName $taskName',
        'Start-Sleep -Milliseconds 300',
        'Disable-ScheduledTask -TaskName $taskName | Out-Null',
      ].join('; ');
      await this._exec('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', script]);
      return this._waitForRunning(true);
    }

    await this._exec('schtasks.exe', ['/End', '/TN', this.taskName]);
    return this._waitForRunning(false);
  }

  _exec(file, args) {
    return new Promise((resolve, reject) => {
      this.execFile(file, args, { windowsHide: true }, (error, stdout, stderr) => {
        if (error) {
          reject(new Error(String(stderr || stdout || error.message).trim()));
          return;
        }
        resolve({ stdout, stderr });
      });
    });
  }

  async _waitForRunning(expected) {
    let result = await this.status();
    for (let attempt = 0; attempt < 12 && result.running !== expected; attempt += 1) {
      await delay(250);
      result = await this.status();
    }
    if (result.running !== expected) {
      throw new Error(expected ? '数字人控制服务启动后未响应' : '数字人控制服务未能停止');
    }
    return { ...result, actionAccepted: true };
  }
}

export const avatarServiceController = new AvatarServiceController();
export default avatarServiceController;
