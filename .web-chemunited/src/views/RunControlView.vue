<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { type NodeCard, type ProcessCard, type RunState, useRunStatusStore } from '../stores/runStatus'

interface ProtocolMeta {
  filename: string
  modified: string
  size_bytes: number
}

interface ProcessInfo {
  name: string
  description: string
}

const store = useRunStatusStore()

// Local only — not worth persisting
const protocols = ref<ProtocolMeta[]>([])
const streamStatus = ref<'closed' | 'opening' | 'open' | 'reconnecting'>('closed')
const runId = ref<string | null>(store.activeRunId)

// Drives the live wait-countdown display — ticks while any node has an
// active wait_seconds, updated on a shared interval rather than one timer
// per node.
const nowSeconds = ref(Date.now() / 1000)
let waitTicker: ReturnType<typeof window.setInterval> | null = null

let eventSource: EventSource | null = null

function remainingWaitSeconds(node: NodeCard): number {
  if (node.waitSeconds === null || node.waitStartedAt === null) return 0
  return Math.max(0, node.waitSeconds - (nowSeconds.value - node.waitStartedAt))
}

// Fraction of the wait still remaining (1 = just started, 0 = elapsed) —
// drives the depleting clock's conic-gradient fill.
function waitFraction(node: NodeCard): number {
  if (node.waitSeconds === null || node.waitSeconds <= 0) return 0
  return remainingWaitSeconds(node) / node.waitSeconds
}

function formatWaitTime(seconds: number): string {
  const whole = Math.ceil(seconds)
  if (whole < 60) return `${whole}s`
  const minutes = Math.floor(whole / 60)
  const secs = whole % 60
  return `${minutes}:${secs.toString().padStart(2, '0')}`
}

const isRunning = computed(() => store.runState === 'running')
const canStart = computed(() => Boolean(store.selectedProtocol) && !isRunning.value)

const stateText: Record<string, [string, string]> = {
  idle: ['Idle', 'Waiting for protocol dispatch.'],
  running: ['Running', 'Protocol is executing.'],
  finished: ['Finished', 'Protocol completed successfully.'],
  failed: ['Error', 'Protocol stopped with an error.'],
  cancelled: ['Cancelled', 'Protocol cancellation requested.'],
}

async function loadProtocols() {
  try {
    const res = await fetch('/protocols/')
    if (res.ok) protocols.value = await res.json() as ProtocolMeta[]
  } catch { /* network error — leave empty */ }
}

async function onProtocolChange() {
  store.processCards = []
  if (!store.selectedProtocol) return

  try {
    const [protocolRes, processesRes] = await Promise.all([
      fetch(`/protocols/${encodeURIComponent(store.selectedProtocol)}`),
      fetch('/processes/'),
    ])
    if (!protocolRes.ok || !processesRes.ok) return

    const protocolData = await protocolRes.json() as Record<string, unknown>
    const processes = await processesRes.json() as ProcessInfo[]
    const descMap = Object.fromEntries(processes.map(p => [p.name, p.description]))

    const cards: ProcessCard[] = []
    for (const stepKey of Object.keys(protocolData)) {
      if (stepKey === 'main_parameter') continue
      const baseName = stepKey.replace(/_\d+$/, '')
      const displayName = stepKey
        .replace(/_/g, ' ')
        .replace(/\b\w/g, c => c.toUpperCase())

      cards.push({
        stepKey,
        displayName,
        description: descMap[baseName] ?? '',
        status: 'waiting',
        hasFailed: false,
        errorMessage: '',
        nodes: [],
      })
    }
    store.processCards = cards
  } catch { /* ignore */ }
}

function closeStream({ markFailed = false }: { markFailed?: boolean } = {}) {
  if (eventSource) { eventSource.close(); eventSource = null }
  streamStatus.value = 'closed'
  if (markFailed) {
    for (const card of store.processCards) {
      if (card.status === 'running') card.status = 'failed'
    }
  }
}

