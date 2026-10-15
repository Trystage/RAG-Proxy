<template>
  <div>
    <el-row :gutter="16">
      <el-col :span="4" v-for="card in cards" :key="card.label">
        <el-card shadow="hover">
          <div class="stat-label">{{ card.label }}</div>
          <div class="stat-value" :style="{ color: card.color }">{{ card.value }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" style="margin-top: 16px">
      <template #header><span class="card-dot"></span>{{ t('dashboard.last24h') }}</template>
      <div ref="chartRef" style="height: 280px"></div>
    </el-card>

    <el-row :gutter="16" style="margin-top: 16px">
      <el-col :span="12">
        <el-card shadow="never">
          <template #header><span class="card-dot"></span>{{ t('dashboard.byModel') }}</template>
          <el-table :data="stats.per_model || []" size="small" max-height="300">
            <el-table-column prop="model" :label="t('dashboard.model')" />
            <el-table-column prop="c" :label="t('dashboard.requests')" width="90" />
            <el-table-column prop="hits" :label="t('dashboard.hits')" width="80" />
            <el-table-column prop="avg_ms" :label="t('dashboard.avgRetrieve')" width="140" />
          </el-table>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never">
          <template #header><span class="card-dot"></span>{{ t('dashboard.byKb') }}</template>
          <el-table :data="stats.per_kb || []" size="small" max-height="300">
            <el-table-column prop="kb" :label="t('dashboard.kb')" />
            <el-table-column prop="c" :label="t('dashboard.requests')" width="110" />
          </el-table>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import * as echarts from 'echarts'
import http from '../api'

const { t } = useI18n()

const stats = ref({})
const chartRef = ref(null)
let chart = null

const cards = computed(() => [
  { label: t('dashboard.total'), value: stats.value.total ?? 0, color: '#F57EA8' },
  { label: t('dashboard.today'), value: stats.value.today ?? 0, color: '#7FB8E8' },
  { label: t('dashboard.hitRate'), value: ((stats.value.hit_rate ?? 0) * 100).toFixed(1) + '%', color: '#9F8FE8' },
  { label: t('dashboard.avgLatency'), value: (stats.value.avg_retrieve_ms ?? 0) + ' ms', color: '#F5A97E' },
  { label: t('dashboard.errors'), value: stats.value.error_count ?? 0, color: '#909399' }
])

function renderChart() {
  if (!chartRef.value) return
  chart = chart || echarts.init(chartRef.value)
  const h24 = stats.value.last24h || []
  chart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 40, right: 20, top: 20, bottom: 30 },
    xAxis: { type: 'category', data: h24.map((x) => x.h.slice(11, 16)) },
    yAxis: { type: 'value', minInterval: 1 },
    series: [{
      type: 'bar',
      data: h24.map((x) => x.c),
      itemStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: '#8FC7E8' },
          { offset: 1, color: '#F7A8C4' }
        ]),
        borderRadius: [6, 6, 0, 0]
      },
      barMaxWidth: 26
    }]
  })
}

async function load() {
  const { data } = await http.get('/admin/dashboard/stats')
  stats.value = data
  renderChart()
}

onMounted(load)
onBeforeUnmount(() => chart && chart.dispose())
</script>

<style scoped>
.stat-label { color: #6E6E80; font-size: 13px; }
.stat-value { font-size: 26px; font-weight: 700; margin-top: 6px; }
.card-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 8px;
  background: linear-gradient(120deg, #F7A8C4, #8FC7E8);
}
</style>
