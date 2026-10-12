import { todayInTimezone } from '@/lib/date-utils'
import { useEffectiveTimezone } from '@/hooks/use-timezone'
import { useState } from 'react'
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Settings2, Plus, Pencil, Trash2, ChevronDown } from 'lucide-react'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts'
import { forecast, accounts, categories, budgets, pockets, recurring } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { extractApiError } from '@/lib/api-errors'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useWorkspace } from '@/contexts/workspace-context'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import type { ExpenseForecastRule, ForecastScenario, FundingForecast, ForecastGoal } from '@/types'

function compactCurrency(value: number, currency: string, locale: string) {
  return new Intl.NumberFormat(locale, { style: 'currency', currency, notation: 'compact', maximumFractionDigits: 1 }).format(value)
}

const selectClass = 'w-full rounded-lg border border-border bg-card px-3 py-2 text-sm'

export function GoalForecastDetails({ goal, defaultOpen = false, view = 'disclosure' }: { goal: ForecastGoal; defaultOpen?: boolean; view?: 'summary' | 'chart' | 'disclosure' }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const [open, setOpen] = useState(defaultOpen)
  const value = (n: string | null) => n === null ? t('funding.unknown') : mask(formatCurrency(Number(n), goal.currency, locale))
  const dateLabel = (timestamp: number) => new Intl.DateTimeFormat(locale, { day: 'numeric', month: 'short', year: '2-digit', timeZone: 'UTC' }).format(timestamp)
  const heading = <>
      <span className={`font-medium ${goal.status === 'shortfall' ? 'text-rose-500' : goal.status === 'funded' ? 'text-emerald-500' : 'text-muted-foreground'}`}>{t(`funding.${goal.status}`)}</span>
      <span>{goal.deadline ?? t('funding.noDeadline')} · {value(goal.projected)} / {value(goal.target)}</span>
      {view === 'disclosure' && <span className="inline-flex items-center gap-1 text-primary ml-auto"><ChevronDown aria-hidden="true" className={`h-4 w-4 transition-transform ${open ? 'rotate-180' : ''}`} />{t(open ? 'funding.hideGoalChart' : 'funding.showGoalChart')}</span>}
  </>
  if (view === 'summary') return <div className="mt-3 rounded-lg border border-border bg-card p-3 text-xs flex flex-wrap items-center gap-2">{heading}</div>
  const expanded = view === 'chart' || open
  const large = view === 'chart' || defaultOpen
  const points = goal.history.map(p => ({
    date: Date.parse(`${p.date}T00:00:00Z`),
    amount: p.amount === null ? null : Number(p.amount),
    allocated: p.allocated_amount == null ? null : Number(p.allocated_amount),
  }))
  const fundedPoints = points.filter(p => p.amount !== null).length
  const allocatedPoints = points.filter(p => p.allocated !== null).length
  const hasData = fundedPoints > 0 || allocatedPoints > 0
  const uncertain = points.some(p => p.amount === null)
  const content = <>
    {expanded && <>
      <div className="mt-3 grid grid-cols-2 gap-3">
        <p>{t('funding.shortfall')}: <strong>{value(goal.shortfall)}</strong></p>
        <p>{t('funding.achievedDate')}: <strong>{goal.achieved_date ?? '—'}</strong></p>
      </div>
      {goal.reason && <p className="mt-2 text-amber-600 dark:text-amber-400">{t(`funding.reason.${goal.reason}`)}</p>}
      <p className="mt-2 text-muted-foreground">{t('funding.goalHelp')}</p>
      {uncertain && hasData && <p className="mt-2 text-amber-600 dark:text-amber-400">{t('funding.goalChartUncertain')}</p>}
      {hasData ? <>
        <div className="flex flex-wrap gap-x-4 gap-y-1 mt-3 text-muted-foreground">
          {allocatedPoints > 0 && <span className="inline-flex items-center gap-1"><span className="w-4 border-t-2 border-dashed border-amber-500" />{t('funding.goalPocketPlan')}</span>}
          {fundedPoints > 0 && <span className="inline-flex items-center gap-1"><span className="w-4 border-t-2 border-indigo-400" />{t('funding.goalBackedAmount')}</span>}
          <span className="inline-flex items-center gap-1"><span className="w-4 border-t border-dashed border-slate-400" />{t('goals.targetAmount')}</span>
        </div>
        <div className={large ? 'h-64 mt-3 min-w-0' : 'h-40 mt-3 min-w-0'} aria-label={t('funding.goalChart')}>
          <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 320, height: large ? 256 : 160 }}>
            <LineChart data={points}>
              <XAxis dataKey="date" type="number" scale="time" domain={['dataMin', 'dataMax']} minTickGap={45} tick={{ fontSize: 10 }} tickFormatter={dateLabel} />
              <YAxis tick={{ fontSize: 10 }} tickFormatter={n => mask(compactCurrency(n, goal.currency, locale))} width={85} />
              <Tooltip labelFormatter={n => dateLabel(Number(n))} formatter={n => n == null ? t('funding.unknown') : mask(formatCurrency(Number(n), goal.currency, locale))} contentStyle={{ background: 'var(--color-card)', borderColor: 'var(--color-border)', borderRadius: 8 }} />
              <ReferenceLine ifOverflow="extendDomain" y={Number(goal.target)} stroke="#94a3b8" strokeDasharray="4 4" />
              {allocatedPoints > 0 && <Line type="stepAfter" dataKey="allocated" name={t('funding.goalPocketPlan')} stroke="#f59e0b" strokeDasharray="5 4" dot={allocatedPoints === 1 ? { r: 3 } : false} connectNulls={false} isAnimationActive={false} />}
              {fundedPoints > 0 && <Line type="stepAfter" dataKey="amount" name={t('funding.goalBackedAmount')} stroke="#818cf8" dot={fundedPoints === 1 ? { r: 3 } : false} connectNulls={false} isAnimationActive={false} />}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </> : <p className="mt-3 rounded-lg bg-muted/30 p-4 text-muted-foreground" role="status">{t('funding.goalChartEmpty')}</p>}
      {!!goal.cycles.length && <ul className="mt-2 space-y-1">{goal.cycles.map(c => <li key={c.deadline}>{c.deadline}: {value(c.projected)} · {t('funding.shortfall')}: {value(c.shortfall)}</li>)}</ul>}
    </>}
  </>
  if (view === 'chart') return <section className="mt-3 rounded-lg border border-border bg-card p-3 text-xs"><div className="flex flex-wrap items-center gap-2">{heading}</div>{content}</section>
  return <details open={open} onToggle={e => setOpen(e.currentTarget.open)} className="mt-3 rounded-lg border border-border bg-card p-3 text-xs"><summary className="cursor-pointer list-none flex flex-wrap items-center gap-2">{heading}</summary>{content}</details>
}

