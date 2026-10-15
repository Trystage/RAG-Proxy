<template>
  <div>
    <el-row :gutter="16">
      <!-- 知识库列表 -->
      <el-col :span="8">
        <el-card shadow="never" style="min-height: 560px">
          <template #header>
            <div class="card-header">
              <span>{{ t('kb.title') }}</span>
              <el-button size="small" type="primary" @click="createKb">{{ t('kb.create') }}</el-button>
            </div>
          </template>
          <el-menu :default-active="currentKb" @select="(k) => selectKb(k)">
            <el-menu-item v-for="kb in kbs" :key="kb.name" :index="kb.name">
              <div class="kb-item">
                <span>{{ kb.name }}</span>
                <el-button size="small" type="danger" text @click.stop="deleteKb(kb)">{{ t('common.delete') }}</el-button>
              </div>
              <div class="kb-meta">{{ t('kb.docsChunks', { docs: kb.documents ?? 0, chunks: kb.chunks ?? 0 }) }}
                <el-tag v-if="kb.embed_provider" size="small" type="primary" style="margin-left: 6px">{{ kb.embed_provider }}</el-tag>
                <el-tag v-else size="small" type="info" style="margin-left: 6px">{{ t('kb.defaultProvider') }}</el-tag>
              </div>
            </el-menu-item>
            <div v-if="!kbs.length" style="color:#909399; text-align:center; padding: 20px">{{ t('kb.empty') }}</div>
          </el-menu>
        </el-card>
      </el-col>

      <!-- 文件管理 -->
      <el-col :span="16">
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <span>{{ t('kb.current', { name: currentKb || t('kb.pick') }) }}</span>
              <div v-if="currentKb" class="card-header-actions">
                <el-select v-model="kbProviderSel" size="small" style="width: 170px" placeholder="Embedding Provider" @change="saveKbProvider">
                  <el-option :label="t('kb.globalDefault')" value="" />
                  <el-option v-for="p in embedProviders" :key="p" :label="p" :value="p" />
                </el-select>
                <el-button size="small" @click="testOpen">{{ t('kb.retrievalTest') }}</el-button>
                <el-button size="small" @click="reindex">{{ t('kb.reindex') }}</el-button>
              </div>
            </div>
          </template>

          <template v-if="currentKb">
            <el-upload
              drag
              multiple
              :auto-upload="false"
              :show-file-list="true"
              :limit="500"
              v-model:file-list="fileList"
              class="uploader"
            >
              <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
              <div class="el-upload__text">{{ t('kb.drop') }}</div>
              <div class="el-upload__tip">{{ t('kb.dropTip') }}</div>
            </el-upload>
            <div style="margin: 8px 0">
              <el-button size="small" type="primary" :loading="uploading" @click="startUpload">{{ t('kb.uploadIndex') }}</el-button>
              <el-button size="small" @click="pickFolder">{{ t('kb.pickFolder') }}</el-button>
              <input ref="folderInput" type="file" webkitdirectory multiple style="display:none" @change="onFolderPick" />
              <span v-if="uploading" style="margin-left: 12px; color:#e6a23c">{{ t('kb.processing', { n: taskTotal }) }}</span>
            </div>
            <el-progress v-if="taskTotal" :percentage="taskPercent" style="margin-bottom: 12px" />

            <el-table :data="files" size="small" max-height="420">
              <el-table-column prop="original_name" :label="t('kb.fileName')" min-width="200" show-overflow-tooltip />
              <el-table-column :label="t('kb.size')" width="90">
                <template #default="{ row }">{{ fmtSize(row.size) }}</template>
              </el-table-column>
              <el-table-column :label="t('kb.status')" width="100">
                <template #default="{ row }">
                  <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="chunks" :label="t('kb.chunks')" width="70" />
              <el-table-column :label="t('common.actions')" width="170" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" @click="previewChunks(row)">{{ t('kb.chunkPreview') }}</el-button>
                  <el-button size="small" type="danger" plain @click="deleteFile(row)">{{ t('common.delete') }}</el-button>
                </template>
              </el-table-column>
            </el-table>
          </template>
          <el-empty v-else :description="t('kb.emptyHint')" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 新建知识库 -->
    <el-dialog v-model="createDlg" :title="t('kb.createTitle')" width="440px">
      <el-input v-model="newKbName" :placeholder="t('kb.namePlaceholder')" />
      <div style="margin-top: 12px">
        <el-select v-model="newKbProvider" style="width: 100%" :placeholder="t('kb.providerPlaceholder')" clearable>
          <el-option v-for="p in embedProviders" :key="p" :label="p" :value="p" />
        </el-select>
      </div>
      <template #footer>
        <el-button @click="createDlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="doCreateKb">{{ t('common.create') }}</el-button>
      </template>
    </el-dialog>

    <!-- 分块预览 -->
    <el-dialog v-model="chunkDlg" :title="t('kb.chunkTitle', { name: chunkFile?.original_name || '' })" width="720px">
      <el-pagination
        small
        layout="prev, pager, next, total"
        :total="chunkTotal"
        :page-size="chunkSize"
        v-model:current-page="chunkPage"
        @current-change="loadChunks"
        style="margin-bottom: 10px"
      />
      <el-timeline>
        <el-timeline-item v-for="(c, i) in chunks" :key="c.id" :timestamp="`${t('kb.chunkN', { n: (chunkPage - 1) * chunkSize + i + 1 })} · ${(c.score !== undefined && c.score !== null) ? 'score=' + c.score : ''}`">
          <pre class="chunk-text">{{ c.text }}</pre>
        </el-timeline-item>
      </el-timeline>
      <div v-if="!chunks.length" style="color:#909399">{{ t('kb.noChunks') }}</div>
    </el-dialog>

    <!-- 检索测试 -->
    <el-dialog v-model="testDlg" :title="t('kb.testTitle')" width="640px">
      <el-input v-model="testQuery" :placeholder="t('kb.testPlaceholder')" @keyup.enter="runTest" />
      <div style="margin: 10px 0">
        <el-input-number v-model="testTopK" :min="1" :max="20" size="small" /> top_k
      </div>
      <el-button type="primary" size="small" :loading="testing" @click="runTest">{{ t('kb.retrieve') }}</el-button>
      <el-table v-if="testResults.length" :data="testResults" size="small" style="margin-top: 12px" max-height="360">
        <el-table-column prop="score" :label="t('kb.score')" width="90" />
        <el-table-column prop="file_name" :label="t('kb.source')" width="160" show-overflow-tooltip />
        <el-table-column prop="text" :label="t('kb.content')" min-width="280" show-overflow-tooltip />
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import http, { errMsg } from '../api'

