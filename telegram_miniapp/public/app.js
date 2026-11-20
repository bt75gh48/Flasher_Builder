// Flasher Builder Macan • Telegram Mini App Client

const tg = window.Telegram?.WebApp;
const isInsideTelegram = Boolean(tg && (tg.initData || (tg.platform && tg.platform !== 'unknown')));

// App State
const state = {
  device: 'Oneplus 15R',
  flasher_type: 'Super Flasher',
  region: 'GLO',
  ota_version: 'latest',
  ota_url: '',
  bloat_static_odm: '0',
  upload_to: 'Gofile',
  gofile_api_key: '',
  notify_telegram: true,
  isCustomUrl: false
};

// Catalog & Auth State
let catalogData = null;
let isFetchingCatalog = false;
let isUserWhitelisted = false;
let canUserTrigger = false;
let canUserViewRuns = false;
let canUserCancel = false;
let canUserViewGithub = false;

// Device & Builder Matrix matching build_macan.yml
const DEVICE_MAP = {
  'Oneplus 15R': { alias: '15R', otaName: 'OP 15R' },
  'Oneplus Ace 6T': { alias: 'Ace6T', otaName: 'OP ACE 6T' }
};

const BUILDER_MAP = {
  'Super Flasher|15R|EU': 'BUILD_SUPER',
  'Super Flasher|15R|GLO': 'BUILD_SUPER',
  'Super Flasher|15R|IN': 'BUILD_SUPER',
  'Super Flasher|Ace6T|CN': 'BUILD_SUPER',
  'Super Flasher|Ace6T|IN': 'BUILD_SUPER',
  'Super Flasher|Ace6T|GLO': 'BUILD_SUPER',
  'Regional Flasher|15R|IN': 'BUILD_REGIONAL',
  'Regional Flasher|15R|GLO': 'BUILD_REGIONAL',
  'Regional Flasher|15R|EU': 'BUILD_REGIONAL',
};

// =========================================================================
// Initialization
// =========================================================================

function initApp() {
  const inPageBtn = document.getElementById('btn-trigger');

  if (tg) {
    tg.ready();
    tg.expand();
    if (tg.setHeaderColor) tg.setHeaderColor('secondary_bg_color');

    // Display user name
    const user = tg.initDataUnsafe?.user;
    if (user) {
      document.getElementById('tg-username').textContent = user.first_name || user.username || 'User';
    }

    // Configure Native Main Button in Telegram; hide duplicate in-page button
    if (isInsideTelegram && tg.MainButton) {
      tg.MainButton.setText('🚀 TRIGGER BUILD');
      tg.MainButton.show();
      tg.MainButton.onClick(handleTriggerBuild);
      if (inPageBtn) inPageBtn.classList.add('hidden');
    } else if (inPageBtn) {
      inPageBtn.classList.remove('hidden');
      inPageBtn.classList.add('flex');
      if (tg.MainButton) tg.MainButton.hide();
    }
  } else if (inPageBtn) {
    inPageBtn.classList.remove('hidden');
    inPageBtn.classList.add('flex');
  }

  setupEventListeners();
  checkAuthStatus();
  validateMatrix();
  fetchOtaCatalog();
}

// =========================================================================
// Authentication & Whitelist Verification
// =========================================================================

async function checkAuthStatus() {
  const runsBtn = document.getElementById('tab-runs-btn');
  const userBadge = document.getElementById('user-badge');
  const tgUser = tg?.initDataUnsafe?.user;

  try {
    const res = await fetch('/api/auth-status', {
      headers: { 'Authorization': `tma ${tg?.initData || ''}` }
    });

    if (res.ok) {
      const data = await res.json();
      if (data.ok) {
        isUserWhitelisted = Boolean(data.is_whitelisted);
        canUserTrigger = Boolean(data.can_trigger);
        canUserViewRuns = Boolean(data.can_view_runs);
        canUserCancel = Boolean(data.can_cancel ?? data.can_trigger);
        canUserViewGithub = Boolean(data.can_view_github);

        if (canUserViewRuns) {
          runsBtn.classList.remove('hidden');
          runsBtn.classList.add('flex');
        } else {
          runsBtn.classList.add('hidden');
          runsBtn.classList.remove('flex');
        }

        const isWeb = Boolean(data.user?.id === 'web_visitor' || !tg?.initData);
        const name = data.user?.first_name || (isWeb ? 'Web Visitor' : (tgUser?.first_name || 'User'));
        if (isUserWhitelisted) {
          userBadge.innerHTML = `
            <span class="w-2 h-2 rounded-full bg-emerald-400"></span>
            <span class="text-slate-300 font-medium">${escapeHtml(name)}</span>
            <span class="text-[9px] px-1 rounded bg-blue-500/20 text-blue-300 font-mono">Whitelisted</span>
          `;
        } else {
          userBadge.innerHTML = `
            <span class="w-2 h-2 rounded-full bg-blue-400"></span>
            <span class="text-slate-300 font-medium">${escapeHtml(name)}</span>
            <span class="text-[9px] px-1 rounded bg-slate-700 text-slate-300 font-mono">${isWeb ? 'Web' : 'Public'}</span>
          `;
        }
        return;
      }
    }
  } catch (err) {
    console.warn('Auth check error:', err);
  }

  // Unauthorized or access disabled
  isUserWhitelisted = false;
  canUserTrigger = false;
  canUserViewRuns = false;
  canUserViewGithub = false;
  runsBtn.classList.add('hidden');
  runsBtn.classList.remove('flex');

  const isWeb = !tg?.initData;
  const userId = tgUser?.id ? String(tgUser.id) : (isWeb ? 'Web' : 'Guest');
  const name = tgUser?.first_name || (isWeb ? 'Web Visitor' : 'Guest');
  userBadge.innerHTML = `
    <span class="w-2 h-2 rounded-full bg-amber-400"></span>
    <span class="text-slate-300 font-medium">${escapeHtml(name)}</span>
    <span class="text-[9px] px-1 rounded bg-amber-500/20 text-amber-300 font-mono">${isWeb ? 'No Access' : `ID: ${userId}`}</span>
  `;
}

