'use strict';
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const state = {
  config: null, data: null, revision: -1, wantedRevision: -1, page: 1, pageSize: 25,
  total: 0, image: 'overlay', sequence: 0, controller: null, chart: null, latest: null, refreshTimer: null,
  range: 'today', connection: false, health: null, heatMode: 'both', heatSelection: null,
  heatHover: null, heatPinned: false, heatFocusLane: 'left', heatCells: new Map(), heatPairs: new Map(),
  // กราฟแนวโน้ม: span = 'all' (ตามช่วงที่เลือกด้านบน) | วินาทีของปุ่ม 1/3/6/12 ชม. | 'custom' | 'window' (ลากซูม/เลื่อน)
  trend: {span: 'all', from: null, to: null, follow: false}, trendController: null, trendSequence: 0,
  // ประวัติย้อนหลังมีช่วงวัน/เวลาของตัวเอง range = 'today' | 'yesterday' | '7' | 'custom'
  history: {range: 'today', controller: null, sequence: 0}
};

const lightbox = {
  isOpen: false,
  scale: 1,
  minScale: 0.5,
  maxScale: 4.0,
  translateX: 0,
  translateY: 0,
  isPanning: false,
  startX: 0,
  startY: 0,
  sourceImage: 'overlay',
  currentFrame: null
};

