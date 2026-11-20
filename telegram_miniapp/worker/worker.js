/**
 * Cloudflare Worker Backend for Flasher Builder Macan Telegram Mini App
 * 
 * Required Secrets (wrangler secret put <NAME>):
 * - GITHUB_PAT: GitHub Personal Access Token (Actions read/write)
 * - TELEGRAM_BOT_TOKEN: Bot token from @BotFather
 * 
 * Optional Environment Variables:
 * - GITHUB_REPO: "owner/repo" (default: "bt75gh48/Flasher_Builder")
 * - GITHUB_BRANCH: Target git branch (default: "main")
 * - ALLOWED_TELEGRAM_USER_IDS: Comma-separated list of authorized Telegram user IDs
 */

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

const DEVICE_ALIAS = {
  'Oneplus 15R': '15R',
  'Oneplus Ace 6T': 'Ace6T'
};

const OTA_API = 'https://roms.danielspringer.at/api/ota.php';
const NA_API = 'https://roms.danielspringer.at/api/na.php';

export default {
  async fetch(request, env) {
    // Handle CORS preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: getCorsHeaders() });
    }

    const { pathname } = new URL(request.url);

    // API Routes
    switch (pathname) {
      case '/api/auth-status':
        if (request.method === 'GET') return handleAuthStatus(request, env);
        break;
      case '/api/catalog':
        if (request.method === 'GET') return handleGetCatalog(request);
        break;
      case '/api/trigger':
        if (request.method === 'POST') return handleTrigger(request, env);
        break;
      case '/api/runs':
        if (request.method === 'GET') return handleGetRuns(request, env);
        break;
      case '/api/run-details':
        if (request.method === 'GET') return handleGetRunDetails(request, env);
        break;
      case '/api/cancel-run':
        if (request.method === 'POST') return handleCancelRun(request, env);
        break;
      case '/api/health':
        return jsonResponse({ status: 'ok', time: new Date().toISOString() });
    }

    // Serve static assets from public/ directory if ASSETS binding exists
    if (env.ASSETS) {
      return env.ASSETS.fetch(request);
    }

    return new Response('Not Found', { status: 404 });
  }
};

/**
 * Helper to check whether public action runs & build triggering is enabled
 */
function isPublicAccessEnabled(env) {
  const val = String(env.ENABLE_PUBLIC_ACTION_RUNS || '').trim().toLowerCase();
  return val === 'true' || val === '1' || val === 'yes';
}

/**
 * Check User Authentication, Whitelist Status & Permissions
 */
async function handleAuthStatus(request, env) {
  const auth = await authenticateTelegram(request, env);
  const whitelistConfigured = Boolean(env.ALLOWED_TELEGRAM_USER_IDS && env.ALLOWED_TELEGRAM_USER_IDS.trim());
  const publicAccess = isPublicAccessEnabled(env);

  if (!auth.ok) {
    return jsonResponse({
      ok: false,
      user: null,
      whitelist_configured: whitelistConfigured,
      public_access: publicAccess,
      is_whitelisted: false,
      can_trigger: false,
      can_view_runs: false,
      can_cancel: false,
      can_view_github: false,
      error: auth.error
    });
  }

  return jsonResponse({
    ok: true,
    user: auth.user || null,
    whitelist_configured: whitelistConfigured,
    public_access: publicAccess,
    is_whitelisted: auth.isWhitelisted,
    can_trigger: auth.canTrigger,
    can_view_runs: auth.canViewRuns,
    can_cancel: auth.canCancel,
    can_view_github: auth.canViewGithub
  });
}

/**
 * Handle Workflow Dispatch Trigger
 */
