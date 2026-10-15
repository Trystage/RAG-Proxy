<template>
  <el-card shadow="never">
    <el-form inline>
      <el-form-item :label="t('logs.start')">
        <el-date-picker v-model="filters.start" type="datetime" :placeholder="t('logs.startPh')" value-format="YYYY-MM-DD HH:mm:ss" />
      </el-form-item>
      <el-form-item :label="t('logs.end')">
        <el-date-picker v-model="filters.end" type="datetime" :placeholder="t('logs.endPh')" value-format="YYYY-MM-DD HH:mm:ss" />
      </el-form-item>
      <el-form-item :label="t('logs.model')">
        <el-input v-model="filters.model" :placeholder="t('logs.modelPh')" clearable style="width: 160px" />
      </el-form-item>
      <el-form-item :label="t('logs.kb')">
        <el-input v-model="filters.kb" :placeholder="t('logs.kbPh')" clearable style="width: 160px" />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" @click="search">{{ t('common.search') }}</el-button>
      </el-form-item>
    </el-form>

    <el-table :data="rows" size="small" max-height="520">
      <el-table-column prop="ts" :label="t('logs.time')" width="165" />
      <el-table-column prop="model" :label="t('logs.model')" width="140" />
      <el-table-column prop="kb" :label="t('logs.kb')" width="150" show-overflow-tooltip />
      <el-table-column prop="query_preview" :label="t('logs.query')" min-width="200" show-overflow-tooltip />
      <el-table-column :label="t('logs.hit')" width="80">
        <template #default="{ row }">
          <el-tag :type="row.hit ? 'success' : 'info'" size="small">{{ row.hit ? t('common.yes') : t('common.no') }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="retrieve_ms" :label="t('logs.retrieveMs')" width="110" />
      <el-table-column prop="total_ms" :label="t('logs.totalMs')" width="110" />
      <el-table-column :label="t('logs.status')" width="80">
        <template #default="{ row }">
          <el-tag :type="row.status < 400 ? 'success' : 'danger'" size="small">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('logs.stream')" width="80">
        <template #default="{ row }">
          <el-tag v-if="row.stream" size="small" type="warning">SSE</el-tag>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('common.actions')" width="110" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="showDetail(row)">{{ t('common.detail') }}</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      style="margin-top: 12px; justify-content: flex-end"
      layout="total, prev, pager, next"
      :total="total"
      :page-size="size"
      v-model:current-page="page"
      @current-change="load"
    />

    <el-dialog v-model="detailDlg" :title="t('logs.detailTitle')" width="640px">
      <el-descriptions :column="1" border size="small">
        <el-descriptions-item :label="t('logs.time')">{{ detail.ts }}</el-descriptions-item>
        <el-descriptions-item :label="t('logs.model')">{{ detail.model }}</el-descriptions-item>
        <el-descriptions-item :label="t('logs.kb')">{{ detail.kb || '-' }}</el-descriptions-item>
        <el-descriptions-item label="Query">{{ detail.query_preview }}</el-descriptions-item>
        <el-descriptions-item :label="t('logs.hitDocs')">
          <template v-if="detail.hit_docs">
            <el-tag v-for="d in parseDocs(detail.hit_docs)" :key="d" size="small" style="margin: 2px">{{ d }}</el-tag>
          </template>
          <span v-else>-</span>
        </el-descriptions-item>
        <el-descriptions-item :label="t('logs.retrieveCost')">{{ detail.retrieve_ms }} ms</el-descriptions-item>
        <el-descriptions-item :label="t('logs.totalCost')">{{ detail.total_ms }} ms</el-descriptions-item>
        <el-descriptions-item label="Upstream">{{ detail.upstream || '-' }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import http from '../api'

const { t } = useI18n()

const filters = reactive({ start: '', end: '', model: '', kb: '' })
const rows = ref([])
const total = ref(0)
const page = ref(1)
const size = 20
const detailDlg = ref(false)
const detail = ref({})

function parseDocs(s) {
  try { return JSON.parse(s) } catch { return [] }
}

async function load() {
  try {
    const { data } = await http.get('/admin/logs', {
      params: { ...filters, page: page.value, size }
    })
    rows.value = data.items || []
    total.value = data.total || 0
  } catch (e) { /* 拦截器已处理 */ }
}

function search() { page.value = 1; load() }
function showDetail(row) { detail.value = row; detailDlg.value = true }

onMounted(load)
</script>
