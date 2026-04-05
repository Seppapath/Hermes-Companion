<script lang="ts">
  import { onMount } from 'svelte'

  import {
    bootstrapOperatorConnect,
    connectToCentral,
    createServerSetupBundle,
    getDaemonStatus,
    provisionServerSetupBundle,
    getMachineInfo,
    getSettings,
    sendChat,
  } from './lib/tauri'
  import type {
    CompanionSettings,
    DaemonStatus,
    IssuedInvite,
    MachineInfo,
    OperatorBootstrapRequest,
    ServerSetupBundle,
    ServerSetupRequest,
    RemoteProvisionResponse,
  } from './lib/types'

  type ViewMessage = {
    id: string
    role: 'assistant' | 'user' | 'system'
    content: string
    timestamp: string
  }

  type SetupTrack = 'join' | 'owner' | 'server'

  const emptySettings: CompanionSettings = {
    clientId: '',
    nodeName: '',
    centralName: 'Central Hermes',
    inviteCodeOrLink: '',
    inviteRedeemUrl: '',
    registrationUrl: '',
    chatHttpUrl: '',
    chatWsUrl: '',
    statusWsUrl: '',
    heartbeatUrl: '',
    apiToken: '',
    chatModel: 'gpt-4.1-mini',
    centralSshPublicKey: '',
    sshAuthorizedUser: '',
    heartbeatIntervalSeconds: 60,
    retryIntervalSeconds: 15,
  }

  const emptyBootstrap: OperatorBootstrapRequest = {
    centralApiUrl: '',
    adminSecret: '',
    centralSshPublicKey: '',
    sshAuthorizedUser: '',
    expiresInMinutes: 60,
    centralName: 'Central Hermes',
    chatModel: 'gpt-4.1-mini',
    note: '',
  }

  const emptyServerSetup: ServerSetupRequest = {
    centralHostname: '',
    centralName: 'Central Hermes',
    repoCloneUrl: '',
    serverSshTarget: '',
    adminSecret: '',
    chatModel: 'gpt-5.4-mini',
  }

  let machine: MachineInfo | null = $state(null)
  let settings: CompanionSettings = $state({ ...emptySettings })
  let bootstrap: OperatorBootstrapRequest = $state({ ...emptyBootstrap })
  let serverSetup: ServerSetupRequest = $state({ ...emptyServerSetup })
  let daemonStatus: DaemonStatus | null = $state(null)
  let latestInvite: IssuedInvite | null = $state(null)
  let latestBundle: ServerSetupBundle | null = $state(null)
  let latestProvision: RemoteProvisionResponse | null = $state(null)
  let messages: ViewMessage[] = $state([])
  let draft = $state('')
  let showSettings = $state(false)
  let busyConnecting = $state(false)
  let busyBootstrap = $state(false)
  let busyGeneratingBundle = $state(false)
  let busyProvisioning = $state(false)
  let busySending = $state(false)
  let booting = $state(true)
  let banner = $state('')
  let setupTrack: SetupTrack = $state('join')
  let chatScroller: HTMLDivElement | null = null
  let refreshTimer: number | undefined
  let remoteSetupDir = $state('~/hermes-companion-setup')

  function stamp() {
    return new Date().toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  function pushMessage(role: ViewMessage['role'], content: string) {
    messages = [
      ...messages,
      {
        id: crypto.randomUUID(),
        role,
        content,
        timestamp: stamp(),
      },
    ]
  }

  function errorMessage(error: unknown) {
    return error instanceof Error ? error.message : String(error)
  }

  function inferCentralApiUrl(value: string) {
    const trimmed = value.trim()
    if (!trimmed) return ''

    let normalized = trimmed.replace(/\/+$/, '')
    for (const suffix of [
      '/api/device-invites/redeem',
      '/api/device-invites',
      '/api/register-node',
      '/api/node-heartbeat',
      '/api/health',
      '/api',
    ]) {
      if (normalized.endsWith(suffix)) {
        normalized = normalized.slice(0, -suffix.length)
        break
      }
    }

    return normalized
  }

  function inferCentralHostname(value: string) {
    const normalized = inferCentralApiUrl(value)
    if (!normalized) return ''
    return normalized.replace(/^https?:\/\//, '')
  }

  function bootstrapNote(machineInfo: MachineInfo, nodeName: string) {
    return `Self-enrollment for ${nodeName || machineInfo.hostname}`
  }

  function syncBootstrap(machineInfo: MachineInfo, savedSettings: CompanionSettings) {
    bootstrap = {
      ...emptyBootstrap,
      centralApiUrl: inferCentralApiUrl(savedSettings.inviteRedeemUrl),
      centralSshPublicKey: savedSettings.centralSshPublicKey,
      sshAuthorizedUser: savedSettings.sshAuthorizedUser || machineInfo.currentUser,
      centralName: savedSettings.centralName || emptyBootstrap.centralName,
      chatModel: savedSettings.chatModel || emptyBootstrap.chatModel,
      note: bootstrapNote(machineInfo, savedSettings.nodeName || machineInfo.hostname),
    }
  }

  function syncServerSetup(savedSettings: CompanionSettings) {
    serverSetup = {
      ...emptyServerSetup,
      centralHostname: inferCentralHostname(savedSettings.inviteRedeemUrl),
      centralName: savedSettings.centralName || emptyServerSetup.centralName,
      chatModel: savedSettings.chatModel || emptyServerSetup.chatModel,
    }
  }

  function selectTrack(track: SetupTrack) {
    setupTrack = track
    banner = ''
  }

  function statusTone(status: DaemonStatus | null) {
    if (!status) return 'idle'
    if (status.lastError) return 'error'
    if (status.registered) return 'online'
    if (status.state === 'installing' || status.state === 'registering') return 'working'
    return 'idle'
  }

  function statusLabel(status: DaemonStatus | null) {
    if (!status) return 'Waiting for daemon'
    if (status.lastError) return status.lastError
    if (status.registered) return `Registered as ${status.nodeId ?? 'node'}`
    return status.state.replaceAll('-', ' ')
  }

  async function refreshStatus() {
    try {
      daemonStatus = await getDaemonStatus()
    } catch {
      return
    }
  }

  async function handleConnect() {
    banner = ''
    latestInvite = null

    if (!settings.inviteCodeOrLink.trim() && !settings.registrationUrl.trim()) {
      banner = 'Paste an invite link or open Advanced Settings before connecting.'
      return
    }

    busyConnecting = true

    try {
      daemonStatus = await connectToCentral(settings)
      settings = await getSettings()
      if (machine) {
        syncBootstrap(machine, settings)
      }
      syncServerSetup(settings)
      await refreshStatus()
      showSettings = false
      pushMessage(
        'assistant',
        `${settings.centralName || 'Central Hermes'} accepted the local install plan. The daemon is now installing, authorizing SSH access, and registering this node.`,
      )
    } catch (error) {
      const message = errorMessage(error)
      try {
        settings = await getSettings()
      } catch {
        // Keep the in-memory values if the persisted settings are unavailable.
      }
      banner = message
      pushMessage('system', message)
    } finally {
      busyConnecting = false
    }
  }

  async function handleBootstrapConnect() {
    if (!machine || busyBootstrap) return

    banner = ''
    latestInvite = null

    if (!bootstrap.centralApiUrl.trim()) {
      banner = 'Enter the public central Hermes URL first.'
      return
    }

    if (!bootstrap.adminSecret.trim()) {
      banner = 'Enter the central admin secret before creating an invite.'
      return
    }

    if (!bootstrap.centralSshPublicKey.trim()) {
      banner = 'Paste the central SSH public key before creating an invite.'
      return
    }

    busyBootstrap = true

    try {
      const response = await bootstrapOperatorConnect({
        ...bootstrap,
        sshAuthorizedUser: bootstrap.sshAuthorizedUser.trim() || machine.currentUser,
        centralName: bootstrap.centralName.trim() || settings.centralName || 'Central Hermes',
        chatModel: bootstrap.chatModel.trim() || settings.chatModel || 'gpt-4.1-mini',
        note: bootstrap.note.trim() || bootstrapNote(machine, settings.nodeName || machine.hostname),
      })

      latestInvite = response.invite
      daemonStatus = response.status
      settings = await getSettings()
      syncBootstrap(machine, settings)
      syncServerSetup(settings)
      await refreshStatus()
      pushMessage(
        'assistant',
        `${settings.centralName || 'Central Hermes'} created a one-time invite and used it immediately for this machine. The daemon is now installing, authorizing SSH access, and registering this node.`,
      )
    } catch (error) {
      const message = errorMessage(error)
      banner = message
      pushMessage('system', message)
    } finally {
      bootstrap.adminSecret = ''
      busyBootstrap = false
    }
  }

  async function handleCreateServerBundle() {
    if (busyGeneratingBundle) return

    banner = ''
    latestBundle = null
    latestProvision = null

    if (!serverSetup.centralHostname.trim()) {
      banner = 'Enter the public hostname you want people to use, for example companion.example.com.'
      return
    }

    busyGeneratingBundle = true

    try {
      const bundle = await createServerSetupBundle(serverSetup)
      latestBundle = bundle
      bootstrap.centralApiUrl = bundle.centralUrl
      bootstrap.centralName = serverSetup.centralName.trim() || 'Central Hermes'
      bootstrap.chatModel = serverSetup.chatModel.trim() || bootstrap.chatModel
      bootstrap.adminSecret = bundle.adminSecret
      pushMessage(
        'assistant',
        `Server setup bundle created. Follow the checklist at ${bundle.checklistPath}, deploy the files to your Linux server, then come back here and use I Run Central Hermes with ${bundle.centralUrl}.`,
      )
    } catch (error) {
      const message = errorMessage(error)
      banner = message
      pushMessage('system', message)
    } finally {
      busyGeneratingBundle = false
    }
  }

  async function handleProvisionBundle() {
    if (!latestBundle || busyProvisioning) return

    banner = ''
    latestProvision = null

    if (!serverSetup.serverSshTarget.trim()) {
      banner = 'Enter the server SSH target first, for example ubuntu@203.0.113.10.'
      return
    }

    busyProvisioning = true

    try {
      const response = await provisionServerSetupBundle({
        bundleDir: latestBundle.bundleDir,
        serverSshTarget: serverSetup.serverSshTarget,
        remoteDir: remoteSetupDir,
      })
      latestProvision = response
      pushMessage(
        'assistant',
        `The setup pack was uploaded and the bootstrap script ran on ${serverSetup.serverSshTarget}. The next step is to run certbot on the server, print the central SSH public key, and then use I Run Central Hermes to connect this machine.`,
      )
    } catch (error) {
      const message = errorMessage(error)
      banner = message
      pushMessage('system', message)
    } finally {
      busyProvisioning = false
    }
  }

  async function handleChatSubmit() {
    const message = draft.trim()
    if (!message || busySending) return

    draft = ''
    pushMessage('user', message)
    busySending = true

    try {
      const response = await sendChat(message)
      pushMessage('assistant', response.message)
    } catch (error) {
      pushMessage('system', errorMessage(error))
    } finally {
      busySending = false
    }
  }

  function handleComposerKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void handleChatSubmit()
    }
  }

  $effect(() => {
    messages.length
    queueMicrotask(() => {
      chatScroller?.scrollTo({
        top: chatScroller.scrollHeight,
        behavior: 'smooth',
      })
    })
  })

  onMount(() => {
    void (async () => {
      try {
        const [machineInfo, savedSettings, savedStatus] = await Promise.all([
          getMachineInfo(),
          getSettings(),
          getDaemonStatus(),
        ])

        machine = machineInfo
        settings = {
          ...emptySettings,
          ...savedSettings,
          nodeName: savedSettings.nodeName || machineInfo.hostname,
        }
        syncBootstrap(machineInfo, settings)
        syncServerSetup(settings)
        daemonStatus = savedStatus

        pushMessage(
          'assistant',
          'Choose the setup path that matches your situation. You can join an existing Hermes, create your own server setup pack, or connect as the owner of a running central Hermes.',
        )
      } catch (error) {
        banner = errorMessage(error)
      } finally {
        booting = false
      }
    })()

    refreshTimer = window.setInterval(() => {
      void refreshStatus()
    }, 5000)

    return () => {
      if (refreshTimer) {
        window.clearInterval(refreshTimer)
      }
    }
  })
