import { invoke } from '@tauri-apps/api/core'

import type {
  ChatResponse,
  CompanionSettings,
  DaemonStatus,
  MachineInfo,
  OperatorBootstrapRequest,
  OperatorBootstrapResponse,
  RemoteProvisionRequest,
  RemoteProvisionResponse,
  ServerSetupBundle,
  ServerSetupRequest,
} from './types'

export const getMachineInfo = () => invoke<MachineInfo>('get_machine_info')

export const getSettings = () => invoke<CompanionSettings>('get_settings')

export const getDaemonStatus = () => invoke<DaemonStatus>('get_daemon_status')

export const connectToCentral = (settings: CompanionSettings) =>
  invoke<DaemonStatus>('connect_to_central', { settings })

export const bootstrapOperatorConnect = (request: OperatorBootstrapRequest) =>
  invoke<OperatorBootstrapResponse>('bootstrap_operator_connect', { request })

export const createServerSetupBundle = (request: ServerSetupRequest) =>
  invoke<ServerSetupBundle>('create_server_setup_bundle', { request })

export const provisionServerSetupBundle = (request: RemoteProvisionRequest) =>
  invoke<RemoteProvisionResponse>('provision_server_setup_bundle', { request })

export const sendChat = (message: string) =>
  invoke<ChatResponse>('send_chat_message', {
    request: { message },
  })
