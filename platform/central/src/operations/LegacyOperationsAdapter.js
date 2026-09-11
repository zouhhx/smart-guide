import { deviceManager } from '../devices/DeviceManager.js';
import { wsServer } from '../ws/WsServer.js';
import { avatarTargetCoordinator } from '../avatar/AvatarTargetCoordinator.js';
import { ttsService } from '../tts/TtsService.js';
import { ttsConfigStore } from '../tts/TtsConfigStore.js';
import { isClientMediaAvailable, mediaLibrary } from '../media/MediaLibrary.js';

export const SCREEN_COMMANDS = Object.freeze([
  'home',
  'blackout',
  'next_slide',
  'prev_slide',
  'goto_slide',
  'close_ppt',
]);

export const MEDIA_COMMANDS = Object.freeze([
  'play_ppt',
  'play_video',
  'show_doc',
]);

const SCREEN_COMMAND_SET = new Set(SCREEN_COMMANDS);
const MEDIA_COMMAND_SET = new Set(MEDIA_COMMANDS);

export class LegacyOperationsAdapter {
  constructor(options = {}) {
    this.deviceManager = options.deviceManager || deviceManager;
    this.wsServer = options.wsServer || wsServer;
    this.avatarTargetCoordinator = options.avatarTargetCoordinator || avatarTargetCoordinator;
    this.ttsService = options.ttsService || ttsService;
    this.ttsConfigStore = options.ttsConfigStore || ttsConfigStore;
    this.mediaLibrary = options.mediaLibrary || mediaLibrary;
  }

  handler(capability) {
    const handlers = {
      'avatar.speak': request => this._avatarSpeak(request),
      'tts.speak': request => this._ttsSpeak(request),
      'screen.command': request => this._screenCommand(request),
      'media.command': request => this._mediaCommand(request),
      'monitor.status': request => this._monitorStatus(request),
    };
    if (!handlers[capability]) throw new Error(`unsupported operations capability: ${capability}`);
    return handlers[capability];
  }

  async _avatarSpeak(request) {
    const selected = this.avatarTargetCoordinator.getTargetStatus();
    const requestedTarget = String(request.target || 'all');
    if (!['all', 'selected', String(selected.target)].includes(requestedTarget)) {
      return {
        status: 'REJECTED',
        code: 'AVATAR_TARGET_NOT_SELECTED',
        message: `avatar target is managed by operations console and currently set to ${selected.target}`,
      };
    }
    const result = await this.avatarTargetCoordinator.say(request.params.text);
    return {
      data: { target: selected.target, screenNumber: result.screenNumber || null, deduplicated: result.deduplicated === true },
      message: 'digital human speech accepted',
    };
  }

  async _ttsSpeak(request) {
    const state = this.ttsConfigStore.get();
    const provider = request.params.provider || state.provider;
    const voice = request.params.voice || request.params.speaker || state.voice;
    const audio = await this.ttsService.synthesize(request.params.text, voice, { provider });
    const delivery = request.params.delivery || 'screen';
    let delivered = 0;
    if (delivery === 'screen') {
      const dispatch = this._dispatch(request.target, 'speak', {
        text: request.params.text,
        audioUrl: audio.url,
        voice: audio.voice,
        provider: audio.provider,
      }, request);
      if (!dispatch.ok) return { status: 'REJECTED', code: dispatch.code, message: dispatch.message };
      delivered = dispatch.delivered;
    } else if (delivery !== 'url') {
      return { status: 'REJECTED', code: 'INVALID_DELIVERY', message: 'delivery must be screen or url' };
    }
    return { data: { audio, delivery, delivered }, message: 'tts speech prepared' };
  }

  async _screenCommand(request) {
    const command = String(request.params.command || '');
    if (!SCREEN_COMMAND_SET.has(command)) {
      return { status: 'REJECTED', code: 'COMMAND_NOT_ALLOWED', message: `screen command ${command} is not allowed` };
    }
    const params = { ...request.params };
    delete params.command;
    if (command === 'goto_slide') {
      const slide = Number.parseInt(params.slide || params.page, 10);
      if (!Number.isFinite(slide) || slide <= 0) {
        return { status: 'REJECTED', code: 'SLIDE_REQUIRED', message: 'positive slide is required' };
      }
      params.slide = slide;
    }
    const dispatch = this._dispatch(request.target, command, params, request);
    if (!dispatch.ok) return { status: 'REJECTED', code: dispatch.code, message: dispatch.message };
    return { data: { command, delivered: dispatch.delivered }, message: 'screen command dispatched' };
  }

  async _mediaCommand(request) {
    const command = String(request.params.command || '');
    if (!MEDIA_COMMAND_SET.has(command)) {
      return { status: 'REJECTED', code: 'COMMAND_NOT_ALLOWED', message: `media command ${command} is not allowed` };
    }
    const item = this._findMedia(request.params.fileId || request.params.file || request.params.name);
    if (!item) {
      return { status: 'REJECTED', code: 'MEDIA_NOT_FOUND', message: 'media must exist in the managed media library' };
    }
    const expected = item.type === 'ppt' ? 'play_ppt' : item.type === 'image' ? 'show_doc' : 'play_video';
    if (command !== expected) {
      return { status: 'REJECTED', code: 'MEDIA_COMMAND_MISMATCH', message: `${item.type} media requires ${expected}` };
    }
    const dispatch = this._dispatch(request.target, command, {
      file: item.localPath || item.url || item.name,
      name: item.name,
    }, request);
    if (!dispatch.ok) return { status: 'REJECTED', code: dispatch.code, message: dispatch.message };
    return { data: { command, delivered: dispatch.delivered, media: { id: item.id, name: item.name, type: item.type } }, message: 'media command dispatched' };
  }

  async _monitorStatus() {
    return {
      data: {
        devices: this.deviceManager.getSummary(),
        avatar: this.avatarTargetCoordinator.getTargetStatus(),
        tts: this.ttsConfigStore.get(),
      },
      message: 'operations status ready',
    };
  }

  _findMedia(value) {
    const key = String(value || '').trim();
    if (!key) return null;
    return this.mediaLibrary.getAll().filter(isClientMediaAvailable).find(item =>
      String(item.id || '') === key
      || item.name === key
      || item.path === key
      || item.title === key
    ) || null;
  }

  _dispatch(target, action, params, request) {
    const command = {
      type: 'command',
      action,
      params,
      text: params.text || '',
      from: `operations:${request.actor.id}`,
      requestId: request.requestId,
      eventId: request.eventId,
      serverTime: Date.now(),
    };
    if (String(target) === 'all') {
      const delivered = this.wsServer.pushToClient(command);
      return delivered > 0
        ? { ok: true, delivered }
        : { ok: false, delivered: 0, code: 'NO_ONLINE_SCREEN', message: 'no online display client' };
    }
    const screenNumber = Number.parseInt(target, 10);
    if (!Number.isFinite(screenNumber) || screenNumber <= 0) {
      return { ok: false, delivered: 0, code: 'INVALID_SCREEN_TARGET', message: 'target must be all or a positive screen number' };
    }
    const device = this.deviceManager.getByScreenNumber(screenNumber);
    if (!device?.online) {
      return { ok: false, delivered: 0, code: 'SCREEN_OFFLINE', message: `${screenNumber} screen is offline` };
    }
    const delivered = this.wsServer.sendToDevice(device.id, command) ? 1 : 0;
    return delivered
      ? { ok: true, delivered }
      : { ok: false, delivered: 0, code: 'SCREEN_DELIVERY_FAILED', message: 'screen command delivery failed' };
  }
}

export default LegacyOperationsAdapter;