// =========================================================================
// Matrix & Rules Validation
// =========================================================================

function needsMatchedOdm(device, kind, region) {
  return device === 'Oneplus Ace 6T' && kind === 'Super Flasher' && (region === 'IN' || region === 'GLO');
}

function getOtaTargets(device, kind, region) {
  if (needsMatchedOdm(device, kind, region)) {
    return { ota: ['OP 15R', region], donor: ['OP ACE 6T', 'CN'] };
  }
  const mapped = DEVICE_MAP[device]?.otaName || device;
  return { ota: [mapped, region] };
}

function validateMatrix() {
  const devShort = DEVICE_MAP[state.device]?.alias || '15R';
  const regionalBtn = document.getElementById('btn-regional-flasher');
  const ace6tNote = document.getElementById('ace6t-flasher-note');
  const euBtn = document.getElementById('btn-region-eu');
  const cnBtn = document.getElementById('btn-region-cn');
  const regionWarn = document.getElementById('region-warning');

  // Rule 1: Ace 6T does not support Regional Flasher
  if (devShort === 'Ace6T') {
    regionalBtn.classList.add('disabled-choice');
    ace6tNote.classList.remove('hidden');
    if (state.flasher_type === 'Regional Flasher') {
      state.flasher_type = 'Super Flasher';
      syncButtonChoices('.flasher-btn', 'data-flasher', state.flasher_type);
    }
  } else {
    regionalBtn.classList.remove('disabled-choice');
    ace6tNote.classList.add('hidden');
  }

  // Rule 2: Region constraints per device
  if (devShort === '15R') {
    cnBtn.classList.add('disabled-choice');
    euBtn.classList.remove('disabled-choice');
    if (state.region === 'CN') {
      state.region = 'GLO';
      syncButtonChoices('.region-btn', 'data-region', state.region);
    }
  } else if (devShort === 'Ace6T') {
    euBtn.classList.add('disabled-choice');
    cnBtn.classList.remove('disabled-choice');
    if (state.region === 'EU') {
      state.region = 'GLO';
      syncButtonChoices('.region-btn', 'data-region', state.region);
    }
  }

  // Check supported combination
  const comboKey = `${state.flasher_type}|${devShort}|${state.region}`;
  const isSupported = Boolean(BUILDER_MAP[comboKey]);
  if (!isSupported) {
    regionWarn.textContent = `⚠️ Unsupported combination: ${state.device} • ${state.flasher_type} • ${state.region}`;
    regionWarn.classList.remove('hidden');
  } else {
    regionWarn.classList.add('hidden');
  }

  populateOtaVersions();
  updateArtifactPreview();
  updateUploadDestinationUI();
}

function syncButtonChoices(selector, attr, activeValue) {
  document.querySelectorAll(selector).forEach(btn => {
    const isMatch = btn.getAttribute(attr) === activeValue;
    btn.classList.toggle('active-choice', isMatch);
    btn.classList.toggle('inactive-choice', !isMatch);
  });
}

function updateUploadDestinationUI() {
  const isGofile = state.upload_to === 'Gofile';
  const gofileKeyGroup = document.getElementById('gofile-key-group');
  if (gofileKeyGroup) {
    gofileKeyGroup.classList.toggle('hidden', !isGofile);
  }
}

function cleanVersionString(val) {
  if (!val) return '';
  const str = String(val).trim();
  const matches = str.match(/\d+(?:\.\d+){1,4}/g);
  if (matches && matches.length > 0) {
    return matches.reduce((longest, curr) => curr.length > longest.length ? curr : longest, '');
  }
  return str.replace(/[^\w.()+-]/g, '_').slice(0, 80);
}

function computeArtifactName() {
  let ver = '';
  if (state.isCustomUrl) {
    const urlMatches = state.ota_url ? state.ota_url.match(/\d+(?:\.\d+){1,4}/g) : null;
    ver = (urlMatches && urlMatches.length > 0)
      ? urlMatches.reduce((longest, curr) => curr.length > longest.length ? curr : longest, '')
      : 'custom';
  } else {
    const targetInfo = getOtaTargets(state.device, state.flasher_type, state.region);
    const [targetDev, targetReg] = targetInfo.ota;
    const entries = catalogData ? releasesFor(catalogData, targetDev, targetReg) : [];
    const isLatest = state.ota_version === 'latest' || !state.ota_version;
    const entry = isLatest
      ? (entries.length > 0 ? entries[0] : null)
      : (entries.find(e => e.version === state.ota_version) || (entries.length > 0 ? entries[0] : null));

    if (entry && entry.version) {
      ver = cleanVersionString(entry.version);
    } else if (state.ota_version && state.ota_version !== 'latest') {
      ver = cleanVersionString(state.ota_version);
    } else {
      ver = 'latest';
    }
  }

  const devModel = state.device === 'Oneplus Ace 6T' ? 'A6T' : '15R';
  const regionDisp = state.region === 'CN' ? 'COS' : state.region;

  if (devModel === 'A6T') {
    const osTag = state.region === 'CN' ? 'COS' : `${regionDisp} OOS+COS ODM`;
    return `${state.flasher_type} A6T ${osTag} ${ver}.zip`;
  }
  return `${state.flasher_type} 15R ${regionDisp} ${ver}.zip`;
}

function updateArtifactPreview() {
  const name = computeArtifactName();
  const el = document.getElementById('preview-artifact');
  if (el) el.textContent = name;
}

// =========================================================================
// OTA Catalog System (Synced with flasher_builder/ota_catalog.py)
// =========================================================================

function releasesFor(data, device, region) {
  if (!data || !Array.isArray(data.releases)) return [];
  const filtered = data.releases.filter(e => e.device === device && e.region === region);
  return filtered.sort((a, b) => {
    if (Boolean(a.is_latest) !== Boolean(b.is_latest)) {
      return a.is_latest ? -1 : 1;
    }
    const aTime = String(a.build_timestamp || '');
    const bTime = String(b.build_timestamp || '');
    if (aTime && bTime && aTime !== bTime) {
      return bTime.localeCompare(aTime);
    }
    return compareVersionStrings(b.version, a.version);
  });
}

