import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api, type Chat, type ChatSummary, type Program, type Refs, type RequestBody, type RunState } from './api'

// Клиентский таймаут должен быть больше серверного RUN_TIMEOUT_S
const RUN_TIMEOUT_S = Number(import.meta.env.VITE_RUN_TIMEOUT_S ?? 300)

export const useStore = defineStore('main', () => {
  const refs = ref<Refs | null>(null)
  const chats = ref<ChatSummary[]>([])
  const chat = ref<Chat | null>(null)
  const program = ref<Program | null>(null)
  const run = ref<RunState | null>(null)
  // Текст в редакторе программы, включая несохранённые правки
  const draft = ref('')
  const elapsed = ref(0)
  const error = ref('')

  let stream: EventSource | null = null
  let ticker: number | undefined

  function stopWatching() {
    stream?.close()
    stream = null
    window.clearInterval(ticker)
  }

  async function guard<T>(action: () => Promise<T>): Promise<T | undefined> {
    try {
      error.value = ''
      return await action()
    } catch (e) {
      error.value = (e as Error).message
    }
  }

  async function init() {
    await guard(async () => {
      refs.value = await api.refs()
      chats.value = await api.chats()
      if (chats.value.length) await openChat(chats.value[0].id)
    })
  }

  async function openChat(id: number) {
    stopWatching()
    run.value = null
    program.value = null
    await guard(async () => {
      chat.value = await api.chat(id)
      const lastProgram = [...chat.value.messages].reverse().find((m) => m.program_id)
      if (lastProgram?.program_id) await openProgram(lastProgram.program_id)
      // После обновления страницы продолжаем ждать незавершённый запуск
      if (chat.value.active_run) watchRun(chat.value.active_run)
      else if (chat.value.last_run?.stage === 'failed') run.value = chat.value.last_run
    })
  }

  async function createChat(machineId: number, material: string) {
    await guard(async () => {
      const created = await api.createChat(machineId, material)
      chats.value = await api.chats()
      await openChat(created.id)
    })
  }

  async function deleteChat(id: number) {
    await guard(async () => {
      await api.deleteChat(id)
      chats.value = await api.chats()
      if (chat.value?.id === id) {
        chat.value = null
        program.value = null
        if (chats.value.length) await openChat(chats.value[0].id)
      }
    })
  }

  async function openProgram(id: number) {
    await guard(async () => {
      program.value = await api.program(id)
    })
  }

  async function sendRequest(body: RequestBody) {
    const chatId = chat.value?.id
    if (!chatId) return
    await guard(async () => {
      // Правка идёт по тому, что видит технолог: несохранённые изменения сохраняются версией
      const current = program.value
      if (body.base_program_id && current?.id === body.base_program_id) {
        const last = current.versions[current.versions.length - 1]
        if (draft.value && last && draft.value !== last.text) {
          await api.saveVersion(current.id, draft.value)
        }
      }
      const state = await api.sendRequest(chatId, body)
      chat.value = await api.chat(chatId)
      watchRun(state)
    })
  }

  function watchRun(state: RunState) {
    stopWatching()
    run.value = state
    elapsed.value = 0
    const started = Date.now()
    ticker = window.setInterval(() => {
      elapsed.value = Math.floor((Date.now() - started) / 1000)
      if (elapsed.value >= RUN_TIMEOUT_S) {
        stopWatching()
        run.value = {
          ...state,
          stage: 'failed',
          final: true,
          error: `Сервер не ответил за ${RUN_TIMEOUT_S} с. Обновите страницу, чтобы проверить состояние запуска.`,
        }
      }
    }, 1000)
    // При обрыве связи EventSource переподключается сам, сервер заново отдаёт текущее состояние
    stream = new EventSource(`/api/runs/${state.run_id}/events`)
    stream.onmessage = async (event) => {
      const next: RunState = JSON.parse(event.data)
      run.value = next
      if (!next.final) return
      stopWatching()
      if (chat.value?.id === next.chat_id) {
        chat.value = await api.chat(next.chat_id)
        if (next.program_id) await openProgram(next.program_id)
      }
    }
  }

  async function saveProgram(text: string) {
    const id = program.value?.id
    if (!id) return
    await guard(async () => {
      await api.saveVersion(id, text)
      await openProgram(id)
    })
  }

  return {
    refs, chats, chat, program, run, draft, elapsed, error,
    init, openChat, createChat, deleteChat, openProgram, sendRequest, saveProgram,
  }
})
