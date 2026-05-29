export default function HintModal({ hint, onAccept, onReject }) {
  if (!hint) return null
  return (
    <div className="modal-backdrop">
      <div className="modal">
        <h3>出现提示</h3>
        <p>{hint.hint_text}</p>
        <div className="actions">
          <button onClick={onReject}>拒绝</button>
          <button className="primary" onClick={onAccept}>接受</button>
        </div>
      </div>
    </div>
  )
}
