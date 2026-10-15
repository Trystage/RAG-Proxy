<template>
  <el-card shadow="never">
    <template #header>
      <div class="page-header">
        <span>{{ t('apiKeys.title') }}</span>
        <div class="header-actions">
          <el-switch v-model="enabled" :active-text="t('apiKeys.enabled')" @change="toggleAuth" />
          <el-button type="primary" @click="openCreate">{{ t('apiKeys.add') }}</el-button>
        </div>
      </div>
    </template>

    <el-table v-if="items.length" :data="items" size="small">
      <el-table-column prop="name" :label="t('apiKeys.name')" min-width="150">
        <template #default="{ row }">{{ row.name || '-' }}</template>
      </el-table-column>
      <el-table-column prop="preview" :label="t('apiKeys.preview')" min-width="170">
        <template #default="{ row }"><code>{{ row.preview }}</code></template>
      </el-table-column>
      <el-table-column :label="t('apiKeys.models')" min-width="180">
        <template #default="{ row }">{{ row.allowed_models?.length ? row.allowed_models.join(', ') : t('common.none') }}</template>
      </el-table-column>
      <el-table-column :label="t('apiKeys.expires')" width="180">
        <template #default="{ row }">{{ row.expires_at || t('apiKeys.never') }}</template>
      </el-table-column>
      <el-table-column :label="t('apiKeys.created')" width="180">
        <template #default="{ row }">{{ row.created_at || '-' }}</template>
      </el-table-column>
      <el-table-column :label="t('common.actions')" width="190" fixed="right">
        <template #default="{ row }">
          <el-switch v-model="row.enabled" :active-text="t('apiKeys.enabledTag')" :inactive-text="t('apiKeys.disabledTag')" @change="toggleKey(row)" />
          <el-button text type="primary" @click="openEdit(row)">{{ t('common.edit') }}</el-button>
          <el-button text type="danger" @click="remove(row)">{{ t('common.delete') }}</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-else :description="t('apiKeys.empty')" />

    <el-dialog v-model="editDialog" :title="editing ? t('apiKeys.edit') : t('apiKeys.add')" width="560px">
      <el-form :model="form" label-width="130px">
        <el-form-item :label="t('apiKeys.name')">
          <el-input v-model="form.name" :placeholder="t('apiKeys.namePlaceholder')" />
        </el-form-item>
        <el-form-item :label="t('apiKeys.expires')">
          <el-date-picker v-model="form.expires_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ssZ" clearable style="width: 100%" />
        </el-form-item>
        <el-form-item :label="t('apiKeys.models')">
          <el-select v-model="form.models" multiple clearable :placeholder="t('apiKeys.modelsSelectPlaceholder')" style="width: 100%">
            <el-option value="*" :label="t('apiKeys.allModels')" />
            <el-option v-for="model in modelOptions" :key="model" :value="model" :label="model" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialog = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="saving" @click="save">{{ t('apiKeys.save') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="createdDialog" :title="t('apiKeys.createdMessage')" width="620px">
      <p>{{ t('apiKeys.fullKeyHint') }}</p>
      <el-input v-model="createdKey" readonly>
        <template #append><el-button @click="copyKey">{{ t('apiKeys.copy') }}</el-button></template>
      </el-input>
      <template #footer>
        <el-button type="primary" @click="createdDialog = false">{{ t('common.confirm') }}</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { createApiKey, deleteApiKey, errMsg, getApiModels, getApiSettings, updateApiKey, updateApiSettings } from '../api'

const { t } = useI18n()
const enabled = ref(true)
const items = ref([])
const modelOptions = ref([])
const editDialog = ref(false)
const createdDialog = ref(false)
const createdKey = ref('')
const editing = ref(null)
const saving = ref(false)
const form = reactive({ name: '', expires_at: '', models: [] })

async function load() {
  try {
    const [{ data }, { data: modelData }] = await Promise.all([getApiSettings(), getApiModels()])
    enabled.value = data.enabled
    items.value = data.keys || []
    modelOptions.value = [...new Set([
      ...(modelData.models || []),
      ...items.value.flatMap((item) => item.allowed_models || []).filter((model) => model !== '*')
    ])].sort()
  } catch (e) { ElMessage.error(errMsg(e)) }
}

async function toggleAuth(value) {
  try {
    await updateApiSettings(value)
  } catch (e) {
    enabled.value = !value
    ElMessage.error(errMsg(e))
  }
}

function openCreate() {
  editing.value = null
  Object.assign(form, { name: '', expires_at: '', models: [] })
  editDialog.value = true
}

function openEdit(row) {
  editing.value = row
  Object.assign(form, {
    name: row.name || '',
    expires_at: row.expires_at || '',
    models: [...(row.allowed_models || [])]
  })
  editDialog.value = true
}

function payload() {
  const models = form.models.includes('*') ? ['*'] : [...new Set(form.models)]
  return {
    name: form.name,
    expires_at: form.expires_at || null,
    allowed_models: models
  }
}

async function save() {
  saving.value = true
  try {
    if (editing.value) {
      await updateApiKey(editing.value.id, payload())
      ElMessage.success(t('apiKeys.saved'))
    } else {
      const { data } = await createApiKey(payload())
      createdKey.value = data.key
      createdDialog.value = true
    }
    editDialog.value = false
    await load()
  } catch (e) { ElMessage.error(errMsg(e)) } finally { saving.value = false }
}

async function toggleKey(row) {
  try {
    await updateApiKey(row.id, { enabled: row.enabled })
  } catch (e) {
    row.enabled = !row.enabled
    ElMessage.error(errMsg(e))
  }
}

async function remove(row) {
  try { await ElMessageBox.confirm(t('apiKeys.deleteConfirm', { name: row.name || row.preview }), t('common.tip'), { type: 'warning' }) } catch { return }
  try {
    await deleteApiKey(row.id)
    ElMessage.success(t('apiKeys.deleted'))
    await load()
  } catch (e) { ElMessage.error(errMsg(e)) }
}

async function copyKey() {
  await navigator.clipboard.writeText(createdKey.value)
  ElMessage.success(t('apiKeys.copied'))
}

onMounted(load)
</script>

<style scoped>
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 16px;
}
code {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
}
</style>
