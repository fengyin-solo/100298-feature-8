<template>
  <section class="page" data-module="inspection">
    <header class="page-head">
      <div>
        <h2>定期检验管理</h2>
        <p class="page-desc">季度定期检验支持勾选同一批设备一次安排；逐台带出被检设备与检验类别，结论逐台录入，不合格单独退回整改。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" :disabled="!selectedIds.length" @click="openSchedule">
          批量安排检验{{ selectedIds.length ? `（已选 ${selectedIds.length} 台）` : '' }}
        </button>
        <button class="btn" type="button" :disabled="!selectedIds.length" @click="openConclusion">
          录入检验结论{{ selectedIds.length ? `（已选 ${selectedIds.length} 台）` : '' }}
        </button>
        <button class="btn ghost" type="button" @click="exportRows">导出检验清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload(1)">
      <label class="filter-item">
        <span>检验编号</span>
        <input v-model="filters.keyword" placeholder="按检验编号检索" />
      </label>
      <label class="filter-item">
        <span>检验状态</span>
        <select v-model="filters.status">
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
          <th class="check-col">
            <input
              type="checkbox"
              :checked="pageAllSelected"
              :indeterminate.prop="pageSomeSelected && !pageAllSelected"
              :disabled="!selectableRows.length"
              @change="togglePageSelection(($event.target as HTMLInputElement).checked)"
            />
          </th>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="{ 'row-selected': isSelected(Number(row.id)) }">
          <td class="check-col">
            <input
              v-if="canSchedule(row)"
              type="checkbox"
              :checked="isSelected(Number(row.id))"
              @change="toggleSelection(Number(row.id))"
            />
            <span v-else class="check-tip" :title="`${row.检验状态}设备不参与本批安排`">—</span>
          </td>
          <td v-for="column in columns" :key="column">{{ row[column] || '—' }}</td>
          <td class="row-actions">
            <button v-if="canSchedule(row)" class="link" type="button" @click="scheduleOne(row)">安排检验</button>
            <button v-if="row.status === '检验中'" class="link" type="button" @click="concludeOne(row)">录入结论</button>
            <button v-if="row.status === '检验中'" class="link danger" type="button" @click="rectifyOne(row)">下达整改</button>
            <span v-if="!canSchedule(row) && row.status !== '检验中'" class="muted-text">—</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无符合条件的检验任务</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条检验记录，当前第 {{ page }} 页 / 共 {{ totalPages }} 页</span>
      <span class="pager">
        <button class="btn" type="button" :disabled="page <= 1" @click="reload(page - 1)">上一页</button>
        <button class="btn" type="button" :disabled="page >= totalPages" @click="reload(page + 1)">下一页</button>
      </span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-if="noticeMessage" class="ok-text">{{ noticeMessage }}</span>
    </footer>

    <!-- 批量安排检验 -->
    <div v-if="scheduleModal.open" class="modal-mask" @click.self="scheduleModal.open = false">
      <div class="modal">
        <h3>批量安排检验（{{ scheduleModal.entries.length }} 台）</h3>
        <p class="modal-tip">同一批重复提交只认第一次；检验机构按检验类别逐台带出，取不到时可直接重试，不会写成空值。</p>
        <label class="form-line">
          <span>计划检验日</span>
          <input v-model="scheduleModal.planDate" type="date" />
        </label>
        <div class="modal-list">
          <div v-for="item in scheduleModal.entries" :key="String(item.id)" class="modal-row">
            <span class="modal-device">{{ item.被检设备 }}</span>
            <span class="tag">{{ item.检验类别 }}</span>
            <span v-if="item.检验机构" class="modal-agency">{{ item.检验机构 }}</span>
            <span v-else class="tag warning">检验机构待带出</span>
          </div>
        </div>
        <div v-if="scheduleModal.skipped.length" class="modal-skip">
          以下设备不排入本批：
          <span v-for="s in scheduleModal.skipped" :key="String(s.id)" class="skip-item">{{ s.被检设备 ?? `任务${s.id}` }}（{{ s.reason }}）</span>
        </div>
        <p v-if="scheduleModal.error" class="error-text">{{ scheduleModal.error }}</p>
        <div class="modal-actions">
          <button class="btn" type="button" @click="scheduleModal.open = false">关闭</button>
          <button class="btn primary" type="button" :disabled="scheduleModal.busy" @click="submitSchedule">
            {{ scheduleModal.busy ? '提交中…' : (scheduleModal.retryable ? '重试安排检验' : '确认安排检验') }}
          </button>
        </div>
      </div>
    </div>

    <!-- 逐台录入结论 -->
    <div v-if="conclusionModal.open" class="modal-mask" @click.self="conclusionModal.open = false">
      <div class="modal modal-wide">
        <h3>逐台录入检验结论（{{ conclusionModal.entries.length }} 台）</h3>
        <p class="modal-tip">结论按设备逐台填写、对号入座；含“不合格”的设备将单独退回整改，其余照常落合格结论。</p>
        <div class="modal-list">
          <div v-for="item in conclusionModal.entries" :key="String(item.id)" class="conclude-row">
            <div class="conclude-head">
              <span class="modal-device">{{ item.被检设备 }}</span>
              <span class="tag">{{ item.检验类别 }}</span>
              <span class="modal-agency">{{ item.检验机构 }}</span>
            </div>
            <div class="conclude-input">
              <label class="result-pick">
                <input v-model="item.result" type="radio" value="合格" /> 合格
              </label>
              <label class="result-pick">
                <input v-model="item.result" type="radio" value="不合格" /> 不合格（退回整改）
              </label>
              <input
                v-model="item.detail"
                class="detail-input"
                :placeholder="item.result === '不合格' ? '请填写不合格问题及整改要求' : '检验情况说明（可选）'"
              />
            </div>
          </div>
        </div>
        <p v-if="conclusionModal.error" class="error-text">{{ conclusionModal.error }}</p>
        <div class="modal-actions">
          <button class="btn" type="button" @click="conclusionModal.open = false">关闭</button>
          <button class="btn primary" type="button" :disabled="conclusionModal.busy" @click="submitConclusion">
            {{ conclusionModal.busy ? '提交中…' : '提交检验结论' }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/inspection'
const columns = ['检验编号', '被检设备', '检验类别', '检验机构', '计划检验日', '实际检验日', '检验结论', '检验状态']
const statuses = ['待检验', '检验中', '合格', '不合格']
const PAGE_SIZE = 10

const rows = ref<Row[]>([])
const total = ref(0)
const page = ref(1)
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
const errorMessage = ref('')
const noticeMessage = ref('')
const filters = reactive({ keyword: '', status: '' })
const stats = ref([
  { label: '待检验设备', value: 0 },
  { label: '检验中设备', value: 0 },
  { label: '不合格待整改', value: 0 },
])

// 跨页保留勾选；只收集可安排（待检验/不合格复检）的设备。
const selectedIds = ref<number[]>([])

const selectableRows = computed(() => rows.value.filter(canSchedule))
const pageAllSelected = computed(
  () => selectableRows.value.length > 0 && selectableRows.value.every((row) => isSelected(Number(row.id))),
)
const pageSomeSelected = computed(() => selectableRows.value.some((row) => isSelected(Number(row.id))))
const selectedRows = computed(() => rows.value.filter((row) => selectedIds.value.includes(Number(row.id))))

function canSchedule(row: Row): boolean {
  return row.status === '待检验' || row.status === '不合格'
}

function isSelected(id: number): boolean {
  return selectedIds.value.includes(id)
}

function toggleSelection(id: number) {
  if (isSelected(id)) {
    selectedIds.value = selectedIds.value.filter((item) => item !== id)
  } else {
    selectedIds.value = [...selectedIds.value, id]
  }
}

function togglePageSelection(checked: boolean) {
  const pageIds = selectableRows.value.map((row) => Number(row.id))
  selectedIds.value = checked
    ? Array.from(new Set([...selectedIds.value, ...pageIds]))
    : selectedIds.value.filter((id) => !pageIds.includes(id))
}

// ------------------------------------------------------------ 批量安排检验
const scheduleModal = reactive({
  open: false,
  busy: false,
  retryable: false,
  error: '',
  batchNo: '',
  planDate: new Date().toISOString().slice(0, 10),
  entries: [] as Row[],
  skipped: [] as Array<Record<string, string | number>>,
})

async function fetchRows(ids: number[]): Promise<Row[]> {
  // 勾选跨页保留，弹窗里要用完整明细，逐台拉取以保证与设备对得上。
  const settled = await Promise.allSettled(
    ids.map(async (id) => {
      const response = await request(`${ENDPOINT}/${id}`)
      if (!response.ok) {
        throw new Error(String(id))
      }
      return (await response.json()) as Row
    }),
  )
  return settled.flatMap((item) => (item.status === 'fulfilled' ? [item.value] : []))
}

async function openSchedule() {
  if (!selectedIds.value.length) {
    return
  }
  const detail = await fetchRows(selectedIds.value)
  const validIds = new Set(detail.map((row) => Number(row.id)))
  // 勾选后被归档/删除的任务直接清出选择
  selectedIds.value = selectedIds.value.filter((id) => validIds.has(id))
  scheduleModal.open = true
  scheduleModal.busy = false
  scheduleModal.retryable = false
  scheduleModal.error = ''
  // 每次新开一批给新批次号；同一批内重试沿用，保证“重复提交只认第一次”。
  scheduleModal.batchNo = `BATCH-${Date.now()}`
  scheduleModal.entries = detail.filter(canSchedule)
  scheduleModal.skipped = detail
    .filter((row) => !canSchedule(row))
    .map((row) => ({ id: Number(row.id), 被检设备: String(row.被检设备 ?? ''), reason: `当前为${row.status}，不再排入检验批次` }))
  if (!scheduleModal.entries.length) {
    scheduleModal.error = '勾选的设备均无需安排检验（合格设备不会重复入批）'
  }
}

async function submitSchedule() {
  scheduleModal.busy = true
  scheduleModal.error = ''
  try {
    const response = await request(`${ENDPOINT}/batch/schedule`, {
      method: 'POST',
      body: JSON.stringify({
        entry_ids: scheduleModal.entries.map((item) => Number(item.id)),
        batch_no: scheduleModal.batchNo,
        plan_date: scheduleModal.planDate,
      }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      // 机构取不到：保持勾选与弹窗，允许原样重试，绝不把机构写成空。
      scheduleModal.retryable = Boolean(payload.retryable)
      scheduleModal.error = payload.message || '安排检验未成功，请稍后重试'
      return
    }
    noticeMessage.value = payload.duplicated ? '该批次已提交过，已按第一次提交生效' : payload.message
    scheduleModal.open = false
    selectedIds.value = []
    await reload()
  } catch (error) {
    scheduleModal.retryable = true
    scheduleModal.error = error instanceof Error ? error.message : '安排检验失败，请重试'
  } finally {
    scheduleModal.busy = false
  }
}

async function scheduleOne(row: Row) {
  selectedIds.value = [Number(row.id)]
  openSchedule()
}

// ------------------------------------------------------------ 逐台录入结论
type ConclusionItem = Row & { result: string; detail: string }

const conclusionModal = reactive({
  open: false,
  busy: false,
  error: '',
  batchNo: '',
  entries: [] as ConclusionItem[],
})

function buildConclusionEntries(source: Row[]): ConclusionItem[] {
  return source
    .filter((row) => row.status === '检验中')
    .map((row) => ({ ...row, result: '合格', detail: '' }))
}

async function openConclusion() {
  const detail = await fetchRows(selectedIds.value)
  const items = buildConclusionEntries(detail)
  if (!items.length) {
    errorMessage.value = '勾选的设备里没有“检验中”的任务，无需录入结论'
    return
  }
  errorMessage.value = ''
  conclusionModal.open = true
  conclusionModal.busy = false
  conclusionModal.error = ''
  conclusionModal.batchNo = `RESULT-${Date.now()}`
  conclusionModal.entries = items
}

async function concludeOne(row: Row) {
  selectedIds.value = [Number(row.id)]
  const detail = await fetchRows([Number(row.id)])
  const items = buildConclusionEntries(detail)
  if (!items.length) {
    return
  }
  conclusionModal.open = true
  conclusionModal.busy = false
  conclusionModal.error = ''
  conclusionModal.batchNo = `RESULT-${Date.now()}`
  conclusionModal.entries = items
}

async function submitConclusion() {
  const missing = conclusionModal.entries.filter(
    (item) => item.result === '不合格' && !item.detail.trim(),
  )
  if (missing.length) {
    conclusionModal.error = `「${missing.map((item) => item.被检设备).join('、')}」判为不合格，请填写问题与整改要求`
    return
  }
  const conclusions: Record<string, string> = {}
  for (const item of conclusionModal.entries) {
    // 结论带设备与说明，后端按 id 对号入座，防止录串台。
    conclusions[String(item.id)] = item.detail.trim()
      ? `${item.result}：${item.detail.trim()}`
      : item.result
  }
  conclusionModal.busy = true
  conclusionModal.error = ''
  try {
    const response = await request(`${ENDPOINT}/batch/conclusions`, {
      method: 'POST',
      body: JSON.stringify({ conclusions, batch_no: conclusionModal.batchNo }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      conclusionModal.error = payload.message || '检验结论未生效，请稍后重试'
      return
    }
    noticeMessage.value = payload.message
    conclusionModal.open = false
    selectedIds.value = []
    await reload()
  } catch (error) {
    conclusionModal.error = error instanceof Error ? error.message : '检验结论提交失败'
  } finally {
    conclusionModal.busy = false
  }
}

async function rectifyOne(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action: '下达整改' } }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.message)
    }
    noticeMessage.value = payload.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '下达整改失败'
  }
}

