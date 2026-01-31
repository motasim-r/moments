import { useEffect, useMemo, useState } from 'react'
import './App.css'

type ClipItem = {
  id: string
  file: File
  locked: boolean
}

type JobStatus = {
  status: string
  step?: string
  progress?: number
  message?: string
  artifact_urls?: {
    preview?: string
    final?: string
    edl?: string
  }
}

type VersionInfo = {
  version: string
  git?: {
    commit: string
    branch: string
    recent?: { commit: string; message: string; date: string }[]
  }
}

type LibraryItem = {
  name: string
  size: number
  modified_at: string
}

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

const allowedClipExt = ['mp4', 'mov', 'webm']
const allowedSongExt = ['mp3', 'm4a', 'wav']

const createId = () =>
  typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random()}`

function formatSize(bytes: number) {
  const units = ['B', 'KB', 'MB', 'GB']
  let size = bytes
  let unitIndex = 0
  while (size > 1024 && unitIndex < units.length - 1) {
    size /= 1024
    unitIndex += 1
  }
  return `${size.toFixed(1)} ${units[unitIndex]}`
}

function getFileExt(name: string) {
  const parts = name.toLowerCase().split('.')
  return parts.length > 1 ? parts[parts.length - 1] : ''
}

function formatDate(value: string) {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.valueOf())) return value
  return parsed.toLocaleString()
}

function App() {
  const [clips, setClips] = useState<ClipItem[]>([])
  const [song, setSong] = useState<File | null>(null)
  const [clipSource, setClipSource] = useState<'local' | 'glasses'>('local')
  const [targetLength, setTargetLength] = useState(15)
  const [vibe, setVibe] = useState<'hype' | 'chill' | 'chaotic'>('hype')
  const [vhsIntensity, setVhsIntensity] = useState(0.7)
  const [glitchAmount, setGlitchAmount] = useState(0.2)
  const [includeClipAudio, setIncludeClipAudio] = useState(false)
  const [resolution, setResolution] = useState<'1080x1920' | '1280x960' | '1360x1824'>(
    '1080x1920',
  )
  const [ntscPreset, setNtscPreset] = useState<
    'custom' | 'noisy' | 'semi-sharp' | 'game-tape' | 'dynamic'
  >('custom')
  const [jobId, setJobId] = useState<string | null>(null)
  const [jobStatus, setJobStatus] = useState<JobStatus | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [finalUrl, setFinalUrl] = useState<string | null>(null)
  const [edlText, setEdlText] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [versionInfo, setVersionInfo] = useState<VersionInfo | null>(null)
  const [libraryItems, setLibraryItems] = useState<LibraryItem[]>([])
  const [librarySelected, setLibrarySelected] = useState<string[]>([])
  const [libraryLocked, setLibraryLocked] = useState<string[]>([])
  const [libraryMessage, setLibraryMessage] = useState<string | null>(null)
  const [libraryError, setLibraryError] = useState<string | null>(null)
  const [importPath, setImportPath] = useState('')

  const lockedClipNames = useMemo(
    () =>
      clipSource === 'glasses'
        ? libraryLocked
        : clips.filter((clip) => clip.locked).map((clip) => clip.file.name),
    [clipSource, clips, libraryLocked],
  )

  const handleClipFiles = (files: FileList | null) => {
    if (!files) return
    setClipSource('local')
    const next: ClipItem[] = []
    Array.from(files).forEach((file) => {
      const ext = getFileExt(file.name)
      if (!allowedClipExt.includes(ext)) {
        return
      }
      next.push({
        id: createId(),
        file,
        locked: false,
      })
    })
    setClips((current) => [...current, ...next].slice(0, 20))
  }

  const handleSongFile = (files: FileList | null) => {
    if (!files || files.length === 0) return
    const file = files[0]
    const ext = getFileExt(file.name)
    if (!allowedSongExt.includes(ext)) {
      setError('Unsupported song format')
      return
    }
    setSong(file)
  }

  const handleGenerate = async () => {
    setError(null)
    setEdlText(null)
    setPreviewUrl(null)
    setFinalUrl(null)
    if (!song) {
      setError('Please add a song.')
      return
    }

    const settingsPayload = JSON.stringify({
      target_length_s: targetLength,
      vibe,
      vhs_intensity: vhsIntensity,
      glitch_amount: glitchAmount,
      include_clip_audio: includeClipAudio,
      locked_clips: lockedClipNames,
      ntsc_preset: ntscPreset,
      resolution,
      seed: Math.floor(Math.random() * 1_000_000),
    })

    let response: Response
    if (clipSource === 'glasses') {
      if (librarySelected.length === 0) {
        setError('Please select at least one glasses clip.')
        return
      }
      const payload = new FormData()
      payload.append('clip_names', JSON.stringify(librarySelected))
      payload.append('song', song)
      payload.append('settings', settingsPayload)
      response = await fetch(`${API_BASE}/jobs/from-library`, {
        method: 'POST',
        body: payload,
      })
    } else {
      if (clips.length === 0) {
        setError('Please add at least one clip.')
        return
      }
      const payload = new FormData()
      clips.forEach((clip) => payload.append('clips', clip.file))
      payload.append('song', song)
      payload.append('settings', settingsPayload)
      response = await fetch(`${API_BASE}/jobs`, {
        method: 'POST',
        body: payload,
      })
    }
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}))
      setError(detail.detail ?? 'Failed to create job')
      return
    }
    const result = (await response.json()) as { job_id: string }
    setJobId(result.job_id)
    setJobStatus({ status: 'queued', progress: 0 })
  }

  const handleRegenerate = () => {
    if (!jobId) return
    void handleGenerate()
  }

  const handleShowEdl = async () => {
    if (!jobId) return
    const response = await fetch(`${API_BASE}/jobs/${jobId}/edl.json`)
    if (!response.ok) {
      setError('EDL not ready yet')
      return
    }
    const text = await response.text()
    setEdlText(text)
  }

  const refreshLibrary = async () => {
    try {
      const response = await fetch(`${API_BASE}/glasses/library`)
      if (!response.ok) {
        setLibraryError('Failed to load glasses library')
        return
      }
      const data = (await response.json()) as { items: LibraryItem[] }
      setLibraryItems(data.items ?? [])
      const names = new Set((data.items ?? []).map((item) => item.name))
      setLibrarySelected((current) => current.filter((name) => names.has(name)))
      setLibraryLocked((current) => current.filter((name) => names.has(name)))
      setLibraryError(null)
    } catch {
      setLibraryError('Failed to load glasses library')
    }
  }

  const handleLibraryImport = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    setLibraryError(null)
    setLibraryMessage('Importing glasses footage…')
    const payload = new FormData()
    Array.from(files).forEach((file) => payload.append('clips', file))
    try {
      const response = await fetch(`${API_BASE}/glasses/import`, {
        method: 'POST',
        body: payload,
      })
      if (!response.ok) {
        setLibraryError('Failed to import glasses footage')
        setLibraryMessage(null)
        return
      }
      const data = (await response.json()) as { count?: number }
      setLibraryMessage(`Imported ${data.count ?? 0} clips into the glasses library.`)
      void refreshLibrary()
    } catch {
      setLibraryError('Failed to import glasses footage')
      setLibraryMessage(null)
    }
  }

  const handleLibraryImportPath = async () => {
    if (!importPath.trim()) {
      setLibraryError('Please enter a folder path to import from.')
      return
    }
    setLibraryError(null)
    setLibraryMessage('Scanning folder for glasses footage…')
    try {
      const response = await fetch(`${API_BASE}/glasses/import-path`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: importPath.trim(), recursive: true }),
      })
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}))
        setLibraryError(detail.detail ?? 'Failed to import from folder')
        setLibraryMessage(null)
        return
      }
      const data = (await response.json()) as { count?: number }
      setLibraryMessage(`Imported ${data.count ?? 0} clips from folder.`)
      void refreshLibrary()
    } catch {
      setLibraryError('Failed to import from folder')
      setLibraryMessage(null)
    }
  }

  const toggleLibrarySelected = (name: string) => {
    if (!librarySelected.includes(name) && librarySelected.length >= 20) {
      setLibraryError('Maximum of 20 clips allowed.')
      return
    }
    setLibrarySelected((current) => {
      if (current.includes(name)) {
        setLibraryLocked((locked) => locked.filter((item) => item !== name))
        return current.filter((item) => item !== name)
      }
      return [...current, name]
    })
  }

  const toggleLibraryLocked = (name: string) => {
    setLibraryLocked((current) =>
      current.includes(name)
        ? current.filter((item) => item !== name)
        : [...current, name],
    )
    setLibrarySelected((current) =>
      current.includes(name) ? current : [...current, name],
    )
  }

  useEffect(() => {
    fetch(`${API_BASE}/version`)
      .then((response) => response.json())
      .then((data: VersionInfo) => setVersionInfo(data))
      .catch(() => setVersionInfo(null))
  }, [])

  useEffect(() => {
    void refreshLibrary()
  }, [])

  useEffect(() => {
    if (!jobId) return
    let active = true

    const poll = async () => {
      try {
        const response = await fetch(`${API_BASE}/jobs/${jobId}`)
        if (!response.ok) return
        const data = (await response.json()) as JobStatus
        if (!active) return
        setJobStatus(data)
        if (data.artifact_urls?.preview) {
          setPreviewUrl(`${API_BASE}${data.artifact_urls.preview}`)
        }
        if (data.artifact_urls?.final) {
          setFinalUrl(`${API_BASE}${data.artifact_urls.final}`)
        }
      } catch {
        if (active) {
          setError('Failed to fetch job status')
        }
      }
    }

    void poll()
    const interval = window.setInterval(poll, 2000)
    return () => {
      active = false
      window.clearInterval(interval)
    }
  }, [jobId])

  return (
    <div className="app">
      <header className="hero">
        <div>
          <p className="badge">Code X • Local VHS Reel Editor</p>
          <h1>
            Turn night-out clips into a <span>15–30s VHS highlight reel</span> on
            your laptop.
          </h1>
          <p className="subhead">
            Drop in your clips, pick a vibe, and generate a synced vertical reel
            with a VHS finish—no uploads.
          </p>
          {versionInfo?.git?.commit && (
            <p className="version">
              Version {versionInfo.version} • {versionInfo.git.commit} (
              {versionInfo.git.branch})
            </p>
          )}
        </div>
        <div className="hero-card">
          <div className="hero-stat">
            <span>Avg. loop</span>
            <strong>2–8 min</strong>
          </div>
          <div className="hero-stat">
            <span>Output</span>
            <strong>720p / 1080p</strong>
          </div>
          <div className="hero-stat">
            <span>Sync</span>
            <strong>Beat-aligned</strong>
          </div>
        </div>
      </header>

      <section className="panel">
        <div className="panel-header">
          <h2>1. Drop your assets</h2>
          <p>Up to 20 clips. MP4/MOV/WEBM + one MP3/M4A/WAV track.</p>
        </div>
        <div className="source-toggle">
          <span>Clip source</span>
          <div className="segmented">
            <button
              className={clipSource === 'local' ? 'active' : ''}
              onClick={() => setClipSource('local')}
            >
              Local uploads
            </button>
            <button
              className={clipSource === 'glasses' ? 'active' : ''}
              onClick={() => setClipSource('glasses')}
            >
              Glasses library
            </button>
          </div>
        </div>
        {clipSource === 'local' ? (
        <div className="drop-grid">
          <label
            className="dropzone"
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault()
              handleClipFiles(event.dataTransfer.files)
            }}
          >
            <input
              type="file"
              multiple
              accept="video/mp4,video/quicktime,video/webm"
              onChange={(event) => handleClipFiles(event.target.files)}
            />
            <div>
              <h3>Video Clips</h3>
              <p>Drag & drop or click to select</p>
            </div>
          </label>
          <label
            className="dropzone"
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault()
              handleSongFile(event.dataTransfer.files)
            }}
          >
            <input
              type="file"
              accept="audio/mpeg,audio/mp4,audio/wav"
              onChange={(event) => handleSongFile(event.target.files)}
            />
            <div>
              <h3>Music Track</h3>
              <p>{song ? song.name : 'Drop your song or click to select'}</p>
            </div>
          </label>
        </div>
        ) : (
          <div className="drop-grid">
            <div className="library-card">
              <h3>Glasses Library</h3>
              <p>
                Import footage captured on the glasses. Use the mobile SDK to pull
                clips over Wi-Fi/BLE, then drop them here or import from the
                download folder.
              </p>
              <div className="library-actions">
                <label className="chip">
                  Import files
                  <input
                    type="file"
                    multiple
                    accept="video/mp4,video/quicktime,video/webm"
                    onChange={(event) => handleLibraryImport(event.target.files)}
                  />
                </label>
                <button className="chip" onClick={refreshLibrary}>
                  Refresh
                </button>
                <button
                  className="chip"
                  onClick={() => {
                    setLibrarySelected([])
                    setLibraryLocked([])
                  }}
                >
                  Clear selection
                </button>
              </div>
              <p className="muted">
                Selected {librarySelected.length} of {libraryItems.length} clips
                (max 20).
              </p>
              <div className="library-path">
                <input
                  type="text"
                  value={importPath}
                  onChange={(event) => setImportPath(event.target.value)}
                  placeholder="/Users/you/GlassesDownloads"
                />
                <button className="chip" onClick={handleLibraryImportPath}>
                  Import folder
                </button>
              </div>
              {libraryMessage && <p className="note">{libraryMessage}</p>}
              {libraryError && <p className="error">{libraryError}</p>}
            </div>
            <label
              className="dropzone"
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault()
                handleSongFile(event.dataTransfer.files)
              }}
            >
              <input
                type="file"
                accept="audio/mpeg,audio/mp4,audio/wav"
                onChange={(event) => handleSongFile(event.target.files)}
              />
              <div>
                <h3>Music Track</h3>
                <p>{song ? song.name : 'Drop your song or click to select'}</p>
              </div>
            </label>
          </div>
        )}
        {clipSource === 'local' ? (
          <div className="asset-list">
            {clips.length === 0 ? (
              <p className="muted">No clips yet.</p>
            ) : (
              clips.map((clip) => (
                <div className="asset-item" key={clip.id}>
                  <div>
                    <strong>{clip.file.name}</strong>
                    <span>{formatSize(clip.file.size)}</span>
                  </div>
                  <button
                    className={clip.locked ? 'chip active' : 'chip'}
                    onClick={() =>
                      setClips((current) =>
                        current.map((item) =>
                          item.id === clip.id
                            ? { ...item, locked: !item.locked }
                            : item,
                        ),
                      )
                    }
                  >
                    {clip.locked ? 'Locked' : 'Lock clip'}
                  </button>
                </div>
              ))
            )}
          </div>
        ) : (
          <div className="asset-list">
            {libraryItems.length === 0 ? (
              <p className="muted">No glasses clips yet. Import to get started.</p>
            ) : (
              libraryItems.map((item) => {
                const selected = librarySelected.includes(item.name)
                const locked = libraryLocked.includes(item.name)
                return (
                  <div
                    className={selected ? 'asset-item selected' : 'asset-item'}
                    key={item.name}
                  >
                    <div>
                      <strong>{item.name}</strong>
                      <span>
                        {formatSize(item.size)} • {formatDate(item.modified_at)}
                      </span>
                    </div>
                    <div className="asset-actions">
                      <button
                        className={selected ? 'chip active' : 'chip'}
                        onClick={() => toggleLibrarySelected(item.name)}
                      >
                        {selected ? 'Selected' : 'Use clip'}
                      </button>
                      <button
                        className={locked ? 'chip active' : 'chip'}
                        onClick={() => toggleLibraryLocked(item.name)}
                      >
                        {locked ? 'Locked' : 'Lock clip'}
                      </button>
                    </div>
                  </div>
                )
              })
            )}
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2>2. Choose your settings</h2>
          <p>Dial in vibe, length, and VHS intensity.</p>
        </div>
        <div className="settings-grid">
          <div className="setting">
            <label>Target length</label>
            <div className="segmented">
              {[15, 30].map((length) => (
                <button
                  key={length}
                  className={length === targetLength ? 'active' : ''}
                  onClick={() => setTargetLength(length)}
                >
                  {length}s
                </button>
              ))}
            </div>
          </div>
          <div className="setting">
            <label>Vibe preset</label>
            <div className="segmented">
              {(['hype', 'chill', 'chaotic'] as const).map((preset) => (
                <button
                  key={preset}
                  className={preset === vibe ? 'active' : ''}
                  onClick={() => setVibe(preset)}
                >
                  {preset}
                </button>
              ))}
            </div>
          </div>
          <div className="setting">
            <label>VHS intensity</label>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={vhsIntensity}
              onChange={(event) => setVhsIntensity(Number(event.target.value))}
            />
            <span className="value">{vhsIntensity.toFixed(2)}</span>
          </div>
          <div className="setting">
            <label>Glitch amount</label>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={glitchAmount}
              onChange={(event) => setGlitchAmount(Number(event.target.value))}
            />
            <span className="value">{glitchAmount.toFixed(2)}</span>
          </div>
          <div className="setting">
            <label>Original clip audio</label>
            <button
              className={includeClipAudio ? 'toggle active' : 'toggle'}
              onClick={() => setIncludeClipAudio((value) => !value)}
            >
              {includeClipAudio ? 'On' : 'Off'}
            </button>
          </div>
          <div className="setting">
            <label>Output resolution</label>
            <select
              value={resolution}
              onChange={(event) =>
                setResolution(event.target.value as '1080x1920' | '1280x960' | '1360x1824')
              }
            >
              <option value="1080x1920">1080×1920 (Stories)</option>
              <option value="1280x960">1280×960 (Landscape)</option>
              <option value="1360x1824">1360×1824 (Meta Glasses)</option>
            </select>
          </div>
          <div className="setting">
            <label>VHS preset</label>
            <select
              value={ntscPreset}
              onChange={(event) =>
                setNtscPreset(
                  event.target.value as
                    | 'custom'
                    | 'noisy'
                    | 'semi-sharp'
                    | 'game-tape'
                    | 'dynamic',
                )
              }
            >
              <option value="custom">Custom (latest)</option>
              <option value="noisy">Noisy tape</option>
              <option value="semi-sharp">Semi-sharp</option>
              <option value="game-tape">Game tape</option>
              <option value="dynamic">Dynamic mix</option>
            </select>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2>3. Generate</h2>
          <p>Kick off the local render and monitor progress.</p>
        </div>
        <div className="generate-row">
          <button className="primary" onClick={handleGenerate}>
            Generate Reel
          </button>
          <button className="ghost" onClick={handleRegenerate}>
            Regenerate
          </button>
          {error && <span className="error">{error}</span>}
        </div>
        <div className="progress">
          <div>
            <strong>Status</strong>
            <span>{jobStatus?.status ?? 'Idle'}</span>
          </div>
          <div>
            <strong>Step</strong>
            <span>{jobStatus?.step ?? '-'}</span>
          </div>
          <div>
            <strong>Progress</strong>
            <span>
              {jobStatus?.progress !== undefined
                ? `${Math.round(jobStatus.progress * 100)}%`
                : '0%'}
            </span>
          </div>
          <div>
            <strong>Message</strong>
            <span>{jobStatus?.message ?? '-'}</span>
          </div>
        </div>
        <div className="preview">
          {previewUrl ? (
            <video controls src={previewUrl} />
          ) : (
            <div className="preview-placeholder">Preview will appear here.</div>
          )}
          <div className="preview-actions">
            <button className="ghost" onClick={handleShowEdl}>
              Show Timeline JSON
            </button>
            {finalUrl && (
              <a className="primary" href={finalUrl} download>
                Download MP4
              </a>
            )}
          </div>
        </div>
        {edlText && (
          <pre className="edl">{edlText}</pre>
        )}
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2>Change log</h2>
          <p>Latest local commits so you know which build you are testing.</p>
        </div>
        {versionInfo?.git?.recent && versionInfo.git.recent.length > 0 ? (
          <div className="changelog">
            {versionInfo.git.recent.map((entry) => (
              <div className="changelog-item" key={entry.commit}>
                <strong>{entry.message}</strong>
                <span>
                  {entry.commit} • {new Date(entry.date).toLocaleString()}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="muted">No changelog available.</p>
        )}
      </section>
    </div>
  )
}

export default App
