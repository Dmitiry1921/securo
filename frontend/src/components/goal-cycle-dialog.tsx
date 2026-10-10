import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { format } from 'date-fns'
import { goals, categories } from '@/lib/api'
import type { Goal, GoalCompleteInput } from '@/types'
import { formatCurrency } from '@/lib/format'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'

export function GoalCycleDialog({ goal, historyOnly = false, onClose }: {
  goal: Goal; historyOnly?: boolean; onClose: () => void
}) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const qc = useQueryClient()
  const [current, setCurrent] = useState(goal)
  const [mode, setMode] = useState(goal.tracking_type === 'pockets' ? 'payment' : 'none')
  const [pocketId, setPocketId] = useState('')
  const [date, setDate] = useState(format(new Date(), 'yyyy-MM-dd'))
  const completed = current.status === 'completed' || (current.status === 'archived' && current.last_completion?.cycle_number === current.cycle_number)
  const selectClass = 'w-full border border-border rounded-lg px-3 py-2 text-sm bg-card'
  const { data: funding, isError: fundingError } = useQuery({
    queryKey: ['goal-funding', goal.id], queryFn: () => goals.funding(goal.id),
    enabled: !historyOnly && !completed && goal.tracking_type === 'pockets',
  })
  const { data: history } = useQuery({ queryKey: ['goal-history', goal.id], queryFn: () => goals.history(goal.id) })
  const { data: categoryList } = useQuery({ queryKey: ['categories'], queryFn: categories.list, enabled: !historyOnly && !completed && mode === 'payment' })
  const selected = pocketId ? funding?.pockets.find(p => p.id === pocketId) : funding?.pockets[0]
  const done = (result: Goal) => {
    setCurrent(result)
    for (const key of ['goals', 'goal-history', 'goal-funding', 'accounts', 'pockets', 'transactions', 'dashboard', 'recurring-transactions', 'budgets']) {
      qc.invalidateQueries({ queryKey: [key] })
    }
  }
  const error = (err: unknown) => {
    toast.error((err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || t('common.error'))
  }
  const complete = useMutation({ mutationFn: (input: GoalCompleteInput) => goals.complete(goal.id, input), onSuccess: done, onError: error })
  const restart = useMutation({
    mutationFn: () => goals.restart(goal.id, current.cycle_number),
    onSuccess: result => { done(result); onClose() }, onError: error,
  })
  return <Dialog open onOpenChange={open => { if (!open && !complete.isPending && !restart.isPending) onClose() }}>
    <DialogContent className="max-h-[90vh] overflow-y-auto" aria-describedby={undefined}>
      <DialogHeader><DialogTitle>{goal.name}</DialogTitle></DialogHeader>
      {!historyOnly && !completed && <form className="space-y-4" onSubmit={event => {
        event.preventDefault()
        const fields = new FormData(event.currentTarget)
        const input: GoalCompleteInput = { cycle_number: current.cycle_number }
        if (mode === 'payment' && selected) input.payment = {
          pocket_id: selected.id, amount: Number(fields.get('amount')), date,
          category_id: String(fields.get('category_id') || '') || undefined,
        }
        if (mode === 'existing') input.transaction_id = String(fields.get('transaction_id'))
        complete.mutate(input)
      }}>
        <p className="text-sm text-muted-foreground">{t('goals.cycles.completeHint')}</p>
        <Label htmlFor="goal-payment-mode">{t('goals.cycles.paymentMode')}</Label>
        <select id="goal-payment-mode" className={selectClass} value={mode} onChange={event => setMode(event.target.value)}>
          {goal.tracking_type === 'pockets' && <>
            <option value="payment">{t('goals.cycles.recordPayment')}</option>
            <option value="existing">{t('goals.cycles.existingPayment')}</option>
          </>}
          <option value="none">{t('goals.cycles.noPayment')}</option>
        </select>
        {fundingError && <p className="text-sm text-destructive">{t('common.error')}</p>}
        {mode === 'payment' && <>
          <Label>{t('pockets.title')}</Label>
          <select className={selectClass} value={selected?.id || ''} onChange={event => setPocketId(event.target.value)} required>
            <option value="" disabled>{t('goals.cycles.choosePocket')}</option>
            {funding?.pockets.map(p => <option key={p.id} value={p.id}>{p.account_name} · {p.name} · {mask(formatCurrency(Number(p.available), p.currency, locale))}</option>)}
          </select>
          {!funding?.pockets.length && <p className="text-sm text-muted-foreground">{t('goals.cycles.linkPocket')}</p>}
          {selected && <>
            <Label htmlFor="goal-payment-amount">{t('goals.cycles.paidAmount', { currency: selected.currency })}</Label>
            <Input id="goal-payment-amount" key={selected.id} name="amount" type="number" min="0.01" step="0.01" max={Math.max(0, Number(selected.available))}
              defaultValue={selected.currency === goal.currency ? Math.max(0, Math.min(Number(goal.target_amount), Number(selected.available))) : ''} required />
            <Label>{t('goals.cycles.paymentDate')}</Label>
            <DatePickerInput value={date} onChange={setDate} />
            <Label htmlFor="goal-payment-category">{t('transactions.category')}</Label>
            <select id="goal-payment-category" name="category_id" className={selectClass}>
              <option value="">{t('goals.cycles.uncategorized')}</option>
              {categoryList?.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </>}
        </>}
        {mode === 'existing' && <>
          <p className="text-sm text-muted-foreground">{t('goals.cycles.existingHint')}</p>
          <select name="transaction_id" className={selectClass} required defaultValue="">
            <option value="" disabled>{t('goals.cycles.choosePayment')}</option>
            {funding?.payments.map(tx => <option key={tx.id} value={tx.id}>{tx.date} · {tx.description} · {mask(formatCurrency(Number(tx.amount), tx.currency, locale))}</option>)}
          </select>
        </>}
        {mode === 'none' && <p className="text-sm text-muted-foreground">{t('goals.cycles.noPaymentHint')}</p>}
        <Button type="submit" disabled={complete.isPending || (mode === 'payment' && (!selected || selected.needs_review || Number(selected.available) <= 0)) || (mode === 'existing' && !funding?.payments.length)}>
          {mode === 'payment' ? t('goals.cycles.payAndComplete') : t('goals.complete')}
        </Button>
      </form>}
      {!historyOnly && completed && <div className="space-y-3">
        <p>{t('goals.cycles.completed')}</p>
        {current.repeat_frequency && <>
          <p className="text-sm text-muted-foreground">{t('goals.cycles.restartHint', { date: current.next_target_date })}</p>
          <Button onClick={() => restart.mutate()} disabled={restart.isPending}>{t('goals.cycles.restart')}</Button>
        </>}
        <Button variant="outline" onClick={onClose}>{t('common.close')}</Button>
      </div>}
      <div className="space-y-2 border-t pt-4">
        <p className="text-sm font-semibold">{t('goals.cycles.history')}</p>
        {!history?.length && <p className="text-sm text-muted-foreground">{t('goals.cycles.emptyHistory')}</p>}
        {history?.map(cycle => <div key={cycle.id} className="text-sm border-b border-border pb-2">
          <p>{t('goals.cycles.cycle', { count: cycle.cycle_number })} · {cycle.completed_on}</p>
          <p className="text-muted-foreground">{mask(formatCurrency(Number(cycle.saved_amount), cycle.currency, locale))} / {mask(formatCurrency(Number(cycle.target_amount), cycle.currency, locale))}</p>
          <p className="text-xs text-muted-foreground">{cycle.spent_amount != null ? `${t('goals.cycles.paymentRecorded')}: ${mask(formatCurrency(Number(cycle.spent_amount), cycle.spent_currency || cycle.currency, locale))} · ${cycle.spent_on}` : t('goals.cycles.noPayment')}</p>
        </div>)}
      </div>
    </DialogContent>
  </Dialog>
}
