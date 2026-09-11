export function classifyCurrentDateTimeQuery(text) {
  const normalized = String(text || '')
    .replace(/\s+/g, '')
    .replace(/[？?。！!，,]/g, '');
  const eventTimeQuery =
    /(开球|开踢|比赛|决赛|开幕|开始|举行|直播|上映|起飞|到达|截止).{0,16}(几点|时间|日期|什么时候)/.test(normalized)
    || /(几点|时间|日期|什么时候).{0,16}(开球|开踢|比赛|决赛|开幕|开始|举行|直播|上映|起飞|到达|截止)/.test(normalized);
  if (eventTimeQuery) {
    return {
      matched: false,
      reason: 'EVENT_TIME_QUERY',
      asksWeekday: false,
      asksSolarDate: false,
      asksLunarDate: false,
      asksTime: false,
    };
  }

  const asksWeekday = /(星期几|周几|礼拜几|今天星期|今天周|今天礼拜|今天是星期|今天是周|今天是礼拜)/.test(normalized);
  const asksSolarDate = /(今天几号|今天日期|今天的日期|现在日期|现在的日期|今天是什么日子|今天是什么日期|现在是几号|日期是多少|今天多少号|今天是几号|今天是几月几号|今天几月几号|今天几月多少号|今天是几月多少号|今天公历|今天阳历|公历几号|阳历几号|公历日期|阳历日期)/.test(normalized);
  const asksLunarDate = /(农历|阴历|旧历|lunar)/i.test(normalized)
    && /(今天|现在|日期|几号|多少号|几月|是什么日子|是多少)/.test(normalized);
  const timeAnswer = '(?:多少|几点(?:了|钟)?|什么时间)';
  const asksTime = /^(现在|当前|此刻)(是)?几点(了|钟)?$/.test(normalized)
    || new RegExp(`^(现在|当前|此刻)(?:的)?(?:北京|中国标准|中国)?时间(?:是|为)?${timeAnswer}?$`).test(normalized)
    || new RegExp(`^(?:北京|中国标准|中国)时间(?:现在|当前|此刻)?(?:是|为)?${timeAnswer}?$`).test(normalized)
    || /^(现在的时间|当前时间|现在时间)(是|为)?(多少|几点(了|钟)?|什么时间)?$/.test(normalized);
  return {
    matched: asksWeekday || asksSolarDate || asksLunarDate || asksTime,
    reason: asksWeekday || asksSolarDate || asksLunarDate || asksTime ? 'CURRENT_DATETIME_QUERY' : 'NO_MATCH',
    asksWeekday,
    asksSolarDate,
    asksLunarDate,
    asksTime,
  };
}

export default classifyCurrentDateTimeQuery;
