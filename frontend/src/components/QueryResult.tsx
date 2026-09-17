import { useMemo, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { AnalyzeResponse, Row } from '../api/client'

const SERIES_COLORS = ['#E22D00', '#252525', '#8A8577', '#C25A3C', '#4A4A4A']
const MAX_TABLE_ROWS = 200

function formatCell(value: Row[string]): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'number') {
    return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(2)
  }
  return String(value)
}

/** Recharts needs numbers. Warehouse results often arrive as numeric strings. */
function coerceNumeric(rows: Row[], keys: (string | null | undefined)[]): Row[] {
  const numericKeys = keys.filter((k): k is string => Boolean(k))
  return rows.map((row) => {
    const next: Row = { ...row }
    for (const key of numericKeys) {
      const value = row[key]
      if (typeof value === 'string' && value.trim() !== '' && !Number.isNaN(Number(value))) {
        next[key] = Number(value)
      }
    }
    return next
  })
}

function Chart({ result }: { result: AnalyzeResponse }) {
  const config = result.chart_recommendation
  const rows = result.data ?? []

  const data = useMemo(
    () => coerceNumeric(rows, [config?.y_axis, config?.x_axis]),
    [rows, config?.y_axis, config?.x_axis],
  )

  if (!config || config.chart_type === 'table' || data.length === 0) return null

  const { chart_type, x_axis, y_axis } = config
  if (!x_axis || !y_axis) return null

  const axisProps = { stroke: '#8A8577', fontSize: 12 }
  const common = (
    <>
      <CartesianGrid strokeDasharray="3 3" stroke="#E5E0D2" />
      <Tooltip />
    </>
  )

  return (
    <figure className="chart">
      {config.title && <figcaption>{config.title}</figcaption>}
      <ResponsiveContainer width="100%" height={280}>
        {chart_type === 'line' ? (
          <LineChart data={data}>
            {common}
            <XAxis dataKey={x_axis} {...axisProps} />
            <YAxis {...axisProps} />
            <Line
              type="monotone"
              dataKey={y_axis}
              stroke={SERIES_COLORS[0]}
              strokeWidth={2}
              dot={false}
            />
          </LineChart>
        ) : chart_type === 'scatter' ? (
          <ScatterChart>
            {common}
            <XAxis dataKey={x_axis} type="number" {...axisProps} />
            <YAxis dataKey={y_axis} type="number" {...axisProps} />
            <Scatter data={data} fill={SERIES_COLORS[0]} />
          </ScatterChart>
        ) : chart_type === 'pie' ? (
          <PieChart>
            <Tooltip />
            <Legend />
            <Pie data={data} dataKey={y_axis} nameKey={x_axis} outerRadius={100}>
              {data.map((_, index) => (
                <Cell key={index} fill={SERIES_COLORS[index % SERIES_COLORS.length]} />
              ))}
            </Pie>
          </PieChart>
        ) : (
          <BarChart data={data}>
            {common}
            <XAxis dataKey={x_axis} {...axisProps} />
            <YAxis {...axisProps} />
            <Bar dataKey={y_axis} fill={SERIES_COLORS[0]} />
          </BarChart>
        )}
      </ResponsiveContainer>
    </figure>
  )
}

export default function QueryResult({ result }: { result: AnalyzeResponse }) {
  const [showSql, setShowSql] = useState(false)
  const columns = result.columns ?? []
  const rows = result.data ?? []
  const visible = rows.slice(0, MAX_TABLE_ROWS)

  return (
    <section className="result">
      <header className="result-head">
        <span className="agent-tag">{result.agent_used}</span>
        {result.truncated && (
          <span className="warn-tag">Result capped at 1000 rows. Narrow the question.</span>
        )}
      </header>

      <p className="summary">{result.summary}</p>

      <Chart result={result} />

      {columns.length > 0 && rows.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {columns.map((column) => (
                  <th key={column}>{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visible.map((row, index) => (
                <tr key={index}>
                  {columns.map((column) => (
                    <td key={column}>{formatCell(row[column])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length > visible.length && (
            <p className="table-note">
              Showing {visible.length} of {rows.length} rows.
            </p>
          )}
        </div>
      )}

      {result.sources && result.sources.length > 0 && (
        <div className="sources">
          <h4>Sources</h4>
          <ul>
            {result.sources.map((source, index) => (
              <li key={index}>
                <strong>{source.reference}</strong>
                {source.excerpt && <span> — {source.excerpt}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {result.sql && (
        <div className="sql-block">
          <button type="button" onClick={() => setShowSql((open) => !open)}>
            {showSql ? 'Hide SQL' : 'Show the SQL that produced this'}
          </button>
          {showSql && <pre>{result.sql}</pre>}
        </div>
      )}
    </section>
  )
}
