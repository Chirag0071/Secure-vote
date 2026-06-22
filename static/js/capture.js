async function initCamera(videoElId) {
  const video = document.getElementById(videoElId);
  const stream = await navigator.mediaDevices.getUserMedia({ video: { width: 480, height: 360 }, audio: false });
  video.srcObject = stream;
  return new Promise((resolve) => {
    video.onloadedmetadata = () => resolve(stream);
  });
}
function captureFrame(videoElId, canvasElId) {
  const video = document.getElementById(videoElId);
  const canvas = document.getElementById(canvasElId);
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL('image/jpeg', 0.85);
}
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
