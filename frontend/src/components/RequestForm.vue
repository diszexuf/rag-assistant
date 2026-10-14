<script setup lang="ts">
import { NButton, NFlex, NInput, NRadioButton, NRadioGroup, NSelect, NText } from 'naive-ui'
import { computed, ref } from 'vue'

import type { OperationInput, RequestBody } from '../api'

const props = defineProps<{
  opTypes: Record<string, string>
  disabled: boolean
  // Программа этого чата, которую можно изменить
  editableProgramId: number | null
}>()
const emit = defineEmits<{ submit: [body: RequestBody] }>()

const options = computed(() => Object.entries(props.opTypes).map(([value, label]) => ({ value, label })))
const blank = (): OperationInput => ({ type: Object.keys(props.opTypes)[0] ?? '', description: '' })

const mode = ref<'new' | 'edit'>('new')
const operations = ref<OperationInput[]>([blank()])
const instruction = ref('')

const editing = computed(() => mode.value === 'edit' && props.editableProgramId !== null)
const filled = computed(() => operations.value.filter((op) => op.description.trim().length > 0))
const valid = computed(() => {
  if (editing.value) {
    const operationsValid = filled.value.every((op) => op.description.trim().length >= 3)
    return operationsValid && (instruction.value.trim().length >= 3 || filled.value.length > 0)
  }
  return operations.value.length > 0 && operations.value.every((op) => op.description.trim().length >= 3)
})

function setMode(value: 'new' | 'edit') {
  mode.value = value
  // В режиме правки операции необязательны: добавляются только новые
  operations.value = value === 'edit' ? [] : [blank()]
}

function submit() {
  if (editing.value) {
    emit('submit', {
      operations: filled.value,
      base_program_id: props.editableProgramId ?? undefined,
      instruction: instruction.value.trim(),
    })
    instruction.value = ''
    operations.value = []
  } else {
    emit('submit', { operations: operations.value })
    operations.value = [blank()]
  }
}
</script>

<template>
  <NFlex vertical size="small">
    <NRadioGroup v-if="editableProgramId !== null" :value="mode" @update:value="setMode">
      <NRadioButton value="new">Новая программа</NRadioButton>
      <NRadioButton value="edit">Изменить текущую программу</NRadioButton>
    </NRadioGroup>

    <template v-if="editing">
      <NText depth="3">
        Опишите, что изменить в программе справа (в том числе в ваших ручных правках), и при
        необходимости добавьте новые операции.
      </NText>
      <NInput
        v-model:value="instruction"
        type="textarea"
        :rows="2"
        placeholder="Например: глубину сверления сделать 35 мм, подачу при точении 0.2 мм/об"
      />
      <NText depth="3" v-if="operations.length">Новые операции</NText>
    </template>
    <NText v-else depth="3">
      Перечислите операции по порядку. В описании укажите размеры, инструмент и режимы.
    </NText>

    <NFlex v-for="(op, index) in operations" :key="index" :wrap="false">
      <NSelect v-model:value="op.type" :options="options" style="width: 200px" />
      <NInput
        v-model:value="op.description"
        type="textarea"
        :rows="2"
        :placeholder="`Операция ${index + 1}: например, подрезка торца Ø60 мм резцом T0101, 120 м/мин`"
      />
      <NButton
        quaternary
        :disabled="!editing && operations.length === 1"
        @click="operations.splice(index, 1)"
      >
        Удалить
      </NButton>
    </NFlex>
    <NFlex justify="space-between">
      <NButton @click="operations.push(blank())">
        {{ editing ? 'Добавить новую операцию' : 'Добавить операцию' }}
      </NButton>
      <NButton type="primary" :disabled="disabled || !valid" @click="submit">
        {{ editing ? 'Изменить программу' : 'Сгенерировать программу' }}
      </NButton>
    </NFlex>
  </NFlex>
</template>
