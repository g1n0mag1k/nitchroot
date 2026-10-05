const API_KEY_KEY = 'snitchroot_api_key'
const MERCHANT_ID_KEY = 'snitchroot_merchant_id'

export function getApiKey(): string | null {
  return localStorage.getItem(API_KEY_KEY)
}

export function setApiKey(value: string): void {
  localStorage.setItem(API_KEY_KEY, value)
}

export function getMerchantId(): string | null {
  return localStorage.getItem(MERCHANT_ID_KEY)
}

export function setMerchantId(value: string): void {
  localStorage.setItem(MERCHANT_ID_KEY, value)
}

export function clearAuth(): void {
  localStorage.removeItem(API_KEY_KEY)
}

export function isAuthenticated(): boolean {
  return Boolean(getApiKey())
}
