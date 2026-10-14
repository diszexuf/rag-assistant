<script setup lang="ts">
import { NAlert, NButton, NFlex, NH3, NInput, NSelect, NTag, NText } from 'naive-ui'
import { computed, ref, watch } from 'vue'

import type { Program } from '../api'

const props = defineProps<{ program: Program; opTypes: Record<string, string> }>()
const emit = defineEmits<{ save: [text: string] }>()

const ORIGINS = {
  kb: { title: 'На основе базы знаний', type: 'success' },
  converted: { title: 'С конвертацией из другого диалекта', type: 'warning' },
  unknown: { title: 'Есть операции без опоры на базу знаний', type: 'error' },
} as const
const KINDS = { kb: 'из базы знаний', converted: 'конвертирована', unknown: 'источника нет' }

const versionId = ref(0)
const text = ref('')

const versions = computed(() => props.program.versions)
const version = computed(() => versions.value.find((v) => v.id === versionId.value) ?? versions.value[0])
const issues = computed(() => version.value.report.issues ?? [])
const versionOptions = computed(() =>
  versions.value.map((v, index) => ({
    value: v.id,
    label: `${index + 1} — ${v.author === 'user' ? 'ручная правка' : 'генерация'}, ошибок: ${v.report.error_count ?? 0}`,
  })),
)

// При открытии программы и после сохранения показываем последнюю версию
watch(
  () => [props.program.id, versions.value.length],
  () => (versionId.value = versions.value[versions.value.length - 1].id),
  { immediate: true },
)
watch(version, (v) => (text.value = v.text), { immediate: true })
</script>

<template>
  <NFlex vertical>
    <NFlex align="center">
      <NH3 style="margin: 0">{{ program.title }}</NH3>
      <NTag :type="ORIGINS[program.origin].type" round>{{ ORIGINS[program.origin].title }}</NTag>
    </NFlex>
    <NAlert type="warning" :show-icon="false">
      Программа не проверена симуляцией и не подтверждена. Перед запуском на станке её должен
      проверить технолог.
    </NAlert>

    <NSelect v-model:value="versionId" :options="versionOptions" />
    <NInput
      v-model:value="text"
      type="textarea"
      :rows="18"
      :input-props="{ spellcheck: false }"
      style="font-family: Consolas, monospace"
    />
    <NButton type="primary" :disabled="text === version.text" @click="emit('save', text)">
      Сохранить изменения
    </NButton>

    <NH3 style="margin: 0">Отчёт валидатора</NH3>
    <NAlert v-if="!issues.length" type="success" :show-icon="false">Замечаний нет.</NAlert>
    <NAlert v-for="(issue, index) in issues" :key="index" :type="issue.severity" :show-icon="false">
      <template v-if="issue.line">Строка {{ issue.line }}. </template>{{ issue.message }}
    </NAlert>

    <NH3 style="margin: 0">Источники по операциям</NH3>
    <div v-for="source in program.sources" :key="source.index">
      {{ source.index }}. {{ opTypes[source.op_type] ?? source.op_type }} — {{ KINDS[source.kind] }}
      <template v-if="source.program_title">
        : «{{ source.program_title }}», расстояние {{ source.distance }}
      </template>
      <br />
      <NText v-if="source.note" depth="3">{{ source.note }}</NText>
    </div>
  </NFlex>
</template>
