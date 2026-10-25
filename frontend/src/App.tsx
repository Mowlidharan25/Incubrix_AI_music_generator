import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  AudioLines,
  BookOpen,
  Clock3,
  Copy,
  Download,
  ExternalLink,
  FileAudio2,
  Headphones,
  KeyRound,
  Layers,
  Library,
  LoaderCircle,
  Menu,
  Music2,
  Pause,
  Play,
  RefreshCw,
  SlidersHorizontal,
  Sparkles,
  X,
  Zap,
  ChevronDown,
  CheckCircle2,
  AlertCircle,
} from 'lucide-react'
import {
  createMusicJob,
  getHealth,
  getJobManifest,
  getProviderConfig,
  getRecentJobs,
  login,
  logout,
  musicApiBaseUrl,
  restoreSession,
  updateProviderConfig,
  type JobStatusResponse,
  type MusicJobRequest,
  type NarrationMixResult,
  type ProviderConfig,
  uploadNarrationMix,
} from './api/client'
import './App.css'

type ViewId = 'studio' | 'jobs' | 'results' | 'narration' | 'api'
type BackendStatus = 'checking' | 'connected' | 'unavailable'

type StudioJob = JobStatusResponse & {
  request?: MusicJobRequest
}

type Section = MusicJobRequest['sections'][number]

interface Preset {
  name: string
  prompt: string
  duration: number
  tempo: number
  key: string
  sections: Section[]
}

const PRESETS: Preset[] = [
  {
    name: 'Cinematic Epic',
    prompt: 'Cinematic orchestral score with soaring brass, dramatic timpani, and sweeping strings, builds to an epic climax',
    duration: 45,
    tempo: 110,
    key: 'Dm',
    sections: [
      { start_s: 0, mood: 'quiet anticipation with cellos' },
      { start_s: 15, mood: 'horns and percussion enter building tension' },
      { start_s: 30, mood: 'full orchestra grand finale' },
    ],
  },
  {
    name: 'Lo-Fi Chill',
    prompt: 'Relaxing lo-fi hip hop beat with dusty vinyl crackle, gentle electric piano chords, and mellow bassline',
    duration: 30,
    tempo: 80,
    key: 'Eb',
    sections: [
      { start_s: 0, mood: 'warm dusty vinyl intro' },
      { start_s: 15, mood: 'mellow rhodes and soft beat' },
    ],
  },
  {
    name: 'Synthwave 80s',
    prompt: 'Energetic 80s synthwave retro electro with punchy synthesizer bass, gated reverb drums, and neon lead arp',
    duration: 40,
    tempo: 128,
    key: 'Am',
    sections: [
      { start_s: 0, mood: 'driving bassline pulse' },
      { start_s: 20, mood: 'soaring lead melody and synth chorus' },
    ],
  },
  {
    name: 'Corporate Explainer',
    prompt: 'Upbeat, clean corporate tech explainer music with acoustic guitar, bright piano, and crisp modern beat',
    duration: 45,
    tempo: 120,
    key: 'C',
    sections: [
      { start_s: 0, mood: 'clean acoustic strumming' },
      { start_s: 20, mood: 'inspiring piano lift and light beat' },
    ],
  },
  {
    name: 'Ambient Drone',
    prompt: 'Peaceful ambient soundscape with lush evolving warm pads, gentle singing bowls, and deep calm resonance',
    duration: 60,
    tempo: 70,
    key: 'F',
    sections: [
      { start_s: 0, mood: 'deep resonant calm' },
    ],
  },
  {
    name: 'Indie Rock',
    prompt: 'Driving indie rock instrumental with rhythmic electric rhythm guitar, punchy acoustic drums, and driving bassline',
    duration: 35,
    tempo: 135,
    key: 'E',
    sections: [
      { start_s: 0, mood: 'rhythm guitar intro' },
      { start_s: 15, mood: 'energetic drum build and lead riff' },
    ],
  },
]

const navItems: Array<{ id: ViewId; label: string; icon: typeof Music2 }> = [
  { id: 'studio', label: 'Music Studio', icon: AudioLines },
  { id: 'jobs', label: 'Generation History', icon: Clock3 },
  { id: 'results', label: 'Audio Results & Stems', icon: Library },
  { id: 'narration', label: 'Narration Mixer', icon: Headphones },
  { id: 'api', label: 'API Reference', icon: BookOpen },
]

const formatDuration = (seconds: number) => {
  const mins = Math.floor(seconds / 60)
  const secs = Math.round(seconds % 60)
  return `${mins}:${String(secs).padStart(2, '0')}`
}

const formatTimestamp = (timestamp: string) =>
  new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(timestamp))

const statusLabels: Record<JobStatusResponse['status'], string> = {
  queued: 'Queued',
  running: 'Processing',
  succeeded: 'Completed',
  failed: 'Failed',
}

