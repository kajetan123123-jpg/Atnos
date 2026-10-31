import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../lib/api'
import ProductionFeedbackPanel from './ProductionFeedbackPanel'

vi.mock('../lib/api', () => ({ addProductionFeedback: vi.fn(), disableProductionFeedback: vi.fn(), fetchProductionFeedback: vi.fn() }))

describe('production feedback', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.fetchProductionFeedback).mockResolvedValue({ feedback: [] })
  })

  it('defaults to one production and saves a general rule only after selecting its scope', async () => {
    vi.mocked(api.addProductionFeedback).mockResolvedValue({ id: 'rule-1', source_job_id: 'prod-1', scope: 'project', category: 'captions', note: 'Wolniejsze napisy', active: true })
    render(<ProductionFeedbackPanel jobId="prod-1" hasSeries={false} />)
    expect(screen.getByLabelText('Zakres uwagi')).toHaveValue('production')
    expect(screen.queryByRole('option', { name: 'Ta seria' })).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Co poprawić?'), { target: { value: 'Wolniejsze napisy' } })
    fireEvent.change(screen.getByLabelText('Zakres uwagi'), { target: { value: 'project' } })
    fireEvent.click(screen.getByRole('button', { name: 'Zapisz uwagę' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Atnos pobierze ją do kolejnych zleceń')
    expect(api.addProductionFeedback).toHaveBeenCalledWith('prod-1', { note: 'Wolniejsze napisy', scope: 'project' })
    expect(screen.getByLabelText('Co poprawić?')).toHaveValue('')
  })

  it('retains unsaved feedback after a server failure', async () => {
    vi.mocked(api.addProductionFeedback).mockRejectedValue(new Error('Zapis jest niedostępny'))
    render(<ProductionFeedbackPanel jobId="prod-1" hasSeries />)
    fireEvent.change(screen.getByLabelText('Co poprawić?'), { target: { value: 'Lepsze przykłady' } })
    fireEvent.click(screen.getByRole('button', { name: 'Zapisz uwagę' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Zapis jest niedostępny')
    expect(screen.getByLabelText('Co poprawić?')).toHaveValue('Lepsze przykłady')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('withdraws an existing rule from its source production', async () => {
    vi.mocked(api.fetchProductionFeedback).mockResolvedValue({ feedback: [{ id: 'rule-2', source_job_id: 'older-prod', scope: 'project', category: 'visuals', note: 'Mniej scen giełdowych', active: true }] })
    vi.mocked(api.disableProductionFeedback).mockResolvedValue({ disabled: true })
    render(<ProductionFeedbackPanel jobId="new-prod" hasSeries={false} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Wycofaj uwagę: Mniej scen giełdowych' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Uwaga wycofana')
    expect(api.disableProductionFeedback).toHaveBeenCalledWith('older-prod', 'rule-2')
    expect(screen.queryByText('Mniej scen giełdowych')).not.toBeInTheDocument()
  })
})
