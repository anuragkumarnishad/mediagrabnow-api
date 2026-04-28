/**
 * MediaGrabNow Frontend — Backend Integration
 * =============================================
 * Ye code MediaGrabNow-Final.html mein showPanel() aur startDlFmt() replace karega
 * 
 * Backend endpoints:
 *   POST /info       → video info, formats, sizes
 *   POST /download   → native browser download
 *   POST /clip       → trimmed clip download
 */

// ─── CONFIG ───────────────────────────────────────────────────────────────────
const API_BASE = 'https://api.mediagrabnow.com';

// ─── STEP 1: URL paste hone par — /info call karo ─────────────────────────────
async function fetchVideoInfo(url, platform) {
  try {
    const res = await fetch(`${API_BASE}/info`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url })
    });

    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();

    // ── Thumbnail show karo ──
    const vThumb = document.getElementById('vThumb');
    if (data.thumbnail) {
      vThumb.src = data.thumbnail;
      vThumb.style.display = 'block';
      vThumb.onerror = () => { vThumb.style.display = 'none'; };
    }

    // ── Title aur duration ──
    document.getElementById('vTitle').textContent = data.title || 'Unknown';
    document.getElementById('vDuration').textContent = data.duration || '';
    document.getElementById('vPlatName').textContent = data.platform || platform;

    // ── Video quality grid banao (real sizes ke saath) ──
    const vGrid = document.getElementById('vGrid');
    vGrid.innerHTML = '';
    (data.video_formats || []).forEach(f => {
      const card = document.createElement('div');
      card.className = 'mgn-vc';
      card.dataset.quality = f.quality;
      card.dataset.format  = 'mp4';
      card.dataset.type    = 'video';

      // Quality color
      const colors = {
        '4K':'#a855f7','2K':'#8b5cf6','1080p':'#6366f1',
        '720p':'#3b82f6','480p':'#22c55e','360p':'#eab308',
        '240p':'#f97316','144p':'#94a3b8'
      };
      const color = colors[f.quality] || '#6366f1';

      card.innerHTML = `
        <div class="mgn-vc-top">
          <span class="mgn-vc-res">${f.quality}</span>
          <span class="mgn-vc-fmt">${f.format}</span>
        </div>
        <div class="mgn-vc-size">${f.size}</div>
        <div class="mgn-vc-bot">
          <span class="mgn-qdot" style="background:${color}"></span>
          <button class="mgn-dlbtn" onclick="nativeDownload(this,'${url}','${f.quality}','mp4','video','${platform}')" title="Download ${f.quality}">
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="var(--c1)" stroke-width="2" stroke-linecap="round">
              <path d="M8 2v9M4 8l4 4 4-4"/><path d="M2 14h12"/>
            </svg>
          </button>
        </div>`;
      vGrid.appendChild(card);
    });

    // ── Audio grid banao (real sizes ke saath) ──
    const aGrid = document.getElementById('aGrid');
    aGrid.innerHTML = '';
    (data.audio_formats || []).forEach(f => {
      const row = document.createElement('div');
      row.className = 'mgn-ac';
      row.innerHTML = `
        <div class="mgn-wave">
          <div class="mgn-wb" style="height:${4 + Math.random()*8}px"></div>
          <div class="mgn-wb" style="height:${4 + Math.random()*8}px"></div>
          <div class="mgn-wb" style="height:${4 + Math.random()*8}px"></div>
          <div class="mgn-wb" style="height:${4 + Math.random()*8}px"></div>
          <div class="mgn-wb" style="height:${4 + Math.random()*8}px"></div>
        </div>
        <div class="mgn-ac-info">
          <div class="mgn-ac-kbps">${f.quality}</div>
          <div class="mgn-ac-sz">${f.size}</div>
        </div>
        <span class="mgn-ac-fmt">${f.format}</span>
        <button class="mgn-sm-btn" onclick="nativeDownload(this,'${url}','${f.quality}','mp3','audio','${platform}')">
          Download
        </button>`;
      aGrid.appendChild(row);
    });

    return data;

  } catch (err) {
    console.warn('Info fetch failed:', err.message);
    return null;
  }
}

