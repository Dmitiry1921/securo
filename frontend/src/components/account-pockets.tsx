import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Plus, ArrowLeftRight, Pencil } from 'lucide-react'
import { toast } from 'sonner'
import { goals, pockets } from '@/lib/api'
import type { Pocket } from '@/types'
import { formatCurrency } from '@/lib/format'
import { extractApiError } from '@/lib/api-errors'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useWorkspace } from '@/contexts/workspace-context'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'

export function AccountPockets({ accountId, closed }: { accountId: string; closed: boolean }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const { canWrite } = useWorkspace()
  const qc = useQueryClient()
  const { data, isError } = useQuery({
    queryKey: ['pockets', accountId],
    queryFn: () => pockets.list(accountId),
  })
  const { data: goalList } = useQuery({ queryKey: ['goals'], queryFn: () => goals.list() })
  const [form, setForm] = useState<{ kind: 'create' | 'edit' | 'move'; pocket?: Pocket; key: string } | null>(
    null,
  )
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const mutation = useMutation({
    mutationFn: async (values: FormData) => {
      if (!form) return
      if (form.kind === 'move')
        return pockets.move({
          account_id: accountId,
          from_pocket_id: from || null,
          to_pocket_id: to || null,
          amount: String(values.get('amount')),
          idempotency_key: form.key,
        })
      const payload = {
        name: String(values.get('name')),
        goal_id: String(values.get('goal_id') || '') || null,
      }
      if (form.kind === 'edit')
        return pockets.update(form.pocket!.id, { ...payload, archived: values.get('archived') === 'on' })
      return pockets.create({ ...payload, account_id: accountId })
    },
    onSuccess: () => {
      setForm(null)
      invalidateFinancialQueries(qc)
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
  const open = (kind: 'create' | 'edit' | 'move', pocket?: Pocket) => {
    setFrom('')
    setTo(pocket?.id || '')
    setForm({ kind, pocket, key: crypto.randomUUID() })
  }
  const money = (value: string) => mask(formatCurrency(Number(value), data?.currency || 'USD', locale))
  if (isError)
    return (
      <p role="alert" className="text-sm text-destructive">
        {t('pockets.loadError')}
      </p>
    )
  if (!data) return null
  return (
    <section className="rounded-xl border bg-card p-4 sm:p-6 space-y-4 mb-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">{t('pockets.title')}</h2>
          <p className="text-sm text-muted-foreground">
            {t('pockets.unallocated')}: {money(data.unallocated_available)}
          </p>
        </div>
        {canWrite && !closed && (
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => open('move')}
              disabled={!data.items.some((p) => !p.archived)}
            >
              <ArrowLeftRight size={14} />
              {t('pockets.move')}
            </Button>
            <Button size="sm" onClick={() => open('create')}>
              <Plus size={14} />
              {t('pockets.create')}
            </Button>
          </div>
        )}
      </div>
      {data.needs_review && (
        <p role="alert" className="text-sm text-destructive">
          {t('pockets.review')}
        </p>
      )}
      {!data.items.length && <p className="text-sm text-muted-foreground">{t('pockets.empty')}</p>}
      <div className="divide-y">
        {data.items.map((p) => (
          <div
            key={p.id}
            className={`flex items-center justify-between gap-3 py-3 ${p.archived ? 'opacity-50' : ''}`}
          >
            <div className="min-w-0">
              <p className="font-medium truncate">
                {p.name}
                {p.archived && ` · ${t('pockets.archived')}`}
              </p>
              {p.goal_id && (
                <p className="text-xs text-muted-foreground">
                  {goalList?.find((g) => g.id === p.goal_id)?.name}
                </p>
              )}
              {Number(p.reserved) > 0 && (
                <p className="text-xs text-muted-foreground">
                  {t('pockets.reserved')}: {money(p.reserved)}
                </p>
              )}
              {p.needs_review && <p className="text-xs text-destructive">{t('pockets.review')}</p>}
            </div>
            <div className="flex items-center gap-2">
              <span className={`tabular-nums ${Number(p.available) < 0 ? 'text-destructive' : ''}`}>
                {p.needs_review ? '—' : money(p.available)}
              </span>
              {canWrite && !closed && (
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={t('common.edit')}
                  onClick={() => open('edit', p)}
                >
                  <Pencil size={14} />
                </Button>
              )}
            </div>
          </div>
        ))}
      </div>
      <Dialog
        open={!!form}
        onOpenChange={(open) => {
          if (!open) setForm(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {t(
                form?.kind === 'move'
                  ? 'pockets.move'
                  : form?.kind === 'edit'
                    ? 'pockets.edit'
                    : 'pockets.create',
              )}
            </DialogTitle>
          </DialogHeader>
          <form
            key={form?.key}
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault()
              mutation.mutate(new FormData(e.currentTarget))
            }}
          >
            {form?.kind === 'move' ? (
              <>
                <p className="text-sm text-muted-foreground">{t('pockets.moveHelp')}</p>
                {(['from', 'to'] as const).map((side) => (
                  <div key={side} className="space-y-2">
                    <Label htmlFor={`move-${side}`}>{t(`pockets.${side}`)}</Label>
                    <select
                      id={`move-${side}`}
                      className="w-full border border-input rounded-md px-3 py-2 bg-card"
                      value={side === 'from' ? from : to}
                      onChange={(e) => (side === 'from' ? setFrom : setTo)(e.target.value)}
                    >
                      <option value="">{t('pockets.unallocated')}</option>
                      {data.items
                        .filter((p) => !p.archived)
                        .map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.name}
                          </option>
                        ))}
                    </select>
                  </div>
                ))}
                <Label htmlFor="move-amount">
                  {t('transactions.amount')} ({data.currency})
                </Label>
                <Input id="move-amount" name="amount" type="number" min="0.01" step="0.01" required />
              </>
            ) : (
              <>
                <Label htmlFor="pocket-name">{t('common.name')}</Label>
                <Input
                  id="pocket-name"
                  name="name"
                  defaultValue={form?.pocket?.name}
                  maxLength={255}
                  required
                />
                <Label htmlFor="pocket-goal">{t('pockets.goal')}</Label>
                <select
                  id="pocket-goal"
                  name="goal_id"
                  defaultValue={form?.pocket?.goal_id || ''}
                  className="w-full border border-input rounded-md px-3 py-2 bg-card"
                >
                  <option value="">{t('pockets.noGoal')}</option>
                  {goalList
                    ?.filter((g) => g.tracking_type === 'pockets')
                    .map((g) => (
                      <option key={g.id} value={g.id}>
                        {g.name}
                      </option>
                    ))}
                </select>
                <p className="text-xs text-muted-foreground">{t('pockets.goalHelp')}</p>
                {form?.kind === 'edit' && (
                  <label className="flex gap-2 text-sm">
                    <input type="checkbox" name="archived" defaultChecked={form.pocket?.archived} />
                    {t('pockets.archived')}
                  </label>
                )}
              </>
            )}
            <DialogFooter>
              <Button type="submit" disabled={mutation.isPending || (form?.kind === 'move' && from === to)}>
                {t('common.save')}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </section>
  )
}
