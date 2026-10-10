import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Plus, Pencil } from 'lucide-react'
import { transferSchedules, accounts } from '@/lib/api'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { extractApiError } from '@/lib/api-errors'
import { getAccountLabel } from '@/lib/account-utils'
import { localDateString } from '@/lib/date-utils'
import { useWorkspace } from '@/contexts/workspace-context'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { formatCurrency } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import { PocketSelect } from '@/components/pocket-select'
import type { TransferSchedule } from '@/types'

interface PendingPair {
  id: string
  debit: { amount: number; currency: string; date: string; account: string; description: string }
  credit: { amount: number; currency: string; date: string; account: string; description: string }
}
const control = 'w-full rounded-md border border-border bg-card px-3 py-2 text-sm'

export function TransferSchedules() {
  const { t } = useTranslation()
  const { canWrite } = useWorkspace()
  const { mask } = usePrivacyMode()
  const locale = useDisplayLocale()
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<Partial<TransferSchedule> | null>(null)
  const [confirming, setConfirming] = useState<PendingPair | null>(null)
  const { data: plans = [] } = useQuery({ queryKey: ['transferSchedules'], queryFn: transferSchedules.list })
  const { data: accountList = [] } = useQuery({ queryKey: ['accounts'], queryFn: () => accounts.list() })
  const { data: pending = [] } = useQuery({ queryKey: ['pendingTransfers'], queryFn: transferSchedules.pending })
  const refresh = () => {
    invalidateFinancialQueries(queryClient)
    queryClient.invalidateQueries({ queryKey: ['transferSchedules'] })
    queryClient.invalidateQueries({ queryKey: ['pendingTransfers'] })
  }
  const save = useMutation({
    mutationFn: ({ id, ...data }: TransferSchedule) => id ? transferSchedules.update(id, data) : transferSchedules.create(data),
    onSuccess: () => { refresh(); setEditing(null) },
    onError: e => toast.error(extractApiError(e, t('common.error'))),
  })
  const confirm = useMutation({
    mutationFn: (data: { amount: number; destination_amount: number; date: string }) => transferSchedules.confirm(confirming!.id, data),
    onSuccess: () => { refresh(); setConfirming(null) },
    onError: e => toast.error(extractApiError(e, t('common.error'))),
  })
  return <section className="mt-6 rounded-xl border border-border bg-card shadow-sm overflow-hidden">
    <div className="px-4 sm:px-5 py-4 border-b border-border flex justify-between items-center gap-2">
      <h2 className="text-sm font-semibold">{t('transfers.title')}</h2>
      {canWrite && <Button size="sm" onClick={() => setEditing({})}><Plus size={14} />{t('transfers.add')}</Button>}
    </div>
    <p className="px-4 sm:px-5 py-3 text-sm text-muted-foreground">{t('transfers.help')}</p>
    <div className="divide-y divide-border">
      {plans.map(p => <div key={p.id} className="px-4 sm:px-5 py-4 flex flex-wrap justify-between items-center gap-3">
        <div className="space-y-1">
          <p className="text-sm font-medium">{p.description} {!p.is_active && <span className="text-muted-foreground">({t('transfers.paused')})</span>}</p>
          <p className="text-xs text-muted-foreground">{getAccountLabel(accountList.find(a => a.id === p.from_account_id) ?? { name: '' })} → {getAccountLabel(accountList.find(a => a.id === p.to_account_id) ?? { name: '' })}</p>
          <p className="text-xs text-muted-foreground">{p.amount_mode === 'prorated_weekly' ? t('transfers.proratedSummary', { day: p.cycle_day, weekday: t(`transfers.weekdays.${p.weekday}`) }) : t(p.frequency === 'days' ? 'transfers.everyDays' : 'transfers.everyMonths', { count: p.interval, day: p.day_of_month ?? Number(p.start_date.slice(-2)) })}</p>
        </div>
        <div className="flex items-center gap-3"><span className="text-sm font-medium">{mask(formatCurrency(Number(p.amount), p.currency, locale))}{p.amount_mode === 'prorated_weekly' && <span className="text-muted-foreground"> · {t('transfers.monthlyEnvelope')}</span>}</span>{canWrite && <Button variant="ghost" size="icon" aria-label={t('common.edit')} onClick={() => setEditing(p)}><Pencil size={14} /></Button>}</div>
      </div>)}
      {pending.map(p => <div key={p.id} className="px-4 sm:px-5 py-4 flex flex-wrap justify-between items-center gap-3">
        <div><p className="text-sm font-medium">{p.debit.description} · {t('transfers.pending')}</p><p className="text-xs text-muted-foreground">{p.debit.date} · {p.debit.account} → {p.credit.account}</p></div>
        <div className="flex items-center gap-3"><span className="text-sm">{mask(formatCurrency(p.debit.amount, p.debit.currency, locale))} → {mask(formatCurrency(p.credit.amount, p.credit.currency, locale))}</span>{canWrite && <Button variant="outline" size="sm" onClick={() => setConfirming(p)}>{t('transfers.confirm')}</Button>}</div>
      </div>)}
    </div>
    {editing && <ScheduleForm key={editing.id ?? 'new'} initial={editing} accountList={accountList} onClose={() => setEditing(null)} onSave={data => save.mutate(data)} loading={save.isPending} />}
    {confirming && <ConfirmForm key={confirming.id} pair={confirming} onClose={() => setConfirming(null)} onSave={data => confirm.mutate(data)} loading={confirm.isPending} />}
  </section>
}