const { t } = useI18n()

const kbs = ref([])
const currentKb = ref('')
const files = ref([])
const fileList = ref([])
const folderInput = ref(null)
const uploading = ref(false)
const taskId = ref('')
const taskTotal = ref(0)
const taskDone = ref(0)
const taskFailed = ref(0)
let pollTimer = null

const createDlg = ref(false)
const newKbName = ref('')
const newKbProvider = ref('')
const embedProviders = ref([])
const kbProviderSel = ref('')
const chunkDlg = ref(false)
const chunkFile = ref(null)
const chunks = ref([])
const chunkPage = ref(1)
const chunkSize = 10
const chunkTotal = ref(0)
const testDlg = ref(false)
const testQuery = ref('')
const testTopK = ref(5)
const testResults = ref([])
const testing = ref(false)

const taskPercent = computed(() => (taskTotal.value ? Math.round((taskDone.value / taskTotal.value) * 100) : 0))

function fmtSize(n) {
  if (!n) return '-'
  if (n < 1024) return n + ' B'
  if (n < 1048576) return (n / 1024).toFixed(1) + ' KB'
  return (n / 1048576).toFixed(1) + ' MB'
}
function statusType(s) {
  return { indexed: 'success', failed: 'danger', indexing: 'warning', uploaded: 'info' }[s] || 'info'
}
function statusText(s) {
  return {
    indexed: t('kb.statusIndexed'),
    failed: t('kb.statusFailed'),
    indexing: t('kb.statusIndexing'),
    uploaded: t('kb.statusUploaded')
  }[s] || s
}

