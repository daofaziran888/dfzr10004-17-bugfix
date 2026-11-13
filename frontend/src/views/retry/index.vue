<template>
  <section class="page" data-module="retry">
    <header class="page-head">
      <div>
        <h2>异常重试管理管理</h2>
        <p class="page-desc">维护异常重试的异常分类、退避算法与降级策略，为采集异常恢复提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记重试规则</button>
        <button class="btn" type="button" @click="exportRows">导出异常重试管理清单</button>
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
          <td :colspan="columns.length + 1" class="empty-state">暂无异常重试管理数据，可先登记重试规则</td>
        </tr>
      </tbody>
    </table>

    <section class="pool-panel">
      <h3 class="pool-title">重试选择代理池（同步代理节点是否可参与请求）</h3>
      <div class="pool-grid">
        <article class="stat-card">
          <span class="stat-label">可参与请求的代理节点（{{ pool?.selectable ?? 0 }}）</span>
          <ul class="pool-list">
            <li v-for="node in pool?.selectable_nodes ?? []" :key="node.id">{{ node.节点编号 }}</li>
            <li v-if="!(pool?.selectable_nodes ?? []).length" class="pool-empty">暂无可参与请求的节点</li>
          </ul>
        </article>
        <article class="stat-card">
          <span class="stat-label">待禁用清单（网络异常，冷却中，{{ pool?.abnormal ?? 0 }}）</span>
          <ul class="pool-list">
            <li v-for="node in pool?.pending_disable ?? []" :key="node.id">
              {{ node.节点编号 }}（{{ node.原因 }}）
            </li>
            <li v-if="!(pool?.pending_disable ?? []).length" class="pool-empty">暂无异常节点</li>
          </ul>
        </article>
        <article class="stat-card">
          <span class="stat-label">已停用节点（人工停用，{{ pool?.disabled_nodes.length ?? 0 }}）</span>
          <ul class="pool-list">
            <li v-for="node in pool?.disabled_nodes ?? []" :key="node.id">
              {{ node.节点编号 }}（{{ node.原因 }}）
            </li>
            <li v-if="!(pool?.disabled_nodes ?? []).length" class="pool-empty">暂无停用节点</li>
          </ul>
        </article>
      </div>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条异常重试管理记录</span>
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

const ENDPOINT = '/api/retry'
const baseColumns = ["规则编号", "异常类型", "重试上限", "退避策略", "冷却时间", "告警阈值", "降级方案", "是否启用"]
const columns = [...baseColumns, '当前状态']
const ACTIONS_BY_STATUS: Record<string, string[]> = {
  草稿: ['提交校验'],
  待校验: ['启用规则'],
  已启用: ['停用规则'],
  已停用: ['提交校验'],
}
const stats = ref([
  { label: '重试规则总数', value: 0 },
  { label: '待校验规则', value: 0 },
  { label: '已启用规则', value: 0 },
])

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = baseColumns.slice(0, 3)
const pool = ref<PoolSummary | null>(null)

function rowActions(row: Row): string[] {
  return ACTIONS_BY_STATUS[String(row.status ?? '')] ?? []
}

function decorate(row: Row): Row {
  return { ...row, 当前状态: String(row.status ?? '—') }
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '重试规则登记入口尚未接入审批流'
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
      throw new Error(payload?.message ?? '异常重试管理动作未生效，请稍后重试')
    }
    await Promise.all([reload(), loadPool()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '异常重试管理操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('重试规则列表读取失败')
    }
    const payload = await response.json()
    rows.value = (payload.items ?? []).map(decorate)
    total.value = payload.total ?? rows.value.length
    refreshStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '异常重试管理列表读取失败'
  }
}

async function refreshStats() {
  try {
    const response = await request(`${ENDPOINT}?size=200`)
    if (!response.ok) {
      return
    }
    const payload = await response.json()
    const all = (payload.items ?? []) as Row[]
    stats.value = [
      { label: '重试规则总数', value: payload.total ?? all.length },
      { label: '待校验规则', value: all.filter((row) => row.status === '待校验').length },
      { label: '已启用规则', value: all.filter((row) => row.status === '已启用').length },
    ]
  } catch {
    // 统计卡片刷新失败不阻断列表展示
  }
}

async function loadPool() {
  try {
    const response = await request('/api/proxy/pool')
    if (!response.ok) {
      return
    }
    pool.value = (await response.json()) as PoolSummary
  } catch {
    // 代理池面板刷新失败不阻断规则列表展示
  }
}

onMounted(() => {
  void reload()
  void loadPool()
})
</script>

<style scoped>
.pool-panel { margin-top: 16px; }
.pool-title { font-size: 14px; margin: 0 0 8px; }
.pool-grid { display: flex; gap: 12px; }
.pool-list { margin: 6px 0 0; padding-left: 18px; font-size: 13px; }
.pool-empty { color: var(--muted); list-style: none; margin-left: -18px; }
</style>
