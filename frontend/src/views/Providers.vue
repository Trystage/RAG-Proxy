<template>
  <el-card shadow="never">
    <el-tabs v-model="tab">
      <!-- LLM / Embedding / ReRanker 通用列表 -->
      <el-tab-pane v-for="t in listTabs" :key="t.key" :label="t.label" :name="t.key">
        <div style="margin-bottom: 12px">
          <el-button type="primary" @click="openEdit(t.key)">{{ tr('providers.add', { label: t.label }) }}</el-button>
        </div>
        <el-table :data="providers[t.key] || []" size="small">
          <el-table-column prop="id" :label="tr('common.id')" width="140" />
          <el-table-column prop="type" :label="tr('common.type')" width="110" />
          <el-table-column prop="base_url" :label="tr('providers.baseUrl')" min-width="220" show-overflow-tooltip />
          <el-table-column prop="api_key" :label="tr('providers.apiKey')" width="170">
            <template #default="{ row }">
              <el-tag v-if="row.api_key_invalid" type="danger" size="small">{{ tr('providers.keyInvalid') }}</el-tag>
              <el-tag v-else-if="row.api_key" size="small">{{ mask(row.api_key) }}</el-tag>
              <el-tag v-else type="info" size="small">{{ tr('providers.keyUnset') }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="timeout" :label="tr('providers.timeoutSec')" width="100" />
          <el-table-column v-if="t.key === 'llm'" prop="model" :label="tr('providers.modelName')" width="150">
            <template #default="{ row }">
              <span v-if="row.model">{{ row.model }}</span>
              <el-tag v-else size="small" type="info">{{ tr('providers.useRequestName') }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column v-if="t.key === 'embedding'" prop="model" :label="tr('common.model')" width="160" />
          <el-table-column v-if="t.key === 'embedding'" prop="dimensions" :label="tr('providers.dimensions')" width="100" />
          <el-table-column v-if="t.key === 'reranker'" prop="model" :label="tr('common.model')" width="160" />
          <el-table-column :label="tr('common.actions')" width="220" fixed="right">
            <template #default="{ row }">
              <el-button size="small" @click="openEdit(t.key, row)">{{ tr('common.edit') }}</el-button>
              <el-button size="small" type="primary" plain :loading="testing[t.key + row.id]" @click="testConn(t.key, row)">{{ tr('providers.test') }}</el-button>
              <el-button size="small" type="danger" plain @click="remove(t.key, row)">{{ tr('common.delete') }}</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- VectorStore -->
      <el-tab-pane label="VectorStore" name="vectorstore">
        <el-form :model="vsForm" label-width="130px" style="max-width: 640px">
          <el-form-item :label="tr('providers.vsType')">
            <el-select v-model="vsForm.type">
              <el-option :label="tr('providers.vsSimple')" value="simple" />
              <el-option :label="tr('providers.vsChroma')" value="chroma_http" />
            </el-select>
          </el-form-item>
          <el-alert :title="tr('providers.vsHint')" type="info" :closable="false" show-icon style="margin-bottom: 16px" />
          <template v-if="vsForm.type === 'simple'">
            <el-form-item :label="tr('providers.cacheLimit')">
              <el-input-number v-model="vsForm.simple.cache_limit" :min="1000" :step="10000" />
            </el-form-item>
          </template>
          <template v-else>
            <el-form-item :label="tr('providers.baseUrl')">
              <el-input v-model="vsForm.chroma_http.base_url" placeholder="http://127.0.0.1:8000" />
            </el-form-item>
            <el-form-item :label="tr('providers.tenant')">
              <el-input v-model="vsForm.chroma_http.tenant" />
            </el-form-item>
            <el-form-item :label="tr('providers.database')">
              <el-input v-model="vsForm.chroma_http.database" />
            </el-form-item>
            <el-form-item :label="tr('providers.collectionPrefix')">
              <el-input v-model="vsForm.chroma_http.collection_prefix" />
            </el-form-item>
          </template>
          <el-form-item>
            <el-button type="primary" @click="saveVs">{{ tr('common.save') }}</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>
    </el-tabs>

    <!-- 编辑对话框 -->
    <el-dialog v-model="dialog.show" :title="dialog.title" width="560px">
      <el-form :model="dialog.item" label-width="90px">
        <el-form-item :label="tr('common.id')" required>
          <el-input v-model="dialog.item.id" :disabled="!!dialog.originId" :placeholder="tr('providers.idPlaceholder')" />
        </el-form-item>
        <el-form-item :label="tr('common.type')" required>
          <el-select v-model="dialog.item.type" style="width: 100%">
            <el-option v-for="opt in typeOptions(dialog.kind)" :key="opt" :label="opt" :value="opt" />
          </el-select>
        </el-form-item>
        <el-form-item :label="tr('providers.baseUrl')" required>
          <el-input v-model="dialog.item.base_url" placeholder="http://127.0.0.1:8080/v1" />
        </el-form-item>
        <el-form-item :label="tr('providers.apiKey')">
          <el-input v-model="dialog.item.api_key" type="password" show-password placeholder="sk-xxx" />
        </el-form-item>
        <el-form-item :label="tr('common.timeout')">
          <el-input-number v-model="dialog.item.timeout" :min="5" :max="600" />
        </el-form-item>
        <template v-if="dialog.kind === 'llm'">
          <el-form-item :label="tr('providers.modelName')">
            <el-input v-model="dialog.item.model" :placeholder="tr('providers.modelPlaceholder')" />
          </el-form-item>
        </template>
        <template v-if="dialog.kind === 'embedding'">
          <el-form-item :label="tr('common.model')" required>
            <el-input v-model="dialog.item.model" :placeholder="tr('providers.embedModelPlaceholder')" />
          </el-form-item>
          <el-form-item :label="tr('providers.dimensions')">
            <el-input-number v-model="dialog.item.dimensions" :min="0" :max="8192" />
          </el-form-item>
        </template>
        <template v-if="dialog.kind === 'reranker'">
          <el-form-item :label="tr('common.model')" required>
            <el-input v-model="dialog.item.model" :placeholder="tr('providers.rerankModelPlaceholder')" />
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="dialog.show = false">{{ tr('common.cancel') }}</el-button>
        <el-button type="primary" :loading="saving" @click="save">{{ tr('common.save') }}</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import http, { errMsg } from '../api'

const { t: tr } = useI18n()

const tab = ref('llm')
const listTabs = [
  { key: 'llm', label: 'LLM (Upstream)' },
  { key: 'embedding', label: 'Embedding' },
  { key: 'reranker', label: 'ReRanker' }
]
const providers = reactive({ llm: [], embedding: [], reranker: [] })
const testing = reactive({})
const saving = ref(false)

const dialog = reactive({ show: false, kind: 'llm', title: '', item: {}, originId: '' })
const vsForm = reactive({ type: 'simple', simple: { cache_limit: 200000 }, chroma_http: {} })

function mask(v) {
  if (!v) return ''
  if (v === '****' || v.includes('****')) return v
  return v.length > 8 ? v.slice(0, 3) + '****' + v.slice(-3) : v.slice(0, 2) + '***'
}
function typeOptions(kind) {
  if (kind === 'llm') return ['openai', 'anthropic']
  if (kind === 'embedding') return ['openai']
  return ['openai', 'cohere', 'jina']
}

async function load() {
  for (const t of listTabs) {
    try {
      const { data } = await http.get(`/admin/providers/${t.key}`)
      providers[t.key] = data.items || []
    } catch (e) { ElMessage.error(errMsg(e)) }
  }
  try {
    const { data } = await http.get('/admin/providers/vectorstore')
    Object.assign(vsForm, data.item || {})
    vsForm.simple = vsForm.simple || { cache_limit: 200000 }
    vsForm.chroma_http = vsForm.chroma_http || {}
  } catch (e) { /* ignore */ }
}

function openEdit(kind, row) {
  dialog.kind = kind
  dialog.originId = row ? row.id : ''
  dialog.title = row ? tr('providers.editTitle', { kind }) : tr('providers.addTitle', { kind })
  dialog.item = row
    ? { ...row }
    : { id: '', type: kind === 'llm' ? 'openai' : 'openai', base_url: '', api_key: '', timeout: 60, model: '', dimensions: 0 }
  dialog.show = true
  if (row && row.api_key_invalid) {
    ElMessage.warning(tr('providers.keyBroken'))
  }
}

async function save() {
  saving.value = true
  try {
    const item = { ...dialog.item }
    if (!item.id) { ElMessage.warning(tr('providers.idRequired')); return }
    if (dialog.originId) {
      await http.put(`/admin/providers/${dialog.kind}/${dialog.originId}`, { item })
    } else {
      await http.post(`/admin/providers/${dialog.kind}`, { item })
    }
    ElMessage.success(tr('providers.saved'))
    dialog.show = false
    await load()
  } catch (e) { ElMessage.error(errMsg(e)) } finally { saving.value = false }
}

async function testConn(kind, row) {
  testing[kind + row.id] = true
  try {
    const { data } = await http.post(`/admin/providers/${kind}/${row.id}/test`)
    if (data.ok) ElMessage.success(data.message)
    else ElMessage.error(data.message)
  } catch (e) { ElMessage.error(errMsg(e)) } finally { testing[kind + row.id] = false }
}

async function remove(kind, row) {
  try {
    await ElMessageBox.confirm(tr('providers.confirmDelete', { id: row.id }), tr('common.tip'), { type: 'warning' })
  } catch { return }
  try {
    await http.delete(`/admin/providers/${kind}/${row.id}`)
    ElMessage.success(tr('providers.deleted'))
    await load()
  } catch (e) { ElMessage.error(errMsg(e)) }
}

async function saveVs() {
  try {
    await http.put('/admin/providers/vectorstore', { item: vsForm })
    ElMessage.success(tr('providers.saved'))
  } catch (e) { ElMessage.error(errMsg(e)) }
}

onMounted(load)
</script>