function RuleEditor({ rule, done }: { rule: ExpenseForecastRule | null; done: () => void }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const timezone = useEffectiveTimezone()
  const [draft, setDraft] = useState<ExpenseForecastRule>(() => rule ?? {
    id: crypto.randomUUID(), name: '', account_id: '', pocket_id: null, budget_id: null,
    amount: '0', currency: 'USD', category_ids: [], cycle_day: 1, distribution: 'daily', weekday: 0,
    start_date: todayInTimezone(timezone), end_date: null, enabled: true,
  })
  const ac = useQuery({ queryKey: ['accounts'], queryFn: () => accounts.list() })
  const cat = useQuery({ queryKey: ['categories'], queryFn: categories.list })
  const bd = useQuery({ queryKey: ['budgets'], queryFn: () => budgets.list() })
  const pk = useQuery({ queryKey: ['pockets', draft.account_id], queryFn: () => pockets.list(draft.account_id), enabled: !!draft.account_id })
  const save = useMutation({ mutationFn: () => forecast.saveRule(draft), onSuccess: done })
  const update = (p: Partial<ExpenseForecastRule>) => setDraft(d => ({ ...d, ...p }))
  return <form className="space-y-4" onSubmit={e => { e.preventDefault(); save.mutate() }}>
    <Label className="block space-y-1">{t('common.name')}<Input required value={draft.name} onChange={e => update({ name: e.target.value })} /></Label>
    <Label className="block space-y-1">{t('funding.ruleMode')}<select className={selectClass} value={draft.mode ?? 'budget'} onChange={e => update({ mode: e.target.value as ExpenseForecastRule['mode'] })}><option value="budget">{t('funding.budgetMode')}</option><option value="payments_only">{t('funding.paymentMode')}</option></select></Label>
    <div className="grid gap-3 sm:grid-cols-2">
      <Label className="space-y-1">{t('funding.account')}<select required className={selectClass} value={draft.account_id} onChange={e => {
        const a = ac.data?.find(a => a.id === e.target.value)
        update({ account_id: e.target.value, currency: a?.currency ?? 'USD', pocket_id: null, budget_id: null })
      }}><option value="">{t('funding.choose')}</option>{ac.data?.filter(a => !a.is_closed).map(a => <option key={a.id} value={a.id}>{a.display_name || a.name} · {a.currency}</option>)}</select></Label>
      <Label className="space-y-1">{t('funding.pocket')}<select className={selectClass} value={draft.pocket_id ?? ''} onChange={e => update({ pocket_id: e.target.value || null })}>
        <option value="">{t('funding.unallocated')}</option>{pk.data?.items.filter(p => !p.archived).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></Label>
      <Label className="space-y-1">{t('funding.budget')}<select className={selectClass} value={draft.budget_id ?? ''} onChange={e => {
        const budget = bd.data?.find(b => b.id === e.target.value)
        update({ budget_id: budget?.id ?? null, amount: budget ? null : '0', category_ids: budget ? [budget.category_id] : draft.category_ids })
      }}><option value="">{t('funding.customAmount')}</option>{bd.data?.filter(b => b.currency === draft.currency).map(b => <option key={b.id} value={b.id}>{cat.data?.find(c => c.id === b.category_id)?.name} · {formatCurrency(b.amount, b.currency ?? draft.currency, locale)}</option>)}</select></Label>
      <Label className="space-y-1">{t('funding.amount')} ({draft.currency})<Input type="number" min="0" step="0.01" required={!draft.budget_id} disabled={!!draft.budget_id} value={draft.amount ?? ''} onChange={e => update({ amount: e.target.value })} /></Label>
      <Label className="space-y-1">{t('funding.cycleDay')}<Input type="number" min="1" max="31" required value={draft.cycle_day} onChange={e => update({ cycle_day: Number(e.target.value) })} /></Label>
      <Label className="space-y-1">{t('funding.distribution')}<select className={selectClass} value={draft.distribution} onChange={e => update({ distribution: e.target.value as ExpenseForecastRule['distribution'] })}>{['daily', 'weekly', 'period_start'].map(v => <option key={v} value={v}>{t(`funding.${v}`)}</option>)}</select></Label>
      {draft.distribution === 'weekly' && <Label className="space-y-1">{t('funding.weekday')}<select className={selectClass} value={draft.weekday} onChange={e => update({ weekday: Number(e.target.value) })}>{Array.from({ length: 7 }, (_, i) => <option key={i} value={i}>{new Intl.DateTimeFormat(locale, { weekday: 'long' }).format(new Date(2026, 0, 5 + i))}</option>)}</select></Label>}
      <Label className="space-y-1">{t('funding.start')}<Input required type="date" value={draft.start_date} onChange={e => update({ start_date: e.target.value })} /></Label>
      <Label className="space-y-1">{t('funding.end')}<Input type="date" value={draft.end_date ?? ''} onChange={e => update({ end_date: e.target.value || null })} /></Label>
    </div>
    <Label className="block space-y-1">{t('funding.creditKind')}<select className={selectClass} value={draft.credit_kind ?? 'refund'} onChange={e => update({ credit_kind: e.target.value as ExpenseForecastRule['credit_kind'] })}>{['refund', 'reimbursement', 'loan_principal', 'income', 'other'].map(kind => <option key={kind} value={kind}>{t(`funding.cashRole.${kind}`)}</option>)}</select></Label>
    <details className="rounded-lg border border-border p-3"><summary className="text-sm cursor-pointer">{t('funding.actualAccounts')}</summary><p className="text-xs text-muted-foreground my-2">{t('funding.actualAccountsHelp')}</p><div className="grid sm:grid-cols-2 gap-2">{ac.data?.filter(a => !a.is_closed && a.currency === draft.currency).map(a => <label className="flex items-center gap-2 text-xs" key={a.id}><input type="checkbox" checked={(draft.actual_account_ids ?? []).includes(a.id)} onChange={e => update({ actual_account_ids: e.target.checked ? [...(draft.actual_account_ids ?? []), a.id] : (draft.actual_account_ids ?? []).filter(id => id !== a.id) })} />{a.display_name || a.name}</label>)}</div></details>
    <fieldset className="rounded-lg border border-border p-3"><legend className="px-1 text-sm">{t('funding.categories')}</legend>
      <p className="text-xs text-muted-foreground mb-2">{t('funding.matchHelp')}</p>
      <div className="max-h-40 overflow-auto grid gap-2 sm:grid-cols-2">{cat.data?.filter(c => !c.treat_as_transfer || draft.mode === 'payments_only').map(c => <label key={c.id} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={draft.category_ids.includes(c.id)} onChange={e => update({ category_ids: e.target.checked ? [...draft.category_ids, c.id] : draft.category_ids.filter(id => id !== c.id) })} />{c.name}</label>)}</div>
    </fieldset>
    <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={draft.enabled} onChange={e => update({ enabled: e.target.checked })} />{t('funding.enabled')}</label>
    <details className="rounded-lg border border-border p-3"><summary className="text-sm cursor-pointer">{t('funding.historyCoverage')}</summary><p className="mt-2 text-xs text-muted-foreground">{t('funding.historyHelp')}</p><div className="grid sm:grid-cols-2 gap-2 mt-3"><Label>{t('funding.start')}<Input type="date" value={draft.history_from ?? ''} onChange={e => update({ history_from: e.target.value || null })} /></Label><Label>{t('funding.end')}<Input type="date" value={draft.history_through ?? ''} onChange={e => update({ history_through: e.target.value || null })} /></Label></div><label className="flex items-center gap-2 text-xs mt-3"><input type="checkbox" checked={draft.reviewed_history ?? false} onChange={e => update({ reviewed_history: e.target.checked })} />{t('funding.reviewedHistory')}</label></details>
    {(ac.isError || cat.isError || bd.isError || pk.isError) && <p role="alert" className="text-rose-500 text-sm">{t('funding.loadError')}</p>}
    {save.isError && <p role="alert" className="text-rose-500 text-sm">{extractApiError(save.error, t('funding.saveError'))}</p>}
    <div className="flex justify-end"><Button type="submit" disabled={save.isPending || ac.isLoading || cat.isLoading}>{t('common.save')}</Button></div>
  </form>
}

function ScenarioEditor({ scenario, done }: { scenario: ForecastScenario | null; done: () => void }) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState<ForecastScenario>(() => scenario ?? { id: crypto.randomUUID(), name: '', excluded_recurring_ids: [], income_delay_days: 0, income_multiplier: '1', expense_multiplier: '1', fx_rates: {} })
  const plans = useQuery({ queryKey: ['recurring-transactions'], queryFn: recurring.list })
  const ac = useQuery({ queryKey: ['accounts'], queryFn: () => accounts.list() })
  const save = useMutation({ mutationFn: () => forecast.saveScenario(draft), onSuccess: done })
  return <form className="space-y-4" onSubmit={e => { e.preventDefault(); save.mutate() }}>
    <p className="text-xs text-muted-foreground">{t('funding.scenarioHelp')}</p>
    <Label className="block space-y-1">{t('common.name')}<Input required value={draft.name} onChange={e => setDraft(d => ({ ...d, name: e.target.value }))} /></Label>
    <div className="grid gap-3 sm:grid-cols-2">
      <Label className="space-y-1">{t('funding.delayDays')}<Input min="0" max="365" type="number" value={draft.income_delay_days} onChange={e => setDraft(d => ({ ...d, income_delay_days: Number(e.target.value) }))} /></Label>
      {(['income_multiplier', 'expense_multiplier'] as const).map(key => <Label className="space-y-1" key={key}>{t(`funding.${key}`)}<Input min="0" max="10" step="0.01" type="number" required value={draft[key]} onChange={e => setDraft(d => ({ ...d, [key]: e.target.value }))} /></Label>)}
    </div>
    <fieldset><legend className="text-sm mb-2">{t('funding.excludeIncome')}</legend><div className="space-y-2">{plans.data?.filter(p => p.is_active && p.type === 'credit').map(p => <label className="flex items-center gap-2 text-sm" key={p.id}><input type="checkbox" checked={draft.excluded_recurring_ids.includes(p.id)} onChange={e => setDraft(d => ({ ...d, excluded_recurring_ids: e.target.checked ? [...d.excluded_recurring_ids, p.id] : d.excluded_recurring_ids.filter(id => id !== p.id) }))} />{p.description}</label>)}</div></fieldset>
    <fieldset className="space-y-2"><legend className="text-sm">{t('funding.fxOverride')}</legend><p className="text-xs text-muted-foreground">{t('funding.fxHelp')}</p>{[...new Set(ac.data?.map(a => a.currency))].map(currency => <Label className="flex items-center gap-3" key={currency}>{currency}<Input aria-label={`${t('funding.fxOverride')} ${currency}`} type="number" min="0.0000000001" step="any" placeholder={t('funding.savedRate')} value={draft.fx_rates[currency] ?? ''} onChange={e => setDraft(d => { const fx = { ...d.fx_rates }; if (e.target.value) fx[currency] = e.target.value; else delete fx[currency]; return { ...d, fx_rates: fx } })} /></Label>)}</fieldset>
    {save.isError && <p role="alert" className="text-rose-500 text-sm">{extractApiError(save.error, t('funding.saveError'))}</p>}
    <div className="flex justify-end"><Button type="submit" disabled={save.isPending}>{t('common.save')}</Button></div>
  </form>
}

