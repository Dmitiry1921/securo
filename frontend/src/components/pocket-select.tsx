import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { pockets } from '@/lib/api'
import { Label } from '@/components/ui/label'

export function PocketSelect({
  accountId,
  value,
  onChange,
}: {
  accountId: string
  value: string
  onChange: (value: string) => void
}) {
  const { t } = useTranslation()
  const { data, isError } = useQuery({
    queryKey: ['pockets', accountId],
    queryFn: () => pockets.list(accountId),
    enabled: !!accountId,
  })
  if (!data?.items.length && !value && !isError) return null
  return (
    <div className="space-y-2">
      <Label htmlFor={`pocket-${accountId}`}>{t('pockets.title')}</Label>
      <select
        id={`pocket-${accountId}`}
        className="w-full border border-input rounded-md px-3 py-2 text-sm bg-card"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">{t('pockets.unallocated')}</option>
        {data?.items
          .filter((p) => !p.archived || p.id === value)
          .map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
      </select>
      {isError && <p className="text-sm text-destructive">{t('pockets.loadError')}</p>}
    </div>
  )
}
