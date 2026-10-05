export type DiscrepancySeverity = 'HIGH' | 'MEDIUM' | 'LOW' | string

export interface Discrepancy {
  detected_at: string
  severity: DiscrepancySeverity
  description: string
  dossier_path?: string | null
  resolved: boolean
}

export interface ChainEntry {
  seq: number
  timestamp: string
  dossier_hash: string
  prev_hash: string
}

export interface ChainResponse {
  verified: boolean
  entries: ChainEntry[]
}

export interface MerchantCreateResponse {
  id: string
  api_key: string
  name?: string
}
