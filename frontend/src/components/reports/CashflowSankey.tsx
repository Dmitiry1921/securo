import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { sankey as d3Sankey, sankeyLinkHorizontal, sankeyJustify } from 'd3-sankey'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatCurrency } from '@/lib/format'
import { buildMoneyMapGraph, FLOW_COLORS } from '@/lib/money-map-graph'
import type { MoneyMapNode, MoneyMapLink } from '@/lib/money-map-graph'
import type { MoneyMapData } from '@/types'

export function CashflowSankey({ data, currency, locale }: {
  data: MoneyMapData; currency: string; locale: string
}) {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  const containerRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  const [hover, setHover] = useState<number | null>(null)
  const graph = useMemo(() => buildMoneyMapGraph(data, t('moneyMap.unallocated')), [data, t])
  const money = (value: number, ccy = currency) => mask(formatCurrency(value, ccy, locale))

  useEffect(() => {
    const element = containerRef.current
    if (!element) return
    const observer = new ResizeObserver(entries => setWidth(entries[0].contentRect.width))
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  const layout = useMemo(() => {
    if (!graph.links.length || width <= 0) return null
    // Native Sankey assigns columns; leave enough room for account/pocket labels
    // and use horizontal scrolling on narrow screens instead of hiding routes.
    const initial = d3Sankey<MoneyMapNode, MoneyMapLink>().nodePadding(0)({
      nodes: graph.nodes.map(n => ({ ...n })), links: graph.links.map(l => ({ ...l })),
    })
    const columns = new Map<number, number>()
    for (const node of initial.nodes) columns.set(node.x0!, (columns.get(node.x0!) ?? 0) + 1)
    const depth = Math.max(...initial.nodes.map(n => n.depth ?? 0))
    const chartWidth = Math.max(width, (depth + 1) * 260)
    const chartHeight = Math.max(420, Math.max(...columns.values()) * 66 + 60)
    const chart = d3Sankey<MoneyMapNode, MoneyMapLink>()
      .nodeWidth(16).nodePadding(32).nodeAlign(sankeyJustify)
      .extent([[12, 26], [chartWidth - 12, chartHeight - 24]])({
        nodes: graph.nodes.map(n => ({ ...n })), links: graph.links.map(l => ({ ...l })),
      })
    const labels = new Map<number, number>()
    const byColumn = new Map<number, typeof chart.nodes>()
    for (const node of chart.nodes) {
      const column = node.x0!
      if (!byColumn.has(column)) byColumn.set(column, [])
      byColumn.get(column)!.push(node)
    }
    for (const column of byColumn.values()) {
      column.sort((a, b) => a.y0! - b.y0!)
      let previous = 0
      for (const node of column) {
        const y = Math.max((node.y0! + node.y1!) / 2 - 12, previous + 52)
        labels.set(node.index!, y)
        previous = y
      }
      // Keep thin bottom flows' labels within the SVG.
      const overflow = Math.max(0, previous + 32 - chartHeight)
      if (overflow) for (const node of column) labels.set(node.index!, labels.get(node.index!)! - overflow)
    }
    return { ...chart, chartWidth, chartHeight, labels }
  }, [graph, width])

  const path = sankeyLinkHorizontal<MoneyMapNode, MoneyMapLink>()
  const pockets = new Map(data.accounts.flatMap(a => a.items.map(p => [p.id, p] as const)))
  const goals = new Map(data.goals.map(g => [g.id, g]))
  const linkDescription = (link: MoneyMapLink) => {
    const source = link.source as unknown as MoneyMapNode
    const target = link.target as unknown as MoneyMapNode
    return `${source.name} · ${source.detail} → ${target.name} · ${target.detail}\n${t(`moneyMap.${link.kind}`)} · ${t(`moneyMap.${link.status}`)} · ${money(link.value)}\n` +
      link.flows.map(f => `${f.date} · ${f.description} · ${money(f.amount, f.currency)}${f.received_amount !== null ? ` → ${money(f.received_amount, f.received_currency ?? currency)}` : ''}`).join('\n')
  }

  return (
    <div>
      <div ref={containerRef} className="w-full overflow-x-auto privacy-sensitive">
        {!graph.links.length && <p className="text-muted-foreground text-sm text-center py-16">{t('reports.noData')}</p>}
        {layout && <svg width={layout.chartWidth} height={layout.chartHeight} role="img"
          aria-label={t('reports.flowChartAria')} onMouseLeave={() => setHover(null)}>
          <g fill="none">
            {layout.links.map((link, i) => {
              const source = link.source as unknown as MoneyMapNode & { index: number }
              const target = link.target as unknown as MoneyMapNode & { index: number }
              const active = hover === source.index || hover === target.index
              return <g key={i}>
                <path d={path(link) ?? undefined} stroke={FLOW_COLORS[link.kind]}
                  strokeOpacity={hover !== null ? active ? 0.6 : 0.06 : link.status === 'projected' ? 0.2 : 0.4}
                  strokeWidth={Math.max(1.5, link.width ?? 1)}
                  data-flow-kind={link.kind} data-flow-status={link.status}>
                  <title>{linkDescription(link)}</title>
                </path>
                {link.status !== 'posted' && <path d={path(link) ?? undefined} stroke={FLOW_COLORS[link.kind]}
                  strokeWidth={1.5} strokeOpacity={hover !== null && !active ? 0.06 : 0.7}
                  strokeDasharray={link.status === 'projected' ? '3 5' : '10 4'} pointerEvents="none" />}
              </g>
            })}
          </g>
          <g>
            {layout.nodes.map((node, i) => {
              const lastColumn = node.x1! >= layout.chartWidth - 28
              const labelX = lastColumn ? node.x0! - 8 : node.x1! + 8
              const labelY = layout.labels.get(i)!
              const active = hover === null || hover === i || layout.links.some(l =>
                ((l.source as unknown as { index: number }).index === hover && (l.target as unknown as { index: number }).index === i) ||
                ((l.target as unknown as { index: number }).index === hover && (l.source as unknown as { index: number }).index === i))
              const pocket = node.pocketId ? pockets.get(node.pocketId) : undefined
              const goal = pocket?.goal_id ? goals.get(pocket.goal_id) : undefined
              const info = [node.name, node.detail, `${t('moneyMap.throughput')}: ${money(node.value ?? 0)}`]
              if (pocket) info.push(`${t('moneyMap.currentBalance')}: ${money(Number(pocket.balance), pocket.currency)}`)
              if (goal) info.push(`${goal.name}: ${goal.valuation_available === false ? t('moneyMap.rateMissing') : `${mask(`${goal.percentage}%`)} · ${money(Number(goal.current_amount), goal.currency)} / ${money(Number(goal.target_amount), goal.currency)}`}${goal.target_date ? ` · ${goal.target_date}` : ''}`)
              return <g key={node.id} onMouseEnter={() => setHover(i)} opacity={active ? 1 : 0.3}>
                <title>{info.join('\n')}</title>
                <rect x={node.x0} y={node.y0} width={16} height={Math.max(2, node.y1! - node.y0!)} fill={node.color} rx={3} />
                <text x={labelX} y={labelY} textAnchor={lastColumn ? 'end' : 'start'} className="fill-foreground" style={{ fontSize: 12, fontWeight: 600, paintOrder: 'stroke', stroke: 'var(--card)', strokeWidth: 4, strokeLinejoin: 'round' }}>
                  {node.name.length > 29 ? `${node.name.slice(0, 28)}…` : node.name}
                  <tspan x={labelX} dy={15} className="fill-muted-foreground" style={{ fontSize: 11, fontWeight: 400 }}>
                    {node.detail.length > 32 ? `${node.detail.slice(0, 31)}…` : node.detail}
                  </tspan>
                  <tspan x={labelX} dy={15} className="fill-muted-foreground" style={{ fontSize: 11, fontWeight: 400 }}>{money(node.value ?? 0)}</tspan>
                </text>
              </g>
            })}
          </g>
        </svg>}
      </div>
      <div className="sr-only"><table>
        <caption>{t('reports.flowChartAria')}</caption>
        <thead><tr><th>{t('moneyMap.incoming')}</th><th>{t('moneyMap.outgoing')}</th><th>{t('reports.amount')}</th></tr></thead>
        <tbody>{graph.links.map((link, i) => <tr key={i}><td>{graph.nodes[link.source].name} · {graph.nodes[link.source].detail}</td><td>{graph.nodes[link.target].name} · {graph.nodes[link.target].detail}</td><td>{money(link.value)} · {t(`moneyMap.${link.status}`)}</td></tr>)}</tbody>
      </table></div>
    </div>
  )
}