function openStream() {
  if (eventSource) eventSource.close()
  streamStatus.value = 'opening'
  eventSource = new EventSource('/run/stream')

  eventSource.onopen = () => { streamStatus.value = 'open' }

  eventSource.onmessage = (e: MessageEvent) => {
    let data: Record<string, unknown>
    try { data = JSON.parse(e.data as string) } catch { return }

    // Stream-level error
    if ('error' in data && data.error) {
      store.setMessage(data.error as string, 'error')
      store.setRunState('failed', runId.value)
      closeStream({ markFailed: true })
      return
    }

    if ('event_type' in data) {
      const eventType = data.event_type as string
      const processKey = data.process as string | null
      if (processKey) {
        const card = store.processCards.find(c => c.stepKey === processKey)
        if (card) {
          if (eventType === 'EXECUTION_STARTED') {
            card.status = 'running'
          } else if (eventType === 'NODE_FAILED') {
            card.hasFailed = true
            // Capture the first error message for the card
            if (data.message && !card.errorMessage) {
              card.errorMessage = data.message as string
            }
          } else if (eventType === 'EXECUTION_FINISHED') {
            card.status = card.hasFailed ? 'failed' : 'completed'
          }

          // Per-node ("script block") progress — one row per nodeId, keyed
          // regardless of iteration. A late/out-of-order event for a lower
          // iteration (see 13e96b4) is ignored so it can't regress a row that
          // has already advanced.
          const nodeKey = data.node_key as [string, number] | null
          if (nodeKey) {
            const [nodeId, iteration] = nodeKey
            let node = card.nodes.find(n => n.nodeId === nodeId)
            if (!node) {
              node = {
                nodeId,
                iteration,
                method: '',
                state: 'WAITING',
                percentage: 0,
                message: '',
                waitSeconds: null,
                waitStartedAt: null,
              }
              card.nodes.push(node)
            }
            if (iteration >= node.iteration) {
              node.iteration = iteration
              if (data.state) node.state = data.state as NodeCard['state']
              if (data.method) node.method = data.method as string
              if (typeof data.percentage === 'number') node.percentage = data.percentage
              if (typeof data.message === 'string') node.message = data.message

              // A wait countdown is a fire-once hint: present only on the
              // event that reports it, anchored to that event's backend
              // timestamp. Any other event (new message, state change,
              // completion) omits it and clears the countdown.
              if (typeof data.wait_seconds === 'number' && data.wait_seconds > 0) {
                node.waitSeconds = data.wait_seconds
                node.waitStartedAt = typeof data.timestamp === 'number' ? data.timestamp : Date.now() / 1000
              } else {
                node.waitSeconds = null
                node.waitStartedAt = null
              }
            }
          }
        }
      }
    } else if ('state' in data) {
      const state = data.state as RunState
      store.setRunState(state, runId.value)
      closeStream({ markFailed: state === 'failed' || state === 'cancelled' })
      if (state === 'finished')  store.setMessage('Run finished. All processes completed.', 'success')
      else if (state === 'failed') store.setMessage('Run stopped with an error.', 'error')
      else if (state === 'cancelled') store.setMessage('Run cancelled.', '')
    }
  }

  eventSource.onerror = () => {
    if (eventSource?.readyState === EventSource.CLOSED) {
      closeStream()
    } else {
      streamStatus.value = 'reconnecting'
    }
  }
}

async function startRun() {
  if (!canStart.value) return

  store.setMessage('Dispatching protocol request…')
  try {
    const res = await fetch('/run/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        protocol: store.selectedProtocol,
        dry_run: store.formDryRun,
        timeout_commands: store.formTimeout.trim(),
        error_resilient: store.formErrorResilient,
      }),
    })

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText })) as { detail?: string }
      store.setMessage(`Failed to start run: ${err.detail ?? res.statusText}`, 'error')
      return
    }

    const data = await res.json() as { run_id: string; state: string }
    runId.value = data.run_id
    store.setRunState('running', data.run_id)

    for (const card of store.processCards) {
      card.status = 'waiting'
      card.hasFailed = false
      card.errorMessage = ''
      card.nodes = []
    }

    store.setMessage('Run accepted. Listening for live events.', 'info')
    openStream()
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'Unknown error'
    store.setMessage(`Network error: ${msg}`, 'error')
  }
}

async function cancelRun() {
  store.setMessage('Cancellation requested…')
  try {
    const res = await fetch('/run/', { method: 'DELETE' })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText })) as { detail?: string }
      store.setMessage(`Cancel failed: ${err.detail ?? res.statusText}`, 'error')
    }
  } catch {
    store.setMessage('Could not reach the run endpoint.', 'error')
  }
}

