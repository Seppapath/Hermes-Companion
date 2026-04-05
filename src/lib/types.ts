export type MachineInfo = {
  hostname: string
  osType: string
  arch: string
  currentUser: string
}

export type CompanionSettings = {
  clientId: string
  nodeName: string
  centralName: string
  inviteCodeOrLink: string
  inviteRedeemUrl: string
  registrationUrl: string
  chatHttpUrl: string
  chatWsUrl: string
  statusWsUrl: string
  heartbeatUrl: string
  apiToken: string
  chatModel: string
  centralSshPublicKey: string
  sshAuthorizedUser: string
  heartbeatIntervalSeconds: number
  retryIntervalSeconds: number
}

export type DaemonStatus = {
  state: string
  registered: boolean
  nodeId: string | null
  lastRegistrationAt: string | null
  lastHeartbeatAt: string | null
  lastError: string | null
  daemonVersion: string
  publicKeyPath: string | null
  serviceMode: string
  sshAccessConfigured: boolean
  sshAuthorizedUser: string | null
}

export type ChatResponse = {
  message: string
  rawResponse: Record<string, unknown>
}

export type OperatorBootstrapRequest = {
  centralApiUrl: string
  adminSecret: string
  centralSshPublicKey: string
  sshAuthorizedUser: string
  expiresInMinutes: number
  centralName: string
  chatModel: string
  note: string
}

export type IssuedInvite = {
  inviteId: string
  inviteCode: string
  inviteUrl: string
  expiresAt: string
}

export type OperatorBootstrapResponse = {
  invite: IssuedInvite
  status: DaemonStatus
}

export type ServerSetupRequest = {
  centralHostname: string
  centralName: string
  repoCloneUrl: string
  serverSshTarget: string
  adminSecret: string
  chatModel: string
}

export type ServerSetupBundle = {
  bundleDir: string
  centralUrl: string
  adminSecret: string
  chatBridgeToken: string
  bootstrapScriptPath: string
  uploadScriptPath: string | null
  checklistPath: string
  publicKeyCommand: string
  remoteBootstrapCommand: string
  certbotCommand: string
  validateCommand: string
}

export type RemoteProvisionRequest = {
  bundleDir: string
  serverSshTarget: string
  remoteDir: string
}

export type RemoteProvisionResponse = {
  uploadCommand: string
  bootstrapCommand: string
  transcript: string
}
