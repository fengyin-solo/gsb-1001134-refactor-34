<template>
  <section class="page" data-module="patrol-compiler">
    <header class="page-head">
      <div>
        <h2>巡查约定编译器</h2>
        <p class="page-desc">
          把终端、地图、后台三套裁决收束为版本化约定：影子运行 → 逐班组切流 → 全量，
          结论回写巡查台账、病害清单与车队待办；偏离可一键切回旧实现。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="replayEvents">事件重放（幂等）</button>
        <button class="btn danger" type="button" @click="fallback">一键切回旧实现</button>
      </div>
    </header>

    <p v-if="message" class="page-desc" :class="messageKind">{{ message }}</p>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">发布模式</span>
        <strong class="stat-value">{{ rollout.mode || '—' }}<template v-if="rollout.force_legacy">（旧实现兜底中）</template></strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">生效约定</span>
        <strong class="stat-value">{{ rollout.active_convention_version || '—' }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">影子偏离累计</span>
        <strong class="stat-value">{{ drifts.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">车队待办</span>
        <strong class="stat-value">{{ todos.length }}</strong>
      </article>
    </div>

    <div class="panel-grid">
      <!-- 发布控制 -->
      <article class="panel">
        <h3>发布与切流</h3>
        <div class="form-row">
          <label>模式
            <select v-model="rolloutForm.mode">
              <option value="shadow">shadow（影子双跑）</option>
              <option value="canary">canary（按班组）</option>
              <option value="full">full（全量编译器）</option>
            </select>
          </label>
          <label>切流班组（逗号分隔）
            <input v-model="rolloutForm.crewsText" placeholder="路面一班,交安班" />
          </label>
        </div>
        <div class="form-row">
          <button class="btn primary" type="button" @click="saveRollout">应用切流</button>
          <span class="hint">canary 仅名单内班组走编译器；任何模式下编译器都影子双跑并记账。</span>
        </div>
      </article>

      <!-- 约定水位 -->
      <article class="panel">
        <h3>约定版本与水位</h3>
        <table class="mini-table">
          <thead><tr><th>版本</th><th>生效日期</th><th>规则数</th><th>指纹（前 10 位）</th></tr></thead>
          <tbody>
            <tr v-for="item in conventions" :key="item.version">
              <td>{{ item.version }}</td>
              <td>{{ item.effective_from }}</td>
              <td>{{ item.rule_count }}</td>
              <td :title="item.fingerprint">{{ item.fingerprint.slice(0, 10) }}</td>
            </tr>
          </tbody>
        </table>
        <p class="hint">存量巡查按「巡查日期」选用当时生效的约定留档，事件携带完整水位。</p>
      </article>

      <!-- 单条裁决 -->
      <article class="panel">
        <h3>对巡查记录裁决</h3>
        <div class="form-row">
          <label>巡查记录 ID <input v-model="adjudicateForm.id" placeholder="如 5" /></label>
          <label>来源端
            <select v-model="adjudicateForm.terminal">
              <option value="terminal">手持终端</option>
              <option value="map">地图图层</option>
              <option value="office">后台台账</option>
            </select>
          </label>
          <label>发现问题（可空，空则取台账原文）
            <input v-model="adjudicateForm.text" placeholder="如：严重坑槽，有安全隐患" />
          </label>
        </div>
        <div class="form-row">
          <button class="btn primary" type="button" @click="runAdjudicate">执行裁决</button>
          <span v-if="lastVerdict" class="hint">
            权威引擎：{{ lastVerdict.engine }} · {{ lastVerdict.disposition }}
            · {{ lastVerdict.sla_hours }}h · 班组 {{ lastVerdict.target_crew || '—' }}
          </span>
        </div>
      </article>

      <!-- 存量迁移 -->
      <article class="panel">
        <h3>存量迁移（整批原子、带水位）</h3>
        <div class="form-row">
          <label>记录 ID 列表（逗号分隔）
            <input v-model="migrationForm.idsText" placeholder="4,5,6" />
          </label>
          <label>批次标签
            <input v-model="migrationForm.label" placeholder="如 batch-2026-09" />
          </label>
        </div>
        <div class="form-row">
          <button class="btn primary" type="button" @click="runMigration">按当时约定迁移留档</button>
          <span class="hint">任一条转换失败，整批不落任何事件（不会只写入半个事件）。</span>
        </div>
      </article>

      <!-- 抽样比对 -->
      <article class="panel panel-wide">
        <h3>新旧结果抽样比对</h3>
        <div class="form-row">
          <label class="grow">批次标签
            <input v-model="reconcileForm.label" placeholder="如 sample-A" />
          </label>
        </div>
        <textarea v-model="reconcileForm.samplesText" rows="5" class="code-input"
          placeholder='[{"source":"terminal","终端问题码":"T_CRACK","终端严重程度":"L3"},{"source":"map","图层类型":"面层坑槽图层","告警等级":"橙色"}]'></textarea>
        <div class="form-row">
          <button class="btn primary" type="button" @click="runReconcile">跑比对批次</button>
          <span v-if="lastReport" class="hint">
            共 {{ lastReport.total }} 条，一致 {{ lastReport.matched }}，偏离 {{ lastReport.diverged }}，
            未识别 {{ lastReport.unrecognized }}
          </span>
        </div>
        <table v-if="lastReport" class="mini-table">
          <thead><tr><th>#</th><th>端</th><th>问题码</th><th>新结论</th><th>旧结论</th><th>偏离字段</th></tr></thead>
          <tbody>
            <tr v-for="item in lastReport.items" :key="item.index">
              <td>{{ item.index }}</td>
              <td>{{ item.source }}</td>
              <td>{{ item.facts.problem_code }} / {{ item.facts.severity }}</td>
              <td>{{ item.compiler.disposition }} · {{ item.compiler.sla_hours }}h</td>
              <td>{{ item.legacy.disposition }} · {{ item.legacy.sla_hours }}h</td>
              <td>{{ Object.keys(item.diff).join('、') || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </article>

      <!-- 影子偏离 -->
      <article class="panel panel-wide">
        <h3>影子偏离台账</h3>
        <table class="mini-table">
          <thead><tr><th>记录</th><th>端</th><th>事实</th><th>编译器</th><th>旧实现</th><th>偏离字段</th></tr></thead>
          <tbody>
            <tr v-for="(drift, idx) in drifts.slice().reverse()" :key="idx">
              <td>#{{ drift.patrol_id }}</td>
              <td>{{ drift.terminal }}</td>
              <td>{{ drift.facts.problem_code }} / {{ drift.facts.severity }}</td>
              <td>{{ drift.compiler.disposition }} · {{ drift.compiler.sla_hours }}h</td>
              <td>{{ drift.legacy.disposition }} · {{ drift.legacy.sla_hours }}h</td>
              <td>{{ Object.keys(drift.diff).join('、') }}</td>
            </tr>
            <tr v-if="!drifts.length"><td colspan="6" class="empty-state">暂无偏离记录</td></tr>
          </tbody>
        </table>
      </article>

      <!-- 回写三表 -->
      <article class="panel">
        <h3>病害清单（编译器接管行）</h3>
        <table class="mini-table">
          <thead><tr><th>编号</th><th>来源巡查</th><th>类型</th><th>处置</th><th>约定</th></tr></thead>
          <tbody>
            <tr v-for="row in pavementRows" :key="row.id">
              <td>{{ row['病害编号'] }}</td><td>#{{ row['来源巡查'] }}</td>
              <td>{{ row['病害类型'] }}</td><td>{{ row['处置建议'] }}</td>
              <td>{{ row['约定版本'] }}</td>
            </tr>
            <tr v-if="!pavementRows.length"><td colspan="5" class="empty-state">暂无</td></tr>
          </tbody>
        </table>
      </article>

      <article class="panel">
        <h3>车队待办</h3>
        <table class="mini-table">
          <thead><tr><th>编号</th><th>来源</th><th>班组</th><th>处置</th><th>时限</th></tr></thead>
          <tbody>
            <tr v-for="row in todos" :key="row.id">
              <td>{{ row['待办编号'] }}</td><td>#{{ row['来源巡查'] }}</td>
              <td>{{ row['处置班组'] }}</td><td>{{ row['处置建议'] }}</td>
              <td>{{ row['处置时限小时'] }}h</td>
            </tr>
            <tr v-if="!todos.length"><td colspan="5" class="empty-state">暂无待办</td></tr>
          </tbody>
        </table>
      </article>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

const BASE = '/api/patrol-compiler'

type VerdictShape = {
  engine: string
  disposition: string
  sla_hours: number
  target_crew: string | null
}
type Rollout = {
  mode: string
  canary_crews: string[]
  force_legacy: boolean
  active_convention_version: string | null
}

const conventions = ref<Array<Record<string, unknown>>>([])
const rollout = ref<Rollout>({ mode: 'shadow', canary_crews: [], force_legacy: false, active_convention_version: null })
const drifts = ref<Array<Record<string, any>>>([])
const todos = ref<Array<Record<string, any>>>([])
const pavementRows = ref<Array<Record<string, any>>>([])
const message = ref('')
const messageKind = ref('hint')
const lastVerdict = ref<VerdictShape | null>(null)
const lastReport = ref<Record<string, any> | null>(null)

const rolloutForm = reactive({ mode: 'shadow', crewsText: '' })
const adjudicateForm = reactive({ id: '5', terminal: 'map', text: '' })
const migrationForm = reactive({ idsText: '4,5,6', label: 'batch-seed' })
const reconcileForm = reactive({
  label: 'sample-A',
  samplesText: JSON.stringify([
    { source: 'terminal', 终端问题码: 'T_CRACK', 终端严重程度: 'L3' },
    { source: 'map', 图层类型: '面层坑槽图层', 告警等级: '橙色' },
  ], null, 2),
})

async function postJson(path: string, body: unknown): Promise<any> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  return response.json()
}

function notify(text: string, ok = true) {
  message.value = text
  messageKind.value = ok ? 'ok-text' : 'error-text'
}

async function refresh() {
  const [conv, roll, drift, todoResp, paveResp] = await Promise.all([
    request(`${BASE}/conventions`).then((r) => r.json()),
    request(`${BASE}/rollout`).then((r) => r.json()),
    request(`${BASE}/drifts`).then((r) => r.json()),
    request(`${BASE}/fleet-todos`).then((r) => r.json()),
    request('/api/pavement?size=200').then((r) => r.json()),
  ])
  conventions.value = conv.items
  rollout.value = roll
  rolloutForm.mode = roll.mode
  rolloutForm.crewsText = (roll.canary_crews || []).join(',')
  drifts.value = drift.items
  todos.value = todoResp.items
  pavementRows.value = (paveResp.items || []).filter((row: Record<string, unknown>) => row._compiler_managed)
}

async function saveRollout() {
  const crews = rolloutForm.crewsText.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
  const result = await postJson(`${BASE}/rollout`, { values: { mode: rolloutForm.mode, canary_crews: crews } })
  notify(`已切换到 ${result.mode}，切流班组：${(result.canary_crews || []).join('、') || '（无）'}`)
  await refresh()
}

async function fallback() {
  const result = await postJson(`${BASE}/rollout/fallback`, { values: { reason: '控制台人工切回' } })
  notify(`已一键切回旧实现（${result.reason}）`, false)
  await refresh()
}

async function runAdjudicate() {
  const id = Number(adjudicateForm.id)
  if (!id) {
    notify('请填写巡查记录 ID', false)
    return
  }
  const values = adjudicateForm.text ? { 发现问题: adjudicateForm.text } : {}
  const result = await postJson(`${BASE}/adjudicate/${id}`, { values, terminal: adjudicateForm.terminal })
  if (!result.ok) {
    notify(result.error || '裁决失败', false)
    return
  }
  lastVerdict.value = result.verdict
  notify(`裁决完成（模式：${result.mode}${result.idempotent ? '，幂等命中' : ''}）`)
  await refresh()
}

async function runMigration() {
  const ids = migrationForm.idsText.split(/[,，]/).map((s) => Number(s.trim())).filter(Boolean)
  const result = await postJson(`${BASE}/migrate`, {
    patrol_ids: ids,
    batch_label: migrationForm.label || `batch-${Date.now()}`,
    strict: true,
  })
  if (!result.ok) {
    notify(`迁移已整批中止：${(result.failures || []).map((f: any) => `#${f.patrol_id} ${f.reason}`).join('；')}`, false)
    return
  }
  notify(`迁移完成：${result.migrated} 条，约定版本 ${(result.convention_versions || []).join('、')}，事件 ${result.event_count} 个`)
  await refresh()
}

async function runReconcile() {
  let samples: unknown
  try {
    samples = JSON.parse(reconcileForm.samplesText)
  } catch {
    notify('抽样数据不是合法 JSON', false)
    return
  }
  if (!Array.isArray(samples)) {
    notify('抽样数据必须是数组', false)
    return
  }
  lastReport.value = await postJson(`${BASE}/reconcile`, {
    samples,
    batch_label: reconcileForm.label || `sample-${Date.now()}`,
  })
  await refresh()
}

async function replayEvents() {
  const result = await postJson(`${BASE}/replay`, {})
  notify(`事件重放完成：${result.events_replayed} 个事件，投影已按序重建`)
  await refresh()
}

onMounted(refresh)
</script>

<style scoped>
.panel-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-top: 12px;
}
.panel-wide {
  grid-column: 1 / -1;
}
.panel {
  background: #fff;
  border: 1px solid #e6e8eb;
  border-radius: 8px;
  padding: 14px 16px;
}
.panel h3 {
  margin: 0 0 10px;
  font-size: 15px;
}
.form-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: flex-end;
  margin-bottom: 8px;
}
.form-row label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
  color: #555;
  flex: 1 1 180px;
}
.form-row .grow {
  flex: 1 1 100%;
}
.form-row input,
.form-row select,
.code-input {
  padding: 6px 8px;
  border: 1px solid #d6d9de;
  border-radius: 6px;
  font-size: 13px;
}
.code-input {
  width: 100%;
  box-sizing: border-box;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.mini-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}
.mini-table th,
.mini-table td {
  border-bottom: 1px solid #eef0f2;
  padding: 6px 8px;
  text-align: left;
}
.hint {
  color: #777;
  font-size: 12.5px;
}
.ok-text { color: #1a7f37; }
.error-text { color: #c0392b; }
.btn.danger {
  border-color: #c0392b;
  color: #c0392b;
}
</style>
