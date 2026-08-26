import { forwardRef, useState } from 'react'
import { Eye, EyeOff } from 'lucide-react'

const PasswordInput = forwardRef(function PasswordInput({ className = '', ...props }, ref) {
  const [visible, setVisible] = useState(false)
  const actionLabel = visible ? '隐藏密码' : '显示密码'

  return (
    <span className={`password-input-wrap${className ? ` ${className}` : ''}`}>
      <input {...props} ref={ref} type={visible ? 'text' : 'password'} />
      <button
        type="button"
        className="password-visibility-toggle"
        aria-controls={props.id}
        aria-label={actionLabel}
        aria-pressed={visible}
        title={actionLabel}
        onClick={() => setVisible((current) => !current)}
      >
        {visible ? <Eye aria-hidden="true" size={20} /> : <EyeOff aria-hidden="true" size={20} />}
      </button>
    </span>
  )
})

export default PasswordInput