function ScheduleForm({ initial, accountList, onClose, onSave, loading }: { initial: Partial<TransferSchedule>; accountList: import('@/types').Account[]; onClose: () => void; onSave: (p: TransferSchedule) => void; loading: boolean }) {
  const { t } = useTranslation()
  const [data, setData] = useState<TransferSchedule>({ id: '', from_account_id: '', to_account_id: '', from_pocket_id: null, to_pocket_id: null, description: '', notes: null, amount: 0, currency: 'USD', frequency: 'months', interval: 1, day_of_month: 20, start_date: localDateString(), end_date: null, is_active: true, ...initial })
  const set = (patch: Partial<TransferSchedule>) => setData(p => ({ ...p, ...patch }))
  const currencies = [...new Set(accountList.filter(a => [data.from_account_id, data.to_account_id].includes(a.id)).map(a => a.currency))]
  return <Dialog open onOpenChange={onClose}><DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto"><DialogHeader><DialogTitle>{t('transfers.title')}</DialogTitle></DialogHeader>
    <form className="space-y-4" onSubmit={e => { e.preventDefault(); onSave(data) }}>
      <div className="space-y-2"><Label>{t('transactions.description')}</Label><Input required value={data.description} onChange={e => set({ description: e.target.value })} /></div>
      <div className="grid grid-cols-2 gap-3">{(['from', 'to'] as const).map(side => <div key={side} className="space-y-2"><Label>{t(side === 'from' ? 'transactions.transferFromAccount' : 'transactions.transferToAccount')}</Label>
        <select required className={control} value={data[`${side}_account_id`]} onChange={e => { const a = accountList.find(a => a.id === e.target.value)!; set({ [`${side}_account_id`]: a.id, [`${side}_pocket_id`]: null, currency: a.currency }) }}>
          <option value="">{t('transactions.account')}</option>{accountList.filter(a => !a.is_closed).map(a => <option key={a.id} value={a.id}>{getAccountLabel(a)} ({a.currency})</option>)}
        </select><PocketSelect accountId={data[`${side}_account_id`]} value={data[`${side}_pocket_id`] ?? ''} onChange={id => set({ [`${side}_pocket_id`]: id || null })} /></div>)}</div>
      <div className="space-y-2"><Label htmlFor="transfer-amount-mode">{t('transfers.amountMode')}</Label><select id="transfer-amount-mode" className={control} value={data.amount_mode ?? 'fixed'} onChange={e => set(e.target.value === 'prorated_weekly' ? { amount_mode: 'prorated_weekly', frequency: 'days', interval: 7, day_of_month: null, cycle_day: 5, weekday: 0 } : { amount_mode: 'fixed', cycle_day: null, weekday: null, initial_cycle_amount: null })}><option value="fixed">{t('transfers.fixedAmount')}</option><option value="prorated_weekly">{t('transfers.proratedWeekly')}</option></select></div>
      <div className="grid grid-cols-2 gap-3"><div className="space-y-2"><Label>{t(data.amount_mode === 'prorated_weekly' ? 'transfers.monthlyEnvelope' : 'transactions.amount')}</Label><Input type="number" min="0.01" step="0.01" required value={data.amount || ''} onChange={e => set({ amount: Number(e.target.value) })} /></div><div className="space-y-2"><Label>{t('transfers.fixedCurrency')}</Label><select className={control} required value={data.currency} onChange={e => set({ currency: e.target.value })}>{currencies.map(c => <option key={c}>{c}</option>)}</select></div></div>
      {data.amount_mode === 'prorated_weekly' ? <>
        <div className="grid grid-cols-2 gap-3"><div className="space-y-2"><Label>{t('transfers.cycleDay')}</Label><Input type="number" min="1" max="31" required value={data.cycle_day ?? 5} onChange={e => set({ cycle_day: Number(e.target.value) })} /></div><div className="space-y-2"><Label>{t('transfers.weekday')}</Label><select className={control} value={data.weekday ?? 0} onChange={e => set({ weekday: Number(e.target.value) })}>{Array.from({ length: 7 }, (_, day) => <option key={day} value={day}>{t(`transfers.weekdays.${day}`)}</option>)}</select></div></div>
        <div className="space-y-2"><Label htmlFor="initial-cycle-amount">{t('transfers.initialCycleAmount')}</Label><Input id="initial-cycle-amount" type="number" min="0.01" step="0.01" value={data.initial_cycle_amount ?? ''} onChange={e => set({ initial_cycle_amount: e.target.value === '' ? null : Number(e.target.value) })} /><p className="text-xs text-muted-foreground">{t('transfers.initialCycleHelp')}</p></div>
        <p className="text-xs text-muted-foreground">{t('transfers.proratedHelp')}</p>
      </> : <div className="grid grid-cols-2 gap-3"><div className="space-y-2"><Label>{t('transfers.interval')}</Label><Input type="number" min="1" max="3650" step="1" required value={data.interval} onChange={e => set({ interval: Number(e.target.value) })} /></div><div className="space-y-2"><Label>{t('transfers.unit')}</Label><select className={control} value={data.frequency} onChange={e => set({ frequency: e.target.value as 'days' | 'months', day_of_month: e.target.value === 'days' ? null : 20 })}><option value="days">{t('transfers.days')}</option><option value="months">{t('transfers.months')}</option></select></div></div>}
      {data.frequency === 'months' && <div className="space-y-2"><Label>{t('transfers.day')}</Label><Input type="number" min="1" max="31" step="1" required value={data.day_of_month ?? ''} onChange={e => set({ day_of_month: Number(e.target.value) })} /><p className="text-xs text-muted-foreground">{t('transfers.shortMonth')}</p></div>}
      <div className="grid grid-cols-2 gap-3"><div className="space-y-2"><Label>{t('transfers.start')}</Label><DatePickerInput value={data.start_date} onChange={value => set({ start_date: value })} /></div><div className="space-y-2"><Label>{t('transfers.end')}</Label><Input type="date" value={data.end_date ?? ''} onChange={e => set({ end_date: e.target.value || null })} /></div></div>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={data.is_active} onChange={e => set({ is_active: e.target.checked })} />{t('transfers.active')}</label>
      <DialogFooter><Button type="button" variant="outline" onClick={onClose}>{t('common.cancel')}</Button><Button disabled={loading || data.from_account_id === data.to_account_id}>{t('common.save')}</Button></DialogFooter>
    </form>
  </DialogContent></Dialog>
}