function compareVersionStrings(vA, vB) {
  const splitA = String(vA).split(/(\d+)/).filter(Boolean);
  const splitB = String(vB).split(/(\d+)/).filter(Boolean);
  const maxLen = Math.max(splitA.length, splitB.length);
  for (let i = 0; i < maxLen; i++) {
    const partA = splitA[i] || '';
    const partB = splitB[i] || '';
    const isNumA = /^\d+$/.test(partA);
    const isNumB = /^\d+$/.test(partB);
    if (isNumA && isNumB) {
      const diff = parseInt(partA, 10) - parseInt(partB, 10);
      if (diff !== 0) return diff;
    } else {
      const diff = partA.localeCompare(partB);
      if (diff !== 0) return diff;
    }
  }
  return 0;
}

function matchingOdm(data, targetRelease) {
  if (!data || !targetRelease?.security_patch) return null;
  const patch = targetRelease.security_patch;
  const candidates = releasesFor(data, 'OP ACE 6T', 'CN').filter(e => e.security_patch === patch);
  return candidates.length > 0 ? candidates[0] : null;
}

async function fetchOtaCatalog(force = false) {
  if (isFetchingCatalog) return;
  isFetchingCatalog = true;

  const spinner = document.getElementById('fetch-spinner');
  const icon = document.getElementById('fetch-icon');
  const btnLabel = document.getElementById('fetch-btn-label');
  const statusEl = document.getElementById('ota-catalog-status');

  spinner.classList.remove('hidden');
  icon.classList.add('hidden');
  btnLabel.textContent = 'Fetching…';
  statusEl.textContent = 'Fetching available versions…';

  try {
    let data = null;
    try {
      const res = await fetch('/api/catalog');
      if (res.ok) data = await res.json();
    } catch (_) {}

    // Direct fallback if worker proxy is not available
    if (!data) {
      const directRes = await fetch('https://roms.danielspringer.at/api/ota.php');
      if (directRes.ok) data = await directRes.json();
    }

    if (!data || !Array.isArray(data.releases)) {
      throw new Error('Invalid catalog format');
    }

    catalogData = data;
    populateOtaVersions();
    haptic('success');
  } catch (err) {
    console.error('Catalog fetch error:', err);
    statusEl.textContent = 'Catalog fetch failed. Using latest mode.';
  } finally {
    isFetchingCatalog = false;
    spinner.classList.add('hidden');
    icon.classList.remove('hidden');
    btnLabel.textContent = 'Fetch versions';
  }
}

function populateOtaVersions() {
  const versionSelect = document.getElementById('select-ota-version');
  const statusEl = document.getElementById('ota-catalog-status');
  const targetInfo = getOtaTargets(state.device, state.flasher_type, state.region);
  const [targetDev, targetReg] = targetInfo.ota;

  const previousSelection = state.ota_version;
  versionSelect.innerHTML = '<option value="latest">⚡ Latest Catalog Release (Auto-detect in Action)</option>';

  if (!catalogData) {
    statusEl.textContent = `Tap 'Fetch versions' for ${targetDev} / ${targetReg}`;
    versionSelect.value = 'latest';
    state.ota_version = 'latest';
    updateOtaVersionDetails();
    return;
  }

  const entries = releasesFor(catalogData, targetDev, targetReg);
  statusEl.textContent = `${targetDev} / ${targetReg}: ${entries.length} versions found`;

  entries.forEach(entry => {
    const opt = document.createElement('option');
    opt.value = entry.version;
    const latestTag = entry.is_latest ? ' · Latest' : '';
    const patchTag = entry.security_patch ? ` · Patch ${entry.security_patch}` : '';
    opt.textContent = `${entry.version}${latestTag}${patchTag}`;
    versionSelect.appendChild(opt);
  });

  const matchingOpt = Array.from(versionSelect.options).find(o => o.value === previousSelection);
  if (matchingOpt) {
    versionSelect.value = previousSelection;
  } else if (entries.length > 0 && previousSelection !== 'latest') {
    versionSelect.value = entries[0].version;
    state.ota_version = entries[0].version;
  } else {
    versionSelect.value = 'latest';
    state.ota_version = 'latest';
  }

  updateOtaVersionDetails();
}