onMounted(async () => {
  waitTicker = window.setInterval(() => { nowSeconds.value = Date.now() / 1000 }, 250)

  await Promise.all([loadProtocols(), store.checkActiveRun()])

  if (store.runState === 'running' && store.activeRunId) {
    runId.value = store.activeRunId
    store.setMessage('Reconnected to active run. Listening for events.', 'info')
    openStream()
  } else if (store.runState !== 'idle') {
    runId.value = store.activeRunId
    // Fix cards stuck in 'running' if we navigated away while run completed
    const hasStaleRunning = store.processCards.some(c => c.status === 'running')
    if (hasStaleRunning) await store.restoreFromReport()
  }
})

onUnmounted(() => {
  closeStream()
  if (waitTicker !== null) { window.clearInterval(waitTicker); waitTicker = null }
})
</script>

<template>
  <div class="page-shell">
    <header class="page-header">
      <div>
        <p class="page-eyebrow">Protocol Execution</p>
        <h1 class="page-title">Run Control</h1>
        <p class="page-description">
          Select a protocol, configure execution parameters, and monitor workflow progress.
        </p>
      </div>
    </header>

    <div class="control-grid">
      <!-- Control Panel -->
      <section class="panel">
        <div class="panel-header">
          <div>
            <h2>Control Panel</h2>
            <p>Adjust execution parameters before dispatching the protocol.</p>
          </div>
        </div>

        <form class="control-form" @submit.prevent>
          <div class="field">
            <label for="protocol-select">Protocol</label>
            <select
              id="protocol-select"
              v-model="store.selectedProtocol"
              :disabled="isRunning || protocols.length === 0"
              @change="onProtocolChange"
            >
              <option value="" disabled>
                {{ protocols.length === 0 ? 'No protocols available' : 'Select a protocol' }}
              </option>
              <option v-for="p in protocols" :key="p.filename" :value="p.filename">
                {{ p.filename }}
              </option>
            </select>
            <span class="field-help">Protocol file from the project history folder.</span>
          </div>

          <div class="field">
            <label for="timeout-input">Command timeout</label>
            <input
              id="timeout-input"
              v-model="store.formTimeout"
              type="text"
              inputmode="text"
              placeholder="10 s"
              :disabled="isRunning"
            />
            <span class="field-help">
              Leave blank to wait indefinitely. Use values like <code>5 s</code>.
            </span>
          </div>

          <div class="toggle-list" aria-label="Execution options">
            <label class="toggle-row" for="dry-run-check">
              <input
                id="dry-run-check"
                v-model="store.formDryRun"
                type="checkbox"
                :disabled="isRunning"
              />
              <span>
                <span class="toggle-title">Dry run</span>
                <span class="field-help">
                  Validate workflow logic without calling physical devices.
                </span>
              </span>
            </label>
            <label class="toggle-row" for="error-resilient-check">
              <input
                id="error-resilient-check"
                v-model="store.formErrorResilient"
                type="checkbox"
                :disabled="isRunning"
              />
              <span>
                <span class="toggle-title">Error-resilient mode</span>
                <span class="field-help">
                  Log device errors and allow independent workflow branches to continue.
                </span>
              </span>
            </label>
          </div>

          <div class="control-actions">
            <button
              type="button"
              class="primary-action"
              :disabled="!canStart"
              @click="startRun"
            >
              {{ isRunning ? 'Running…' : 'Start Protocol' }}
            </button>
            <button
              v-if="isRunning"
              type="button"
              class="danger-action"
              @click="cancelRun"
            >
              Cancel Run
            </button>
          </div>

          <p
            v-if="store.formMessage"
            :class="['form-message', store.formMessageType]"
            role="status"
            aria-live="polite"
          >
            {{ store.formMessage }}
          </p>
        </form>
      </section>

      <!-- System State -->
      <section class="panel">
        <div class="panel-header">
          <div>
            <h2>System State</h2>
            <p>Idle, running, and error states update from the live execution stream.</p>
          </div>
          <span :class="['badge', store.runState]">
            {{ stateText[store.runState]?.[0] ?? store.runState }}
          </span>
        </div>

        <div class="state-readout" :data-state="store.runState">
          <span :class="['status-orb', store.runState]" aria-hidden="true" />
          <span>
            <span class="status-label">{{ stateText[store.runState]?.[0] ?? store.runState }}</span>
            <span class="status-caption">{{ stateText[store.runState]?.[1] ?? '' }}</span>
          </span>
        </div>

        <div class="run-meta">
          <div class="meta-row">
            <span class="meta-label">Run ID</span>
            <span class="meta-value">{{ runId ?? 'Not assigned' }}</span>
          </div>
          <div class="meta-row">
            <span class="meta-label">Stream</span>
            <span class="meta-value">{{ streamStatus }}</span>
          </div>
        </div>
      </section>
    </div>

    <!-- Process Cards -->
    <section v-if="store.processCards.length > 0" class="panel spaced-top">
      <div class="panel-header">
        <div>
          <h2>Process Execution</h2>
          <p>Live status for each process step in the selected protocol.</p>
        </div>
      </div>
      <div class="process-grid">
        <div
          v-for="card in store.processCards"
          :key="card.stepKey"
          :class="['process-card', card.status]"
        >
          <div class="process-card-header">
            <span :class="['process-status-dot', card.status]" aria-hidden="true" />
            <span class="process-name">{{ card.displayName }}</span>
            <span :class="['process-badge', card.status]">{{ card.status }}</span>
          </div>
          <p v-if="card.description" class="process-description">{{ card.description }}</p>
          <p v-if="card.status === 'failed' && card.errorMessage" class="process-error">
            {{ card.errorMessage }}
          </p>
          <div v-if="card.nodes.length" class="node-list">
            <div
              v-for="node in card.nodes"
              :key="node.nodeId"
              :class="['node-row', node.state.toLowerCase()]"
            >
              <div class="node-row-header">
                <span class="node-name">
                  {{ node.method || node.nodeId }}
                  <span v-if="node.iteration > 0" class="node-iteration-badge">
                    iteration {{ node.iteration }}
                  </span>
                </span>
                <span class="node-percentage">{{ node.percentage }}%</span>
              </div>
              <div class="progress-track">
                <div class="progress-fill" :style="{ width: node.percentage + '%' }" />
              </div>
              <div v-if="node.message || remainingWaitSeconds(node) > 0" class="node-status-row">
                <p v-if="node.message" class="node-message">{{ node.message }}</p>
                <span v-if="remainingWaitSeconds(node) > 0" class="node-wait">
                  <span
                    class="node-wait-clock"
                    :style="{ '--wait-frac': waitFraction(node) }"
                    aria-hidden="true"
                  />
                  <span class="node-wait-time">{{ formatWaitTime(remainingWaitSeconds(node)) }}</span>
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* ── Layout ─────────────────────────────────────────── */
.control-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.5rem;
  margin-bottom: 1.5rem;
}

