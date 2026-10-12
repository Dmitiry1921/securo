import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, AlertTriangle } from 'lucide-react'
import { isAxiosError } from 'axios'
import { goals, forecast, transferSchedules } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { goalPlan } from '@/lib/goal-plan'
import { formatCurrency } from '@/lib/format'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { PageHeader } from '@/components/page-header'
import { Button } from '@/components/ui/button'
import { GoalForecastDetails } from '@/components/funding-forecast'

const card = 'rounded-xl border border-border bg-card p-4 sm:p-5'

export default function GoalDetailPage() {
  const { id = '' } = useParams()
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const [months, setMonths] = useState(12)
  const [scenarioId, setScenarioId] = useState('')
  const [visibleEvents, setVisibleEvents] = useState(30)
  const goal = useQuery({ queryKey: ['goals', id], queryFn: () => goals.get(id), enabled: !!id })
  const projection = useQuery({ queryKey: ['funding-forecast', months, scenarioId],
    queryFn: () => forecast.calculate({ months, ...(scenarioId ? { scenario_id: scenarioId } : {}) }), enabled: !!goal.data })
  const scenarios = useQuery({ queryKey: ['forecast-scenarios'], queryFn: forecast.scenarios })
  const schedules = useQuery({ queryKey: ['transfer-schedules'], queryFn: transferSchedules.list, enabled: !!goal.data })
  const value = (amount: number | string | null, currency: string) => amount === null
    ? t('funding.unknown') : mask(formatCurrency(Number(amount), currency, locale))
  const date = (on: string | null) => on ? new Date(`${on}T12:00:00`).toLocaleDateString(locale) : '—'
  const back = <Link to="/goals" className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground mb-4"><ArrowLeft className="h-4 w-4" />{t('goalDetail.back')}</Link>
  if (goal.isPending) return <>{back}<p role="status">{t('common.loading')}</p></>
  if (goal.isError || !goal.data) return <>{back}<div role="alert" className={card}>
    <p>{isAxiosError(goal.error) && goal.error.response?.status === 404 ? t('goalDetail.notFound') : extractApiError(goal.error)}</p>
    <Button variant="outline" className="mt-3" onClick={() => void goal.refetch()}>{t('goalDetail.retry')}</Button>
  </div></>
  const target = goal.data
  const data = projection.data
  const plan = data ? goalPlan(data, id) : null
  const projectedGoal = data?.goals.find(g => g.goal_id === id)
  const accountName = (accountId: string | null) => data?.opening_accounts.find(a => a.account_id === accountId)?.name ?? ''
  const location = (accountId: string | null, pocketId?: string | null) => {
    const account = data?.opening_accounts.find(a => a.account_id === accountId)
    const pocket = account?.items.find(p => p.id === pocketId)
    return [account?.name, pocket?.name].filter(Boolean).join(' · ') || '—'
  }
  const firstDeficit = data?.warnings.find(w => w.code === 'account_deficit')
  const relatedWarnings = data?.warnings.filter(w => w.goal_id === id || plan?.events.some(e => e.id === w.flow_id)) ?? []
  const uniqueWarnings = relatedWarnings.filter((w, i, rows) => rows.findIndex(x => x.code === w.code && x.account_id === w.account_id) === i)
  const relatedSchedules = schedules.data?.filter(s => s.is_active &&
    (plan?.pocketIds.has(s.from_pocket_id ?? '') || plan?.pocketIds.has(s.to_pocket_id ?? ''))) ?? []
  return <>
    {back}<PageHeader title={target.name} section={t('goalDetail.subtitle')} />
    <div className="grid gap-4 sm:grid-cols-3 mb-5">
      <div className={card}><p className="text-xs text-muted-foreground">{t('goals.currentAmount')}</p><p className="text-2xl font-semibold tabular-nums mt-2">{target.valuation_available === false ? t('funding.unknown') : value(target.current_amount, target.currency)}</p><p className="text-xs text-muted-foreground mt-1">{t('goalDetail.actual')}</p></div>
      <div className={card}><p className="text-xs text-muted-foreground">{t('goals.targetAmount')}</p><p className="text-2xl font-semibold tabular-nums mt-2">{value(target.target_amount, target.currency)}</p><p className="text-xs text-muted-foreground mt-1">{date(target.target_date)}</p></div>
      <div className={card}><p className="text-xs text-muted-foreground">{t('goalDetail.byDeadline')}</p><p className="text-2xl font-semibold tabular-nums mt-2">{projectedGoal ? value(projectedGoal.projected, target.currency) : '—'}</p><p className="text-xs text-muted-foreground mt-1">{projectedGoal ? t(`funding.${projectedGoal.status}`) : t('goalDetail.noProjection')}</p></div>
    </div>
    <div className="flex flex-wrap gap-3 items-end mb-5">
      <label className="text-sm space-y-1"><span className="block">{t('goalDetail.horizon')}</span><select className="rounded-lg border border-border bg-card px-3 py-2" value={months} onChange={e => { setMonths(Number(e.target.value)); setVisibleEvents(30) }}>{[3, 6, 12, 24].map(n => <option key={n} value={n}>{t('goalDetail.months', { count: n })}</option>)}</select></label>
      <label className="text-sm space-y-1"><span className="block">{t('funding.scenario')}</span><select className="rounded-lg border border-border bg-card px-3 py-2 max-w-full" value={scenarioId} onChange={e => { setScenarioId(e.target.value); setVisibleEvents(30) }}><option value="">{t('funding.baseScenario')}</option>{scenarios.data?.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
      <Link to="/reports" className="text-sm text-primary underline underline-offset-4 py-2">{t('funding.settings')}</Link>
    </div>
    {scenarios.isError && <p role="alert" className="text-amber-600 mb-4">{t('funding.loadError')}</p>}
    {projection.isPending && <p role="status" className="mb-4">{t('common.loading')}</p>}
    {projection.isError && <div role="alert" className={`${card} mb-5`}><p>{extractApiError(projection.error)}</p><Button variant="outline" className="mt-3" onClick={() => void projection.refetch()}>{t('goalDetail.retry')}</Button></div>}
    {firstDeficit && <div role="status" className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-4 mb-5 text-sm"><p className="flex items-center gap-2 font-medium"><AlertTriangle className="h-4 w-4 shrink-0" />{t('goalDetail.familyWarning')}</p><p className="mt-2">{date(firstDeficit.date)} · {accountName(firstDeficit.account_id)} · {firstDeficit.amount && firstDeficit.currency ? value(firstDeficit.amount, firstDeficit.currency) : ''}</p><p className="text-xs text-muted-foreground mt-2">{t('goalDetail.familyWarningHelp')}</p></div>}
    {projectedGoal && <div className="mb-5"><GoalForecastDetails goal={projectedGoal} view="chart" /></div>}
    {uniqueWarnings.length > 0 && <ul className={`${card} mb-5 space-y-2 text-sm`}>{uniqueWarnings.map((w, i) => <li key={i} className="text-amber-600 dark:text-amber-400">{date(w.date)} · {t(`funding.warning.${w.code}`, { defaultValue: t('funding.reviewWarning') })}{w.amount && w.currency && ` · ${value(w.amount, w.currency)}`}<span className="block text-xs text-muted-foreground">{w.detail}</span></li>)}</ul>}
    {plan && <div className="grid gap-5 lg:grid-cols-2 mb-5">
      <section className={card}><h2 className="font-semibold mb-3">{t('goalDetail.pockets')}</h2>{plan.linked.length ? <ul className="space-y-3">{plan.linked.map(({ account, pocket }) => <li key={pocket.id} className="border-b border-border pb-3 last:border-0 last:pb-0"><Link to={`/accounts/${account.account_id}`} className="text-sm font-medium hover:text-primary">{account.name} · {pocket.name}</Link><p className="text-sm mt-1">{t('goalDetail.allocated')}: {pocket.needs_review ? t('funding.unknown') : value(pocket.balance, pocket.currency)}</p><p className="text-xs text-muted-foreground">{t('pockets.reserved')}: {value(pocket.reserved, pocket.currency)} · {t('goalDetail.available')}: {pocket.needs_review ? t('funding.unknown') : value(pocket.available, pocket.currency)}</p></li>)}</ul> : <p className="text-sm text-muted-foreground">{t('funding.reason.no_linked_pocket')}</p>}</section>
      <section className={card}><h2 className="font-semibold mb-3">{t('goalDetail.contributions')}</h2><p className="text-xs text-muted-foreground mb-3">{t('goalDetail.rulesHelp')}</p>{!plan.incomeRules.length && !relatedSchedules.length && <p className="text-sm text-muted-foreground">{t('goalDetail.noRules')}</p>}
        {plan.incomeRules.length > 0 && <div className="rounded-lg bg-muted/30 border border-border p-3 mb-3 text-xs space-y-2"><p>{t('goalDetail.incomeRuleScope')}</p><p className="text-muted-foreground">{t('goalDetail.incomeRuleBase')}</p></div>}
        <ul className="space-y-3">{plan.incomeRules.map(({ account, rule }) => <li key={rule.id} className="text-sm border-b border-border pb-3"><p className="font-medium">{t('goalDetail.incomeShare', { percentage: mask(`${rule.percentage}%`), account: account.name })}</p><p className="text-xs text-muted-foreground mt-1">{t('goalDetail.allocateTo')}: {location(account.account_id, rule.pocket_id)}</p><p className="text-xs text-muted-foreground">{t('goalDetail.rulePeriod')}: {date(rule.start_date)} → {rule.end_date ? date(rule.end_date) : t('goalDetail.indefinite')}</p><p className="text-xs mt-1">{t('goalDetail.ruleExample', { income: value(1000, account.currency), allocated: value(1000 * Number(rule.percentage) / 100, account.currency) })}</p></li>)}{relatedSchedules.map(s => <li key={s.id} className="text-sm border-b border-border pb-3"><p className="font-medium">{s.description}</p><p>{location(s.from_account_id, s.from_pocket_id)} → {location(s.to_account_id, s.to_pocket_id)}</p><p className="text-xs text-muted-foreground">{s.amount_mode === 'prorated_weekly' ? t('goalDetail.dynamicAmount') : value(s.amount, s.currency)} · {t(s.frequency === 'months' ? 'goalDetail.everyMonths' : 'goalDetail.everyDays', { count: s.interval })}{s.day_of_month && ` · ${t('goalDetail.monthDay', { day: s.day_of_month })}`} · {date(s.start_date)} → {s.end_date ? date(s.end_date) : t('goalDetail.indefinite')}</p></li>)}</ul>
        {schedules.isError && <p role="alert" className="text-amber-600 text-sm mt-3">{t('funding.loadError')}</p>}
      </section>
    </div>}
    {plan && <section className={`${card} mb-5`}><h2 className="font-semibold">{t('goalDetail.movements')}</h2><p className="text-xs text-muted-foreground my-2">{t('goalDetail.movementsHelp')}</p>{plan.events.length ? <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr className="text-left text-xs text-muted-foreground border-b border-border"><th className="py-3 pr-4">{t('goalDetail.date')}</th><th className="py-3 pr-4">{t('goalDetail.movement')}</th><th className="py-3 text-right">{t('goalDetail.amount')}</th></tr></thead><tbody>{plan.events.slice(0, visibleEvents).map(event => <tr key={event.id} className="border-b border-border last:border-0 align-top"><td className="py-3 pr-4 whitespace-nowrap">{date(event.date)}<span className="block text-[11px] text-muted-foreground">{t(`moneyMap.${event.status}`, { defaultValue: event.status })}</span></td><td className="py-3 pr-4 min-w-40"><span className="font-medium">{event.description}</span><span className="block text-xs text-muted-foreground mt-1">{t(`goalDetail.${plan.direction(event)}`)} · {location(event.from_account_id, event.from_pocket_id)} → {location(event.to_account_id, event.to_pocket_id)}</span></td><td className="py-3 text-right whitespace-nowrap">{value(event.amount, event.currency)}{event.received_amount && event.received_currency && <span className="block text-xs text-muted-foreground">→ {value(event.received_amount, event.received_currency)}</span>}{event.shortfall !== null && Number(event.shortfall) > 0 && <span className="block text-xs text-rose-500">{t('funding.shortfall')}: {value(event.shortfall, event.funded_currency ?? event.currency)}</span>}</td></tr>)}</tbody></table></div> : <p className="text-sm text-muted-foreground py-4">{t('goalDetail.noMovements')}</p>}{plan.events.length > visibleEvents && <Button variant="outline" className="mt-4" onClick={() => setVisibleEvents(n => n + 30)}>{t('goalDetail.more')}</Button>}</section>}
  </>
}
