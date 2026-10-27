import { getAccountLabel } from '@/lib/account-utils'

type BankAccount = {
  name?: string
  display_name?: string | null
  institution_name?: string | null
}

const BANKS = [
  { pattern: /\bpermata(?:bank)?\b/i, logo: '/bank-logos/permata.png' },
  { pattern: /\bmaybank\b/i, logo: '/bank-logos/maybank.png' },
  { pattern: /\bbca\b/i, logo: '/bank-logos/bca.png' },
  { pattern: /\btinkoff\b|\bt-?bank\b|тинькофф|т[ -]?банк/i, logo: '/bank-logos/tbank.jpg' },
  { pattern: /raiffeisen|райфф?айзен/i, logo: '/bank-logos/raiffeisen.svg' },
  { pattern: /\bsber(?:bank)?\b|сбер(?:банк)?/i, logo: '/bank-logos/sber.png' },
]

/** Local marks also cover manual accounts without provider metadata. */
export function getAccountBankBrand(account: BankAccount) {
  for (const name of [account.institution_name, account.name, account.display_name]) {
    const brand = name && BANKS.find(({ pattern }) => pattern.test(name))
    if (brand) return brand
  }
  return null
}

/** Remove only a recognized leading bank name; retain owner and account suffix. */
export function getCompactAccountLabel(account: BankAccount & { name: string; masked_number?: string | null }) {
  const label = getAccountLabel(account)
  const brand = getAccountBankBrand(account)
  const parts = label.match(/^(.+?)\s+[—–-]\s+(.+)$/)
  return brand && parts && brand.pattern.test(parts[1]) ? parts[2] : label
}
