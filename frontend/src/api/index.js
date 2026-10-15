import axios from 'axios'

const http = axios.create({ timeout: 120000 })

http.interceptors.request.use((config) => {
  const token = localStorage.getItem('ragproxy_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

http.interceptors.response.use(
  (resp) => resp,
  (err) => {
    if (err.response && err.response.status === 401 && !err.config.url.includes('/auth/login')) {
      localStorage.removeItem('ragproxy_token')
      if (location.pathname !== '/login') {
        location.href = '/login'
      }
    }
    return Promise.reject(err)
  }
)

export function errMsg(e) {
  const d = e.response && e.response.data
  const fallback = localStorage.getItem('ragproxy_locale') === 'en' ? 'Request failed' : '请求失败'
  return (d && (d.detail || d.message || JSON.stringify(d))) || e.message || fallback
}

export function getApiSettings() { return http.get('/admin/api') }
export function getApiModels() { return http.get('/admin/api/models') }
export function updateApiSettings(enabled) { return http.put('/admin/api', { enabled }) }
export function createApiKey(payload) { return http.post('/admin/api/keys', payload) }
export function updateApiKey(id, payload) { return http.put(`/admin/api/keys/${id}`, payload) }
export function deleteApiKey(id) { return http.delete(`/admin/api/keys/${id}`) }

export default http
