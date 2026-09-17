// Typed client for the FastAPI backend. The shapes here mirror
// backend/models/schemas.py; change one and change the other.

export type ChartType =
  | 'bar'
  | 'line'
  | 'pie'
  | 'scatter'
  | 'funnel'
  | 'heatmap'
  | 'table'

export interface ChartConfig {
  chart_type: ChartType
  x_axis?: string | null
  y_axis?: string | null
  color_by?: string | null
  title?: string | null
}

export interface Source {
  type: string
  reference: string
  excerpt?: string | null
  score?: number | null
}

export type Row = Record<string, string | number | boolean | null>

export interface AnalyzeResponse {
  question: string
  agent_used: string
  summary: string
  sql?: string | null
  data?: Row[] | null
  columns?: string[] | null
  chart_recommendation?: ChartConfig | null
  sources?: Source[] | null
  truncated: boolean
}

export interface TableSummary {
  name: string
  type: string
  comment: string
}

export interface SchemaInfo {
  catalog: string
  schema_name: string
  tables: TableSummary[]
}

export interface ColumnInfo {
  name: string
  type: string
  comment: string
  nullable?: boolean | null
}

export interface TableDetails {
  full_name: string
  columns: ColumnInfo[]
  comment: string
  table_type: string
  row_count?: number | null
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })

  if (!response.ok) {
    // FastAPI puts the reason in `detail`. Surfacing it is what makes a
    // rejected query ("only read-only queries are allowed") readable instead
    // of collapsing into a generic 500.
    let detail = `Request failed with ${response.status}`
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      // Non-JSON error body. Keep the status message.
    }
    throw new ApiError(detail, response.status)
  }

  return (await response.json()) as T
}

export function analyze(
  question: string,
  options: { catalog?: string; schema?: string | null; signal?: AbortSignal } = {},
): Promise<AnalyzeResponse> {
  return request<AnalyzeResponse>('/api/analyze', {
    method: 'POST',
    signal: options.signal,
    body: JSON.stringify({
      question,
      catalog: options.catalog ?? 'medallion_demo',
      schema: options.schema ?? null,
    }),
  })
}

export function listSchemas(catalog = 'medallion_demo'): Promise<SchemaInfo[]> {
  return request<SchemaInfo[]>(`/api/schemas?catalog=${encodeURIComponent(catalog)}`)
}

export function getTableDetails(
  catalog: string,
  schema: string,
  table: string,
): Promise<TableDetails> {
  const path = [catalog, schema, table].map(encodeURIComponent).join('/')
  return request<TableDetails>(`/api/table/${path}`)
}

export function health(): Promise<{
  status: string
  service: string
  vector_search: boolean
}> {
  return request('/api/health')
}