</script>

<svelte:head>
  <title>Hermes Companion</title>
  <meta
    name="description"
    content="Self-installing desktop node for a personal Hermes network."
  />
</svelte:head>

<main class="shell">
  <section class="frame">
    <header class="topbar">
      <div>
        <p class="eyebrow">Hermes Companion</p>
        <h1>{machine?.hostname ?? 'Loading machine name...'}</h1>
      </div>

      <div class="topbar-actions">
        <span class={`status-pill ${statusTone(daemonStatus)}`}>{statusLabel(daemonStatus)}</span>
        <button class="secondary" type="button" onclick={() => (showSettings = !showSettings)}>
          {showSettings ? 'Hide Advanced' : 'Advanced Settings'}
        </button>
      </div>
    </header>

    <section class="wizard-card">
      <div class="card-heading">
        <div>
          <p class="eyebrow">Setup Wizard</p>
          <h2>What Are You Trying To Do?</h2>
        </div>
        <p class="section-copy">
          Pick the path that matches you. Hermes Companion will guide the next steps in plain English.
        </p>
      </div>

      <div class="track-grid">
        <button
          class={`track-button ${setupTrack === 'join' ? 'active' : ''}`}
          type="button"
          onclick={() => selectTrack('join')}
        >
          <strong>Join An Existing Hermes</strong>
          <span>I already have an invite link or code.</span>
        </button>

        <button
          class={`track-button ${setupTrack === 'owner' ? 'active' : ''}`}
          type="button"
          onclick={() => selectTrack('owner')}
        >
          <strong>I Run Central Hermes</strong>
          <span>I already have a live server, admin secret, and SSH public key.</span>
        </button>

        <button
          class={`track-button ${setupTrack === 'server' ? 'active' : ''}`}
          type="button"
          onclick={() => selectTrack('server')}
        >
          <strong>Create My Hermes Server</strong>
          <span>Generate a deployment pack and a checklist for a fresh Linux server.</span>
        </button>
      </div>
    </section>

    {#if setupTrack === 'join'}
      <section class="invite-card">
        <div class="card-heading compact">
          <div>
            <p class="eyebrow">Join Existing</p>
            <h2>Paste Your Invite</h2>
          </div>
          <p class="section-copy">
            This is the easiest path. If somebody already gave you a one-time invite, paste it here and press Connect.
          </p>
        </div>

        <label class="invite-field">
          <span>Invite Link Or Code</span>
          <input
            bind:value={settings.inviteCodeOrLink}
            placeholder="https://hermes.example.com/invite?code=..."
          />
        </label>

        <div class="action-row">
          <button class="primary" type="button" disabled={busyConnecting} onclick={handleConnect}>
            {busyConnecting ? 'Connecting...' : 'Connect This Machine'}
          </button>
          <p class="helper-copy">Hermes Companion will redeem the invite, install the daemon, and register the node.</p>
        </div>
      </section>
    {:else if setupTrack === 'owner'}
      <section class="settings-card">
        <div class="card-heading">
          <div>
            <p class="eyebrow">Owner Operator</p>
            <h2>Create Invite And Connect</h2>
          </div>
          <p class="section-copy">
            Use this when your central Hermes server already exists. The admin secret stays in memory only and is cleared after use.
          </p>
        </div>

        <div class="settings-grid">
          <label class="wide">
            <span>Central URL</span>
            <input bind:value={bootstrap.centralApiUrl} placeholder="https://companion.example.com" />
          </label>

          <label>
            <span>Admin secret</span>
            <input bind:value={bootstrap.adminSecret} type="password" placeholder="Central admin secret" />
          </label>

          <label>
            <span>Local user to authorize</span>
            <input bind:value={bootstrap.sshAuthorizedUser} placeholder={machine?.currentUser ?? 'local user'} />
          </label>

          <label class="wide">
            <span>Central SSH public key</span>
            <input bind:value={bootstrap.centralSshPublicKey} placeholder="ssh-ed25519 AAAA..." />
          </label>

          <label>
            <span>Central name</span>
            <input bind:value={bootstrap.centralName} placeholder="Central Hermes" />
          </label>

          <label>
            <span>Chat model</span>
            <input bind:value={bootstrap.chatModel} placeholder="gpt-4.1-mini" />
          </label>

          <label>
            <span>Invite expiry (minutes)</span>
            <input bind:value={bootstrap.expiresInMinutes} min="5" max="10080" type="number" />
          </label>

          <label class="wide">
            <span>Note</span>
            <input
              bind:value={bootstrap.note}
              placeholder={machine ? bootstrapNote(machine, settings.nodeName || machine.hostname) : 'Self-enrollment for this machine'}
            />
          </label>
        </div>

        <div class="action-row">
          <button class="primary" type="button" disabled={busyBootstrap} onclick={handleBootstrapConnect}>
            {busyBootstrap ? 'Creating Invite...' : 'Create Invite + Connect'}
          </button>
          <p class="helper-copy">
            Recommended for owner-operators. This uses the public control plane API directly from this machine.
          </p>
        </div>

        {#if latestInvite}
          <div class="summary-card">
            <p class="eyebrow">Last Invite</p>
            <p class="summary-copy">
              Invite <strong>{latestInvite.inviteId}</strong> was created for this machine and expires at {new Date(latestInvite.expiresAt).toLocaleString()}.
            </p>
          </div>
        {/if}
      </section>
    {:else}
      <section class="settings-card">
        <div class="card-heading">
          <div>
            <p class="eyebrow">Create Server</p>
            <h2>Generate A Server Setup Pack</h2>
          </div>
          <p class="section-copy">
            This creates a local deployment bundle with production-oriented config files, a bootstrap script, and a plain-English checklist for your Linux server.
          </p>
        </div>

        <div class="settings-grid">
          <label class="wide">
            <span>Public hostname</span>
            <input bind:value={serverSetup.centralHostname} placeholder="companion.example.com" />
          </label>

          <label>
            <span>Central name</span>
            <input bind:value={serverSetup.centralName} placeholder="Central Hermes" />
          </label>

          <label>
            <span>Default chat model</span>
            <input bind:value={serverSetup.chatModel} placeholder="gpt-5.4-mini" />
          </label>

          <label class="wide">
            <span>Repo clone URL</span>
            <input bind:value={serverSetup.repoCloneUrl} placeholder="https://github.com/your-org/hermes-companion.git" />
          </label>

          <label class="wide">
            <span>Server SSH target</span>
            <input bind:value={serverSetup.serverSshTarget} placeholder="ubuntu@203.0.113.10" />
          </label>

          <label class="wide">
            <span>Remote setup folder</span>
            <input bind:value={remoteSetupDir} placeholder="~/hermes-companion-setup" />
          </label>

          <label class="wide">
            <span>Admin secret</span>
            <input bind:value={serverSetup.adminSecret} type="password" placeholder="Leave blank to generate a strong secret for you" />
          </label>
        </div>

        <div class="action-row">
          <button class="primary" type="button" disabled={busyGeneratingBundle} onclick={handleCreateServerBundle}>
            {busyGeneratingBundle ? 'Generating...' : 'Generate Server Setup Pack'}
          </button>
          {#if latestBundle}
            <button class="secondary" type="button" disabled={busyProvisioning} onclick={handleProvisionBundle}>
              {busyProvisioning ? 'Provisioning...' : 'Upload + Run On Server'}
            </button>
          {/if}
          <p class="helper-copy">
            The generated files stay local to this machine until you choose to copy them to your server.
          </p>
        </div>

        {#if latestBundle}
          <div class="summary-card">
            <p class="eyebrow">Generated Bundle</p>
            <p class="summary-copy">
              Bundle folder: <span class="mono-line">{latestBundle.bundleDir}</span>
            </p>
            <p class="summary-copy">
              Checklist: <span class="mono-line">{latestBundle.checklistPath}</span>
            </p>
            <p class="summary-copy">
              Bootstrap script: <span class="mono-line">{latestBundle.bootstrapScriptPath}</span>
            </p>
            {#if latestBundle.uploadScriptPath}
              <p class="summary-copy">
                Upload script: <span class="mono-line">{latestBundle.uploadScriptPath}</span>
              </p>
            {/if}
            <p class="summary-copy">
              Central URL: <span class="mono-line">{latestBundle.centralUrl}</span>
            </p>
            <p class="summary-copy">
              Admin secret: <span class="mono-line">{latestBundle.adminSecret}</span>
            </p>
            <p class="summary-copy">
              Remote bootstrap: <span class="mono-line">{latestBundle.remoteBootstrapCommand}</span>
            </p>
            <p class="summary-copy">
              Certbot command: <span class="mono-line">{latestBundle.certbotCommand}</span>
            </p>
            <p class="summary-copy">
              Print the central SSH public key later with: <span class="mono-line">{latestBundle.publicKeyCommand}</span>
            </p>
            <p class="summary-copy">
              Validate the live deployment with: <span class="mono-line">{latestBundle.validateCommand}</span>
            </p>
          </div>
        {/if}

        {#if latestProvision}
          <div class="summary-card">
            <p class="eyebrow">Provision Transcript</p>
            <p class="summary-copy">
              Upload command: <span class="mono-line">{latestProvision.uploadCommand}</span>
            </p>
            <p class="summary-copy">
              Bootstrap command: <span class="mono-line">{latestProvision.bootstrapCommand}</span>
            </p>
            <pre class="transcript">{latestProvision.transcript}</pre>
          </div>
        {/if}
      </section>
    {/if}

    {#if banner}
      <section class="invite-card">
        <p class="banner">{banner}</p>
      </section>
    {/if}

    {#if showSettings}
      <section class="settings-card">
        <div class="card-heading compact">
          <div>
            <p class="eyebrow">Advanced</p>
            <h2>Manual Settings</h2>
          </div>
          <p class="section-copy">
            Use this only when you need to override endpoints or recover from a partial setup.
          </p>
        </div>

        <div class="settings-grid">
          <label>
            <span>Central name</span>
            <input bind:value={settings.centralName} placeholder="Central Hermes" />
          </label>

          <label>
            <span>Node name</span>
            <input bind:value={settings.nodeName} placeholder={machine?.hostname ?? 'remote-node'} />
          </label>

          <label class="wide">
            <span>Invite redeem URL</span>
            <input
              bind:value={settings.inviteRedeemUrl}
              placeholder="https://hermes.example.com/api/device-invites/redeem"
            />
          </label>

          <label class="wide">
            <span>Registration URL</span>
            <input
              bind:value={settings.registrationUrl}
              placeholder="https://hermes.example.com/api/register-node"
            />
          </label>

          <label class="wide">
            <span>Chat HTTP URL</span>
            <input
              bind:value={settings.chatHttpUrl}
              placeholder="https://hermes.example.com/v1/responses"
            />
          </label>

          <label class="wide">
            <span>Status WebSocket URL</span>
            <input bind:value={settings.statusWsUrl} placeholder="wss://hermes.example.com/ws/nodes" />
          </label>

          <label class="wide">
            <span>Chat WebSocket URL</span>
            <input bind:value={settings.chatWsUrl} placeholder="wss://hermes.example.com/ws/chat" />
          </label>

          <label class="wide">
            <span>Heartbeat URL</span>
            <input bind:value={settings.heartbeatUrl} placeholder="https://hermes.example.com/api/node-heartbeat" />
          </label>

          <label>
            <span>Chat model</span>
            <input bind:value={settings.chatModel} placeholder="gpt-4.1-mini" />
          </label>

          <label>
            <span>API token</span>
            <input bind:value={settings.apiToken} type="password" placeholder="Optional bearer token" />
          </label>

          <label class="wide">
            <span>Central SSH public key</span>
            <input bind:value={settings.centralSshPublicKey} placeholder="ssh-ed25519 AAAA..." />
          </label>

          <label>
            <span>SSH authorized user</span>
            <input bind:value={settings.sshAuthorizedUser} placeholder={machine?.currentUser ?? 'local user'} />
          </label>
        </div>
      </section>
    {/if}

    <section class="details">
      <div class="detail-card">
        <span>Platform</span>
        <strong>{machine?.osType ?? 'Unknown'} / {machine?.arch ?? 'Unknown'}</strong>
      </div>

      <div class="detail-card">
        <span>Daemon</span>
        <strong>{daemonStatus?.daemonVersion ?? 'Not installed yet'}</strong>
      </div>

      <div class="detail-card">
        <span>Auto-start mode</span>
        <strong>{daemonStatus?.serviceMode ?? 'Pending'}</strong>
      </div>

      <div class="detail-card">
        <span>SSH access</span>
        <strong>
          {#if daemonStatus?.sshAccessConfigured}
            Ready for {daemonStatus.sshAuthorizedUser ?? 'this user'}
          {:else}
            Not configured yet
          {/if}
        </strong>
      </div>
    </section>

    <section class="chat-card">
      <div class="chat-log" bind:this={chatScroller}>
        {#if booting}
          <div class="message assistant">
            <p>Booting Hermes Companion...</p>
          </div>
        {:else}
          {#each messages as message (message.id)}
            <article class={`message ${message.role}`}>
              <div class="message-meta">
                <span>{message.role}</span>
                <time>{message.timestamp}</time>
              </div>
              <p>{message.content}</p>
            </article>
          {/each}
        {/if}
      </div>

      <form
        class="composer"
        onsubmit={(event) => {
          event.preventDefault()
          void handleChatSubmit()
        }}
      >
        <textarea
          bind:value={draft}
          placeholder="Send a command or question to central Hermes..."
          rows="3"
          onkeydown={handleComposerKeydown}
        ></textarea>
        <button class="primary" type="submit" disabled={busySending || !draft.trim()}>
          {busySending ? 'Sending...' : 'Send'}
        </button>
      </form>
    </section>
  </section>
</main>