export function ForecastSettings({ onChanged }: { onChanged?: () => void }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { canWrite } = useWorkspace()
  const rules = useQuery({ queryKey: ['forecast-rules'], queryFn: forecast.rules })
  const scenarios = useQuery({ queryKey: ['forecast-scenarios'], queryFn: forecast.scenarios })
  const [editor, setEditor] = useState<{ kind: 'expense_rule'; value: ExpenseForecastRule | null } | { kind: 'scenario'; value: ForecastScenario | null } | null>(null)
  const done = () => { setEditor(null); void queryClient.invalidateQueries({ queryKey: ['forecast-rules'] }); void queryClient.invalidateQueries({ queryKey: ['forecast-scenarios'] }); void queryClient.invalidateQueries({ queryKey: ['funding-forecast'] }); void queryClient.invalidateQueries({ queryKey: ['reports'] }); onChanged?.() }
  const remove = useMutation({ mutationFn: ({ kind, id }: { kind: 'expense_rule' | 'scenario'; id: string }) => forecast.remove(kind, id), onSuccess: done })
  return <details className="rounded-xl border border-border bg-card p-4">
    <summary className="cursor-pointer flex items-center gap-2 text-sm font-medium"><Settings2 className="h-4 w-4" />{t('funding.settings')}</summary>
    <p className="text-xs text-muted-foreground mt-3">{t('funding.settingsHelp')}</p>
    {rules.isLoading || scenarios.isLoading ? <p className="py-3 text-sm">{t('common.loading')}</p> : null}
    {(rules.isError || scenarios.isError) && <p role="alert" className="text-sm text-rose-500">{t('funding.loadError')}</p>}
    {(['expense_rule', 'scenario'] as const).map(kind => <section className="mt-4" key={kind}>
      <div className="flex items-center justify-between gap-2"><h3 className="text-sm font-semibold">{t(`funding.${kind}`)}</h3>{canWrite && <Button size="sm" variant="outline" onClick={() => setEditor(kind === 'expense_rule' ? { kind, value: null } : { kind, value: null })}><Plus className="h-3 w-3 mr-1" />{t('common.add')}</Button>}</div>
      <div className="mt-2 space-y-2">{(kind === 'expense_rule' ? rules.data : scenarios.data)?.map(item => <div key={item.id} className="flex items-center justify-between gap-2 rounded-lg bg-muted/40 p-2 text-sm"><span className="min-w-0 truncate">{item.name}{'enabled' in item && !item.enabled && ` · ${t('funding.disabled')}`}</span>{canWrite && <div className="flex gap-1 shrink-0"><Button size="icon" variant="ghost" aria-label={t('common.edit')} onClick={() => setEditor(kind === 'expense_rule' ? { kind, value: item as ExpenseForecastRule } : { kind, value: item as ForecastScenario })}><Pencil className="h-3 w-3" /></Button><Button size="icon" variant="ghost" aria-label={t('common.delete')} disabled={remove.isPending} onClick={() => remove.mutate({ kind, id: item.id })}><Trash2 className="h-3 w-3" /></Button></div>}</div>)}</div>
    </section>)}
    {remove.isError && <p role="alert" className="text-rose-500 text-sm">{extractApiError(remove.error, t('funding.saveError'))}</p>}
    <Dialog open={!!editor} onOpenChange={open => { if (!open) setEditor(null) }}><DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl"><DialogHeader><DialogTitle>{t(`funding.${editor?.kind ?? 'expense_rule'}`)}</DialogTitle></DialogHeader>{editor && (editor.kind === 'expense_rule' ? <RuleEditor key={editor.value?.id ?? 'new-rule'} rule={editor.value} done={done} /> : <ScenarioEditor key={editor.value?.id ?? 'new-scenario'} scenario={editor.value} done={done} />)}</DialogContent></Dialog>
  </details>
}

