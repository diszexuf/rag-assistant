export interface Tool {
  position: number
  name: string
  type: string
}

export interface Machine {
  id: number
  name: string
  dialect: string
  dialect_title: string
  max_spindle_rpm: number
  tools: Tool[]
}

export interface Refs {
  machines: Machine[]
  materials: Record<string, string>
  op_types: Record<string, string>
}

export interface ChatSummary {
  id: number
  title: string
  machine_id: number
  material: string
}

export interface OperationInput {
  type: string
  description: string
}

export interface Message {
  id: number
  role: 'user' | 'assistant'
  text: string
  operations: OperationInput[]
  // У запроса пользователя — программа, которую нужно изменить (режим правки)
  program_id: number | null
}

export interface RequestBody {
  operations: OperationInput[]
  base_program_id?: number
  instruction?: string
}

export interface RunState {
  run_id: number
  chat_id: number
  stage: string
  attempt: number
  error: string | null
  program_id: number | null
  final: boolean
}

export interface Chat extends ChatSummary {
  machine: Machine
  messages: Message[]
  active_run: RunState | null
  last_run: RunState | null
}

export interface Issue {
  line: number
  code: string
  severity: 'error' | 'warning'
  message: string
}

export interface Version {
  id: number
  author: 'system' | 'user' | 'edit'
  text: string
  report: { issues?: Issue[]; error_count?: number }
  created_at: string
}

export interface Source {
  index: number
  op_type: string
  description: string
  kind: 'kb' | 'converted' | 'unknown'
  program_title: string | null
  src_dialect: string | null
  distance: number | null
  // Оценка реранкера 0..1, если он использовался
  score: number | null
  note: string
}

export interface Program {
  id: number
  title: string
  status: string
  origin: 'kb' | 'converted' | 'unknown'
  dialect: string
  sources: Source[]
  versions: Version[]
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    const detail = typeof body?.detail === 'string' ? body.detail : `Ошибка ${response.status}`
    throw new Error(detail)
  }
  return response.status === 204 ? (undefined as T) : response.json()
}

const post = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) })

export const api = {
  refs: () => request<Refs>('/refs'),
  chats: () => request<ChatSummary[]>('/chats'),
  chat: (id: number) => request<Chat>(`/chats/${id}`),
  createChat: (machine_id: number, material: string) =>
    request<ChatSummary>('/chats', post({ machine_id, material })),
  deleteChat: (id: number) => request<void>(`/chats/${id}`, { method: 'DELETE' }),
  sendRequest: (chatId: number, body: RequestBody) =>
    request<RunState>(`/chats/${chatId}/requests`, post(body)),
  program: (id: number) => request<Program>(`/programs/${id}`),
  saveVersion: (programId: number, text: string) =>
    request<Version>(`/programs/${programId}/versions`, post({ text })),
}
