import React from 'react';
import { ArrowRight, Check, CircleHelp, RotateCcw } from 'lucide-react';
import { questionAssessment } from './review-verdict.mjs';

export function ReviewQuestion({ question, answer = {}, onChange, evidence, onNext, hasNext }) {
  const assessment = questionAssessment(question, answer);
  const reflection = question.kind !== 'choice';
  const canSubmit = reflection
    ? String(answer.response || '').trim().split(/\s+/).filter(Boolean).length >= 4
    : question.choices?.some(choice => choice.id === answer.choice);
  return <div className="ru-comprehension">
    <h2 className="ru-question-title">{question.prompt}</h2>
    {question.context && <p className="ru-question-context">{question.context.replace('execution is unverified.', 'CI results are shown separately in Tests.')}</p>}
    {reflection ? <label className="ru-form">Explain it in your own words
      <textarea rows={4} maxLength={6000} value={answer.response || ''} placeholder="Describe the behavior and why the change matters." onChange={event => onChange({ response: event.target.value, submitted: false, confirmed: false })}/>
    </label> : <div className="ru-quiz-options" role="group" aria-label="Choose an explanation">
      {question.choices.map((choice, index) => <button key={choice.id} aria-pressed={answer.choice === choice.id} disabled={answer.submitted === true}
        className={`${answer.choice === choice.id ? 'selected' : ''} ${assessment.submitted && choice.id === question.correctChoiceId ? 'correct' : ''} ${assessment.submitted && answer.choice === choice.id && !assessment.correct ? 'incorrect' : ''}`}
        onClick={() => onChange({ choice: choice.id, submitted: false })}><span>{String.fromCharCode(65 + index)}</span><p>{choice.text}</p>{assessment.submitted && choice.id === question.correctChoiceId && <Check size={16}/>}</button>)}
    </div>}
    {!answer.submitted && <button className="ru-action" disabled={!canSubmit} onClick={() => onChange({ ...answer, submitted: true })}>{reflection ? 'Compare with the evidence' : 'Check my reasoning'}<ArrowRight size={15}/></button>}
    {answer.submitted && <section className={`ru-answer-feedback ${assessment.correct ? 'correct' : ''}`} aria-live="polite">
      <h3>{reflection ? 'Compare your explanation' : assessment.correct ? 'That matches the source.' : 'There’s a distinction to revisit.'}</h3>
      <p>{question.explanation || 'Inspect the linked changes and compare the inputs, outputs, and effects described in your answer.'}</p>
      {evidence}
      {reflection && <><p className="ru-fine-print">This answer is self-assessed against the evidence.</p><button className="ru-link" onClick={() => onChange({ ...answer, confirmed: !answer.confirmed })}>{answer.confirmed ? <Check size={14}/> : <CircleHelp size={14}/>} {answer.confirmed ? 'Reasoning recorded' : 'My explanation accounts for this'}</button></>}
      <button className="ru-link" onClick={() => onChange({})}><RotateCcw size={14}/>{assessment.complete ? 'Revisit answer' : 'Try again'}</button>
      {assessment.complete && hasNext && <button className="ru-link ru-next-question" onClick={onNext}>Next question<ArrowRight size={15}/></button>}
    </section>}
  </div>;
}