.spaced-top {
  margin-top: 0;
}

/* ── Panel ──────────────────────────────────────────── */
.panel {
  padding: 1.5rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-background-soft);
  box-shadow: var(--shadow-sm);
}

.panel-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 1.25rem;
  padding-bottom: 1rem;
  border-bottom: 1px solid var(--color-border);
}

.panel-header h2 {
  margin-bottom: 0.2rem;
  font-size: 0.95rem;
  font-weight: 680;
  color: var(--color-heading);
}

.panel-header p {
  margin-bottom: 0;
  font-size: 0.82rem;
  color: var(--color-text-muted);
}

/* ── Run-level badge ────────────────────────────────── */
.badge {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  gap: 0.35rem;
  padding: 0.25rem 0.65rem;
  font-size: 0.72rem;
  font-weight: 720;
  letter-spacing: 0.04em;
  border-radius: 999px;
}

.badge::before {
  content: '';
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.badge.idle      { color: var(--color-text-muted);  background: var(--color-background-mute); }
.badge.running   { color: var(--color-primary);     background: var(--color-primary-soft); }
.badge.finished  { color: var(--color-success);     background: var(--color-success-soft); }
.badge.failed    { color: var(--color-danger);      background: var(--color-danger-soft); }
.badge.cancelled { color: var(--color-text-muted);  background: var(--color-background-mute); }

/* ── State readout ──────────────────────────────────── */
.state-readout {
  display: flex;
  align-items: center;
  gap: 0.85rem;
  padding: 0.9rem;
  margin-bottom: 1.25rem;
  border-radius: var(--radius-sm);
  background: var(--color-background-mute);
}

.status-orb {
  width: 12px;
  height: 12px;
  flex-shrink: 0;
  border-radius: 50%;
}

.status-orb.idle      { background: var(--color-text-muted); opacity: 0.55; }
.status-orb.running   { background: var(--color-primary); animation: orb-pulse 1.5s ease-in-out infinite; }
.status-orb.finished  { background: var(--color-success); }
.status-orb.failed    { background: var(--color-danger); }
.status-orb.cancelled { background: var(--color-text-muted); opacity: 0.55; }

.status-label {
  display: block;
  font-size: 0.87rem;
  font-weight: 680;
  color: var(--color-heading);
}

.status-caption {
  display: block;
  font-size: 0.79rem;
  color: var(--color-text-muted);
}

/* ── Run meta ───────────────────────────────────────── */
.run-meta {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.meta-row {
  display: flex;
  align-items: baseline;
  gap: 0.6rem;
  font-size: 0.82rem;
}

.meta-label {
  min-width: 64px;
  flex-shrink: 0;
  color: var(--color-text-muted);
  font-weight: 600;
}

.meta-value {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-heading);
  font-family: monospace;
  font-size: 0.78rem;
}

/* ── Form ───────────────────────────────────────────── */
.control-form {
  display: flex;
  flex-direction: column;
  gap: 1.1rem;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.field label {
  font-size: 0.82rem;
  font-weight: 650;
  color: var(--color-heading);
}

.field select,
.field input[type='text'] {
  width: 100%;
  padding: 0.5rem 0.7rem;
  font-size: 0.875rem;
  color: var(--color-text);
  background: var(--color-background);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  transition: border-color 150ms;
}

.field select:hover:not(:disabled),
.field input[type='text']:hover:not(:disabled) {
  border-color: var(--color-border-hover);
}

.field select:focus,
.field input[type='text']:focus {
  border-color: var(--color-primary);
  outline: none;
}

.field select:disabled,
.field input[type='text']:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.field-help {
  font-size: 0.77rem;
  color: var(--color-text-muted);
}

.field-help code {
  padding: 0.1em 0.3em;
  border-radius: 4px;
  background: var(--color-background-mute);
  font-family: monospace;
}

/* ── Toggle list ────────────────────────────────────── */
.toggle-list {
  display: flex;
  flex-direction: column;
  gap: 0.55rem;
}

.toggle-row {
  display: flex;
  align-items: flex-start;
  gap: 0.65rem;
  padding: 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition:
    border-color 150ms,
    background 150ms;
}

.toggle-row:hover {
  border-color: var(--color-border-hover);
  background: var(--color-background-mute);
}

.toggle-row input[type='checkbox'] {
  flex-shrink: 0;
  width: 15px;
  height: 15px;
  margin-top: 0.15rem;
  cursor: pointer;
  accent-color: var(--color-primary);
}

.toggle-row input[type='checkbox']:disabled {
  cursor: not-allowed;
}

.toggle-title {
  display: block;
  margin-bottom: 0.15rem;
  font-size: 0.84rem;
  font-weight: 640;
  color: var(--color-heading);
}

/* ── Control actions ────────────────────────────────── */
.control-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
}

.primary-action,
.danger-action {
  padding: 0.55rem 1.1rem;
  font-size: 0.84rem;
  font-weight: 650;
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition:
    background 150ms,
    opacity 150ms;
}

.primary-action {
  color: #ffffff;
  background: var(--color-primary);
}

.primary-action:hover:not(:disabled) {
  background: var(--color-primary-hover);
}

.primary-action:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.danger-action {
  color: var(--color-danger);
  background: var(--color-danger-soft);
  border: 1px solid color-mix(in srgb, var(--color-danger) 22%, transparent);
}

.danger-action:hover:not(:disabled) {
  background: color-mix(in srgb, var(--color-danger) 18%, transparent);
}

/* ── Form message ───────────────────────────────────── */
.form-message {
  padding: 0.55rem 0.8rem;
  margin: 0;
  font-size: 0.82rem;
  color: var(--color-text-muted);
  background: var(--color-background-mute);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
}

.form-message.error {
  color: var(--color-danger);
  background: var(--color-danger-soft);
  border-color: color-mix(in srgb, var(--color-danger) 24%, var(--color-border));
}

.form-message.success {
  color: var(--color-success);
  background: var(--color-success-soft);
  border-color: color-mix(in srgb, var(--color-success) 24%, var(--color-border));
}

.form-message.info {
  color: var(--color-primary);
  background: var(--color-primary-soft);
  border-color: color-mix(in srgb, var(--color-primary) 20%, var(--color-border));
}

/* ── Process grid ───────────────────────────────────── */
.process-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 1rem;
}

