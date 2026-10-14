<script setup lang="ts">
import { NButton, NFlex, NInput, NSelect, NText } from 'naive-ui'
import { computed, ref } from 'vue'

import type { OperationInput } from '../api'

const props = defineProps<{ opTypes: Record<string, string>; disabled: boolean }>()
const emit = defineEmits<{ submit: [operations: OperationInput[]] }>()

const options = computed(() => Object.entries(props.opTypes).map(([value, label]) => ({ value, label })))
const blank = (): OperationInput => ({ type: Object.keys(props.opTypes)[0] ?? '', description: '' })
const operations = ref([blank()])
const valid = computed(() => operations.value.every((op) => op.description.trim().length >= 3))

function submit() {
  emit('submit', operations.value)
  operations.value = [blank()]
}
</script>

<template>
  <NFlex vertical size="small">
    <NText depth="3">
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
      <NButton quaternary :disabled="operations.length === 1" @click="operations.splice(index, 1)">
        Удалить
      </NButton>
    </NFlex>
    <NFlex justify="space-between">
      <NButton @click="operations.push(blank())">Добавить операцию</NButton>
      <NButton type="primary" :disabled="disabled || !valid" @click="submit">
        Сгенерировать программу
      </NButton>
    </NFlex>
  </NFlex>
</template>
