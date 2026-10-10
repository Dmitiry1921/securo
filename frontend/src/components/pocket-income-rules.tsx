import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Percent } from 'lucide-react'
import { toast } from 'sonner'
import { pockets } from '@/lib/api'
import { localDateString } from '@/lib/date-utils'
import { extractApiError } from '@/lib/api-errors'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import type { PocketAccount, PocketIncomeRule } from '@/types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { DatePickerInput } from '@/components/ui/date-picker-input'

export function PocketIncomeRules({ data, canWrite }: { data: PocketAccount; canWrite: boolean }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const today = localDateString()
  const [creating, setCreating] = useState<string | null>(null)
  const [start, setStart] = useState(today)
  const [stopping, setStopping] = useState<PocketIncomeRule | null>(null)
  const refresh = () => { invalidateFinancialQueries(qc); setCreating(null); setStopping(null) }
  const create = useMutation({
    mutationFn: (form: FormData) => pockets.createIncomeRule({
      id: creating!, account_id: data.account_id, pocket_id: String(form.get('pocket')),
      percentage: String(form.get('percentage')), start_date: start, end_date: null,
    }),
    onSuccess: refresh,
    onError: error => toast.error(extractApiError(error)),
  })
  const stop = useMutation({
    mutationFn: () => pockets.stopIncomeRule(stopping!.id, stopping!.start_date > today ? stopping!.start_date : today),
    onSuccess: refresh,
    onError: error => toast.error(extractApiError(error)),
  })
  return <div className="border-t pt-4 space-y-3">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="text-sm font-medium">{t('pockets.incomeRules.title')}</h3>
      {canWrite && data.items.some(p => !p.archived) && <Button size="sm" variant="outline" onClick={() => { setStart(today); setCreating(crypto.randomUUID()) }}><Percent size={14} />{t('pockets.incomeRules.add')}</Button>}
    </div>
    <p className="text-xs text-muted-foreground">{t('pockets.incomeRules.help')}</p>
    {(data.income_rules ?? []).map(rule => <div key={rule.id} className="flex flex-wrap justify-between items-center gap-2 text-sm">
      <div><p>{Number(rule.percentage)}% → {data.items.find(p => p.id === rule.pocket_id)?.name}</p><p className="text-xs text-muted-foreground">{rule.start_date}{rule.end_date && ` — ${rule.end_date}`}</p></div>
      {canWrite && !rule.end_date && <Button size="sm" variant="ghost" onClick={() => setStopping(rule)}>{t('pockets.incomeRules.stop')}</Button>}
    </div>)}
    <Dialog open={creating !== null} onOpenChange={open => { if (!open) setCreating(null) }}><DialogContent><DialogHeader><DialogTitle>{t('pockets.incomeRules.add')}</DialogTitle></DialogHeader>
      <form className="space-y-4" onSubmit={e => { e.preventDefault(); create.mutate(new FormData(e.currentTarget)) }}>
        <div className="space-y-2"><Label htmlFor="income-pocket">{t('pockets.to')}</Label><select id="income-pocket" name="pocket" required className="w-full rounded-md border border-input bg-card px-3 py-2">{data.items.filter(p => !p.archived).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></div>
        <div className="space-y-2"><Label htmlFor="income-percentage">{t('pockets.incomeRules.percentage')}</Label><Input id="income-percentage" name="percentage" type="number" min="0.01" max="100" step="0.01" defaultValue="10" required /></div>
        <div className="space-y-2"><Label>{t('transfers.start')}</Label><DatePickerInput value={start} onChange={setStart} /></div>
        <p className="text-xs text-muted-foreground">{t('pockets.incomeRules.historyHelp')}</p>
        <DialogFooter><Button type="button" variant="outline" onClick={() => setCreating(null)}>{t('common.cancel')}</Button><Button disabled={create.isPending}>{t('common.save')}</Button></DialogFooter>
      </form>
    </DialogContent></Dialog>
    <Dialog open={stopping !== null} onOpenChange={open => { if (!open) setStopping(null) }}><DialogContent><DialogHeader><DialogTitle>{t('pockets.incomeRules.stop')}</DialogTitle></DialogHeader>
      <p className="text-sm text-muted-foreground">{t('pockets.incomeRules.stopHelp', { day: stopping?.start_date && stopping.start_date > today ? stopping.start_date : today })}</p>
      <DialogFooter><Button variant="outline" onClick={() => setStopping(null)}>{t('common.cancel')}</Button><Button disabled={stop.isPending} onClick={() => stop.mutate()}>{t('pockets.incomeRules.stop')}</Button></DialogFooter>
    </DialogContent></Dialog>
  </div>
}