async function loadKbs() {
  try {
    const { data } = await http.get('/admin/kbs')
    kbs.value = data.items || []
    if (currentKb.value && !kbs.value.some((k) => k.name === currentKb.value)) {
      currentKb.value = ''
      files.value = []
    }
    if (!currentKb.value && kbs.value.length) selectKb(kbs.value[0].name)
  } catch (e) { ElMessage.error(errMsg(e)) }
}

async function loadEmbedProviders() {
  try {
    const { data } = await http.get('/admin/providers/embedding')
    embedProviders.value = (data.items || []).map((p) => p.id)
  } catch (e) { /* 下拉留空即可 */ }
}

async function selectKb(name) {
  currentKb.value = name
  files.value = []
  const kb = kbs.value.find((k) => k.name === name)
  kbProviderSel.value = (kb && kb.embed_provider) || ''
  try {
    const { data } = await http.get(`/admin/kbs/${name}/files`)
    files.value = data.items || []
  } catch (e) { ElMessage.error(errMsg(e)) }
}

function createKb() { createDlg.value = true; newKbName.value = ''; newKbProvider.value = '' }
async function doCreateKb() {
  if (!newKbName.value) return
  try {
    const payload = { name: newKbName.value }
    if (newKbProvider.value) payload.embed_provider = newKbProvider.value
    await http.post('/admin/kbs', payload)
    ElMessage.success(t('kb.created'))
    createDlg.value = false
    await loadKbs()
  } catch (e) { ElMessage.error(errMsg(e)) }
}

async function saveKbProvider(val) {
  if (!currentKb.value) return
  try {
    const { data } = await http.put(`/admin/kbs/${currentKb.value}/provider`, { embed_provider: val || null })
    if (data.need_reindex) {
      ElMessageBox.confirm(
        t('kb.dimChange', { oldDim: data.old_dim, newDim: data.new_dim }),
        t('kb.dimTitle'),
        { type: 'warning', confirmButtonText: t('kb.reindexNow'), cancelButtonText: t('kb.reindexLater') }
      ).then(() => reindex()).catch(() => {})
    } else {
      ElMessage.success(data.message || t('kb.updated'))
    }
    await loadKbs()
  } catch (e) {
    // 失败时回滚下拉到原值
    const kb = kbs.value.find((k) => k.name === currentKb.value)
    kbProviderSel.value = (kb && kb.embed_provider) || ''
    ElMessage.error(errMsg(e))
  }
}

async function deleteKb(kb) {
  try { await ElMessageBox.confirm(t('kb.confirmDeleteKb', { name: kb.name }), t('common.danger'), { type: 'warning' }) } catch { return }
  try {
    await http.delete(`/admin/kbs/${kb.name}`)
    ElMessage.success(t('kb.deleted'))
    if (currentKb.value === kb.name) { currentKb.value = ''; files.value = [] }
    await loadKbs()
  } catch (e) { ElMessage.error(errMsg(e)) }
}

function pickFolder() { folderInput.value && folderInput.value.click() }
function onFolderPick(e) {
  const picked = Array.from(e.target.files || [])
  e.target.value = ''
  for (const f of picked) {
    if (!fileList.value.some((x) => x.name === f.name && x.size === f.size)) {
      fileList.value.push({ name: f.name, raw: f, size: f.size })
    }
  }
  if (picked.length) ElMessage.success(t('kb.folderAdded', { n: picked.length }))
}

