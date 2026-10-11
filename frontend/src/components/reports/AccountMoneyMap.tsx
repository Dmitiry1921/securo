import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ArrowDownLeft, ArrowUpRight, ArrowRight, ChevronDown } from 'lucide-react'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { Button } from '@/components/ui/button'
import { formatCurrency } from '@/lib/format'
import type { MoneyMapData, MoneyMapFlow } from '@/types'

export function AccountMoneyMap({ data, currency, locale }: { data: MoneyMapData; currency: string; locale: string }) {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  const [focused, setFocused] = useState<string | null>(null)
  const [kind, setKind] = useState<string>('all')
  const [page, setPage] = useState(1)
  const money = (amount: number, ccy = currency) => mask(formatCurrency(amount, ccy, locale))
  const pockets = new Map(data.accounts.flatMap(a => a.items.map(p => [p.id, p] as const)))
  const goals = new Map(data.goals.map(g => [g.id, g]))
  const pocketName = (id: string | null) => id ? pockets.get(id)?.name ?? t('moneyMap.pocket') : t('moneyMap.unallocated')
  const route = (flow: MoneyMapFlow, side: 'from' | 'to') => {
    const account = side === 'from' ? flow.from_account_name : flow.to_account_name
    const pocket = side === 'from' ? flow.from_pocket_id : flow.to_pocket_id
    return account ? `${account} · ${pocketName(pocket)}` : flow.description
  }
  const status = (flow: MoneyMapFlow) => (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${flow.status === 'posted' ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400' : flow.status === 'pending' ? 'bg-amber-500/10 text-amber-600 dark:text-amber-400' : 'bg-violet-500/10 text-violet-600 dark:text-violet-400'}`}>
      {t(`moneyMap.${flow.status}`)}
    </span>
  )
  // Aggregate routes for the map; the dated ledger below retains every occurrence.
  const routes = (rows: MoneyMapFlow[]) => {
    const grouped = new Map<string, { flow: MoneyMapFlow; count: number; total: number | null }>()
    for (const flow of rows) {
      const key = [flow.kind, flow.from_account_id, flow.to_account_id, flow.from_pocket_id,
        flow.to_pocket_id, flow.kind === 'income' ? flow.description : flow.category_id, flow.status].join(':')
      const old = grouped.get(key)
      if (old) {
        old.count++
        old.total = old.total === null || flow.amount_primary === null ? null : old.total + flow.amount_primary
      } else grouped.set(key, { flow, count: 1, total: flow.amount_primary })
    }
    return [...grouped.values()]
  }
  const flowList = (rows: MoneyMapFlow[], incoming: boolean) => (
    <div className="space-y-2">
      {routes(rows).map(({ flow, count, total }) => (
        <div key={flow.id} className={`rounded-lg border px-3 py-2 ${flow.kind === 'transfer' ? 'border-blue-500/20 bg-blue-500/5' : incoming ? 'border-emerald-500/20 bg-emerald-500/5' : 'border-rose-500/20 bg-rose-500/5'}`}>
          <p className="text-xs font-medium break-words">{flow.kind === 'transfer' ? route(flow, incoming ? 'from' : 'to') : flow.kind === 'expense' ? flow.category_name ?? flow.description : flow.description}</p>
          <p className="mt-1 text-[11px] text-muted-foreground">{t('moneyMap.pocket')}: {pocketName(incoming ? flow.to_pocket_id : flow.from_pocket_id)}</p>
          <div className="mt-1 flex flex-wrap items-center justify-between gap-1">
            <span className="text-sm font-semibold tabular-nums">{total === null ? t('moneyMap.rateMissing') : money(total)}</span>
            {status(flow)}
          </div>
          {count > 1 && <p className="mt-1 text-[11px] text-muted-foreground">{t('moneyMap.occurrences', { count })}</p>}
        </div>
      ))}
      {!rows.length && <p className="text-xs text-muted-foreground py-2">{t('moneyMap.noFlows')}</p>}
    </div>
  )
  const visibleFlows = data.flows.filter(f => (!focused || f.from_account_id === focused || f.to_account_id === focused) && (kind === 'all' || f.kind === kind))
  const pageSize = 50
  const pages = Math.max(1, Math.ceil(visibleFlows.length / pageSize))
  const safePage = Math.min(page, pages)
  const pageFlows = visibleFlows.slice((safePage - 1) * pageSize, safePage * pageSize)
  return (
    <div className="space-y-5">
      <p className="text-sm text-muted-foreground">{t('moneyMap.help')}</p>
      {!!data.missing_rates.length && <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm">{t('moneyMap.missingRates', { currencies: data.missing_rates.join(', ') })}</div>}
      {!!data.unpaired_transfers && <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm">{t('moneyMap.unpairedTransfers', { count: data.unpaired_transfers })}</div>}
      <div className="space-y-4">
        {data.accounts.map(account => {
          const incoming = data.flows.filter(f => f.to_account_id === account.account_id && f.kind !== 'allocation')
          const outgoing = data.flows.filter(f => f.from_account_id === account.account_id && f.kind !== 'allocation')
          const allocation = data.flows.filter(f => f.from_account_id === account.account_id && f.kind === 'allocation')
          return (
            <section key={account.account_id} className={`rounded-xl border bg-card p-4 shadow-sm ${focused === account.account_id ? 'border-primary' : 'border-border'}`}>
              <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
                <div>
                  <Link to={`/accounts/${account.account_id}`} className="font-semibold hover:text-primary">{account.name}</Link>
                  <p className="text-xs text-muted-foreground mt-1">{t('moneyMap.currentBalance')}: {money(Number(account.balance), account.currency)}</p>
                  {account.needs_review && <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">{t('moneyMap.needsReview')}</p>}
                </div>
                <button type="button" onClick={() => { setFocused(focused === account.account_id ? null : account.account_id); setPage(1) }} aria-pressed={focused === account.account_id} className="text-xs font-medium rounded-lg border border-border px-3 py-1.5 hover:bg-muted">{t('moneyMap.showOperations')}</button>
              </div>
              <div className="grid gap-4 lg:grid-cols-[1fr_1.15fr_1fr]">
                <div>
                  <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-emerald-600 dark:text-emerald-400"><ArrowDownLeft size={14} />{t('moneyMap.incoming')}</p>
                  {flowList(incoming, true)}
                </div>
                <div className="rounded-lg border border-border bg-muted/20 p-3">
                  <p className="text-xs font-semibold mb-2">{t('moneyMap.pocketsNow')}</p>
                  <div className="space-y-3">
                    {account.items.filter(p => !p.archived || Number(p.balance) !== 0).map(pocket => {
                      const goal = pocket.goal_id ? goals.get(pocket.goal_id) : undefined
                      return (
                        <div key={pocket.id}>
                          <div className="flex justify-between gap-2 text-xs"><span>{pocket.name}</span><span className="font-semibold tabular-nums">{money(Number(pocket.balance), account.currency)}</span></div>
                          {Number(pocket.reserved) > 0 && <p className="mt-1 text-[11px] text-amber-600 dark:text-amber-400">{t('moneyMap.reserved')}: {money(Number(pocket.reserved), account.currency)}</p>}
                          {pocket.needs_review && <p className="text-xs text-amber-600">{t('moneyMap.needsReview')}</p>}
                          {goal && <div className="mt-1.5">
                            <div className="h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full bg-primary" style={{ width: `${Math.min(100, Math.max(0, goal.percentage))}%` }} /></div>
                            <Link to="/goals" className="mt-1 block text-[11px] text-muted-foreground hover:text-primary">
                              {goal.valuation_available === false ? t('moneyMap.rateMissing') : `${mask(`${goal.percentage}%`)} · ${money(Number(goal.current_amount), goal.currency)} / ${money(Number(goal.target_amount), goal.currency)}`}
                              {goal.target_date && ` · ${goal.target_date}`}
                              {goal.repeat_frequency && ` · ${t(`moneyMap.repeat.${goal.repeat_frequency}`)}`}
                            </Link>
                          </div>}
                        </div>
                      )
                    })}
                    <div className="border-t border-border pt-2 flex justify-between gap-2 text-xs"><span>{t('moneyMap.unallocated')}</span><span className="tabular-nums">{money(Number(account.unallocated), account.currency)}</span></div>
                  </div>
                  {!!allocation.length && <details className="mt-3 border-t border-border pt-2">
                    <summary className="cursor-pointer text-xs font-medium">{t('moneyMap.allocations')} · {allocation.length}</summary>
                    <div className="mt-2 space-y-2">{allocation.map(f => <div key={f.id} className="text-xs"><p>{pocketName(f.from_pocket_id)} → {pocketName(f.to_pocket_id)}</p><p className="mt-1 flex flex-wrap gap-2 items-center">{f.date} · {money(f.amount, f.currency)} {status(f)}</p></div>)}</div>
                  </details>}
                </div>
                <div>
                  <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-rose-500"><ArrowUpRight size={14} />{t('moneyMap.outgoing')}</p>
                  {flowList(outgoing, false)}
                </div>
              </div>
            </section>
          )
        })}
        {!data.accounts.length && <p className="py-12 text-center text-muted-foreground">{t('reports.noData')}</p>}
      </div>
      {!!data.budget_gaps.length && <details open className="rounded-xl border border-amber-500/30 bg-card p-4">
        <summary className="cursor-pointer text-sm font-semibold">{t('moneyMap.budgetGaps')}</summary>
        <p className="mt-2 text-xs text-muted-foreground">{t('moneyMap.budgetGapsHelp')}</p>
        <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{data.budget_gaps.map(g => <div key={`${g.month}:${g.category}`} className="rounded-lg bg-muted/30 px-3 py-2 text-xs"><p>{g.category} · {g.month.slice(0, 7)}</p><p className="mt-1 font-semibold">{money(g.amount, g.currency)}</p></div>)}</div>
      </details>}
      <div className="rounded-xl border border-border bg-card p-4">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
          <p className="text-sm font-semibold">{t('moneyMap.operations')} · {visibleFlows.length}</p>
          <div className="flex flex-wrap gap-2">
            {focused && <button type="button" className="text-xs text-primary" onClick={() => { setFocused(null); setPage(1) }}>{t('moneyMap.allAccounts')}</button>}
            <label className="flex items-center gap-1 text-xs"><select value={kind} onChange={e => { setKind(e.target.value); setPage(1) }} className="bg-card rounded border border-border p-1.5" aria-label={t('moneyMap.flowType')}>{['all', 'income', 'expense', 'transfer', 'allocation', 'investment'].map(k => <option key={k} value={k}>{t(`moneyMap.${k}`)}</option>)}</select><ChevronDown size={12} /></label>
          </div>
        </div>
        <div className="divide-y divide-border">
          {pageFlows.map(flow => <div key={flow.id} className="py-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0"><p className="text-sm font-medium">{flow.description}</p><p className="mt-1 flex flex-wrap items-center gap-1 text-xs text-muted-foreground"><span>{route(flow, 'from')}</span><ArrowRight size={12} /><span>{route(flow, 'to')}</span></p><p className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">{flow.date} {status(flow)} · {t(`moneyMap.${flow.kind}`)}</p></div>
            <div className="sm:text-right shrink-0"><p className="font-semibold tabular-nums">{money(flow.amount, flow.currency)}{flow.received_amount !== null && <> → {money(flow.received_amount, flow.received_currency ?? currency)}</>}</p>{flow.currency !== currency && <p className="text-xs text-muted-foreground">{flow.amount_primary === null ? t('moneyMap.rateMissing') : money(flow.amount_primary)}</p>}</div>
          </div>)}
          {!visibleFlows.length && <p className="py-8 text-center text-sm text-muted-foreground">{t('moneyMap.noFlows')}</p>}
        </div>
        {pages > 1 && <div className="mt-3 flex items-center justify-end gap-3 text-xs">
          <Button variant="outline" size="sm" disabled={safePage === 1} onClick={() => setPage(safePage - 1)}>{t('common.previous')}</Button>
          <span className="text-muted-foreground">{safePage} / {pages}</span>
          <Button variant="outline" size="sm" disabled={safePage === pages} onClick={() => setPage(safePage + 1)}>{t('common.next')}</Button>
        </div>}
      </div>
    </div>
  )
}