function updateOtaVersionDetails() {
  const versionSelect = document.getElementById('select-ota-version');
  state.ota_version = versionSelect.value;

  const targetInfo = getOtaTargets(state.device, state.flasher_type, state.region);
  const [targetDev, targetReg] = targetInfo.ota;

  const targetLabel = document.getElementById('detail-target-label');
  const modelLabel = document.getElementById('detail-model-label');
  const sizeLabel = document.getElementById('detail-size-label');
  const patchLabel = document.getElementById('detail-patch-label');
  const odmStatus = document.getElementById('odm-matched-status');
  const odmIcon = document.getElementById('odm-status-icon');
  const odmText = document.getElementById('odm-status-text');

  targetLabel.textContent = `Target: ${targetDev} / ${targetReg}`;

  const entries = catalogData ? releasesFor(catalogData, targetDev, targetReg) : [];
  const isLatest = state.ota_version === 'latest';
  const entry = isLatest
    ? (entries.length > 0 ? entries[0] : null)
    : (entries.find(e => e.version === state.ota_version) || (entries.length > 0 ? entries[0] : null));

  if (entry) {
    modelLabel.textContent = isLatest ? 'Auto-detect' : (entry.model || 'Unknown');
    sizeLabel.textContent = entry.size ? `${(entry.size / (1024**3)).toFixed(2)} GiB` : '-- GiB';
    patchLabel.textContent = entry.security_patch || (isLatest ? 'Latest patch' : 'None');

    if (needsMatchedOdm(state.device, state.flasher_type, state.region)) {
      odmStatus.classList.remove('hidden');
      const donor = matchingOdm(catalogData, entry);
      if (donor) {
        odmStatus.className = 'pt-1.5 border-t border-slate-800/60 text-[10px] flex items-start space-x-1.5 text-emerald-400 bg-emerald-500/10 p-1.5 rounded-lg';
        odmIcon.textContent = '✅';
        odmText.innerHTML = `Matched Ace 6T CN ODM: <b>${donor.version}</b> (Patch: ${donor.security_patch})`;
      } else {
        odmStatus.className = 'pt-1.5 border-t border-slate-800/60 text-[10px] flex items-start space-x-1.5 text-amber-400 bg-amber-500/10 p-1.5 rounded-lg';
        odmIcon.textContent = '⚠️';
        odmText.innerHTML = `No Ace 6T CN OTA matches patch <b>${entry.security_patch || 'unknown'}</b>. Static Bloat ODM=1 required in Advanced Options.`;
      }
    } else {
      odmStatus.classList.add('hidden');
    }
  } else {
    modelLabel.textContent = 'Auto-detect';
    sizeLabel.textContent = '-- GiB';
    patchLabel.textContent = 'Latest patch';

    if (needsMatchedOdm(state.device, state.flasher_type, state.region)) {
      odmStatus.classList.remove('hidden');
      odmStatus.className = 'pt-1.5 border-t border-slate-800/60 text-[10px] flex items-start space-x-1.5 text-blue-400 bg-blue-500/5 p-1.5 rounded-lg';
      odmIcon.textContent = 'ℹ️';
      odmText.textContent = 'Action will auto-match latest 15R OTA with compatible Ace 6T CN ODM.';
    } else {
      odmStatus.classList.add('hidden');
    }
  }

  updateArtifactPreview();
}

// =========================================================================
// UI Event Handling & Tab Navigation
// =========================================================================

function setupEventListeners() {
  // Device Selection
  document.querySelectorAll('.device-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      haptic('light');
      state.device = btn.getAttribute('data-device');
      syncButtonChoices('.device-btn', 'data-device', state.device);
      validateMatrix();
    });
  });

  // Flasher Selection
  document.querySelectorAll('.flasher-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      if (btn.classList.contains('disabled-choice')) return;
      haptic('light');
      state.flasher_type = btn.getAttribute('data-flasher');
      syncButtonChoices('.flasher-btn', 'data-flasher', state.flasher_type);
      validateMatrix();
    });
  });

  // Region Selection
  document.querySelectorAll('.region-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      if (btn.classList.contains('disabled-choice')) return;
      haptic('light');
      state.region = btn.getAttribute('data-region');
      syncButtonChoices('.region-btn', 'data-region', state.region);
      validateMatrix();
    });
  });

  // OTA Source Tabs
  const tabCatalog = document.getElementById('tab-source-catalog');
  const tabUrl = document.getElementById('tab-source-url');
  const catalogGroup = document.getElementById('ota-catalog-group');
  const urlGroup = document.getElementById('ota-url-group');

  tabCatalog.addEventListener('click', () => {
    haptic('light');
    state.isCustomUrl = false;
    tabCatalog.className = 'px-2.5 py-0.5 rounded-md bg-blue-600 text-white font-medium transition-all';
    tabUrl.className = 'px-2.5 py-0.5 rounded-md text-slate-400 hover:text-white transition-all';
    catalogGroup.classList.remove('hidden');
    urlGroup.classList.add('hidden');
    validateMatrix();
  });

  tabUrl.addEventListener('click', () => {
    haptic('light');
    state.isCustomUrl = true;
    tabUrl.className = 'px-2.5 py-0.5 rounded-md bg-blue-600 text-white font-medium transition-all';
    tabCatalog.className = 'px-2.5 py-0.5 rounded-md text-slate-400 hover:text-white transition-all';
    catalogGroup.classList.add('hidden');
    urlGroup.classList.remove('hidden');
    validateMatrix();
  });

  document.getElementById('btn-fetch-catalog').addEventListener('click', () => {
    haptic('light');
    fetchOtaCatalog(true);
  });

  document.getElementById('select-ota-version').addEventListener('change', () => {
    haptic('light');
    updateOtaVersionDetails();
  });

  document.getElementById('input-ota-url').addEventListener('input', (e) => {
    state.ota_url = e.target.value.trim();
    validateMatrix();
  });

  // Advanced Options Accordion
  const advBtn = document.getElementById('toggle-advanced');
  const advContent = document.getElementById('advanced-content');
  const advArrow = document.getElementById('advanced-arrow');
  advBtn.addEventListener('click', () => {
    haptic('light');
    advContent.classList.toggle('hidden');
    advArrow.classList.toggle('rotate-180');
  });

  // Upload Destination
  document.querySelectorAll('.upload-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      haptic('light');
      state.upload_to = btn.getAttribute('data-upload');
      syncButtonChoices('.upload-btn', 'data-upload', state.upload_to);
      updateUploadDestinationUI();
    });
  });

  document.getElementById('select-bloat').addEventListener('change', (e) => {
    state.bloat_static_odm = e.target.value;
  });

  document.getElementById('input-gofile-key').addEventListener('input', (e) => {
    state.gofile_api_key = e.target.value.trim();
  });

  document.getElementById('toggle-tg-notify').addEventListener('change', (e) => {
    state.notify_telegram = e.target.checked;
  });

  // Trigger Button
  document.getElementById('btn-trigger').addEventListener('click', handleTriggerBuild);

  // Tab Navigation
  document.getElementById('tab-builder-btn').addEventListener('click', () => switchTab('builder'));
  document.getElementById('tab-runs-btn').addEventListener('click', () => switchTab('runs'));

  document.getElementById('btn-refresh-runs').addEventListener('click', () => {
    haptic('light');
    fetchActionRuns();
  });
}

