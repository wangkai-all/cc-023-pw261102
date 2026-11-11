const BASE = ''

async function get(path) {
  const res = await fetch(BASE + path)
  if (!res.ok) throw new Error(`${path} -> HTTP ${res.status}`)
  return res.json()
}

export const api = {
  meta: () => get('/api/meta'),
  samples: () => get('/api/samples'),
  sample: (id) => get(`/api/samples/${encodeURIComponent(id)}`),
}
