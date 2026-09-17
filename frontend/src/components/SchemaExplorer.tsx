import { useEffect, useState } from 'react'
import {
  getTableDetails,
  listSchemas,
  type SchemaInfo,
  type TableDetails,
} from '../api/client'

export default function SchemaExplorer({ catalog }: { catalog: string }) {
  const [schemas, setSchemas] = useState<SchemaInfo[]>([])
  const [expanded, setExpanded] = useState<string | null>(null)
  const [details, setDetails] = useState<Record<string, TableDetails>>({})
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    listSchemas(catalog)
      .then((result) => {
        if (!cancelled) {
          setSchemas(result)
          setError(null)
        }
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [catalog])

  async function toggle(schema: string, table: string) {
    const key = `${schema}.${table}`
    if (expanded === key) {
      setExpanded(null)
      return
    }
    setExpanded(key)
    if (!details[key]) {
      try {
        const result = await getTableDetails(catalog, schema, table)
        setDetails((prev) => ({ ...prev, [key]: result }))
      } catch {
        // A table the caller cannot read is expected, not exceptional. The
        // row stays, without columns, so the gap is visible.
      }
    }
  }

  return (
    <aside className="explorer">
      <h3>{catalog}</h3>
      {loading && <p className="muted">Loading catalog…</p>}
      {error && <p className="error">{error}</p>}

      {schemas.map((schema) => (
        <section key={schema.schema_name}>
          <h4>{schema.schema_name}</h4>
          <ul>
            {schema.tables.map((table) => {
              const key = `${schema.schema_name}.${table.name}`
              const open = expanded === key
              const detail = details[key]
              return (
                <li key={table.name}>
                  <button
                    type="button"
                    className={open ? 'table-btn open' : 'table-btn'}
                    onClick={() => void toggle(schema.schema_name, table.name)}
                    title={table.comment || undefined}
                  >
                    {table.name}
                  </button>
                  {open && (
                    <div className="columns">
                      {detail ? (
                        detail.columns.map((column) => (
                          <div key={column.name} className="column">
                            <span className="col-name">{column.name}</span>
                            <span className="col-type">{column.type}</span>
                            {column.comment && (
                              <span className="col-comment">{column.comment}</span>
                            )}
                          </div>
                        ))
                      ) : (
                        <p className="muted">No column detail available.</p>
                      )}
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        </section>
      ))}

      {!loading && !error && schemas.length === 0 && (
        <p className="muted">No schemas visible under your permissions.</p>
      )}
    </aside>
  )
}