async function startUpload() {
  if (!fileList.value.length) { ElMessage.warning(t('kb.pickFiles')); return }
  const form = new FormData()
  for (const f of fileList.value) {
    form.append('files', f.raw || f)
  }
  uploading.value = true
  taskTotal.value = fileList.value.length
  taskDone.value = 0
  try {
    const { data } = await http.post(`/admin/kbs/${currentKb.value}/upload`, form, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })
    if (!data.ok) {
      ElMessage.warning(data.message || t('kb.partialReject'))
      if (data.rejected && data.rejected.length) {
        console.warn('rejected:', data.rejected)
      }
    }
    taskId.value = data.task_id
    pollTimer = setInterval(pollTask, 800)
  } catch (e) {
    uploading.value = false
    ElMessage.error(errMsg(e))
  }
}

async function pollTask() {
  if (!taskId.value) return
  try {
    const { data } = await http.get(`/admin/kbs/${currentKb.value}/tasks/${taskId.value}`)
    taskTotal.value = data.total || 0
    taskDone.value = data.done || 0
    taskFailed.value = data.failed || 0
    if (['done', 'partial', 'failed'].includes(data.status)) {
      clearInterval(pollTimer)
      pollTimer = null
      uploading.value = false
      fileList.value = []
      if (data.status === 'done') ElMessage.success(t('kb.uploadDone'))
      else if (data.status === 'partial') ElMessage.warning(t('kb.partialFail'))
      else ElMessage.error(t('kb.taskFail', { error: data.error || '' }))
      taskId.value = ''
      await loadKbs()
      await selectKb(currentKb.value)
    }
  } catch (e) {
    clearInterval(pollTimer)
    pollTimer = null
    uploading.value = false
    ElMessage.error(errMsg(e))
  }
}

async function reindex() {
  try { await ElMessageBox.confirm(t('kb.confirmReindex', { name: currentKb.value }), t('common.tip'), { type: 'warning' }) } catch { return }
  try {
    const { data } = await http.post(`/admin/kbs/${currentKb.value}/reindex`)
    ElMessage.success(t('kb.reindexStarted'))
    taskId.value = data.task_id
    taskTotal.value = 0
    uploading.value = true
    pollTimer = setInterval(pollTask, 800)
  } catch (e) { ElMessage.error(errMsg(e)) }
}

async function deleteFile(row) {
  try { await ElMessageBox.confirm(t('kb.confirmDeleteFile', { name: row.original_name }), t('common.tip'), { type: 'warning' }) } catch { return }
  try {
    await http.delete(`/admin/kbs/${currentKb.value}/files/${row.file_id}`)
    ElMessage.success(t('kb.deleted'))
    await selectKb(currentKb.value)
  } catch (e) { ElMessage.error(errMsg(e)) }
}

async function previewChunks(row) {
  chunkFile.value = row
  chunkPage.value = 1
  chunkDlg.value = true
  await loadChunks()
}
async function loadChunks() {
  try {
    const { data } = await http.get(`/admin/kbs/${currentKb.value}/chunks`, {
      params: { file_id: chunkFile.value.file_id, page: chunkPage.value, size: chunkSize }
    })
    chunks.value = data.chunks || []
    chunkTotal.value = data.total || 0
  } catch (e) { ElMessage.error(errMsg(e)) }
}

function testOpen() { testDlg.value = true; testQuery.value = ''; testResults.value = [] }
async function runTest() {
  if (!testQuery.value) return
  testing.value = true
  try {
    const { data } = await http.post(`/admin/kbs/${currentKb.value}/test`, { query: testQuery.value, top_k: testTopK.value })
    testResults.value = data.items || []
  } catch (e) { ElMessage.error(errMsg(e)) } finally { testing.value = false }
}

onMounted(() => { loadKbs(); loadEmbedProviders() })
onBeforeUnmount(() => pollTimer && clearInterval(pollTimer))
</script>

<style scoped>
.card-header { display: flex; justify-content: space-between; align-items: center; }
.card-header-actions { display: flex; align-items: center; gap: 8px; }
.kb-item { display: flex; justify-content: space-between; align-items: center; width: 100%; }
.kb-meta { font-size: 12px; color: #909399; }
.uploader { margin-top: 4px; }
.chunk-text { white-space: pre-wrap; word-break: break-word; background: #f7f8fa; padding: 10px; border-radius: 6px; margin: 0; font-family: inherit; }
</style>
