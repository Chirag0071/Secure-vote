async function postJSON(url, body, timeoutMs = 90000) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    let data;
    try {
      data = await res.json();
    } catch (parseErr) {
      return { ok: false, error: `Server returned an unexpected response (status ${res.status}). Please try again.` };
    }
    return { ok: data.ok, data, error: data.error };
  } catch (err) {
    if (err.name === 'AbortError') {
      return { ok: false, error: 'This is taking longer than expected (the server may be starting up). Please try again in a moment.' };
    }
    return { ok: false, error: 'Network error -- could not reach the server. Check your connection and try again.' };
  } finally {
    clearTimeout(timeoutId);
  }
}

async function initCamera(videoElId) {
  const video = document.getElementById(videoElId);
  const stream = await navigator.mediaDevices.getUserMedia({ video: { width: 480, height: 360 }, audio: false });
  video.srcObject = stream;
  return new Promise((resolve) => {
    video.onloadedmetadata = () => resolve(stream);
  });
}

// Captures a single frame as a JPEG data URL.
function captureFrame(videoElId, canvasElId) {
  const video = document.getElementById(videoElId);
  const canvas = document.getElementById(canvasElId);
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL('image/jpeg', 0.85);
}

// Captures `count` frames spaced `intervalMs` apart -- used for the
// liveness burst (give the voter time to blink naturally).
function captureBurst(videoElId, canvasElId, count, intervalMs) {
  return new Promise((resolve) => {
    const frames = [];
    let taken = 0;
    const tick = () => {
      frames.push(captureFrame(videoElId, canvasElId));
      taken += 1;
      if (taken >= count) {
        resolve(frames);
      } else {
        setTimeout(tick, intervalMs);
      }
    };
    tick();
  });
}