function switchTab(tab) {
  haptic('light');
  const isBuilder = tab === 'builder';
  const tabBuilderBtn = document.getElementById('tab-builder-btn');
  const tabRunsBtn = document.getElementById('tab-runs-btn');
  const tabBuilder = document.getElementById('tab-builder');
  const tabRuns = document.getElementById('tab-runs');
  const inPageBtn = document.getElementById('btn-trigger');

  if (isBuilder) {
    stopPollingRuns();
    tabBuilder.classList.remove('hidden');
    tabRuns.classList.add('hidden');

    tabBuilderBtn.classList.add('bg-blue-600', 'text-white', 'shadow', 'font-semibold');
    tabBuilderBtn.classList.remove('text-slate-400', 'hover:text-slate-200');

    tabRunsBtn.classList.remove('bg-blue-600', 'text-white', 'shadow', 'font-semibold');
    tabRunsBtn.classList.add('text-slate-400', 'hover:text-slate-200');

    // Keep runs button hidden if user cannot view runs
    if (!canUserViewRuns) {
      tabRunsBtn.classList.add('hidden');
      tabRunsBtn.classList.remove('flex');
    }

    if (isInsideTelegram && tg?.MainButton) {
      tg.MainButton.show();
    } else if (inPageBtn) {
      inPageBtn.classList.remove('hidden');
      inPageBtn.classList.add('flex');
    }
  } else {
    if (!canUserViewRuns) {
      haptic('error');
      showToast('Action runs are restricted to authorized users', true);
      return;
    }

    tabRuns.classList.remove('hidden');
    tabBuilder.classList.add('hidden');

    tabRunsBtn.classList.add('bg-blue-600', 'text-white', 'shadow', 'font-semibold');
    tabRunsBtn.classList.remove('text-slate-400', 'hover:text-slate-200');

    tabBuilderBtn.classList.remove('bg-blue-600', 'text-white', 'shadow', 'font-semibold');
    tabBuilderBtn.classList.add('text-slate-400', 'hover:text-slate-200');

    if (tg?.MainButton) tg.MainButton.hide();
    if (inPageBtn) inPageBtn.classList.add('hidden');

    fetchActionRuns();
  }
}

// =========================================================================
// Dispatch Workflow Run
// =========================================================================

async function handleTriggerBuild() {
  if (!canUserTrigger) {
    haptic('error');
    showToast('Build trigger is restricted to authorized users', true);
    return;
  }

  const devShort = DEVICE_MAP[state.device]?.alias || '15R';
  const comboKey = `${state.flasher_type}|${devShort}|${state.region}`;
  if (!BUILDER_MAP[comboKey]) {
    haptic('error');
    showToast(`Invalid combination: ${state.device} • ${state.flasher_type} • ${state.region}`, true);
    return;
  }

  const spinner = document.getElementById('spinner-icon');
  const btnText = document.getElementById('btn-trigger-text');
  const triggerBtn = document.getElementById('btn-trigger');

  spinner.classList.remove('hidden');
  btnText.textContent = 'Dispatching GitHub Action...';
  triggerBtn.disabled = true;
  if (tg?.MainButton) {
    tg.MainButton.showProgress();
    tg.MainButton.disable();
  }

  const payload = {
    device: state.device,
    flasher_type: state.flasher_type,
    region: state.region,
    ota_version: state.isCustomUrl ? '' : (state.ota_version || 'latest'),
    ota_url: state.isCustomUrl ? state.ota_url : '',
    bloat_static_odm: state.bloat_static_odm,
    upload_to: state.upload_to,
    gofile_api_key: state.gofile_api_key,
    notify_telegram: state.notify_telegram
  };

  try {
    const res = await fetch('/api/trigger', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `tma ${tg?.initData || ''}`
      },
      body: JSON.stringify(payload)
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || 'Failed to dispatch GitHub Action');
    }

    haptic('success');
    showToast('🚀 Action build triggered successfully!');

    setTimeout(() => {
      switchTab('runs');
    }, 1200);

  } catch (err) {
    console.error(err);
    haptic('error');
    showToast(err.message, true);
  } finally {
    spinner.classList.add('hidden');
    btnText.textContent = '🚀 Trigger Action Build';
    triggerBtn.disabled = false;
    if (tg?.MainButton) {
      tg.MainButton.hideProgress();
      tg.MainButton.enable();
    }
  }
}

// =========================================================================
// Real-Time Action Runs & Progress Tracking
// =========================================================================

let runsPollInterval = null;
const openStepDetails = new Set();
const cachedRunDetails = new Map();

function startPollingRuns() {
  if (runsPollInterval) return;
  const liveIndicator = document.getElementById('live-indicator');
  if (liveIndicator) {
    liveIndicator.classList.remove('hidden');
    liveIndicator.classList.add('flex');
  }

  runsPollInterval = setInterval(() => {
    fetchActionRuns(true);
  }, 4000);
}

function stopPollingRuns() {
  if (runsPollInterval) {
    clearInterval(runsPollInterval);
    runsPollInterval = null;
  }
  const liveIndicator = document.getElementById('live-indicator');
  if (liveIndicator) {
    liveIndicator.classList.add('hidden');
    liveIndicator.classList.remove('flex');
  }
}