const labels = {left: 'ฝั่งซ้าย', right: 'ฝั่งขวา', clear: 'โล่ง', moderate: 'ปานกลาง', congested: 'ติด'};
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const percent = value => value === null || value === undefined ? '—' : (value*100).toFixed(1)+'%';
const dateTime = value => value ? new Intl.DateTimeFormat('th-TH', {timeZone:'Asia/Bangkok', day:'numeric', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit', hour12:false}).format(new Date(value)) : '—';
const shortTime = value => new Intl.DateTimeFormat('en-GB', {timeZone:'Asia/Bangkok', hour:'2-digit', minute:'2-digit', hour12:false}).format(new Date(value));
const levelChip = level => `<span class="level-chip ${escapeHtml(level)}">${labels[level] || '—'}</span>`;

function bangkokDay(){
  return new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Bangkok', year:'numeric', month:'2-digit', day:'2-digit'}).format(new Date());
}

function addDays(day, count){
  const date = new Date(day + 'T00:00:00+07:00');
  date.setUTCDate(date.getUTCDate() + count);
  return new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Bangkok', year:'numeric', month:'2-digit', day:'2-digit'}).format(date);
}

function query(){
  const from = $('#date-from').value, to = $('#date-to').value;
  if(!from || !to || to < from) throw new Error('กรุณาเลือกวันที่เริ่มต้นและสิ้นสุดให้ถูกต้อง');
  if((new Date(to) - new Date(from)) / 86400000 > 365) throw new Error('เลือกช่วงเวลาได้ไม่เกิน 366 วัน');
  return new URLSearchParams({from: from + 'T00:00:00+07:00', to: addDays(to, 1) + 'T00:00:00+07:00', lane: $('#lane-filter').value});
}

const FULL_DAY = ['00:00', '23:59'];

function historyQuery(){
  const from = $('#history-from').value, to = $('#history-to').value;
  if(!from || !to || to < from) throw new Error('ประวัติย้อนหลัง: กรุณาเลือกวันที่เริ่มต้นและสิ้นสุดให้ถูกต้อง');
  if((new Date(to) - new Date(from)) / 86400000 > 365) throw new Error('ประวัติย้อนหลัง: เลือกช่วงวันที่ได้ไม่เกิน 366 วัน');
  const timeFrom = $('#history-time-from').value, timeTo = $('#history-time-to').value;
  if(!timeFrom || !timeTo) throw new Error('ประวัติย้อนหลัง: กรุณาเลือกเวลาเริ่มต้นและสิ้นสุด');
  const params = new URLSearchParams({from: from + 'T00:00:00+07:00', to: addDays(to, 1) + 'T00:00:00+07:00',
    lane: $('#lane-filter').value, page: state.page, page_size: state.pageSize});
  if($('#level-filter').value) params.set('level', $('#level-filter').value);
  if(timeFrom !== FULL_DAY[0] || timeTo !== FULL_DAY[1]){
    params.set('time_from', timeFrom);
    params.set('time_to', timeTo);
  }
  return params;
}

function historyFilterLabel(){
  const from = $('#history-from').value, to = $('#history-to').value;
  const day = value => new Intl.DateTimeFormat('th-TH', {timeZone: 'Asia/Bangkok', day: 'numeric', month: 'short', year: 'numeric'}).format(new Date(value + 'T12:00:00+07:00'));
  const days = from === to ? day(from) : `${day(from)} – ${day(to)}`;
  const timeFrom = $('#history-time-from').value, timeTo = $('#history-time-to').value;
  if(timeFrom === FULL_DAY[0] && timeTo === FULL_DAY[1]) return `${days} · ทั้งวัน`;
  const overnight = timeFrom > timeTo ? ' (ข้ามเที่ยงคืน)' : '';
  return `${days} · เฉพาะเวลา ${timeFrom}–${timeTo} น.${from === to ? '' : ' ของทุกวัน'}${overnight}`;
}

function markHistoryPresets(){
  const from = $('#history-from').value, to = $('#history-to').value, today = bangkokDay();
  const range = from === today && to === today ? 'today'
    : (from === addDays(today, -1) && to === from ? 'yesterday' : (from === addDays(today, -6) && to === today ? '7' : 'custom'));
  state.history.range = range;
  $$('[data-history-range]').forEach(button => button.classList.toggle('selected', button.dataset.historyRange === range));
  const time = $('#history-time-from').value + '-' + $('#history-time-to').value;
  $$('[data-history-time]').forEach(button => button.classList.toggle('selected', button.dataset.historyTime === time));
}

function setHistoryDays(range){
  const today = bangkokDay();
  const from = range === 'today' ? today : (range === 'yesterday' ? addDays(today, -1) : addDays(today, 1 - Number(range)));
  $('#history-from').value = from;
  $('#history-to').value = range === 'yesterday' ? from : today;
}

async function loadHistory(){
  let params;
  try { params = historyQuery(); } catch(error) { showError(error); return; }
  markHistoryPresets();
  $('#history-filter-label').textContent = 'กำลังโหลด · ' + historyFilterLabel();
  const sequence = ++state.history.sequence;
  state.history.controller?.abort();
  state.history.controller = new AbortController();
  try {
    const history = await getJSON('/api/history?' + params, state.history.controller.signal);
    if(sequence !== state.history.sequence) return;
    renderHistory(history);
    $('#new-data').hidden = true;
    $('#history-filter-label').textContent = historyFilterLabel();
  } catch(error){
    if(error.name !== 'AbortError'){
      showError(error);
      $('#history-filter-label').textContent = 'โหลดประวัติไม่สำเร็จ · ' + historyFilterLabel();
    }
  }
}

async function getJSON(url, signal){
  const response = await fetch(url, {signal, cache: 'no-store'});
  if(!response.ok){
    let message;
    try { message = (await response.json()).detail; } catch {}
    throw new Error(typeof message === 'string' ? message : 'ไม่สามารถโหลดข้อมูลได้ (' + response.status + ')');
  }
  return response.json();
}

function showError(error){
  $('#error-text').textContent = error.message || String(error);
  $('#error-banner').hidden = false;
}

function chooseRange(range){
  state.range = range;
  $$('[data-range]').forEach(button => button.classList.toggle('selected', button.dataset.range === range));
  if(range === 'custom'){
    $('#date-from').focus();
    return;
  }
  const today = bangkokDay();
  $('#date-to').value = today;
  $('#date-from').value = addDays(today, range === 'today' ? 0 : 1 - Number(range));
  syncHistoryDays();
  state.trend.span = 'all';
  refresh('filter');
}

// เปลี่ยนช่วงวันด้านบน -> ประวัติตามไปด้วย (ช่วงเวลาของวันคงเดิม) แล้วปรับเองต่อในแผงประวัติได้
function syncHistoryDays(){
  $('#history-from').value = $('#date-from').value;
  $('#history-to').value = $('#date-to').value;
  state.page = 1;
}

function scheduleRefresh(){
  clearTimeout(state.refreshTimer);
  state.refreshTimer = setTimeout(() => refresh('live'), 200);
}

async function refresh(reason = 'manual'){
  let params;
  try { params = query(); } catch(error) { showError(error); return; }
  const sequence = ++state.sequence;
  state.controller?.abort();
  state.controller = new AbortController();
  const signal = state.controller.signal;
  const updateHistory = reason !== 'live' || (state.page === 1 && !$('#detail-dialog').open);
  if(updateHistory) loadHistory();
  else if($('#history-to').value >= bangkokDay()) $('#new-data').hidden = false;
  $('#sample-count').textContent = 'กำลังโหลด…';
  try {
    const data = await getJSON('/api/dashboard?' + params, signal);
    if(sequence !== state.sequence) return;
    state.data = data;
    state.revision = data.revision;
    $('#error-banner').hidden = true;
    renderLatest(data.latest);
    renderSummary(data);
    loadTrend();
    renderHeatmap();
    $('#period-label').textContent = $('#date-from').value === $('#date-to').value
      ? dateTime($('#date-from').value + 'T00:00:00+07:00').split(' เวลา')[0].replace(/00:00.*$/, '')
      : $('#date-from').value + ' → ' + $('#date-to').value;
    $('#sample-count').textContent = `${data.frame_count.toLocaleString()} ภาพในช่วงที่เลือก`;
    if(state.wantedRevision > state.revision) scheduleRefresh();
  } catch(error){
    if(error.name !== 'AbortError'){
      showError(error);
      $('#sample-count').textContent = 'โหลดข้อมูลไม่สำเร็จ';
    }
  }
}

function updateCamera(row){
  state.latest = row;
  const image = $('#camera-image'), placeholder = $('#camera-placeholder');
  $('#camera-timestamp').textContent = row ? dateTime(row.captured_at) : 'NO SNAPSHOT';
  const source = row?.images[state.image];
  if(!source){
    image.hidden = true;
    placeholder.hidden = false;
    placeholder.querySelector('b').textContent = row ? 'ไม่มีไฟล์' + (state.image === 'overlay' ? 'ภาพวิเคราะห์' : 'ภาพต้นฉบับ') : 'ยังไม่มีภาพในระบบ';
    return;
  }
  if(image.dataset.source === source && !image.hidden){
    if(lightbox.isOpen && lightbox.currentFrame === row) updateLightboxImage();
    return;
  }
  image.dataset.source = source;
  image.hidden = true;
  placeholder.hidden = false;
  placeholder.querySelector('b').textContent = 'กำลังโหลดภาพกล้อง';
  const pending = new Image();
  pending.onload = () => {
    if(image.dataset.source !== source) return;
    image.src = source;
    image.hidden = false;
    placeholder.hidden = true;
    if(lightbox.isOpen && lightbox.currentFrame === row) updateLightboxImage();
  };
  pending.onerror = () => {
    if(image.dataset.source !== source) return;
    image.hidden = true;
    placeholder.hidden = false;
    placeholder.querySelector('b').textContent = 'ไม่สามารถเปิดไฟล์ภาพนี้ได้';
  };
  pending.src = source;
}

function renderLatest(rows){
  for(const lane of ['left', 'right']){
    const row = rows.find(item => item.lane === lane), card = $('#metric-' + lane);
    card.querySelector('strong').textContent = percent(row?.occupancy);
    card.querySelector('.level-chip').outerHTML = levelChip(row?.level || '');
    card.querySelector('.meter i').style.width = ((row?.occupancy || 0) * 100) + '%';
    const notes = [];
    if(row?.is_night) notes.push('ภาพกลางคืน');
    if(row && ((row.cam_score !== null && row.cam_score < state.config.quality.min_score) || Math.abs(row.cam_dx) > state.config.quality.shift_tolerance || Math.abs(row.cam_dy) > state.config.quality.shift_tolerance)) {
      notes.push('ตรวจพบกล้องขยับ/จุดอ้างอิงไม่ชัด');
    }
    card.querySelector('.metric-note').textContent = notes.join(' · ');
    const points = state.data.series.filter(item => item.lane === lane).slice(-24).map(item => item.occupancy);
    card.querySelector('.sparkline').innerHTML = points.length > 1
      ? `<polyline points="${points.map((value, index) => `${index * 150 / (points.length - 1)},${38 - value * 36}`).join(' ')}"></polyline>`
      : '';
    card.querySelector('.sparkline').setAttribute('aria-label', 'แนวโน้มช่วงเวลาที่เลือก');
  }
  updateCamera(rows[0] || null);
  renderFreshness();
  if(lightbox.isOpen) renderLightboxMetrics();
}

function renderSummary(data){
  const stats = data.summary;
  const count = stats.reduce((sum, item) => sum + item.sample_count, 0);
  const mean = count ? stats.reduce((sum, item) => sum + item.mean * item.sample_count, 0) / count : null;
  const peak = stats.length ? stats.reduce((a, b) => a.peak > b.peak ? a : b) : null;
  const congested = count ? stats.reduce((sum, item) => sum + item.congested, 0) / count : null;
  const peakHour = stats.filter(item => item.peak_hour).sort((a, b) => b.peak_hour.mean - a.peak_hour.mean)[0];
  const items = [
    ['ความหนาแน่นเฉลี่ย', percent(mean), `${count.toLocaleString()} ตัวอย่าง · ${$('#lane-filter').value === 'all' ? 'รวมสองฝั่ง' : labels[$('#lane-filter').value]}`],
    ['ความหนาแน่นสูงสุด', percent(peak?.peak), peak ? `${labels[peak.lane]} · ${dateTime(peak.peak_at)}` : 'ยังไม่มีข้อมูล'],
    ['สัดส่วนตัวอย่างที่ติด', percent(congested), 'สัดส่วนจำนวนตัวอย่าง ไม่ใช่ระยะเวลารถติด'],
    ['ชั่วโมงที่หนาแน่นที่สุด', peakHour ? peakHour.peak_hour.hour + ':00' : '—', peakHour ? `${labels[peakHour.lane]} · เฉลี่ย ${percent(peakHour.peak_hour.mean)} / ${peakHour.peak_hour.sample_count} ตัวอย่าง` : 'ยังไม่มีข้อมูล']
  ];
  $('#summary').innerHTML = items.map(([title, value, note]) => `<div class="summary-item"><span>${escapeHtml(title)}</span><b>${escapeHtml(value)}</b><small>${escapeHtml(note)}</small></div>`).join('');
}

const thresholdPlugin = {
  id: 'thresholdGuides',
  afterDraw(chart){
    if(!state.config) return;
    const {ctx, chartArea, scales} = chart;
    if(!chartArea) return;
    ctx.save();
    ctx.font = '9px "Noto Sans Thai", Consolas, monospace';
    ctx.textAlign = 'right';
    ctx.fillStyle = '#92929a';
    ctx.strokeStyle = '#bbb9c3';
    ctx.setLineDash([3, 4]);
    for(const value of Object.values(state.config.thresholds)){
      const y = scales.y.getPixelForValue(value * 100);
      ctx.beginPath();
      ctx.moveTo(chartArea.left, y);
      ctx.lineTo(chartArea.right, y);
      ctx.stroke();
      ctx.fillText(Math.round(value * 100) + '%  เกณฑ์อ้างอิง', chartArea.right - 4, y - 5);
    }
    ctx.restore();
  }
};

function renderChart(data){
  if(!window.Chart){ showError(new Error('ไม่พบไลบรารีกราฟ กรุณาตรวจไฟล์ static/vendor')); return; }
  const showDates = Date.parse(data.range.to) - Date.parse(data.range.from) > 2 * 86400000;
  const datasets = ['left', 'right'].map(lane => {
    const values = data.series.filter(item => item.lane === lane), points = [];
    let previous = null;
    const maxGap = Math.max(state.config.interval_sec * 2, data.bucket_seconds * 1.5) * 1000;
    for(const item of values){
      const x = Date.parse(item.timestamp);
      if(previous !== null && x - previous > maxGap) points.push({x: previous + 1, y: null});
      points.push({x, y: item.occupancy * 100, count: item.sample_count});
      previous = x;
    }
    return {
      label: labels[lane],
      data: points,
      borderColor: lane === 'left' ? '#9253f5' : '#19191e',
      borderWidth: 1.8,
      borderDash: lane === 'right' ? [5, 4] : [],
      pointRadius: values.length < 25 ? 2.5 : values.length <= 90 ? 1.6 : 0,
      pointHoverRadius: 5,
      tension: 0,
      spanGaps: false
    };
  });
  const options = {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    parsing: false,
    interaction: {mode: 'nearest', intersect: false},
    plugins: {
      legend: {display: false},
      tooltip: {
        backgroundColor: '#111',
        titleFont: {family: '"Noto Sans Thai", Tahoma, sans-serif', size: 11},
        bodyFont: {family: '"Noto Sans Thai", Tahoma, sans-serif', size: 11},
        callbacks: {
          title: items => dateTime(items[0].parsed.x),
          label: context => `${context.dataset.label}: ${context.parsed.y?.toFixed(1)}% (${context.raw.count || 0} ตัวอย่าง)`
        }
      }
    },
    scales: {
      x: {
        type: 'linear',
        min: Date.parse(data.range.from),
        max: Math.min(Date.parse(data.range.to), Date.now() > Date.parse(data.range.from) ? Date.now() : Date.parse(data.range.to)),
        grid: {color: '#efeff1'},
        ticks: {
          maxTicksLimit: 8,
          font: {family: 'Consolas', size: 9},
          callback: value => showDates
            ? new Intl.DateTimeFormat('en-GB', {timeZone:'Asia/Bangkok', day:'2-digit', month:'2-digit'}).format(new Date(value))
            : shortTime(value)
        }
      },
      y: {
        min: 0,
        max: 100,
        grid: {color: '#e9e9ec'},
        ticks: {stepSize: 20, font: {family: 'Consolas', size: 9}, callback: value => value + '%'}
      }
    }
  };
  if(state.chart){
    state.chart.data.datasets = datasets;
    state.chart.options = options;
    state.chart.update('none');
  } else {
    state.chart = new Chart($('#trend-chart'), {type: 'line', data: {datasets}, options, plugins: [thresholdPlugin]});
  }
  $('#chart-empty').hidden = data.series.length > 0;
  $('#bucket-label').textContent = data.bucket_seconds === 0 ? 'ตามรอบเก็บภาพ' : data.bucket_seconds === 3600 ? 'เฉลี่ยรายชั่วโมง' : 'เฉลี่ยรายวัน';
}

/* ==========================================================================
   TREND WINDOW: เลือกความกว้าง / เลื่อนก่อน-หลัง / กำหนดเอง / ลากซูม
   ช่วงที่ไม่ใช่ 'all' โหลดจาก /api/trend แยก ช่วงสั้นจึงได้ทุกเฟรมแม้ภาพรวมเลือกหลายวัน
   ========================================================================== */
const MIN_TREND_SPAN = 10 * 60 * 1000;
const isPresetSpan = () => /^\d+$/.test(state.trend.span);
const followTolerance = () => 2 * (state.config?.interval_sec || 60) * 1000;

function bangkokInput(ms){
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Bangkok', year:'numeric', month:'2-digit',
    day:'2-digit', hour:'2-digit', minute:'2-digit', hourCycle:'h23'}).formatToParts(new Date(ms)).map(part => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

function trendLabel(from, to){
  const day = ms => new Intl.DateTimeFormat('th-TH', {timeZone:'Asia/Bangkok', day:'numeric', month:'short'}).format(new Date(ms));
  return day(from) === day(to)
    ? `${day(from)} ${shortTime(from)}–${shortTime(to)}`
    : `${day(from)} ${shortTime(from)} – ${day(to)} ${shortTime(to)}`;
}

function currentTrendRange(){
  if(state.trend.span !== 'all') return state.trend.from === null ? null : {from: state.trend.from, to: state.trend.to};
  return state.data ? {from: Date.parse(state.data.range.from), to: Date.parse(state.data.range.to)} : null;
}

function isLatest(range){
  return range.to >= Date.now() - followTolerance();
}

function updateTrendControls(){
  $$('[data-span]').forEach(button => {
    const selected = button.dataset.span === state.trend.span;
    button.classList.toggle('selected', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
  $('#trend-custom').hidden = state.trend.span !== 'custom';
  const range = currentTrendRange();
  if(!range) return;
  $('#trend-window').textContent = trendLabel(range.from, Math.min(range.to, Date.now()));
  $('#trend-next').disabled = isLatest(range);
}

function setTrendWindow(from, to){
  const now = Date.now();
  if(to > now){
    // ปุ่ม 1/3/6/12 ชม. คงความกว้างไว้ ช่วงอื่นตัดปลายที่เวลาปัจจุบัน
    if(isPresetSpan()) from -= to - now;
    to = now;
  }
  if(to - from < MIN_TREND_SPAN) from = to - MIN_TREND_SPAN;
  state.trend.from = Math.round(from);
  state.trend.to = Math.round(to);
  state.trend.follow = to >= now - followTolerance();
  loadTrend();
}

function chooseSpan(span){
  const range = currentTrendRange();
  state.trend.span = span;
  if(span === 'all' || !range){
    state.trend.span = 'all';
    loadTrend();
    return;
  }
  if(span === 'custom'){
    state.trend.from = range.from;
    state.trend.to = Math.min(range.to, Date.now());
    $('#trend-from').value = bangkokInput(state.trend.from);
    $('#trend-to').value = bangkokInput(state.trend.to);
    updateTrendControls();
    $('#trend-from').focus();
    return;
  }
  // กำลังดูช่วงล่าสุด -> ยึดเวลาปัจจุบัน, ดูย้อนหลังอยู่ -> ยึดปลายช่วงที่ดูอยู่
  const end = isLatest(range) ? Date.now() : range.to;
  setTrendWindow(end - Number(span) * 1000, end);
}

function shiftTrend(direction){
  const range = currentTrendRange();
  if(!range) return;
  const width = range.to - range.from;
  if(state.trend.span === 'all' || state.trend.span === 'custom') state.trend.span = 'window';
  setTrendWindow(range.from + direction * width, range.to + direction * width);
}

async function loadTrend(){
  if(!state.data) return;
  if(state.trend.span === 'all'){
    state.trendController?.abort();
    updateTrendControls();
    renderChart(state.data);
    return;
  }
  if(state.trend.follow){
    // ดูช่วงล่าสุดอยู่: เลื่อนตามข้อมูลใหม่ที่เข้ามา
    const now = Date.now();
    if(isPresetSpan()) state.trend.from = now - Number(state.trend.span) * 1000;
    state.trend.to = now;
  }
  updateTrendControls();
  const sequence = ++state.trendSequence;
  state.trendController?.abort();
  state.trendController = new AbortController();
  const params = new URLSearchParams({from: new Date(state.trend.from).toISOString(),
    to: new Date(state.trend.to).toISOString(), lane: $('#lane-filter').value});
  try {
    const data = await getJSON('/api/trend?' + params, state.trendController.signal);
    if(sequence === state.trendSequence) renderChart(data);
  } catch(error){
    if(error.name !== 'AbortError') showError(error);
  }
}

function setupTrendEvents(){
  $$('[data-span]').forEach(button => button.addEventListener('click', () => chooseSpan(button.dataset.span)));
  $('#trend-prev').addEventListener('click', () => shiftTrend(-1));
  $('#trend-next').addEventListener('click', () => shiftTrend(1));
  $('#trend-latest').addEventListener('click', () => {
    const range = currentTrendRange();
    if(!range) return;
    const width = Math.min(range.to, Date.now()) - range.from;
    if(state.trend.span === 'all' || state.trend.span === 'custom') state.trend.span = 'window';
    setTrendWindow(Date.now() - width, Date.now());
  });
  $('#trend-custom').addEventListener('submit', event => {
    event.preventDefault();
    const from = Date.parse($('#trend-from').value + ':00+07:00'), to = Date.parse($('#trend-to').value + ':00+07:00');
    if(!(to > from)){ showError(new Error('เวลาสิ้นสุดต้องหลังเวลาเริ่มต้น')); return; }
    if(to - from > 366 * 86400000){ showError(new Error('เลือกช่วงเวลาได้ไม่เกิน 366 วัน')); return; }
    $('#error-banner').hidden = true;
    setTrendWindow(from, to);
  });

  // ลากบนกราฟ (เมาส์/ปากกา) เพื่อซูมเข้า ช่วงที่ลากแคบกว่า 8 px ถือเป็นคลิกธรรมดา
  const canvas = $('#trend-chart'), brush = $('#trend-brush');
  let start = null;
  const clampX = x => Math.min(Math.max(x, state.chart.chartArea.left), state.chart.chartArea.right);
  canvas.addEventListener('pointerdown', event => {
    const area = state.chart?.chartArea;
    if(!area || event.button !== 0 || event.pointerType === 'touch' || event.offsetX < area.left || event.offsetX > area.right) return;
    start = event.offsetX;
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener('pointermove', event => {
    if(start === null) return;
    const area = state.chart.chartArea, x = clampX(event.offsetX);
    Object.assign(brush.style, {left: canvas.offsetLeft + Math.min(start, x) + 'px', width: Math.abs(x - start) + 'px',
      top: canvas.offsetTop + area.top + 'px', height: area.bottom - area.top + 'px'});
    brush.hidden = Math.abs(x - start) < 3;
  });
  canvas.addEventListener('pointerup', event => {
    if(start === null) return;
    const x = clampX(event.offsetX), scale = state.chart.scales.x, left = Math.min(start, x), right = Math.max(start, x);
    start = null;
    brush.hidden = true;
    if(right - left < 8) return;
    let from = scale.getValueForPixel(left), to = scale.getValueForPixel(right);
    if(to - from < MIN_TREND_SPAN){
      const middle = (from + to) / 2;
      from = middle - MIN_TREND_SPAN / 2;
      to = middle + MIN_TREND_SPAN / 2;
    }
    state.trend.span = 'window';
    setTrendWindow(from, to);
  });
  canvas.addEventListener('pointercancel', () => { start = null; brush.hidden = true; });
  canvas.addEventListener('dblclick', () => chooseSpan('all'));
}

/* ==========================================================================
   HEATMAP LOGIC & BUG FIXES
   ========================================================================== */
function getLatestHeatmapDataHour(){
  if(!state.data) return null;
  const items = state.data.heatmap;
  const lanes = state.heatMode === 'both' ? ['left', 'right'] : [state.heatMode];
  const available = items.filter(item => lanes.includes(item.lane)).sort((a, b) => a.date.localeCompare(b.date) || a.hour - b.hour);
  return available.length ? available.at(-1) : null;
}

function updateHeatResetButton(){
  const btn = $('#heat-reset-btn');
  if(!btn) return;
  const latest = getLatestHeatmapDataHour();
  if(!latest || !state.heatSelection || (state.heatSelection.date === latest.date && state.heatSelection.hour === latest.hour && !state.heatPinned)){
    btn.hidden = true;
  } else {
    btn.hidden = false;
  }
}

function renderHeatmap(){
  if(!state.data) return;
  const items = state.data.heatmap, pairs = state.data.heatmap_comparison || [];
  state.heatCells = new Map(items.map(item => [`${item.date}|${item.hour}|${item.lane}`, item]));
  state.heatPairs = new Map(pairs.map(item => [`${item.date}|${item.hour}`, item]));
  const lanes = state.heatMode === 'both' ? ['left', 'right'] : [state.heatMode];
  const days = [];
  let day = $('#date-from').value;
  while(day <= $('#date-to').value && days.length < 366){
    days.push(day);
    day = addDays(day, 1);
  }
  const available = items.filter(item => lanes.includes(item.lane)).sort((a, b) => a.date.localeCompare(b.date) || a.hour - b.hour);
  const latest = available.at(-1);

  if(!state.heatPinned || !days.includes(state.heatSelection?.date)){
    state.heatSelection = latest ? {date: latest.date, hour: latest.hour} : null;
    state.heatPinned = false;
  }
  if(!lanes.includes(state.heatFocusLane)) state.heatFocusLane = lanes[0];

  if(!available.length){
    $('#heatmap').innerHTML = '<div class="heat-empty"><span class="mono purple">NO RECORDS / —</span><b>ยังไม่มีข้อมูลในช่วงเวลานี้</b><small>เลือกวันที่อื่น หรือรอผลจากกล้องรอบถัดไป</small></div>';
    renderHeatInspector(null);
    updateHeatResetButton();
    return;
  }

  let html = '<span class="heat-axis-caption">วัน</span><span class="heat-axis-caption">ฝั่ง</span>';
  html += Array.from({length: 24}, (_, hour) => `<span class="heat-hour" data-hour="${hour}">${hour % 3 === 0 ? String(hour).padStart(2, '0') : ''}</span>`).join('');

  for(const date of days){
    const month = new Intl.DateTimeFormat('th-TH', {month: 'short'}).format(new Date(date + 'T00:00:00+07:00'));
    html += `<span class="heat-day" style="grid-row:span ${lanes.length}"><b>${date.slice(8)}</b><small>${month}</small></span>`;
    for(const lane of lanes){
      html += `<span class="heat-lane-label ${lane}"><i></i>${lane === 'left' ? 'ซ้าย' : 'ขวา'}</span>`;
      for(let hour = 0; hour < 24; hour++){
        const item = state.heatCells.get(`${date}|${hour}|${lane}`);
        const text = `${date} ${String(hour).padStart(2, '0')}:00 / ${labels[lane]} / ${item ? percent(item.occupancy) + ' / ' + item.sample_count + ' ตัวอย่าง' : 'ไม่มีข้อมูล'}`;
        const fill = item ? `--heat-fill:hsl(265 76% ${96 - Math.min(item.occupancy / 0.6, 1) * 62}%)` : '';
        html += `<button type="button" class="heat-cell ${item ? 'has-data' : 'no-data'}" style="${fill}" data-date="${date}" data-hour="${hour}" data-lane="${lane}" tabindex="-1" aria-pressed="false" aria-label="${escapeHtml(text)}"></button>`;
      }
    }
  }
  $('#heatmap').innerHTML = html;
  applyHeatSelection(state.heatSelection, false);
  updateHeatResetButton();
}

function applyHeatSelection(selection, isPreview = false){
  if(!selection){
    $$('#heatmap .is-selected, #heatmap .is-hovered').forEach(c => {
      c.classList.remove('is-selected', 'is-hovered');
      if(c.matches('button')) c.setAttribute('aria-pressed', 'false');
    });
    renderHeatInspector(null);
    return;
  }
  const {date, hour} = selection;
  const numHour = Number(hour);
  $$('#heatmap .is-selected, #heatmap .is-hovered').forEach(c => {
    c.classList.remove('is-selected', 'is-hovered');
    if(c.matches('button')) c.setAttribute('aria-pressed', 'false');
  });
  $$('#heatmap [tabindex="0"]').forEach(cell => cell.tabIndex = -1);

  const targetClass = isPreview ? 'is-hovered' : 'is-selected';
  $$(`#heatmap .heat-cell[data-date="${date}"][data-hour="${numHour}"]`).forEach(cell => {
    cell.classList.add(targetClass);
    if(!isPreview){
      cell.setAttribute('aria-pressed', 'true');
      if(cell.dataset.lane === state.heatFocusLane) cell.tabIndex = 0;
    }
  });
  const hourBtn = $(`#heatmap .heat-hour[data-hour="${numHour}"]`);
  if(hourBtn) hourBtn.classList.add(targetClass);

  renderHeatInspector(selection);
}

function selectHeatHour(date, hour, lane, pin = true){
  state.heatSelection = {date, hour: Number(hour)};
  if(lane) state.heatFocusLane = lane;
  if(pin) state.heatPinned = true;
  applyHeatSelection(state.heatSelection, false);
  updateHeatResetButton();
}

function renderHeatInspector(selection = state.heatHover || state.heatSelection){
  if(!selection){
    $('#heat-selection-label').textContent = 'เลือกช่วงเวลาเพื่อดูรายละเอียด';
    $('#heat-values').innerHTML = ['left', 'right'].map(lane =>
      `<div class="heat-value ${lane}"><span><i></i>${labels[lane]}</span><b>—</b><div class="heat-value-track"><i style="width:0%"></i></div><small>ไม่มีข้อมูล</small></div>`
    ).join('');
    const note = $('#heat-comparison-note');
    note.className = 'heat-comparison-note';
    note.innerHTML = '<span class="heat-note-icon">ℹ</span><span>คลิกเพื่อเลือกชั่วโมง · เลื่อนเมาส์ผ่านเพื่อพรีวิว</span>';
    return;
  }

  const key = `${selection.date}|${selection.hour}`;
  const leftCell = state.heatCells.get(`${key}|left`);
  const rightCell = state.heatCells.get(`${key}|right`);
  const pair = state.heatPairs.get(key);

  const date = new Intl.DateTimeFormat('th-TH', {day: 'numeric', month: 'short', year: 'numeric'}).format(new Date(selection.date + 'T00:00:00+07:00'));
  $('#heat-selection-label').textContent = `${date} · ${String(selection.hour).padStart(2, '0')}:00–${String((selection.hour + 1) % 24).padStart(2, '0')}:00`;

  // Check if selection is in future of current Bangkok time
  const now = new Date();
  const bangkokNow = new Date(now.toLocaleString('en-US', {timeZone: 'Asia/Bangkok'}));
  const todayStr = bangkokDay();
  const currentHour = bangkokNow.getHours();
  const isFuture = selection.date > todayStr || (selection.date === todayStr && selection.hour > currentHour);

  // Render values
  $('#heat-values').innerHTML = ['left', 'right'].map(lane => {
    const cell = lane === 'left' ? leftCell : rightCell;
    const value = cell?.occupancy ?? null;
    const count = cell?.sample_count || 0;
    const isDimmed = state.heatMode !== 'both' && state.heatMode !== lane;
    return `<div class="heat-value ${lane} ${isDimmed ? 'is-dimmed' : ''}">
      <span><i></i>${labels[lane]}</span>
      <b>${percent(value)}</b>
      <div class="heat-value-track"><i style="width:${value === null ? 0 : Math.min(value * 100, 100)}%"></i></div>
      <small>${count ? count + ' ตัวอย่าง' : (isFuture ? 'ยังไม่ถึงช่วงเวลานี้' : 'ไม่มีข้อมูล')}</small>
    </div>`;
  }).join('');

  const note = $('#heat-comparison-note');
  note.className = 'heat-comparison-note';

  // Case 1: No data on either lane
  if(!leftCell && !rightCell){
    if(isFuture){
      note.className = 'heat-comparison-note future-note';
      note.innerHTML = '<span class="heat-note-icon">⏳</span><div><b>ยังไม่ถึงช่วงเวลานี้</b><small>ระบบยังไม่มีการบันทึกภาพสำหรับชั่วโมงนี้ · เลือกชั่วโมงที่มีแถบสีม่วงเพื่อดูผลวิเคราะห์</small></div>';
    } else {
      note.className = 'heat-comparison-note no-data-note';
      note.innerHTML = '<span class="heat-note-icon">∅</span><div><b>ไม่มีข้อมูลการตรวจวัดในชั่วโมงนี้</b><small>ไม่มีภาพที่บันทึกไว้ในช่วงเวลานี้ · เลือกชั่วโมงที่มีแถบสีม่วงเพื่อดูผลวิเคราะห์</small></div>';
    }
    return;
  }

  // Case 2: Only one lane has data
  if((leftCell && !rightCell) || (!leftCell && rightCell)){
    note.className = 'heat-comparison-note single-lane-note';
    const hasLane = leftCell ? 'left' : 'right';
    const hasCell = leftCell || rightCell;
    note.innerHTML = `<span class="heat-note-icon">↔</span><div><b>มีข้อมูลเฉพาะ${labels[hasLane]}</b><small>บันทึก ${hasCell.sample_count} ภาพ · ยังไม่มีภาพที่ครอบคลุมทั้งสองฝั่งพร้อมกันในชั่วโมงนี้</small></div>`;
    return;
  }

  // Case 3: Both lanes have data
  if(state.heatMode === 'both'){
    if(pair){
      note.classList.add('comparable');
      const difference = pair.difference * 100;
      const higher = difference > 0 ? 'left' : 'right';
      const message = Math.abs(difference) < 0.5
        ? 'ค่าเฉลี่ยทั้งสองฝั่งใกล้เคียงกัน'
        : `${labels[higher]}หนาแน่นกว่า <b>${Math.abs(difference).toFixed(1)} จุดเปอร์เซ็นต์</b>`;
      note.innerHTML = `<span class="heat-note-icon">↔</span><div>${message}<small>คำนวณจากภาพคู่เวลาเดียวกัน ${pair.sample_count} ภาพ</small></div>`;
    } else {
      note.classList.add('comparable');
      note.innerHTML = `<span class="heat-note-icon">↔</span><div><b>บันทึกข้อมูลแยกสองฝั่ง</b><small>ฝั่งซ้าย ${leftCell.sample_count} ภาพ · ฝั่งขวา ${rightCell.sample_count} ภาพ</small></div>`;
    }
  } else {
    note.classList.add('comparable');
    const activeLane = state.heatMode;
    const activeCell = activeLane === 'left' ? leftCell : rightCell;
    const levelText = activeCell.occupancy < 0.25 ? 'สภาพจราจรโล่ง คล่องตัว' : (activeCell.occupancy < 0.5 ? 'สภาพจราจรปานกลาง' : 'สภาพจราจรหนาแน่นมาก');
    note.innerHTML = `<span class="heat-note-icon">●</span><div>${labels[activeLane]}: <b>${levelText}</b><small>เฉลี่ย ${percent(activeCell.occupancy)} จาก ${activeCell.sample_count} ตัวอย่าง</small></div>`;
  }
}

function renderHistory(data){
  state.total = data.total;
  const start = (data.page - 1) * data.page_size;
  $('#history-body').innerHTML = data.items.length
    ? data.items.map(item => `<tr data-frame="${item.frame_id}" tabindex="0" aria-label="รายละเอียด ${escapeHtml(dateTime(item.captured_at))} ${labels[item.lane]}">
        <td>${escapeHtml(dateTime(item.captured_at))}</td>
        <td>${labels[item.lane]} <span class="muted">/ ${item.lane === 'left' ? 'ออกจากกล้อง' : 'เข้าหากล้อง'}</span></td>
        <td>${percent(item.occupancy)}</td>
        <td>${levelChip(item.level)}</td>
        <td>${item.images.thumbnail ? `<img src="${escapeHtml(item.images.thumbnail)}" loading="lazy" alt="ภาพเวลา ${escapeHtml(shortTime(item.captured_at))}">` : '<span class="thumb-empty">ไม่มีภาพ</span>'}</td>
        <td class="row-arrow">↗</td>
      </tr>`).join('')
    : '<tr><td colspan="6" class="empty-cell">ไม่พบข้อมูลตามช่วงเวลาและตัวกรองนี้</td></tr>';
  $('#history-count').textContent = data.total ? `แสดง ${start + 1}–${Math.min(start + data.page_size, data.total)} จาก ${data.total.toLocaleString()} รายการ` : '0 รายการ';
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));
  $('#page-label').textContent = String(data.page).padStart(2, '0') + ' / ' + String(pages).padStart(2, '0');
  $('#prev-page').disabled = data.page <= 1;
  $('#next-page').disabled = data.page >= pages;
}

const fieldLabels = {
  timestamp: 'เวลาเก็บภาพ', file: 'ไฟล์ต้นฉบับ', lane: 'ฝั่งถนน', occupancy: 'ความหนาแน่นชดเชย perspective',
  occupancy_raw: 'ความหนาแน่นตามพิกเซล', occ_far: 'ช่วงไกล', occ_mid: 'ช่วงกลาง', occ_near: 'ช่วงใกล้',
  occupancy_kmeans: 'ความหนาแน่นวิธี K-means', edge_density: 'สัดส่วนขอบ Sobel', motion: 'การเคลื่อนไหวจากภาพคู่',
  brightness: 'ความสว่าง', contrast: 'ความเปรียบต่าง', is_night: 'ภาพกลางคืน', threshold: 'ค่า Otsu threshold',
  cam_dx: 'กล้องเลื่อนแกน X', cam_dy: 'กล้องเลื่อนแกน Y', cam_score: 'คะแนนจุดอ้างอิงกล้อง', level: 'สถานะจราจร'
};

async function showDetail(frameId){
  const dialog = $('#detail-dialog');
  $('#detail-content').innerHTML = '<p class="empty-cell">กำลังโหลดรายละเอียด…</p>';
  if(!dialog.open) dialog.showModal();
  dialog.dataset.frame = frameId;
  try {
    const data = await getJSON('/api/frames/' + frameId);
    if(!dialog.open || String(dialog.dataset.frame) !== String(frameId)) return;
    const sample = data.lanes[0];
    $('#detail-title').textContent = dateTime(sample.captured_at);
    let html = '<div class="detail-pictures">';
    for(const [key, label] of [['raw', 'ภาพต้นฉบับ'], ['overlay', 'ภาพวิเคราะห์']]){
      html += `<div class="detail-picture">
        <span>${label}</span>
        ${sample.images[key] ? `<img src="${escapeHtml(sample.images[key])}" alt="${label}" data-zoom-img="${key}" style="cursor:zoom-in" title="คลิกเพื่อซูมเต็มจอ">` : '<div class="empty-cell">ไม่มีไฟล์ภาพ</div>'}
      </div>`;
    }
    html += '</div><div class="detail-info">';
    html += `<p class="detail-note">ผลพร้อมแสดง: ${escapeHtml(dateTime(sample.processed_at))} · ข้อมูลเก่าที่ไม่มีเวลาประมวลผลจะแสดง —</p><div class="table-scroll"><table><thead><tr><th>รายละเอียด / คอลัมน์ CSV</th>${data.lanes.map(item => `<th>${labels[item.lane]}</th>`).join('')}</tr></thead><tbody>`;
    for(const [field, label] of Object.entries(fieldLabels)){
      html += `<tr><td>${label}<b>${field}</b></td>${data.lanes.map(item => {
        const value = item[field];
        let text = value === null || value === '' ? '—' : String(value);
        if(['occupancy', 'occupancy_raw', 'occ_far', 'occ_mid', 'occ_near', 'occupancy_kmeans', 'edge_density', 'motion'].includes(field)) text = percent(value);
        if(field === 'level') return `<td>${levelChip(value)}</td>`;
        if(field === 'is_night') text = value ? 'ใช่' : 'ไม่ใช่';
        return `<td>${escapeHtml(text)}</td>`;
      }).join('')}</tr>`;
    }
    html += '</tbody></table></div>';
    if(data.processing_config){
      html += `<details><summary>ค่าตั้งต้นที่ใช้วิเคราะห์ภาพนี้</summary><pre class="detail-config">${escapeHtml(JSON.stringify(data.processing_config, null, 2))}</pre></details>`;
    } else {
      html += '<p class="detail-note">ข้อมูลนำเข้าจาก CSV เดิมไม่มีบันทึกค่าตั้งต้นย้อนหลัง</p>';
    }
    html += '</div>';
    $('#detail-content').innerHTML = html;

    // Attach zoom listeners to detail dialog pictures
    $$('#detail-content .detail-picture img').forEach(img => {
      img.addEventListener('click', () => {
        openLightbox(img.dataset.zoomImg || 'raw', sample);
      });
    });
  } catch(error){
    $('#detail-content').innerHTML = `<p class="empty-cell">${escapeHtml(error.message)}</p>`;
  }
}

function renderFreshness(){
  if(!state.health) return;
  const health = state.health, latest = state.latest;
  const age = latest ? Math.max(0, Math.round((Date.now() - Date.parse(latest.captured_at)) / 60000)) : null;
  $('#freshness').textContent = latest ? `ภาพล่าสุด ${dateTime(latest.captured_at)} · ${age} นาทีที่แล้ว` : 'ยังไม่มีผลวิเคราะห์ที่ครบสองฝั่ง';
  $('#stale-banner').hidden = !(health.stale || health.last_error);
  $('#stale-banner').textContent = health.last_error
    ? 'ระบบเก็บข้อมูลพบปัญหา: ' + health.last_error + ' · แสดงผลล่าสุดที่มีอยู่'
    : health.stale ? 'ข้อมูลล่าสุดยังไม่อัปเดตตามรอบ · กำลังแสดงข้อมูลที่บันทึกไว้ ไม่ใช่ภาพสด' : '';
  const phaseNames = {
    starting: 'กำลังเริ่มระบบ', fetching: 'กำลังดึงภาพ', waiting_pair: 'กำลังรอภาพคู่',
    processing: 'กำลังวิเคราะห์', watching: 'กำลังติดตามภาพใหม่', idle: 'ระบบกำลังเก็บข้อมูล',
    duplicate: 'ภาพกล้องยังไม่เปลี่ยน', fetch_error: 'ดึงภาพไม่สำเร็จ', error: 'พบข้อผิดพลาด', stopped: 'Worker ยังไม่ทำงาน'
  };
  const online = health.worker_online;
  $('#worker-status').className = 'status-chip ' + (health.last_error ? 'problem' : online ? '' : 'neutral');
  $('#worker-status').innerHTML = '<i></i>' + escapeHtml(online ? (phaseNames[health.phase] || 'กำลังทำงาน') : 'Worker ยังไม่ทำงาน');
  $('#footer-status').textContent = online ? 'WORKER ONLINE' : 'WORKER OFFLINE';
  $('#footer-dot').classList.toggle('online', online);
}

function connectEvents(){
  const source = new EventSource('/api/events');
  source.onopen = () => {
    state.connection = true;
    $('#connection').textContent = '● เชื่อมต่ออัตโนมัติ';
    $('#connection').classList.add('live');
  };
  source.onerror = () => {
    state.connection = false;
    $('#connection').textContent = 'กำลังเชื่อมต่อใหม่…';
    $('#connection').classList.remove('live');
  };
  source.addEventListener('sync', event => {
    const data = JSON.parse(event.data);
    state.health = data.status;
    renderFreshness();
    state.wantedRevision = Math.max(state.wantedRevision, data.revision);
    if(data.revision > state.revision) scheduleRefresh();
  });
  source.addEventListener('measurements_updated', event => {
    const data = JSON.parse(event.data);
    state.wantedRevision = Math.max(state.wantedRevision, data.revision);
    if(data.revision > state.revision) scheduleRefresh();
  });
  source.addEventListener('system_status', event => {
    state.health = JSON.parse(event.data);
    renderFreshness();
    if(state.health.data_revision > state.revision){
      state.wantedRevision = state.health.data_revision;
      scheduleRefresh();
    }
  });
  window.addEventListener('beforeunload', () => source.close(), {once: true});
}

/* ==========================================================================
   LIGHTBOX FULLSCREEN ZOOM FUNCTIONALITY
   ========================================================================== */
function openLightbox(variant, frameRow){
  const row = frameRow || state.latest;
  if(!row) return;
  lightbox.currentFrame = row;
  lightbox.sourceImage = variant || state.image || 'raw';

  const modal = $('#image-lightbox');
  modal.hidden = false;
  document.body.style.overflow = 'hidden';

  // Force reflow for animation
  void modal.offsetWidth;
  modal.classList.add('is-active');
  lightbox.isOpen = true;

  updateLightboxImage();
  renderLightboxMetrics();
  setLightboxZoom(1, 0, 0);

  // Sync segmented buttons in lightbox
  $$('[data-lightbox-image]').forEach(btn => {
    btn.classList.toggle('selected', btn.dataset.lightboxImage === lightbox.sourceImage);
  });
}

function closeLightbox(){
  const modal = $('#image-lightbox');
  modal.classList.remove('is-active');
  lightbox.isOpen = false;
  document.body.style.overflow = '';
  if(document.fullscreenElement) {
    document.exitFullscreen().catch(() => {});
  }
  setTimeout(() => {
    if(!lightbox.isOpen) modal.hidden = true;
  }, 260);
}

function updateLightboxImage(){
  const row = lightbox.currentFrame || state.latest;
  if(!row) return;
  const src = row.images[lightbox.sourceImage] || row.images.raw;
  const img = $('#lightbox-image');
  img.src = src;
  $('#lightbox-timestamp').textContent = dateTime(row.captured_at);
  $('#lightbox-title').textContent = lightbox.sourceImage === 'overlay'
    ? 'ภาพวิเคราะห์ความหนาแน่นจราจร (Detection & Segmentation)'
    : 'ภาพต้นฉบับจากกล้อง CCTV เทศบาลนครรังสิต';
}

function renderLightboxMetrics(){
  const row = lightbox.currentFrame || state.latest;
  const container = $('#lightbox-metrics');
  if(!container || !row) return;
  const leftItem = state.data?.latest?.find(item => item.lane === 'left');
  const rightItem = state.data?.latest?.find(item => item.lane === 'right');
  container.innerHTML = `
    <div class="lightbox-chip"><span class="chip-dot left"></span><span>ฝั่งซ้าย: <b>${percent(leftItem?.occupancy ?? row.occupancy)}</b> (${labels[leftItem?.level || row.level] || '—'})</span></div>
    <div class="lightbox-chip"><span class="chip-dot right"></span><span>ฝั่งขวา: <b>${percent(rightItem?.occupancy)}</b> (${labels[rightItem?.level] || '—'})</span></div>
  `;
}

function setLightboxZoom(scale, x = lightbox.translateX, y = lightbox.translateY){
  lightbox.scale = Math.min(lightbox.maxScale, Math.max(lightbox.minScale, scale));
  if(lightbox.scale === 1){
    lightbox.translateX = 0;
    lightbox.translateY = 0;
  } else {
    lightbox.translateX = x;
    lightbox.translateY = y;
  }
  $('#lightbox-zoom-level').textContent = Math.round(lightbox.scale * 100) + '%';
  $('#lightbox-canvas').style.transform = `translate(${lightbox.translateX}px, ${lightbox.translateY}px) scale(${lightbox.scale})`;
}

function setupLightboxEvents(){
  const stage = $('#lightbox-stage');

  // Zoom buttons
  $('#lightbox-zoom-in')?.addEventListener('click', () => setLightboxZoom(lightbox.scale + 0.3));
  $('#lightbox-zoom-out')?.addEventListener('click', () => setLightboxZoom(lightbox.scale - 0.3));
  $('#lightbox-zoom-reset')?.addEventListener('click', () => setLightboxZoom(1, 0, 0));

  // Native Browser Fullscreen toggle
  $('#lightbox-browser-fs')?.addEventListener('click', () => {
    if(!document.fullscreenElement){
      $('#image-lightbox').requestFullscreen().catch(() => {});
    } else {
      document.exitFullscreen().catch(() => {});
    }
  });

  // Close triggers
  $('#lightbox-close')?.addEventListener('click', closeLightbox);
  $('#lightbox-backdrop')?.addEventListener('click', closeLightbox);

  // Switch image variant in lightbox
  $$('[data-lightbox-image]').forEach(button => button.addEventListener('click', () => {
    lightbox.sourceImage = button.dataset.lightboxImage;
    state.image = lightbox.sourceImage;
    $$('[data-lightbox-image]').forEach(btn => btn.classList.toggle('selected', btn === button));
    $$('[data-image]').forEach(btn => btn.classList.toggle('selected', btn.dataset.image === lightbox.sourceImage));
    updateCamera(state.latest);
    updateLightboxImage();
  }));

  // Wheel Zoom
  stage?.addEventListener('wheel', event => {
    if(!lightbox.isOpen) return;
    event.preventDefault();
    const delta = event.deltaY < 0 ? 0.25 : -0.25;
    const newScale = Math.min(lightbox.maxScale, Math.max(lightbox.minScale, lightbox.scale + delta));
    setLightboxZoom(newScale);
  }, {passive: false});

  // Pan / Drag
  stage?.addEventListener('pointerdown', event => {
    if(!lightbox.isOpen || event.button !== 0) return;
    lightbox.isPanning = true;
    lightbox.startX = event.clientX - lightbox.translateX;
    lightbox.startY = event.clientY - lightbox.translateY;
    stage.classList.add('is-panning');
    stage.setPointerCapture(event.pointerId);
  });

  stage?.addEventListener('pointermove', event => {
    if(!lightbox.isPanning) return;
    const x = event.clientX - lightbox.startX;
    const y = event.clientY - lightbox.startY;
    setLightboxZoom(lightbox.scale, x, y);
  });

  const stopPan = event => {
    if(!lightbox.isPanning) return;
    lightbox.isPanning = false;
    stage.classList.remove('is-panning');
    try { stage.releasePointerCapture(event.pointerId); } catch {}
  };
  stage?.addEventListener('pointerup', stopPan);
  stage?.addEventListener('pointercancel', stopPan);

  // Double click to toggle 100% / 200%
  stage?.addEventListener('dblclick', () => {
    if(!lightbox.isOpen) return;
    if(lightbox.scale === 1){
      setLightboxZoom(2.0, 0, 0);
    } else {
      setLightboxZoom(1.0, 0, 0);
    }
  });
}

function setupEvents(){
  $$('[data-range]').forEach(button => button.addEventListener('click', () => chooseRange(button.dataset.range)));
  $('#apply-dates').addEventListener('click', () => {
    state.range = 'custom';
    syncHistoryDays();
    state.trend.span = 'all';
    $$('[data-range]').forEach(button => button.classList.toggle('selected', button.dataset.range === 'custom'));
    refresh('filter');
  });

  $('#lane-filter').addEventListener('change', () => {
    state.page = 1;
    refresh('filter');
  });
  $('#level-filter').addEventListener('change', () => {
    state.page = 1;
    loadHistory();
  });

  $$('[data-history-range]').forEach(button => button.addEventListener('click', () => {
    setHistoryDays(button.dataset.historyRange);
    state.page = 1;
    loadHistory();
  }));
  $$('[data-history-time]').forEach(button => button.addEventListener('click', () => {
    [$('#history-time-from').value, $('#history-time-to').value] = button.dataset.historyTime.split('-');
    state.page = 1;
    loadHistory();
  }));
  $('#history-filter').addEventListener('submit', event => {
    event.preventDefault();
    state.page = 1;
    loadHistory();
  });

  $$('[data-heat-mode]').forEach(button => button.addEventListener('click', () => {
    state.heatMode = button.dataset.heatMode;
    $$('[data-heat-mode]').forEach(item => {
      const selected = item === button;
      item.classList.toggle('selected', selected);
      item.setAttribute('aria-pressed', String(selected));
    });
    renderHeatmap();
  }));

  // Heatmap Interactions: Click = Pin, Pointerover = Preview, Pointerleave = Restore
  $('#heatmap').addEventListener('click', event => {
    const cell = event.target.closest('.heat-cell');
    if(cell){
      selectHeatHour(cell.dataset.date, cell.dataset.hour, cell.dataset.lane, true);
      return;
    }
    const hourBtn = event.target.closest('.heat-hour');
    if(hourBtn){
      const targetDate = state.heatSelection?.date || $('#date-to').value;
      selectHeatHour(targetDate, hourBtn.dataset.hour, state.heatFocusLane, true);
    }
  });

  $('#heatmap').addEventListener('pointerover', event => {
    const cell = event.target.closest('.heat-cell');
    if(cell){
      state.heatHover = {date: cell.dataset.date, hour: Number(cell.dataset.hour)};
      applyHeatSelection(state.heatHover, true);
      return;
    }
    const hourBtn = event.target.closest('.heat-hour');
    if(hourBtn){
      const targetDate = state.heatSelection?.date || $('#date-to').value;
      state.heatHover = {date: targetDate, hour: Number(hourBtn.dataset.hour)};
      applyHeatSelection(state.heatHover, true);
    }
  });

  $('#heatmap').addEventListener('pointerleave', () => {
    state.heatHover = null;
    applyHeatSelection(state.heatSelection, false);
  });

  $('#heat-reset-btn')?.addEventListener('click', () => {
    const latest = getLatestHeatmapDataHour();
    state.heatPinned = false;
    state.heatSelection = latest ? {date: latest.date, hour: latest.hour} : null;
    applyHeatSelection(state.heatSelection, false);
    updateHeatResetButton();
  });

  $('#heatmap').addEventListener('keydown', event => {
    const cell = event.target.closest('.heat-cell');
    if(!cell) return;
    const hour = Number(cell.dataset.hour);
    const keys = {ArrowLeft: -1, ArrowRight: 1, ArrowUp: -24, ArrowDown: 24, Home: -hour, End: 23 - hour};
    if(event.key === 'Enter' || event.key === ' '){
      event.preventDefault();
      selectHeatHour(cell.dataset.date, cell.dataset.hour, cell.dataset.lane, true);
      return;
    }
    if(!(event.key in keys)) return;
    event.preventDefault();
    if((event.key === 'ArrowLeft' && hour === 0) || (event.key === 'ArrowRight' && hour === 23)) return;
    const cells = $$('#heatmap .heat-cell');
    const index = cells.indexOf(cell);
    const nextCell = cells[index + keys[event.key]];
    if(nextCell){
      nextCell.focus();
      selectHeatHour(nextCell.dataset.date, nextCell.dataset.hour, nextCell.dataset.lane, true);
    }
  });

  // Camera Zoom Triggers
  $('#camera-zoom-btn')?.addEventListener('click', () => openLightbox(state.image, state.latest));
  $('#camera-zoom-trigger')?.addEventListener('click', () => openLightbox(state.image, state.latest));
  $('#camera-image')?.addEventListener('click', () => openLightbox(state.image, state.latest));

  $$('[data-image]').forEach(button => button.addEventListener('click', () => {
    state.image = button.dataset.image;
    $$('[data-image]').forEach(item => item.classList.toggle('selected', item === button));
    $$('[data-lightbox-image]').forEach(item => item.classList.toggle('selected', item.dataset.lightboxImage === state.image));
    updateCamera(state.latest);
  }));

  $('#retry').addEventListener('click', () => state.config ? refresh() : initialize());
  $('#prev-page').addEventListener('click', () => { if(state.page > 1){ state.page--; loadHistory(); } });
  $('#next-page').addEventListener('click', () => { state.page++; loadHistory(); });
  $('#new-data').addEventListener('click', () => { state.page = 1; loadHistory(); });

  $('#history-body').addEventListener('click', event => {
    const row = event.target.closest('[data-frame]');
    if(row) showDetail(row.dataset.frame);
  });
  $('#history-body').addEventListener('keydown', event => {
    if(event.key === 'Enter'){
      const row = event.target.closest('[data-frame]');
      if(row) showDetail(row.dataset.frame);
    }
  });

  $('#close-dialog').addEventListener('click', () => $('#detail-dialog').close());
  $('#detail-dialog').addEventListener('click', event => {
    if(event.target === event.currentTarget){
      const bounds = event.currentTarget.getBoundingClientRect();
      if(event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom){
        event.currentTarget.close();
      }
    }
  });

  $('#export').addEventListener('click', () => {
    try {
      const params = query();
      if($('#level-filter').value) params.set('level', $('#level-filter').value);
      const link = document.createElement('a');
      link.href = '/api/export.csv?' + params;
      link.download = 'rangsit-traffic.csv';
      link.click();
    } catch(error){
      showError(error);
    }
  });

  $$('nav a').forEach(link => link.addEventListener('click', () => {
    $$('nav a').forEach(item => item.classList.toggle('active', item === link));
  }));

  // Global Keyboard shortcuts
  window.addEventListener('keydown', event => {
    if(event.key === 'Escape'){
      if(lightbox.isOpen){
        closeLightbox();
        return;
      }
      if($('#detail-dialog').open){
        $('#detail-dialog').close();
        return;
      }
    }
    // 'f' or 'F' to toggle fullscreen zoom when not typing in form inputs
    if((event.key === 'f' || event.key === 'F') && !['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement?.tagName)){
      event.preventDefault();
      if(lightbox.isOpen){
        closeLightbox();
      } else {
        openLightbox(state.image, state.latest);
      }
    }
  });

  setupLightboxEvents();
  setupTrendEvents();
}

async function initialize(){
  await document.fonts.load('400 13px "Noto Sans Thai"', 'การจราจร').catch(() => {});
  try {
    [state.config, state.health] = await Promise.all([getJSON('/api/config'), getJSON('/api/health')]);
    $('#snapshot-interval').textContent = `SNAPSHOT / ${state.config.interval_sec} SEC`;
    $('#footer-interval').textContent = `CAM_01 / INTERVAL: ${state.config.interval_sec}s`;
    renderFreshness();
    connectEvents();
    await refresh();
  } catch(error){
    showError(error);
  }
}

async function start(){
  const today = bangkokDay();
  $('#date-from').value = today;
  $('#date-to').value = today;
  $('#history-from').value = today;
  $('#history-to').value = today;
  setupEvents();
  let lastDay = today;
  const clock = () => {
    $('#clock').textContent = new Intl.DateTimeFormat('en-GB', {timeZone: 'Asia/Bangkok', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false}).format(new Date());
  };
  clock();
  setInterval(clock, 1000);
  setInterval(() => {
    renderFreshness();
    const day = bangkokDay();
    if(day !== lastDay){
      lastDay = day;
      // ประวัติที่ใช้ปุ่มลัด (วันนี้/เมื่อวาน/7 วัน) เลื่อนตามวันใหม่ด้วย
      const historyRange = state.history.range;
      if(state.config && state.range !== 'custom') chooseRange(state.range);
      if(state.config && historyRange !== 'custom'){ setHistoryDays(historyRange); state.page = 1; loadHistory(); }
    }
  }, 30000);
  await initialize();
}

start();
