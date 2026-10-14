<script setup lang="ts">
import { NAlert, NButton, NCard, NEmpty, NFlex, NH2, NSelect, NSpin, NText } from 'naive-ui'
import { computed, onMounted, ref } from 'vue'

import ProgramPanel from './components/ProgramPanel.vue'
import RequestForm from './components/RequestForm.vue'
import { useStore } from './store'

const store = useStore()

const STAGES: Record<string, string> = {
  queued: 'В очереди',
  searching: 'Поиск похожих операций',
  converting: 'Конвертация из другого диалекта',
  generating: 'Модель составляет программу',
  validating: 'Проверка валидатором',
  fixing: 'Модель исправляет ошибки',
}

const machineId = ref<number | null>(null)
const material = ref<string | null>(null)

const machineOptions = computed(() =>
  (store.refs?.machines ?? []).map((m) => ({ value: m.id, label: m.name })),
)
const materialOptions = computed(() =>
  Object.entries(store.refs?.materials ?? {}).map(([value, label]) => ({ value, label })),
)
const running = computed(() => !!store.run && !store.run.final)
const stageTitle = computed(() => {
  const run = store.run
  if (!run) return ''
  const title = STAGES[run.stage] ?? run.stage
  return run.attempt ? `${title}, попытка ${run.attempt}` : title
})

onMounted(async () => {
  await store.init()
  machineId.value = machineOptions.value[0]?.value ?? null
  material.value = materialOptions.value[0]?.value ?? null
})

function createChat() {
  if (machineId.value !== null && material.value) store.createChat(machineId.value, material.value)
}
</script>

<template>
  <div class="layout">
    <NFlex vertical class="column">
      <NH2 style="margin: 0">Помощник технолога</NH2>
      <NSelect v-model:value="machineId" :options="machineOptions" placeholder="Станок" />
      <NSelect v-model:value="material" :options="materialOptions" placeholder="Материал" />
      <NText depth="3">
        Материал определяет режимы резания (скорость и подачу), если они не указаны в описании
        операции, и приоритет примеров из базы знаний.
      </NText>
      <NButton type="primary" @click="createChat">Начать новый чат</NButton>
      <NFlex v-for="c in store.chats" :key="c.id" :wrap="false" align="center">
        <NButton
          text
          :type="c.id === store.chat?.id ? 'primary' : 'default'"
          style="flex: 1; white-space: normal; text-align: left"
          @click="store.openChat(c.id)"
        >
          {{ c.title }}
        </NButton>
        <NButton quaternary size="tiny" @click="store.deleteChat(c.id)">Удалить</NButton>
      </NFlex>
    </NFlex>

    <NFlex v-if="store.chat && store.refs" vertical class="column">
      <div>
        <b>{{ store.chat.machine.name }}</b><br />
        <NText depth="3">
          {{ store.chat.machine.dialect_title }} ·
          {{ store.refs.materials[store.chat.material] }} · до
          {{ store.chat.machine.max_spindle_rpm }} об/мин
        </NText>
      </div>

      <NCard v-for="m in store.chat.messages" :key="m.id" size="small" :embedded="m.role === 'user'">
        <template v-if="m.role === 'user'">
          <div v-for="(op, index) in m.operations" :key="index">
            {{ index + 1 }}. <b>{{ store.refs.op_types[op.type] ?? op.type }}.</b> {{ op.description }}
          </div>
        </template>
        <template v-else>
          {{ m.text || 'Программа сгенерирована.' }}
          <NButton
            v-if="m.program_id && m.program_id !== store.program?.id"
            text
            type="primary"
            @click="store.openProgram(m.program_id)"
          >
            Открыть программу
          </NButton>
        </template>
      </NCard>

      <NFlex v-if="running" align="center" role="status">
        <NSpin size="small" /> {{ stageTitle }} · {{ store.elapsed }} с
      </NFlex>
      <NAlert v-else-if="store.run?.stage === 'failed'" type="error" title="Генерация не удалась">
        {{ store.run.error }}
      </NAlert>

      <RequestForm :op-types="store.refs.op_types" :disabled="running" @submit="store.sendRequest" />
    </NFlex>
    <NEmpty v-else class="column" description="Выберите станок и материал и начните новый чат" />

    <ProgramPanel
      v-if="store.program && store.refs"
      class="column"
      :program="store.program"
      :op-types="store.refs.op_types"
      @save="store.saveProgram"
    />
    <NEmpty v-else class="column" description="Здесь появится программа" />

    <NAlert v-if="store.error" class="toast" type="error" closable @close="store.error = ''">
      {{ store.error }}
    </NAlert>
  </div>
</template>

<style>
body {
  margin: 0;
  font-family: system-ui, sans-serif;
  font-size: 14px;
}
.layout {
  display: grid;
  grid-template-columns: 280px 1fr 1fr;
  height: 100vh;
}
.column {
  padding: 16px;
  overflow-y: auto;
  border-right: 1px solid #e0e0e6;
}
.toast {
  position: fixed;
  right: 16px;
  bottom: 16px;
  width: 360px;
}
</style>