.process-card {
  padding: 1rem 1.1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-background-mute);
  transition: border-color 200ms;
}

.process-card.running {
  border-color: color-mix(in srgb, var(--color-primary) 32%, var(--color-border));
  background: color-mix(in srgb, var(--color-primary-soft) 55%, var(--color-background-soft));
}

.process-card.completed {
  border-color: color-mix(in srgb, var(--color-success) 30%, var(--color-border));
}

.process-card.failed {
  border-color: color-mix(in srgb, var(--color-danger) 30%, var(--color-border));
}

.process-card-header {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  margin-bottom: 0.5rem;
}

.process-status-dot {
  width: 8px;
  height: 8px;
  flex-shrink: 0;
  border-radius: 50%;
}

.process-status-dot.waiting   { background: var(--color-text-muted); opacity: 0.45; }
.process-status-dot.running   { background: var(--color-primary); animation: orb-pulse 1.5s ease-in-out infinite; }
.process-status-dot.completed { background: var(--color-success); }
.process-status-dot.failed    { background: var(--color-danger); }

.process-name {
  flex: 1;
  font-size: 0.87rem;
  font-weight: 680;
  color: var(--color-heading);
}

.process-badge {
  padding: 0.15rem 0.5rem;
  font-size: 0.68rem;
  font-weight: 720;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  border-radius: 999px;
}

