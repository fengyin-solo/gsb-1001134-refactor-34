<template>
  <section class="page" data-module="patrol-convention">
    <header class="page-head">
      <div>
        <h2>巡查约定编译器</h2>
        <p class="page-desc">
          终端 / 地图 / 后台三套裁决已收束为同一份版本化约定：影子运行 → 逐班组切流 → 全量，
          偏离可一键切回旧实现；结论原子回写巡查台账、病害清单、车队待办。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn danger" type="button" @click="rollback">一键切回旧实现</button>
        <button class="btn" type="button" @click="runReplay">事件重放校验</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">发布模式</span>
        <strong class="stat-value">{{ modeLabel(rollout.mode) }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">现行约定水位</span>
        <strong class="stat-value">{{ currentVersion }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">切流班组</span>
        <strong class="stat-value">{{ rollout.canary_crews?.length ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">重放收敛</span>
        <strong class="stat-value" :class="converged === null ? '' : converged ? 'ok' : 'bad'">
          {{ converged === null ? '未校验' : converged ? '一致' : '偏离' }}
        </strong>
      </article>
    </div>

    <div class="panel-grid">
      <article class="panel">
        <h3>发布策略</h3>
        <div class="mode-row">
          <button
            v-for="item in modes"
            :key="item.mode"
            class="btn"
            :class="{ primary: rollout.mode === item.mode }"
            type="button"
            @click="switchMode(item.mode)"
          >
            {{ item.label }}
          </button>
        </div>
        <label class="filter-item" style="margin-top:12px">
          <span>切流班组白名单（逗号分隔，仅灰度模式生效）</span>
          <input v-model="crewsText" placeholder="如：东片一班,南片二班" />
        </label>
        <p class="hint-text">
          影子运行：对外仍走旧裁决，编译器同步算结论但只留影子记录，绝不落账。
        </p>
        <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
        <p v-if="notice" class="ok-text">{{ notice }}</p>
      </article>

      <article class="panel">
        <h3>约定版本（水位）</h3>
        <ul class="plain-list">
          <li v-for="item in versions" :key="item.version">
            <strong>{{ item.version }}</strong> · {{ item.effective_from }} 起
            <div class="hint-text">{{ item.title }}：{{ item.notes }}</div>
          </li>
        </ul>
      </article>
    </div>

    <article class="panel" style="margin-top:16px">
      <h3>抽样批次比对（新旧结果）</h3>
      <div class="mode-row">
        <label class="filter-item">
          <span>抽样比例</span>
          <input v-model.number="ratio" type="number" min="0.05" max="1" step="0.05" style="width:90px" />
        </label>
        <label class="filter-item">
          <span>盐（留空自动编号）</span>
          <input v-model="salt" placeholder="同盐同数据抽同一批" />
        </label>
        <button class="btn primary" type="button" @click="openBatch">开比对批次</button>
        <button class="btn" type="button" @click="runMigration">存量记录补约定水位</button>
      </div>
      <table class="data-table" style="margin-top:12px">
        <thead>
          <tr>
            <th>批次</th><th>抽样/总量</th><th>偏离数</th><th>偏离率</th><th>约定版本</th><th>开启时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="batch in batches" :key="batch.batch_id">
            <td>{{ batch.batch_id }}</td>
            <td>{{ batch.sampled }} / {{ batch.population }}</td>
            <td :class="batch.drift ? 'bad' : 'ok'">{{ batch.drift }}</td>
            <td>{{ (batch.drift_ratio * 100).toFixed(1) }}%</td>
            <td>{{ batch.version }}</td>
            <td>{{ batch.opened_at }}</td>
          </tr>
          <tr v-if="!batches.length">
            <td colspan="6" class="empty-state">还没有比对批次，先开一批影子比对</td>
          </tr>
        </tbody>
      </table>
    </article>

    <article class="panel" style="margin-top:16px">
      <h3>影子运行记录（只看偏离）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>巡查编号</th><th>班组</th><th>请求端</th><th>偏离字段</th>
            <th>编译器建议</th><th>旧裁决建议</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in shadows" :key="item.shadow_id">
            <td>{{ item['巡查编号'] }}</td>
            <td>{{ item['管养班组'] }}</td>
            <td>{{ sourceLabels[item.source] ?? item.source }}</td>
            <td>{{ Object.keys(item.drift).join('、') || '—' }}</td>
            <td>{{ item.compiler['处置建议'] }}</td>
            <td>{{ item.legacy['处置建议'] }}</td>
          </tr>
          <tr v-if="!shadows.length">
            <td colspan="6" class="empty-state">暂无偏离：要么还没产生巡查流量，要么新旧口径已经一致</td>
          </tr>
        </tbody>
      </table>
    </article>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

const BASE = '/api/patrol/convention'

type Rollout = { mode: string; canary_crews: string[]; history: unknown[] }
type Batch = {
  batch_id: string
  sampled: number
  population: number
  drift: number
  drift_ratio: number
  version: string
  opened_at: string
}
type Shadow = {
  shadow_id: string
  巡查编号: string
  管养班组: string
  source: string
  drift: Record<string, unknown>
  compiler: Record<string, string>
  legacy: Record<string, string>
}

const modes = [
  { mode: 'shadow', label: '影子运行' },
  { mode: 'canary', label: '逐班组切流' },
  { mode: 'compiler', label: '全量编译器' },
  { mode: 'legacy', label: '旧实现（止损位）' },
]
const modeLabels: Record<string, string> = Object.fromEntries(modes.map((item) => [item.mode, item.label]))
const sourceLabels: Record<string, string> = { terminal: '手持终端', map: '地图端', backend: '后台页面' }

const rollout = ref<Rollout>({ mode: 'shadow', canary_crews: [], history: [] })
const versions = ref<Array<{ version: string; effective_from: string; title: string; notes: string }>>([])
const currentVersion = ref('')
const batches = ref<Batch[]>([])
const shadows = ref<Shadow[]>([])
const crewsText = ref('')
const ratio = ref(0.2)
const salt = ref('')
const converged = ref<boolean | null>(null)
const errorMessage = ref('')
const notice = ref('')

function modeLabel(mode: string): string {
  return modeLabels[mode] ?? mode
}

async function refresh() {
  const [rolloutResp, versionResp, batchResp, shadowResp] = await Promise.all([
    request(`${BASE}/rollout`),
    request(`${BASE}/versions`),
    request(`${BASE}/comparisons`),
    request(`${BASE}/shadow?drift_only=true&limit=50`),
  ])
  rollout.value = await rolloutResp.json()
  const versionPayload = await versionResp.json()
  versions.value = versionPayload.versions
  currentVersion.value = versionPayload.current
  batches.value = (await batchResp.json()).items
  shadows.value = (await shadowResp.json()).items
  crewsText.value = rollout.value.canary_crews.join(',')
}

async function switchMode(mode: string) {
  errorMessage.value = ''
  notice.value = ''
  const crews = mode === 'canary'
    ? crewsText.value.split(/[,，]/).map((item) => item.trim()).filter(Boolean)
    : []
  const resp = await request(`${BASE}/rollout`, {
    method: 'POST',
    body: JSON.stringify({ mode, canary_crews: crews, reason: '控制台切换', operator: '值班长' }),
  })
  if (!resp.ok) {
    errorMessage.value = '发布策略切换失败'
    return
  }
  notice.value = `已切换到「${modeLabel(mode)}」`
  await refresh()
}

async function rollback() {
  errorMessage.value = ''
  notice.value = ''
  const resp = await request(`${BASE}/rollback`, {
    method: 'POST',
    body: JSON.stringify({ reason: '控制台一键止损', operator: '值班长' }),
  })
  if (!resp.ok) {
    errorMessage.value = '切回旧实现失败'
    return
  }
  notice.value = '已切回旧实现，全部流量恢复三套旧裁决'
  converged.value = null
  await refresh()
}

async function openBatch() {
  errorMessage.value = ''
  notice.value = ''
  const resp = await request(`${BASE}/comparisons`, {
    method: 'POST',
    body: JSON.stringify({ ratio: ratio.value, salt: salt.value || undefined, scope: 'manual' }),
  })
  if (!resp.ok) {
    errorMessage.value = '比对批次开启失败'
    return
  }
  const batch = await resp.json()
  notice.value = `批次 ${batch.batch_id}：抽样 ${batch.sampled}/${batch.population}，偏离 ${batch.drift} 条`
  salt.value = ''
  await refresh()
}

async function runMigration() {
  errorMessage.value = ''
  notice.value = ''
  const resp = await request(`${BASE}/migration`, {
    method: 'POST',
    body: JSON.stringify({ batch_id: `MIG-${Date.now()}`, operator: '值班长' }),
  })
  if (!resp.ok) {
    errorMessage.value = '存量迁移失败'
    return
  }
  const report = await resp.json()
  notice.value = `水位补档 ${report.backfilled} 条（legacy ${report.waterlines.legacy} / ${currentVersion.value === 'v2026-10' ? 'v2026-09' : '基线'} ${report.waterlines['v2026-09'] ?? 0}）`
}

async function runReplay() {
  errorMessage.value = ''
  notice.value = ''
  const resp = await request(`${BASE}/replay`, { method: 'POST' })
  if (!resp.ok) {
    errorMessage.value = '事件重放失败'
    return
  }
  const report = await resp.json()
  converged.value = report.converged
  notice.value = report.converged
    ? `重放 ${report.events} 个事件，派生台账与现态完全一致`
    : '重放结果与现态不一致，请立即排查事件处理函数'
}

onMounted(refresh)
</script>

<style scoped>
.page-actions {
  display: flex;
  gap: 8px;
}
.btn.danger {
  background: #b42318;
  color: #fff;
  border-color: #b42318;
}
.panel-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-top: 16px;
}
.panel {
  background: #fff;
  border: 1px solid #e4e7ec;
  border-radius: 8px;
  padding: 16px;
}
.panel h3 {
  margin: 0 0 12px;
  font-size: 15px;
}
.mode-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: flex-end;
}
.plain-list {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: 10px;
}
.hint-text {
  color: #667085;
  font-size: 12px;
  margin: 8px 0 0;
}
.ok-text {
  color: #067647;
  font-size: 13px;
}
.ok {
  color: #067647;
}
.bad {
  color: #b42318;
}
</style>
