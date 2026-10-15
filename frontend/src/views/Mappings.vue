<template>
  <el-card shadow="never">
    <div style="margin-bottom: 12px">
      <el-button type="primary" @click="openEdit()">{{ t('mappings.add') }}</el-button>
    </div>
    <el-table :data="items" size="small">
      <el-table-column prop="name" :label="t('mappings.model')" width="170" />
      <el-table-column :label="t('mappings.kb')" min-width="180">
        <template #default="{ row }">
          <template v-if="!row.kb || (Array.isArray(row.kb) && !row.kb.length)">
            <el-tag type="info" size="small">{{ t('mappings.passthrough') }}</el-tag>
          </template>
          <el-tag v-for="k in (Array.isArray(row.kb) ? row.kb : [row.kb])" :key="k" size="small" style="margin-right: 4px">{{ k }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="kb_merge" :label="t('mappings.merge')" width="120">
        <template #default="{ row }">{{ row.kb_merge || row['kb-merge'] || 'priority' }}</template>
      </el-table-column>
      <el-table-column prop="upstream" label="Upstream" width="130" />
      <el-table-column prop="inject_mode" :label="t('mappings.inject')" width="160">
        <template #default="{ row }">{{ row.inject_mode || row['inject-mode'] || 'system_prepend' }}</template>
      </el-table-column>
      <el-table-column label="Rerank" width="80">
        <template #default="{ row }">
          <el-tag :type="row.rerank ? 'success' : 'info'" size="small">{{ row.rerank ? t('common.yes') : t('common.no') }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('common.actions')" width="160" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="openEdit(row)">{{ t('common.edit') }}</el-button>
          <el-button size="small" type="danger" plain @click="remove(row)">{{ t('common.delete') }}</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dlg" :title="editing ? t('mappings.edit') : t('mappings.add')" width="620px">
      <el-form :model="form" label-width="120px">
        <el-form-item :label="t('mappings.model')" required>
          <el-input v-model="form.name" :disabled="!!editing" :placeholder="t('mappings.modelPlaceholder')" />
        </el-form-item>
        <el-form-item :label="t('mappings.kb')">
          <el-select
            v-model="form.kb"
            multiple
            filterable
            allow-create
            default-first-option
            clearable
            :placeholder="t('mappings.kbPlaceholder')"
            style="width: 100%"
          >
            <el-option v-for="k in kbNames" :key="k" :label="k" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('mappings.merge')">
          <el-select v-model="form.kb_merge" style="width: 100%">
            <el-option :label="t('mappings.mergePriority')" value="priority" />
            <el-option :label="t('mappings.mergeConcat')" value="concat" />
            <el-option :label="t('mappings.mergeRrf')" value="rrf" />
          </el-select>
        </el-form-item>
        <el-form-item label="Upstream" required>
          <el-select v-model="form.upstream" style="width: 260px">
            <el-option v-for="u in upstreams" :key="u.id" :label="`${u.id} (${u.type})`" :value="u.id" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('mappings.inject')">
          <el-select v-model="form.inject_mode" style="width: 100%">
            <el-option :label="t('mappings.injectPrepend')" value="system_prepend" />
            <el-option :label="t('mappings.injectAppend')" value="system_append" />
            <el-option :label="t('mappings.injectNew')" value="new_message" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('mappings.enableRerank')">
          <el-switch v-model="form.rerank" />
          <span v-if="form.rerank" style="margin-left: 10px; font-size: 12px; color: #e6a23c">
            <template v-if="!rerankers.length">{{ t('mappings.noReranker') }}</template>
            <template v-else>{{ t('mappings.usingReranker', { names: rerankers.map(r => r.id).join(', ') }) }}</template>
          </span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="saving" @click="save">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import http, { errMsg } from '../api'

const { t } = useI18n()

const items = ref([])
const kbNames = ref([])
const upstreams = ref([])
const rerankers = ref([])
const dlg = ref(false)
const editing = ref(null)
const saving = ref(false)
const form = reactive({ name: '', kb: [], kb_merge: 'priority', upstream: '', inject_mode: 'system_prepend', rerank: false })

async function load() {
  try {
    const { data } = await http.get('/admin/mappings')
    items.value = (data.items || []).map((m) => {
      if (m.kb && !Array.isArray(m.kb)) m.kb = [m.kb]
      return { ...m }
    })
  } catch (e) { ElMessage.error(errMsg(e)) }
  try {
    const { data } = await http.get('/admin/kbs')
    kbNames.value = (data.items || []).map((k) => k.name)
  } catch (e) { /* ignore */ }
  try {
    const { data } = await http.get('/admin/providers/llm')
    upstreams.value = data.items || []
  } catch (e) { /* ignore */ }
  try {
    const { data } = await http.get('/admin/providers/reranker')
    rerankers.value = data.items || []
  } catch (e) { /* ignore */ }
}

function openEdit(row) {
  editing.value = row || null
  Object.assign(form, {
    name: row ? row.name : '',
    kb: row ? [...(row.kb || [])] : [],
    kb_merge: row ? row.kb_merge || row['kb-merge'] || 'priority' : 'priority',
    upstream: row ? row.upstream : '',
    inject_mode: row ? row.inject_mode || row['inject-mode'] || 'system_prepend' : 'system_prepend',
    rerank: row ? !!row.rerank : false
  })
  dlg.value = true
}

async function save() {
  if (!form.name) { ElMessage.warning(t('mappings.nameRequired')); return }
  if (!form.upstream) { ElMessage.warning(t('mappings.upstreamRequired')); return }
  saving.value = true
  try {
    const item = { ...form }
    if (editing.value) {
      await http.put(`/admin/mappings/${editing.value.name}`, { item })
    } else {
      await http.post('/admin/mappings', { item })
    }
    ElMessage.success(t('mappings.saved'))
    dlg.value = false
    await load()
  } catch (e) { ElMessage.error(errMsg(e)) } finally { saving.value = false }
}

async function remove(row) {
  try { await ElMessageBox.confirm(t('mappings.confirmDelete', { name: row.name }), t('common.tip'), { type: 'warning' }) } catch { return }
  try {
    await http.delete(`/admin/mappings/${row.name}`)
    ElMessage.success(t('mappings.deleted'))
    await load()
  } catch (e) { ElMessage.error(errMsg(e)) }
}

onMounted(load)
</script>