async function fetchActionRuns(silent = false) {
  const container = document.getElementById('runs-list');

  if (!canUserViewRuns) {
    stopPollingRuns();
    container.innerHTML = `
      <div class="p-6 text-center text-xs space-y-2 bg-amber-950/20 rounded-xl border border-amber-900/40 text-amber-300">
        <div class="text-sm font-bold flex items-center justify-center space-x-1">
          <span>🔒 Access Restricted</span>
        </div>
        <p class="text-slate-400">Action runs are only visible to authorized users.</p>
        <p class="text-[10px] text-slate-500 font-mono">Your ID: ${escapeHtml(String(tg?.initDataUnsafe?.user?.id || 'Unknown'))}</p>
      </div>
    `;
    return;
  }

  if (!silent && (!container.children.length || container.querySelector('.animate-pulse'))) {
    container.innerHTML = `
      <div class="p-6 text-center text-xs text-slate-500 animate-pulse">
        Fetching workflow runs from GitHub...
      </div>
    `;
  }

  try {
    const res = await fetch('/api/runs', {
      headers: { 'Authorization': `tma ${tg?.initData || ''}` }
    });

    if (!res.ok) {
      if (res.status === 403 || res.status === 401) {
        stopPollingRuns();
        canUserViewRuns = false;
        const runsBtn = document.getElementById('tab-runs-btn');
        runsBtn.classList.add('hidden');
        runsBtn.classList.remove('flex');
        switchTab('builder');
        showToast('Action runs are only visible to authorized users', true);
        return;
      }
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || 'Failed to fetch runs');
    }

    const runsData = await res.json();
    const { workflow_runs } = runsData;
    if (typeof runsData.can_view_github === 'boolean') {
      canUserViewGithub = runsData.can_view_github;
    }
    if (typeof runsData.can_cancel === 'boolean') {
      canUserCancel = runsData.can_cancel;
    }

    if (!workflow_runs || workflow_runs.length === 0) {
      stopPollingRuns();
      container.innerHTML = `
        <div class="p-6 text-center text-xs text-slate-500">
          No recent runs found for build_macan.yml
        </div>
      `;
      return;
    }

    // Auto-polling management: if any run is in progress or queued, keep polling
    const hasActiveRun = workflow_runs.some(r => r.status === 'in_progress' || r.status === 'queued');
    if (hasActiveRun) {
      startPollingRuns();
    } else {
      stopPollingRuns();
    }

    // Update active runs badge on tab button
    const activeRunsCount = workflow_runs.filter(r => r.status === 'in_progress' || r.status === 'queued').length;
    const badge = document.getElementById('runs-badge');
    if (activeRunsCount > 0) {
      badge.textContent = activeRunsCount;
      badge.classList.remove('hidden');
    } else {
      badge.classList.add('hidden');
    }

    container.innerHTML = workflow_runs.slice(0, 10).map(run => {
      const statusBadge = getStatusBadge(run.status, run.conclusion);
      const timeAgo = formatTimeAgo(new Date(run.created_at));
      const duration = run.updated_at ? formatDuration(new Date(run.created_at), new Date(run.updated_at)) : '';
      const isRunning = run.status === 'in_progress';
      const isQueued = run.status === 'queued';
      const isExpanded = openStepDetails.has(run.id);

      // 1. Live Step Progress Bar (for in_progress / queued)
      let progressBarHtml = '';
      if (isRunning || isQueued) {
        const percent = Math.min(100, Math.max(isQueued ? 5 : 10, run.progress_percent || 0));
        const activeStepName = run.active_step || (isQueued ? 'Queued on GitHub runner...' : 'Building...');

        progressBarHtml = `
          <div class="mt-2.5 p-3 rounded-xl bg-slate-950/70 border border-blue-500/25 space-y-2">
            <div class="flex items-center justify-between text-[11px]">
              <span class="text-blue-300 font-semibold flex items-center space-x-1.5 truncate">
                <span class="w-2 h-2 rounded-full bg-blue-400 animate-ping"></span>
                <span class="truncate">${escapeHtml(activeStepName)}</span>
              </span>
              <span class="text-blue-400 font-mono font-bold ml-2 shrink-0">${percent}%</span>
            </div>
            <div class="w-full bg-slate-900 rounded-full h-2 overflow-hidden border border-slate-800">
              <div class="bg-gradient-to-r from-blue-500 via-indigo-500 to-emerald-400 h-full rounded-full transition-all duration-500 ease-out" style="width: ${percent}%"></div>
            </div>
            <div class="flex items-center justify-between text-[10px] text-slate-400 pt-0.5">
              <span>Step ${run.completed_steps || 0} of ${run.total_steps || '—'}</span>
              <span>⚡ Live Tracker</span>
            </div>
          </div>
        `;
      }

      // 2. Gofile Download Link Card (if ready)
      let gofileHtml = '';
      if (run.gofile_url) {
        gofileHtml = `
          <div class="mt-2.5 p-3 rounded-xl bg-emerald-950/30 border border-emerald-500/30 space-y-2">
            <div class="flex items-center justify-between">
              <div class="flex items-center space-x-1.5 text-emerald-400 font-semibold text-xs">
                <span class="text-sm">📦</span>
                <span>Gofile Download Ready</span>
              </div>
              <span class="px-1.5 py-0.5 rounded text-[9px] bg-emerald-500/20 text-emerald-300 font-mono">Completed</span>
            </div>
            <div class="text-[10px] text-slate-300 bg-slate-950/80 p-1.5 rounded-lg border border-emerald-500/20 truncate font-mono">
              ${escapeHtml(run.gofile_url)}
            </div>
            <div class="flex items-center space-x-2 pt-0.5">
              <button type="button" onclick="openExternalLink('${escapeHtml(run.gofile_url)}')" class="flex-1 py-1.5 px-3 bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white font-bold rounded-lg text-xs shadow-md shadow-emerald-950 flex items-center justify-center space-x-1.5 transition-all active:scale-[0.98]">
                <span>⬇️ Download Package</span>
              </button>
              <button type="button" onclick="copyToClipboard('${escapeHtml(run.gofile_url)}')" class="py-1.5 px-2.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 font-medium rounded-lg text-xs flex items-center space-x-1 transition-colors">
                <span>📋 Copy</span>
              </button>
            </div>
          </div>
        `;
      }

      // 3. Step Checklist Accordion
      const totalSteps = run.total_steps || 0;
      const stepButtonText = totalSteps > 0
        ? `Steps Details (${run.completed_steps || 0}/${totalSteps})`
        : 'Steps Details';

      const mainTitle = run.job_name || (run.display_title && run.display_title !== run.name ? run.display_title : `Run #${run.run_number}`);

      return `
        <div class="bg-slate-900/90 border border-slate-800/80 rounded-2xl p-3.5 shadow-lg space-y-2">
          <div class="flex items-center justify-between">
            <span class="text-xs font-bold text-white flex items-center space-x-1.5 min-w-0 pr-2">
              <span class="truncate">${escapeHtml(mainTitle)}</span>
            </span>
            ${statusBadge}
          </div>

          <div class="text-[11px] text-slate-400 font-medium flex items-center space-x-1.5">
            <span class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 font-mono text-[10px]">#${run.run_number}</span>
            <span class="truncate">${escapeHtml(run.name || 'Build Workflow')}</span>
          </div>

          ${progressBarHtml}
          ${gofileHtml}

          <!-- Cancel Action Build Button (for Active Runs) -->
          ${(isRunning || isQueued) && canUserCancel ? `
            <div class="pt-0.5">
              <button type="button" id="btn-cancel-${run.id}" onclick="confirmCancelRun(${run.id}, ${run.run_number})" class="w-full py-2 px-3 rounded-xl bg-rose-500/10 hover:bg-rose-500/20 active:bg-rose-500/25 border border-rose-500/30 text-rose-300 font-semibold text-xs flex items-center justify-center space-x-1.5 transition-all active:scale-[0.98]">
                <span>🛑</span>
                <span>Cancel Action Build</span>
              </button>
            </div>
          ` : ''}

          <!-- Step-by-Step Toggle -->
          <div class="pt-1">
            <button type="button" onclick="toggleStepDetails(${run.id})" class="w-full py-1.5 px-2.5 rounded-xl bg-slate-950/60 hover:bg-slate-950 border border-slate-800/80 text-[11px] text-slate-300 font-medium flex items-center justify-between transition-colors">
              <span class="flex items-center space-x-1.5">
                <span>📋</span>
                <span>${stepButtonText}</span>
              </span>
              <span id="step-arrow-${run.id}" class="text-[10px] text-slate-500 transform transition-transform duration-200 ${isExpanded ? 'rotate-180' : ''}">▼</span>
            </button>
            <div id="step-details-${run.id}" class="${isExpanded ? 'block' : 'hidden'} mt-2 pt-2 border-t border-slate-800/60 space-y-1.5">
              ${isExpanded ? renderCachedOrLoadingSteps(run.id) : ''}
            </div>
          </div>

          <div class="flex items-center justify-between pt-1 border-t border-slate-800/60 text-[10px] text-slate-400">
            <span>⏱️ ${timeAgo}${duration ? ` • ${duration}` : ''}</span>
            ${canUserViewGithub && run.html_url ? `
              <a href="${run.html_url}" target="_blank" rel="noopener noreferrer" class="text-blue-400 hover:text-blue-300 font-medium flex items-center space-x-1">
                <span>View GitHub</span>
                <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/>
                </svg>
              </a>
            ` : ''}
          </div>
        </div>
      `;
    }).join('');

    // Re-trigger live steps fetch for any currently open accordions
    for (const runId of openStepDetails) {
      loadAndRenderStepDetails(runId);
    }

  } catch (err) {
    if (!silent) {
      container.innerHTML = `
        <div class="p-6 text-center text-xs text-rose-400 bg-rose-950/20 rounded-xl border border-rose-900/40">
          ${escapeHtml(err.message)}
        </div>
      `;
    }
  }
}

