import { useEffect, useState } from 'react'
import ChatInterface from './components/ChatInterface'
import SchemaExplorer from './components/SchemaExplorer'
import { health } from './api/client'

const CATALOG = 'medallion_demo'

export default function App() {
  const [vectorSearch, setVectorSearch] = useState<boolean | null>(null)

  useEffect(() => {
    health()
      .then((result) => setVectorSearch(result.vector_search))
      .catch(() => setVectorSearch(null))
  }, [])

  return (
    <div className="app">
      <header className="masthead">
        <div>
          <span className="eyebrow">Databricks · Claude</span>
          <h1>AI Data Analyst</h1>
        </div>
        {/* Saying which mode the app is in keeps a thin definition answer
            explainable rather than looking like the model got it wrong. */}
        {vectorSearch !== null && (
          <span className={vectorSearch ? 'mode' : 'mode degraded'}>
            {vectorSearch
              ? 'Documentation index connected'
              : 'Catalog metadata only, no docs index'}
          </span>
        )}
      </header>

      <main>
        <SchemaExplorer catalog={CATALOG} />
        <ChatInterface catalog={CATALOG} />
      </main>
    </div>
  )
}