export function FundingDetails({ data, showSummary = true, selectedDate, onSelectDate }: { data: FundingForecast; showSummary?: boolean; selectedDate?: string | null; onSelectDate?: (value: string) => void }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const [selectedDay, setSelectedDay] = useState('')
  const [selectedPocket, setSelectedPocket] = useState('')
  const value = (amount: string | null, currency = data.currency) => amount === null ? t('funding.unknown') : mask(formatCurrency(Number(amount), currency, locale))
  const warningKeys = new Set<string>()
  const firstWarnings = data.warnings.filter(w => { const key = `${w.code}:${w.account_id}:${w.goal_id}`; if (warningKeys.has(key)) return false; warningKeys.add(key); return true })
  const day = data.timeline.find(p => p.date === (selectedDate || selectedDay)) ?? data.timeline[0]
  const acName = (id: string | null) => data.opening_accounts.find(a => a.account_id === id)?.name ?? ''
  return <div className="space-y-4">
    {showSummary && <div className="grid sm:grid-cols-2 gap-3"><div className="rounded-xl border border-border bg-card p-4"><p className="text-xs text-muted-foreground">{t('funding.endingBalance')} · {data.end_date}</p><p className="text-xl font-semibold mt-1 tabular-nums">{value(data.ending_balance)}</p></div><div className="rounded-xl border border-border bg-card p-4"><p className="text-xs text-muted-foreground">{t('funding.freeFunds')}</p><p className={`text-xl font-semibold mt-1 tabular-nums ${Number(data.free_funds) < 0 ? 'text-rose-500' : ''}`}>{value(data.free_funds)}</p><p className="text-xs text-muted-foreground mt-1">{t('funding.freeHelp')}</p></div></div>}
    {!!firstWarnings.length && <div role="status" className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4"><h3 className="flex items-center gap-2 text-sm font-semibold"><AlertTriangle className="h-4 w-4 text-amber-500" />{t('funding.warnings')}</h3><ul className="mt-2 space-y-2 text-xs">{firstWarnings.map((w, i) => <li key={i}>{w.date && <strong>{w.date} · </strong>}{acName(w.account_id)} · {t(`funding.warning.${w.code}`, { defaultValue: w.code })}{w.amount !== null && w.currency && ` · ${value(w.amount, w.currency)}`}{['unfunded_allocation', 'protected_funds_required', 'same_day_income_dependency', 'unclassified_actual', 'unmapped_actual_category', 'overdue_pending', 'budget_exceeded', 'pocket_top_up_required', 'allocation_rule_ends', 'uncovered_budget'].includes(w.code) && <span className="block text-muted-foreground">{w.detail}</span>}</li>)}</ul></div>}
    <details open={selectedDate ? true : undefined} className="rounded-xl border border-border bg-card p-4"><summary className="cursor-pointer text-sm font-medium">{t('funding.calculation')}</summary><p className="text-xs text-muted-foreground mt-2">{t('funding.orderHelp')}</p>
      <Label className="block mt-3 space-y-1">{t('funding.day')}<Input type="date" min={data.start_date} max={data.end_date} value={selectedDate || selectedDay || data.start_date} onChange={e => onSelectDate ? onSelectDate(e.target.value) : setSelectedDay(e.target.value)} /></Label>
      {day && <div className="mt-3 space-y-2 text-xs"><p>{t('funding.endingBalance')}: {value(day.balance)} · {t('funding.freeFunds')}: {value(day.free_funds)}</p>{data.warnings.filter(w => w.date === day.date).map((w, i) => <p key={`${w.code}:${i}`} className="text-amber-600 dark:text-amber-400">{t(`funding.warning.${w.code}`, { defaultValue: t('funding.reviewWarning') })} · {acName(w.account_id)}{w.amount && w.currency && ` · ${value(w.amount, w.currency)}`}</p>)}{Object.entries(day.accounts).map(([id, balance]) => <p key={id}>{acName(id)}: {value(balance, data.opening_accounts.find(a => a.account_id === id)?.currency)}</p>)}<ul className="divide-y divide-border">{data.events.filter(e => day.event_ids.includes(e.id)).map(e => <li className="py-2" key={e.id}><strong>{e.description}</strong> · {e.status === 'pending' ? 'Pending' : 'Projected'} · {t(`funding.cashRole.${e.cash_role ?? e.kind}`, { defaultValue: e.kind })} · {value(e.amount, e.currency)}<span className="block text-muted-foreground">{acName(e.from_account_id)} → {acName(e.to_account_id)}{e.shortfall && Number(e.shortfall) > 0 && ` · ${t('funding.shortfall')}: ${value(e.shortfall, e.funded_currency ?? e.currency)}`}</span></li>)}</ul></div>}
    </details>
    <details className="rounded-xl border border-border bg-card p-4"><summary className="cursor-pointer text-sm font-medium">{t('funding.pocketForecast')}</summary><Label className="block mt-3 space-y-1">{t('funding.pocket')}<select className={selectClass} value={selectedPocket} onChange={e => setSelectedPocket(e.target.value)}><option value="">{t('funding.choosePocket')}</option>{data.opening_accounts.flatMap(a => a.items.map(p => <option key={p.id} value={p.id}>{a.name} · {p.name}</option>))}</select></Label>{selectedPocket && <div className="h-48 mt-3"><ResponsiveContainer width="100%" height="100%"><LineChart data={data.timeline.map(p => ({ date: p.date, balance: p.pockets[selectedPocket] === undefined ? null : Number(p.pockets[selectedPocket]) }))}><XAxis dataKey="date" minTickGap={45} tick={{ fontSize: 10 }} /><YAxis tick={{ fontSize: 10 }} width={85} tickFormatter={n => mask(compactCurrency(n, data.opening_accounts.find(a => a.items.some(p => p.id === selectedPocket))?.currency ?? data.currency, locale))} /><Tooltip formatter={n => n == null ? t('funding.unknown') : value(String(n), data.opening_accounts.find(a => a.items.some(p => p.id === selectedPocket))?.currency)} /><Line dataKey="balance" name={t('funding.projected')} stroke="#818cf8" dot={false} connectNulls={false} isAnimationActive={false} /></LineChart></ResponsiveContainer></div>}</details>
    <details className="rounded-xl border border-border bg-card p-4"><summary className="cursor-pointer text-sm font-medium">{t('funding.assumptions')}</summary><p className="mt-2 text-xs text-muted-foreground">{t('funding.historyHelp')}</p><div className="mt-3 space-y-2 text-xs">{data.rates.map(r => <p key={r.currency}>{r.currency} → {r.target}: {r.rate ?? t('funding.unknown')} · {r.date ?? '—'} · {r.source || '—'}</p>)}{data.budget_periods.map((p, i) => <p key={`${p.rule_id}:${i}`} className="border-t border-border pt-2">{p.name} · {p.start} → {p.end}<br />{t('funding.target')}: {value(p.target, p.currency)} · {t('funding.actual')}: {value(p.actual, p.currency)} · Pending/Projected: {value(p.committed, p.currency)} · {t('funding.remainingForecast')}: {value(p.forecast, p.currency)}</p>)}{data.history_recommendations.map(r => <p key={r.rule_id} className="border-t border-border pt-2">{data.budget_periods.find(p => p.rule_id === r.rule_id)?.name}: {r.status === 'available' && r.median && r.currency ? `${t('funding.historyMedian')}: ${value(r.median, r.currency)} (${value(r.minimum ?? null, r.currency)} – ${value(r.maximum ?? null, r.currency)})` : t('funding.historyUnavailable')}</p>)}</div></details>
    <details className="rounded-xl border border-border bg-card p-4"><summary className="cursor-pointer text-sm font-semibold">{t('funding.goals')}</summary>{data.goals.length ? <div className="grid md:grid-cols-2 gap-3">{data.goals.map(g => <div key={g.goal_id}><p className="text-sm font-medium mt-3">{g.name}</p><GoalForecastDetails goal={g} /></div>)}</div> : <p className="text-sm text-muted-foreground mt-2">{t('funding.noGoals')}</p>}</details>
  </div>
}