.process-badge.waiting   { color: var(--color-text-muted);  background: var(--color-background-soft); }
.process-badge.running   { color: var(--color-primary);     background: var(--color-primary-soft); }
.process-badge.completed { color: var(--color-success);     background: var(--color-success-soft); }
.process-badge.failed    { color: var(--color-danger);      background: var(--color-danger-soft); }

.process-description {
  margin: 0;
  font-size: 0.79rem;
  line-height: 1.4;
  color: var(--color-text-muted);
}

.process-error {
  margin: 0.4rem 0 0;
  padding: 0.4rem 0.55rem;
  font-size: 0.77rem;
  color: var(--color-danger);
  background: var(--color-danger-soft);
  border-left: 2px solid var(--color-danger);
  border-radius: var(--radius-sm);
}

/* ── Node ("script block") list ────────────────────────── */
.node-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin-top: 0.6rem;
  padding-top: 0.6rem;
  border-top: 1px solid var(--color-border);
}

.node-row {
  padding: 0.4rem 0.55rem;
  border-radius: var(--radius-sm);
  background: var(--color-background-soft);
}

.node-row-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  margin-bottom: 0.3rem;
}

.node-name {
  font-size: 0.76rem;
  font-weight: 620;
  color: var(--color-heading);
}

.node-iteration-badge {
  margin-left: 0.4rem;
  font-size: 0.68rem;
  font-weight: 600;
  color: var(--color-text-muted);
}

.node-percentage {
  font-size: 0.72rem;
  font-weight: 620;
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.progress-track {
  width: 100%;
  height: 5px;
  border-radius: 999px;
  background: var(--color-border);
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  border-radius: 999px;
  background: var(--color-primary);
  transition: width 0.3s ease;
}

.node-row.completed .progress-fill { background: var(--color-success); }
.node-row.failed .progress-fill    { background: var(--color-danger); }
.node-row.waiting .progress-fill,
.node-row.inactive .progress-fill  { background: var(--color-text-muted); }

.node-row.completed .node-percentage { color: var(--color-success); }
.node-row.failed .node-percentage    { color: var(--color-danger); }

.node-status-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.5rem;
  margin-top: 0.3rem;
}

.node-message {
  margin: 0;
  font-size: 0.73rem;
  line-height: 1.35;
  color: var(--color-text-muted);
}

.node-row.failed .node-message {
  color: var(--color-danger);
}

/* ── Wait countdown ─────────────────────────────────── */
.node-wait {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 0.3rem;
  margin-left: auto;
}

.node-wait-clock {
  --wait-frac: 1;
  width: 11px;
  height: 11px;
  flex-shrink: 0;
  border-radius: 50%;
  background: conic-gradient(
    var(--color-primary) calc(var(--wait-frac) * 360deg),
    var(--color-border) 0
  );
  transition: background 0.2s linear;
}

.node-wait-time {
  font-size: 0.72rem;
  font-weight: 620;
  font-variant-numeric: tabular-nums;
  color: var(--color-primary);
  white-space: nowrap;
}

/* ── Animations ─────────────────────────────────────── */
@keyframes orb-pulse {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.35; }
}

/* ── Responsive ─────────────────────────────────────── */
@media (max-width: 840px) {
  .control-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 480px) {
  .process-grid {
    grid-template-columns: 1fr;
  }
}

@media (prefers-reduced-motion: reduce) {
  .status-orb.running,
  .process-status-dot.running {
    animation: none;
  }
}
</style>