// =========================================================================
// Step-by-Step Details Accordion
// =========================================================================

function renderCachedOrLoadingSteps(runId) {
  if (cachedRunDetails.has(runId)) {
    return generateStepsListHtml(cachedRunDetails.get(runId));
  }
  return `
    <div class="py-2 text-center text-[10px] text-slate-500 animate-pulse flex items-center justify-center space-x-1.5">
      <svg class="w-3 h-3 text-blue-400 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path></svg>
      <span>Loading step execution details...</span>
    </div>
  `;
}

async function toggleStepDetails(runId) {
  haptic('light');
  const detailsEl = document.getElementById(`step-details-${runId}`);
  const arrowEl = document.getElementById(`step-arrow-${runId}`);
  if (!detailsEl) return;

  if (openStepDetails.has(runId)) {
    openStepDetails.delete(runId);
    detailsEl.classList.add('hidden');
    detailsEl.classList.remove('block');
    if (arrowEl) arrowEl.classList.remove('rotate-180');
    return;
  }

  openStepDetails.add(runId);
  detailsEl.classList.remove('hidden');
  detailsEl.classList.add('block');
  if (arrowEl) arrowEl.classList.add('rotate-180');

  await loadAndRenderStepDetails(runId);
}

async function loadAndRenderStepDetails(runId) {
  const detailsEl = document.getElementById(`step-details-${runId}`);
  if (!detailsEl) return;

  try {
    const res = await fetch(`/api/run-details?run_id=${runId}`, {
      headers: { 'Authorization': `tma ${tg?.initData || ''}` }
    });
    if (!res.ok) throw new Error('Failed to load steps');
    const data = await res.json();
    cachedRunDetails.set(runId, data);
    detailsEl.innerHTML = generateStepsListHtml(data);
  } catch (err) {
    detailsEl.innerHTML = `
      <div class="py-1 text-[10px] text-rose-400">Failed to load step details: ${escapeHtml(err.message)}</div>
    `;
  }
}

function generateStepsListHtml(data) {
  const steps = data.steps || [];
  if (steps.length === 0) {
    return `<div class="py-1 text-[10px] text-slate-500">No step records available yet</div>`;
  }

  return steps.map(step => {
    let icon = '⏳';
    let statusClass = 'text-slate-400';
    let durationText = '';

    if (step.status === 'completed') {
      if (step.conclusion === 'success') {
        icon = '✅';
        statusClass = 'text-emerald-400';
      } else if (step.conclusion === 'skipped') {
        icon = '⏭️';
        statusClass = 'text-slate-500 line-through';
      } else {
        icon = '❌';
        statusClass = 'text-rose-400 font-semibold';
      }
      if (step.started_at && step.completed_at) {
        durationText = formatDuration(new Date(step.started_at), new Date(step.completed_at));
      }
    } else if (step.status === 'in_progress') {
      icon = '<span class="inline-block animate-spin">⚙️</span>';
      statusClass = 'text-blue-300 font-semibold';
      durationText = 'running...';
    }

    return `
      <div class="flex items-center justify-between text-[11px] py-1 px-2 rounded-lg bg-slate-950/40 border border-slate-800/40">
        <div class="flex items-center space-x-1.5 min-w-0">
          <span class="shrink-0 text-xs">${icon}</span>
          <span class="truncate ${statusClass}">${escapeHtml(step.name)}</span>
        </div>
        ${durationText ? `<span class="text-[9px] text-slate-500 font-mono shrink-0 ml-2">${durationText}</span>` : ''}
      </div>
    `;
  }).join('');
}

