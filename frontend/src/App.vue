<script setup>
import { computed, ref, watch } from 'vue'
import TrackCard from './components/TrackCard.vue'
import { recommend, feedback, getPreferences, getHistory, getHealth, getFeedback } from './services/api'

const userId = ref(localStorage.getItem('music-user-id') || 'demo_user')
const message = ref('推荐适合健身的音乐，希望节奏感强，能够让我保持动力。')
const limit = ref(20)
const items = ref([]), history = ref([]), savedPreferences = ref(null), intent = ref(null)
const loading = ref(false), warning = ref(''), mockMode = ref(null), feedbackState = ref({})
const healthStatus = ref('connecting')
const modeLabel = computed(() => healthStatus.value === 'unavailable' ? '后端未连接' : healthStatus.value === 'configuration_error' ? 'Last.fm 配置缺失' : mockMode.value === null ? '正在连接后端' : mockMode.value ? 'Mock 模式' : 'Last.fm 实时模式')
const filters = ref({ scene: 'any', mood: 'any', genres: [], language: 'any', energy: 'any', familiarity: 'mixed' })
const options = {
  scene: [['any', '不限'], ['workout', '健身运动'], ['study', '学习专注'], ['work', '工作办公'], ['commute', '通勤出行'], ['relax', '睡前放松'], ['party', '聚会派对'], ['leisure', '休闲娱乐']],
  mood: [['any', '不限'], ['energetic', '充满活力'], ['happy', '开心愉悦'], ['calm', '平静放松'], ['focused', '专注沉浸'], ['romantic', '浪漫温柔'], ['sad', '忧伤治愈'], ['inspiring', '热血激昂']],
  genre: [['pop', '流行'], ['rock', '摇滚'], ['electronic', '电子'], ['hip-hop', '嘻哈 / Rap'], ['jazz', '爵士'], ['classical', '古典'], ['easy listening', '轻音乐'], ['rnb', 'R&B']],
  language: [['any', '不限'], ['chinese', '华语'], ['english', '英语'], ['japanese', '日语'], ['korean', '韩语'], ['instrumental', '纯音乐']],
  energy: [['any', '不限'], ['low', '低能量、舒缓'], ['medium', '中等能量'], ['high', '高能量']],
  familiarity: [['any', '不限'], ['familiar', '经典熟悉歌曲'], ['discover', '发现新歌'], ['mixed', '新歌和熟悉的歌都可以']],
}
const currentConditions = computed(() => !intent.value ? [] : [['场景', intent.value.scene], ['氛围', intent.value.mood], ['风格', intent.value.genres?.join('、') || 'any'], ['语言', intent.value.language], ['能量', intent.value.energy], ['熟悉度', intent.value.familiarity]].filter(([, value]) => value && value !== 'any'))
const learned = computed(() => savedPreferences.value?.learned)
const manualPreferences = computed(() => {
  if (!savedPreferences.value) return null
  const { learned, ...manual } = savedPreferences.value
  return manual
})
let sidebarVersion = 0
let requestVersion = 0
let lastRequest = null
async function refreshSidebar(id = userId.value) {
  const version = ++sidebarVersion
  const [past, preferences, states] = await Promise.all([getHistory(id), getPreferences(id), getFeedback(id)])
  if (id !== userId.value || version !== sidebarVersion) return
  history.value = past.data; savedPreferences.value = preferences.data
  feedbackState.value = Object.fromEntries(states.data.map((state) => [state.track_id, { type: state.feedback_type, status: 'sent' }]))
}
watch(userId, (id) => {
  ++requestVersion; ++sidebarVersion
  items.value = []; history.value = []; savedPreferences.value = null; feedbackState.value = {}; intent.value = null
  loading.value = false; lastRequest = null; warning.value = ''
  if (!/^[\p{L}\p{N}_-]{1,64}$/u.test(id)) return
  localStorage.setItem('music-user-id', id)
  refreshSidebar(id).catch(() => { if (id === userId.value) warning.value = '暂时无法加载历史偏好，请确认后端已启动。' })
}, { immediate: true })
async function loadHealth() {
  try {
    const { data } = await getHealth()
    mockMode.value = data.mock_mode
    healthStatus.value = data.status
  } catch { mockMode.value = null; healthStatus.value = 'unavailable' }
}
loadHealth()
function toggleGenre(genre) { filters.value.genres = filters.value.genres.includes(genre) ? filters.value.genres.filter((item) => item !== genre) : [...filters.value.genres, genre] }
function clearGenres() { filters.value.genres = [] }
async function run(refresh = false) {
  if (loading.value) return
  if (!message.value.trim()) { warning.value = '请先描述你的音乐需求。'; return }
  loading.value = true; warning.value = ''
  const version = ++requestVersion
  const id = userId.value
  const request = refresh && lastRequest ? structuredClone(lastRequest) : { user_id: id, request_text: message.value, preferences: JSON.parse(JSON.stringify(filters.value)), limit: limit.value }
  try {
    const response = await recommend({ ...request, refresh, previous_track_ids: refresh ? items.value.map((item) => item.track_id) : [] })
    if (version !== requestVersion || id !== userId.value) return
    lastRequest = request
    items.value = response.data.tracks; intent.value = response.data.intent; mockMode.value = response.data.music_mode === 'mock'; healthStatus.value = 'ok'; warning.value = response.data.warnings.join(' ')
    loading.value = false
    feedbackState.value = Object.fromEntries(items.value.filter((item) => item.feedback_type).map((item) => [item.track_id, { type: item.feedback_type, status: 'sent' }]))
    try { await refreshSidebar(id) } catch { warning.value += ' 偏好区域暂时无法刷新，推荐结果已保留。' }
  } catch (error) {
    if (version === requestVersion) {
      warning.value = errorText(error, '后端未启动或音乐服务暂时不可用。')
      void loadHealth()
    }
  } finally { if (version === requestVersion) loading.value = false }
}
function errorText(error, fallback) {
  if (error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT') return '请求超时，请检查后端及音乐服务的连接后重试。'
  const detail = error.response?.data?.detail
  return typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((item) => item.msg).join('；') : fallback
}
async function sendFeedback(item, type) {
  const key = item.track_id
  const id = userId.value
  if (feedbackState.value[key]?.status === 'sending') return
  const previous = feedbackState.value[key]
  feedbackState.value[key] = { type, status: 'sending' }
  try {
    await feedback({ user_id: id, track_id: key, feedback_type: type })
    if (id !== userId.value) return
    feedbackState.value[key] = { type, status: 'sent' }
    try { await refreshSidebar(id) } catch { warning.value = '反馈已保存，偏好区域暂时无法刷新。' }
  } catch (error) { if (id === userId.value) { feedbackState.value[key] = previous || {}; warning.value = errorText(error, '反馈提交失败，请稍后重试。') } }
}
</script>

<template>
  <main>
    <header><div><span class="eyebrow">AI MUSIC</span><h1>智能音乐推荐 Agent</h1><p>描述你的音乐需求，或通过下方选项快速指定偏好。</p></div><span class="status" :class="{ live: mockMode === false && healthStatus === 'ok' }">● {{ modeLabel }}</span></header>
    <section class="composer" aria-label="音乐推荐条件">
      <label for="request">你的音乐需求</label><textarea id="request" v-model="message" rows="3" @keyup.ctrl.enter="run(false)" />
      <div class="filters">
        <label>使用场景<select v-model="filters.scene"><option v-for="[value, text] in options.scene" :key="value" :value="value">{{ text }}</option></select></label>
        <label>情绪氛围<select v-model="filters.mood"><option v-for="[value, text] in options.mood" :key="value" :value="value">{{ text }}</option></select></label>
        <fieldset class="genre-picker"><legend>音乐风格</legend><div class="genre-options"><label class="chip"><input type="checkbox" :checked="!filters.genres.length" @change="clearGenres"><span>不限</span></label><label v-for="[value, text] in options.genre" :key="value" class="chip"><input type="checkbox" :checked="filters.genres.includes(value)" @change="toggleGenre(value)"><span>{{ text }}</span></label></div></fieldset>
        <label>语言<select v-model="filters.language"><option v-for="[value, text] in options.language" :key="value" :value="value">{{ text }}</option></select></label>
        <label>能量 / 节奏偏好<select v-model="filters.energy"><option v-for="[value, text] in options.energy" :key="value" :value="value">{{ text }}</option></select></label>
        <label>歌曲熟悉度<select v-model="filters.familiarity"><option v-for="[value, text] in options.familiarity" :key="value" :value="value">{{ text }}</option></select></label>
      </div>
      <div class="actions"><input v-model="userId" aria-label="用户 ID" placeholder="用户 ID" maxlength="64"><button class="primary" :disabled="loading" @click="run(false)">{{ loading ? '推荐中…' : '开始推荐' }}</button><button :disabled="loading || !items.length" @click="run(true)">换一批</button></div>
    </section>
    <p v-if="warning" class="warning">{{ warning }}</p>
    <section class="layout">
      <div class="results"><div v-if="currentConditions.length" class="intent-bar"><span>本次条件</span><b v-for="[key, value] in currentConditions" :key="key">{{ key }}：{{ value }}</b></div><div class="section-title"><h2>为你推荐</h2><span>{{ items.length }} 首</span></div><TrackCard v-for="item in items" :key="item.track_id" :item="item" :feedback-state="feedbackState[item.track_id]" @feedback="sendFeedback(item, $event)" /><p v-if="!items.length && !loading" class="empty">填写条件后开始探索你的下一首歌。</p></div>
      <aside>
        <h2>正在了解你的喜好</h2>
        <p class="aside-hint">依据当前用户的最新歌曲反馈学习，本次筛选不会自动变成长期偏好。</p>
        <p v-if="!learned?.feedback_count" class="aside-hint">还没有反馈。点击歌曲的喜欢或不喜欢后，这里会显示学到的标签与艺人。</p>
        <template v-else>
          <p class="learning-summary">{{ learned.liked_count }} 首喜欢 / 收藏 · {{ learned.disliked_count }} 首不喜欢</p>
          <p v-if="learned.missing_metadata_count" class="aside-hint">{{ learned.missing_metadata_count }} 首缺少标签，仅使用已有艺人信息。</p>
          <div v-for="group in [{ key: 'tags', title: '音乐标签' }, { key: 'artists', title: '艺人倾向' }]" :key="group.key" class="learned-group">
            <h3>{{ group.title }}</h3>
            <details v-for="signal in learned[group.key].slice(0, 8)" :key="signal.name" class="learned-signal">
              <summary><span>{{ signal.name }}</span><b :class="{ negative: signal.score < 0 }">{{ signal.score > 0 ? '偏好 +' + signal.score : signal.score < 0 ? '减少 ' + signal.score : '待观察' }}</b></summary>
              <p>{{ signal.positive_count }} 首正向、{{ signal.negative_count }} 首负向反馈；净支持按不同歌曲计算。</p>
              <ul><li v-for="e in signal.evidence" :key="e.track_id">{{ e.feedback_type === 'dislike' ? '不喜欢' : '喜欢 / 收藏' }} · {{ e.track_name }} — {{ e.artist_name }}<small v-if="e.source === 'mock'">（Mock 数据）</small></li></ul>
            </details>
          </div>
        </template>
        <details class="manual-preferences"><summary>手动保存的偏好</summary><pre>{{ manualPreferences ? JSON.stringify(manualPreferences, null, 2) : '暂无数据' }}</pre></details>
        <h2>推荐历史</h2><div v-for="entry in history.slice(0, 6)" :key="entry.id || entry.created_at" class="history"><span>{{ entry.track_name || entry.track_id }}<small v-if="entry.artist_name"> · {{ entry.artist_name }}</small></span><small>{{ entry.score }}</small></div>
      </aside>
    </section>
  </main>
</template>