export function App() {
  const [authenticated, setAuthenticated] = useState(false)
  const [authLoading, setAuthLoading] = useState(true)
  const [authUsername, setAuthUsername] = useState('')
  const [authPassword, setAuthPassword] = useState('')
  const [authError, setAuthError] = useState('')
  const [activeView, setActiveView] = useState<ViewId>('studio')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  
  // Generator & Provider State
  const [providerConfig, setProviderConfig] = useState<ProviderConfig | null>(null)
  const [providerError, setProviderError] = useState('')
  const [showKeyModal, setShowKeyModal] = useState(false)
  const [switchingProvider, setSwitchingProvider] = useState(false)

  // Creation State
  const [prompt, setPrompt] = useState('Warm optimistic acoustic pop for a product launch, builds to an uplifting finish')
  const [videoContext, setVideoContext] = useState('Product launch video showcasing modern creators making music with AI')
  const [moodUseCase, setMoodUseCase] = useState('uplifting product showcase')
  const [duration, setDuration] = useState(30)
  const [tempo, setTempo] = useState(120)
  const [musicKey, setMusicKey] = useState('C')
  const [loopable, setLoopable] = useState(false)
  const [separateStems, setSeparateStems] = useState(false)
  const [sections, setSections] = useState<Section[]>([
    { start_s: 0, mood: 'calm intro' },
    { start_s: 15, mood: 'uplifting build' },
  ])
  const [narrationUrl, setNarrationUrl] = useState('')
  const [callbackUrl, setCallbackUrl] = useState('')

  // Narration mixer state
  const [narrationFile, setNarrationFile] = useState<File | null>(null)
  const [narrationPreviewUrl, setNarrationPreviewUrl] = useState('')
  const [narrationJobId, setNarrationJobId] = useState('')
  const [narrationMix, setNarrationMix] = useState<NarrationMixResult | null>(null)
  const [mixingNarration, setMixingNarration] = useState(false)
  const [narrationError, setNarrationError] = useState('')

  // System & Jobs State
  const [backendStatus, setBackendStatus] = useState<BackendStatus>('checking')
  const [jobs, setJobs] = useState<StudioJob[]>([])
  const [jobsLoading, setJobsLoading] = useState(true)
  const [refreshingJobs, setRefreshingJobs] = useState(false)
  const [expandedPrompts, setExpandedPrompts] = useState<Set<string>>(() => new Set())
  const [toast, setToast] = useState<{ id: number; kind: 'success' | 'error'; message: string } | null>(null)
  const [selectedJob, setSelectedJob] = useState<StudioJob | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState('')
  const [jobsError, setJobsError] = useState('')
  const [manifestJob, setManifestJob] = useState<string | null>(null)
  const [manifest, setManifest] = useState<Record<string, unknown> | null>(null)
  const [manifestLoading, setManifestLoading] = useState(false)
  const [copyNotice, setCopyNotice] = useState(false)

  // Audio Playback State
  const [playingJobId, setPlayingJobId] = useState<string | null>(null)
  const [playbackProgress, setPlaybackProgress] = useState(0)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const narrationPreviewRef = useRef<string | null>(null)
  const priorJobStatuses = useRef(new Map<string, JobStatusResponse['status']>())

  const notify = useCallback((kind: 'success' | 'error', message: string) => {
    setToast({ id: Date.now(), kind, message })
  }, [])

  useEffect(() => {
    let active = true
    void restoreSession()
      .then((user) => {
        if (active) {
          setAuthUsername(user.username)
          setAuthenticated(true)
        }
      })
      .catch(() => {
        if (active) setAuthenticated(false)
      })
      .finally(() => {
        if (active) setAuthLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  const handleLogin = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setAuthError('')
    try {
      const user = await login(authUsername, authPassword)
      setAuthUsername(user.username)
      setAuthPassword('')
      setAuthenticated(true)
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : 'Sign-in failed.')
    }
  }

  const handleLogout = async () => {
    try {
      await logout()
      setAuthenticated(false)
      setJobs([])
      setProviderConfig(null)
    } catch (err) {
      notify('error', err instanceof Error ? err.message : 'Sign-out failed.')
    }
  }

  // Refresh Provider Config
  const refreshProvider = useCallback(async () => {
    if (!authenticated) return
    try {
      const config = await getProviderConfig()
      setProviderConfig(config)
      setProviderError('')
    } catch (err) {
      setProviderError(err instanceof Error ? err.message : 'Provider status could not be loaded.')
    }
  }, [authenticated])

  // Refresh Jobs & Health
  const refreshJobs = useCallback(async (manual = false) => {
    if (!authenticated) {
      setJobsLoading(false)
      return
    }
    if (manual) setRefreshingJobs(true)
    try {
      const data = await getRecentJobs(50)
      const previous = priorJobStatuses.current
      for (const job of data) {
        const oldStatus = previous.get(job.job_id)
        if (oldStatus && (oldStatus === 'queued' || oldStatus === 'running') &&
          (job.status === 'succeeded' || job.status === 'failed')) {
          notify(
            job.status === 'succeeded' ? 'success' : 'error',
            job.status === 'succeeded'
              ? 'Your track is ready to preview and download.'
              : `${job.error?.code ?? 'Generation failed'}: ${job.error?.message ?? 'The job could not be completed.'}`,
          )
        }
        previous.set(job.job_id, job.status)
      }
      setJobs(data)
      setJobsError('')
      setSelectedJob((current) => data.find((job) => job.job_id === current?.job_id) ?? data[0] ?? null)
    } catch (err) {
      setJobsError(err instanceof Error ? err.message : 'Generation history could not be loaded.')
    } finally {
      setJobsLoading(false)
      if (manual) setRefreshingJobs(false)
    }
  }, [authenticated, notify])

  const checkConnection = useCallback(async () => {
    try {
      await getHealth()
      setBackendStatus('connected')
    } catch {
      setBackendStatus('unavailable')
    }
  }, [])

  useEffect(() => {
    const initialCheck = window.setTimeout(() => {
      void checkConnection()
    }, 0)
    const timer = window.setInterval(() => {
      void checkConnection()
    }, 15000)
    return () => {
      window.clearTimeout(initialCheck)
      window.clearInterval(timer)
    }
  }, [checkConnection])

  useEffect(() => {
    const initialRefresh = window.setTimeout(() => {
      void refreshProvider()
    }, 0)
    const timer = window.setInterval(() => {
      void refreshProvider()
    }, 60000)
    return () => {
      window.clearTimeout(initialRefresh)
      window.clearInterval(timer)
    }
  }, [refreshProvider])

  useEffect(() => {
    const initialRefresh = window.setTimeout(() => {
      void refreshJobs()
    }, 0)
    const timer = window.setInterval(() => {
      void refreshJobs()
    }, 5000)
    return () => {
      window.clearTimeout(initialRefresh)
      window.clearInterval(timer)
    }
  }, [refreshJobs])

  // Handle Provider Switch
  const handleToggleProvider = async (target: 'stability' | 'mock') => {
    setSwitchingProvider(true)
    setProviderError('')
    try {
      const updated = await updateProviderConfig(target)
      setProviderConfig(updated)
      notify(
        'success',
        target === 'mock'
          ? 'Mock Preview is ready for workflow testing.'
          : updated.is_configured
            ? 'Switched to the configured Stability provider.'
            : 'Stability selected, but no API key is configured on the backend.',
      )
    } catch (err) {
      setProviderError(err instanceof Error ? err.message : 'Failed to switch provider.')
    } finally {
      setSwitchingProvider(false)
    }
  }

  // Preset Selection
  const applyPreset = (preset: Preset) => {
    setPrompt(preset.prompt)
    setDuration(preset.duration)
    setTempo(preset.tempo)
    setMusicKey(preset.key)
    setSections(preset.sections)
  }

  // Generation submission
  const handleGenerate = async () => {
    if (!prompt.trim()) {
      setFormError('Add a musical prompt before generating a track.')
      return
    }
    if (!providerConfig?.is_configured) {
      setFormError('Configure a music provider before generating a track.')
      return
    }
    if (providerConfig.provider === 'stability' && providerConfig.stability_credits !== null && providerConfig.stability_credits <= 0) {
      setFormError('This provider account has no available credits. Add credits or switch to Mock Preview to test the workflow.')
      return
    }
    if (sections.some((section) =>
      !Number.isFinite(section.start_s) || section.start_s < 0 || section.start_s >= duration || !section.mood.trim(),
    )) {
      setFormError('Each arrangement point needs a mood and a time between 0 and the track duration.')
      return
    }
    if (sections.some((section, index) => index > 0 && section.start_s < sections[index - 1].start_s)) {
      setFormError('Arrange mood changes in increasing time order before generating.')
      return
    }
    const fullPrompt = [
      videoContext.trim() ? `Video: ${videoContext.trim()}.` : '',
      moodUseCase.trim() ? `Mood: ${moodUseCase.trim()}.` : '',
      prompt.trim(),
    ].filter(Boolean).join(' ')
    if (fullPrompt.length > 2000) {
      setFormError('The combined music direction must be 2,000 characters or fewer.')
      return
    }

    setSubmitting(true)
    setFormError('')
    const payload: MusicJobRequest = {
      prompt: fullPrompt,
      duration_s: duration,
      sections,
      tempo_bpm: tempo || null,
      key: musicKey || null,
      loopable,
      narration_url: narrationUrl.trim() || null,
      callback_url: callbackUrl.trim() || null,
      separate_stems: separateStems,
      seed: null,
      output: {
        sample_rate: 48000,
        target_lufs: -16,
        formats: ['wav'],
      },
    }

    try {
      const accepted = await createMusicJob(payload)
      const initial: StudioJob = {
        job_id: accepted.job_id,
        status: accepted.status,
        progress: accepted.progress,
        created_at: accepted.created_at,
        updated_at: accepted.updated_at,
        error: null,
        result: null,
        warnings: [],
        prompt: payload.prompt,
        duration_s: payload.duration_s,
        request: payload,
      }
      setJobs((prev) => [initial, ...prev])
      priorJobStatuses.current.set(initial.job_id, initial.status)
      setSelectedJob(initial)
      setActiveView('jobs')
      notify('success', 'Your generation job has been queued.')
      void refreshJobs()
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Job submission failed.'
      setFormError(message)
      notify('error', message)
    } finally {
      setSubmitting(false)
    }
  }

  // Audio Playback Controller
  const togglePlayAudio = (jobId: string, url: string) => {
    if (playingJobId === jobId) {
      audioRef.current?.pause()
      setPlayingJobId(null)
      return
    }

    if (audioRef.current) {
      audioRef.current.pause()
    }

    const fullUrl = url.startsWith('http') ? url : `${musicApiBaseUrl}${url}`
    const audio = new Audio(fullUrl)
    audio.crossOrigin = 'use-credentials'
    audioRef.current = audio

    audio.ontimeupdate = () => {
      if (audio.duration) {
        setPlaybackProgress((audio.currentTime / audio.duration) * 100)
      }
    }
    audio.onended = () => {
      setPlayingJobId(null)
      setPlaybackProgress(0)
    }
    audio.onerror = () => {
      setPlayingJobId(null)
      notify('error', 'Audio preview could not be loaded. Check that the backend audio file is still available.')
    }

    audio.play().then(() => {
      setPlayingJobId(jobId)
    }).catch((err) => {
      setPlayingJobId(null)
      notify('error', err instanceof Error ? `Audio playback failed: ${err.message}` : 'Audio playback could not start.')
    })
  }

  // Open Manifest Viewer
  const openManifestViewer = async (jobId: string) => {
    setManifestJob(jobId)
    setManifest(null)
    setManifestLoading(true)
    try {
      const data = await getJobManifest(jobId)
      setManifest(data)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Manifest could not be loaded.'
      setManifest({ error: message })
      notify('error', message)
    } finally {
      setManifestLoading(false)
    }
  }

  const copyManifest = async () => {
    if (manifest) {
      try {
        await navigator.clipboard.writeText(JSON.stringify(manifest, null, 2))
        setCopyNotice(true)
        notify('success', 'Manifest copied to clipboard.')
        setTimeout(() => setCopyNotice(false), 2000)
      } catch (err) {
        notify('error', err instanceof Error ? `Clipboard copy failed: ${err.message}` : 'Clipboard access is unavailable.')
      }
    }
  }

  // Narration Upload
  const handleUploadNarration = async () => {
    setNarrationError('')
    if (!narrationFile) {
      setNarrationError('Choose a WAV or MP3 voiceover file first.')
      return
    }
    if (narrationFile.size > 50 * 1024 * 1024) {
      setNarrationError('The voiceover file must be 50 MB or smaller.')
      return
    }
    const targetId = narrationJobId || (selectedJob?.status === 'succeeded' ? selectedJob.job_id : '')
    if (!targetId) {
      setNarrationError('Choose a successfully generated background track first.')
      return
    }

    setMixingNarration(true)
    try {
      const res = await uploadNarrationMix(targetId, narrationFile)
      setNarrationMix(res)
      notify('success', 'Voiceover mix completed and is ready to download.')
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Narration mixing failed.'
      setNarrationError(message)
      notify('error', message)
    } finally {
      setMixingNarration(false)
    }
  }

  const isRealAi = providerConfig?.provider === 'stability' && providerConfig.is_configured
  const isMockPreview = providerConfig?.provider === 'mock'
  const providerHasNoCredits = isRealAi && providerConfig.stability_credits !== null &&
    providerConfig.stability_credits !== undefined && providerConfig.stability_credits <= 0
  const providerStatusLabel = backendStatus !== 'connected'
    ? backendStatus === 'checking' ? 'Connecting...' : 'API Offline'
    : !providerConfig?.is_configured
      ? 'Provider Setup Needed'
      : providerConfig.provider === 'mock'
        ? 'Mock Preview'
        : providerConfig.provider === 'stability'
          ? providerConfig.stability_credits == null
            ? 'Stability configured · credits unverified'
            : providerHasNoCredits
              ? 'Stability credits depleted'
              : 'Stability credits available'
          : 'Provider configured · availability unverified'
  const completedJobs = jobs.filter((job) => job.status === 'succeeded' && job.result)
  const resultJob = (selectedJob?.status === 'succeeded' && selectedJob.result
    ? selectedJob
    : completedJobs[0]) ?? null
  const narrationMusicJob = completedJobs.find((job) => job.job_id === narrationJobId) ??
    (selectedJob?.status === 'succeeded' ? selectedJob : null)

  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(null), 5000)
    return () => window.clearTimeout(timer)
  }, [toast])

  useEffect(() => () => audioRef.current?.pause(), [])
  useEffect(() => () => {
    if (narrationPreviewRef.current) URL.revokeObjectURL(narrationPreviewRef.current)
  }, [])

  const handleNarrationFileChange = (file: File | null) => {
    if (narrationPreviewRef.current) URL.revokeObjectURL(narrationPreviewRef.current)
    narrationPreviewRef.current = file ? URL.createObjectURL(file) : null
    setNarrationPreviewUrl(narrationPreviewRef.current ?? '')
    setNarrationFile(file)
    setNarrationMix(null)
    setNarrationError('')
  }

  const togglePromptExpansion = (jobId: string) => {
    setExpandedPrompts((current) => {
      const next = new Set(current)
      if (next.has(jobId)) next.delete(jobId)
      else next.add(jobId)
      return next
    })
  }

  if (authLoading) {
    return <main className="auth-screen" aria-live="polite">Checking secure session…</main>
  }

  if (!authenticated) {
    return (
      <main className="auth-screen">
        <form className="auth-card" onSubmit={handleLogin}>
          <div className="brand-mark"><Sparkles size={20} /></div>
          <p className="field-label">PRIVATE WORKSPACE</p>
          <h1>Sign in to IncuBrix</h1>
          <p>Your generation history and audio files are private to this account.</p>
          <label>
            Username
            <input
              autoComplete="username"
              value={authUsername}
              onChange={(event) => setAuthUsername(event.target.value)}
              required
              maxLength={64}
            />
          </label>
          <label>
            Password
            <input
              type="password"
              autoComplete="current-password"
              value={authPassword}
              onChange={(event) => setAuthPassword(event.target.value)}
              required
              maxLength={1024}
            />
          </label>
          {authError && <p className="auth-error" role="alert">{authError}</p>}
          <button className="btn-primary" type="submit">Sign in</button>
        </form>
      </main>
    )
  }

  return (
    <div
      className={`studio-shell ${sidebarCollapsed ? 'sidebar-is-collapsed' : ''} ${mobileMenuOpen ? 'mobile-menu-open' : ''}`}
      onPointerMove={(event) => {
        event.currentTarget.style.setProperty('--pointer-x', `${event.clientX}px`)
        event.currentTarget.style.setProperty('--pointer-y', `${event.clientY}px`)
      }}
    >
      <div className="ambient-cursor" aria-hidden="true" />
      {mobileMenuOpen && (
        <button
          type="button"
          className="mobile-nav-scrim"
          aria-label="Close navigation menu"
          onClick={() => setMobileMenuOpen(false)}
        />
      )}
      {/* SIDEBAR NAVIGATION */}
      <aside className="sidebar">
        <div className="brand-lockup">
          <div className="brand-mark">
            <Sparkles size={20} />
          </div>
          <div className="brand-wordmark">
            <div className="brand-title">IncuBrix</div>
            <div className="brand-sub">AI Music Engine</div>
          </div>
          <button
            className="sidebar-collapse"
            type="button"
            aria-label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
            title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            <Menu size={16} />
          </button>
        </div>

        <div className="sidebar-section-label">{sidebarCollapsed ? 'NAV' : 'STUDIO WORKSPACE'}</div>

        <nav className="primary-nav">
          {navItems.map((item) => {
            const Icon = item.icon
            const isActive = activeView === item.id
            return (
              <button
                key={item.id}
                className={`nav-item ${isActive ? 'active' : ''}`}
                onClick={() => {
                  setActiveView(item.id)
                  setMobileMenuOpen(false)
                }}
                aria-label={item.label}
                aria-current={isActive ? 'page' : undefined}
                title={sidebarCollapsed ? item.label : undefined}
              >
                <Icon size={18} />
                <span className="nav-label">{item.label}</span>
                {item.id === 'jobs' && jobs.some((j) => j.status === 'running') && (
                  <span className="nav-live-dot" title="Job in progress" />
                )}
              </button>
            )
          })}
        </nav>

        <div className="sidebar-bottom">
          {(!sidebarCollapsed || mobileMenuOpen) && (
            <div className="sidebar-provider-card">
              <div className="provider-header">
                <span className="field-label" style={{ margin: 0 }}>ACTIVE ENGINE</span>
                {isRealAi ? (
                  <span className="provider-badge-real"><Zap size={10} /> REAL AI</span>
                ) : providerConfig?.provider === 'mock' ? (
                  <span className="provider-badge-mock">MOCK PREVIEW</span>
                ) : (
                  <span className="provider-badge-mock">NOT CONFIGURED</span>
                )}
              </div>
              <div style={{ fontSize: '11px', color: '#94a3b8', margin: '6px 0 10px' }}>
                {isRealAi ? 'Stable Audio 3.0 · 48 kHz stereo' : providerConfig?.provider === 'mock' ? 'Synthetic test audio · not a music model' : 'Choose a provider to get started'}
              </div>
              {isRealAi && providerConfig?.stability_credits !== null && providerConfig?.stability_credits !== undefined && (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <span style={{ fontSize: '11px', color: '#64748b' }}>Stability credits:</span>
                  <span className="credit-pill">
                    {providerConfig.stability_credits <= 0 ? 'Depleted' : `${providerConfig.stability_credits.toLocaleString()} available`}
                  </span>
                </div>
              )}
            </div>
          )}
        </div>
      </aside>

      {/* MAIN STUDIO VIEWPORT */}
      <main className="app-main">
        {/* TOPBAR */}
        <header className="topbar">
          <div className="topbar-left">
            <button
              type="button"
              className="mobile-nav-toggle"
              aria-label={mobileMenuOpen ? 'Close navigation' : 'Open navigation'}
              aria-expanded={mobileMenuOpen}
              onClick={() => setMobileMenuOpen((open) => !open)}
            >
              {mobileMenuOpen ? <X size={18} /> : <Menu size={18} />}
            </button>
            <span className="breadcrumb-title">
              {navItems.find((n) => n.id === activeView)?.label || 'Music Studio'}
            </span>
          </div>

          <div className="topbar-actions">
            {/* Real AI vs Mock Toggle Switcher */}
            <div className="generator-switcher">
              <button
                className={`switcher-tab ${isRealAi ? 'active' : ''}`}
                onClick={() => handleToggleProvider('stability')}
                disabled={switchingProvider}
                title="Generate with Stable Audio 3.0"
              >
                <Zap size={13} />
                <span>Real AI Engine</span>
              </button>
              <button
                className={`switcher-tab ${isMockPreview ? 'active' : ''}`}
                onClick={() => handleToggleProvider('mock')}
                disabled={switchingProvider}
                title="Generate instant sandbox test fixtures"
              >
                <span>Mock Preview</span>
              </button>
            </div>
            <button className="btn-action" type="button" onClick={() => void handleLogout()}>
              Sign out
            </button>

            <button
              className="btn-action"
              onClick={() => setShowKeyModal(true)}
              title="Configure API Credentials"
            >
              <KeyRound size={13} />
              <span>API Key</span>
            </button>

            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px' }}>
              <span
                style={{
                  width: '8px',
                  height: '8px',
                  borderRadius: '50%',
                  background: backendStatus === 'connected' ? '#10b981' : '#f43f5e',
                  boxShadow: backendStatus === 'connected' ? '0 0 8px #10b981' : 'none',
                }}
              />
              <span style={{ color: '#94a3b8', fontWeight: 600 }}>
                {providerStatusLabel}
              </span>
            </div>
          </div>
        </header>

        {toast && (
          <div className={`toast toast-${toast.kind}`} role={toast.kind === 'error' ? 'alert' : 'status'} key={toast.id}>
            {toast.kind === 'success' ? <CheckCircle2 size={18} /> : <AlertCircle size={18} />}
            <span>{toast.message}</span>
            <button type="button" aria-label="Dismiss notification" onClick={() => setToast(null)}><X size={15} /></button>
          </div>
        )}
        {providerError && <div className="global-notice notice-error" role="alert">{providerError}</div>}

        {/* VIEW 1: MUSIC STUDIO CREATION */}
        {activeView === 'studio' && (
          <div className="page-wrap">
            <div className="page-header">
              <div>
                <h1 className="page-title">Generate AI Background Music</h1>
                <p className="page-subtitle">
                  Describe a sound, shape its energy, and create a track for your next project.
                </p>
              </div>
            </div>

            <section className="studio-hero">
              <div className="hero-copy">
                <span className="eyebrow"><Sparkles size={13} /> YOUR NEXT SOUND STARTS HERE</span>
                <h2>Make the music<br /><span>fit your moment.</span></h2>
                <p>Start with a style, make it yours, then preview and download your track.</p>
                <div className="hero-note">
                  <span className={`status-pulse ${backendStatus === 'connected' ? 'is-online' : ''}`} />
                  {backendStatus === 'connected'
                    ? isRealAi
                      ? providerHasNoCredits
                        ? 'Provider credits are depleted'
                        : providerConfig?.stability_credits == null
                          ? 'Provider configured · credits unverified'
                          : 'Stability credits available'
                      : providerConfig?.provider === 'mock' ? 'Mock preview mode · synthetic test audio' : 'Choose a provider to begin'
                    : backendStatus === 'checking' ? 'Connecting to your studio…' : 'Backend offline · start the API to continue'}
                </div>
              </div>
              <div className="hero-art" aria-hidden="true">
                <div className="hero-orbit orbit-one" />
                <div className="hero-orbit orbit-two" />
                <div className="hero-disc"><AudioLines size={48} strokeWidth={1.2} /></div>
                <div className="hero-spark hero-spark-one"><Sparkles size={18} /></div>
                <div className="hero-spark hero-spark-two"><Music2 size={17} /></div>
                <div className="hero-wave"><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /></div>
              </div>
            </section>

            {providerHasNoCredits && (
              <div className="notice notice-warn" role="status">
                <strong>Generation is paused: no provider credits available.</strong>
                <span>Music generation is not unlimited; the connected provider may charge per successful track. Add credits with your provider, or switch to Mock Preview to check the workflow.</span>
              </div>
            )}

            {/* Quick Presets Bar */}
            <div style={{ marginBottom: '22px' }}>
              <span className="field-label">PICK A STARTING STYLE</span>
              <div className="genre-pills-row">
                {PRESETS.map((preset) => (
                  <button
                    key={preset.name}
                    className="genre-pill"
                    onClick={() => applyPreset(preset)}
                  >
                    {preset.name}
                  </button>
                ))}
              </div>
            </div>

            <div className="workspace-grid">
              {/* Left Column: Prompting & Composition Shape */}
              <div>
                {/* Step 1: Prompt & Direction */}
                <section className="panel">
                  <div className="panel-header">
                    <div className="panel-title-group">
                      <div className="step-badge">01</div>
                      <div className="panel-heading-text">
                        <h2>Video Context & Musical Direction</h2>
                        <p>Describe your scene or enter detailed musical instructions.</p>
                      </div>
                    </div>
                  </div>

                  <div style={{ marginBottom: '16px' }}>
                    <label className="field-label">Video Scene Context (Cut Points & Action)</label>
                    <textarea
                      className="studio-textarea"
                      value={videoContext}
                      onChange={(e) => setVideoContext(e.target.value)}
                      placeholder="e.g. A fast-paced tech startup product launch video with energetic cuts, moving from calm anticipation to a bold, exciting finale..."
                      rows={2}
                    />
                  </div>

                  <div style={{ marginBottom: '16px' }}>
                    <label className="field-label">Mood & Use Case</label>
                    <input
                      type="text"
                      className="studio-input"
                      value={moodUseCase}
                      onChange={(e) => setMoodUseCase(e.target.value)}
                      placeholder="e.g. Uplifting product showcase, emotional cinematic documentary..."
                    />
                  </div>

                  <div>
                    <label className="field-label">Musical Prompt & Instruments</label>
                    <textarea
                      className="studio-textarea"
                      value={prompt}
                      onChange={(e) => setPrompt(e.target.value)}
                      placeholder="e.g. Warm acoustic pop with bright fingerpicked guitar, uplifting piano chords, and driving drum groove..."
                      rows={3}
                    />
                  </div>
                </section>

                {/* Step 2: Shape Composition */}
                <section className="panel">
                  <div className="panel-header">
                    <div className="panel-title-group">
                      <div className="step-badge">02</div>
                      <div className="panel-heading-text">
                        <h2>Composition & Arrangement Controls</h2>
                        <p>Guide the arrangement with timing, tempo, key, and mood cues.</p>
                      </div>
                    </div>
                    <SlidersHorizontal size={18} color="#818cf8" />
                  </div>

                  <div className="composition-grid">
                    {/* Duration Slider */}
                    <div className="slider-container">
                      <div className="slider-header">
                        <span className="field-label" style={{ margin: 0 }}>Track duration</span>
                        <span className="slider-val">{duration} seconds ({formatDuration(duration)})</span>
                      </div>
                      <input
                        type="range"
                        min={5}
                        max={300}
                        step={1}
                        value={duration}
                        onChange={(e) => setDuration(Number(e.target.value))}
                      />
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '10px', color: '#64748b', marginTop: '4px' }}>
                        <span>5s</span>
                        <span>60s</span>
                        <span>300s (5 min max)</span>
                      </div>
                    </div>

                    {/* Tempo */}
                    <div>
                      <label className="field-label">Tempo (BPM)</label>
                      <input
                        type="number"
                        min={40}
                        max={240}
                        className="studio-input"
                        value={tempo}
                        onChange={(e) => setTempo(Number(e.target.value))}
                      />
                    </div>

                    {/* Musical Key */}
                    <div>
                      <label className="field-label">Key Signature</label>
                      <select
                        className="studio-select"
                        value={musicKey}
                        onChange={(e) => setMusicKey(e.target.value)}
                      >
                        {['C', 'G', 'D', 'A', 'E', 'F', 'Bb', 'Eb', 'Am', 'Em', 'Dm', 'Gm', 'Bm'].map((k) => (
                          <option key={k} value={k}>{k}</option>
                        ))}
                      </select>
                    </div>
                  </div>

                  {/* Section Plan */}
                  <div style={{ marginTop: '16px', borderTop: '1px solid rgba(255,255,255,0.06)', paddingTop: '16px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                      <span className="field-label" style={{ margin: 0 }}>Section Timeline (Mood Changes at Cut Points)</span>
                      <button
                        className="btn-action"
                        onClick={() => setSections([...sections, { start_s: Math.round(duration / 2), mood: 'energetic build' }])}
                      >
                        + Add Cut Point
                      </button>
                    </div>

                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                      {sections.map((sec, i) => (
                        <div key={i} className="section-row">
                          <input
                            type="number"
                            min={0}
                            max={duration}
                            style={{ width: '80px' }}
                            className="studio-input"
                            value={sec.start_s}
                            onChange={(e) => {
                              const updated = [...sections]
                              updated[i].start_s = Number(e.target.value)
                              setSections(updated)
                            }}
                          />
                          <span style={{ fontSize: '12px', color: '#64748b' }}>sec:</span>
                          <input
                            type="text"
                            className="studio-input"
                            style={{ flex: 1 }}
                            value={sec.mood}
                            placeholder="Mood / energy change"
                            onChange={(e) => {
                              const updated = [...sections]
                              updated[i].mood = e.target.value
                              setSections(updated)
                            }}
                          />
                          <button
                            className="btn-action"
                            type="button"
                            aria-label={`Remove arrangement point ${i + 1}`}
                            onClick={() => setSections(sections.filter((_, idx) => idx !== i))}
                            style={{ padding: '8px' }}
                          >
                            <X size={14} />
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                </section>
              </div>

              {/* Right Column: Mastering Toggles & Generate Action */}
              <div>
                <section className="panel">
                  <div className="panel-header">
                    <span className="field-label" style={{ margin: 0 }}>AUDIO MASTERING SPECS</span>
                    <FileAudio2 size={16} color="#818cf8" />
                  </div>

                  <button type="button" className="toggle-card" aria-pressed={loopable} onClick={() => setLoopable(!loopable)}>
                    <div className="toggle-info">
                      <div className="toggle-icon-wrap"><RefreshCw size={16} /></div>
                      <div className="toggle-text">
                        <strong>Seamless Loop</strong>
                        <p>Crossfade the edges and measure the loop join</p>
                      </div>
                    </div>
                    <span className={`toggle-switch ${loopable ? 'is-on' : ''}`} aria-hidden="true"><span /></span>
                  </button>

                  <button type="button" className="toggle-card" aria-pressed={separateStems} onClick={() => setSeparateStems(!separateStems)}>
                    <div className="toggle-info">
                      <div className="toggle-icon-wrap"><Layers size={16} /></div>
                      <div className="toggle-text">
                        <strong>Generate Stems</strong>
                        <p>Experimental separation; results depend on local model setup</p>
                      </div>
                    </div>
                    <span className={`toggle-switch ${separateStems ? 'is-on' : ''}`} aria-hidden="true"><span /></span>
                  </button>

                  <div style={{ marginTop: '14px' }}>
                    <label className="field-label" style={{ fontSize: '11px' }}>Voiceover URL for Ducking (Optional)</label>
                    <input
                      type="url"
                      className="studio-input"
                      value={narrationUrl}
                      onChange={(e) => setNarrationUrl(e.target.value)}
                      placeholder="https://approved-host.example/speech.wav"
                      style={{ fontSize: '12px' }}
                    />
                    <div className="field-hint">Use an HTTPS host configured in the backend allowlist.</div>
                  </div>

                  <div style={{ marginTop: '10px' }}>
                    <label className="field-label" style={{ fontSize: '11px' }}>Webhook Callback URL (Optional)</label>
                    <input
                      type="url"
                      className="studio-input"
                      value={callbackUrl}
                      onChange={(e) => setCallbackUrl(e.target.value)}
                      placeholder="https://approved-host.example/webhook"
                      style={{ fontSize: '12px' }}
                    />
                    <div className="field-hint">Only backend-allowlisted HTTPS destinations are accepted.</div>
                  </div>

                  <div style={{ marginTop: '16px', background: '#0a0d14', padding: '12px', borderRadius: '10px', fontSize: '11px', color: '#64748b', lineHeight: 1.6 }}>
                    <div>• Output: <strong>48,000 Hz Stereo WAV</strong></div>
                    <div>• Target Loudness: <strong>-16.0 LUFS (±1 LU)</strong></div>
                    <div>• Peak Limit: <strong>True Peak ≤ -1.0 dBTP</strong></div>
                    <div>• Duration Precision: <strong>Within ±0.05 seconds</strong></div>
                  </div>

                  {formError && (
                    <div role="alert" style={{ margin: '14px 0 0', padding: '10px 14px', borderRadius: '8px', background: 'rgba(244, 63, 94, 0.15)', border: '1px solid rgba(244, 63, 94, 0.3)', color: '#fb7185', fontSize: '12px' }}>
                      {formError}
                    </div>
                  )}

                  <div style={{ marginTop: '20px' }}>
                    <button
                      className="btn-generate"
                      onClick={handleGenerate}
                      disabled={submitting || backendStatus !== 'connected' || !providerConfig?.is_configured || providerHasNoCredits}
                    >
                      {submitting ? (
                        <>
                          <LoaderCircle className="animate-spin" size={18} />
                          <span>Generating Music...</span>
                        </>
                      ) : (
                        <>
                          <Sparkles size={18} />
                          <span>{providerConfig?.provider === 'mock' ? 'Create Mock Preview' : `Generate Track · ${duration}s`}</span>
                        </>
                      )}
                    </button>
                  </div>
                </section>
              </div>
            </div>
          </div>
        )}

        {/* VIEW 2: GENERATION HISTORY */}
        {activeView === 'jobs' && (
          <div className="page-wrap">
            <div className="page-header">
              <div>
                <h1 className="page-title">Generation History & Deliveries</h1>
                <p className="page-subtitle">
                  Follow every generation, review measured audio details, and get to your finished files.
                </p>
              </div>
              <button className="btn-action refresh-button" onClick={() => void refreshJobs(true)} disabled={refreshingJobs || backendStatus !== 'connected'}>
                <RefreshCw className={refreshingJobs ? 'refresh-spinning' : ''} size={14} />
                <span>Refresh Jobs</span>
              </button>
            </div>

            <div className="history-overview" aria-label="Job status summary">
              {(['succeeded', 'running', 'queued', 'failed'] as const).map((status) => (
                <div className={`history-stat history-stat-${status}`} key={status}>
                  <span className="history-stat-dot" />
                  <span>{statusLabels[status]}</span>
                  <strong>{jobs.filter((job) => job.status === status).length}</strong>
                </div>
              ))}
            </div>

            {jobsError && <div className="notice notice-error" role="alert">{jobsError}</div>}
            {jobsLoading ? (
              <div className="history-skeleton-list" aria-label="Loading generation history" aria-busy="true">
                {[1, 2, 3].map((item) => (
                  <div className="history-skeleton" key={item}>
                    <div className="skeleton-line skeleton-short" />
                    <div className="skeleton-line skeleton-title" />
                    <div className="skeleton-line skeleton-meta" />
                    <div className="skeleton-line skeleton-player" />
                  </div>
                ))}
              </div>
            ) : jobs.length === 0 ? (
              <div className="panel" style={{ textAlign: 'center', padding: '48px 24px' }}>
                <Music2 size={36} color="#64748b" style={{ margin: '0 auto 14px' }} />
                <h3 style={{ margin: '0 0 6px', color: '#f1f5f9' }}>No tracks generated yet</h3>
                <p style={{ margin: '0 0 20px', color: '#64748b', fontSize: '13px' }}>
                  Your generations will appear here as soon as you create a track.
                </p>
                <button className="btn-action" onClick={() => setActiveView('studio')}>
                  Go to Music Studio
                </button>
              </div>
            ) : (
              jobs.map((job) => {
                const isPlaying = playingJobId === job.job_id
                const hasStems = job.result?.stem_processing?.status === 'available'
                const hasDucked = Boolean(job.result?.mix_ducked_download_url)
                const providerLabel = job.result?.provider === 'stability'
                  ? 'STABLE AUDIO'
                  : job.result?.provider === 'mock'
                    ? 'MOCK PREVIEW'
                    : ''
                const isPromptExpanded = expandedPrompts.has(job.job_id)
                const isLongPrompt = job.prompt.length > 220
                const statusLabel = statusLabels[job.status]

                return (
                  <article key={job.job_id} className={`job-card job-card-${job.status}`}>
                    <div className="job-card-header">
                      <div className="job-main-info">
                        <div className="job-heading-row">
                          <span className={`job-status-chip status-${job.status}`}><i />{statusLabel}</span>
                          {providerLabel && <span className="job-provider-chip">{providerLabel}</span>}
                          <time className="job-timestamp" dateTime={job.created_at}>{formatTimestamp(job.created_at)}</time>
                        </div>
                        <h3 className={`job-prompt ${isPromptExpanded ? 'is-expanded' : ''}`}>
                          {job.prompt}
                        </h3>
                        {isLongPrompt && (
                          <button
                            type="button"
                            className="prompt-expand-button"
                            aria-expanded={isPromptExpanded}
                            onClick={() => togglePromptExpansion(job.job_id)}
                          >
                            {isPromptExpanded ? 'Show less' : 'Read full prompt'}
                            <ChevronDown className={isPromptExpanded ? 'chevron-up' : ''} size={14} />
                          </button>
                        )}
                        <div className="job-meta-row">
                          <span className="metric-chip"><Clock3 size={13} /> {job.duration_s}s requested</span>
                          {job.result && <span className="metric-chip"><Clock3 size={13} /> {job.result.duration_s.toFixed(1)}s actual</span>}
                          {!job.result && <span className="metric-chip"><Clock3 size={13} /> {job.duration_s}s requested</span>}
                          {job.result?.format && <span className="metric-chip"><FileAudio2 size={13} /> {job.result.format.toUpperCase()}</span>}
                          {job.result?.sample_rate != null && <span className="metric-chip"><AudioLines size={13} /> {(job.result.sample_rate / 1000).toFixed(1)} kHz</span>}
                          {job.result?.integrated_lufs != null && <span className="metric-chip">{job.result.integrated_lufs.toFixed(1)} LUFS</span>}
                          {job.result?.true_peak_dbtp != null && <span className="metric-chip">{job.result.true_peak_dbtp.toFixed(1)} dBTP</span>}
                          {hasStems && <span className="metric-chip metric-chip-accent">{job.result?.stem_processing.stems.length} stems</span>}
                          {hasDucked && <span className="metric-chip metric-chip-accent">Voiceover mix</span>}
                        </div>
                      </div>

                      {job.status === 'succeeded' && job.result && (
                      <div className="job-actions">
                        {job.status === 'succeeded' && (
                          <button
                            className="btn-action"
                            type="button"
                            onClick={() => {
                              setSelectedJob(job)
                              setActiveView('results')
                            }}
                          >
                            View results
                          </button>
                        )}
                        <button className="btn-action" type="button" onClick={() => openManifestViewer(job.job_id)}>Manifest</button>
                        <button
                          className="btn-action"
                          type="button"
                          onClick={() => {
                            setNarrationJobId(job.job_id)
                            setActiveView('narration')
                          }}
                        >
                          <Headphones size={13} /> Mix narration
                        </button>
                        {job.result.download_url && (
                          <a
                            href={`${musicApiBaseUrl}${job.result.download_url}`}
                            download
                            className="btn-action job-download"
                          >
                            <Download size={13} />
                            <span>Download {job.result.format.toUpperCase()}</span>
                          </a>
                        )}
                      </div>
                      )}
                    </div>

                    {(job.status === 'queued' || job.status === 'running') && (
                      <div className="job-progress" role="status">
                        <div className="job-progress-copy"><span>{job.status === 'queued' ? 'Waiting in queue' : 'Creating your track'}</span><strong>{job.progress}%</strong></div>
                        <div className="job-progress-track"><div style={{ width: `${job.progress}%` }} /></div>
                      </div>
                    )}
                    {job.status === 'failed' && job.error && (
                      <div className="job-error" role="alert">
                        <div className="job-error-heading">
                          <AlertCircle size={17} />
                          <strong>{job.error.code === 'PROVIDER_INSUFFICIENT_CREDITS' ? 'Provider credits required' : 'Generation could not be completed'}</strong>
                        </div>
                        <span>{job.error.message}</span>
                        {job.error.code === 'PROVIDER_INSUFFICIENT_CREDITS' ? (
                          <p>This is an external provider billing issue. IncuBrix cannot add credits. Add credits through your Stability account before submitting a new generation; this failed job was not retried.</p>
                        ) : (
                          <p>This failed job remains in your history. Review the error above, then make a new request from Music Studio if appropriate. No automatic retry was made.</p>
                        )}
                      </div>
                    )}

                    {/* Audio Player Bar */}
                    {job.result?.download_url && (
                      <div className="audio-player-bar">
                        <button
                          className="play-btn"
                          type="button"
                          aria-label={isPlaying ? 'Pause preview' : 'Play preview'}
                          onClick={() => togglePlayAudio(job.job_id, job.result!.download_url)}
                        >
                          {isPlaying ? <Pause size={16} /> : <Play size={16} style={{ marginLeft: '2px' }} />}
                        </button>

                        <div className="audio-progress-bar">
                          <div
                            className="audio-progress-fill"
                            style={{ width: `${isPlaying ? playbackProgress : 0}%` }}
                          />
                        </div>

                        {/* Equalizer Waveform Bars */}
                        {isPlaying && (
                          <div className="eq-bars">
                            <div className="eq-bar eq-bar-1" />
                            <div className="eq-bar eq-bar-2" />
                            <div className="eq-bar eq-bar-3" />
                            <div className="eq-bar eq-bar-4" />
                          </div>
                        )}

                        <span style={{ fontSize: '11px', fontFamily: 'monospace', color: '#94a3b8' }}>
                          {formatDuration(job.duration_s)}
                        </span>
                      </div>
                    )}
                  </article>
                )
              })
            )}
          </div>
        )}

        {/* VIEW 3: AUDIO RESULTS & STEMS */}
        {activeView === 'results' && (
          <div className="page-wrap">
            <div className="page-header">
              <div>
                <h1 className="page-title">Your audio results</h1>
                <p className="page-subtitle">
                  Preview and download finished tracks, inspect measured audio details, and access stems when the separation step produced them.
                </p>
              </div>
              <button className="btn-action" onClick={() => setActiveView('studio')}><Sparkles size={14} /> New track</button>
            </div>

            {jobsLoading && completedJobs.length === 0 ? (
              <div className="history-skeleton-list" aria-label="Loading audio results" aria-busy="true">
                {[1, 2].map((item) => <div className="history-skeleton" key={item}><div className="skeleton-line skeleton-short" /><div className="skeleton-line skeleton-title" /><div className="skeleton-line skeleton-player" /></div>)}
              </div>
            ) : completedJobs.length === 0 ? (
              <section className="panel empty-state">
                <div className="empty-icon"><Music2 size={25} /></div>
                <h2>No finished tracks yet</h2>
                <p>Your completed mixes and any available stems will appear here.</p>
                <button className="btn-generate" onClick={() => setActiveView('studio')}>Create your first track</button>
              </section>
            ) : (
              <>
                <section className="panel result-select-panel">
                  <label className="field-label" htmlFor="result-track">Selected track</label>
                  <select
                    id="result-track"
                    className="studio-select"
                    value={resultJob?.job_id ?? ''}
                    onChange={(event) => {
                      const nextJob = completedJobs.find((job) => job.job_id === event.target.value)
                      if (nextJob) setSelectedJob(nextJob)
                    }}
                  >
                    {completedJobs.map((job) => (
                      <option key={job.job_id} value={job.job_id}>
                        {job.prompt.slice(0, 80)} · {job.duration_s}s · {new Date(job.created_at).toLocaleString()}
                      </option>
                    ))}
                  </select>
                </section>

                {resultJob?.result && (
                  <div className="results-grid">
                    <section className="panel result-main">
                      <div className="panel-header">
                        <div>
                          <span className="eyebrow"><AudioLines size={13} /> FINISHED MIX</span>
                          <h2 className="result-title">{resultJob.prompt}</h2>
                        </div>
                        <span className="provider-badge-real">READY</span>
                      </div>
                      <div className="result-player">
                        <button
                          className="play-btn"
                          aria-label={playingJobId === resultJob.job_id ? 'Pause audio' : 'Play audio'}
                          onClick={() => togglePlayAudio(resultJob.job_id, resultJob.result!.download_url)}
                        >
                          {playingJobId === resultJob.job_id ? <Pause size={16} /> : <Play size={16} />}
                        </button>
                        <div className="result-track-copy"><strong>{playingJobId === resultJob.job_id ? 'Now playing' : 'Preview your track'}</strong><span>{formatDuration(resultJob.duration_s)} · {resultJob.result.format.toUpperCase()}</span></div>
                        <a className="btn-action" href={`${musicApiBaseUrl}${resultJob.result.download_url}`} download><Download size={14} /> Download mix</a>
                      </div>
                      <div className="result-metrics">
                        <div><span>Duration</span><strong>{resultJob.result.duration_s.toFixed(1)} sec</strong></div>
                        <div><span>Sample rate</span><strong>{resultJob.result.sample_rate != null ? `${(resultJob.result.sample_rate / 1000).toFixed(1)} kHz` : 'Not measured'}</strong></div>
                        <div><span>Channels</span><strong>{resultJob.result.channel_count ?? 'Not measured'}</strong></div>
                        <div><span>Integrated loudness</span><strong>{resultJob.result.integrated_lufs !== null ? `${resultJob.result.integrated_lufs.toFixed(1)} LUFS` : 'Not measured'}</strong></div>
                        <div><span>True peak</span><strong>{resultJob.result.true_peak_dbtp !== null ? `${resultJob.result.true_peak_dbtp.toFixed(1)} dBTP` : 'Not measured'}</strong></div>
                        <div><span>Loop check</span><strong>{resultJob.result.loop_validation.status.replace('_', ' ')}</strong></div>
                      </div>
                    </section>

                    <section className="panel stems-panel">
                      <div className="panel-header">
                        <div><span className="eyebrow"><Layers size={13} /> SEPARATED AUDIO</span><h2 className="result-title">Stems</h2></div>
                        <span className="stem-count">{resultJob.result.stem_processing.stems.length} available</span>
                      </div>
                      {resultJob.result.stem_processing.stems.length > 0 ? (
                        <div className="stem-list">
                          {resultJob.result.stem_processing.stems.map((stem) => (
                            <div className="stem-row" key={stem.stem_id}>
                              <div className="stem-icon"><AudioLines size={16} /></div>
                              <div className="stem-details"><strong>{stem.label}</strong><span>{stem.sample_rate / 1000} kHz · {formatDuration(stem.duration_s)}</span></div>
                              <a className="icon-download" href={`${musicApiBaseUrl}${stem.download_url}`} download aria-label={`Download ${stem.label}`}><Download size={15} /></a>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <div className="stems-empty">
                          <p>{resultJob.result.stem_processing.error?.message ?? 'No stems were created for this track.'}</p>
                          <span>Stem separation is optional and may require the experimental local model.</span>
                        </div>
                      )}
                    </section>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {/* VIEW 4: NARRATION MIXER */}
        {activeView === 'narration' && (
          <div className="page-wrap">
            <div className="page-header">
              <div>
                <h1 className="page-title">Voiceover Ducking & Dialogue Mixer</h1>
                <p className="page-subtitle">
                  Upload a WAV or MP3 voiceover and mix it with a completed background track using automatic music ducking.
                </p>
              </div>
            </div>

            <div className="workspace-grid">
              <div>
                <section className="panel">
                  <div className="panel-header">
                    <span className="field-label" style={{ margin: 0 }}>UPLOAD VOICEOVER AUDIO</span>
                    <Headphones size={18} color="#818cf8" />
                  </div>

                  <div style={{ marginBottom: '16px' }}>
                    <label className="field-label">Target Background Track</label>
                    <select
                      className="studio-select"
                      value={narrationJobId || (selectedJob?.status === 'succeeded' ? selectedJob.job_id : '')}
                      onChange={(e) => setNarrationJobId(e.target.value)}
                    >
                      <option value="">Select a completed track...</option>
                      {completedJobs.map((j) => (
                        <option key={j.job_id} value={j.job_id}>
                          {j.job_id.slice(0, 8)} - {j.prompt.slice(0, 40)}... ({j.duration_s}s)
                        </option>
                      ))}
                    </select>
                  </div>

                  <div className="upload-dropzone">
                    <input
                      type="file"
                      id="voiceover-file"
                      accept="audio/wav,audio/mp3,audio/mpeg"
                      onChange={(e) => handleNarrationFileChange(e.target.files?.[0] || null)}
                      className="visually-hidden"
                    />
                    <label htmlFor="voiceover-file" style={{ cursor: 'pointer' }}>
                      <Headphones size={32} color="#818cf8" style={{ margin: '0 auto 12px' }} />
                      <div style={{ fontSize: '14px', fontWeight: 600, color: '#f1f5f9' }}>
                        {narrationFile ? narrationFile.name : 'Click to select WAV or MP3 voiceover'}
                      </div>
                      <div style={{ fontSize: '12px', color: '#64748b', marginTop: '4px' }}>
                        WAV or MP3 · Up to 50 MB · Automatically aligned to track length
                      </div>
                      <span className="upload-pick-button">{narrationFile ? 'Choose a different file' : 'Choose voiceover'}</span>
                    </label>
                  </div>

                  {narrationPreviewUrl && (
                    <div className="audio-source-card">
                      <div className="audio-source-heading"><span className="source-icon"><Headphones size={15} /></span><div><strong>Narration preview</strong><span>{narrationFile?.name} · {((narrationFile?.size ?? 0) / (1024 * 1024)).toFixed(1)} MB</span></div></div>
                      <audio controls preload="metadata" src={narrationPreviewUrl}>Your browser does not support audio playback.</audio>
                    </div>
                  )}

                  <button
                    className="btn-generate"
                    onClick={handleUploadNarration}
                    disabled={!narrationFile || narrationFile.size > 50 * 1024 * 1024 || !(narrationJobId || (selectedJob?.status === 'succeeded' && selectedJob.job_id)) || mixingNarration}
                  >
                    {mixingNarration ? <><LoaderCircle className="animate-spin" size={17} /> Mixing voiceover with music…</> : <><AudioLines size={17} /> Mix voiceover with music</>}
                  </button>

                  {narrationError && <div className="notice notice-error" role="alert">{narrationError}</div>}
                  {narrationMix && (
                    <div className="mix-result-card">
                      <div className="mix-result-heading"><CheckCircle2 size={18} /><strong>Voiceover mix is ready</strong></div>
                      <p className="mix-result-meta">{narrationMix.duration_s.toFixed(1)} sec · {narrationMix.sample_rate / 1000} kHz · music ducked by {narrationMix.duck_attenuation_db.toFixed(1)} dB</p>
                      <audio controls crossOrigin="use-credentials" preload="metadata" src={`${musicApiBaseUrl}${narrationMix.download_url}`}>Your browser does not support audio playback.</audio>
                      {narrationMix.warnings.length > 0 && <div className="mix-warnings">{narrationMix.warnings.map((warning) => <span key={warning}>{warning}</span>)}</div>}
                      <a
                        href={`${musicApiBaseUrl}${narrationMix.download_url}`}
                        download
                        className="btn-action"
                        style={{ marginTop: '8px' }}
                      >
                        <Download size={14} />
                        <span>Download mix_ducked.wav</span>
                      </a>
                    </div>
                  )}
                </section>
              </div>

              <div>
                <section className="panel">
                  <div className="panel-header">
                    <div><span className="eyebrow"><SlidersHorizontal size={13} /> MIX WORKSPACE</span><h2 className="mixer-panel-title">Your audio sources</h2></div>
                    <SlidersHorizontal size={18} color="#818cf8" />
                  </div>
                  {narrationMusicJob?.result ? (
                    <div className="audio-source-card">
                      <div className="audio-source-heading"><span className="source-icon source-music"><Music2 size={15} /></span><div><strong>Background music</strong><span>{narrationMusicJob.duration_s}s · {narrationMusicJob.result.format.toUpperCase()} · {narrationMusicJob.prompt.slice(0, 64)}</span></div></div>
                      <audio controls crossOrigin="use-credentials" preload="metadata" src={`${musicApiBaseUrl}${narrationMusicJob.result.download_url}`}>Your browser does not support audio playback.</audio>
                    </div>
                  ) : (
                    <div className="stems-empty"><p>Choose a completed track to preview the music source.</p><span>Only successfully generated tracks are available for mixing.</span></div>
                  )}
                  <div className="mixer-divider" />
                  <div className="panel-header mixer-spec-header">
                    <span className="field-label" style={{ margin: 0 }}>DUCKING PARAMETERS</span>
                    <SlidersHorizontal size={16} color="#818cf8" />
                  </div>
                  <div style={{ fontSize: '12px', color: '#94a3b8', lineHeight: 1.8 }}>
                    <div>• Ducking level: <strong>12 dB target</strong></div>
                    <div>• Timing: <strong>50 ms attack / 250 ms release</strong></div>
                    <div>• Accepted files: <strong>WAV or MP3</strong></div>
                    <div>• Upload limit: <strong>50 MB</strong></div>
                  </div>
                </section>
              </div>
            </div>
          </div>
        )}

        {/* VIEW 5: API REFERENCE */}
        {activeView === 'api' && (
          <div className="page-wrap">
            <div className="page-header">
              <div>
                <h1 className="page-title">HTTP API Contract (OpenAPI 3.1)</h1>
                <p className="page-subtitle">
                  Every endpoint conforms to the IncuBrix Integration Standard. All routes live under <code>/api/v1/</code>.
                </p>
              </div>
              <a
                href={`${musicApiBaseUrl}/docs`}
                target="_blank"
                rel="noreferrer"
                className="btn-action"
              >
                <ExternalLink size={14} />
                <span>Interactive Swagger Docs</span>
              </a>
            </div>

            <section className="panel">
              <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                <div style={{ background: '#0d1018', padding: '14px', borderRadius: '10px', border: '1px solid rgba(255,255,255,0.08)' }}>
                  <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '6px' }}>
                    <span style={{ background: '#4f46e5', color: '#fff', fontSize: '10px', fontWeight: 700, padding: '2px 6px', borderRadius: '4px' }}>POST</span>
                    <strong style={{ fontSize: '13px' }}>/api/v1/music/jobs</strong>
                  </div>
                  <p style={{ fontSize: '12px', color: '#94a3b8', margin: 0 }}>
                    Enqueues an asynchronous generation job. Returns HTTP 202 with <code>job_id</code>. Supports <code>Idempotency-Key</code> header.
                  </p>
                </div>

                <div style={{ background: '#0d1018', padding: '14px', borderRadius: '10px', border: '1px solid rgba(255,255,255,0.08)' }}>
                  <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '6px' }}>
                    <span style={{ background: '#10b981', color: '#fff', fontSize: '10px', fontWeight: 700, padding: '2px 6px', borderRadius: '4px' }}>GET</span>
                    <strong style={{ fontSize: '13px' }}>/api/v1/jobs/{'{job_id}'}</strong>
                  </div>
                  <p style={{ fontSize: '12px', color: '#94a3b8', margin: 0 }}>
                    Polls job status (queued, running, succeeded, failed), progress (0–100%), and output download links.
                  </p>
                </div>

                <div style={{ background: '#0d1018', padding: '14px', borderRadius: '10px', border: '1px solid rgba(255,255,255,0.08)' }}>
                  <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '6px' }}>
                    <span style={{ background: '#10b981', color: '#fff', fontSize: '10px', fontWeight: 700, padding: '2px 6px', borderRadius: '4px' }}>GET</span>
                    <strong style={{ fontSize: '13px' }}>/api/v1/jobs/{'{job_id}'}/manifest</strong>
                  </div>
                  <p style={{ fontSize: '12px', color: '#94a3b8', margin: 0 }}>
                    Returns <code>music_manifest.json</code> containing complete technical provenance, DSP metrics, and licensing terms.
                  </p>
                </div>
              </div>
            </section>
          </div>
        )}

        {/* MODAL: MANIFEST VIEWER */}
        {manifestJob && (
          <div className="modal-overlay" onClick={() => setManifestJob(null)}>
            <div className="modal-content" role="dialog" aria-modal="true" aria-labelledby="manifest-title" onClick={(e) => e.stopPropagation()}>
              <div className="modal-header">
                <div>
                  <h3 id="manifest-title" style={{ margin: 0, fontSize: '15px', color: '#f1f5f9' }}>music_manifest.json</h3>
                  <div style={{ fontSize: '11px', color: '#64748b' }}>Job ID: {manifestJob}</div>
                </div>
                <div style={{ display: 'flex', gap: '8px' }}>
                  <button className="btn-action" onClick={() => void copyManifest()} disabled={manifestLoading || !manifest || 'error' in manifest}>
                    <Copy size={13} />
                    <span>{copyNotice ? 'Copied!' : 'Copy JSON'}</span>
                  </button>
                  <button className="btn-action" aria-label="Close manifest" onClick={() => setManifestJob(null)}>
                    <X size={14} />
                  </button>
                </div>
              </div>
              <div className="modal-body">
                {manifestLoading ? <div className="manifest-loading" role="status"><LoaderCircle className="animate-spin" size={18} /> Loading manifest…</div> : (
                  'error' in (manifest ?? {}) ? <div className="notice notice-error" role="alert">{String(manifest?.error)}</div> : <pre>{JSON.stringify(manifest, null, 2)}</pre>
                )}
              </div>
            </div>
          </div>
        )}

        {/* MODAL: BACKEND PROVIDER CREDENTIALS */}
        {showKeyModal && (
          <div className="modal-overlay" onClick={() => setShowKeyModal(false)}>
            <div className="modal-content" role="dialog" aria-modal="true" aria-labelledby="provider-modal-title" style={{ maxWidth: '480px' }} onClick={(e) => e.stopPropagation()}>
              <div className="modal-header">
                <h3 id="provider-modal-title" style={{ margin: 0, fontSize: '15px', color: '#f1f5f9' }}>Configure Backend Provider Credentials</h3>
                <button className="btn-action" aria-label="Close provider settings" onClick={() => setShowKeyModal(false)}><X size={14} /></button>
              </div>
              <div style={{ padding: '20px' }}>
                <p style={{ fontSize: '12px', color: '#94a3b8', lineHeight: 1.6, margin: '0 0 14px' }}>
                  Provider keys must stay on the backend. For local development, set
                  <code> STABILITY_API_KEY</code> in <code>backend/.env</code>; in production, use your hosting
                  platform&apos;s secret manager. Never paste a provider key into this page.
                </p>
                <button
                  className="btn-generate"
                  style={{ marginTop: '16px' }}
                  onClick={() => {
                    setShowKeyModal(false)
                    void refreshProvider()
                  }}
                  disabled={switchingProvider}
                >
                  Refresh Provider Status
                </button>
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  )
}

export default App
