/**
 * Tiny typed-by-convention API client. No framework, no build step.
 */

export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function request(path, { method = 'GET', body, signal } = {}) {
  let response;
  try {
    response = await fetch(path, {
      method,
      credentials: 'same-origin',
      headers: body === undefined ? { Accept: 'application/json' } : {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (error?.name === 'AbortError') throw error;
    throw new ApiError('无法连接后端服务，请确认服务已启动', 0);
  }

  if (response.status === 204) return null;

  const contentType = response.headers.get('content-type') || '';
  let payload = null;
  if (contentType.includes('application/json')) {
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
  } else {
    const text = await response.text().catch(() => '');
    payload = text ? { detail: text } : null;
  }

  if (!response.ok) {
    let detail = payload?.detail;
    if (Array.isArray(detail)) {
      detail = detail.map((item) => item?.msg || String(item)).join('；');
    }
    if (!detail) detail = `请求失败（HTTP ${response.status}）`;
    throw new ApiError(String(detail), response.status);
  }
  return payload;
}

export const api = {
  listStreams: () => request('/api/streams'),
  createStream: (payload) => request('/api/streams', { method: 'POST', body: payload }),
  updateStream: (id, payload) => request(`/api/streams/${id}`, { method: 'PUT', body: payload }),
  deleteStream: (id) => request(`/api/streams/${id}`, { method: 'DELETE' }),
  checkStream: (id) => request(`/api/streams/${id}/check`, { method: 'POST' }),
  startPreview: (id) => request(`/api/streams/${id}/preview/start`, { method: 'POST' }),
  previewReady: (id) => request(`/api/streams/${id}/preview/ready`),
  stopPreview: (id) => request(`/api/streams/${id}/preview/stop`, { method: 'POST' }),
  startRecording: (id) => request(`/api/streams/${id}/recording/start`, { method: 'POST' }),
  stopRecording: (id) => request(`/api/streams/${id}/recording/stop`, { method: 'POST' }),
  listRecordings: (limit = 200) => request(`/api/recordings?limit=${limit}`),
  deleteRecording: (id) => request(`/api/recordings/${id}`, { method: 'DELETE' }),
  systemInfo: () => request('/api/system/info'),
  health: () => request('/api/health'),
};
