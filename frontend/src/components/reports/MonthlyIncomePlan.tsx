import { useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Info, X } from 'lucide-react'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatCurrency } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import type { FundingForecast, MonthlyPlanAmount } from '@/types'

const colors = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-4)', 'var(--chart-5)',
  'color-mix(in srgb, var(--chart-4) 70%, var(--chart-5))',
  'color-mix(in srgb, var(--chart-1) 50%, var(--chart-3))']
const selectClass = 'max-w-full rounded-lg border border-border bg-card px-3 py-2 text-sm'

function PlanRow({ name, source, amount }: { name: ReactNode; source: ReactNode; amount: ReactNode }) {
  return <tr className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1 border-b border-border py-3 sm:table-row sm:py-0 last:border-0">
    <td className="min-w-0 font-medium sm:py-4 sm:pr-4 sm:w-1/5">{name}</td>
    <td className="col-span-2 row-start-2 min-w-0 text-sm text-muted-foreground sm:py-4 sm:pr-4">{source}</td>
    <td className="col-start-2 row-start-1 text-right tabular-nums whitespace-nowrap sm:py-4">{amount}</td>
  </tr>
}

export function MonthlyIncomePlan({ data, onSelectDate }: { data: FundingForecast; onSelectDate: (date: string) => void }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const [chosenMonth, setChosenMonth] = useState('')
  const [chosenCurrency, setChosenCurrency] = useState('')
  const [openMonth, setOpenMonth] = useState('')
  const detailsRequested = useRef(false)
  const months = data.monthly_plan ?? []
  const currencies = Object.keys(months[0]?.totals ?? {})
  const nativeBudgetCurrency = data.budget_periods.reduce<Record<string, number>>((counts, p) => {
    counts[p.currency] = (counts[p.currency] ?? 0) + 1
    return counts
  }, {})
  const defaultCurrency = Object.keys(nativeBudgetCurrency).sort((a, b) => nativeBudgetCurrency[b] - nativeBudgetCurrency[a])[0]
  const currency = currencies.includes(chosenCurrency) ? chosenCurrency
    : currencies.includes(defaultCurrency) ? defaultCurrency : data.currency
  const selected = months.find(m => m.month === chosenMonth) ?? months[0]
  const value = (amount: string | null | undefined, native = currency) => amount == null
    ? t('funding.unknown') : mask(formatCurrency(Number(amount), native, locale))
  const monthName = (month: string) => new Date(`${month}-01T12:00:00Z`).toLocaleDateString(locale, { month: 'long', year: 'numeric', timeZone: 'UTC' })
  const date = (on: string) => new Date(`${on}T12:00:00Z`).toLocaleDateString(locale, { day: 'numeric', month: 'short', timeZone: 'UTC' })
  const accountName = (id?: string | null) => data.opening_accounts.find(a => a.account_id === id)?.name ?? ''
  const pockets = data.opening_accounts.flatMap(account => account.items.filter(p => !p.archived).map(p => ({ ...p, accountName: account.name })))
  const expenseDetails = (costs: MonthlyPlanAmount[]) => <details className="mt-2">
    <summary className="cursor-pointer text-xs text-primary">{t('monthlyPlan.expenseDetails')}</summary>
    <p className="mt-2 text-xs text-muted-foreground">{t('monthlyPlan.expensesHelp')}</p>
    <ul className="mt-3 space-y-3">{costs.map(cost => {
      const pocket = pockets.find(p => p.id === cost.pocket_id)
      const linked = cost.rule_id && pocket?.goal_id ? pockets.filter(p => p.goal_id === pocket.goal_id) : pocket ? [pocket] : []
      return <li key={cost.id} className="text-xs">
        <div className="flex flex-wrap justify-between gap-x-3 gap-y-1 text-foreground"><span>{cost.name}</span><span className="tabular-nums">{value(cost.values[currency])}</span></div>
        <p className="mt-1 text-muted-foreground">{cost.start && cost.end ? `${date(cost.start)} — ${date(cost.end)}` : cost.date ? date(cost.date) : ''}</p>
        <p className="mt-1 text-muted-foreground">{t(cost.rule_id ? 'monthlyPlan.budgetExpense' : 'monthlyPlan.scheduledExpense')}</p>
        {linked.length > 0 ? <ul className="mt-1 space-y-1">{linked.map(p => <li key={p.id}><Link to={`/accounts/${p.account_id}`} className="text-primary hover:underline">{p.accountName} · {p.name}</Link></li>)}</ul>
          : cost.account_id && <Link to={`/accounts/${cost.account_id}`} className="mt-1 block text-primary hover:underline">{accountName(cost.account_id)}</Link>}
        {cost.budget_id && <Link to="/budgets" className="mt-1 block text-primary hover:underline">{t('monthlyPlan.openBudget')}</Link>}
      </li>
    })}</ul>
  </details>
  const savings = months[0]?.contributions ?? []
  const color = (id: string) => colors[Math.max(0, savings.findIndex(s => s.id === id)) % colors.length]
  const maximum = Math.max(1, ...months.flatMap(m => {
    const totals = m.totals[currency]
    return [Number(totals?.income ?? 0), Number(totals?.requirements ?? 0)]
  })) * 1.03
  const width = (amount: number) => `${Math.max(0, amount) / maximum * 100}%`
  const segment = (id: string, label: string, amount: string | null | undefined, background: string, offset?: number) => {
    const quantity = Number(amount)
    if (!(quantity > 0)) return null
    return <Tooltip key={id}>
      <TooltipTrigger asChild><span data-plan-segment={id} className="block h-full hover:brightness-125" style={{ width: width(quantity), background,
        ...(offset === undefined ? {} : { position: 'absolute', top: 0, left: width(offset) }) }} /></TooltipTrigger>
      <TooltipContent>{label}: <span className="tabular-nums">{value(amount)}</span></TooltipContent>
    </Tooltip>
  }
  const showBreakdown = () => {
    const target = document.getElementById('monthly-plan-breakdown')
    target?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' })
    target?.querySelector<HTMLSelectElement>('select')?.focus({ preventScroll: true })
  }
  const firstDeficit = data.warnings.filter(w => w.date && ['account_deficit', 'protected_funds_required', 'unfunded_allocation'].includes(w.code))
    .sort((a, b) => a.date!.localeCompare(b.date!))[0]
  const incomplete = data.warnings.some(w => ['no_expense_rules', 'uncovered_budget', 'unclassified_actual', 'unmapped_actual_category', 'unpaired_transfers', 'missing_account', 'missing_pocket'].includes(w.code))
  const showDate = (on: string) => {
    onSelectDate(on)
    document.getElementById('funding-details')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
  const totals = selected?.totals[currency]
  const rate = data.rates.find(r => r.currency === currency)

  return <section className="rounded-xl border border-border bg-card p-4 sm:p-5 mb-5" aria-labelledby="monthly-plan-title">
    <div className="flex flex-wrap items-start justify-between gap-3 mb-3">
      <div className="min-w-0"><h2 id="monthly-plan-title" className="text-base font-semibold">{t('monthlyPlan.title')}</h2><p className="text-xs text-muted-foreground mt-1">{t('monthlyPlan.subtitle')}</p></div>
      {currencies.length > 0 && <label className="text-xs text-muted-foreground space-y-1"><span className="block">{t('monthlyPlan.currency')}</span><select aria-label={t('monthlyPlan.currency')} className={selectClass} value={currency} onChange={e => setChosenCurrency(e.target.value)}>{currencies.map(c => <option key={c} value={c}>{c}</option>)}</select></label>}
    </div>
    <div className="text-xs text-muted-foreground space-y-1 mb-4">
      <p>{t('monthlyPlan.completeMonths', { date: date(data.start_date) })}</p>
      <p>{t('funding.scenario')}: <span className="font-medium text-foreground">{data.scenario?.name ?? t('funding.baseScenario')}</span></p>
      {data.scenario && <p>{t('monthlyPlan.multipliers', { income: mask(data.scenario.income_multiplier), expense: mask(data.scenario.expense_multiplier), days: data.scenario.income_delay_days })}</p>}
      {currency !== data.currency && <p>{t('monthlyPlan.rate', { currency: data.currency, target: currency, rate: rate?.rate ? mask(new Intl.NumberFormat(locale, { maximumSignificantDigits: 8 }).format(1 / Number(rate.rate))) : t('funding.unknown'), date: rate?.date ?? '—' })}</p>}
    </div>
    {incomplete && <p role="status" className="text-xs text-amber-600 dark:text-amber-400 mb-4">{t('monthlyPlan.incomplete')}</p>}
    {!selected || !totals ? <p role="status" className="text-sm text-muted-foreground py-4">{t(data.monthly_plan ? 'monthlyPlan.noMonths' : 'monthlyPlan.unavailable')}</p> : <>
      <div className="flex flex-wrap gap-x-4 gap-y-2 text-xs mb-2" aria-label={t('monthlyPlan.legend')}>
        <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 bg-muted-foreground/60" />{t('monthlyPlan.living')}</span>
        {savings.filter(s => months.some(m => Number(m.contributions.find(c => c.id === s.id)?.values[currency]) > 0)).map(s => <span key={s.id} className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5" style={{ background: color(s.id) }} />{s.name}</span>)}
        <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5" style={{ background: 'var(--chart-3)' }} />{t('monthlyPlan.remaining')}</span>
        <span className="inline-flex items-center gap-1.5"><span className="h-3 border-l-2 border-foreground" />{t('monthlyPlan.incomeBoundary')}</span>
        <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5" style={{ background: 'repeating-linear-gradient(135deg, transparent 0 3px, var(--foreground) 3px 4px)' }} />{t('monthlyPlan.deficit')}</span>
      </div>
      <p className="text-xs text-muted-foreground mb-3">{t('monthlyPlan.interactionHelp')}</p>
      <TooltipProvider disableHoverableContent><div role="group" aria-label={t('monthlyPlan.chartLabel')} className="divide-y divide-border">
        {months.map(month => {
          const amounts = month.totals[currency]
          const known = amounts?.income != null && amounts?.requirements != null && amounts?.remaining != null
          const remainder = Number(amounts?.remaining ?? 0)
          return <Popover key={month.month} open={openMonth === month.month} onOpenChange={open => {
            if (open) { setChosenMonth(month.month); setOpenMonth(month.month) }
            else setOpenMonth(current => current === month.month ? '' : current)
          }}><PopoverTrigger asChild><button type="button" aria-pressed={selected.month === month.month}
            className={`w-full text-left py-3 px-2 rounded-md transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${selected.month === month.month ? 'bg-muted/30' : ''}`}>
            <span className="flex flex-wrap justify-between items-baseline gap-x-4 gap-y-1 text-sm mb-2">
              <span className="inline-flex items-center gap-2 font-medium capitalize">{monthName(month.month)}<Info className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" /></span>
              <span className="text-xs text-muted-foreground">{t('monthlyPlan.income')}: <span className="tabular-nums">{value(amounts?.income)}</span></span>
            </span>
            {known && <span className="relative block h-5 mb-2" aria-hidden="true">
              <span className="flex h-full">
                {segment('living', t('monthlyPlan.living'), amounts.living, 'color-mix(in srgb, var(--muted-foreground) 60%, transparent)')}
                {month.contributions.map(c => segment(c.id, c.name, c.values[currency], color(c.id)))}
                {remainder > 0 && segment('remaining', t('monthlyPlan.remaining'), amounts.remaining, 'var(--chart-3)')}
              </span>
              <span className="absolute top-[-3px] h-7 border-l-2 border-foreground" style={{ left: width(Number(amounts.income)) }} />
              {remainder < 0 && segment('deficit', t('monthlyPlan.deficit'), String(-remainder), 'repeating-linear-gradient(135deg, transparent 0 3px, var(--foreground) 3px 4px)', Number(amounts.income))}
            </span>}
            <span className={`block text-xs tabular-nums ${known && remainder < 0 ? 'text-rose-600 dark:text-rose-400' : 'text-muted-foreground'}`}>
              {t(known && remainder < 0 ? 'monthlyPlan.deficit' : 'monthlyPlan.remaining')}: {value(amounts?.remaining == null ? null : String(Math.abs(remainder)))}
            </span>
          </button></PopoverTrigger>
            <PopoverContent aria-label={t('monthlyPlan.monthOverview', { month: monthName(month.month) })} align="start" collisionPadding={16}
              className="w-[min(24rem,calc(100vw-2rem))] max-h-[min(44rem,var(--radix-popover-content-available-height),calc(100dvh-3rem))] overflow-y-auto select-text"
              onCloseAutoFocus={event => {
                if (detailsRequested.current) { event.preventDefault(); detailsRequested.current = false; showBreakdown() }
              }}>
              <div className="flex items-center justify-between gap-3 mb-3"><h3 className="font-medium capitalize">{monthName(month.month)}</h3>
                <Button variant="ghost" size="icon" aria-label={t('common.close')} onClick={() => setOpenMonth('')}><X className="h-4 w-4" /></Button>
              </div>
              <p className="text-xs text-muted-foreground mb-3">{t('monthlyPlan.formula')}</p>
              <dl className="text-sm space-y-2">
                <div className="flex justify-between gap-3 font-medium"><dt>{t('monthlyPlan.income')}</dt><dd className="tabular-nums whitespace-nowrap">{value(amounts?.income)}</dd></div>
                {month.incomes.map(income => <div key={income.id} className="flex justify-between gap-3 text-xs text-muted-foreground"><dt>{date(income.date!)} · {income.name} · {accountName(income.account_id)}</dt><dd className="tabular-nums whitespace-nowrap">{value(income.amount, income.currency)}</dd></div>)}
                <div className="border-t border-border pt-2"><div className="flex justify-between gap-3"><dt>{t('monthlyPlan.living')}</dt><dd className="tabular-nums whitespace-nowrap">{value(amounts?.living)}</dd></div>{expenseDetails(month.costs)}</div>
                <div className="flex justify-between gap-3 font-medium"><dt>{t('monthlyPlan.contributionsTotal')}</dt><dd className="tabular-nums whitespace-nowrap">{value(amounts?.contributions)}</dd></div>
                {month.contributions.map(c => <div key={c.id} className="flex justify-between gap-3 text-xs text-muted-foreground"><dt>{c.name}</dt><dd className="tabular-nums whitespace-nowrap">{value(c.values[currency])}</dd></div>)}
                <div className={`flex justify-between gap-3 border-t border-border pt-3 font-medium ${known && remainder < 0 ? 'text-rose-600 dark:text-rose-400' : ''}`}><dt>{t('monthlyPlan.remaining')}</dt><dd className="tabular-nums whitespace-nowrap">{value(amounts?.remaining)}</dd></div>
              </dl>
              <p className="text-xs text-muted-foreground my-3">{t('monthlyPlan.remainingHelp')}</p>
              <Button variant="outline" className="w-full" onClick={() => { detailsRequested.current = true; setChosenMonth(month.month); setOpenMonth('') }}>{t('monthlyPlan.openBreakdown')}</Button>
            </PopoverContent>
          </Popover>
        })}
      </div></TooltipProvider>
      <p className="text-xs text-muted-foreground mt-3">{t('monthlyPlan.sharedScale', { currency })}</p>
      {firstDeficit && <div role="status" className="border-l-2 border-amber-500 pl-3 my-4 text-sm">
        <p className="flex gap-2 items-start"><AlertTriangle className="h-4 w-4 mt-0.5 shrink-0 text-amber-600 dark:text-amber-400" />{t('monthlyPlan.datedDeficit', { date: date(firstDeficit.date!), account: accountName(firstDeficit.account_id) })}</p>
        <Button variant="link" className="h-auto p-0 mt-2" onClick={() => showDate(firstDeficit.date!)}>{t('monthlyPlan.explainDate')}</Button>
      </div>}
      <div id="monthly-plan-breakdown" className="border-t border-border mt-5 pt-4 scroll-mt-24">
        <div className="flex flex-wrap items-end justify-between gap-3 mb-4">
          <label className="text-sm space-y-1"><span className="block font-medium">{t('monthlyPlan.breakdown')}</span><select aria-label={t('monthlyPlan.breakdown')} className={selectClass} value={selected.month} onChange={e => setChosenMonth(e.target.value)}>{months.map(m => <option key={m.month} value={m.month}>{monthName(m.month)}</option>)}</select></label>
          <span className="text-xs text-muted-foreground">{t('monthlyPlan.expected')}</span>
        </div>
        <ul className="text-sm space-y-2 mb-4">{selected.incomes.length ? selected.incomes.map(income => <li key={income.id} className="flex flex-wrap justify-between gap-x-4 gap-y-1"><span>{date(income.date!)} · {income.name}<span className="text-xs text-muted-foreground"> · {accountName(income.account_id)}</span></span><span className="tabular-nums">{value(income.amount, income.currency)}</span></li>) : <li className="text-muted-foreground">{t('monthlyPlan.noIncome')}</li>}</ul>
        <table className="block w-full text-sm sm:table"><thead className="hidden sm:table-header-group"><tr className="border-b border-border text-left text-xs text-muted-foreground"><th className="py-3 pr-4 font-medium">{t('monthlyPlan.destination')}</th><th className="py-3 pr-4 font-medium">{t('monthlyPlan.source')}</th><th className="py-3 text-right font-medium">{t('monthlyPlan.amount', { currency })}</th></tr></thead>
          <tbody className="block sm:table-row-group">
            <PlanRow name={t('monthlyPlan.living')} amount={value(totals.living)} source={<>
              <p>{t('monthlyPlan.livingSource')}</p>
              {expenseDetails(selected.costs)}
            </>} />
            {selected.contributions.map(c => <PlanRow key={c.id} name={c.goal_id ? <Link to={`/goals/${c.goal_id}`} className="hover:text-primary hover:underline">{c.name}</Link> : c.name} amount={value(c.values[currency])}
              source={c.sources.length ? <ul className="space-y-3">{c.sources.map(source => <li key={source.id}>
                <p>{date(source.date!)} · {source.income_name ?? t('monthlyPlan.otherFunds')}</p>
                <p className="text-xs mt-1">{accountName(source.account_id)}{source.income_amount && source.income_currency ? ` · ${value(source.income_amount, source.income_currency)}` : ''}</p>
                <p className="text-foreground mt-1">{source.percentage ? `${mask(new Intl.NumberFormat(locale, { maximumFractionDigits: 2 }).format(Number(source.percentage)))}% → ` : ''}{value(source.amount, source.currency)}</p>
                {source.shortfall == null ? <p className="text-xs text-amber-600 dark:text-amber-400">{t('funding.unknown')}</p> : Number(source.shortfall) > 0 && <p className="text-xs text-rose-600 dark:text-rose-400">{t('funding.shortfall')}: {value(source.shortfall, source.currency)}</p>}
              </li>)}</ul> : t('monthlyPlan.noContribution')} />)}
            <PlanRow name={totals.remaining != null && Number(totals.remaining) < 0 ? t('monthlyPlan.deficit') : t('monthlyPlan.remaining')}
              source={<><p>{t('monthlyPlan.remainingSource')}</p><p className="text-xs mt-1">{t('monthlyPlan.remainingHelp')}</p></>} amount={<span className={totals.remaining != null && Number(totals.remaining) < 0 ? 'text-rose-600 dark:text-rose-400' : ''}>{value(totals.remaining)}</span>} />
          </tbody>
        </table>
        {selected.saved_payments.length > 0 && <details className="border-t border-border pt-3 mt-3 text-sm"><summary className="cursor-pointer font-medium">{t('monthlyPlan.savedPayments')}: {value(totals.saved_payments)}</summary><p className="text-xs text-muted-foreground mt-2">{t('monthlyPlan.savedPaymentsHelp')}</p><ul className="text-xs space-y-2 mt-3">{selected.saved_payments.map(p => <li key={p.id}>{date(p.date!)} · {p.name} · {value(p.values[currency])}</li>)}</ul></details>}
        <p className="text-xs text-muted-foreground border-t border-border pt-3 mt-3">{t('monthlyPlan.scope')}</p>
      </div>
    </>}
  </section>
}
