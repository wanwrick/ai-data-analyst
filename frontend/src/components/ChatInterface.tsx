import { useEffect, useRef, useState } from 'react'
import { analyze, ApiError, type AnalyzeResponse } from '../api/client'
import QueryResult from './QueryResult'

interface Turn {
  id: number
  question: string
  status: 'pending' | 'done' | 'error'
  result?: AnalyzeResponse
  error?: string
}

const EXAMPLES = [
  'What was revenue last month?',
  'Which customers are at risk of churning?',
  'What does churn_score actually mean?',
  'Show me a bar chart of revenue by product category',
]

export default function ChatInterface({ catalog }: { catalog: string }) {
  const [question, setQuestion] = useState('')
  const [turns, setTurns] = useState<Turn[]>([])
  const [busy, setBusy] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)
  const nextId = useRef(0)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns])

  async function ask(text: string) {
    const trimmed = text.trim()
    if (!trimmed || busy) return

    const id = nextId.current++
    setTurns((prev) => [...prev, { id, question: trimmed, status: 'pending' }])
    setQuestion('')
    setBusy(true)

    try {
      const result = await analyze(trimmed, { catalog })
      setTurns((prev) =>
        prev.map((turn) => (turn.id === id ? { ...turn, status: 'done', result } : turn)),
      )
    } catch (error) {
      // A 422 means the generated SQL was refused. That is a real answer for
      // the user, not a crash, so it reads as a message rather than a banner.
      const message =
        error instanceof ApiError
          ? error.status === 422
            ? `That question produced a query I will not run: ${error.message}`
            : error.message
          : 'Something went wrong reaching the analyst.'
      setTurns((prev) =>
        prev.map((turn) =>
          turn.id === id ? { ...turn, status: 'error', error: message } : turn,
        ),
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="chat">
      <div className="transcript">
        {turns.length === 0 && (
          <div className="empty">
            <h2>Ask a question about your data</h2>
            <p>
              Every answer comes back with the SQL that produced it and the
              permissions it ran under.
            </p>
            <ul className="examples">
              {EXAMPLES.map((example) => (
                <li key={example}>
                  <button type="button" onClick={() => ask(example)}>
                    {example}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {turns.map((turn) => (
          <article key={turn.id} className="turn">
            <p className="question">{turn.question}</p>
            {turn.status === 'pending' && <p className="pending">Working on it…</p>}
            {turn.status === 'error' && <p className="error">{turn.error}</p>}
            {turn.status === 'done' && turn.result && <QueryResult result={turn.result} />}
          </article>
        ))}
        <div ref={endRef} />
      </div>

      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault()
          void ask(question)
        }}
      >
        <input
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Ask about revenue, customers, definitions…"
          aria-label="Your question"
          disabled={busy}
        />
        <button type="submit" disabled={busy || !question.trim()}>
          {busy ? 'Thinking' : 'Ask'}
        </button>
      </form>
    </div>
  )
}
