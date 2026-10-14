<script setup lang="ts">
import { NAlert, NButton, NCollapse, NCollapseItem, NFlex, NH3, NInput, NSelect, NTag, NText } from 'naive-ui'
import { computed, ref, watch } from 'vue'

import type { Program } from '../api'
import { collapseDiff, diffLines } from '../diff'

const props = defineProps<{ program: Program; opTypes: Record<string, string> }>()
const emit = defineEmits<{ save: [text: string]; draft: [text: string] }>()

const ORIGINS = {
  kb: { title: 'На основе базы знаний', type: 'success' },
  converted: { title: 'С конвертацией из другого диалекта', type: 'warning' },
  unknown: { title: 'Есть операции без опоры на базу знаний', type: 'error' },
} as const
const KINDS = { kb: 'из базы знаний', converted: 'конвертирована', unknown: 'источника нет' }
const AUTHORS = { system: 'генерация', user: 'ручная правка', edit: 'правка по запросу' }

const versionId = ref(0)
const text = ref('')

const versions = computed(() => props.program.versions)
const versionIndex = computed(() => {
  const index = versions.value.findIndex((v) => v.id === versionId.value)
  return index === -1 ? 0 : index
})
const version = computed(() => versions.value[versionIndex.value])
const issues = computed(() => version.value.report.issues ?? [])
const versionOptions = computed(() =>
  versions.value.map((v, index) => ({
    value: v.id,
    label: `${index + 1} — ${AUTHORS[v.author]}, ошибок: ${v.report.error_count ?? 0}`,
  })),
)
// Изменения относительно предыдущей версии
const diff = computed(() => {
  if (versionIndex.value === 0) return []
  return collapseDiff(diffLines(versions.value[versionIndex.value - 1].text, version.value.text))
})
const hasChanges = computed(() => diff.value.some((line) => line.kind !== 'same'))

// При открытии программы и после сохранения показываем последнюю версию
watch(
  () => [props.program.id, versions.value.length],
  () => (versionId.value = versions.value[versions.value.length - 1].id),
  { immediate: true },
)
watch(version, (v) => (text.value = v.text), { immediate: true })
// Текст редактора нужен форме запроса: правка идёт по тому, что видит технолог
watch(text, (value) => emit('draft', value), { immediate: true })
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

    <NCollapse v-if="versionIndex > 0">
      <NCollapseItem title="Изменения относительно предыдущей версии" name="diff">
        <NText v-if="!hasChanges" depth="3">Версии совпадают.</NText>
        <pre v-else class="diff"><span
          v-for="(line, index) in diff"
          :key="index"
          :class="`diff-line diff-${line.kind}`"
        >{{ line.kind === 'add' ? '+ ' : line.kind === 'del' ? '- ' : '  ' }}{{ line.text }}</span></pre>
      </NCollapseItem>
    </NCollapse>

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

<style scoped>
.diff {
  margin: 0;
  font-family: Consolas, monospace;
  font-size: 13px;
  white-space: pre-wrap;
}
.diff-line {
  display: block;
}
.diff-add {
  background: rgba(24, 160, 88, 0.18);
}
.diff-del {
  background: rgba(208, 48, 80, 0.18);
}
.diff-same {
  opacity: 0.7;
}
</style>
