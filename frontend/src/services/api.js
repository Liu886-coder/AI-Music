import axios from 'axios'
const api = axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000/api', timeout: 8000 })
export const recommend = (data) => api.post('/recommend', data, { timeout: 50000 })
export const feedback = (data) => api.post('/feedback', data)
export const getPreferences = (id) => api.get(`/preferences/${id}`)
export const getHistory = (id) => api.get(`/history/${id}`)
export const getHealth = () => api.get('/health')
export const getFeedback = (id) => api.get(`/feedback/${encodeURIComponent(id)}`)