// ---------------------------------------------------------------- 列表
function resetFilters() {
  filters.keyword = ''
  filters.status = ''
  void reload(1)
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function refreshStats() {
  try {
    const response = await request(`${ENDPOINT}/export`)
    if (!response.ok) {
      return
    }
    const payload = await response.json()
    const all: Row[] = payload.items ?? []
    const count = (status: string) => all.filter((row) => row.status === status).length
    stats.value[0].value = count('待检验')
    stats.value[1].value = count('检验中')
    stats.value[2].value = count('不合格')
  } catch {
    // 统计卡片不阻塞列表
  }
}

async function reload(targetPage?: number) {
  if (targetPage) {
    page.value = targetPage
  }
  errorMessage.value = ''
  noticeMessage.value = ''
  const query = new URLSearchParams({
    page: String(page.value),
    size: String(PAGE_SIZE),
  })
  if (filters.keyword.trim()) {
    query.set('keyword', filters.keyword.trim())
  }
  if (filters.status) {
    query.set('status', filters.status)
  }
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('检验任务列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    // total 以后端过滤后的全集为准，翻页后条数与总数自然对得上。
    total.value = payload.total ?? 0
    if (page.value > totalPages.value) {
      page.value = totalPages.value
    }
    // 清掉已经不在可安排状态（如刚录完合格结论）的跨页勾选
    selectedIds.value = selectedIds.value.filter((id) => {
      const row = rows.value.find((item) => Number(item.id) === id)
      return row ? canSchedule(row) : true
    })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '定期检验列表读取失败'
  }
  void refreshStats()
}

onMounted(() => reload(1))
</script>
