import fs from 'fs';
import path from 'path';
import { config } from '../config/index.js';

const CITY_ADCODE = {
  北京: '110000',
  上海: '310000',
  天津: '120000',
  重庆: '500000',
  南京: '320100',
  南京建邺: '320105',
  南京市建邺区: '320105',
  建邺: '320105',
  建邺区: '320105',
  西安: '610100',
  苏州: '320500',
  杭州: '330100',
  广州: '440100',
  深圳: '440300',
  成都: '510100',
  武汉: '420100',
  郑州: '410100',
  合肥: '340100',
  济南: '370100',
  青岛: '370200',
  厦门: '350200',
  福州: '350100',
  南昌: '360100',
  长沙: '430100',
};

class WeatherService {
  constructor() {
    this.cacheFile = config.WEATHER.cacheFile;
    this.cache = this._loadCache();
    this._refreshing = new Map();
    this._timer = null;
  }

  start() {
    if (!config.WEATHER.enabled) return;
    this.refreshCity(config.WEATHER.defaultCity).catch(err => {
      console.warn(`[Weather] 默认城市天气刷新失败: ${err.message}`);
    });
    this._timer = setInterval(() => {
      this.refreshCity(config.WEATHER.defaultCity).catch(err => {
        console.warn(`[Weather] 定时天气刷新失败: ${err.message}`);
      });
    }, Math.max(1, config.WEATHER.refreshIntervalHours) * 60 * 60 * 1000);
  }

  stop() {
    if (this._timer) clearInterval(this._timer);
    this._timer = null;
  }

  detect(text) {
    const value = String(text || '').replace(/\s+/g, '');
    if (!value || !/(天气|气温|温度|下雨|降雨|冷不冷|热不热|穿衣|空气质量|风力)/.test(value)) {
      return null;
    }

    const dayOffset = /后天/.test(value) ? 2 : /明天/.test(value) ? 1 : 0;
    const city = this._extractCity(value) || config.WEATHER.defaultCity;
    return { city, dayOffset };
  }

  async answer(intent) {
    if (!config.WEATHER.enabled) {
      return { ok: false, reply: '天气服务未启用。' };
    }

    const city = intent?.city || config.WEATHER.defaultCity;
    const dayOffset = Number.isFinite(intent?.dayOffset) ? intent.dayOffset : 0;
    let data = null;
    try {
      data = await this.getWeather(city);
    } catch (err) {
      const cached = this.cache.cities?.[this._cityKey(city)];
      if (cached) {
        data = { ...cached, fromCache: true, stale: true };
      } else {
        return { ok: false, reply: '天气信息暂时获取失败。' };
      }
    }
    if (!data?.ok) {
      return { ok: false, reply: data?.error || '天气信息暂时获取失败。' };
    }

    const reply = this._formatAnswer(data, dayOffset);
    return {
      ok: true,
      reply,
      city: data.city,
      cachedAt: data.cachedAt,
      fromCache: data.fromCache === true,
    };
  }

  async getWeather(city) {
    const key = this._cityKey(city);
    const cached = this.cache.cities?.[key];
    const expectedAdcode = this._expectedAdcode(city);
    const cachedMatchesCity = !expectedAdcode || cached?.adcode === expectedAdcode;
    if (cached && cachedMatchesCity && !this._isStale(cached.cachedAt)) {
      return { ...cached, fromCache: true };
    }
    return this.refreshCity(city);
  }

  async refreshCity(city) {
    const normalizedCity = this._normalizeCity(city || config.WEATHER.defaultCity);
    const key = this._cityKey(normalizedCity);
    if (this._refreshing.has(key)) return this._refreshing.get(key);

    const task = this._fetchAmapWeather(normalizedCity)
      .then(data => {
        this.cache.cities = this.cache.cities || {};
        this.cache.cities[key] = data;
        this.cache.updatedAt = new Date().toISOString();
        this._saveCache();
        return data;
      })
      .finally(() => this._refreshing.delete(key));

    this._refreshing.set(key, task);
    return task;
  }

  async _fetchAmapWeather(city) {
    const liveUrl = this._amapUrl(city, 'base');
    const forecastUrl = this._amapUrl(city, 'all');
    const liveJson = await this._fetchJson(liveUrl);
    await this._sleep(250);
    const forecastJson = await this._fetchJson(forecastUrl);

    const live = liveJson.lives?.[0] || null;
    const forecast = forecastJson.forecasts?.[0] || null;
    if (!live && !forecast) {
      const info = liveJson.info || forecastJson.info || '高德天气没有返回有效数据';
      throw new Error(info);
    }

    const resolvedCity = live?.city || forecast?.city || city;
    return {
      ok: true,
      provider: 'amap',
      keyName: config.WEATHER.keyName,
      city: resolvedCity,
      province: live?.province || forecast?.province || '',
      adcode: live?.adcode || forecast?.adcode || '',
      live: live ? {
        weather: live.weather,
        temperature: live.temperature,
        winddirection: live.winddirection,
        windpower: live.windpower,
        humidity: live.humidity,
        reporttime: live.reporttime,
      } : null,
      casts: Array.isArray(forecast?.casts) ? forecast.casts.map(item => ({
        date: item.date,
        week: item.week,
        dayweather: item.dayweather,
        nightweather: item.nightweather,
        daytemp: item.daytemp,
        nighttemp: item.nighttemp,
        daywind: item.daywind,
        nightwind: item.nightwind,
        daypower: item.daypower,
        nightpower: item.nightpower,
      })) : [],
      cachedAt: new Date().toISOString(),
    };
  }

