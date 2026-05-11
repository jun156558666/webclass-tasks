const API = '';

let nextRefreshIn = 300;
let countdownTimer = null;

// ── Fetch & render ──────────────────────────────────────────────────────────

async function loadAssignments() {
  try {
    const res = await fetch(`${API}/api/assignments`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderAssignments(data);
  } catch (e) {
    showStatus(`取得エラー: ${e.message}`, true);
  }
}

async function loadStatus() {
  try {
    const res = await fetch(`${API}/api/status`);
    if (!res.ok) return;
    const data = await res.json();
    if (data.last_scrape) {
      const t = new Date(data.last_scrape.scraped_at).toLocaleString('ja-JP');
      if (data.last_scrape.error) showStatus(`⚠ スクレイピングエラー: ${data.last_scrape.error}`, true);
      document.getElementById('last-updated').textContent = `最終更新: ${t}`;
    }
    nextRefreshIn = data.refresh_interval_seconds ?? 300;
  } catch (_) {}
}

async function manualRefresh() {
  const btn = document.getElementById('refresh-btn');
  btn.disabled = true;
  btn.textContent = '更新中…';
  showStatus('<span class="spinner"></span> スクレイピング中…');
  try {
    const res = await fetch(`${API}/api/refresh`, { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
      showStatus(`✅ 取得完了 (${data.count} 件)`);
      await loadAssignments();
      await loadStatus();
    } else {
      showStatus('❌ 更新失敗', true);
    }
  } catch (e) {
    showStatus(`更新エラー: ${e.message}`, true);
  } finally {
    btn.disabled = false;
    btn.textContent = '🔄 今すぐ更新';
    resetCountdown();
  }
}

// ── Render ──────────────────────────────────────────────────────────────────

function urgencyClass(deadline, submitted) {
  if (submitted) return 'is-submitted';
  if (!deadline) return 'no-deadline';
  const diff = parseDeadline(deadline) - Date.now();
  const days = diff / 86400000;
  if (days < 0)  return 'urgent';
  if (days <= 2) return 'urgent';
  if (days <= 7) return 'warning';
  return 'ok';
}

function parseDeadline(str) {
  if (!str) return null;
  return new Date(str.replace(/\//g, '-'));
}

function formatCountdown(deadline) {
  const parsed = parseDeadline(deadline);
  if (!parsed) return '';
  const diff = parsed - Date.now();
  if (diff < 0) return '(期限切れ)';
  const days  = Math.floor(diff / 86400000);
  const hours = Math.floor((diff % 86400000) / 3600000);
  const mins  = Math.floor((diff % 3600000) / 60000);
  if (days > 0)  return `(あと ${days}日 ${hours}時間)`;
  if (hours > 0) return `(あと ${hours}時間 ${mins}分)`;
  return `(あと ${mins}分)`;
}

function renderAssignments(assignments) {
  const list    = document.getElementById('assignment-list');
  const emptyEl = document.getElementById('empty-msg');
  list.innerHTML = '';

  if (!assignments.length) {
    emptyEl.classList.remove('hidden');
    return;
  }
  emptyEl.classList.add('hidden');

  // 授業ごとにグループ化
  const groups = {};
  for (const a of assignments) {
    (groups[a.course_name] ??= []).push(a);
  }

  for (const [course, items] of Object.entries(groups).sort(([a], [b]) => courseOrder(a) - courseOrder(b) || a.localeCompare(b, 'ja'))) {
    const pending   = items.filter(a => !a.submitted);
    const submitted = items.filter(a =>  a.submitted);

    const block = document.createElement('div');
    block.className = 'course-block';

    // ── ヘッダー
    const pendingCount   = pending.length   ? `<span class="badge badge-pending">未提出 ${pending.length}</span>` : '';
    const submittedCount = submitted.length ? `<span class="badge badge-submitted">提出済み ${submitted.length}</span>` : '';
    block.innerHTML = `
      <div class="course-block-header">
        <span class="course-block-name">${escHtml(course)}</span>
        <div class="course-block-badges">${pendingCount}${submittedCount}</div>
      </div>
      <div class="course-columns">
        <div class="course-col">
          <div class="course-col-header">未提出</div>
          <div class="col-pending-items"></div>
        </div>
        <div class="course-col">
          <div class="course-col-header">提出済み</div>
          <div class="col-submitted-items"></div>
        </div>
      </div>`;

    const pendingEl   = block.querySelector('.col-pending-items');
    const submittedEl = block.querySelector('.col-submitted-items');

    if (pending.length === 0) {
      pendingEl.innerHTML = '<div class="col-empty">なし</div>';
    } else {
      for (const a of pending) {
        pendingEl.appendChild(buildCard(a));
      }
    }

    if (submitted.length === 0) {
      submittedEl.innerHTML = '<div class="col-empty">なし</div>';
    } else {
      for (const a of submitted) {
        submittedEl.appendChild(buildCard(a));
      }
    }

    list.appendChild(block);
  }
}

function buildCard(a) {
  const cls = urgencyClass(a.deadline, a.submitted);
  const deadlineText = a.deadline
    ? `<span class="deadline-badge">${escHtml(a.deadline)}</span>
       <span class="countdown">${formatCountdown(a.deadline)}</span>`
    : `<span class="deadline-badge">締切不明</span>`;

  const titleHtml = a.url
    ? `<a href="${escHtml(a.url)}" target="_blank" rel="noopener">${escHtml(a.title)}</a>`
    : escHtml(a.title);

  const unsubmitActive = !a.submitted ? 'active-unsubmit' : '';
  const submitActive   =  a.submitted ? 'active-submit'   : '';
  const safeId = escHtml(a.id);

  const card = document.createElement('div');
  card.className = `card ${cls}`;
  card.dataset.id = a.id;
  card.innerHTML = `
    <div class="card-title">${titleHtml}</div>
    <div class="card-deadline">${deadlineText}</div>
    <div class="radio-group">
      <label class="${unsubmitActive}">
        <input type="radio" name="status-${safeId}" value="0" ${!a.submitted ? 'checked' : ''}>
        未提出
      </label>
      <label class="${submitActive}">
        <input type="radio" name="status-${safeId}" value="1" ${a.submitted ? 'checked' : ''}>
        提出済み
      </label>
    </div>`;

  card.querySelectorAll('input[type="radio"]').forEach(radio => {
    radio.addEventListener('change', () => handleStatusChange(a.id, radio.value === '1'));
  });

  return card;
}

// ── Status change ─────────────────────────────────────────────────────────────

async function handleStatusChange(id, toSubmitted) {
  const endpoint = toSubmitted ? 'submit' : 'unsubmit';
  try {
    const res = await fetch(`${API}/api/assignments/${encodeURIComponent(id)}/${endpoint}`, {
      method: 'POST',
    });
    if (res.ok) await loadAssignments();
  } catch (e) {
    alert(`エラー: ${e.message}`);
    await loadAssignments();
  }
}

// ── Course sort ───────────────────────────────────────────────────────────────

const DAY_ORDER = { '月': 0, '火': 1, '水': 2, '木': 3, '金': 4, '土': 5, '日': 6 };

function courseOrder(name) {
  // "(2026-春学期-月1)" や "(2026-春学期-火1-他)" からday・periodを抽出
  const m = name.match(/-([月火水木金土日])(\d)/);
  if (!m) return 9999;
  return (DAY_ORDER[m[1]] ?? 9) * 10 + parseInt(m[2], 10);
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function showStatus(html, isError = false) {
  document.getElementById('status-bar').innerHTML =
    `<div class="status-msg${isError ? ' error' : ''}">${html}</div>`;
}

// ── Auto-refresh ──────────────────────────────────────────────────────────────

function resetCountdown() {
  clearInterval(countdownTimer);
  let remaining = nextRefreshIn;
  countdownTimer = setInterval(async () => {
    remaining--;
    if (remaining <= 0) {
      remaining = nextRefreshIn;
      await loadAssignments();
      await loadStatus();
    }
  }, 1000);
}

// ── Init ──────────────────────────────────────────────────────────────────────

async function init() {
  showStatus('<span class="spinner"></span> 読み込み中…');
  await loadStatus();
  await loadAssignments();
  document.getElementById('status-bar').innerHTML = '';
  resetCountdown();
}

init();
