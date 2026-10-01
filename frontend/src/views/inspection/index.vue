<template>
  <section class="page" data-module="inspection">
    <header class="page-head">
      <div>
        <h2>定期检验管理</h2>
        <p class="page-desc">季度定期检验支持勾选多台设备一次安排；逐台带出被检设备与检验类别，合格设备不再自动排进下一批。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openBatchDialog">批量安排检验</button>
        <button class="btn" type="button" @click="exportRows">导出定期检验清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in statCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload(1)">
      <label class="filter-item">
        <span>检验编号 / 设备</span>
        <input v-model="keyword" placeholder="按检验编号、设备编号或名称检索" />
      </label>
      <label class="filter-item">
        <span>检验状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
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
          <td v-for="column in columns" :key="column">{{ row[column] || '—' }}</td>
          <td class="row-actions">
            <template v-if="row.status === '待检验'">
              <button v-if="row['批次号']" class="link" type="button" @click="retryBatch(String(row['批次号']))">重试获取机构</button>
              <button v-else class="link" type="button" @click="runAction('安排检验', row)">安排检验</button>
            </template>
            <template v-else-if="row.status === '检验中'">
              <button class="link" type="button" @click="openConclusion(row)">录入结论</button>
              <button class="link danger" type="button" @click="runAction('下达整改', row)">退回整改</button>
            </template>
            <template v-else>
              <span class="muted-text">{{ row.status === '合格' ? '已完成' : '整改中' }}</span>
            </template>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无符合条件的检验任务，可点击右上角批量安排检验</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条定期检验记录</span>
      <div class="pager">
        <button class="btn" type="button" :disabled="page <= 1" @click="reload(page - 1)">上一页</button>
        <span>第 {{ page }} / {{ totalPages }} 页</span>
        <button class="btn" type="button" :disabled="page >= totalPages" @click="reload(page + 1)">下一页</button>
      </div>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-else-if="noticeMessage" class="ok-text">{{ noticeMessage }}</span>
    </footer>

    <!-- 批量安排检验：勾选同一批设备一次提交 -->
    <div v-if="batchDialog.open" class="modal-mask" @click.self="closeBatchDialog">
      <div class="modal">
        <h3>批量安排季度定期检验</h3>
        <p class="modal-tip">已合格或已有在办检验的设备不再出现在候选名单中；同一批次重复提交只认第一次。</p>
        <div class="modal-filter">
          <input v-model="candidateKeyword" placeholder="按设备编号或名称筛选候选设备" @keyup.enter="loadCandidates" />
          <button class="btn" type="button" @click="loadCandidates">筛选</button>
        </div>
        <div class="batch-meta">
          <label>
            <span>计划检验日</span>
            <input v-model="batchDialog.plannedDate" type="date" />
          </label>
          <span class="muted-text">已勾选 {{ selectedCandidates.size }} 台 / 候选 {{ candidates.length }} 台</span>
        </div>
        <table class="data-table candidate-table">
          <thead>
            <tr>
              <th class="check-col"><input type="checkbox" :checked="allChecked" @change="toggleAll" /></th>
              <th>设备编号</th>
              <th>被检设备</th>
              <th>检验类别</th>
              <th>使用单位</th>
              <th>安装地点</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="device in candidates" :key="device['设备登记id']">
              <td><input type="checkbox" :value="device['设备登记id']" v-model="selectedList" /></td>
              <td>{{ device['设备编号'] }}</td>
              <td>{{ device['被检设备'] }}</td>
              <td>{{ device['检验类别'] }}</td>
              <td>{{ device['使用单位'] }}</td>
              <td>{{ device['安装地点'] }}</td>
            </tr>
            <tr v-if="!candidates.length">
              <td colspan="6" class="empty-state">暂无可安排设备（均已合格或在检验中）</td>
            </tr>
          </tbody>
        </table>
        <p v-if="batchDialog.error" class="error-text">{{ batchDialog.error }}</p>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="closeBatchDialog">取消</button>
          <button class="btn primary" type="button" :disabled="batchDialog.submitting || !selectedList.length" @click="submitBatch">
            {{ batchDialog.submitting ? '提交中…' : `一次性安排（${selectedList.length} 台）` }}
          </button>
        </div>
      </div>
    </div>

    <!-- 录入结论：结论与具体设备绑定 -->
    <div v-if="conclusionDialog.open" class="modal-mask" @click.self="closeConclusion">
      <div class="modal small">
        <h3>录入检验结论</h3>
        <dl class="device-summary">
          <div><dt>被检设备</dt><dd>{{ conclusionDialog.row?.['被检设备'] }}（{{ conclusionDialog.row?.['设备编号'] }}）</dd></div>
          <div><dt>检验类别</dt><dd>{{ conclusionDialog.row?.['检验类别'] }}</dd></div>
          <div><dt>检验机构</dt><dd>{{ conclusionDialog.row?.['检验机构'] }}</dd></div>
        </dl>
        <label class="conclusion-field">
          <span>检验结论（合格）</span>
          <textarea v-model="conclusionDialog.text" rows="3" placeholder="请填写检验结论，确认与被检设备对应"></textarea>
        </label>
        <label class="conclusion-field">
          <span>实际检验日</span>
          <input v-model="conclusionDialog.actualDate" type="date" />
        </label>
        <p v-if="conclusionDialog.error" class="error-text">{{ conclusionDialog.error }}</p>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="closeConclusion">取消</button>
          <button class="btn primary" type="button" :disabled="conclusionDialog.submitting" @click="submitConclusion">
            {{ conclusionDialog.submitting ? '提交中…' : '提交合格结论' }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>
type Candidate = Record<string, string | number>

const ENDPOINT = '/api/inspection'
const PAGE_SIZE = 10
const columns = ['检验编号', '批次号', '被检设备', '设备编号', '检验类别', '检验机构', '计划检验日', '实际检验日', '检验结论', '检验状态']
const statuses = ['待检验', '检验中', '合格', '不合格']

const rows = ref<Row[]>([])
const total = ref(0)
const page = ref(1)
const keyword = ref('')
const statusFilter = ref('')
const errorMessage = ref('')
const noticeMessage = ref('')
const statCards = ref([
  { label: '待检验设备', value: 0 },
  { label: '检验中设备', value: 0 },
  { label: '合格设备', value: 0 },
  { label: '不合格设备', value: 0 },
])

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

const batchDialog = reactive({
  open: false,
  submitting: false,
  plannedDate: '',
  error: '',
})
const candidates = ref<Candidate[]>([])
const candidateKeyword = ref('')
const selectedList = ref<number[]>([])
const selectedCandidates = computed(() => new Set(selectedList.value))
const allChecked = computed(
  () => candidates.value.length > 0 && selectedList.value.length === candidates.value.length,
)

const conclusionDialog = reactive({
  open: false,
  submitting: false,
  row: null as Row | null,
  text: '',
  actualDate: '',
  error: '',
})

function flash(message: string, ok = false) {
  errorMessage.value = ok ? '' : message
  noticeMessage.value = ok ? message : ''
}

async function loadStats() {
  try {
    const response = await request(`${ENDPOINT}/stats`)
    if (!response.ok) return
    const data: Record<string, number> = await response.json()
    statCards.value = [
      { label: '待检验设备', value: data['待检验设备'] ?? 0 },
      { label: '检验中设备', value: data['检验中设备'] ?? 0 },
      { label: '合格设备', value: data['合格设备'] ?? 0 },
      { label: '不合格设备', value: data['不合格设备'] ?? 0 },
    ]
  } catch {
    // 统计只是辅助信息，失败时保留旧值，不打断列表操作
  }
}

async function reload(targetPage = page.value) {
  errorMessage.value = ''
  noticeMessage.value = ''
  page.value = Math.min(Math.max(targetPage, 1), totalPages.value || 1)
  const params = new URLSearchParams({ page: String(page.value), size: String(PAGE_SIZE) })
  if (keyword.value.trim()) params.set('keyword', keyword.value.trim())
  if (statusFilter.value) params.set('status', statusFilter.value)
  try {
    const response = await request(`${ENDPOINT}?${params.toString()}`)
    if (!response.ok) throw new Error('检验任务列表读取失败')
    const payload = await response.json()
    rows.value = payload.items ?? []
    // 条数与总数都以服务端过滤后的口径为准，避免翻页后对不上
    total.value = payload.total ?? 0
    if (page.value > totalPages.value && page.value > 1) {
      await reload(totalPages.value)
      return
    }
    await loadStats()
  } catch (error) {
    flash(error instanceof Error ? error.message : '定期检验列表读取失败')
  }
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload(1)
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function openBatchDialog() {
  batchDialog.open = true
  batchDialog.error = ''
  batchDialog.plannedDate = ''
  candidateKeyword.value = ''
  selectedList.value = []
  await loadCandidates()
}

function closeBatchDialog() {
  batchDialog.open = false
}

async function loadCandidates() {
  batchDialog.error = ''
  try {
    const params = new URLSearchParams()
    if (candidateKeyword.value.trim()) params.set('keyword', candidateKeyword.value.trim())
    const response = await request(`${ENDPOINT}/candidates?${params.toString()}`)
    if (!response.ok) throw new Error('候选设备读取失败')
    const payload = await response.json()
    candidates.value = payload.items ?? []
    const validIds = new Set(candidates.value.map((item) => Number(item['设备登记id'])))
    selectedList.value = selectedList.value.filter((id) => validIds.has(id))
  } catch (error) {
    batchDialog.error = error instanceof Error ? error.message : '候选设备读取失败'
  }
}

function toggleAll(event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  selectedList.value = checked ? candidates.value.map((item) => Number(item['设备登记id'])) : []
}

async function submitBatch() {
  if (!selectedList.value.length) return
  batchDialog.submitting = true
  batchDialog.error = ''
  // batch_key 由前端按本次勾选生成；重复提交时后端只认第一次，不会重复建任务
  const batchKey = `${new Date().toISOString().slice(0, 10)}-${[...selectedList.value].sort((a, b) => a - b).join('_')}`
  try {
    const response = await request(`${ENDPOINT}/batch`, {
      method: 'POST',
      body: JSON.stringify({
        device_ids: selectedList.value,
        batch_key: batchKey,
        planned_date: batchDialog.plannedDate || null,
      }),
    })
    const data = await response.json()
    if (!response.ok || !data.ok) throw new Error(data.message || '批量安排失败')
    closeBatchDialog()
    flash(data.message || '本批检验已安排', true)
    await reload(1)
  } catch (error) {
    batchDialog.error = error instanceof Error ? error.message : '批量安排失败，请稍后重试'
  } finally {
    batchDialog.submitting = false
  }
}

async function retryBatch(batchNo: string) {
  try {
    const response = await request(`${ENDPOINT}/batch/${batchNo}/retry`, { method: 'POST', body: '{}' })
    const data = await response.json()
    if (!response.ok || !data.ok) throw new Error(data.message || '重试失败')
    flash(data.message, true)
    await reload()
  } catch (error) {
    flash(error instanceof Error ? error.message : '检验机构重试失败，可再次重试')
  }
}

function openConclusion(row: Row) {
  conclusionDialog.open = true
  conclusionDialog.row = row
  conclusionDialog.text = ''
  conclusionDialog.actualDate = ''
  conclusionDialog.error = ''
}

function closeConclusion() {
  conclusionDialog.open = false
  conclusionDialog.row = null
}

async function submitConclusion() {
  if (!conclusionDialog.row) return
  conclusionDialog.submitting = true
  conclusionDialog.error = ''
  try {
    const response = await request(`${ENDPOINT}/${conclusionDialog.row.id}/conclusion`, {
      method: 'POST',
      body: JSON.stringify({
        conclusion: conclusionDialog.text,
        actual_date: conclusionDialog.actualDate || null,
      }),
    })
    const data = await response.json()
    if (!response.ok || !data.ok) throw new Error(data.message || '结论录入失败')
    closeConclusion()
    flash(data.message, true)
    await reload()
  } catch (error) {
    conclusionDialog.error = error instanceof Error ? error.message : '结论录入失败'
  } finally {
    conclusionDialog.submitting = false
  }
}

async function runAction(action: string, row: Row) {
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const data = await response.json()
    if (!response.ok || !data.ok) throw new Error(data.message || '操作未生效')
    flash(data.message, true)
    await reload()
  } catch (error) {
    flash(error instanceof Error ? error.message : '定期检验操作失败')
  }
}

onMounted(() => {
  void reload(1)
})
</script>

<style scoped>
.page-actions { display: flex; gap: 8px; }
.muted-text { color: var(--muted); font-size: 12px; }
.ok-text { color: #067647; }
.pager { display: flex; align-items: center; gap: 8px; }
.pager .btn:disabled { opacity: 0.45; cursor: not-allowed; }
.link.danger { color: #b42318; }

.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(16, 24, 40, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}
.modal {
  background: #fff;
  border-radius: 10px;
  width: 820px;
  max-width: 94vw;
  max-height: 88vh;
  overflow: auto;
  padding: 18px 20px;
}
.modal.small { width: 520px; }
.modal h3 { margin: 0 0 6px; }
.modal-tip { color: var(--muted); font-size: 12px; margin: 0 0 12px; }
.modal-filter { display: flex; gap: 8px; margin-bottom: 10px; }
.modal-filter input { flex: 1; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.batch-meta { display: flex; align-items: flex-end; justify-content: space-between; margin-bottom: 10px; }
.batch-meta label span { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.batch-meta input { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.candidate-table .check-col { width: 36px; text-align: center; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
.device-summary { margin: 8px 0 12px; display: grid; grid-template-columns: 1fr 1fr; gap: 6px 16px; }
.device-summary div { display: flex; gap: 8px; font-size: 13px; }
.device-summary dt { color: var(--muted); margin: 0; }
.device-summary dd { margin: 0; }
.conclusion-field { display: block; margin-bottom: 10px; }
.conclusion-field span { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.conclusion-field textarea,
.conclusion-field input {
  width: 100%;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-family: inherit;
}
</style>