// ─── STEP 2: Download button click — native browser download ──────────────────
async function nativeDownload(btn, url, quality, format, type, platform) {
  if (!url) { showToast('❗', 'No URL found!'); return; }

  // Button loading state
  const origHTML = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none">
    <circle cx="12" cy="12" r="9" stroke="var(--c1)" stroke-width="2.5" stroke-dasharray="40" stroke-dashoffset="10">
      <animateTransform attributeName="transform" type="rotate" dur="0.7s" from="0 12 12" to="360 12 12" repeatCount="indefinite"/>
    </circle></svg>`;

  // Progress bar show karo
  const pa   = document.getElementById('progArea');
  const fill = document.getElementById('progFill');
  const lbl  = document.getElementById('progLbl');
  const pct  = document.getElementById('progPct');
  pa.classList.add('show');
  fill.style.width = '0%'; pct.textContent = '0%';

  // Smooth fake progress (real download ke saath)
  let v = 0;
  const messages = ['🔍 Detecting media…','⚙️ Processing quality…','📦 Preparing file…','⬇️ Starting download…'];
  const iv = setInterval(() => {
    if (v < 25)      { v += 5; lbl.textContent = messages[0]; }
    else if (v < 55) { v += 4; lbl.textContent = messages[1]; }
    else if (v < 80) { v += 2; lbl.textContent = messages[2]; }
    else if (v < 92) { v += 1; lbl.textContent = messages[3]; }
    else clearInterval(iv);
    fill.style.width = v + '%';
    pct.textContent  = v + '%';
  }, 150);

  try {
    // ── Backend se file maango ──
    const res = await fetch(`${API_BASE}/download`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Client': 'mediagrabnow-web-v1'
      },
      body: JSON.stringify({ url, quality, format, type, platform, noWatermark: true })
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Server error' }));
      throw new Error(err.detail || 'Download failed');
    }

    // ── File blob banao ──
    const blob = await res.blob();

    // Filename header se lo ya default banao
    const disposition = res.headers.get('Content-Disposition') || '';
    const nameMatch   = disposition.match(/filename="?([^";\n]+)"?/);
    const filename    = nameMatch ? nameMatch[1] : `mediagrabnow_${platform}_${quality}.${format}`;

    // ── Native browser download trigger ──
    // Browser ka download dialog open hoga — koi redirect nahi, koi new tab nahi
    const objectUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href     = objectUrl;
    a.download = filename;          // ← ye line browser ko download karne par force karti hai
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);

    // Cleanup
    setTimeout(() => URL.revokeObjectURL(objectUrl), 10000);

    // ── Success ──
    clearInterval(iv);
    fill.style.width = '100%'; pct.textContent = '100%';
    lbl.textContent  = '✅ Download started!';
    showToast('✅', `${quality} ${format.toUpperCase()} — Downloading!`);

    setTimeout(() => {
      pa.classList.remove('show');
      document.getElementById('succArea').classList.add('show');
    }, 800);

  } catch (err) {
    clearInterval(iv);
    pa.classList.remove('show');
    fill.style.width = '0%';
    btn.disabled  = false;
    btn.innerHTML = origHTML;
    showToast('❌', err.message || 'Download failed!');
    console.error('Download error:', err);
  } finally {
    btn.disabled  = false;
    btn.innerHTML = origHTML;
  }
}

// ─── STEP 3: Clip download ─────────────────────────────────────────────────────
async function triggerClipDl(btn) {
  const url     = document.getElementById('mainInput').value.trim();
  const sm      = document.getElementById('sm')?.value || '0';
  const ss      = document.getElementById('ss')?.value || '0';
  const em      = document.getElementById('em')?.value || '1';
  const es      = document.getElementById('es')?.value || '0';
  const quality = document.querySelector('.mgn-qactive')?.textContent || '1080p';
  const format  = document.getElementById('fmp3')?.classList.contains('mgn-fmt-active') ? 'mp3' : 'mp4';

  const start = `${sm}:${ss.toString().padStart(2,'0')}`;
  const end   = `${em}:${es.toString().padStart(2,'0')}`;

  if (!url) { showToast('❗', 'No URL!'); return; }

  btn.disabled   = true;
  btn.textContent = 'Preparing clip…';

  try {
    const res = await fetch(
      `${API_BASE}/clip?url=${encodeURIComponent(url)}&start=${start}&end=${end}&quality=${quality}&format=${format}`,
      { method: 'POST' }
    );

    if (!res.ok) throw new Error('Clip failed');
    const blob = await res.blob();
    const a    = document.createElement('a');
    a.href     = URL.createObjectURL(blob);
    a.download = `clip_${start.replace(':','-')}_${end.replace(':','-')}.${format}`;
    a.click();
    showToast('✅', `Clip ${start}→${end} downloading!`);

  } catch (err) {
    showToast('❌', 'Clip failed: ' + err.message);
  } finally {
    btn.disabled    = false;
    btn.innerHTML   = `<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round"><path d="M8 1v10M4 8l4 4 4-4"/><path d="M1 14h14"/></svg> Download clip`;
  }
}