function ConfirmForm({ pair, onClose, onSave, loading }: { pair: PendingPair; onClose: () => void; onSave: (data: { amount: number; destination_amount: number; date: string }) => void; loading: boolean }) {
  const { t } = useTranslation()
  const [amount, setAmount] = useState(pair.debit.amount)
  const [destination, setDestination] = useState(pair.credit.amount)
  const [date, setDate] = useState(localDateString())
  const cross = pair.debit.currency !== pair.credit.currency
  return <Dialog open onOpenChange={onClose}><DialogContent><DialogHeader><DialogTitle>{t('transfers.confirm')}</DialogTitle></DialogHeader><form className="space-y-4" onSubmit={e => { e.preventDefault(); onSave({ amount, destination_amount: cross ? destination : amount, date }) }}>
    <p className="text-sm text-muted-foreground">{t('transfers.confirmHelp')}</p>
    <div className="space-y-2"><Label>{t('transfers.actualSource')} ({pair.debit.currency})</Label><Input type="number" min="0.01" step="0.01" required value={amount} onChange={e => setAmount(Number(e.target.value))} /></div>
    {cross && <div className="space-y-2"><Label>{t('transfers.actualDestination')} ({pair.credit.currency})</Label><Input type="number" min="0.01" step="0.01" required value={destination} onChange={e => setDestination(Number(e.target.value))} /></div>}
    <div className="space-y-2"><Label>{t('transactions.date')}</Label><DatePickerInput value={date} onChange={setDate} /></div>
    <DialogFooter><Button type="button" variant="outline" onClick={onClose}>{t('common.cancel')}</Button><Button disabled={loading}>{t('transfers.confirm')}</Button></DialogFooter>
  </form></DialogContent></Dialog>
}
