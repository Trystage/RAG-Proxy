<template>
  <el-card shadow="never">
    <template #header>
      <div class="card-header">
        <span>{{ t('settings.title') }}</span>
        <div>
          <el-button @click="loadRaw">{{ t('common.reload') }}</el-button>
          <el-button type="primary" :loading="saving" @click="save">{{ t('settings.save') }}</el-button>
        </div>
      </div>
    </template>
    <el-alert
      type="info"
      :closable="false"
      show-icon
      :title="t('settings.hint')"
      style="margin-bottom: 12px"
    />
    <el-input
      v-model="content"
      type="textarea"
      :rows="24"
      spellcheck="false"
      style="font-family: 'JetBrains Mono', Consolas, monospace; font-size: 13px"
    />
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import http, { errMsg } from '../api'

const { t } = useI18n()

const content = ref('')
const saving = ref(false)

async function loadRaw() {
  try {
    const { data } = await http.get('/admin/settings/raw')
    content.value = data.content || ''
  } catch (e) { ElMessage.error(errMsg(e)) }
}

async function save() {
  saving.value = true
  try {
    const { data } = await http.put('/admin/settings', { content: content.value })
    ElMessage.success(data.message || t('settings.saved'))
  } catch (e) { ElMessage.error(errMsg(e)) } finally { saving.value = false }
}

onMounted(loadRaw)
</script>

<style scoped>
.card-header { display: flex; justify-content: space-between; align-items: center; }
</style>
