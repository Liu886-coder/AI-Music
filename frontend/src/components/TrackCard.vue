<script setup>
defineProps({ item: Object, feedbackState: { type: Object, default: () => ({}) } })
const emit = defineEmits(['feedback'])
</script>
<template>
  <article class="card" :class="{ 'feedback-sent': feedbackState.status === 'sent' }">
    <div class="cover">♫</div><div class="meta"><h3>{{ item.track_name }}</h3><p>{{ item.artist_name }} · {{ item.album_name || '未知专辑' }}</p><p class="reason">{{ item.reason }}</p><p v-if="item.unverified_conditions?.length" class="reason">未验证：{{ item.unverified_conditions.join('、') }}</p><div class="row"><span class="score">{{ item.score.toFixed(2) }}</span><a v-if="item.url" :href="item.url" target="_blank" rel="noopener">查看来源</a><button aria-label="喜欢" :aria-pressed="feedbackState.type === 'like'" :class="{ selected: feedbackState.type === 'like' }" :disabled="feedbackState.status === 'sending'" @click="emit('feedback', 'like')">❤️</button><button aria-label="不喜欢" :aria-pressed="feedbackState.type === 'dislike'" :class="{ selected: feedbackState.type === 'dislike' }" :disabled="feedbackState.status === 'sending'" @click="emit('feedback', 'dislike')">👎</button><span v-if="feedbackState.status === 'sending'" class="feedback-ok" role="status">保存中…</span><span v-if="feedbackState.status === 'sent'" role="status" class="feedback-ok">已记录</span></div></div>
  </article>
</template>