  async _fetchJson(url) {
    let lastError = null;
    for (let attempt = 0; attempt < 3; attempt++) {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), config.WEATHER.timeoutMs);
      try {
        const response = await fetch(url, { signal: controller.signal });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const json = await response.json();
        if (json.status !== '1') {
          throw new Error(json.info || json.infocode || '高德天气请求失败');
        }
        return json;
      } catch (err) {
        lastError = err;
        if (!/CUQPS|QPS|ACCESS_TOO_FREQUENT|USER_DAILY_QUERY_OVER_LIMIT/i.test(err.message) || attempt >= 2) {
          throw err;
        }
        await this._sleep(1000 * (attempt + 1));
      } finally {
        clearTimeout(timeout);
      }
    }
    throw lastError || new Error('高德天气请求失败');
  }

  _amapUrl(city, extensions) {
    const params = new URLSearchParams({
      key: config.WEATHER.key,
      city: this._amapCityParam(city),
      extensions,
      output: 'json',
    });
    return `https://restapi.amap.com/v3/weather/weatherInfo?${params.toString()}`;
  }

  _formatAnswer(data, dayOffset) {
    if (dayOffset > 0 && data.casts?.[dayOffset]) {
      const cast = data.casts[dayOffset];
      const label = dayOffset === 1 ? '明天' : '后天';
      const weather = cast.dayweather === cast.nightweather
        ? cast.dayweather
        : `${cast.dayweather}转${cast.nightweather}`;
      const wind = cast.daywind && cast.daywind !== '无风向'
        ? `${cast.daywind}风${cast.daypower}级`
        : `${cast.daypower}级风`;
      return `${data.city}${label}${weather}，${cast.nighttemp}到${cast.daytemp}度，${wind}。`;
    }

    const live = data.live;
    if (live) {
      const wind = live.winddirection && live.winddirection !== '无风向'
        ? `${live.winddirection}风${live.windpower}级`
        : `${live.windpower}级风`;
      return `${data.city}现在${live.weather}，${live.temperature}度，湿度${live.humidity}%，${wind}。`;
    }

    const cast = data.casts?.[0];
    if (cast) {
      const weather = cast.dayweather === cast.nightweather
        ? cast.dayweather
        : `${cast.dayweather}转${cast.nightweather}`;
      return `${data.city}今天${weather}，${cast.nighttemp}到${cast.daytemp}度。`;
    }

    return `${data.city}天气信息暂时不完整。`;
  }

  _extractCity(value) {
    let text = String(value || '')
      .replace(/(今天|今日|现在|当前|实时|明天|后天|未来|这周|本周|天气|气温|温度|怎么样|如何|查询|查一下|告诉我|的)/g, '')
      .replace(/[？?！!。,.，、]/g, '')
      .trim();
    if (!text) return '';
    const match = text.match(/[\u4e00-\u9fa5]{2,10}/);
    return match ? this._normalizeCity(match[0]) : '';
  }

  _normalizeCity(city) {
    const normalized = String(city || '').replace(/\s+/g, '').replace(/市$/, '').trim();
    const mojibakeMap = {
      '鍗椾含': '南京',
    };
    return mojibakeMap[normalized] || normalized;
  }

  _amapCityParam(city) {
    return this._expectedAdcode(city) || this._normalizeCity(city);
  }

  _expectedAdcode(city) {
    const normalized = this._normalizeCity(city);
    const noShi = normalized.replace(/市/g, '');
    const noSuffix = normalized.replace(/[区县]$/g, '');
    return CITY_ADCODE[normalized] || CITY_ADCODE[noShi] || CITY_ADCODE[noSuffix] || '';
  }

  _cityKey(city) {
    return this._normalizeCity(city).toLowerCase();
  }

  _sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  _isStale(cachedAt) {
    const time = Date.parse(cachedAt || '');
    if (!Number.isFinite(time)) return true;
    return Date.now() - time > Math.max(1, config.WEATHER.refreshIntervalHours) * 60 * 60 * 1000;
  }

  _loadCache() {
    try {
      if (!fs.existsSync(this.cacheFile)) return { cities: {} };
      return JSON.parse(fs.readFileSync(this.cacheFile, 'utf8')) || { cities: {} };
    } catch (err) {
      console.warn(`[Weather] 读取缓存失败: ${err.message}`);
      return { cities: {} };
    }
  }

  _saveCache() {
    fs.mkdirSync(path.dirname(this.cacheFile), { recursive: true });
    fs.writeFileSync(this.cacheFile, JSON.stringify(this.cache, null, 2), 'utf8');
  }
}

export const weatherService = new WeatherService();
export default weatherService;
