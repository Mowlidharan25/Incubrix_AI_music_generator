export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed'

export type AudioResult = {
  provider: string
  model: string
  format: string
  requested_duration_s: number
  requested_sample_rate: number
  target_integrated_lufs: number
  true_peak_ceiling_dbtp: number
  sample_rate: number | null
  channel_count: number | null
  duration_s: number
  size_bytes: number
  integrated_lufs: number | null
  true_peak_dbtp: number | null
  is_ai_generated: boolean
  download_url: string
  mix_ducked_download_url?: string | null
  manifest_download_url?: string | null
  loop_validation: {
    status: 'not_requested' | 'passed' | 'failed'
    crossfade_duration_s: number | null
    endpoint_discontinuity_dbfs: number | null
    note: string
  }
  stem_processing: StemProcessingResult
  generation_started_at: string
  generation_completed_at: string
  processing_duration_s: number
}

export type StemArtifact = {
  stem_id: string
  label: string
  prompt: string
  format: 'wav'
  duration_s: number
  sample_rate: number
  channel_count: number
  size_bytes: number
  file_name: string
  gain_linear: number
  gain_db: number
  download_url: string
}

export type StemProcessingResult = {
  status: 'not_requested' | 'available' | 'unavailable' | 'failed'
  error: { code: string; message: string; retryable: boolean; details: Record<string, unknown> } | null
  model_id: string | null
  model_version: string | null
  method: string | null
  stems: StemArtifact[]
  reconstruction_relative_error_db: number | null
  reconstruction_metric: string | null
  reconstruction_note: string | null
}

export type NarrationMixResult = {
  job_id: string
  format: string
  duration_s: number
  sample_rate: number
  channel_count: number
  size_bytes: number
  duck_attenuation_db: number
  integrated_lufs: number | null
  true_peak_dbtp: number | null
  download_url: string
  warnings: string[]
}

export type MusicJobRequest = {
  prompt: string
  duration_s: number
  sections: Array<{ start_s: number; mood: string }>
  tempo_bpm: number | null
  key: string | null
  loopable: boolean
  narration_url: string | null
  callback_url?: string | null
  separate_stems: boolean
  seed: number | null
  output: {
    sample_rate: number
    target_lufs: number
    formats: string[]
  }
}

export type JobStatusResponse = {
  job_id: string
  status: JobStatus
  progress: number
  created_at: string
  updated_at: string
  error: { code: string; message: string; retryable: boolean; details: Record<string, unknown> } | null
  result: AudioResult | null
  warnings: string[]
  prompt: string
  duration_s: number
}

export type ReadinessResponse = {
  status: 'ready' | 'not_ready'
  dependencies: {
    audio_generation: {
      status: 'configured' | 'not_configured'
      provider: string
      required_environment_variable: string
      credentials_configured: boolean
      provider_connection: 'not_checked'
    }
  }
}

export type HealthResponse = {
  status: 'ok'
}

type JobAcceptedResponse = Pick<JobStatusResponse, 'job_id' | 'status' | 'progress' | 'created_at' | 'updated_at'>
type ApiErrorResponse = {
  error?: {
    code?: string
    message?: string
    details?: { fields?: Array<{ field?: string; message?: string }> }
  }
}

const apiBaseUrl = (
  import.meta.env.PROD
    ? 'https://incubrix-ai-music-api.onrender.com'
    : import.meta.env.VITE_API_BASE_URL?.trim() || 'http://127.0.0.1:8000'
).replace(/\/+$/, '')
let csrfToken: string | null = null

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  const headers = new Headers(init?.headers)
  if (init?.method && !['GET', 'HEAD', 'OPTIONS'].includes(init.method.toUpperCase()) && csrfToken) {
    headers.set('X-CSRF-Token', csrfToken)
  }
  try {
    response = await fetch(`${apiBaseUrl}${path}`, {
      ...init,
      headers,
      credentials: 'include',
    })
  } catch {
    throw new Error(`Cannot reach the API at ${apiBaseUrl}. Check that the backend is running.`)
  }

  if (!response.ok) {
    const data = (await response.json().catch(() => ({}))) as ApiErrorResponse
    const fieldErrors = data.error?.details?.fields?.map((field) =>
      `${field.field ? `${field.field}: ` : ''}${field.message}`,
    )
    const message = fieldErrors?.length
      ? fieldErrors.join(' ')
      : data.error?.message || `The API returned HTTP ${response.status}.`
    throw new Error(`${data.error?.code ? `${data.error.code}: ` : ''}${message}`)
  }

  return (await response.json()) as T
}

