<template>
  <section class="page" data-module="patrol">
    <header class="page-head">
      <div>
        <h2>日常巡查管理</h2>
        <p class="page-desc">维护巡查记录，围绕巡查编号、巡查路段、巡查日期、巡查人员做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记巡查记录</button>
        <button class="btn" type="button" @click="exportRows">导出日常巡查清单</button>
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
          <th>处置建议</th>
          <th>处置班组</th>
          <th>约定版本</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>
            <span v-if="row['处置建议']">{{ row['处置建议'] }}（{{ row['处置时限小时'] }}h）</span>
            <span v-else>—</span>
          </td>
          <td>{{ row['处置班组'] || '—' }}</td>
          <td>{{ row['约定版本'] || '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="adjudicate(row)">裁决</button>
            <button
              v-for="action in actions"
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
          <td :colspan="columns.length + 4" class="empty-state">暂无日常巡查数据，可先登记巡查记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条日常巡查记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/patrol'
const columns = ["巡查编号", "巡查路段", "巡查日期", "巡查人员", "巡查车辆", "发现问题", "处置措施", "巡查状态"]
const actions = ["开始巡查", "完成巡查", "复核确认"]
const statuses = ["待巡查", "巡查中", "已完成", "已复核"]
const stats = [{"label": "今日巡查", "value": 0}, {"label": "待巡查路段", "value": 0}, {"label": "发现问题", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '巡查记录登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('日常巡查动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '日常巡查操作失败'
  }
}

async function adjudicate(row: Row) {
  // 走巡查约定编译器：按发布状态决定权威引擎，结论回写台账/病害清单/车队待办
  errorMessage.value = ''
  try {
    const response = await request(`/api/patrol-compiler/adjudicate/${row.id}`, {
      method: 'POST',
      body: JSON.stringify({ values: {}, terminal: row['来源端'] || 'office' }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.error || '裁决未生效')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '巡查裁决失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('巡查记录列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '日常巡查列表读取失败'
  }
}

onMounted(reload)
</script>
