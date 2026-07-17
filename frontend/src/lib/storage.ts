import { get, set } from 'idb-keyval'
import type { Session } from '../store/types'

const SESSIONS_KEY = 'rag_sessions_v1'
const SETTINGS_KEY = 'rag_settings_v1'

export type AppSettings = {
  model?: string
  corpus?: string
  retrievalMode?: 'fast' | 'deep'
  topK?: number
}

export async function loadSessions(): Promise<Session[]> {
  const data = await get(SESSIONS_KEY)
  if (!data) return []
  return data as Session[]
}

export async function saveSessions(sessions: Session[]) {
  await set(SESSIONS_KEY, sessions)
}

export async function loadSettings(): Promise<AppSettings> {
  const data = await get(SETTINGS_KEY)
  if (!data) return {}
  return data as AppSettings
}

export async function saveSettings(settings: AppSettings) {
  await set(SETTINGS_KEY, settings)
}