export type AuthUser = { username: string }

async function fetchCsrfToken(): Promise<string> {
  const tokenResponse = await request<{ csrf_token: string }>('/api/v1/auth/csrf')
  csrfToken = tokenResponse.csrf_token
  return tokenResponse.csrf_token
}

export async function restoreSession(): Promise<AuthUser> {
  await fetchCsrfToken()
  return request<AuthUser>('/api/v1/auth/me')
}

export async function login(username: string, password: string): Promise<AuthUser> {
  const token = csrfToken ?? await fetchCsrfToken()
  const result = await request<AuthUser & { csrf_token: string }>('/api/v1/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token },
    body: JSON.stringify({ username, password }),
  })
  csrfToken = result.csrf_token
  return { username: result.username }
}

export async function logout(): Promise<void> {
  await request<{ status: string }>('/api/v1/auth/logout', { method: 'POST' })
  csrfToken = null
}

export function createMusicJob(payload: MusicJobRequest): Promise<JobAcceptedResponse> {
  return request<JobAcceptedResponse>('/api/v1/music/jobs', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': crypto.randomUUID(),
    },
    body: JSON.stringify(payload),
  })
}

export function getJobStatus(jobId: string, signal?: AbortSignal): Promise<JobStatusResponse> {
  return request<JobStatusResponse>(`/api/v1/jobs/${encodeURIComponent(jobId)}`, { signal })
}

export function getRecentJobs(limit = 50, signal?: AbortSignal): Promise<JobStatusResponse[]> {
  return request<JobStatusResponse[]>(`/api/v1/jobs?limit=${limit}`, { signal })
}

export function getJobManifest(jobId: string, signal?: AbortSignal): Promise<Record<string, unknown>> {
  return request<Record<string, unknown>>(`/api/v1/jobs/${encodeURIComponent(jobId)}/manifest`, { signal })
}

export function uploadNarrationMix(jobId: string, file: File): Promise<NarrationMixResult> {
  const extension = file.name.toLowerCase().split('.').pop()
  const contentType = extension === 'wav' ? 'audio/wav' : extension === 'mp3' ? 'audio/mpeg' : ''
  if (!contentType) {
    return Promise.reject(new Error('Choose a WAV or MP3 narration file.'))
  }
  return request<NarrationMixResult>(`/api/v1/jobs/${encodeURIComponent(jobId)}/narration`, {
    method: 'POST',
    headers: { 'Content-Type': contentType },
    body: file,
  })
}

export async function getNarrationMix(jobId: string): Promise<NarrationMixResult | null> {
  try {
    return await request<NarrationMixResult>(`/api/v1/jobs/${encodeURIComponent(jobId)}/narration`)
  } catch (error) {
    if (error instanceof Error && error.message.startsWith('NARRATION_MIX_NOT_FOUND:')) {
      return null
    }
    throw error
  }
}

export function getOpenApiSpec(signal?: AbortSignal): Promise<Record<string, unknown>> {
  return request<Record<string, unknown>>('/openapi.json', { signal })
}

export function getReadiness(signal?: AbortSignal): Promise<ReadinessResponse> {
  return request<ReadinessResponse>('/ready', { signal })
}

export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return request<HealthResponse>('/health', { signal })
}

export type ProviderConfig = {
  provider: 'stability' | 'elevenlabs' | 'mock'
  is_configured: boolean
  stability_credits: number | null
  model: string
  has_api_key: boolean
}

export function getProviderConfig(signal?: AbortSignal): Promise<ProviderConfig> {
  return request<ProviderConfig>('/api/v1/provider', { signal })
}

export function updateProviderConfig(
  provider: 'stability' | 'elevenlabs' | 'mock',
): Promise<ProviderConfig> {
  return request<ProviderConfig>('/api/v1/provider', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider }),
  })
}

export const musicApiBaseUrl = apiBaseUrl
