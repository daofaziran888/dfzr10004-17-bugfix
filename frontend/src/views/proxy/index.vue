<template>
  <section class="page" data-module="proxy">
    <header class="page-head">
      <div>
        <h2>代理池管理管理</h2>
        <p class="page-desc">维护代理池的节点配置、响应指标与切换策略，为采集网络资源管理提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记代理节点</button>
        <button class="btn" type="button" @click="exportRows">导出代理池管理清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in rowActions(row)"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无代理池管理数据，可先登记代理节点</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条代理池管理记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>
type PoolSummary = {
  total: number
  by_status: Record<string, number>
  abnormal: number
  selectable: number
  availability: number
  selectable_nodes: { id: number; 节点编号: string }[]
  pending_disable: { id: number; 节点编号: string; 原因: string }[]
  disabled_nodes: { id: number; 节点编号: string; 原因: string }[]
}

const ENDPOINT = '/api/proxy'
const baseColumns = ["节点编号", "代理地址", "代理类型", "地理位置", "响应延迟", "可用率", "切换策略", "节点状态"]
// 生命周期状态与健康结论分两列展示：人工停用与网络异常是两类不同原因
const columns = [...baseColumns, '当前状态', '健康状态', '可参与请求']
const ACTIONS_BY_STATUS: Record<string, string[]> = {
  草稿: ['提交校验'],
  待校验: ['启用节点', '上报异常'],
  已启用: ['停用节点', '上报异常'],
  已停用: ['提交校验', '上报异常'],
}
const stats = ref([
  { label: '代理节点总数', value: 0 },
  { label: '待校验节点', value: 0 },
  { label: '已启用节点', value: 0 },
  { label: '已停用节点', value: 0 },
  { label: '异常冷却中', value: 0 },
  { label: '健康可用率', value: '—' },
])

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = baseColumns.slice(0, 3)

function rowActions(row: Row): string[] {
  return ACTIONS_BY_STATUS[String(row.status ?? '')] ?? []
}

function decorate(row: Row): Row {
  const abnormal = Boolean(row.abnormal)
  return {
    ...row,
    当前状态: String(row.status ?? '—'),
    健康状态: abnormal ? '异常（冷却中）' : '健康',
    可参与请求: row.selectable ? '是' : '否',
  }
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '代理节点登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message ?? '代理池管理动作未生效，请稍后重试')
    }
    await Promise.all([reload(), loadPool()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '代理池管理操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('代理节点列表读取失败')
    }
    const payload = await response.json()
    rows.value = (payload.items ?? []).map(decorate)
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '代理池管理列表读取失败'
  }
}

async function loadPool() {
  try {
    const response = await request(`${ENDPOINT}/pool`)
    if (!response.ok) {
      return
    }
    const pool = (await response.json()) as PoolSummary
    stats.value = [
      { label: '代理节点总数', value: pool.total },
      { label: '待校验节点', value: pool.by_status['待校验'] ?? 0 },
      { label: '已启用节点', value: pool.by_status['已启用'] ?? 0 },
      { label: '已停用节点', value: pool.by_status['已停用'] ?? 0 },
      { label: '异常冷却中', value: pool.abnormal },
      { label: '健康可用率', value: `${pool.availability}%` },
    ]
  } catch {
    // 统计卡片刷新失败不阻断列表展示
  }
}

onMounted(() => {
  void reload()
  void loadPool()
})
</script>