// =========================================================================
// Actions & External Link Utilities
// =========================================================================

function openExternalLink(url) {
  haptic('light');
  if (tg?.openLink) {
    tg.openLink(url);
  } else {
    window.open(url, '_blank', 'noopener,noreferrer');
  }
}

async function copyToClipboard(text) {
  try {
    if (navigator?.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const input = document.createElement('input');
      input.value = text;
      document.body.appendChild(input);
      input.select();
      document.execCommand('copy');
      document.body.removeChild(input);
    }
    haptic('light');
    showToast('📋 Copied download link to clipboard!');
  } catch (err) {
    showToast('Failed to copy to clipboard', true);
  }
}

function confirmCancelRun(runId, runNumber) {
  haptic('medium');
  const msg = `Are you sure you want to cancel Action Build #${runNumber}?`;

  if (tg && typeof tg.showConfirm === 'function') {
    tg.showConfirm(msg, (confirmed) => {
      if (confirmed) {
        cancelWorkflowRun(runId, runNumber);
      }
    });
  } else {
    if (window.confirm(msg)) {
      cancelWorkflowRun(runId, runNumber);
    }
  }
}

async function cancelWorkflowRun(runId, runNumber) {
  const btn = document.getElementById(`btn-cancel-${runId}`);
  if (btn) {
    btn.disabled = true;
    btn.classList.add('opacity-60', 'cursor-not-allowed');
    btn.innerHTML = `
      <svg class="w-3.5 h-3.5 text-rose-400 animate-spin" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
      </svg>
      <span>Cancelling build...</span>
    `;
  }

  try {
    const res = await fetch('/api/cancel-run', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `tma ${tg?.initData || ''}`
      },
      body: JSON.stringify({ run_id: runId })
    });

    const data = await res.json().catch(() => ({}));

    if (res.ok) {
      haptic('success');
      showToast(`Build #${runNumber} cancellation requested.`);
      if (tg && typeof tg.showAlert === 'function') {
        tg.showAlert(`Cancellation requested for Build #${runNumber}.`);
      }
      setTimeout(() => fetchActionRuns(true), 1200);
    } else {
      haptic('error');
      const errMsg = data.error || 'Failed to cancel build.';
      showToast(errMsg, true);
      if (tg && typeof tg.showAlert === 'function') {
        tg.showAlert(`Error: ${errMsg}`);
      }
      if (btn) {
        btn.disabled = false;
        btn.classList.remove('opacity-60', 'cursor-not-allowed');
        btn.innerHTML = `<span>🛑</span><span>Cancel Action Build</span>`;
      }
      setTimeout(() => fetchActionRuns(true), 1000);
    }
  } catch (err) {
    haptic('error');
    showToast(`Network error: ${err.message}`, true);
    if (btn) {
      btn.disabled = false;
      btn.classList.remove('opacity-60', 'cursor-not-allowed');
      btn.innerHTML = `<span>🛑</span><span>Cancel Action Build</span>`;
    }
  }
}

// Expose functions globally for HTML inline onclick handlers
window.toggleStepDetails = toggleStepDetails;
window.openExternalLink = openExternalLink;
window.copyToClipboard = copyToClipboard;
window.confirmCancelRun = confirmCancelRun;

// =========================================================================
// UI Utility Helpers
// =========================================================================

function getStatusBadge(status, conclusion) {
  if (status === 'queued') {
    return `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/30">⏳ Queued</span>`;
  }
  if (status === 'in_progress') {
    return `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-blue-500/20 text-blue-300 border border-blue-500/30 animate-pulse">⚙️ Building...</span>`;
  }
  if (status === 'completed') {
    if (conclusion === 'success') {
      return `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">✅ Succeeded</span>`;
    }
    if (conclusion === 'cancelled') {
      return `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-700/90 text-slate-300 border border-slate-600/50">⏹️ Cancelled</span>`;
    }
    return `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-rose-500/20 text-rose-300 border border-rose-500/30">❌ ${conclusion || 'Failed'}</span>`;
  }
  return `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-700 text-slate-300">${status}</span>`;
}

function formatTimeAgo(date) {
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return `${Math.max(0, seconds)}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function formatDuration(start, end) {
  const seconds = Math.floor((end.getTime() - start.getTime()) / 1000);
  const minutes = Math.floor(seconds / 60);
  const remSec = seconds % 60;
  return `${minutes}m ${remSec}s`;
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, m => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[m]));
}

function haptic(type = 'light') {
  if (!tg?.HapticFeedback) return;
  if (type === 'success' || type === 'error') {
    tg.HapticFeedback.notificationOccurred(type);
  } else {
    tg.HapticFeedback.impactOccurred(type);
  }
}

let toastTimeout;
function showToast(message, isError = false) {
  const toast = document.getElementById('toast');
  const toastMsg = document.getElementById('toast-message');
  const toastIcon = document.getElementById('toast-icon');

  toastMsg.textContent = message;
  toastIcon.textContent = isError ? '❌' : '✅';
  toast.classList.remove('opacity-0', 'pointer-events-none');

  toast.classList.toggle('border-rose-500/50', isError);
  toast.classList.toggle('bg-rose-950/90', isError);
  toast.classList.toggle('border-slate-700', !isError);
  toast.classList.toggle('bg-slate-900', !isError);

  clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => {
    toast.classList.add('opacity-0', 'pointer-events-none');
  }, 3500);
}

// Start application
window.addEventListener('DOMContentLoaded', initApp);