async function handleTrigger(request, env) {
  const auth = await authenticateTelegram(request, env);
  if (!auth.ok) {
    return jsonResponse({ error: auth.error }, 401);
  }

  if (!auth.canTrigger) {
    return jsonResponse({
      error: 'Access denied: Build triggering is restricted to authorized users.'
    }, 403);
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return jsonResponse({ error: 'Invalid JSON request body' }, 400);
  }

  const {
    device,
    flasher_type,
    region,
    ota_version,
    ota_url,
    bloat_static_odm,
    upload_to,
    gofile_api_key,
    notify_telegram
  } = body;

  const devShort = DEVICE_ALIAS[device];
  if (!devShort) {
    return jsonResponse({ error: `Unknown device: "${device}"` }, 400);
  }

  const comboKey = `${flasher_type}|${devShort}|${region}`;
  if (!BUILDER_MAP[comboKey]) {
    return jsonResponse({
      error: `Unsupported builder combination: ${device} • ${flasher_type} • ${region}`
    }, 400);
  }

  const repo = env.GITHUB_REPO || 'bt75gh48/Flasher_Builder';
  const branch = env.GITHUB_BRANCH || 'main';
  const pat = env.GITHUB_PAT;

  if (!pat) {
    return jsonResponse({ error: 'Server misconfiguration: GITHUB_PAT secret is missing' }, 500);
  }

  const inputs = {
    device: String(device),
    flasher_type: String(flasher_type),
    region: String(region),
    ota_version: ota_url ? '' : String(ota_version || 'latest').trim(),
    ota_url: String(ota_url || '').trim(),
    bloat_static_odm: String(bloat_static_odm === '1' ? '1' : '0'),
    upload_to: String(upload_to === 'Artifact Only' ? 'Artifact Only' : 'Gofile'),
    notify_telegram: notify_telegram === true || notify_telegram === 'true'
  };

  if (gofile_api_key && gofile_api_key.trim()) {
    inputs.gofile_api_key = gofile_api_key.trim();
  }

  // Dispatch workflow run to GitHub REST API
  const ghEndpoint = `https://api.github.com/repos/${repo}/actions/workflows/build_macan.yml/dispatches`;
  const ghResp = await fetch(ghEndpoint, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${pat}`,
      'Accept': 'application/vnd.github+json',
      'User-Agent': 'FlasherBuilder-MiniApp/2.0',
      'X-GitHub-Api-Version': '2022-11-28',
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ ref: branch, inputs })
  });

  if (!ghResp.ok) {
    const errText = await ghResp.text();
    return jsonResponse({ error: `GitHub API error (${ghResp.status}): ${errText}` }, ghResp.status);
  }

  return jsonResponse({
    ok: true,
    message: 'Build workflow triggered successfully',
    repo,
    branch,
    user: auth.user?.username || auth.user?.id
  });
}

/**
 * Handle Workflow Run Cancellation
 */
async function handleCancelRun(request, env) {
  const auth = await authenticateTelegram(request, env);
  if (!auth.ok) {
    return jsonResponse({ error: auth.error }, 401);
  }

  if (!auth.canCancel && !auth.canTrigger) {
    return jsonResponse({
      error: 'Access denied: You do not have permission to cancel builds.'
    }, 403);
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return jsonResponse({ error: 'Invalid JSON request body' }, 400);
  }

  const runId = body.run_id || body.runId;
  if (!runId) {
    return jsonResponse({ error: 'Missing run_id parameter' }, 400);
  }

  const repo = env.GITHUB_REPO || 'bt75gh48/Flasher_Builder';
  const pat = env.GITHUB_PAT;
  if (!pat) {
    return jsonResponse({ error: 'GitHub PAT is not configured on the server' }, 500);
  }

  const ghEndpoint = `https://api.github.com/repos/${repo}/actions/runs/${runId}/cancel`;
  const headers = {
    'Authorization': `Bearer ${pat}`,
    'Accept': 'application/vnd.github+json',
    'User-Agent': 'FlasherBuilder-MiniApp/2.0',
    'X-GitHub-Api-Version': '2022-11-28'
  };

  try {
    const ghResp = await fetch(ghEndpoint, {
      method: 'POST',
      headers
    });

    if (ghResp.status === 202) {
      return jsonResponse({
        ok: true,
        message: `Cancellation requested for build run #${runId}.`,
        run_id: runId
      });
    }

    if (ghResp.status === 409) {
      return jsonResponse({
        error: 'Run cannot be cancelled (it may have already completed or been cancelled).'
      }, 409);
    }

    const errText = await ghResp.text();
    return jsonResponse({
      error: `GitHub API error (${ghResp.status}): ${errText}`
    }, ghResp.status);
  } catch (err) {
    return jsonResponse({
      error: `Failed to communicate with GitHub API: ${err.message}`
    }, 502);
  }
}

// In-memory cache for resolved Gofile URLs to minimize GitHub API calls
const gofileCache = new Map();

/**
 * Handle Workflow Runs Query (Restricted to ALLOWED_TELEGRAM_USER_IDS or public when ENABLE_PUBLIC_ACTION_RUNS is true)
 */
async function handleGetRuns(request, env) {
  const auth = await authenticateTelegram(request, env);
  if (!auth.ok) {
    return jsonResponse({
      error: auth.error || 'Unauthorized'
    }, 401);
  }

  if (!auth.canViewRuns) {
    return jsonResponse({
      error: 'Access denied: Action runs are restricted to authorized Telegram users.'
    }, 403);
  }

  const repo = env.GITHUB_REPO || 'bt75gh48/Flasher_Builder';
  const pat = env.GITHUB_PAT;
  const ghEndpoint = `https://api.github.com/repos/${repo}/actions/workflows/build_macan.yml/runs?per_page=12`;
  const headers = {
    'Accept': 'application/vnd.github+json',
    'User-Agent': 'FlasherBuilder-MiniApp/2.0',
    'X-GitHub-Api-Version': '2022-11-28'
  };

  if (pat) {
    headers['Authorization'] = `Bearer ${pat}`;
  }

  const ghResp = await fetch(ghEndpoint, { headers });
  if (!ghResp.ok) {
    const errText = await ghResp.text();
    return jsonResponse({ error: `GitHub API error (${ghResp.status}): ${errText}` }, ghResp.status);
  }

  const data = await ghResp.json();
  const rawRuns = data.workflow_runs || [];

  // Concurrently enrich the top runs with live progress & Gofile URL
  const enrichedRuns = await Promise.all(
    rawRuns.map(async (r, index) => {
      const runItem = {
        id: r.id,
        name: r.name,
        display_title: r.display_title,
        run_number: r.run_number,
        status: r.status,
        conclusion: r.conclusion,
        html_url: auth.canViewGithub ? r.html_url : null,
        created_at: r.created_at,
        updated_at: r.updated_at,
        job_id: null,
        job_name: null,
        active_step: null,
        completed_steps: 0,
        total_steps: 0,
        progress_percent: r.status === 'completed' ? 100 : 0,
        gofile_url: null
      };

      // Enrich active runs, queued runs, and the latest 5 completed runs
      if (r.status === 'in_progress' || r.status === 'queued' || index < 5) {
        try {
          const jResp = await fetch(`https://api.github.com/repos/${repo}/actions/runs/${r.id}/jobs`, { headers });
          if (jResp.ok) {
            const jData = await jResp.json();
            const job = jData.jobs?.[0];
            if (job) {
              runItem.job_id = job.id;
              runItem.job_name = job.name || null;
              const steps = job.steps || [];
              runItem.total_steps = steps.length;
              runItem.completed_steps = steps.filter(s => s.status === 'completed').length;
              const inProg = steps.find(s => s.status === 'in_progress');

              if (inProg) {
                runItem.active_step = inProg.name;
              } else if (r.status === 'completed') {
                runItem.active_step = r.conclusion === 'success' ? 'Completed successfully' : `Completed with ${r.conclusion}`;
              } else {
                runItem.active_step = 'Queued on GitHub runner...';
              }

              if (r.status === 'in_progress' && runItem.total_steps > 0) {
                const rawPercent = Math.round((runItem.completed_steps / runItem.total_steps) * 100);
                runItem.progress_percent = inProg
                  ? Math.min(95, Math.max(5, rawPercent + Math.round(50 / runItem.total_steps)))
                  : rawPercent;
              }

              if (job.id) {
                runItem.gofile_url = await fetchGofileUrlForJob(repo, job.id, headers);
              }
            }
          }
        } catch (_) {}
      }

      return runItem;
    })
  );

  return jsonResponse({
    total_count: data.total_count,
    can_view_github: auth.canViewGithub,
    can_cancel: auth.canCancel,
    workflow_runs: enrichedRuns
  });
}

/**
 * Handle Detailed Run Steps & Real-Time Progress Query
 */
async function handleGetRunDetails(request, env) {
  const auth = await authenticateTelegram(request, env);
  if (!auth.ok) {
    return jsonResponse({
      error: auth.error || 'Unauthorized'
    }, 401);
  }

  if (!auth.canViewRuns) {
    return jsonResponse({
      error: 'Access denied: Action runs are restricted to authorized users.'
    }, 403);
  }

  const url = new URL(request.url);
  const runId = url.searchParams.get('run_id');
  if (!runId) {
    return jsonResponse({ error: 'Missing run_id parameter' }, 400);
  }

  const repo = env.GITHUB_REPO || 'bt75gh48/Flasher_Builder';
  const pat = env.GITHUB_PAT;
  const headers = {
    'Accept': 'application/vnd.github+json',
    'User-Agent': 'FlasherBuilder-MiniApp/2.0',
    'X-GitHub-Api-Version': '2022-11-28'
  };

  if (pat) {
    headers['Authorization'] = `Bearer ${pat}`;
  }

  try {
    const jobsResp = await fetch(`https://api.github.com/repos/${repo}/actions/runs/${runId}/jobs`, { headers });
    if (!jobsResp.ok) {
      const errText = await jobsResp.text();
      return jsonResponse({ error: `GitHub API error (${jobsResp.status}): ${errText}` }, jobsResp.status);
    }

    const jobsData = await jobsResp.json();
    const job = jobsData.jobs?.[0];

    if (!job) {
      return jsonResponse({
        ok: true,
        can_view_github: auth.canViewGithub,
        run_id: runId,
        status: 'queued',
        conclusion: null,
        steps: [],
        active_step: 'Waiting for GitHub runner...',
        progress_percent: 0,
        gofile_url: null
      });
    }

    const steps = (job.steps || []).map(s => ({
      number: s.number,
      name: s.name,
      status: s.status, // 'queued', 'in_progress', 'completed'
      conclusion: s.conclusion, // 'success', 'failure', 'skipped', null
      started_at: s.started_at,
      completed_at: s.completed_at
    }));

    const totalSteps = steps.length;
    const completedSteps = steps.filter(s => s.status === 'completed').length;
    const inProgressStep = steps.find(s => s.status === 'in_progress');
    const activeStep = inProgressStep ? inProgressStep.name : (job.status === 'completed' ? (job.conclusion === 'success' ? 'Completed successfully' : `Finished (${job.conclusion})`) : 'Waiting for runner...');

    let progressPercent = 0;
    if (job.status === 'completed') {
      progressPercent = 100;
    } else if (totalSteps > 0) {
      const rawPercent = Math.round((completedSteps / totalSteps) * 100);
      progressPercent = inProgressStep ? Math.min(95, Math.max(5, rawPercent + Math.round(50 / totalSteps))) : rawPercent;
    }

    let gofileUrl = null;
    if (job.id) {
      gofileUrl = await fetchGofileUrlForJob(repo, job.id, headers);
    }

    return jsonResponse({
      ok: true,
      can_view_github: auth.canViewGithub,
      run_id: runId,
      job_id: job.id,
      job_name: job.name,
      status: job.status,
      conclusion: job.conclusion,
      steps,
      total_steps: totalSteps,
      completed_steps: completedSteps,
      active_step: activeStep,
      progress_percent: progressPercent,
      gofile_url: gofileUrl,
      started_at: job.started_at,
      completed_at: job.completed_at
    });
  } catch (err) {
    return jsonResponse({ error: `Failed to fetch run details: ${err.message}` }, 500);
  }
}

/**
 * Fetch Gofile Download URL from GitHub Check Run Annotations
 */
async function fetchGofileUrlForJob(repo, jobId, headers) {
  if (gofileCache.has(jobId)) {
    return gofileCache.get(jobId);
  }

  try {
    const annotResp = await fetch(`https://api.github.com/repos/${repo}/check-runs/${jobId}/annotations`, { headers });
    if (annotResp.ok) {
      const annotations = await annotResp.json();
      if (Array.isArray(annotations)) {
        for (const ann of annotations) {
          if (ann.title === 'gofile_download_url' && ann.message) {
            const url = ann.message.trim();
            gofileCache.set(jobId, url);
            return url;
          }
          // Also match regex pattern if formatted differently
          const match = (ann.message || '').match(/https:\/\/gofile\.io\/d\/[a-zA-Z0-9_-]+/);
          if (match) {
            const url = match[0];
            gofileCache.set(jobId, url);
            return url;
          }
        }
      }
    }
  } catch (err) {
    console.warn('Error querying annotations for Gofile link:', err);
  }

  return null;
}

/**
 * Handle OTA Catalog Feed Query (Cached for 15 minutes)
 */
async function handleGetCatalog(request) {
  const url = new URL(request.url);
  const isNa = url.searchParams.get('na') === '1' || url.searchParams.get('na') === 'true';
  const targetUrl = isNa ? NA_API : OTA_API;

  try {
    const resp = await fetch(targetUrl, {
      headers: {
        'User-Agent': 'FlasherBuilder/1.0',
        'Accept': 'application/json'
      },
      cf: {
        cacheTtl: 900,
        cacheEverything: true
      }
    });

    if (!resp.ok) {
      return jsonResponse({ error: `OTA catalog service returned HTTP ${resp.status}` }, resp.status);
    }

    const data = await resp.json();
    return jsonResponse(data, 200, {
      'Cache-Control': 'public, max-age=900, s-maxage=900'
    });
  } catch (err) {
    return jsonResponse({ error: `Could not reach OTA catalog service: ${err.message}` }, 502);
  }
}

/**
 * Determine granular permissions for a Telegram user
 */
function checkUserPermissions(user, env, devMode = false) {
  const publicAccess = isPublicAccessEnabled(env);
  const allowedStr = env.ALLOWED_TELEGRAM_USER_IDS || '';
  const allowed = allowedStr.split(',').map(s => s.trim()).filter(Boolean);
  const userId = String(user?.id || '');

  let isWhitelisted = false;
  if (devMode) {
    isWhitelisted = true;
  } else if (allowed.length > 0 && userId) {
    isWhitelisted = allowed.includes(userId);
  }

  // Universal Build & Monitoring Access when ENABLE_PUBLIC_ACTION_RUNS is true;
  // otherwise strictly restricted to ALLOWED_TELEGRAM_USER_IDS.
  const canTrigger = isWhitelisted || publicAccess;
  const canViewRuns = isWhitelisted || publicAccess;
  const canCancel = isWhitelisted || publicAccess;

  // Regardless of the environment toggle, "View GitHub" is exclusively for ALLOWED_TELEGRAM_USER_IDS.
  const canViewGithub = isWhitelisted;

  return {
    isWhitelisted,
    canTrigger,
    canViewRuns,
    canCancel,
    canViewGithub,
    publicAccess
  };
}

/**
 * Authenticate Telegram initData and calculate permissions
 */
async function authenticateTelegram(request, env) {
  const authHeader = request.headers.get('Authorization') || '';
  const initData = authHeader.startsWith('tma ') ? authHeader.slice(4).trim() : '';

  // Local development fallback if bot token is not configured
  if (!env.TELEGRAM_BOT_TOKEN) {
    const devUser = { id: 999999999, first_name: 'DevAdmin', username: 'devadmin' };
    const perms = checkUserPermissions(devUser, env, true);
    return {
      ok: true,
      devMode: true,
      user: devUser,
      ...perms
    };
  }

  if (!initData) {
    const publicAccess = isPublicAccessEnabled(env);
    if (publicAccess) {
      // Allow anonymous web visitor when public access is enabled
      const webUser = { id: 'web_visitor', first_name: 'Web Visitor', username: 'guest' };
      const perms = checkUserPermissions(webUser, env, false);
      return {
        ok: true,
        user: webUser,
        isWebGuest: true,
        ...perms
      };
    }
    return {
      ok: false,
      error: 'Access restricted: Direct web access is disabled. Please launch this app via the Telegram Bot.'
    };
  }

  const verification = await verifyTelegramWebAppData(initData, env.TELEGRAM_BOT_TOKEN);
  if (!verification.valid) {
    return { ok: false, error: `Unauthorized: ${verification.reason}` };
  }

  const perms = checkUserPermissions(verification.user, env, false);

  return {
    ok: true,
    user: verification.user,
    ...perms
  };
}

/**
 * Verify Telegram WebApp initData HMAC-SHA256 signature
 */
async function verifyTelegramWebAppData(telegramInitData, botToken) {
  try {
    const params = new URLSearchParams(telegramInitData);
    const hash = params.get('hash');
    if (!hash) return { valid: false, reason: 'Missing hash parameter' };

    // Check expiration (24h)
    const authDate = parseInt(params.get('auth_date') || '0', 10);
    const now = Math.floor(Date.now() / 1000);
    if (authDate > 0 && now - authDate > 86400) {
      return { valid: false, reason: 'Session expired (older than 24 hours)' };
    }

    params.delete('hash');
    const keys = Array.from(params.keys()).sort();
    const dataCheckString = keys.map(k => `${k}=${params.get(k)}`).join('\n');

    const encoder = new TextEncoder();
    const secretKeyMaterial = await crypto.subtle.importKey(
      'raw',
      encoder.encode('WebAppData'),
      { name: 'HMAC', hash: 'SHA-256' },
      false,
      ['sign']
    );

    const secretKeyBuffer = await crypto.subtle.sign(
      'HMAC',
      secretKeyMaterial,
      encoder.encode(botToken)
    );

    const secretKey = await crypto.subtle.importKey(
      'raw',
      secretKeyBuffer,
      { name: 'HMAC', hash: 'SHA-256' },
      false,
      ['sign']
    );

    const signatureBuffer = await crypto.subtle.sign(
      'HMAC',
      secretKey,
      encoder.encode(dataCheckString)
    );

    const calculatedHash = Array.from(new Uint8Array(signatureBuffer))
      .map(b => b.toString(16).padStart(2, '0'))
      .join('');

    if (calculatedHash !== hash) {
      return { valid: false, reason: 'HMAC signature verification failed' };
    }

    let user = null;
    const userStr = params.get('user');
    if (userStr) {
      try {
        user = JSON.parse(userStr);
      } catch (_) {}
    }

    return { valid: true, user };
  } catch (err) {
    return { valid: false, reason: err.message };
  }
}

function jsonResponse(data, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'Content-Type': 'application/json',
      ...getCorsHeaders(),
      ...extraHeaders
    }
  });
}

function getCorsHeaders() {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization'
  };
}
