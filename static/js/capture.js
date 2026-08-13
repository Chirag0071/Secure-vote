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

let _currentStream = null;
let _currentFacingMode = 'user';
let _videoElId = null;

function _stopCurrentStream() {
  if (_currentStream) {
    _currentStream.getTracks().forEach(track => track.stop());
    _currentStream = null;
  }
}

async function _startStream(videoElId, facingMode) {
  const video = document.getElementById(videoElId);
  _stopCurrentStream();
  // { ideal: ... } (not exact) so devices that can't honor the request
  // still return *a* camera instead of hard-failing -- this is the bug
  // that made capture.js unreliable on mobile (no facingMode meant some
  // Android browsers defaulted to the rear camera for a face-login flow).
  const constraints = {
    video: {
      facingMode: { ideal: facingMode },
      width: { ideal: 480 },
      height: { ideal: 360 },
    },
    audio: false,
  };
  const stream = await navigator.mediaDevices.getUserMedia(constraints);
  video.srcObject = stream;
  _currentStream = stream;
  _currentFacingMode = facingMode;
  return new Promise((resolve) => {
    video.onloadedmetadata = () => resolve(stream);
  });
}

async function initCamera(videoElId, switchBtnId) {
  _videoElId = videoElId;
  let stream;
  try {
    stream = await _startStream(videoElId, 'user');
  } catch (err) {
    // Fall back to whatever camera is available (e.g. some laptops/tablets
    // only expose one camera and reject a facingMode request outright).
    stream = await _startStream(videoElId, 'environment');
  }

  // Only show the "switch camera" control if the device actually has more
  // than one camera to switch between.
  if (switchBtnId) {
    const btn = document.getElementById(switchBtnId);
    if (btn) {
      try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        const cameraCount = devices.filter(d => d.kind === 'videoinput').length;
        if (cameraCount > 1) {
          btn.style.display = 'inline-block';
          btn.addEventListener('click', async () => {
            const next = _currentFacingMode === 'user' ? 'environment' : 'user';
            btn.disabled = true;
            try {
              await _startStream(_videoElId, next);
            } catch (err) {
              // ignore -- keep previous stream running
            }
            btn.disabled = false;
          });
        }
      } catch (err) {
        // enumerateDevices can fail/be restricted on some browsers -- not fatal.
      }
    }
  }

  return stream;
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